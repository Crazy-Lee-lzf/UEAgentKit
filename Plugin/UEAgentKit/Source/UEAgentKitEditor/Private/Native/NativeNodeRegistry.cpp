// Copyright Epic Games, Inc. All Rights Reserved.

#include "Native/NativeNodeRegistry.h"

#include "Native/NativeCanonicalJson.h"
#include "Native/NativeCursorCodec.h"
#include "Native/NativeToolManifest.h"
#include "BlueprintContextSha256.h"

#include "BlueprintActionDatabase.h"
#include "BlueprintActionFilter.h"
#include "BlueprintAssetNodeSpawner.h"
#include "BlueprintBoundEventNodeSpawner.h"
#include "BlueprintComponentNodeSpawner.h"
#include "BlueprintDelegateNodeSpawner.h"
#include "BlueprintEventNodeSpawner.h"
#include "BlueprintFunctionNodeSpawner.h"
#include "BlueprintNodeSpawner.h"
#include "BlueprintVariableNodeSpawner.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node.h"
#include "K2Node_CallFunction.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_Variable.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"

#include "CoreGlobals.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "GenericPlatform/GenericPlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Interfaces/IPluginManager.h"
#include "Interfaces/IProjectManager.h"
#include "Misc/App.h"
#include "Misc/AutomationTest.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/EngineVersion.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "ProjectDescriptor.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitNativeRegistry, Log, All);

namespace
{
	const TCHAR* const ActionDomain = TEXT("ueak.node-action/1");
	const TCHAR* const ActionDatabaseDomain = TEXT("ueak.node-action/1.database");
	const TCHAR* const DescriptorDomain = TEXT("ueak.node-descriptor/1");
	const TCHAR* const NodeDescriptorSchemaVersion = TEXT("ueak.node-descriptor/1.0");
	const TCHAR* const SourceKind = TEXT("FBlueprintActionDatabase");
	constexpr int32 MaxUnsupportedEntries = 64;
	constexpr int32 MaxCatalogItems = 512;
	constexpr int32 MaxResponseBytes = 1048576;
	constexpr int32 MaxSessionTemplateCreations = 768;

	FString ComputeCursorQueryHash(const FString& Kind, const TArray<TPair<FString, FString>>& Fields);

	enum class ENativeTrust : uint8
	{
		Official = 0,
		Project = 1,
		ThirdParty = 2,
	};

	FString TrustName(const ENativeTrust Trust)
	{
		switch (Trust)
		{
		case ENativeTrust::Official:
			return TEXT("official");
		case ENativeTrust::Project:
			return TEXT("project");
		default:
			return TEXT("third-party");
		}
	}

	/** Frozen R1.2 special-node whitelist: anything else is unsupported, never "special". */
	const TMap<FString, FString>& GetSpecialNodeWhitelist()
	{
		static const TMap<FString, FString> Whitelist = {
			{ TEXT("/Script/BlueprintGraph.K2Node_Knot"), TEXT("knot") },
			{ TEXT("/Script/BlueprintGraph.K2Node_ExecutionSequence"), TEXT("sequence") },
			{ TEXT("/Script/BlueprintGraph.K2Node_Select"), TEXT("select") },
			{ TEXT("/Script/BlueprintGraph.K2Node_MultiGate"), TEXT("multigate") },
		};
		return Whitelist;
	}

	FString ClassShortName(const FString& PathName)
	{
		FString Result = PathName;
		int32 Index = INDEX_NONE;
		if (Result.FindLastChar(TEXT('.'), Index))
		{
			Result = Result.Mid(Index + 1);
		}
		return Result;
	}

	void AddAsciiTokens(const FString& Value, TSet<FString>& OutTokens)
	{
		FString Current;
		for (const TCHAR Character : Value)
		{
			const bool bAscii = Character < 128;
			const bool bAlnum = (Character >= TEXT('a') && Character <= TEXT('z'))
				|| (Character >= TEXT('A') && Character <= TEXT('Z'))
				|| (Character >= TEXT('0') && Character <= TEXT('9'))
				|| Character == TEXT('_');
			if (bAscii && bAlnum)
			{
				Current.AppendChar(Character);
			}
			else if (!Current.IsEmpty())
			{
				if (Current.Len() >= 2)
				{
					OutTokens.Add(Current.ToLower());
				}
				Current.Reset();
			}
		}
		if (Current.Len() >= 2)
		{
			OutTokens.Add(Current.ToLower());
		}
	}

	TArray<FString> SortedTokens(const TSet<FString>& Tokens, const int32 MaxTokens)
	{
		TArray<FString> Result = Tokens.Array();
		Result.Sort([](const FString& Left, const FString& Right) {
			return UEAgentKitCanonical::CompareUtf8(Left, Right) < 0;
		});
		if (Result.Num() > MaxTokens)
		{
			Result.SetNum(MaxTokens);
		}
		return Result;
	}

	FString GraphKindForType(const EGraphType GraphType)
	{
		switch (GraphType)
		{
		case GT_Ubergraph:
			return TEXT("event");
		case GT_Function:
			return TEXT("function");
		case GT_Macro:
			return TEXT("macro");
		default:
			return TEXT("other");
		}
	}

	FString ContainerName(const EPinContainerType ContainerType)
	{
		switch (ContainerType)
		{
		case EPinContainerType::Array:
			return TEXT("array");
		case EPinContainerType::Set:
			return TEXT("set");
		case EPinContainerType::Map:
			return TEXT("map");
		default:
			return TEXT("none");
		}
	}

	FString CurrentPluginBuildHash()
	{
		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit"));
		if (!Plugin.IsValid())
		{
			return FString();
		}
		const FString BinaryPath = FPaths::Combine(
			Plugin->GetBaseDir(),
			TEXT("Binaries"),
			FPlatformProcess::GetBinariesSubdirectory(),
			TEXT("UnrealEditor-UEAgentKitEditor.dll"));
		FString Digest;
		if (!FPaths::FileExists(BinaryPath) || !FBlueprintContextSha256::HashFile(BinaryPath, Digest))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Digest.ToLower();
	}

	/** Replaces the action-database fingerprint and recomputes the descriptor digest. */
	bool PatchDescriptorFingerprint(
		FNativeNodeDescriptorRecord& Record,
		const FString& ActionDatabaseFingerprint,
		FString& OutError)
	{
		using namespace UEAgentKitCanonical;
		FNativeCanonicalValueRef Tree = FNativeCanonicalValue::MakeNull();
		if (!FromJsonObject(Record.Descriptor, Tree, OutError))
		{
			return false;
		}
		const FNativeCanonicalValueRef* Source = Tree->FindObjectValue(TEXT("source"));
		if (Source == nullptr || (*Source)->GetType() != FNativeCanonicalValue::EType::Object)
		{
			OutError = TEXT("descriptor-source-missing");
			return false;
		}
		(*Source)->SetObjectValue(
			TEXT("actionDatabaseFingerprint"),
			FNativeCanonicalValue::MakeString(ActionDatabaseFingerprint));

		const FNativeCanonicalValueRef Projection =
			Tree->CloneWithoutKeys({ TEXT("descriptorDigest") })->CloneWithoutNestedKey(TEXT("source"), TEXT("discoveredAtUtc"));
		FString ProjectionHash;
		if (!DomainHash(Projection, DescriptorDomain, ProjectionHash, OutError))
		{
			return false;
		}
		const FString Digest = FString(TEXT("sha256:")) + ProjectionHash.Mid(7, 16);
		Tree->SetObjectValue(TEXT("descriptorDigest"), FNativeCanonicalValue::MakeString(Digest));
		Record.DescriptorDigest = Digest;
		Record.DescriptorProjectionHash = ProjectionHash;
		Record.Descriptor = Tree->ToJsonObject();
		return Record.Descriptor.IsValid();
	}

	bool ParseSemver(const FString& Value, int32& OutMajor, int32& OutMinor, int32& OutPatch)
	{
		TArray<FString> Parts;
		Value.ParseIntoArray(Parts, TEXT("."), false);
		if (Parts.Num() != 3)
		{
			return false;
		}
		for (const FString& Part : Parts)
		{
			if (Part.IsEmpty() || !Part.IsNumeric())
			{
				return false;
			}
		}
		OutMajor = FCString::Atoi(*Parts[0]);
		OutMinor = FCString::Atoi(*Parts[1]);
		OutPatch = FCString::Atoi(*Parts[2]);
		return true;
	}

	class FTrustResolver
	{
	public:
		FTrustResolver()
		{
			for (const TSharedRef<IPlugin>& Plugin : IPluginManager::Get().GetEnabledPlugins())
			{
				const ENativeTrust Trust = Plugin->GetLoadedFrom() == EPluginLoadedFrom::Engine
					? ENativeTrust::Official
					: (Plugin->GetLoadedFrom() == EPluginLoadedFrom::Project ? ENativeTrust::Project : ENativeTrust::ThirdParty);
				for (const FModuleDescriptor& Module : Plugin->GetDescriptor().Modules)
				{
					ModuleTrust.Add(Module.Name.ToString(), Trust);
				}
			}

			if (const FProjectDescriptor* Project = IProjectManager::Get().GetCurrentProject())
			{
				for (const FModuleDescriptor& Module : Project->Modules)
				{
					ModuleTrust.Add(Module.Name.ToString(), ENativeTrust::Project);
				}
			}
		}

		ENativeTrust ClassifyOwner(const UObject* Owner) const
		{
			if (Owner == nullptr)
			{
				return ENativeTrust::ThirdParty;
			}
			return ClassifyPackageName(Owner->GetOutermost() != nullptr ? Owner->GetOutermost()->GetName() : FString());
		}

		ENativeTrust ClassifyPackageName(const FString& PackageName) const
		{
			if (PackageName.StartsWith(TEXT("/Script/")))
			{
				const FString ModuleName = PackageName.RightChop(8);
				if (const ENativeTrust* Trust = ModuleTrust.Find(ModuleName))
				{
					return *Trust;
				}
				return ENativeTrust::Official;
			}
			if (PackageName.StartsWith(TEXT("/Game/")) || PackageName.StartsWith(TEXT("/Game")))
			{
				return ENativeTrust::Project;
			}
			if (PackageName.StartsWith(TEXT("/Engine/")) || PackageName.StartsWith(TEXT("/Script")))
			{
				return ENativeTrust::Official;
			}
			return ENativeTrust::ThirdParty;
		}

	private:
		TMap<FString, ENativeTrust> ModuleTrust;
	};

	struct FBuildContext
	{
		FString DiscoveredAtUtc;
		FString ActionDatabaseFingerprint;
		FString PluginBuildHash;
		FString EngineVersion;
		int32 MaxPinsPerDescriptor = 256;
		bool bAllowTemplateSpawn = true;
		bool bAllowPinAllocation = true;
		int32 MaxTemplateSpawns = 512;
		double DeadlineSeconds = 0.0;
		int32 TemplateNodesCached = 0;
		int32 TemplateNodesCreated = 0;
		int32 TemplateBudgetSkips = 0;
		int32 PinsAllocated = 0;
		int32 PinAllocationFailures = 0;
		TMap<const UBlueprintNodeSpawner*, FString>* DeferredReasons = nullptr;
	};

	struct FActionResult
	{
		bool bOk = false;
		bool bCountedAsUnsupported = true;
		FString Reason;
		FString Detail;
		FNativeNodeDescriptorRecord Record;
	};

	bool Fail(FActionResult& Result, const FString& Reason, const FString& Detail = FString())
	{
		Result.bOk = false;
		Result.Reason = Reason;
		Result.Detail = Detail;
		return false;
	}

	FString PayloadValueForFunction(const UFunction* Function)
	{
		return Function != nullptr ? Function->GetPathName() : FString();
	}

