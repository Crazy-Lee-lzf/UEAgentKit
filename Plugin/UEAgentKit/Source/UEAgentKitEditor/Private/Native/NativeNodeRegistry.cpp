// Copyright Epic Games, Inc. All Rights Reserved.

#include "Native/NativeNodeRegistry.h"

#include "Native/NativeCanonicalJson.h"
#include "Native/NativeToolManifest.h"
#include "BlueprintContextSha256.h"

#include "BlueprintActionDatabase.h"
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
			UniqueRecords.Add(Action.Record.ActionId, MoveTemp(Action.Record));
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
	int32 MaxItems = 64;
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

	FNativeCatalogOptions Options;
	Options.MaxItems = MaxItems;
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
	if (bIncludeDescriptors)
	{
		TArray<TSharedPtr<FJsonValue>> DescriptorValues;
		DescriptorValues.Reserve(Catalog.Descriptors.Num());
		for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
		{
			if (Record.Descriptor.IsValid())
			{
				DescriptorValues.Add(MakeShared<FJsonValueObject>(Record.Descriptor));
			}
		}
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