	bool BuildDescriptorForSpawner(
		UBlueprintNodeSpawner* Spawner,
		const ENativeTrust Trust,
		FBuildContext& Context,
		FActionResult& OutResult)
	{
		using namespace UEAgentKitCanonical;

		OutResult = FActionResult();
		if (Spawner == nullptr || Spawner->NodeClass == nullptr)
		{
			return Fail(OutResult, TEXT("node-class-unavailable"));
		}
		if (Trust != ENativeTrust::Official)
		{
			// Never touch templates for project / third-party / unknown owners.
			return Fail(OutResult, TEXT("untrusted-spawner-excluded"));
		}
		if (Context.DeferredReasons != nullptr)
		{
			if (const FString* FrozenReason = Context.DeferredReasons->Find(Spawner))
			{
				return Fail(OutResult, *FrozenReason);
			}
		}

		const FString NodeClassPath = Spawner->NodeClass->GetPathName();
		const FString SpawnerClassPath = Spawner->GetClass()->GetPathName();

		// Reject unknown engine spawner classes before touching the template cache.
		const bool bFunctionSpawner = Spawner->IsA<UBlueprintFunctionNodeSpawner>();
		const bool bVariableSpawner = Spawner->IsA<UBlueprintVariableNodeSpawner>();
		const bool bBoundEventSpawner = Spawner->IsA<UBlueprintBoundEventNodeSpawner>();
		const bool bEventSpawner = Spawner->IsA<UBlueprintEventNodeSpawner>();
		const bool bDelegateSpawner = Spawner->IsA<UBlueprintDelegateNodeSpawner>();
		const bool bComponentSpawner = Spawner->IsA<UBlueprintComponentNodeSpawner>();
		const bool bPlainSpawner = !bFunctionSpawner && !bVariableSpawner && !bBoundEventSpawner && !bEventSpawner
			&& !bDelegateSpawner && !bComponentSpawner;

		const bool bWhitelistedNode = GetSpecialNodeWhitelist().Contains(NodeClassPath)
			|| NodeClassPath == TEXT("/Script/BlueprintGraph.K2Node_DynamicCast")
			|| NodeClassPath == TEXT("/Script/BlueprintGraph.K2Node_ClassDynamicCast")
			|| NodeClassPath == TEXT("/Script/BlueprintGraph.K2Node_MacroInstance");
		if (bPlainSpawner && !bWhitelistedNode)
		{
			return Fail(OutResult, TEXT("unknown-spawner"));
		}

		UEdGraphNode* Template = Spawner->GetCachedTemplateNode();
		if (Template != nullptr)
		{
			++Context.TemplateNodesCached;
		}
		else if (!Context.bAllowTemplateSpawn)
		{
			return Fail(OutResult, TEXT("template-not-cached"));
		}
		else if (FPlatformTime::Seconds() > Context.DeadlineSeconds)
		{
			++Context.TemplateBudgetSkips;
			if (Context.DeferredReasons != nullptr)
			{
				Context.DeferredReasons->Add(Spawner, TEXT("template-budget-exceeded"));
			}
			return Fail(OutResult, TEXT("template-budget-exceeded"));
		}
		else if (Context.TemplateNodesCreated >= Context.MaxTemplateSpawns)
		{
			++Context.TemplateBudgetSkips;
			if (Context.DeferredReasons != nullptr)
			{
				Context.DeferredReasons->Add(Spawner, TEXT("template-budget-exceeded"));
			}
			return Fail(OutResult, TEXT("template-budget-exceeded"));
		}
		else
		{
			Template = Spawner->GetTemplateNode();
			++Context.TemplateNodesCreated;
			if (Template == nullptr)
			{
				if (Context.DeferredReasons != nullptr)
				{
					Context.DeferredReasons->Add(Spawner, TEXT("template-unavailable"));
				}
				return Fail(OutResult, TEXT("template-unavailable"));
			}
		}

		if (Template->GetClass()->GetPathName() != NodeClassPath)
		{
			return Fail(OutResult, TEXT("node-class-mismatch"));
		}

		UEdGraph* TemplateGraph = Template->GetGraph();
		const UEdGraphSchema* Schema = TemplateGraph != nullptr ? TemplateGraph->GetSchema() : nullptr;
		if (Schema == nullptr)
		{
			return Fail(OutResult, TEXT("graph-schema-unavailable"));
		}
		const FString GraphSchemaPath = Schema->GetClass()->GetPathName();

		FString ActionKind;
		FString PayloadValue;
		UFunction* CallFunction = nullptr;

		if (bFunctionSpawner)
		{
			UBlueprintFunctionNodeSpawner* FunctionSpawner = CastChecked<UBlueprintFunctionNodeSpawner>(Spawner);
			UFunction const* Function = FunctionSpawner->GetFunction();
			UK2Node_CallFunction* CallNode = Cast<UK2Node_CallFunction>(Template);
			if (Function == nullptr && CallNode != nullptr)
			{
				Function = CallNode->GetTargetFunction();
			}
			if (Function == nullptr)
			{
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			CallFunction = const_cast<UFunction*>(Function);
			ActionKind = TEXT("callFunction");
			PayloadValue = PayloadValueForFunction(Function);
		}
		else if (bVariableSpawner)
		{
			UK2Node_Variable* VariableNode = Cast<UK2Node_Variable>(Template);
			FString VariableName = VariableNode != nullptr ? VariableNode->GetVarName().ToString() : FString();
			if (VariableName.IsEmpty())
			{
				if (const FProperty* Property = CastChecked<UBlueprintVariableNodeSpawner>(Spawner)->GetVarProperty())
				{
					VariableName = Property->GetName();
				}
			}
			if (VariableName.IsEmpty())
			{
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			const bool bSetter = Spawner->NodeClass->IsChildOf(UK2Node_VariableSet::StaticClass());
			ActionKind = bSetter ? TEXT("variableSet") : TEXT("variableGet");
			PayloadValue = VariableName;
		}
		else if (bBoundEventSpawner)
		{
			const FMulticastDelegateProperty* Delegate = CastChecked<UBlueprintBoundEventNodeSpawner>(Spawner)->GetEventDelegate();
			if (Delegate == nullptr || Delegate->GetName().IsEmpty())
			{
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			ActionKind = TEXT("event");
			PayloadValue = Delegate->GetName();
		}
		else if (bEventSpawner)
		{
			UBlueprintEventNodeSpawner* EventSpawner = CastChecked<UBlueprintEventNodeSpawner>(Spawner);
			UFunction const* EventFunction = EventSpawner->GetEventFunction();
			if (EventFunction == nullptr)
			{
				// Custom-event spawners expose no stable payload getter; never guess.
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			ActionKind = TEXT("event");
			PayloadValue = EventFunction->GetName();
		}
		else if (bDelegateSpawner)
		{
			const FMulticastDelegateProperty* Delegate = CastChecked<UBlueprintDelegateNodeSpawner>(Spawner)->GetDelegateProperty();
			if (Delegate == nullptr || Delegate->GetName().IsEmpty())
			{
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			ActionKind = TEXT("delegate");
			PayloadValue = Delegate->GetName();
		}
		else if (bComponentSpawner)
		{
			const UClass* ComponentClass = CastChecked<UBlueprintComponentNodeSpawner>(Spawner)->GetComponentClass();
			if (ComponentClass == nullptr)
			{
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			ActionKind = TEXT("component");
			PayloadValue = ComponentClass->GetPathName();
		}
		else if (NodeClassPath == TEXT("/Script/BlueprintGraph.K2Node_DynamicCast")
			|| NodeClassPath == TEXT("/Script/BlueprintGraph.K2Node_ClassDynamicCast"))
		{
			const UK2Node_DynamicCast* CastNode = Cast<UK2Node_DynamicCast>(Template);
			const UClass* TargetClass = CastNode != nullptr ? CastNode->TargetType.Get() : nullptr;
			if (TargetClass == nullptr)
			{
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			ActionKind = TEXT("cast");
			PayloadValue = TargetClass->GetPathName();
		}
		else if (NodeClassPath == TEXT("/Script/BlueprintGraph.K2Node_MacroInstance"))
		{
			const UK2Node_MacroInstance* MacroNode = Cast<UK2Node_MacroInstance>(Template);
			UEdGraph* MacroGraph = MacroNode != nullptr ? MacroNode->GetMacroGraph() : nullptr;
			if (MacroGraph == nullptr || MacroGraph->GetName().IsEmpty())
			{
				return Fail(OutResult, TEXT("payload-unavailable"));
			}
			ActionKind = TEXT("macro");
			PayloadValue = MacroGraph->GetName();
		}
		else
		{
			const FString* SpecialKind = GetSpecialNodeWhitelist().Find(NodeClassPath);
			if (SpecialKind == nullptr)
			{
				return Fail(OutResult, TEXT("action-kind-unmapped"));
			}
			ActionKind = TEXT("special");
			PayloadValue = *SpecialKind;
		}

		if (PayloadValue.IsEmpty())
		{
			return Fail(OutResult, TEXT("payload-unavailable"));
		}

		// Pin templates: the engine does not allocate pins on template nodes, so ask
		// the transient template exactly once (never an asset, never a transaction).
		if (Template->Pins.Num() == 0 && Context.bAllowPinAllocation)
		{
			Template->AllocateDefaultPins();
			++Context.PinsAllocated;
			if (Template->Pins.Num() == 0)
			{
				++Context.PinAllocationFailures;
				return Fail(OutResult, TEXT("pin-metadata-unavailable"));
			}
		}
		if (Template->Pins.Num() == 0)
		{
			return Fail(OutResult, TEXT("pin-metadata-unavailable"));
		}
		if (Template->Pins.Num() > Context.MaxPinsPerDescriptor)
		{
			return Fail(OutResult, TEXT("pins-exceeded"));
		}

		// Localized display text (ND-LOCALIZED-BAN: never part of the discovery key).
		Spawner->PrimeDefaultUiSpec();
		const FBlueprintActionUiSpec& UiSpec = Spawner->DefaultMenuSignature;
		FString MenuName = UiSpec.MenuName.ToString();
		FString Category = UiSpec.Category.ToString();
		const FString KeywordsText = UiSpec.Keywords.ToString();

		if (MenuName.IsEmpty())
		{
			MenuName = Spawner->NodeClass->GetName();
		}
		TArray<FString> Keywords;
		KeywordsText.ParseIntoArray(Keywords, TEXT(","), true);
		for (FString& Keyword : Keywords)
		{
			Keyword.TrimStartAndEndInline();
		}
		Keywords.RemoveAll([](const FString& Keyword) { return Keyword.IsEmpty(); });

		TSet<FString> TokenSet;
		AddAsciiTokens(NodeClassPath, TokenSet);
		AddAsciiTokens(SpawnerClassPath, TokenSet);
		AddAsciiTokens(ActionKind, TokenSet);
		AddAsciiTokens(PayloadValue, TokenSet);
		const TArray<FString> SearchTokens = SortedTokens(TokenSet, 24);

		const FString GraphKind = GraphKindForType(Schema->GetGraphType(TemplateGraph));

		// Discovery key projection (exactly the Python discovery_key_projection).
		const FNativeCanonicalValueRef DiscoveryKey = FNativeCanonicalValue::MakeObject();
		DiscoveryKey->SetObjectValue(TEXT("nodeClass"), FNativeCanonicalValue::MakeString(NodeClassPath));
		DiscoveryKey->SetObjectValue(TEXT("spawnerClass"), FNativeCanonicalValue::MakeString(SpawnerClassPath));
		DiscoveryKey->SetObjectValue(TEXT("graphSchema"), FNativeCanonicalValue::MakeString(GraphSchemaPath));
		DiscoveryKey->SetObjectValue(TEXT("actionKind"), FNativeCanonicalValue::MakeString(ActionKind));
		const FNativeCanonicalValueRef Payload = FNativeCanonicalValue::MakeObject();
		const FString PayloadKey = ActionKind == TEXT("callFunction") ? TEXT("functionPath")
			: ActionKind == TEXT("event") ? TEXT("eventName")
			: (ActionKind == TEXT("variableGet") || ActionKind == TEXT("variableSet")) ? TEXT("variableName")
			: ActionKind == TEXT("macro") ? TEXT("macroGraph")
			: ActionKind == TEXT("cast") ? TEXT("targetClass")
			: ActionKind == TEXT("delegate") ? TEXT("delegateName")
			: ActionKind == TEXT("component") ? TEXT("componentClass")
			: TEXT("specialKind");
		Payload->SetObjectValue(PayloadKey, FNativeCanonicalValue::MakeString(PayloadValue));
		DiscoveryKey->SetObjectValue(TEXT("payload"), Payload);

		FString CanonicalDiscoveryKey;
		FString CanonicalError;
		if (!Serialize(DiscoveryKey, CanonicalDiscoveryKey, CanonicalError))
		{
			return Fail(OutResult, CanonicalError);
		}
		FString DomainHashValue;
		if (!DomainHash(DiscoveryKey, ActionDomain, DomainHashValue, CanonicalError))
		{
			return Fail(OutResult, CanonicalError);
		}
		const FString ActionId = FString(TEXT("action:")) + DomainHashValue.Mid(7, 32);

		// Constraints: only demonstrably supported engine facts.
		const UK2Node* K2Node = Cast<UK2Node>(Template);
		bool bPure = K2Node != nullptr && K2Node->IsNodePure();
		bool bLatent = false;
		bool bVariadic = false;
		bool bRequiresTarget = false;
		bool bRequiresWorldContext = false;
		if (CallFunction != nullptr)
		{
			bRequiresTarget = !CallFunction->HasAnyFunctionFlags(FUNC_Static);
			bRequiresWorldContext = CallFunction->HasMetaData(TEXT("WorldContext"));
			bVariadic = CallFunction->HasMetaData(FBlueprintMetadata::MD_Variadic);
			if (const UK2Node_CallFunction* CallNode = Cast<UK2Node_CallFunction>(Template))
			{
				bLatent = CallNode->IsLatentFunction();
			}
		}

		// Descriptor tree (same shape as ueak.node-descriptor.schema.json).
		const FNativeCanonicalValueRef Descriptor = FNativeCanonicalValue::MakeObject();
		Descriptor->SetObjectValue(TEXT("schemaVersion"), FNativeCanonicalValue::MakeString(NodeDescriptorSchemaVersion));
		Descriptor->SetObjectValue(TEXT("actionId"), FNativeCanonicalValue::MakeString(ActionId));
		Descriptor->SetObjectValue(TEXT("discoveryKey"), DiscoveryKey);
		Descriptor->SetObjectValue(TEXT("title"), FNativeCanonicalValue::MakeString(MenuName));
		Descriptor->SetObjectValue(TEXT("menuName"), FNativeCanonicalValue::MakeString(MenuName));
		Descriptor->SetObjectValue(TEXT("category"), FNativeCanonicalValue::MakeString(Category));
		const FNativeCanonicalValueRef KeywordArray = FNativeCanonicalValue::MakeArray();
		for (const FString& Keyword : Keywords)
		{
			KeywordArray->AddArrayValue(FNativeCanonicalValue::MakeString(Keyword));
		}
		Descriptor->SetObjectValue(TEXT("keywords"), KeywordArray);
		const FNativeCanonicalValueRef TokenArray = FNativeCanonicalValue::MakeArray();
		for (const FString& Token : SearchTokens)
		{
			TokenArray->AddArrayValue(FNativeCanonicalValue::MakeString(Token));
		}
		Descriptor->SetObjectValue(TEXT("searchTokens"), TokenArray);
		const FNativeCanonicalValueRef GraphKinds = FNativeCanonicalValue::MakeArray();
		GraphKinds->AddArrayValue(FNativeCanonicalValue::MakeString(GraphKind));
		Descriptor->SetObjectValue(TEXT("graphKinds"), GraphKinds);
		const FNativeCanonicalValueRef GraphSchemas = FNativeCanonicalValue::MakeArray();
		GraphSchemas->AddArrayValue(FNativeCanonicalValue::MakeString(GraphSchemaPath));
		Descriptor->SetObjectValue(TEXT("graphSchemas"), GraphSchemas);

		const FNativeCanonicalValueRef Pins = FNativeCanonicalValue::MakeArray();
		for (const UEdGraphPin* Pin : Template->Pins)
		{
			if (Pin == nullptr)
			{
				continue;
			}
			const FNativeCanonicalValueRef PinObject = FNativeCanonicalValue::MakeObject();
			PinObject->SetObjectValue(TEXT("name"), FNativeCanonicalValue::MakeString(Pin->PinName.ToString()));
			PinObject->SetObjectValue(
				TEXT("direction"),
				FNativeCanonicalValue::MakeString(Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output")));
			const bool bExec = Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec;
			PinObject->SetObjectValue(TEXT("kind"), FNativeCanonicalValue::MakeString(bExec ? TEXT("exec") : TEXT("data")));
			const FNativeCanonicalValueRef PinType = FNativeCanonicalValue::MakeObject();
			PinType->SetObjectValue(
				TEXT("category"),
				FNativeCanonicalValue::MakeString(Pin->PinType.PinCategory.IsNone() ? FString() : Pin->PinType.PinCategory.ToString()));
			PinType->SetObjectValue(
				TEXT("subCategory"),
				FNativeCanonicalValue::MakeString(Pin->PinType.PinSubCategory.IsNone() ? FString() : Pin->PinType.PinSubCategory.ToString()));
			const UObject* SubCategoryObject = Pin->PinType.PinSubCategoryObject.Get();
			PinType->SetObjectValue(
				TEXT("subCategoryObject"),
				FNativeCanonicalValue::MakeString(SubCategoryObject != nullptr ? SubCategoryObject->GetPathName() : FString()));
			PinType->SetObjectValue(TEXT("container"), FNativeCanonicalValue::MakeString(ContainerName(Pin->PinType.ContainerType)));
			PinType->SetObjectValue(TEXT("isReference"), FNativeCanonicalValue::MakeBool(Pin->PinType.bIsReference != 0));
			PinType->SetObjectValue(TEXT("isConst"), FNativeCanonicalValue::MakeBool(Pin->PinType.bIsConst != 0));
			PinType->SetObjectValue(TEXT("isWeakPointer"), FNativeCanonicalValue::MakeBool(Pin->PinType.bIsWeakPointer != 0));
			PinObject->SetObjectValue(TEXT("type"), PinType);
			Pins->AddArrayValue(PinObject);
		}
		Descriptor->SetObjectValue(TEXT("pins"), Pins);

		const FNativeCanonicalValueRef Constraints = FNativeCanonicalValue::MakeObject();
		Constraints->SetObjectValue(TEXT("requiresBlueprintContext"), FNativeCanonicalValue::MakeBool(true));
		Constraints->SetObjectValue(TEXT("requiresTarget"), FNativeCanonicalValue::MakeBool(bRequiresTarget));
		Constraints->SetObjectValue(TEXT("requiresWorldContext"), FNativeCanonicalValue::MakeBool(bRequiresWorldContext));
		Constraints->SetObjectValue(TEXT("pure"), FNativeCanonicalValue::MakeBool(bPure));
		Constraints->SetObjectValue(TEXT("latent"), FNativeCanonicalValue::MakeBool(bLatent));
		Constraints->SetObjectValue(TEXT("variadic"), FNativeCanonicalValue::MakeBool(bVariadic));
		Constraints->SetObjectValue(TEXT("hiddenByDefault"), FNativeCanonicalValue::MakeArray());
		Descriptor->SetObjectValue(TEXT("constraints"), Constraints);

		const FNativeCanonicalValueRef Source = FNativeCanonicalValue::MakeObject();
		Source->SetObjectValue(TEXT("kind"), FNativeCanonicalValue::MakeString(SourceKind));
		Source->SetObjectValue(TEXT("engineVersion"), FNativeCanonicalValue::MakeString(Context.EngineVersion));
		Source->SetObjectValue(TEXT("actionDatabaseFingerprint"), FNativeCanonicalValue::MakeString(Context.ActionDatabaseFingerprint));
		Source->SetObjectValue(TEXT("pluginBuildHash"), FNativeCanonicalValue::MakeString(Context.PluginBuildHash));
		Source->SetObjectValue(TEXT("discoveredAtUtc"), FNativeCanonicalValue::MakeString(Context.DiscoveredAtUtc));
		Descriptor->SetObjectValue(TEXT("source"), Source);
		Descriptor->SetObjectValue(TEXT("risk"), FNativeCanonicalValue::MakeString(TEXT("R2")));
		Descriptor->SetObjectValue(TEXT("capability"), FNativeCanonicalValue::MakeString(TEXT("core")));
		Descriptor->SetObjectValue(TEXT("trustLevel"), FNativeCanonicalValue::MakeString(TEXT("official")));

		const FNativeCanonicalValueRef DigestProjection =
			Descriptor->CloneWithoutKeys({ TEXT("descriptorDigest") })->CloneWithoutNestedKey(TEXT("source"), TEXT("discoveredAtUtc"));
		FString DescriptorHash;
		if (!DomainHash(DigestProjection, DescriptorDomain, DescriptorHash, CanonicalError))
		{
			return Fail(OutResult, CanonicalError);
		}
		const FString Digest = FString(TEXT("sha256:")) + DescriptorHash.Mid(7, 16);
		Descriptor->SetObjectValue(TEXT("descriptorDigest"), FNativeCanonicalValue::MakeString(Digest));

		OutResult.bOk = true;
		OutResult.Record.ActionId = ActionId;
		OutResult.Record.DescriptorDigest = Digest;
		OutResult.Record.CanonicalDiscoveryKey = CanonicalDiscoveryKey;
		OutResult.Record.DescriptorProjectionHash = DescriptorHash;
		OutResult.Record.Descriptor = Descriptor->ToJsonObject();
		return true;
	}
}

TSharedRef<FJsonObject> FNativeCatalogResult::MakeIdentityJson() const
{
	const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
	Object->SetStringField(TEXT("engineVersion"), Identity.EngineVersion);
	Object->SetNumberField(TEXT("engineChangelist"), Identity.EngineChangelist);
	Object->SetStringField(TEXT("buildConfiguration"), Identity.BuildConfiguration);
	Object->SetStringField(TEXT("pluginVersion"), Identity.PluginVersion);
	Object->SetStringField(TEXT("pluginBuildHash"), Identity.PluginBuildHash);
	Object->SetStringField(TEXT("pluginSetHash"), Identity.PluginSetHash);
	Object->SetStringField(TEXT("editorSessionId"), Identity.EditorSessionId);
	Object->SetStringField(TEXT("projectName"), Identity.ProjectName);
	Object->SetStringField(TEXT("actionDatabaseFingerprint"), ActionDatabaseFingerprint);
	return Object;
}

TSharedRef<FJsonObject> FNativeCatalogResult::MakeSummaryJson() const
{
	const TSharedRef<FJsonObject> Object = MakeIdentityJson();
	Object->SetBoolField(TEXT("ok"), bOk);
	Object->SetBoolField(TEXT("readOnly"), true);
	Object->SetBoolField(TEXT("saved"), false);
	Object->SetBoolField(TEXT("compiled"), false);
	Object->SetStringField(
		TEXT("mode"),
		Mode == ENativeRegistryMode::Off ? TEXT("off") : (Mode == ENativeRegistryMode::Shadow ? TEXT("shadow") : TEXT("active")));
	Object->SetBoolField(TEXT("complete"), bComplete);
	Object->SetBoolField(TEXT("truncated"), bTruncated);
	Object->SetNumberField(TEXT("totalSpawners"), TotalSpawners);
	Object->SetNumberField(TEXT("ownerCount"), OwnerCount);
	Object->SetNumberField(TEXT("notNodeActionCount"), NotNodeActionCount);
	Object->SetNumberField(TEXT("descriptorCount"), Descriptors.Num());
	Object->SetNumberField(TEXT("totalDescriptorsAvailable"), TotalDescriptorsAvailable);
	Object->SetNumberField(TEXT("totalUnsupported"), TotalUnsupported);
	Object->SetNumberField(TEXT("duplicateActionIdCount"), DuplicateActionIdCount);
	Object->SetNumberField(TEXT("templateNodesCached"), TemplateNodesCached);
	Object->SetNumberField(TEXT("templateNodesCreated"), TemplateNodesCreated);
	Object->SetNumberField(TEXT("templateBudgetSkips"), TemplateBudgetSkips);
	Object->SetNumberField(TEXT("pinsAllocated"), PinsAllocated);
	Object->SetNumberField(TEXT("pinAllocationFailures"), PinAllocationFailures);
	Object->SetNumberField(TEXT("durationMs"), DurationMs);

	if (UnsupportedByReason.Num() > 0)
	{
		const TSharedRef<FJsonObject> Reasons = MakeShared<FJsonObject>();
		for (const TPair<FString, int32>& Pair : UnsupportedByReason)
		{
			Reasons->SetNumberField(Pair.Key, Pair.Value);
		}
		Object->SetObjectField(TEXT("unsupportedByReason"), Reasons);
	}

	if (Unsupported.Num() > 0)
	{
		TArray<TSharedPtr<FJsonValue>> UnsupportedValues;
		UnsupportedValues.Reserve(Unsupported.Num());
		for (const FNativeUnsupportedEntry& Entry : Unsupported)
		{
			const TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("reason"), Entry.Reason);
			Item->SetStringField(TEXT("spawnerClass"), Entry.SpawnerClass);
			Item->SetStringField(TEXT("nodeClass"), Entry.NodeClass);
			Item->SetStringField(TEXT("trustLevel"), Entry.TrustLevel);
			UnsupportedValues.Add(MakeShared<FJsonValueObject>(Item));
		}
		Object->SetArrayField(TEXT("unsupported"), UnsupportedValues);
	}
	return Object;
}

FNativeNodeRegistry& FNativeNodeRegistry::Get()
{
	static FNativeNodeRegistry Instance;
	return Instance;
}

FString FNativeNodeRegistry::ReadConfiguredModeText()
{
	FString Value;
	if (GConfig != nullptr && GConfig->GetString(TEXT("UEAgentKit"), TEXT("NativeToolRegistry"), Value, GGameIni))
	{
		return Value.TrimStartAndEnd();
	}
	return FString();
}

ENativeRegistryMode FNativeNodeRegistry::ParseMode(const FString& ModeText)
{
	const FString Normalized = ModeText.TrimStartAndEnd().ToLower();
	if (Normalized == TEXT("shadow"))
	{
		return ENativeRegistryMode::Shadow;
	}
	if (Normalized == TEXT("active"))
	{
		return ENativeRegistryMode::Active;
	}
	return ENativeRegistryMode::Off;
}

void FNativeNodeRegistry::Initialize(const ENativeRegistryMode InMode)
{
	check(IsInGameThread());
	if (State != ENativeRegistryState::Uninitialized)
	{
		return;
	}

	Mode = InMode;
	EditorSessionId = FGuid::NewGuid().ToString(EGuidFormats::Digits).Left(16);
	Manifest = FNativeManifestSummary();
	UnavailableCode.Reset();
	UnavailableMessage.Reset();

	// Session-scoped cursor key.  Two 128-bit GUIDs provide 64 ASCII hex bytes;
	// the first 32 bytes are used so cursors cannot be forged across sessions.
	const FString CursorSeed = FGuid::NewGuid().ToString(EGuidFormats::Digits)
		+ FGuid::NewGuid().ToString(EGuidFormats::Digits);
	FTCHARToUTF8 CursorSeedUtf8(*CursorSeed);
	CursorKey.SetNumUninitialized(32);
	FMemory::Memcpy(CursorKey.GetData(), CursorSeedUtf8.Get(), 32);
	CursorTtlSeconds = UEAgentKitNativeCursor::CursorTtlSeconds;

	// R1.6 bounded socket negatives need a short TTL and a shared test key
	// across two disposable editors.  This seam requires the explicit local
	// command-line flag AND -unattended; secrets arrive via environment, never
	// argv, and production defaults are unchanged.
	if (FApp::IsUnattended() && FParse::Param(FCommandLine::Get(), TEXT("UEAgentKitSocketTest")))
	{
		const FString TtlText = FPlatformMisc::GetEnvironmentVariable(TEXT("UEAK_TEST_CURSOR_TTL"));
		if (!TtlText.IsEmpty())
		{
			CursorTtlSeconds = FMath::Clamp(FCString::Atoi(*TtlText), 1, 3600);
		}
		const FString KeyHex = FPlatformMisc::GetEnvironmentVariable(TEXT("UEAK_TEST_CURSOR_KEY"));
		bool bKeyOverride = false;
		if (KeyHex.Len() == 64)
		{
			auto HexNibble = [](TCHAR Character) -> int32
			{
				if (Character >= TEXT('0') && Character <= TEXT('9')) return Character - TEXT('0');
				if (Character >= TEXT('a') && Character <= TEXT('f')) return Character - TEXT('a') + 10;
				if (Character >= TEXT('A') && Character <= TEXT('F')) return Character - TEXT('A') + 10;
				return INDEX_NONE;
			};
			TArray<uint8> ParsedKey;
			ParsedKey.SetNumUninitialized(32);
			bool bValid = true;
			for (int32 Index = 0; Index < 32; ++Index)
			{
				const int32 High = HexNibble(KeyHex[Index * 2]);
				const int32 Low = HexNibble(KeyHex[Index * 2 + 1]);
				if (High == INDEX_NONE || Low == INDEX_NONE)
				{
					bValid = false;
					break;
				}
				ParsedKey[Index] = static_cast<uint8>((High << 4) | Low);
			}
			if (bValid)
			{
				CursorKey = MoveTemp(ParsedKey);
				bKeyOverride = true;
			}
		}
		UE_LOG(
			LogUEAgentKitNativeRegistry,
			Warning,
			TEXT("R1.6 test-only cursor seam enabled: ttl=%d keyOverride=%s"),
			CursorTtlSeconds,
			bKeyOverride ? TEXT("yes") : TEXT("no"));
	}

	if (Mode == ENativeRegistryMode::Off)
	{
		State = ENativeRegistryState::Off;
		UE_LOG(LogUEAgentKitNativeRegistry, Display, TEXT("Native node registry is off (default)."));
		return;
	}

	FNativeToolManifest LoadedManifest;
	if (!FNativeToolManifestLoader::Load(LoadedManifest))
	{
		State = ENativeRegistryState::Unavailable;
		UnavailableCode = LoadedManifest.ErrorCode.IsEmpty() ? TEXT("native-registry-invalid-manifest") : LoadedManifest.ErrorCode;
		UnavailableMessage = LoadedManifest.ErrorMessage.IsEmpty()
			? TEXT("The native tool manifest is unavailable.")
			: LoadedManifest.ErrorMessage;
		UE_LOG(LogUEAgentKitNativeRegistry, Warning, TEXT("Native node registry unavailable: %s"), *UnavailableMessage);
		return;
	}

	Manifest.bLoaded = true;
	Manifest.SchemaVersion = LoadedManifest.SchemaVersion;
	Manifest.RegistryVersion = LoadedManifest.RegistryVersion;
	Manifest.Hash = LoadedManifest.Hash;
	Manifest.ToolCount = LoadedManifest.Tools.Num();
	for (const FNativeToolManifestEntry& Entry : LoadedManifest.Tools)
	{
		Manifest.ToolIds.Add(Entry.Name);
		Manifest.ToolVersions.Add(Entry.Version);
	}
	State = ENativeRegistryState::Ready;
	UE_LOG(
		LogUEAgentKitNativeRegistry,
		Display,
		TEXT("Native node registry ready: mode=%s tools=%d registry=%s hash=%s"),
		Mode == ENativeRegistryMode::Shadow ? TEXT("shadow") : TEXT("active"),
		Manifest.ToolCount,
		*Manifest.RegistryVersion,
		*Manifest.Hash);
}

void FNativeNodeRegistry::Shutdown()
{
	if (State == ENativeRegistryState::Uninitialized)
	{
		return;
	}
	State = ENativeRegistryState::Uninitialized;
	Mode = ENativeRegistryMode::Off;
	Manifest = FNativeManifestSummary();
	UnavailableCode.Reset();
	UnavailableMessage.Reset();
	EditorSessionId.Reset();
	DeferredSpawnerReasons.Reset();
	SessionTemplateCreations = 0;
	DescribedSpawners.Reset();
	DescribedOwners.Reset();
	DescribedDescriptors.Reset();
	DescribedDatabaseFingerprint.Reset();
	DescribedTotalSpawners = 0;
	CursorKey.Reset();
	CursorTtlSeconds = UEAgentKitNativeCursor::CursorTtlSeconds;
}

bool FNativeNodeRegistry::IsInitialized() const
{
	return State != ENativeRegistryState::Uninitialized;
}

bool FNativeNodeRegistry::InitializeForAutomation()
{
	check(IsInGameThread());
	const bool bTestContext = GIsAutomationTesting
		|| FAutomationTestFramework::Get().GetCurrentTest() != nullptr;
	if (!bTestContext)
	{
		return false;
	}
	if (State == ENativeRegistryState::Ready)
	{
		return true;
	}
	if (State != ENativeRegistryState::Uninitialized)
	{
		Shutdown();
	}
	Initialize(ENativeRegistryMode::Shadow);
	return State == ENativeRegistryState::Ready;
}

ENativeRegistryMode FNativeNodeRegistry::GetMode() const
{
	return Mode;
}

ENativeRegistryState FNativeNodeRegistry::GetState() const
{
	return State;
}

bool FNativeNodeRegistry::IsAvailable() const
{
	return State == ENativeRegistryState::Ready;
}

const FString& FNativeNodeRegistry::GetUnavailableCode() const
{
	return UnavailableCode;
}

const FString& FNativeNodeRegistry::GetUnavailableMessage() const
{
	return UnavailableMessage;
}

const FNativeManifestSummary& FNativeNodeRegistry::GetManifestSummary() const
{
	return Manifest;
}

bool FNativeNodeRegistry::HasTool(const FString& ToolId) const
{
	return Manifest.bLoaded && Manifest.ToolIds.Contains(ToolId);
}

FString FNativeNodeRegistry::ComputeActionDatabaseFingerprint(
	const TArray<FString>& SortedActionIds,
	FString& OutErrorCode)
{
	using namespace UEAgentKitCanonical;
	OutErrorCode.Reset();
	TArray<FString> Sorted = SortedActionIds;
	Sorted.Sort([](const FString& Left, const FString& Right) {
		return UEAgentKitCanonical::CompareUtf8(Left, Right) < 0;
	});
	const FNativeCanonicalValueRef Array = FNativeCanonicalValue::MakeArray();
	for (const FString& ActionId : Sorted)
	{
		Array->AddArrayValue(FNativeCanonicalValue::MakeString(ActionId));
	}
	FString FingerprintValue;
	FString Error;
	if (!Fingerprint(Array, ActionDatabaseDomain, 16, FingerprintValue, Error))
	{
		OutErrorCode = Error;
		return FString();
	}
	return FingerprintValue;
}

FString FNativeNodeRegistry::ComputePluginSetHash(FString& OutErrorCode)
{
	using namespace UEAgentKitCanonical;
	OutErrorCode.Reset();
	TArray<FString> Entries;
	for (const TSharedRef<IPlugin>& Plugin : IPluginManager::Get().GetEnabledPlugins())
	{
		Entries.Add(FString::Printf(TEXT("%s|%s"), *Plugin->GetName(), *Plugin->GetDescriptor().VersionName));
	}
	Entries.Sort([](const FString& Left, const FString& Right) {
		return UEAgentKitCanonical::CompareUtf8(Left, Right) < 0;
	});
	TArray<FString> Unique;
	for (const FString& Entry : Entries)
	{
		if (Unique.Num() == 0 || Unique.Last() != Entry)
		{
			Unique.Add(Entry);
		}
	}
	const FNativeCanonicalValueRef Array = FNativeCanonicalValue::MakeArray();
	for (const FString& Entry : Unique)
	{
		Array->AddArrayValue(FNativeCanonicalValue::MakeString(Entry));
	}
	FString Hash;
	FString Error;
	if (!Fingerprint(Array, TEXT("ueak.node-registry.pluginset/1"), 16, Hash, Error))
	{
		OutErrorCode = Error;
		return FString();
	}
	return Hash;
}

FNativeCatalogResult FNativeNodeRegistry::BuildCatalog(const FNativeCatalogOptions& Options) const
{
	FNativeCatalogResult Result;
	Result.Mode = Mode;
	Result.Identity.EditorSessionId = EditorSessionId;
	Result.Identity.EngineVersion = FString::Printf(
		TEXT("%d.%d.%d"),
		FEngineVersion::Current().GetMajor(),
		FEngineVersion::Current().GetMinor(),
		FEngineVersion::Current().GetPatch());
	Result.Identity.EngineChangelist = static_cast<int32>(FEngineVersion::Current().GetChangelist());
	Result.Identity.BuildConfiguration = FString(LexToString(FApp::GetBuildConfiguration()));
	Result.Identity.ProjectName = FApp::GetProjectName();
	if (const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit")))
	{
		Result.Identity.PluginVersion = Plugin->GetDescriptor().VersionName;
	}
	Result.Identity.PluginBuildHash = CurrentPluginBuildHash();
	FString PluginSetError;
	Result.Identity.PluginSetHash = ComputePluginSetHash(PluginSetError);

	if (State != ENativeRegistryState::Ready)
	{
		Result.bOk = false;
		Result.ErrorCode = State == ENativeRegistryState::Unavailable && !UnavailableCode.IsEmpty()
			? UnavailableCode
			: TEXT("native-registry-unavailable");
		Result.ErrorMessage = State == ENativeRegistryState::Unavailable && !UnavailableMessage.IsEmpty()
			? UnavailableMessage
			: TEXT("The native node registry is not enabled.");
		return Result;
	}

	check(IsInGameThread());
	const double StartSeconds = FPlatformTime::Seconds();

	FNativeCatalogOptions Effective = Options;
	Effective.MaxItems = FMath::Clamp(Effective.MaxItems, 1, MaxCatalogItems);
	Effective.MaxTemplateSpawns = FMath::Clamp(Effective.MaxTemplateSpawns, 0, 4096);
	Effective.MaxPinsPerDescriptor = FMath::Clamp(Effective.MaxPinsPerDescriptor, 1, 1024);
	Effective.TemplateBudgetMs = FMath::Clamp(Effective.TemplateBudgetMs, 0.0, 60000.0);

	FBuildContext Context;
	Context.DiscoveredAtUtc = Effective.DiscoveredAtUtcOverride.IsEmpty()
		? FDateTime::UtcNow().ToIso8601()
		: Effective.DiscoveredAtUtcOverride;
	Context.PluginBuildHash = Result.Identity.PluginBuildHash;
	Context.EngineVersion = Result.Identity.EngineVersion;
	Context.MaxPinsPerDescriptor = Effective.MaxPinsPerDescriptor;
	Context.bAllowTemplateSpawn = Effective.bAllowOfficialTemplateSpawn;
	Context.bAllowPinAllocation = Effective.bAllowTransientPinAllocation;
	Context.MaxTemplateSpawns = FMath::Min(
		Effective.MaxTemplateSpawns,
		FMath::Max(0, MaxSessionTemplateCreations - SessionTemplateCreations));
	Context.DeferredReasons = &DeferredSpawnerReasons;
	Context.DeadlineSeconds = StartSeconds + Effective.TemplateBudgetMs / 1000.0;
	// Fingerprint is patched in after the unique action id set is known.
	Context.ActionDatabaseFingerprint = TEXT("sha256:0000000000000000");

	FBlueprintActionDatabase& Database = FBlueprintActionDatabase::Get();
	const FBlueprintActionDatabase::FActionRegistry& Registry = Database.GetAllActions();
	Result.OwnerCount = Registry.Num();

	const FTrustResolver TrustResolver;
	TMap<FString, FNativeNodeDescriptorRecord> UniqueRecords;
	TSet<FString> CollisionIds;

	struct FDescribedSource
	{
		TWeakObjectPtr<UBlueprintNodeSpawner> Spawner;
		TWeakObjectPtr<UObject> Owner;
	};
	TMap<FString, FDescribedSource> DescribedSources;

	for (const TPair<FObjectKey, FBlueprintActionDatabase::FActionList>& OwnerEntry : Registry)
	{
		const UObject* Owner = OwnerEntry.Key.ResolveObjectPtr();
		const ENativeTrust Trust = TrustResolver.ClassifyOwner(Owner);
		for (UBlueprintNodeSpawner* Spawner : OwnerEntry.Value)
		{
			++Result.TotalSpawners;
			if (Spawner == nullptr)
			{
				continue;
			}
			if (Spawner->IsA<UBlueprintAssetNodeSpawner>())
			{
				++Result.NotNodeActionCount;
				continue;
			}

			FActionResult Action;
			if (!BuildDescriptorForSpawner(Spawner, Trust, Context, Action))
			{
				++Result.TotalUnsupported;
				int32& ReasonCount = Result.UnsupportedByReason.FindOrAdd(Action.Reason);
				++ReasonCount;
				if (Result.Unsupported.Num() < MaxUnsupportedEntries)
				{
					FNativeUnsupportedEntry Entry;
					Entry.Reason = Action.Reason;
					Entry.SpawnerClass = Spawner->GetClass()->GetPathName();
					Entry.NodeClass = Spawner->NodeClass != nullptr ? Spawner->NodeClass->GetPathName() : FString();
					Entry.TrustLevel = TrustName(Trust);
					Result.Unsupported.Add(MoveTemp(Entry));
				}
				continue;
			}

			if (FNativeNodeDescriptorRecord* Existing = UniqueRecords.Find(Action.Record.ActionId))
			{
				++Result.DuplicateActionIdCount;
				if (Existing->DescriptorDigest != Action.Record.DescriptorDigest)
				{
					++Result.TotalUnsupported;
					++Result.UnsupportedByReason.FindOrAdd(TEXT("action-id-collision"));
					if (!CollisionIds.Contains(Action.Record.ActionId) && Result.Unsupported.Num() < MaxUnsupportedEntries)
					{
						CollisionIds.Add(Action.Record.ActionId);
						FNativeUnsupportedEntry Entry;
						Entry.Reason = TEXT("action-id-collision");
						Entry.SpawnerClass = Spawner->GetClass()->GetPathName();
						Entry.NodeClass = Spawner->NodeClass != nullptr ? Spawner->NodeClass->GetPathName() : FString();
						Entry.TrustLevel = TrustName(Trust);
						Result.Unsupported.Add(MoveTemp(Entry));
					}
				}
				continue;
			}
			const FString DescribedActionId = Action.Record.ActionId;
			UniqueRecords.Add(DescribedActionId, MoveTemp(Action.Record));
			FDescribedSource Source;
			Source.Spawner = Spawner;
			Source.Owner = const_cast<UObject*>(Owner);
			DescribedSources.Add(DescribedActionId, MoveTemp(Source));
		}
	}

	Result.TotalDescriptorsAvailable = UniqueRecords.Num();

	TArray<FString> UniqueActionIds;
	UniqueRecords.GetKeys(UniqueActionIds);
	FString FingerprintError;
	Result.ActionDatabaseFingerprint = ComputeActionDatabaseFingerprint(UniqueActionIds, FingerprintError);
	if (Result.ActionDatabaseFingerprint.IsEmpty())
	{
		Result.bOk = false;
		Result.ErrorCode = TEXT("native-internal-error");
		Result.ErrorMessage = FingerprintError.IsEmpty()
			? TEXT("The action database fingerprint could not be computed.")
			: FingerprintError;
		return Result;
	}
	Context.ActionDatabaseFingerprint = Result.ActionDatabaseFingerprint;

	TArray<TPair<FString, FNativeNodeDescriptorRecord>> Records = UniqueRecords.Array();
	Records.Sort([](const TPair<FString, FNativeNodeDescriptorRecord>& Left, const TPair<FString, FNativeNodeDescriptorRecord>& Right) {
		return UEAgentKitCanonical::CompareUtf8(Left.Key, Right.Key) < 0;
	});
	Result.Descriptors.Reset();
	for (TPair<FString, FNativeNodeDescriptorRecord>& Pair : Records)
	{
		FString PatchError;
		if (!PatchDescriptorFingerprint(Pair.Value, Result.ActionDatabaseFingerprint, PatchError))
		{
			++Result.TotalUnsupported;
			++Result.UnsupportedByReason.FindOrAdd(TEXT("descriptor-patch-failed"));
			if (Result.Unsupported.Num() < MaxUnsupportedEntries)
			{
				FNativeUnsupportedEntry Entry;
				Entry.Reason = TEXT("descriptor-patch-failed");
				Entry.SpawnerClass = FString();
				Entry.NodeClass = FString();
				Entry.TrustLevel = TEXT("official");
				Result.Unsupported.Add(MoveTemp(Entry));
			}
			continue;
		}
		Result.Descriptors.Add(MoveTemp(Pair.Value));
	}
	Result.TotalDescriptorsAvailable = Result.Descriptors.Num();

	Result.Unsupported.Sort([](const FNativeUnsupportedEntry& Left, const FNativeUnsupportedEntry& Right) {
		if (Left.Reason != Right.Reason)
		{
			return UEAgentKitCanonical::CompareUtf8(Left.Reason, Right.Reason) < 0;
		}
		if (Left.SpawnerClass != Right.SpawnerClass)
		{
			return UEAgentKitCanonical::CompareUtf8(Left.SpawnerClass, Right.SpawnerClass) < 0;
		}
		return UEAgentKitCanonical::CompareUtf8(Left.NodeClass, Right.NodeClass) < 0;
	});

	if (Result.Descriptors.Num() > Effective.MaxItems)
	{
		Result.Descriptors.SetNum(Effective.MaxItems);
		Result.bTruncated = true;
	}

	// Freeze the described set for R1.3 context filtering.  Context only ever
	// evaluates these official, already-described spawners, so a context query
	// can never trigger a project/third-party template.
	DescribedSpawners.Reset();
	DescribedOwners.Reset();
	DescribedDescriptors.Reset();
	for (const FNativeNodeDescriptorRecord& Record : Result.Descriptors)
	{
		const FDescribedSource* Source = DescribedSources.Find(Record.ActionId);
		if (Source == nullptr || !Source->Spawner.IsValid())
		{
			continue;
		}
		DescribedSpawners.Add(Record.ActionId, Source->Spawner);
		DescribedOwners.Add(Record.ActionId, Source->Owner);
		DescribedDescriptors.Add(Record.ActionId, Record.Descriptor);
	}
	DescribedDatabaseFingerprint = Result.ActionDatabaseFingerprint;
	DescribedTotalSpawners = Result.TotalSpawners;

	Result.bComplete = Result.TotalUnsupported == 0;
	Result.TemplateNodesCached = Context.TemplateNodesCached;
	Result.TemplateNodesCreated = Context.TemplateNodesCreated;
	Result.TemplateBudgetSkips = Context.TemplateBudgetSkips;
	Result.PinsAllocated = Context.PinsAllocated;
	Result.PinAllocationFailures = Context.PinAllocationFailures;
	SessionTemplateCreations += Context.TemplateNodesCreated;
	Result.DurationMs = (FPlatformTime::Seconds() - StartSeconds) * 1000.0;
	Result.bOk = true;
	return Result;
}

bool FNativeNodeRegistry::TryInvokeShadowTool(
	const FString& ToolId,
	const FString& ToolVersion,
	const TSharedPtr<FJsonObject>& Arguments,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();

	if (State != ENativeRegistryState::Ready)
	{
		OutErrorCode = State == ENativeRegistryState::Unavailable && !UnavailableCode.IsEmpty()
			? UnavailableCode
			: TEXT("native-registry-unavailable");
		OutErrorMessage = TEXT("The native node registry is not enabled.");
		return false;
	}

	FNativeToolManifest ManifestDocument;
	if (!FNativeToolManifestLoader::Load(ManifestDocument))
	{
		OutErrorCode = ManifestDocument.ErrorCode.IsEmpty() ? TEXT("native-registry-invalid-manifest") : ManifestDocument.ErrorCode;
		OutErrorMessage = ManifestDocument.ErrorMessage;
		return false;
	}
	const FNativeToolManifestEntry* Entry = ManifestDocument.FindTool(ToolId);
	if (Entry == nullptr)
	{
		OutErrorCode = TEXT("native-tool-unknown");
		OutErrorMessage = TEXT("The requested native tool is not registered.");
		return false;
	}

	int32 RequestedMajor = 0;
	int32 RequestedMinor = 0;
	int32 RequestedPatch = 0;
	int32 ManifestMajor = 0;
	int32 ManifestMinor = 0;
	int32 ManifestPatch = 0;
	if (!ParseSemver(ToolVersion, RequestedMajor, RequestedMinor, RequestedPatch)
		|| !ParseSemver(Entry->Version, ManifestMajor, ManifestMinor, ManifestPatch)
		|| RequestedMajor != ManifestMajor
		|| RequestedMinor > ManifestMinor)
	{
		OutErrorCode = TEXT("native-tool-version-unsupported");
		OutErrorMessage = TEXT("The requested native tool version is not supported.");
		return false;
	}

	const TSharedPtr<FJsonObject> SafeArguments = Arguments.IsValid() ? Arguments : MakeShared<FJsonObject>();
	const bool bIsCatalog = ToolId == TEXT("graph.nodeRegistry.catalog");
	const bool bIsFilterContext = ToolId == TEXT("graph.nodeRegistry.filterContext");

	if (bIsFilterContext)
	{
		FString AssetPath;
		FString GraphGuidText;
		FString NodeGuidText;
		FString PinName;
		int32 ContextMaxItems = 128;
		int32 ContextPageSize = 0;
		FString ContextCursor;
		bool bHasMaxItems = false;
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : SafeArguments->Values)
		{
			const bool bStringField = Pair.Key == TEXT("assetPath") || Pair.Key == TEXT("graphGuid")
				|| Pair.Key == TEXT("nodeGuid") || Pair.Key == TEXT("pinName") || Pair.Key == TEXT("cursor");
			if (bStringField)
			{
				if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::String)
				{
					OutErrorCode = TEXT("native-schema-invalid-arguments");
					OutErrorMessage = FString::Printf(TEXT("%s must be a string."), *Pair.Key);
					return false;
				}
				const FString Value = Pair.Value->AsString();
				const int32 MaxLength = Pair.Key == TEXT("cursor") ? 4096 : 512;
				if (Value.Len() > MaxLength)
				{
					OutErrorCode = TEXT("native-schema-invalid-arguments");
					OutErrorMessage = TEXT("A context argument is too long.");
					return false;
				}
				if (Pair.Key == TEXT("assetPath")) { AssetPath = Value; }
				else if (Pair.Key == TEXT("graphGuid")) { GraphGuidText = Value; }
				else if (Pair.Key == TEXT("nodeGuid")) { NodeGuidText = Value; }
				else if (Pair.Key == TEXT("cursor")) { ContextCursor = Value; }
				else { PinName = Value; }
			}
			else if (Pair.Key == TEXT("maxItems"))
			{
				if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::Number)
				{
					OutErrorCode = TEXT("native-schema-invalid-arguments");
					OutErrorMessage = TEXT("maxItems must be an integer.");
					return false;
				}
				const double Number = Pair.Value->AsNumber();
				if (Number < 1.0 || Number > MaxCatalogItems || Number != FMath::RoundToDouble(Number))
				{
					OutErrorCode = TEXT("native-schema-invalid-arguments");
					OutErrorMessage = TEXT("maxItems is out of range.");
					return false;
				}
				ContextMaxItems = static_cast<int32>(Number);
				bHasMaxItems = true;
			}
			else if (Pair.Key == TEXT("pageSize"))
			{
				if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::Number)
				{
					OutErrorCode = TEXT("native-schema-invalid-arguments");
					OutErrorMessage = TEXT("pageSize must be an integer.");
					return false;
				}
				const double Number = Pair.Value->AsNumber();
				if (Number < 1.0 || Number > 256.0 || Number != FMath::RoundToDouble(Number))
				{
					OutErrorCode = TEXT("native-schema-invalid-arguments");
					OutErrorMessage = TEXT("pageSize is out of range.");
					return false;
				}
				ContextPageSize = static_cast<int32>(Number);
			}
			else
			{
				OutErrorCode = TEXT("native-schema-invalid-arguments");
				OutErrorMessage = TEXT("Unknown native tool argument.");
				return false;
			}
		}
		const bool bContextPaging = ContextPageSize > 0 || !ContextCursor.IsEmpty();
		if (bContextPaging && bHasMaxItems)
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("maxItems cannot be combined with pageSize/cursor.");
			return false;
		}
		if (AssetPath.IsEmpty() || GraphGuidText.IsEmpty())
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("assetPath and graphGuid are required.");
			return false;
		}
		if (!PinName.IsEmpty() && NodeGuidText.IsEmpty())
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("pinName requires nodeGuid.");
			return false;
		}

		FNativeContextRequest ContextRequest;
		ContextRequest.AssetPath = AssetPath;
		ContextRequest.GraphGuid = GraphGuidText;
		ContextRequest.NodeGuid = NodeGuidText;
		ContextRequest.PinName = PinName;
		ContextRequest.MaxItems = bContextPaging ? MaxCatalogItems : ContextMaxItems;
		const FNativeContextResult ContextResult = FilterContext(ContextRequest);
		if (!ContextResult.bOk)
		{
			OutErrorCode = ContextResult.ErrorCode;
			OutErrorMessage = ContextResult.ErrorMessage;
			return false;
		}

		TSharedPtr<FJsonObject> ContextData = ContextResult.MakeJson();
		if (bContextPaging)
		{
			if (ContextResult.bTruncated)
			{
				OutErrorCode = TEXT("native-limit-response-too-large");
				OutErrorMessage = TEXT("The context match set exceeds the paging bound; refine the context.");
				return false;
			}
			const TArray<TSharedPtr<FJsonValue>>* AllActions = nullptr;
			if (!ContextData->TryGetArrayField(TEXT("actions"), AllActions) || AllActions == nullptr)
			{
				OutErrorCode = TEXT("native-internal-error");
				OutErrorMessage = TEXT("The context action list is unavailable for pagination.");
				return false;
			}
			TArray<TPair<FString, FString>> QueryFields;
			QueryFields.Add(TPair<FString, FString>(FString(TEXT("assetPath")), FString(AssetPath)));
			QueryFields.Add(TPair<FString, FString>(FString(TEXT("graphGuid")), FString(GraphGuidText)));
			QueryFields.Add(TPair<FString, FString>(FString(TEXT("nodeGuid")), FString(NodeGuidText)));
			QueryFields.Add(TPair<FString, FString>(FString(TEXT("pinName")), FString(PinName)));
			QueryFields.Add(TPair<FString, FString>(FString(TEXT("contextKind")), FString(ContextResult.ContextKind)));
			const FString QueryHash = ComputeCursorQueryHash(TEXT("context"), QueryFields);
			if (QueryHash.IsEmpty())
			{
				OutErrorCode = TEXT("native-internal-error");
				OutErrorMessage = TEXT("The context query identity could not be computed.");
				return false;
			}
			TArray<TSharedPtr<FJsonValue>> AllItems = *AllActions;
			if (!ApplyPagination(
					TEXT("context"),
					QueryHash,
					ContextResult.ActionSetFingerprint,
					AllItems,
					TEXT("actions"),
					ContextPageSize,
					ContextCursor,
					false,
					0,
					ContextData,
					OutErrorCode,
					OutErrorMessage))
			{
				return false;
			}
		}

		const TSharedRef<FJsonObject> ContextEnvelope = MakeShared<FJsonObject>();
		ContextEnvelope->SetStringField(TEXT("toolId"), ToolId);
		ContextEnvelope->SetStringField(TEXT("toolVersion"), Entry->Version);
		ContextEnvelope->SetStringField(TEXT("access"), TEXT("read"));
		ContextEnvelope->SetStringField(TEXT("editorSessionId"), EditorSessionId);
		ContextEnvelope->SetStringField(TEXT("registryVersion"), Manifest.RegistryVersion);
		ContextEnvelope->SetBoolField(TEXT("readOnly"), true);
		ContextEnvelope->SetObjectField(TEXT("data"), ContextData);

		FString ContextSerialized;
		const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> ContextWriter =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&ContextSerialized);
		if (!FJsonSerializer::Serialize(ContextEnvelope, ContextWriter))
		{
			OutErrorCode = TEXT("native-internal-error");
			OutErrorMessage = TEXT("The native tool result could not be serialized.");
			return false;
		}
		FTCHARToUTF8 ContextUtf8(*ContextSerialized);
		if (ContextUtf8.Length() > MaxResponseBytes)
		{
			OutErrorCode = TEXT("native-limit-response-too-large");
			OutErrorMessage = TEXT("The native tool result exceeds the response limit.");
			return false;
		}
		OutResult = ContextEnvelope;
		return true;
	}

	int32 MaxItems = 64;
	int32 PageSize = 0;
	FString Cursor;
	bool bHasMaxItems = false;
	bool bIncludeDescriptors = bIsCatalog;
	bool bIncludeUnsupported = true;
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : SafeArguments->Values)
	{
		if (bIsCatalog && Pair.Key == TEXT("maxItems"))
		{
			if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::Number)
			{
				OutErrorCode = TEXT("native-schema-invalid-arguments");
				OutErrorMessage = TEXT("maxItems must be an integer.");
				return false;
			}
			const double Number = Pair.Value->AsNumber();
			if (Number < 1.0 || Number > MaxCatalogItems || Number != FMath::RoundToDouble(Number))
			{
				OutErrorCode = TEXT("native-schema-invalid-arguments");
				OutErrorMessage = TEXT("maxItems is out of range.");
				return false;
			}
			MaxItems = static_cast<int32>(Number);
			bHasMaxItems = true;
		}
		else if (bIsCatalog && Pair.Key == TEXT("pageSize"))
		{
			if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::Number)
			{
				OutErrorCode = TEXT("native-schema-invalid-arguments");
				OutErrorMessage = TEXT("pageSize must be an integer.");
				return false;
			}
			const double Number = Pair.Value->AsNumber();
			if (Number < 1.0 || Number > 256.0 || Number != FMath::RoundToDouble(Number))
			{
				OutErrorCode = TEXT("native-schema-invalid-arguments");
				OutErrorMessage = TEXT("pageSize is out of range.");
				return false;
			}
			PageSize = static_cast<int32>(Number);
		}
		else if (bIsCatalog && Pair.Key == TEXT("cursor"))
		{
			if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::String || Pair.Value->AsString().Len() > 4096)
			{
				OutErrorCode = TEXT("native-schema-invalid-arguments");
				OutErrorMessage = TEXT("cursor must be a bounded string.");
				return false;
			}
			Cursor = Pair.Value->AsString();
		}
		else if (bIsCatalog && (Pair.Key == TEXT("includeDescriptors") || Pair.Key == TEXT("includeUnsupported")))
		{
			if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::Boolean)
			{
				OutErrorCode = TEXT("native-schema-invalid-arguments");
				OutErrorMessage = FString::Printf(TEXT("%s must be a boolean."), *Pair.Key);
				return false;
			}
			if (Pair.Key == TEXT("includeDescriptors"))
			{
				bIncludeDescriptors = Pair.Value->AsBool();
			}
			else
			{
				bIncludeUnsupported = Pair.Value->AsBool();
			}
		}
		else
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("Unknown native tool argument.");
			return false;
		}
	}

	const bool bCatalogPaging = PageSize > 0 || !Cursor.IsEmpty();
	if (bCatalogPaging)
	{
		if (bHasMaxItems)
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("maxItems cannot be combined with pageSize/cursor.");
			return false;
		}
		if (!bIncludeDescriptors)
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("includeDescriptors=false cannot be combined with pageSize/cursor.");
			return false;
		}
	}

	FNativeCatalogOptions Options;
	Options.MaxItems = bCatalogPaging ? MaxCatalogItems : MaxItems;
	// Use the existing 768-template session cap so the shadow catalog and the
	// paged catalog describe the same official set.  This is not a budget
	// increase: BuildCatalog already enforces MaxSessionTemplateCreations.
	Options.MaxTemplateSpawns = MaxSessionTemplateCreations;
	const FNativeCatalogResult Catalog = BuildCatalog(Options);
	if (!Catalog.bOk)
	{
		OutErrorCode = Catalog.ErrorCode;
		OutErrorMessage = Catalog.ErrorMessage;
		return false;
	}

	const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("toolId"), ToolId);
	Result->SetStringField(TEXT("toolVersion"), Entry->Version);
	Result->SetStringField(TEXT("access"), TEXT("read"));
	Result->SetStringField(TEXT("editorSessionId"), EditorSessionId);
	Result->SetStringField(TEXT("registryVersion"), Manifest.RegistryVersion);
	Result->SetBoolField(TEXT("readOnly"), true);
	const TSharedRef<FJsonObject> Data = Catalog.MakeSummaryJson();
	if (!bIncludeUnsupported)
	{
		Data->RemoveField(TEXT("unsupported"));
	}

	TArray<TSharedPtr<FJsonValue>> DescriptorValues;
	DescriptorValues.Reserve(Catalog.Descriptors.Num());
	for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
	{
		if (Record.Descriptor.IsValid())
		{
			DescriptorValues.Add(MakeShared<FJsonValueObject>(Record.Descriptor));
		}
	}
	if (bCatalogPaging)
	{
		const FString QueryHash = ComputeCursorQueryHash(TEXT("catalog"), TArray<TPair<FString, FString>>());
		if (QueryHash.IsEmpty())
		{
			OutErrorCode = TEXT("native-internal-error");
			OutErrorMessage = TEXT("The catalog query identity could not be computed.");
			return false;
		}
		if (!ApplyPagination(
				TEXT("catalog"),
				QueryHash,
				Catalog.ActionDatabaseFingerprint,
				DescriptorValues,
				TEXT("descriptors"),
				PageSize,
				Cursor,
				Catalog.bTruncated,
				Catalog.TotalDescriptorsAvailable,
				Data,
				OutErrorCode,
				OutErrorMessage))
		{
			return false;
		}
	}
	else if (bIncludeDescriptors)
	{
		Data->SetArrayField(TEXT("descriptors"), DescriptorValues);
	}
	Result->SetObjectField(TEXT("data"), Data);

	FString Serialized;
	const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
		TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Serialized);
	if (!FJsonSerializer::Serialize(Result, Writer))
	{
		OutErrorCode = TEXT("native-internal-error");
		OutErrorMessage = TEXT("The native tool result could not be serialized.");
		return false;
	}
	FTCHARToUTF8 Utf8(*Serialized);
	if (Utf8.Length() > MaxResponseBytes)
	{
		OutErrorCode = TEXT("native-limit-response-too-large");
		OutErrorMessage = TEXT("The native tool result exceeds the response limit.");
		return false;
	}

	OutResult = Result;
	return true;
}

namespace
{
	const TCHAR* const ContextFingerprintDomain = TEXT("ueak.node-action/1.context");

	FNativeCatalogIdentity MakeRegistryIdentity(const FString& EditorSessionId)
	{
		FNativeCatalogIdentity Identity;
		Identity.EditorSessionId = EditorSessionId;
		Identity.EngineVersion = FString::Printf(
			TEXT("%d.%d.%d"),
			FEngineVersion::Current().GetMajor(),
			FEngineVersion::Current().GetMinor(),
			FEngineVersion::Current().GetPatch());
		Identity.EngineChangelist = static_cast<int32>(FEngineVersion::Current().GetChangelist());
		Identity.BuildConfiguration = FString(LexToString(FApp::GetBuildConfiguration()));
		Identity.ProjectName = FApp::GetProjectName();
		if (const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit")))
		{
			Identity.PluginVersion = Plugin->GetDescriptor().VersionName;
		}
		Identity.PluginBuildHash = CurrentPluginBuildHash();
		FString PluginSetError;
		Identity.PluginSetHash = FNativeNodeRegistry::ComputePluginSetHash(PluginSetError);
		return Identity;
	}

	FString ContextKindForType(const EGraphType GraphType)
	{
		switch (GraphType)
		{
		case GT_Ubergraph:
			return TEXT("event");
		case GT_Function:
			return TEXT("function");
		case GT_Macro:
			return TEXT("macro");
		default:
			return TEXT("other");
		}
	}

	FString ComputeCursorQueryHash(
		const FString& Kind,
		const TArray<TPair<FString, FString>>& Fields)
	{
		using namespace UEAgentKitCanonical;
		const FNativeCanonicalValueRef Object = FNativeCanonicalValue::MakeObject();
		Object->SetObjectValue(TEXT("kind"), FNativeCanonicalValue::MakeString(Kind));
		for (const TPair<FString, FString>& Field : Fields)
		{
			Object->SetObjectValue(Field.Key, FNativeCanonicalValue::MakeString(Field.Value));
		}
		FString Hash;
		FString Error;
		if (!Fingerprint(Object, TEXT("ueak.native.cursor.query/1"), 16, Hash, Error))
		{
			return FString();
		}
		return Hash;
	}
}

TSharedRef<FJsonObject> FNativeContextResult::MakeJson() const
{
	const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
	Object->SetStringField(TEXT("schemaVersion"), TEXT("ueak.native.context-result/1.0"));
	Object->SetBoolField(TEXT("ok"), bOk);
	Object->SetBoolField(TEXT("readOnly"), true);
	Object->SetBoolField(TEXT("saved"), false);
	Object->SetBoolField(TEXT("compiled"), false);
	Object->SetBoolField(TEXT("packageDirty"), bPackageDirty);
	Object->SetStringField(TEXT("contextKind"), ContextKind);
	Object->SetStringField(TEXT("assetPath"), AssetPath);
	Object->SetStringField(TEXT("graphGuid"), GraphGuid);
	Object->SetStringField(TEXT("graphName"), GraphName);
	Object->SetStringField(TEXT("graphSchema"), GraphSchemaPath);
	Object->SetStringField(TEXT("graphKind"), GraphKind);
	Object->SetStringField(TEXT("nodeGuid"), NodeGuid);
	Object->SetStringField(TEXT("pinName"), PinName);
	Object->SetStringField(TEXT("pinDirection"), PinDirection);
	Object->SetStringField(TEXT("pinCategory"), PinCategory);
	Object->SetStringField(TEXT("actionDatabaseFingerprint"), ActionDatabaseFingerprint);
	Object->SetStringField(TEXT("actionSetFingerprint"), ActionSetFingerprint);
	Object->SetBoolField(TEXT("truncated"), bTruncated);
	Object->SetBoolField(TEXT("complete"), bComplete);
	Object->SetStringField(TEXT("completeScope"), TEXT("described-actions"));
	Object->SetNumberField(TEXT("totalSpawners"), TotalSpawners);
	Object->SetNumberField(TEXT("describedActions"), DescribedActions);
	Object->SetNumberField(TEXT("notDescribedActions"), NotDescribedActions);
	Object->SetNumberField(TEXT("evaluatedActions"), EvaluatedActions);
	Object->SetNumberField(TEXT("rejectedActions"), RejectedActions);
	Object->SetNumberField(TEXT("matchedActions"), MatchedActions);
	Object->SetNumberField(TEXT("totalAvailable"), TotalAvailable);
	Object->SetNumberField(TEXT("maxItems"), MaxItems);
	Object->SetNumberField(TEXT("durationMs"), DurationMs);

	const TSharedRef<FJsonObject> Semantics = MakeShared<FJsonObject>();
	Semantics->SetStringField(TEXT("catalogGraphKinds"), TEXT("template-context-hint"));
	Semantics->SetStringField(TEXT("contextAuthority"), TEXT("FBlueprintActionFilter"));
	Semantics->SetStringField(
		TEXT("note"),
		TEXT("descriptor.graphKinds describes the transient template graph only; this action set is the authoritative validity for the requested graph/node/pin."));
	Object->SetObjectField(TEXT("semantics"), Semantics);

	const TSharedRef<FJsonObject> IdentityJson = MakeShared<FJsonObject>();
	IdentityJson->SetStringField(TEXT("engineVersion"), Identity.EngineVersion);
	IdentityJson->SetNumberField(TEXT("engineChangelist"), Identity.EngineChangelist);
	IdentityJson->SetStringField(TEXT("pluginVersion"), Identity.PluginVersion);
	IdentityJson->SetStringField(TEXT("pluginBuildHash"), Identity.PluginBuildHash);
	IdentityJson->SetStringField(TEXT("pluginSetHash"), Identity.PluginSetHash);
	IdentityJson->SetStringField(TEXT("editorSessionId"), Identity.EditorSessionId);
	IdentityJson->SetStringField(TEXT("projectName"), Identity.ProjectName);
	Object->SetObjectField(TEXT("identity"), IdentityJson);

	TArray<TSharedPtr<FJsonValue>> ActionValues;
	ActionValues.Reserve(Actions.Num());
	for (const FNativeContextActionRecord& Action : Actions)
	{
		const TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("actionId"), Action.ActionId);
		Item->SetStringField(TEXT("descriptorDigest"), Action.DescriptorDigest);
		Item->SetStringField(TEXT("nodeClass"), Action.NodeClass);
		Item->SetStringField(TEXT("spawnerClass"), Action.SpawnerClass);
		Item->SetStringField(TEXT("title"), Action.Title);
		Item->SetStringField(TEXT("menuName"), Action.MenuName);
		Item->SetStringField(TEXT("category"), Action.Category);
		TArray<TSharedPtr<FJsonValue>> KindValues;
		for (const FString& Kind : Action.GraphKindsHint)
		{
			KindValues.Add(MakeShared<FJsonValueString>(Kind));
		}
		Item->SetArrayField(TEXT("graphKindsHint"), KindValues);
		if (Action.DiscoveryKey.IsValid())
		{
			Item->SetObjectField(TEXT("discoveryKey"), Action.DiscoveryKey);
		}
		ActionValues.Add(MakeShared<FJsonValueObject>(Item));
	}
	Object->SetArrayField(TEXT("actions"), ActionValues);
	return Object;
}

FNativeContextResult FNativeNodeRegistry::FilterContext(const FNativeContextRequest& Request) const
{
	FNativeContextResult Result;
	Result.Identity = MakeRegistryIdentity(EditorSessionId);
	const double StartSeconds = FPlatformTime::Seconds();
	if (State != ENativeRegistryState::Ready)
	{
		Result.ErrorCode = State == ENativeRegistryState::Unavailable && !UnavailableCode.IsEmpty()
			? UnavailableCode
			: TEXT("native-registry-unavailable");
		Result.ErrorMessage = TEXT("The native node registry is not enabled.");
		return Result;
	}
	check(IsInGameThread());

	if (Request.AssetPath.IsEmpty() || Request.GraphGuid.IsEmpty())
	{
		Result.ErrorCode = TEXT("native-schema-invalid-arguments");
		Result.ErrorMessage = TEXT("assetPath and graphGuid are required.");
		return Result;
	}

	UObject* ResolvedObject = FSoftObjectPath(Request.AssetPath).ResolveObject();
	UBlueprint* Blueprint = Cast<UBlueprint>(ResolvedObject);
	if (Blueprint == nullptr)
	{
		Result.ErrorCode = TEXT("graphpatch-asset-not-loaded");
		Result.ErrorMessage = TEXT("The requested Blueprint is not loaded; R1 never loads assets.");
		return Result;
	}

	FGuid GraphGuidValue;
	if (!FGuid::Parse(Request.GraphGuid, GraphGuidValue))
	{
		Result.ErrorCode = TEXT("native-schema-invalid-arguments");
		Result.ErrorMessage = TEXT("graphGuid is not a valid GUID.");
		return Result;
	}
	TArray<UEdGraph*> Graphs;
	Blueprint->GetAllGraphs(Graphs);
	UEdGraph* Graph = nullptr;
	for (UEdGraph* Candidate : Graphs)
	{
		if (Candidate != nullptr && Candidate->GraphGuid == GraphGuidValue)
		{
			Graph = Candidate;
			break;
		}
	}
	if (Graph == nullptr)
	{
		Result.ErrorCode = TEXT("graphpatch-graph-not-found");
		Result.ErrorMessage = TEXT("The requested graph does not exist in the Blueprint.");
		return Result;
	}

	const UEdGraphSchema_K2* K2Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
	if (K2Schema == nullptr)
	{
		Result.ErrorCode = TEXT("graphpatch-context-mismatch");
		Result.ErrorMessage = TEXT("The requested graph is not a K2 Blueprint graph.");
		return Result;
	}

	UEdGraphNode* Node = nullptr;
	if (!Request.NodeGuid.IsEmpty())
	{
		FGuid NodeGuidValue;
		if (!FGuid::Parse(Request.NodeGuid, NodeGuidValue))
		{
			Result.ErrorCode = TEXT("native-schema-invalid-arguments");
			Result.ErrorMessage = TEXT("nodeGuid is not a valid GUID.");
			return Result;
		}
		for (UEdGraphNode* Candidate : Graph->Nodes)
		{
			if (Candidate != nullptr && Candidate->NodeGuid == NodeGuidValue)
			{
				Node = Candidate;
				break;
			}
		}
		if (Node == nullptr)
		{
			Result.ErrorCode = TEXT("graph-node-not-found");
			Result.ErrorMessage = TEXT("The requested node does not exist in the graph.");
			return Result;
		}
	}

	UEdGraphPin* Pin = nullptr;
	if (!Request.PinName.IsEmpty())
	{
		if (Node == nullptr)
		{
			Result.ErrorCode = TEXT("native-schema-invalid-arguments");
			Result.ErrorMessage = TEXT("pinName requires nodeGuid.");
			return Result;
		}
		for (UEdGraphPin* Candidate : Node->Pins)
		{
			if (Candidate != nullptr && Candidate->PinName.ToString() == Request.PinName)
			{
				Pin = Candidate;
				break;
			}
		}
		if (Pin == nullptr)
		{
			Result.ErrorCode = TEXT("graph-pin-not-found");
			Result.ErrorMessage = TEXT("The requested pin does not exist on the node.");
			return Result;
		}
	}

	Result.AssetPath = Blueprint->GetPathName();
	Result.GraphGuid = Graph->GraphGuid.ToString(EGuidFormats::Digits);
	Result.GraphName = Graph->GetName();
	Result.GraphSchemaPath = Graph->GetSchema()->GetClass()->GetPathName();
	Result.GraphKind = ContextKindForType(Graph->GetSchema()->GetGraphType(Graph));
	Result.NodeGuid = Node != nullptr ? Node->NodeGuid.ToString(EGuidFormats::Digits) : FString();
	Result.PinName = Pin != nullptr ? Pin->PinName.ToString() : FString();
	Result.PinDirection = Pin != nullptr ? (Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output")) : FString();
	Result.PinCategory = Pin != nullptr ? Pin->PinType.PinCategory.ToString() : FString();
	Result.ContextKind = Pin != nullptr ? TEXT("pin") : (Node != nullptr ? TEXT("node") : TEXT("palette"));
	Result.MaxItems = FMath::Clamp(Request.MaxItems, 1, MaxCatalogItems);

	Result = FilterContextInternal(
		Blueprint,
		Graph,
		Node,
		Pin,
		Result.ContextKind,
		Result.MaxItems,
		Request.MaxFilterMs);
	Result.DurationMs = (FPlatformTime::Seconds() - StartSeconds) * 1000.0;
	return Result;
}

FNativeContextResult FNativeNodeRegistry::FilterContextInternal(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	UEdGraphNode* Node,
	UEdGraphPin* Pin,
	const FString& ContextKind,
	const int32 MaxItems,
	const double MaxFilterMs) const
{
	FNativeContextResult Result;
	Result.Identity = MakeRegistryIdentity(EditorSessionId);
	Result.ContextKind = ContextKind;
	Result.MaxItems = FMath::Clamp(MaxItems, 1, MaxCatalogItems);
	Result.AssetPath = Blueprint != nullptr ? Blueprint->GetPathName() : FString();
	Result.GraphGuid = Graph != nullptr ? Graph->GraphGuid.ToString(EGuidFormats::Digits) : FString();
	Result.GraphName = Graph != nullptr ? Graph->GetName() : FString();
	if (Graph != nullptr && Graph->GetSchema() != nullptr)
	{
		Result.GraphSchemaPath = Graph->GetSchema()->GetClass()->GetPathName();
		Result.GraphKind = ContextKindForType(Graph->GetSchema()->GetGraphType(Graph));
	}
	Result.NodeGuid = Node != nullptr ? Node->NodeGuid.ToString(EGuidFormats::Digits) : FString();
	Result.PinName = Pin != nullptr ? Pin->PinName.ToString() : FString();
	Result.PinDirection = Pin != nullptr ? (Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output")) : FString();
	Result.PinCategory = Pin != nullptr ? Pin->PinType.PinCategory.ToString() : FString();
	Result.bPackageDirty = Blueprint != nullptr && Blueprint->GetOutermost() != nullptr
		? Blueprint->GetOutermost()->IsDirty()
		: false;

	if (State != ENativeRegistryState::Ready)
	{
		Result.ErrorCode = TEXT("native-registry-unavailable");
		Result.ErrorMessage = TEXT("The native node registry is not enabled.");
		return Result;
	}
	if (Blueprint == nullptr || Graph == nullptr)
	{
		Result.ErrorCode = TEXT("native-schema-invalid-arguments");
		Result.ErrorMessage = TEXT("Blueprint and graph are required.");
		return Result;
	}
	if (Cast<UEdGraphSchema_K2>(Graph->GetSchema()) == nullptr)
	{
		Result.ErrorCode = TEXT("graphpatch-context-mismatch");
		Result.ErrorMessage = TEXT("The requested graph is not a K2 Blueprint graph.");
		return Result;
	}
	if (Node != nullptr && Node->GetGraph() != Graph)
	{
		Result.ErrorCode = TEXT("graph-node-not-found");
		Result.ErrorMessage = TEXT("The requested node does not belong to the requested graph.");
		return Result;
	}
	if (Pin != nullptr && (Node == nullptr || !Node->Pins.Contains(Pin)))
	{
		Result.ErrorCode = TEXT("graph-pin-not-found");
		Result.ErrorMessage = TEXT("The requested pin does not belong to the requested node.");
		return Result;
	}

	// Context only evaluates the session's described, official actions.  This is
	// the key trust bound: no menu builder enumeration and no untrusted template.
	if (DescribedSpawners.Num() == 0)
	{
		const FNativeCatalogResult Catalog = BuildCatalog(FNativeCatalogOptions());
		if (!Catalog.bOk)
		{
			Result.ErrorCode = Catalog.ErrorCode;
			Result.ErrorMessage = Catalog.ErrorMessage;
			return Result;
		}
	}

	FBlueprintActionFilter::EFlags Flags = FBlueprintActionFilter::BPFILTER_NoFlags;
	if (ContextKind == TEXT("palette"))
	{
		// Mirrors FBlueprintActionMenuUtils::MakePaletteMenu(Context, nullptr, ...).
	}
	else
	{
		// Mirrors the context-sensitive flags used by MakeContextMenu.
		Flags |= FBlueprintActionFilter::BPFILTER_RejectGlobalFields;
		Flags |= FBlueprintActionFilter::BPFILTER_RejectNonImportedFields;
		Flags |= FBlueprintActionFilter::BPFILTER_RejectIncompatibleThreadSafety;
	}
	FBlueprintActionFilter Filter(Flags);
	Filter.Context.Blueprints.Add(Blueprint);
	Filter.Context.Graphs.Add(Graph);
	if (Pin != nullptr)
	{
		Filter.Context.Pins.Add(Pin);
		if (ContextKind != TEXT("palette"))
		{
			// Pin context menus are implicitly targeting the pin's node class.
			if (UClass* PinObjectClass = Cast<UClass>(Pin->PinType.PinSubCategoryObject.Get()))
			{
				FBlueprintActionFilter::Add(Filter.TargetClasses, PinObjectClass);
			}
		}
	}
	if (ContextKind == TEXT("palette"))
	{
		Filter.RejectedNodeTypes.Add(UK2Node_VariableGet::StaticClass());
		Filter.RejectedNodeTypes.Add(UK2Node_VariableSet::StaticClass());
	}

	const double Deadline = FPlatformTime::Seconds() + FMath::Clamp(MaxFilterMs, 100.0, 30000.0) / 1000.0;
	TArray<FNativeContextActionRecord> Matched;
	Result.TotalSpawners = DescribedTotalSpawners;
	Result.DescribedActions = DescribedSpawners.Num();
	Result.NotDescribedActions = FMath::Max(0, Result.TotalSpawners - Result.DescribedActions);
	for (const TPair<FString, TWeakObjectPtr<UBlueprintNodeSpawner>>& Pair : DescribedSpawners)
	{
		if ((Result.EvaluatedActions & 0x3f) == 0 && FPlatformTime::Seconds() > Deadline)
		{
			Result.bTruncated = true;
			break;
		}
		UBlueprintNodeSpawner* Spawner = Pair.Value.Get();
		const TWeakObjectPtr<UObject>* OwnerPtr = DescribedOwners.Find(Pair.Key);
		UObject* Owner = OwnerPtr != nullptr ? OwnerPtr->Get() : nullptr;
		const TSharedPtr<FJsonObject>* DescriptorPtr = DescribedDescriptors.Find(Pair.Key);
		if (Spawner == nullptr || Owner == nullptr || DescriptorPtr == nullptr || !DescriptorPtr->IsValid())
		{
			continue;
		}
		++Result.EvaluatedActions;
		FBlueprintActionInfo ActionInfo(Owner, Spawner);
		if (Filter.IsFiltered(ActionInfo))
		{
			++Result.RejectedActions;
			continue;
		}

		const TSharedPtr<FJsonObject> Descriptor = *DescriptorPtr;
		FNativeContextActionRecord Record;
		Record.ActionId = Pair.Key;
		Descriptor->TryGetStringField(TEXT("descriptorDigest"), Record.DescriptorDigest);
		Descriptor->TryGetStringField(TEXT("title"), Record.Title);
		Descriptor->TryGetStringField(TEXT("menuName"), Record.MenuName);
		Descriptor->TryGetStringField(TEXT("category"), Record.Category);
		const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
		if (Descriptor->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey) && DiscoveryKey != nullptr)
		{
			Record.DiscoveryKey = *DiscoveryKey;
			(*DiscoveryKey)->TryGetStringField(TEXT("nodeClass"), Record.NodeClass);
			(*DiscoveryKey)->TryGetStringField(TEXT("spawnerClass"), Record.SpawnerClass);
		}
		const TArray<TSharedPtr<FJsonValue>>* GraphKinds = nullptr;
		if (Descriptor->TryGetArrayField(TEXT("graphKinds"), GraphKinds) && GraphKinds != nullptr)
		{
			for (const TSharedPtr<FJsonValue>& Kind : *GraphKinds)
			{
				if (Kind.IsValid())
				{
					Record.GraphKindsHint.Add(Kind->AsString());
				}
			}
		}
		Matched.Add(MoveTemp(Record));
	}

	Matched.Sort([](const FNativeContextActionRecord& Left, const FNativeContextActionRecord& Right) {
		return UEAgentKitCanonical::CompareUtf8(Left.ActionId, Right.ActionId) < 0;
	});
	Result.MatchedActions = Matched.Num();
	Result.TotalAvailable = Matched.Num();
	Result.ActionDatabaseFingerprint = DescribedDatabaseFingerprint;

	TArray<FString> AllIds;
	AllIds.Reserve(Matched.Num());
	for (const FNativeContextActionRecord& Record : Matched)
	{
		AllIds.Add(Record.ActionId);
	}
	const FNativeCanonicalValueRef IdArray = FNativeCanonicalValue::MakeArray();
	for (const FString& ActionId : AllIds)
	{
		IdArray->AddArrayValue(FNativeCanonicalValue::MakeString(ActionId));
	}
	FString FingerprintValue;
	FString FingerprintError;
	if (!UEAgentKitCanonical::Fingerprint(IdArray, ContextFingerprintDomain, 16, FingerprintValue, FingerprintError))
	{
		Result.ErrorCode = TEXT("native-internal-error");
		Result.ErrorMessage = FingerprintError.IsEmpty() ? TEXT("The context fingerprint failed.") : FingerprintError;
		return Result;
	}
	Result.ActionSetFingerprint = FingerprintValue;

	if (Matched.Num() > Result.MaxItems)
	{
		Matched.SetNum(Result.MaxItems);
		Result.bTruncated = true;
	}
	Result.Actions = MoveTemp(Matched);
	Result.bComplete = !Result.bTruncated;
	Result.bOk = true;
	return Result;
}

bool FNativeNodeRegistry::FilterContextForAutomation(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	UEdGraphNode* Node,
	UEdGraphPin* Pin,
	const int32 MaxItems,
	FNativeContextResult& OutResult) const
{
	const bool bTestContext = GIsAutomationTesting
		|| FAutomationTestFramework::Get().GetCurrentTest() != nullptr;
	if (!bTestContext)
	{
		OutResult = FNativeContextResult();
		OutResult.ErrorCode = TEXT("native-internal-error");
		OutResult.ErrorMessage = TEXT("The automation seam is only available while a test is running.");
		return false;
	}
	check(IsInGameThread());
	const FString ContextKind = Pin != nullptr ? TEXT("pin") : (Node != nullptr ? TEXT("node") : TEXT("palette"));
	OutResult = FilterContextInternal(Blueprint, Graph, Node, Pin, ContextKind, MaxItems, 5000.0);
	return OutResult.bOk;
}

bool FNativeNodeRegistry::DescribeSpawnerForAutomation(
	UBlueprintNodeSpawner* Spawner,
	const bool bTreatAsUntrusted,
	FString& OutCode) const
{
	const bool bTestContext = GIsAutomationTesting
		|| FAutomationTestFramework::Get().GetCurrentTest() != nullptr;
	if (!bTestContext || Spawner == nullptr)
	{
		OutCode = TEXT("native-internal-error");
		return false;
	}
	check(IsInGameThread());
	FBuildContext Context;
	Context.EngineVersion = FString::Printf(
		TEXT("%d.%d.%d"),
		FEngineVersion::Current().GetMajor(),
		FEngineVersion::Current().GetMinor(),
		FEngineVersion::Current().GetPatch());
	Context.ActionDatabaseFingerprint = TEXT("sha256:0000000000000000");
	Context.PluginBuildHash = CurrentPluginBuildHash();
	Context.bAllowTemplateSpawn = true;
	Context.bAllowPinAllocation = false;
	Context.MaxTemplateSpawns = 0;
	Context.DeadlineSeconds = FPlatformTime::Seconds() + 1.0;
	FActionResult Action;
	const bool bOk = BuildDescriptorForSpawner(
		Spawner,
		bTreatAsUntrusted ? ENativeTrust::ThirdParty : ENativeTrust::Official,
		Context,
		Action);
	OutCode = bOk ? TEXT("ok") : Action.Reason;
	return bOk;
}

FString FNativeNodeRegistry::ComputeCursorEpoch(const FString& Kind, const FString& SetFingerprint) const
{
	using namespace UEAgentKitCanonical;
	const FNativeCanonicalValueRef Object = FNativeCanonicalValue::MakeObject();
	Object->SetObjectValue(TEXT("session"), FNativeCanonicalValue::MakeString(EditorSessionId));
	Object->SetObjectValue(TEXT("manifest"), FNativeCanonicalValue::MakeString(Manifest.Hash));
	Object->SetObjectValue(TEXT("kind"), FNativeCanonicalValue::MakeString(Kind));
	Object->SetObjectValue(TEXT("set"), FNativeCanonicalValue::MakeString(SetFingerprint));
	FString Epoch;
	FString Error;
	if (!Fingerprint(Object, TEXT("ueak.native.cursor.epoch/1"), 16, Epoch, Error))
	{
		return FString();
	}
	return Epoch;
}

bool FNativeNodeRegistry::SetCursorKeyForAutomation(const TArray<uint8>& Key)
{
	const bool bTestContext = GIsAutomationTesting
		|| FAutomationTestFramework::Get().GetCurrentTest() != nullptr;
	if (!bTestContext || Key.Num() < 32)
	{
		return false;
	}
	CursorKey = Key;
	return true;
}

bool FNativeNodeRegistry::EncodeCursorForAutomation(
	const FString& Kind,
	const FString& QueryHash,
	const FString& SetFingerprint,
	const FString& Epoch,
	const int32 Offset,
	const int32 Total,
	const int32 PageSize,
	const int64 IssuedAt,
	const int64 ExpiresAt,
	FString& OutToken,
	FString& OutCode) const
{
	const bool bTestContext = GIsAutomationTesting
		|| FAutomationTestFramework::Get().GetCurrentTest() != nullptr;
	if (!bTestContext)
	{
		OutCode = TEXT("native-internal-error");
		return false;
	}
	FNativeCursorPayload Payload;
	Payload.Kind = Kind;
	Payload.QueryHash = QueryHash;
	Payload.SetFingerprint = SetFingerprint;
	Payload.Epoch = Epoch;
	Payload.Offset = Offset;
	Payload.Total = Total;
	Payload.PageSize = PageSize;
	Payload.IssuedAt = IssuedAt;
	Payload.ExpiresAt = ExpiresAt;
	return UEAgentKitNativeCursor::Encode(Payload, CursorKey, OutToken, OutCode);
}

bool FNativeNodeRegistry::ApplyPagination(
	const FString& Kind,
	const FString& QueryHash,
	const FString& SetFingerprint,
	const TArray<TSharedPtr<FJsonValue>>& AllItems,
	const FString& ItemsField,
	const int32 RequestedPageSize,
	const FString& Cursor,
	const bool bCatalogTruncated,
	const int32 CatalogAvailable,
	const TSharedPtr<FJsonObject>& InOutData,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	using namespace UEAgentKitCanonical;
	const int64 NowUnix = FDateTime::UtcNow().ToUnixTimestamp();
	const FString Epoch = ComputeCursorEpoch(Kind, SetFingerprint);
	if (Epoch.IsEmpty() || CursorKey.Num() < 32)
	{
		OutErrorCode = TEXT("native-internal-error");
		OutErrorMessage = TEXT("The pagination cursor key is unavailable.");
		return false;
	}

	int32 PageSize = RequestedPageSize > 0 ? RequestedPageSize : 64;
	if (PageSize < 1 || PageSize > UEAgentKitNativeCursor::MaxPageSize)
	{
		OutErrorCode = TEXT("native-schema-invalid-arguments");
		OutErrorMessage = TEXT("pageSize is out of range.");
		return false;
	}
	int32 Offset = 0;
	int64 IssuedAt = NowUnix;
	int64 ExpiresAt = NowUnix + CursorTtlSeconds;

	if (!Cursor.IsEmpty())
	{
		FNativeCursorPayload Payload;
		FString DecodeCode;
		FString DecodeMessage;
		if (!UEAgentKitNativeCursor::Decode(Cursor, CursorKey, NowUnix, Payload, DecodeCode, DecodeMessage))
		{
			OutErrorCode = DecodeCode.IsEmpty() ? TEXT("native-schema-invalid-arguments") : DecodeCode;
			OutErrorMessage = DecodeMessage.IsEmpty() ? TEXT("The cursor is invalid.") : DecodeMessage;
			return false;
		}
		if (Payload.Kind != Kind || Payload.QueryHash != QueryHash)
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("The cursor does not match this query.");
			return false;
		}
		if (Payload.SetFingerprint != SetFingerprint || Payload.Epoch != Epoch
			|| Payload.Total != AllItems.Num())
		{
			OutErrorCode = TEXT("native-gate-revision-conflict");
			OutErrorMessage = TEXT("The cursor is stale for the current action set.");
			return false;
		}
		if (RequestedPageSize > 0 && Payload.PageSize != RequestedPageSize)
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("pageSize does not match the cursor.");
			return false;
		}
		if (Payload.Offset < 0 || Payload.Offset > AllItems.Num())
		{
			OutErrorCode = TEXT("native-schema-invalid-arguments");
			OutErrorMessage = TEXT("The cursor offset is out of range.");
			return false;
		}
		PageSize = Payload.PageSize;
		Offset = Payload.Offset;
		IssuedAt = Payload.IssuedAt;
		ExpiresAt = Payload.ExpiresAt;
	}

	const int32 Total = AllItems.Num();
	const int32 Returned = FMath::Clamp(Total - Offset, 0, PageSize);
	const int32 NextOffset = Offset + Returned;
	const bool bHasMore = NextOffset < Total;

	TArray<TSharedPtr<FJsonValue>> PageItems;
	PageItems.Reserve(Returned);
	for (int32 Index = 0; Index < Returned; ++Index)
	{
		PageItems.Add(AllItems[Offset + Index]);
	}
	InOutData->SetArrayField(ItemsField, PageItems);

	const TSharedRef<FJsonObject> Pagination = MakeShared<FJsonObject>();
	Pagination->SetStringField(TEXT("kind"), Kind);
	Pagination->SetNumberField(TEXT("pageSize"), PageSize);
	Pagination->SetNumberField(TEXT("offset"), Offset);
	Pagination->SetNumberField(TEXT("returned"), Returned);
	Pagination->SetNumberField(TEXT("total"), Total);
	Pagination->SetBoolField(TEXT("hasMore"), bHasMore);
	Pagination->SetBoolField(TEXT("exhausted"), !bHasMore);
	Pagination->SetBoolField(TEXT("catalogTruncated"), bCatalogTruncated);
	Pagination->SetNumberField(TEXT("catalogAvailable"), CatalogAvailable);
	Pagination->SetStringField(TEXT("queryHash"), QueryHash);
	Pagination->SetStringField(TEXT("epoch"), Epoch);
	Pagination->SetStringField(TEXT("setFingerprint"), SetFingerprint);
	Pagination->SetNumberField(TEXT("issuedAtUnix"), static_cast<double>(IssuedAt));
	Pagination->SetNumberField(TEXT("expiresAtUnix"), static_cast<double>(ExpiresAt));
	Pagination->SetNumberField(TEXT("ttlSeconds"), CursorTtlSeconds);

	if (bHasMore)
	{
		FNativeCursorPayload NextPayload;
		NextPayload.Kind = Kind;
		NextPayload.QueryHash = QueryHash;
		NextPayload.SetFingerprint = SetFingerprint;
		NextPayload.Epoch = Epoch;
		NextPayload.Offset = NextOffset;
		NextPayload.Total = Total;
		NextPayload.PageSize = PageSize;
		NextPayload.IssuedAt = IssuedAt;
		NextPayload.ExpiresAt = ExpiresAt;
		FString NextCursor;
		FString EncodeCode;
		if (!UEAgentKitNativeCursor::Encode(NextPayload, CursorKey, NextCursor, EncodeCode))
		{
			OutErrorCode = TEXT("native-internal-error");
			OutErrorMessage = TEXT("The next cursor could not be encoded.");
			return false;
		}
		Pagination->SetStringField(TEXT("nextCursor"), NextCursor);
	}
	InOutData->SetObjectField(TEXT("pagination"), Pagination);
	return true;
}
