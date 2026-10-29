#include "Native/NativeNodeRegistry.h"

#include "Native/NativeCanonicalJson.h"
#include "Native/NativeCursorCodec.h"
#include "BlueprintContextSha256.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "Internationalization/Regex.h"
#include "Interfaces/IPluginManager.h"
#include "K2Node_CallFunction.h"
#include "BlueprintFunctionNodeSpawner.h"
#include "BlueprintNodeSpawner.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/AutomationTest.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UObjectIterator.h"

namespace UEAgentKitNativeRegistryTestPrivate
{
	const TCHAR* const DescriptorDomain = TEXT("ueak.node-descriptor/1");
	const TCHAR* const ActionDomain = TEXT("ueak.node-action/1");

	bool MatchesRegex(const FString& Pattern, const FString& Value)
	{
		const FRegexPattern Compiled(Pattern);
		FRegexMatcher Matcher(Compiled, Value);
		return Matcher.FindNext() && Matcher.GetMatchBeginning() == 0 && Matcher.GetMatchEnding() == Value.Len();
	}

	bool IsAscii(const FString& Value)
	{
		for (const TCHAR Character : Value)
		{
			if (Character > 127)
			{
				return false;
			}
		}
		return true;
	}

	bool EnsureRegistry(FAutomationTestBase& Test)
	{
		FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();
		if (Registry.GetState() == ENativeRegistryState::Ready)
		{
			return true;
		}
		if (!Registry.InitializeForAutomation())
		{
			Test.AddError(FString::Printf(
				TEXT("The native registry could not be enabled for automation (state=%d, code=%s)."),
				static_cast<int32>(Registry.GetState()),
				*Registry.GetUnavailableCode()));
			return false;
		}
		return true;
	}

	FNativeCatalogOptions CatalogOptions()
	{
		FNativeCatalogOptions Options;
		Options.MaxItems = 512;
		Options.MaxTemplateSpawns = 768;
		Options.TemplateBudgetMs = 20000.0;
		Options.MaxPinsPerDescriptor = 256;
		return Options;
	}

	TSharedPtr<FNativeCanonicalValue> ProjectionFromDiscoveryKey(
		const TSharedPtr<FJsonObject>& DiscoveryKey,
		FString& OutError)
	{
		using namespace UEAgentKitCanonical;
		if (!DiscoveryKey.IsValid())
		{
			OutError = TEXT("discoveryKey-missing");
			return nullptr;
		}
		const FNativeCanonicalValueRef Projection = FNativeCanonicalValue::MakeObject();
		for (const TCHAR* Field : { TEXT("nodeClass"), TEXT("spawnerClass"), TEXT("graphSchema"), TEXT("actionKind") })
		{
			FString Value;
			DiscoveryKey->TryGetStringField(Field, Value);
			Projection->SetObjectValue(Field, FNativeCanonicalValue::MakeString(Value));
		}
		const TSharedPtr<FJsonObject>* PayloadObject = nullptr;
		const FNativeCanonicalValueRef Payload = FNativeCanonicalValue::MakeObject();
		if (DiscoveryKey->TryGetObjectField(TEXT("payload"), PayloadObject) && PayloadObject != nullptr)
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*PayloadObject)->Values)
			{
				if (!Pair.Value.IsValid() || Pair.Value->Type != EJson::String)
				{
					OutError = TEXT("payload-not-string");
					return nullptr;
				}
				Payload->SetObjectValue(Pair.Key, FNativeCanonicalValue::MakeString(Pair.Value->AsString()));
			}
		}
		Projection->SetObjectValue(TEXT("payload"), Payload);
		return Projection;
	}

	bool ComputeActionId(
		const TSharedPtr<FJsonObject>& DiscoveryKey,
		FString& OutActionId,
		FString& OutError)
	{
		using namespace UEAgentKitCanonical;
		const TSharedPtr<FNativeCanonicalValue> Projection = ProjectionFromDiscoveryKey(DiscoveryKey, OutError);
		if (!Projection.IsValid())
		{
			return false;
		}
		FString Hash;
		if (!DomainHash(Projection.ToSharedRef(), ActionDomain, Hash, OutError))
		{
			return false;
		}
		OutActionId = FString(TEXT("action:")) + Hash.Mid(7, 32);
		return true;
	}

	bool ComputeDescriptorDigest(
		const TSharedPtr<FJsonObject>& Descriptor,
		FString& OutDigest,
		FString& OutError)
	{
		using namespace UEAgentKitCanonical;
		FNativeCanonicalValueRef Tree = FNativeCanonicalValue::MakeNull();
		if (!FromJsonObject(Descriptor, Tree, OutError))
		{
			return false;
		}
		const FNativeCanonicalValueRef Projection =
			Tree->CloneWithoutKeys({ TEXT("descriptorDigest") })->CloneWithoutNestedKey(TEXT("source"), TEXT("discoveredAtUtc"));
		return Fingerprint(Projection, DescriptorDomain, 16, OutDigest, OutError);
	}

	bool ContainsLocalizedField(const TSharedPtr<FJsonObject>& Object)
	{
		for (const TCHAR* Field : { TEXT("title"), TEXT("menuName"), TEXT("category"), TEXT("keywords"), TEXT("tooltip"), TEXT("friendlyName") })
		{
			if (Object->HasField(Field))
			{
				return true;
			}
		}
		return false;
	}

	/** ND-LOCALIZED-BAN: no display text may appear anywhere inside discoveryKey. */
	bool DescriptorHasLocalizedDiscoveryKey(const TSharedPtr<FJsonObject>& Descriptor)
	{
		const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
		if (!Descriptor.IsValid()
			|| !Descriptor->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey)
			|| DiscoveryKey == nullptr)
		{
			return false;
		}
		if (ContainsLocalizedField(*DiscoveryKey))
		{
			return true;
		}
		const TSharedPtr<FJsonObject>* Payload = nullptr;
		if ((*DiscoveryKey)->TryGetObjectField(TEXT("payload"), Payload) && Payload != nullptr)
		{
			return ContainsLocalizedField(*Payload);
		}
		return false;
	}

	bool ValidateDescriptorShape(
		FAutomationTestBase& Test,
		const TSharedPtr<FJsonObject>& Descriptor,
		const FString& Context)
	{
		if (!Descriptor.IsValid())
		{
			Test.AddError(FString::Printf(TEXT("%s: descriptor is not an object."), *Context));
			return false;
		}

		static const TSet<FString> RootFields = {
			TEXT("schemaVersion"), TEXT("actionId"), TEXT("discoveryKey"), TEXT("title"), TEXT("menuName"), TEXT("category"),
			TEXT("keywords"), TEXT("searchTokens"), TEXT("graphKinds"), TEXT("graphSchemas"), TEXT("pins"), TEXT("constraints"),
			TEXT("source"), TEXT("risk"), TEXT("capability"), TEXT("trustLevel"), TEXT("descriptorDigest"),
		};
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Descriptor->Values)
		{
			if (!RootFields.Contains(Pair.Key))
			{
				Test.AddError(FString::Printf(TEXT("%s: unexpected top-level field '%s'."), *Context, *Pair.Key));
				return false;
			}
		}
		for (const TCHAR* Required : {
				 TEXT("schemaVersion"), TEXT("actionId"), TEXT("discoveryKey"), TEXT("title"), TEXT("menuName"), TEXT("category"),
				 TEXT("keywords"), TEXT("searchTokens"), TEXT("graphKinds"), TEXT("graphSchemas"), TEXT("pins"), TEXT("constraints"),
				 TEXT("source"), TEXT("risk"), TEXT("capability"), TEXT("trustLevel"), TEXT("descriptorDigest") })
		{
			if (!Descriptor->HasField(Required))
			{
				Test.AddError(FString::Printf(TEXT("%s: missing required field '%s'."), *Context, Required));
				return false;
			}
		}

		FString SchemaVersion;
		Descriptor->TryGetStringField(TEXT("schemaVersion"), SchemaVersion);
		if (SchemaVersion != TEXT("ueak.node-descriptor/1.0"))
		{
			Test.AddError(FString::Printf(TEXT("%s: unexpected schemaVersion '%s'."), *Context, *SchemaVersion));
			return false;
		}

		const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
		if (!Descriptor->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey) || DiscoveryKey == nullptr)
		{
			Test.AddError(FString::Printf(TEXT("%s: discoveryKey missing."), *Context));
			return false;
		}
		static const TSet<FString> DiscoveryFields = {
			TEXT("nodeClass"), TEXT("spawnerClass"), TEXT("graphSchema"), TEXT("actionKind"), TEXT("payload"),
		};
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*DiscoveryKey)->Values)
		{
			if (!DiscoveryFields.Contains(Pair.Key))
			{
				Test.AddError(FString::Printf(TEXT("%s: unexpected discoveryKey field '%s'."), *Context, *Pair.Key));
				return false;
			}
		}
		if (ContainsLocalizedField(*DiscoveryKey))
		{
			Test.AddError(FString::Printf(TEXT("%s: localized field leaked into discoveryKey."), *Context));
			return false;
		}

		FString ActionKind;
		(*DiscoveryKey)->TryGetStringField(TEXT("actionKind"), ActionKind);
		const TMap<FString, FString> PayloadKeys = {
			{ TEXT("callFunction"), TEXT("functionPath") },
			{ TEXT("event"), TEXT("eventName") },
			{ TEXT("variableGet"), TEXT("variableName") },
			{ TEXT("variableSet"), TEXT("variableName") },
			{ TEXT("macro"), TEXT("macroGraph") },
			{ TEXT("cast"), TEXT("targetClass") },
			{ TEXT("delegate"), TEXT("delegateName") },
			{ TEXT("component"), TEXT("componentClass") },
			{ TEXT("special"), TEXT("specialKind") },
		};
		const FString* PayloadKey = PayloadKeys.Find(ActionKind);
		if (PayloadKey == nullptr)
		{
			Test.AddError(FString::Printf(TEXT("%s: unknown actionKind '%s'."), *Context, *ActionKind));
			return false;
		}
		const TSharedPtr<FJsonObject>* Payload = nullptr;
		FString PayloadValue;
		if (!(*DiscoveryKey)->TryGetObjectField(TEXT("payload"), Payload) || Payload == nullptr
			|| !(*Payload)->TryGetStringField(*PayloadKey, PayloadValue) || PayloadValue.IsEmpty())
		{
			Test.AddError(FString::Printf(TEXT("%s: payload.%s is missing."), *Context, **PayloadKey));
			return false;
		}
		if (ContainsLocalizedField(*Payload))
		{
			Test.AddError(FString::Printf(TEXT("%s: localized field leaked into discoveryKey.payload."), *Context));
			return false;
		}

		FString ActionId;
		Descriptor->TryGetStringField(TEXT("actionId"), ActionId);
		if (!MatchesRegex(TEXT("^action:[0-9a-f]{32}$"), ActionId))
		{
			Test.AddError(FString::Printf(TEXT("%s: actionId shape is invalid."), *Context));
			return false;
		}
		FString ExpectedActionId;
		FString Error;
		if (!ComputeActionId(*DiscoveryKey, ExpectedActionId, Error) || ExpectedActionId != ActionId)
		{
			Test.AddError(FString::Printf(TEXT("%s: actionId does not match discoveryKey (%s)."), *Context, *Error));
			return false;
		}

		FString Digest;
		Descriptor->TryGetStringField(TEXT("descriptorDigest"), Digest);
		if (!MatchesRegex(TEXT("^sha256:[0-9a-f]{16}$"), Digest))
		{
			Test.AddError(FString::Printf(TEXT("%s: descriptorDigest shape is invalid."), *Context));
			return false;
		}
		FString ExpectedDigest;
		if (!ComputeDescriptorDigest(Descriptor, ExpectedDigest, Error) || ExpectedDigest != Digest)
		{
			Test.AddError(FString::Printf(TEXT("%s: descriptorDigest does not match the projection (%s)."), *Context, *Error));
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* GraphKinds = nullptr;
		if (!Descriptor->TryGetArrayField(TEXT("graphKinds"), GraphKinds) || GraphKinds == nullptr || GraphKinds->Num() == 0)
		{
			Test.AddError(FString::Printf(TEXT("%s: graphKinds must be non-empty."), *Context));
			return false;
		}
		static const TSet<FString> GraphKindVocabulary = {
			TEXT("event"), TEXT("function"), TEXT("macro"), TEXT("ubergraph"), TEXT("delegate-signature"), TEXT("other"),
		};
		for (const TSharedPtr<FJsonValue>& Kind : *GraphKinds)
		{
			const FString KindText = Kind.IsValid() ? Kind->AsString() : FString();
			if (!GraphKindVocabulary.Contains(KindText) || KindText == TEXT("ubergraph"))
			{
				Test.AddError(FString::Printf(TEXT("%s: graphKind '%s' is not allowed."), *Context, *KindText));
				return false;
			}
		}

		const TArray<TSharedPtr<FJsonValue>>* SearchTokens = nullptr;
		if (!Descriptor->TryGetArrayField(TEXT("searchTokens"), SearchTokens) || SearchTokens == nullptr)
		{
			Test.AddError(FString::Printf(TEXT("%s: searchTokens missing."), *Context));
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Token : *SearchTokens)
		{
			if (!Token.IsValid() || Token->Type != EJson::String || !IsAscii(Token->AsString()))
			{
				Test.AddError(FString::Printf(TEXT("%s: searchTokens must be ASCII strings."), *Context));
				return false;
			}
		}

		const TArray<TSharedPtr<FJsonValue>>* Pins = nullptr;
		if (!Descriptor->TryGetArrayField(TEXT("pins"), Pins) || Pins == nullptr || Pins->Num() == 0)
		{
			Test.AddError(FString::Printf(TEXT("%s: pins must be non-empty."), *Context));
			return false;
		}
		static const TSet<FString> PinTypeFields = {
			TEXT("category"), TEXT("subCategory"), TEXT("subCategoryObject"), TEXT("container"),
			TEXT("isReference"), TEXT("isConst"), TEXT("isWeakPointer"),
		};
		for (const TSharedPtr<FJsonValue>& PinValue : *Pins)
		{
			const TSharedPtr<FJsonObject> Pin = PinValue.IsValid() ? PinValue->AsObject() : nullptr;
			if (!Pin.IsValid() || !Pin->HasField(TEXT("name")) || !Pin->HasField(TEXT("direction"))
				|| !Pin->HasField(TEXT("kind")) || !Pin->HasField(TEXT("type")))
			{
				Test.AddError(FString::Printf(TEXT("%s: pin template shape is invalid."), *Context));
				return false;
			}
			FString Direction;
			FString Kind;
			Pin->TryGetStringField(TEXT("direction"), Direction);
			Pin->TryGetStringField(TEXT("kind"), Kind);
			if ((Direction != TEXT("input") && Direction != TEXT("output")) || (Kind != TEXT("exec") && Kind != TEXT("data")))
			{
				Test.AddError(FString::Printf(TEXT("%s: pin direction/kind is invalid."), *Context));
				return false;
			}
			const TSharedPtr<FJsonObject>* PinType = nullptr;
			if (!Pin->TryGetObjectField(TEXT("type"), PinType) || PinType == nullptr)
			{
				Test.AddError(FString::Printf(TEXT("%s: pin type missing."), *Context));
				return false;
			}
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*PinType)->Values)
			{
				if (!PinTypeFields.Contains(Pair.Key))
				{
					Test.AddError(FString::Printf(TEXT("%s: unexpected pin type field '%s'."), *Context, *Pair.Key));
					return false;
				}
			}
			for (const FString& Required : PinTypeFields)
			{
				if (!(*PinType)->HasField(Required))
				{
					Test.AddError(FString::Printf(TEXT("%s: pin type field '%s' missing."), *Context, *Required));
					return false;
				}
			}
			FString Category;
			(*PinType)->TryGetStringField(TEXT("category"), Category);
			if ((Kind == TEXT("exec") && Category != TEXT("exec")) || (Kind == TEXT("data") && Category == TEXT("exec")))
			{
				Test.AddError(FString::Printf(TEXT("%s: pin kind/type.category mismatch."), *Context));
				return false;
			}
		}

		const TSharedPtr<FJsonObject>* Source = nullptr;
		if (!Descriptor->TryGetObjectField(TEXT("source"), Source) || Source == nullptr)
		{
			Test.AddError(FString::Printf(TEXT("%s: source missing."), *Context));
			return false;
		}
		FString SourceKind;
		(*Source)->TryGetStringField(TEXT("kind"), SourceKind);
		FString DatabaseFingerprint;
		(*Source)->TryGetStringField(TEXT("actionDatabaseFingerprint"), DatabaseFingerprint);
		FString EngineVersion;
		(*Source)->TryGetStringField(TEXT("engineVersion"), EngineVersion);
		FString PluginBuildHash;
		(*Source)->TryGetStringField(TEXT("pluginBuildHash"), PluginBuildHash);
		FString DiscoveredAt;
		(*Source)->TryGetStringField(TEXT("discoveredAtUtc"), DiscoveredAt);
		if (SourceKind != TEXT("FBlueprintActionDatabase")
			|| !MatchesRegex(TEXT("^sha256:[0-9a-f]{16}$"), DatabaseFingerprint)
			|| EngineVersion.IsEmpty() || PluginBuildHash.IsEmpty() || DiscoveredAt.IsEmpty())
		{
			Test.AddError(FString::Printf(TEXT("%s: source fields are invalid."), *Context));
			return false;
		}

		return true;
	}

	FString GoldenPath()
	{
		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit"));
		return Plugin.IsValid()
			? FPaths::Combine(Plugin->GetBaseDir(), TEXT("Resources/NativeTools/Golden/node-descriptor-golden.v1.json"))
			: FString();
	}

	FString ExportPath()
	{
		return FPaths::Combine(
			FPaths::ProjectSavedDir(),
			TEXT("UEAgentKit"),
			TEXT("R1Native"),
			TEXT("catalog-export.json"));
	}

	bool WriteExport(const FNativeCatalogResult& Catalog, FAutomationTestBase& Test)
	{
		const TSharedRef<FJsonObject> Document = MakeShared<FJsonObject>();
		Document->SetStringField(TEXT("schemaVersion"), TEXT("ueak.native.catalog-export/1.0"));
		Document->SetStringField(TEXT("generatedAtUtc"), FDateTime::UtcNow().ToIso8601());
		Document->SetObjectField(TEXT("summary"), Catalog.MakeSummaryJson());
		TArray<TSharedPtr<FJsonValue>> Descriptors;
		Descriptors.Reserve(Catalog.Descriptors.Num());
		for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
		{
			if (Record.Descriptor.IsValid())
			{
				Descriptors.Add(MakeShared<FJsonValueObject>(Record.Descriptor));
			}
		}
		Document->SetArrayField(TEXT("descriptors"), Descriptors);

		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Document, Writer))
		{
			Test.AddError(TEXT("The catalog export could not be serialized."));
			return false;
		}
		const FString Path = ExportPath();
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		if (!FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			Test.AddError(FString::Printf(TEXT("The catalog export could not be written to %s."), *Path));
			return false;
		}
		Test.AddInfo(FString::Printf(TEXT("Wrote catalog export: %s"), *Path));
		return true;
	}

	TArray<FString> DescriptorActionIds(const FNativeCatalogResult& Catalog)
	{
		TArray<FString> Ids;
		Ids.Reserve(Catalog.Descriptors.Num());
		for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
		{
			Ids.Add(Record.ActionId);
		}
		return Ids;
	}

	int32 CountDirtyGamePackages()
	{
		int32 Count = 0;
		for (TObjectIterator<UPackage> It; It; ++It)
		{
			if (It->IsDirty() && It->GetName().StartsWith(TEXT("/Game/")))
			{
				++Count;
			}
		}
		return Count;
	}

	int32 CountGamePackages()
	{
		int32 Count = 0;
		for (TObjectIterator<UPackage> It; It; ++It)
		{
			if (It->GetName().StartsWith(TEXT("/Game/")))
			{
				++Count;
			}
		}
		return Count;
	}

	int32 CountRegisteredGameAssets()
	{
		IAssetRegistry& Registry = FAssetRegistryModule::GetRegistry();
		TArray<FAssetData> Assets;
		Registry.GetAssetsByPath(FName(TEXT("/Game")), Assets, true);
		return Assets.Num();
	}

	const TCHAR* const FixturePackageName = TEXT("/Game/R1Native/BP_R1ContextFixture");
	const TCHAR* const FixtureObjectPath = TEXT("/Game/R1Native/BP_R1ContextFixture.BP_R1ContextFixture");

	FString FixtureFilename(UBlueprint* Blueprint)
	{
		FString Filename;
		if (Blueprint == nullptr || Blueprint->GetOutermost() == nullptr)
		{
			return FString();
		}
		if (!FPackageName::TryConvertLongPackageNameToFilename(
				Blueprint->GetOutermost()->GetName(),
				Filename,
				FPackageName::GetAssetPackageExtension()))
		{
			return FString();
		}
		return Filename;
	}

	FString FileRevision(const FString& Filename)
	{
		FString Hex;
		if (Filename.IsEmpty() || !FPaths::FileExists(Filename) || !FBlueprintContextSha256::HashFile(Filename, Hex))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Hex.ToLower();
	}

	FString ComputeFixtureGraphFingerprint(UEdGraph* Graph)
	{
		if (Graph == nullptr)
		{
			return FString();
		}
		TArray<FString> Lines;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			const FString NodeGuid = Node->NodeGuid.ToString(EGuidFormats::Digits);
			Lines.Add(FString::Printf(
				TEXT("node|%s|%s|%d|%d|%s"),
				*NodeGuid,
				*Node->GetClass()->GetPathName(),
				Node->NodePosX,
				Node->NodePosY,
				*Node->GetNodeTitle(ENodeTitleType::ListView).ToString()));
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr)
				{
					continue;
				}
				Lines.Add(FString::Printf(
					TEXT("pin|%s|%s|%s|%s|%d"),
					*NodeGuid,
					*Pin->PinName.ToString(),
					Pin->Direction == EGPD_Input ? TEXT("in") : TEXT("out"),
					*Pin->PinType.PinCategory.ToString(),
					Pin->LinkedTo.Num()));
				for (UEdGraphPin* Linked : Pin->LinkedTo)
				{
					if (Linked != nullptr && Linked->GetOwningNode() != nullptr)
					{
						Lines.Add(FString::Printf(
							TEXT("link|%s|%s|%s|%s"),
							*NodeGuid,
							*Pin->PinName.ToString(),
							*Linked->GetOwningNode()->NodeGuid.ToString(EGuidFormats::Digits),
							*Linked->PinName.ToString()));
					}
				}
			}
		}
		Lines.Sort();
		FString Hex;
		if (!FBlueprintContextSha256::HashText(FString::Join(Lines, TEXT("\n")), Hex))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Hex.ToLower();
	}

	bool SaveFixture(FAutomationTestBase& Test, UBlueprint* Blueprint, const bool bNotifyCreated)
	{
		if (Blueprint == nullptr || Blueprint->GetOutermost() == nullptr)
		{
			Test.AddError(TEXT("Fixture Blueprint is missing its package."));
			return false;
		}
		const FString Filename = FixtureFilename(Blueprint);
		if (Filename.IsEmpty())
		{
			Test.AddError(TEXT("Fixture package filename could not be resolved."));
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		// Fixture setup may write: force the package dirty so the generated node
		// GUID is actually persisted (a clean package can be skipped by save).
		Blueprint->GetOutermost()->MarkPackageDirty();
		FSavePackageArgs Args;
		Args.TopLevelFlags = RF_Public | RF_Standalone;
		Args.SaveFlags = SAVE_NoError;
		const bool bSaved = UPackage::SavePackage(Blueprint->GetOutermost(), Blueprint, *Filename, Args);
		if (bSaved && bNotifyCreated)
		{
			FAssetRegistryModule::AssetCreated(Blueprint);
		}
		if (!bSaved)
		{
			Test.AddError(FString::Printf(TEXT("Fixture save failed: %s"), *Filename));
			return false;
		}
		return !Blueprint->GetOutermost()->IsDirty();
	}

	/**
	 * Creates (or reuses) a disposable /Game fixture in the disposable host.
	 * All writes happen here, before the read-only measurement.
	 */
	bool EnsureContextFixture(
		FAutomationTestBase& Test,
		UBlueprint*& OutBlueprint,
		UEdGraph*& OutGraph,
		UK2Node_CallFunction*& OutNode,
		UEdGraphPin*& OutExecPin,
		UEdGraphPin*& OutDataPin)
	{
		UBlueprint* Blueprint = FindObject<UBlueprint>(nullptr, FixtureObjectPath);
		bool bCreated = false;
		if (Blueprint == nullptr)
		{
			Blueprint = LoadObject<UBlueprint>(nullptr, FixtureObjectPath);
		}
		if (Blueprint == nullptr)
		{
			UPackage* Package = CreatePackage(FixturePackageName);
			if (Package == nullptr)
			{
				Test.AddError(TEXT("Fixture package could not be created."));
				return false;
			}
			Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				TEXT("BP_R1ContextFixture"),
				BPTYPE_Normal,
				UBlueprint::StaticClass(),
				UBlueprintGeneratedClass::StaticClass());
			bCreated = true;
		}
		if (Blueprint == nullptr)
		{
			Test.AddError(TEXT("Fixture Blueprint could not be created or loaded."));
			return false;
		}

		UEdGraph* Graph = Blueprint->UbergraphPages.Num() > 0 ? Blueprint->UbergraphPages[0].Get() : nullptr;
		if (Graph == nullptr)
		{
			Test.AddError(TEXT("Fixture Blueprint has no event graph."));
			return false;
		}

		UK2Node_CallFunction* CallNode = nullptr;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			UK2Node_CallFunction* Candidate = Cast<UK2Node_CallFunction>(Node);
			if (Candidate != nullptr && Candidate->GetFunctionName() == FName(TEXT("PrintString")))
			{
				CallNode = Candidate;
				break;
			}
		}

		bool bModified = false;
		if (CallNode == nullptr)
		{
			CallNode = NewObject<UK2Node_CallFunction>(Graph);
			CallNode->FunctionReference.SetExternalMember(
				GET_FUNCTION_NAME_CHECKED(UKismetSystemLibrary, PrintString),
				UKismetSystemLibrary::StaticClass());
			CallNode->NodePosX = 0;
			CallNode->NodePosY = 0;
			CallNode->CreateNewGuid();
			CallNode->AllocateDefaultPins();
			Graph->AddNode(CallNode, true, false);
			bModified = true;
		}
		else if (!CallNode->NodeGuid.IsValid())
		{
			// Repair fixtures created before the GUID was assigned, so the
			// engine does not warn about non-deterministic cooking state.
			CallNode->CreateNewGuid();
			bModified = true;
		}

		if (bModified)
		{
			FKismetEditorUtilities::CompileBlueprint(Blueprint);
		}
		if (!SaveFixture(Test, Blueprint, bCreated))
		{
			return false;
		}

		UEdGraphPin* ExecPin = nullptr;
		UEdGraphPin* DataPin = nullptr;
		for (UEdGraphPin* Pin : CallNode->Pins)
		{
			if (Pin == nullptr)
			{
				continue;
			}
			if (Pin->PinName == FName(TEXT("then")) && Pin->Direction == EGPD_Output)
			{
				ExecPin = Pin;
			}
			else if (Pin->PinName == FName(TEXT("InString")) && Pin->Direction == EGPD_Input)
			{
				DataPin = Pin;
			}
		}
		if (!Test.TestNotNull(TEXT("fixture PrintString node"), CallNode)
			|| !Test.TestNotNull(TEXT("fixture exec pin"), ExecPin)
			|| !Test.TestNotNull(TEXT("fixture data pin"), DataPin))
		{
			return false;
		}

		OutBlueprint = Blueprint;
		OutGraph = Graph;
		OutNode = CallNode;
		OutExecPin = ExecPin;
		OutDataPin = DataPin;
		return true;
	}

	FString GuidString(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::Digits);
	}

	FString CursorVectorsPath()
	{
		const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit"));
		return Plugin.IsValid()
			? FPaths::Combine(Plugin->GetBaseDir(), TEXT("Resources/NativeTools/Golden/r1-cursor-vectors.json"))
			: FString();
	}

	bool HexToBytes(const FString& Hex, TArray<uint8>& OutBytes)
	{
		OutBytes.Reset();
		if (Hex.Len() % 2 != 0)
		{
			return false;
		}
		auto Nibble = [](const TCHAR Character) -> int32 {
			if (Character >= TEXT('0') && Character <= TEXT('9')) { return Character - TEXT('0'); }
			if (Character >= TEXT('a') && Character <= TEXT('f')) { return Character - TEXT('a') + 10; }
			if (Character >= TEXT('A') && Character <= TEXT('F')) { return Character - TEXT('A') + 10; }
			return INDEX_NONE;
		};
		for (int32 Index = 0; Index < Hex.Len(); Index += 2)
		{
			const int32 High = Nibble(Hex[Index]);
			const int32 Low = Nibble(Hex[Index + 1]);
			if (High == INDEX_NONE || Low == INDEX_NONE)
			{
				return false;
			}
			OutBytes.Add(static_cast<uint8>((High << 4) | Low));
		}
		return true;
	}

	TSharedPtr<FJsonObject> InvokeToolData(
		FAutomationTestBase& Test,
		FNativeNodeRegistry& Registry,
		const FString& ToolId,
		const TSharedPtr<FJsonObject>& Arguments,
		FString& OutErrorCode,
		const bool bReportFailure = true)
	{
		TSharedPtr<FJsonObject> Result;
		FString ErrorMessage;
		if (!Registry.TryInvokeShadowTool(ToolId, TEXT("1.0.0"), Arguments, Result, OutErrorCode, ErrorMessage))
		{
			if (bReportFailure)
			{
				Test.AddError(FString::Printf(TEXT("%s failed: %s (%s)"), *ToolId, *OutErrorCode, *ErrorMessage));
			}
			return nullptr;
		}
		const TSharedPtr<FJsonObject>* Data = nullptr;
		if (!Result.IsValid() || !Result->TryGetObjectField(TEXT("data"), Data) || Data == nullptr)
		{
			Test.AddError(FString::Printf(TEXT("%s returned no data."), *ToolId));
			return nullptr;
		}
		return *Data;
	}

	TArray<FString> DescriptorIds(const TSharedPtr<FJsonObject>& Data)
	{
		TArray<FString> Ids;
		const TArray<TSharedPtr<FJsonValue>>* Descriptors = nullptr;
		if (Data.IsValid() && Data->TryGetArrayField(TEXT("descriptors"), Descriptors) && Descriptors != nullptr)
		{
			for (const TSharedPtr<FJsonValue>& Value : *Descriptors)
			{
				const TSharedPtr<FJsonObject> Item = Value.IsValid() ? Value->AsObject() : nullptr;
				FString ActionId;
				if (Item.IsValid() && Item->TryGetStringField(TEXT("actionId"), ActionId))
				{
					Ids.Add(ActionId);
				}
			}
		}
		return Ids;
	}

	int32 UniqueIdCount(const TArray<FString>& Ids)
	{
		TSet<FString> Unique;
		for (const FString& Id : Ids)
		{
			Unique.Add(Id);
		}
		return Unique.Num();
	}

	bool WriteContextExport(
		FAutomationTestBase& Test,
		const FNativeContextResult& Palette,
		const FNativeContextResult& Node,
		const FNativeContextResult& ExecPin,
		const FNativeContextResult& DataPin,
		const FString& NonK2Code)
	{
		const TSharedRef<FJsonObject> Document = MakeShared<FJsonObject>();
		Document->SetStringField(TEXT("schemaVersion"), TEXT("ueak.native.context-export/1.0"));
		Document->SetStringField(TEXT("generatedAtUtc"), FDateTime::UtcNow().ToIso8601());
		Document->SetObjectField(TEXT("palette"), Palette.MakeJson());
		Document->SetObjectField(TEXT("node"), Node.MakeJson());
		Document->SetObjectField(TEXT("execPin"), ExecPin.MakeJson());
		Document->SetObjectField(TEXT("dataPin"), DataPin.MakeJson());
		Document->SetStringField(TEXT("nonK2ErrorCode"), NonK2Code);

		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Document, Writer))
		{
			Test.AddError(TEXT("The context export could not be serialized."));
			return false;
		}
		const FString Path = FPaths::Combine(
			FPaths::ProjectSavedDir(),
			TEXT("UEAgentKit"),
			TEXT("R1Native"),
			TEXT("context-export.json"));
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		if (!FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			Test.AddError(FString::Printf(TEXT("The context export could not be written to %s."), *Path));
			return false;
		}
		Test.AddInfo(FString::Printf(TEXT("Wrote context export: %s"), *Path));
		return true;
	}
}

#if WITH_EDITOR && WITH_DEV_AUTOMATION_TESTS

using namespace UEAgentKitNativeRegistryTestPrivate;

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeRegistryLifecycleTest,
	"UEAgentKit.Native.R1Lifecycle",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeRegistryLifecycleTest::RunTest(const FString& Parameters)
{
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();

	TestEqual(TEXT("off mode parses"), static_cast<int32>(FNativeNodeRegistry::ParseMode(TEXT("OFF"))), static_cast<int32>(ENativeRegistryMode::Off));
	TestEqual(TEXT("shadow mode parses"), static_cast<int32>(FNativeNodeRegistry::ParseMode(TEXT("shadow"))), static_cast<int32>(ENativeRegistryMode::Shadow));
	TestEqual(TEXT("unknown mode fails closed"), static_cast<int32>(FNativeNodeRegistry::ParseMode(TEXT("bogus"))), static_cast<int32>(ENativeRegistryMode::Off));

	Registry.Shutdown();
	Registry.Initialize(ENativeRegistryMode::Off);
	TestEqual(TEXT("registry state is off"), static_cast<int32>(Registry.GetState()), static_cast<int32>(ENativeRegistryState::Off));
	TestFalse(TEXT("off registry is not available"), Registry.IsAvailable());
	TestFalse(TEXT("off registry exposes no tools"), Registry.HasTool(TEXT("graph.nodeRegistry.catalog")));
	const FNativeCatalogResult OffCatalog = Registry.BuildCatalog(FNativeCatalogOptions());
	TestFalse(TEXT("off registry cannot build a catalog"), OffCatalog.bOk);
	TestEqual(TEXT("off registry returns the stable code"), OffCatalog.ErrorCode, FString(TEXT("native-registry-unavailable")));

	if (!TestTrue(TEXT("automation seam enables the registry"), Registry.InitializeForAutomation()))
	{
		return false;
	}
	TestTrue(TEXT("shadow registry is ready"), Registry.IsAvailable());
	TestEqual(TEXT("shadow mode is active"), static_cast<int32>(Registry.GetMode()), static_cast<int32>(ENativeRegistryMode::Shadow));
	TestEqual(TEXT("manifest loads three tools"), Registry.GetManifestSummary().ToolCount, 3);
	TestTrue(TEXT("manifest hash exists"), !Registry.GetManifestSummary().Hash.IsEmpty());
	TestTrue(TEXT("catalog tool is declared"), Registry.HasTool(TEXT("graph.nodeRegistry.catalog")));
	TestTrue(TEXT("fingerprint tool is declared"), Registry.HasTool(TEXT("graph.nodeRegistry.fingerprint")));
	TestTrue(TEXT("context filter tool is declared"), Registry.HasTool(TEXT("graph.nodeRegistry.filterContext")));
	TestFalse(TEXT("write tools are not declared"), Registry.HasTool(TEXT("graph.layout.applyTestOnly")));

	Registry.Initialize(ENativeRegistryMode::Shadow);
	TestTrue(TEXT("initialization is idempotent"), Registry.IsAvailable());
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeRegistryCatalogTest,
	"UEAgentKit.Native.R1Catalog",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeRegistryCatalogTest::RunTest(const FString& Parameters)
{
	if (!EnsureRegistry(*this))
	{
		return false;
	}
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();

	const FNativeCatalogResult First = Registry.BuildCatalog(CatalogOptions());
	if (!TestTrue(TEXT("catalog build succeeds"), First.bOk))
	{
		AddError(FString::Printf(TEXT("Catalog failed: %s (%s)"), *First.ErrorCode, *First.ErrorMessage));
		return false;
	}
	TestTrue(TEXT("action database is non-empty"), First.TotalSpawners > 0);
	TestTrue(TEXT("multiple owners are present"), First.OwnerCount > 0);
	TestTrue(
		TEXT("catalog returns at least 200 descriptors"),
		First.Descriptors.Num() >= 200);
	AddInfo(FString::Printf(
		TEXT("spawners=%d owners=%d descriptors=%d available=%d unsupported=%d cached=%d created=%d pins=%d durationMs=%.1f fingerprint=%s pluginSetHash=%s"),
		First.TotalSpawners,
		First.OwnerCount,
		First.Descriptors.Num(),
		First.TotalDescriptorsAvailable,
		First.TotalUnsupported,
		First.TemplateNodesCached,
		First.TemplateNodesCreated,
		First.PinsAllocated,
		First.DurationMs,
		*First.ActionDatabaseFingerprint,
		*First.Identity.PluginSetHash));

	TestTrue(TEXT("action database fingerprint has the contract shape"), MatchesRegex(TEXT("^sha256:[0-9a-f]{16}$"), First.ActionDatabaseFingerprint));
	TestTrue(TEXT("plugin set hash exists"), !First.Identity.PluginSetHash.IsEmpty());
	TestTrue(TEXT("plugin build hash exists"), !First.Identity.PluginBuildHash.IsEmpty());
	TestTrue(TEXT("editor session id exists"), !First.Identity.EditorSessionId.IsEmpty());
	TestTrue(TEXT("engine version exists"), !First.Identity.EngineVersion.IsEmpty());
	TestTrue(TEXT("engine version is 5.6.x"), First.Identity.EngineVersion.StartsWith(TEXT("5.6.")));

	TestTrue(TEXT("unsupported list is bounded"), First.Unsupported.Num() <= 64);
	TestEqual(TEXT("complete is exactly the absence of unsupported entries"), First.bComplete, First.TotalUnsupported == 0);
	TestTrue(TEXT("descriptor list is not truncated at 512"), First.Descriptors.Num() <= 512);

	int32 HistogramTotal = 0;
	TArray<FString> ReasonSummary;
	for (const TPair<FString, int32>& Pair : First.UnsupportedByReason)
	{
		HistogramTotal += Pair.Value;
		ReasonSummary.Add(FString::Printf(TEXT("%s=%d"), *Pair.Key, Pair.Value));
	}
	ReasonSummary.Sort();
	TestTrue(TEXT("unsupported reason histogram is populated"), First.UnsupportedByReason.Num() > 0);
	TestEqual(TEXT("unsupported histogram counts every unsupported entry"), HistogramTotal, First.TotalUnsupported);
	AddInfo(FString::Printf(TEXT("unsupportedByReason: %s"), *FString::Join(ReasonSummary, TEXT(", "))));

	TSet<FString> UniqueIds;
	bool bSorted = true;
	for (int32 Index = 0; Index < First.Descriptors.Num(); ++Index)
	{
		const FNativeNodeDescriptorRecord& Record = First.Descriptors[Index];
		UniqueIds.Add(Record.ActionId);
		if (Index > 0 && UEAgentKitCanonical::CompareUtf8(First.Descriptors[Index - 1].ActionId, Record.ActionId) >= 0)
		{
			bSorted = false;
		}
	}
	TestEqual(TEXT("descriptor action ids are unique"), UniqueIds.Num(), First.Descriptors.Num());
	TestTrue(TEXT("descriptors are sorted by actionId"), bSorted);

	for (int32 Index = 0; Index < FMath::Min(First.Descriptors.Num(), 200); ++Index)
	{
		if (!ValidateDescriptorShape(*this, First.Descriptors[Index].Descriptor, FString::Printf(TEXT("catalog[%d]"), Index)))
		{
			return false;
		}
	}

	const FNativeCatalogResult Second = Registry.BuildCatalog(CatalogOptions());
	if (!TestTrue(TEXT("second catalog build succeeds"), Second.bOk))
	{
		return false;
	}
	TestEqual(TEXT("fingerprint is stable across calls"), Second.ActionDatabaseFingerprint, First.ActionDatabaseFingerprint);
	TestTrue(
		TEXT("action id set is stable across calls"),
		DescriptorActionIds(Second) == DescriptorActionIds(First));

	if (!WriteExport(First, *this))
	{
		return false;
	}

	TestTrue(TEXT("catalog evidence summary is valid"), First.MakeSummaryJson()->HasField(TEXT("actionDatabaseFingerprint")));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeDescriptorParityTest,
	"UEAgentKit.Native.R1DescriptorParity",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeDescriptorParityTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitCanonical;
	if (!EnsureRegistry(*this))
	{
		return false;
	}

	const FString Path = GoldenPath();
	FString Text;
	if (!TestTrue(TEXT("golden fixture exists"), !Path.IsEmpty() && FFileHelper::LoadFileToString(Text, *Path)))
	{
		AddError(FString::Printf(TEXT("Golden fixture missing at %s"), *Path));
		return false;
	}
	TSharedPtr<FJsonObject> Golden;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
	if (!TestTrue(TEXT("golden fixture parses"), FJsonSerializer::Deserialize(Reader, Golden) && Golden.IsValid()))
	{
		return false;
	}
	const TArray<TSharedPtr<FJsonValue>>* Samples = nullptr;
	if (!TestTrue(TEXT("golden fixture has samples"), Golden->TryGetArrayField(TEXT("samples"), Samples) && Samples != nullptr))
	{
		return false;
	}
	TestEqual(TEXT("golden fixture has 200 samples"), Samples->Num(), 200);

	int32 Compared = 0;
	int32 DescriptorCompared = 0;
	for (const TSharedPtr<FJsonValue>& SampleValue : *Samples)
	{
		const TSharedPtr<FJsonObject> Sample = SampleValue.IsValid() ? SampleValue->AsObject() : nullptr;
		if (!TestTrue(TEXT("golden sample is an object"), Sample.IsValid()))
		{
			return false;
		}
		FString SampleId;
		Sample->TryGetStringField(TEXT("sampleId"), SampleId);

		const TSharedPtr<FJsonObject>* DiscoveryKey = nullptr;
		if (!Sample->TryGetObjectField(TEXT("discoveryKey"), DiscoveryKey) || DiscoveryKey == nullptr)
		{
			AddError(FString::Printf(TEXT("%s: discoveryKey missing"), *SampleId));
			return false;
		}
		FString Error;
		const TSharedPtr<FNativeCanonicalValue> Projection = ProjectionFromDiscoveryKey(*DiscoveryKey, Error);
		if (!Projection.IsValid())
		{
			AddError(FString::Printf(TEXT("%s: projection failed (%s)"), *SampleId, *Error));
			return false;
		}
		FString CanonicalText;
		if (!Serialize(Projection.ToSharedRef(), CanonicalText, Error))
		{
			AddError(FString::Printf(TEXT("%s: canonicalization failed (%s)"), *SampleId, *Error));
			return false;
		}
		FString ExpectedCanonical;
		Sample->TryGetStringField(TEXT("expectedCanonicalDiscoveryKey"), ExpectedCanonical);
		if (CanonicalText != ExpectedCanonical)
		{
			AddError(FString::Printf(TEXT("%s: canonical JSON differs from Python.\nC++:   %s\nPython:%s"), *SampleId, *CanonicalText, *ExpectedCanonical));
			return false;
		}

		FString ActionId;
		if (!ComputeActionId(*DiscoveryKey, ActionId, Error))
		{
			AddError(FString::Printf(TEXT("%s: actionId failed (%s)"), *SampleId, *Error));
			return false;
		}
		FString ExpectedActionId;
		Sample->TryGetStringField(TEXT("expectedActionId"), ExpectedActionId);
		if (ActionId != ExpectedActionId)
		{
			AddError(FString::Printf(TEXT("%s: actionId differs (C++ %s vs Python %s)"), *SampleId, *ActionId, *ExpectedActionId));
			return false;
		}
		++Compared;

		const TSharedPtr<FJsonObject>* Descriptor = nullptr;
		if (Sample->TryGetObjectField(TEXT("descriptor"), Descriptor) && Descriptor != nullptr)
		{
			FString Digest;
			if (!ComputeDescriptorDigest(*Descriptor, Digest, Error))
			{
				AddError(FString::Printf(TEXT("%s: descriptor digest failed (%s)"), *SampleId, *Error));
				return false;
			}
			FString ExpectedDigest;
			Sample->TryGetStringField(TEXT("expectedDescriptorDigest"), ExpectedDigest);
			if (Digest != ExpectedDigest)
			{
				AddError(FString::Printf(TEXT("%s: descriptorDigest differs (C++ %s vs Python %s)"), *SampleId, *Digest, *ExpectedDigest));
				return false;
			}
			++DescriptorCompared;
		}
	}
	TestEqual(TEXT("every golden sample was compared"), Compared, Samples->Num());
	TestEqual(TEXT("every golden descriptor was compared"), DescriptorCompared, Samples->Num());

	// Compatibility fact: localized text changes descriptorDigest but not actionId.
	const FNativeCatalogResult Catalog = FNativeNodeRegistry::Get().BuildCatalog(CatalogOptions());
	if (!TestTrue(TEXT("catalog builds for the negative checks"), Catalog.bOk && Catalog.Descriptors.Num() > 0))
	{
		return false;
	}
	const TSharedPtr<FJsonObject> Original = Catalog.Descriptors[0].Descriptor;
	const FString OriginalActionId = Catalog.Descriptors[0].ActionId;
	const FString OriginalDigest = Catalog.Descriptors[0].DescriptorDigest;

	const TSharedRef<FJsonObject> Mutated = MakeShared<FJsonObject>();
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Original->Values)
	{
		Mutated->SetField(Pair.Key, Pair.Value);
	}
	FString Title;
	Mutated->TryGetStringField(TEXT("title"), Title);
	Mutated->SetStringField(TEXT("title"), Title + TEXT(" (renamed)"));
	FString MutatedDigest;
	FString Error;
	if (!ComputeDescriptorDigest(Mutated, MutatedDigest, Error))
	{
		return false;
	}
	TestEqual(TEXT("title change keeps actionId"), OriginalActionId, Catalog.Descriptors[0].ActionId);
	TestTrue(TEXT("title change changes descriptorDigest"), MutatedDigest != OriginalDigest);

	// Localized field inside discoveryKey must be rejected by the shape validator.
	const TSharedPtr<FJsonObject> Leaked = MakeShared<FJsonObject>();
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Original->Values)
	{
		Leaked->SetField(Pair.Key, Pair.Value);
	}
	const TSharedPtr<FJsonObject>* OriginalKey = nullptr;
	Original->TryGetObjectField(TEXT("discoveryKey"), OriginalKey);
	const TSharedRef<FJsonObject> LeakedKey = MakeShared<FJsonObject>();
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*OriginalKey)->Values)
	{
		LeakedKey->SetField(Pair.Key, Pair.Value);
	}
	LeakedKey->SetStringField(TEXT("title"), TEXT("Print String"));
	Leaked->SetObjectField(TEXT("discoveryKey"), LeakedKey);
	TestTrue(TEXT("localized discoveryKey field is detected by the ban predicate"), DescriptorHasLocalizedDiscoveryKey(Leaked));

	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeRegistryReadOnlyTest,
	"UEAgentKit.Native.R1ReadOnly",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeRegistryReadOnlyTest::RunTest(const FString& Parameters)
{
	if (!EnsureRegistry(*this))
	{
		return false;
	}
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();

	// Fixture setup is allowed to write; measurement starts only after this.
	UBlueprint* FixtureBlueprint = nullptr;
	UEdGraph* FixtureGraph = nullptr;
	UK2Node_CallFunction* FixtureNode = nullptr;
	UEdGraphPin* FixtureExecPin = nullptr;
	UEdGraphPin* FixtureDataPin = nullptr;
	if (!EnsureContextFixture(*this, FixtureBlueprint, FixtureGraph, FixtureNode, FixtureExecPin, FixtureDataPin))
	{
		return false;
	}
	if (!TestFalse(TEXT("fixture package is clean before measurement"), FixtureBlueprint->GetOutermost()->IsDirty()))
	{
		return false;
	}
	const FString FixtureFilenameBefore = FixtureFilename(FixtureBlueprint);
	const FString FixtureRevisionBefore = FileRevision(FixtureFilenameBefore);
	const FString FixtureGraphFingerprintBefore = ComputeFixtureGraphFingerprint(FixtureGraph);
	TestTrue(TEXT("fixture disk revision exists"), !FixtureRevisionBefore.IsEmpty());
	TestTrue(TEXT("fixture graph fingerprint exists"), !FixtureGraphFingerprintBefore.IsEmpty());

	const int32 DirtyBefore = CountDirtyGamePackages();
	const int32 PackagesBefore = CountGamePackages();
	const int32 AssetsBefore = CountRegisteredGameAssets();
	const int32 UndoQueueBefore = (GEditor != nullptr && GEditor->Trans != nullptr) ? GEditor->Trans->GetQueueLength() : -1;
	const int32 UndoCountBefore = (GEditor != nullptr && GEditor->Trans != nullptr) ? GEditor->Trans->GetUndoCount() : -1;

	const FNativeCatalogResult Catalog = Registry.BuildCatalog(CatalogOptions());
	if (!TestTrue(TEXT("read-only catalog build succeeds"), Catalog.bOk))
	{
		return false;
	}

	FNativeContextResult PaletteContext = Registry.FilterContext([&]() {
		FNativeContextRequest Request;
		Request.AssetPath = FixtureObjectPath;
		Request.GraphGuid = GuidString(FixtureGraph->GraphGuid);
		Request.MaxItems = 32;
		return Request;
	}());
	TestTrue(TEXT("read-only palette context executes"), PaletteContext.bOk);

	FNativeContextResult NodeContext = Registry.FilterContext([&]() {
		FNativeContextRequest Request;
		Request.AssetPath = FixtureObjectPath;
		Request.GraphGuid = GuidString(FixtureGraph->GraphGuid);
		Request.NodeGuid = GuidString(FixtureNode->NodeGuid);
		Request.MaxItems = 32;
		return Request;
	}());
	TestTrue(TEXT("read-only node context executes"), NodeContext.bOk);

	FNativeContextResult PinContext = Registry.FilterContext([&]() {
		FNativeContextRequest Request;
		Request.AssetPath = FixtureObjectPath;
		Request.GraphGuid = GuidString(FixtureGraph->GraphGuid);
		Request.NodeGuid = GuidString(FixtureNode->NodeGuid);
		Request.PinName = FixtureExecPin->PinName.ToString();
		Request.MaxItems = 32;
		return Request;
	}());
	TestTrue(TEXT("read-only pin context executes"), PinContext.bOk);

	TSharedPtr<FJsonObject> Result;
	FString ErrorCode;
	FString ErrorMessage;
	const TSharedPtr<FJsonObject> Arguments = MakeShared<FJsonObject>();
	Arguments->SetNumberField(TEXT("maxItems"), 8);
	TestTrue(
		TEXT("shadow catalog tool executes"),
		Registry.TryInvokeShadowTool(TEXT("graph.nodeRegistry.catalog"), TEXT("1.0.0"), Arguments, Result, ErrorCode, ErrorMessage));
	TestTrue(
		TEXT("shadow fingerprint tool executes"),
		Registry.TryInvokeShadowTool(TEXT("graph.nodeRegistry.fingerprint"), TEXT("1.0.0"), TSharedPtr<FJsonObject>(), Result, ErrorCode, ErrorMessage));

	const int32 DirtyAfter = CountDirtyGamePackages();
	const int32 PackagesAfter = CountGamePackages();
	const int32 AssetsAfter = CountRegisteredGameAssets();
	const int32 UndoQueueAfter = (GEditor != nullptr && GEditor->Trans != nullptr) ? GEditor->Trans->GetQueueLength() : -1;
	const int32 UndoCountAfter = (GEditor != nullptr && GEditor->Trans != nullptr) ? GEditor->Trans->GetUndoCount() : -1;
	const FString FixtureRevisionAfter = FileRevision(FixtureFilenameBefore);
	const FString FixtureGraphFingerprintAfter = ComputeFixtureGraphFingerprint(FixtureGraph);

	TestEqual(TEXT("dirty /Game package count is unchanged"), DirtyAfter, DirtyBefore);
	TestEqual(TEXT("/Game package count is unchanged"), PackagesAfter, PackagesBefore);
	TestEqual(TEXT("AssetRegistry /Game asset count is unchanged"), AssetsAfter, AssetsBefore);
	TestEqual(TEXT("undo queue length is unchanged"), UndoQueueAfter, UndoQueueBefore);
	TestEqual(TEXT("undo count is unchanged"), UndoCountAfter, UndoCountBefore);
	TestEqual(TEXT("no transaction was opened"), UndoQueueAfter, 0);
	TestEqual(TEXT("fixture disk revision is unchanged"), FixtureRevisionAfter, FixtureRevisionBefore);
	TestEqual(TEXT("fixture graph fingerprint is unchanged"), FixtureGraphFingerprintAfter, FixtureGraphFingerprintBefore);
	TestFalse(TEXT("fixture package is still clean"), FixtureBlueprint->GetOutermost()->IsDirty());
	TestTrue(TEXT("catalog reported read-only"), Catalog.MakeSummaryJson()->GetBoolField(TEXT("readOnly")));
	TestFalse(TEXT("catalog reported no save"), Catalog.MakeSummaryJson()->GetBoolField(TEXT("saved")));
	AddInfo(FString::Printf(
		TEXT("readOnlyEvidence dirtyBefore=%d dirtyAfter=%d gamePackagesBefore=%d gamePackagesAfter=%d assetsBefore=%d assetsAfter=%d undoQueueBefore=%d undoQueueAfter=%d undoCountBefore=%d undoCountAfter=%d fixtureDirty=%d revisionBefore=%s revisionAfter=%s graphFingerprintBefore=%s graphFingerprintAfter=%s"),
		DirtyBefore,
		DirtyAfter,
		PackagesBefore,
		PackagesAfter,
		AssetsBefore,
		AssetsAfter,
		UndoQueueBefore,
		UndoQueueAfter,
		UndoCountBefore,
		UndoCountAfter,
		FixtureBlueprint->GetOutermost()->IsDirty() ? 1 : 0,
		*FixtureRevisionBefore,
		*FixtureRevisionAfter,
		*FixtureGraphFingerprintBefore,
		*FixtureGraphFingerprintAfter));

	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeRegistryShadowToolsTest,
	"UEAgentKit.Native.R1ShadowTools",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeRegistryShadowToolsTest::RunTest(const FString& Parameters)
{
	if (!EnsureRegistry(*this))
	{
		return false;
	}
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();
	TSharedPtr<FJsonObject> Result;
	FString ErrorCode;
	FString ErrorMessage;

	// Fingerprint tool: identity only, no descriptors.
	TestTrue(
		TEXT("fingerprint tool succeeds"),
		Registry.TryInvokeShadowTool(TEXT("graph.nodeRegistry.fingerprint"), TEXT("1.0.0"), TSharedPtr<FJsonObject>(), Result, ErrorCode, ErrorMessage));
	if (Result.IsValid())
	{
		TestTrue(TEXT("fingerprint result is read-only"), Result->GetBoolField(TEXT("readOnly")));
		const TSharedPtr<FJsonObject>* Data = nullptr;
		Result->TryGetObjectField(TEXT("data"), Data);
		TestTrue(TEXT("fingerprint result has data"), Data != nullptr && Data->IsValid());
		if (Data != nullptr && Data->IsValid())
		{
			FString Fingerprint;
			(*Data)->TryGetStringField(TEXT("actionDatabaseFingerprint"), Fingerprint);
			TestTrue(TEXT("fingerprint data carries the database fingerprint"), MatchesRegex(TEXT("^sha256:[0-9a-f]{16}$"), Fingerprint));
			TestFalse(TEXT("fingerprint result carries no descriptors"), (*Data)->HasField(TEXT("descriptors")));
		}
	}

	// Catalog tool with a small page.
	const TSharedPtr<FJsonObject> Arguments = MakeShared<FJsonObject>();
	Arguments->SetNumberField(TEXT("maxItems"), 8);
	Arguments->SetBoolField(TEXT("includeDescriptors"), true);
	TestTrue(
		TEXT("catalog tool succeeds"),
		Registry.TryInvokeShadowTool(TEXT("graph.nodeRegistry.catalog"), TEXT("1.0.0"), Arguments, Result, ErrorCode, ErrorMessage));
	if (Result.IsValid())
	{
		const TSharedPtr<FJsonObject>* Data = nullptr;
		Result->TryGetObjectField(TEXT("data"), Data);
		const TArray<TSharedPtr<FJsonValue>>* Descriptors = nullptr;
		if (Data != nullptr && Data->IsValid() && (*Data)->TryGetArrayField(TEXT("descriptors"), Descriptors) && Descriptors != nullptr)
		{
			TestTrue(TEXT("catalog page is bounded by maxItems"), Descriptors->Num() <= 8);
			TestTrue(TEXT("catalog page is non-empty"), Descriptors->Num() > 0);
		}
		else
		{
			AddError(TEXT("catalog tool returned no descriptors array."));
		}
	}

	// Negative branches.
	TestFalse(
		TEXT("unknown tool is rejected"),
		Registry.TryInvokeShadowTool(TEXT("graph.unknown.tool"), TEXT("1.0.0"), TSharedPtr<FJsonObject>(), Result, ErrorCode, ErrorMessage));
	TestEqual(TEXT("unknown tool code"), ErrorCode, FString(TEXT("native-tool-unknown")));

	TestFalse(
		TEXT("unsupported version is rejected"),
		Registry.TryInvokeShadowTool(TEXT("graph.nodeRegistry.catalog"), TEXT("2.0.0"), TSharedPtr<FJsonObject>(), Result, ErrorCode, ErrorMessage));
	TestEqual(TEXT("version code"), ErrorCode, FString(TEXT("native-tool-version-unsupported")));

	const TSharedPtr<FJsonObject> BadArguments = MakeShared<FJsonObject>();
	BadArguments->SetStringField(TEXT("unexpected"), TEXT("value"));
	TestFalse(
		TEXT("unknown argument is rejected"),
		Registry.TryInvokeShadowTool(TEXT("graph.nodeRegistry.catalog"), TEXT("1.0.0"), BadArguments, Result, ErrorCode, ErrorMessage));
	TestEqual(TEXT("argument code"), ErrorCode, FString(TEXT("native-schema-invalid-arguments")));

	const TSharedPtr<FJsonObject> OutOfRange = MakeShared<FJsonObject>();
	OutOfRange->SetNumberField(TEXT("maxItems"), 513);
	TestFalse(
		TEXT("out-of-range page size is rejected"),
		Registry.TryInvokeShadowTool(TEXT("graph.nodeRegistry.catalog"), TEXT("1.0.0"), OutOfRange, Result, ErrorCode, ErrorMessage));
	TestEqual(TEXT("range code"), ErrorCode, FString(TEXT("native-schema-invalid-arguments")));

	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeContextFilterTest,
	"UEAgentKit.Native.R1ContextFilter",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeContextFilterTest::RunTest(const FString& Parameters)
{
	if (!EnsureRegistry(*this))
	{
		return false;
	}
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	UK2Node_CallFunction* Node = nullptr;
	UEdGraphPin* ExecPin = nullptr;
	UEdGraphPin* DataPin = nullptr;
	if (!EnsureContextFixture(*this, Blueprint, Graph, Node, ExecPin, DataPin))
	{
		return false;
	}

	auto RunContext = [&](const bool bNode, UEdGraphPin* InPin, const int32 MaxItems) {
		FNativeContextRequest Request;
		Request.AssetPath = FixtureObjectPath;
		Request.GraphGuid = GuidString(Graph->GraphGuid);
		if (bNode)
		{
			Request.NodeGuid = GuidString(Node->NodeGuid);
		}
		if (InPin != nullptr)
		{
			Request.PinName = InPin->PinName.ToString();
		}
		Request.MaxItems = MaxItems;
		return Registry.FilterContext(Request);
	};

	const FNativeContextResult Palette = RunContext(false, nullptr, 128);
	const FNativeContextResult NodeContext = RunContext(true, nullptr, 128);
	const FNativeContextResult ExecContext = RunContext(true, ExecPin, 128);
	const FNativeContextResult DataContext = RunContext(true, DataPin, 128);
	if (!TestTrue(TEXT("palette context succeeds"), Palette.bOk)
		|| !TestTrue(TEXT("node context succeeds"), NodeContext.bOk)
		|| !TestTrue(TEXT("exec pin context succeeds"), ExecContext.bOk)
		|| !TestTrue(TEXT("data pin context succeeds"), DataContext.bOk))
	{
		AddError(FString::Printf(
			TEXT("Context errors: palette=%s node=%s exec=%s data=%s"),
			*Palette.ErrorCode,
			*NodeContext.ErrorCode,
			*ExecContext.ErrorCode,
			*DataContext.ErrorCode));
		return false;
	}

	TestEqual(TEXT("palette context kind"), Palette.ContextKind, FString(TEXT("palette")));
	TestEqual(TEXT("node context kind"), NodeContext.ContextKind, FString(TEXT("node")));
	TestEqual(TEXT("pin context kind"), ExecContext.ContextKind, FString(TEXT("pin")));
	TestEqual(TEXT("fixture graph kind is the event graph"), ExecContext.GraphKind, FString(TEXT("event")));
	TestEqual(TEXT("fixture graph schema is K2"), ExecContext.GraphSchemaPath, FString(TEXT("/Script/BlueprintGraph.EdGraphSchema_K2")));
	TestTrue(TEXT("palette has actions"), Palette.Actions.Num() > 0);
	TestTrue(TEXT("node context has actions"), NodeContext.Actions.Num() > 0);
	TestTrue(TEXT("exec pin context has actions"), ExecContext.Actions.Num() > 0);
	TestTrue(TEXT("data pin context has actions"), DataContext.Actions.Num() > 0);
	TestTrue(TEXT("context fingerprint has contract shape"), MatchesRegex(TEXT("^sha256:[0-9a-f]{16}$"), ExecContext.ActionSetFingerprint));

	// Determinism: identical request twice yields identical sorted ids + fingerprint.
	const FNativeContextResult ExecAgain = RunContext(true, ExecPin, 128);
	TestEqual(TEXT("context fingerprint is stable"), ExecAgain.ActionSetFingerprint, ExecContext.ActionSetFingerprint);
	TArray<FString> ExecIds;
	TArray<FString> ExecAgainIds;
	for (const FNativeContextActionRecord& Record : ExecContext.Actions) { ExecIds.Add(Record.ActionId); }
	for (const FNativeContextActionRecord& Record : ExecAgain.Actions) { ExecAgainIds.Add(Record.ActionId); }
	TestTrue(TEXT("context action id list is stable"), ExecIds == ExecAgainIds);

	// Catalog vs context: every context action id must exist in the catalog with
	// the same descriptor digest (same action identity in both channels).
	const FNativeCatalogResult Catalog = Registry.BuildCatalog(CatalogOptions());
	TestTrue(TEXT("catalog builds for identity cross-check"), Catalog.bOk);
	TMap<FString, FString> CatalogDigests;
	for (const FNativeNodeDescriptorRecord& Record : Catalog.Descriptors)
	{
		CatalogDigests.Add(Record.ActionId, Record.DescriptorDigest);
	}
	for (const FNativeContextActionRecord& Record : ExecContext.Actions)
	{
		const FString* CatalogDigest = CatalogDigests.Find(Record.ActionId);
		TestTrue(TEXT("context action id is in the catalog"), CatalogDigest != nullptr);
		if (CatalogDigest != nullptr)
		{
			TestEqual(TEXT("context descriptor digest matches catalog"), Record.DescriptorDigest, *CatalogDigest);
		}
	}

	// The descriptor's graphKinds is only a template hint: at least one action
	// described as "function" is valid in the fixture's event graph.
	bool bFoundFunctionHint = false;
	for (const FNativeContextActionRecord& Record : ExecContext.Actions)
	{
		if (Record.GraphKindsHint.Contains(TEXT("function")))
		{
			bFoundFunctionHint = true;
			break;
		}
	}
	TestTrue(TEXT("a function-hint action is context-valid in the event graph"), bFoundFunctionHint);

	// Context sensitivity: palette/node/pin must not be the same set.
	TestTrue(TEXT("node context differs from palette"), NodeContext.ActionSetFingerprint != Palette.ActionSetFingerprint);
	TestTrue(TEXT("exec pin context differs from palette"), ExecContext.ActionSetFingerprint != Palette.ActionSetFingerprint);
	TestTrue(TEXT("exec pin context differs from data pin context"), ExecContext.ActionSetFingerprint != DataContext.ActionSetFingerprint);

	// Negative: an action valid for the palette is not valid for the string pin.
	TSet<FString> DataIds;
	for (const FNativeContextActionRecord& Record : DataContext.Actions) { DataIds.Add(Record.ActionId); }
	FString PaletteOnlyAction;
	for (const FNativeContextActionRecord& Record : Palette.Actions)
	{
		if (!DataIds.Contains(Record.ActionId))
		{
			PaletteOnlyAction = Record.ActionId;
			break;
		}
	}
	TestTrue(TEXT("a palette action is incompatible with the data pin"), !PaletteOnlyAction.IsEmpty());

	// Fail-closed negatives.
	FNativeContextRequest UnknownGraph;
	UnknownGraph.AssetPath = FixtureObjectPath;
	UnknownGraph.GraphGuid = TEXT("00000000000000000000000000000000");
	const FNativeContextResult UnknownGraphResult = Registry.FilterContext(UnknownGraph);
	TestFalse(TEXT("unknown graph is rejected"), UnknownGraphResult.bOk);
	TestEqual(TEXT("unknown graph code"), UnknownGraphResult.ErrorCode, FString(TEXT("graphpatch-graph-not-found")));

	FNativeContextRequest UnknownPin;
	UnknownPin.AssetPath = FixtureObjectPath;
	UnknownPin.GraphGuid = GuidString(Graph->GraphGuid);
	UnknownPin.NodeGuid = GuidString(Node->NodeGuid);
	UnknownPin.PinName = TEXT("R1NotARealPin");
	const FNativeContextResult UnknownPinResult = Registry.FilterContext(UnknownPin);
	TestFalse(TEXT("unknown pin is rejected"), UnknownPinResult.bOk);
	TestEqual(TEXT("unknown pin code"), UnknownPinResult.ErrorCode, FString(TEXT("graph-pin-not-found")));

	FNativeContextRequest UnknownAsset;
	UnknownAsset.AssetPath = TEXT("/Game/R1Native/BP_R1DoesNotExist.BP_R1DoesNotExist");
	UnknownAsset.GraphGuid = GuidString(Graph->GraphGuid);
	const FNativeContextResult UnknownAssetResult = Registry.FilterContext(UnknownAsset);
	TestFalse(TEXT("unloaded asset is rejected"), UnknownAssetResult.bOk);
	TestEqual(TEXT("unloaded asset code"), UnknownAssetResult.ErrorCode, FString(TEXT("graphpatch-asset-not-loaded")));

	// Non-K2 graph fails closed through the object seam (transient, never saved).
	UBlueprint* TransientBlueprint = NewObject<UBlueprint>(GetTransientPackage());
	UEdGraph* TransientGraph = NewObject<UEdGraph>(TransientBlueprint);
	TransientGraph->Schema = UEdGraphSchema::StaticClass();
	TransientBlueprint->FunctionGraphs.Add(TransientGraph);
	FNativeContextResult NonK2Result;
	TestFalse(
		TEXT("non-K2 graph is rejected"),
		Registry.FilterContextForAutomation(TransientBlueprint, TransientGraph, nullptr, nullptr, 8, NonK2Result));
	TestEqual(TEXT("non-K2 code"), NonK2Result.ErrorCode, FString(TEXT("graphpatch-context-mismatch")));

	if (!WriteContextExport(*this, Palette, NodeContext, ExecContext, DataContext, NonK2Result.ErrorCode))
	{
		return false;
	}

	AddInfo(FString::Printf(
		TEXT("contextEvidence graphKind=%s graphSchema=%s palette=%d node=%d execPin=%d dataPin=%d described=%d notDescribed=%d evaluated=%d rejected=%d execFingerprint=%s"),
		*ExecContext.GraphKind,
		*ExecContext.GraphSchemaPath,
		Palette.Actions.Num(),
		NodeContext.Actions.Num(),
		ExecContext.Actions.Num(),
		DataContext.Actions.Num(),
		ExecContext.DescribedActions,
		ExecContext.NotDescribedActions,
		ExecContext.EvaluatedActions,
		ExecContext.RejectedActions,
		*ExecContext.ActionSetFingerprint));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeUntrustedSpawnerTest,
	"UEAgentKit.Native.R1UntrustedSpawner",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeUntrustedSpawnerTest::RunTest(const FString& Parameters)
{
	if (!EnsureRegistry(*this))
	{
		return false;
	}
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();

	UBlueprintNodeSpawner* Spawner = NewObject<UBlueprintNodeSpawner>(GetTransientPackage());
	Spawner->NodeClass = UEdGraphNode::StaticClass();

	FString Code;
	TestFalse(
		TEXT("untrusted spawner is rejected"),
		Registry.DescribeSpawnerForAutomation(Spawner, true, Code));
	TestEqual(TEXT("untrusted reason"), Code, FString(TEXT("untrusted-spawner-excluded")));
	TestNull(TEXT("untrusted spawner did not create a template"), Spawner->GetCachedTemplateNode());

	TestFalse(
		TEXT("unknown plain spawner is rejected"),
		Registry.DescribeSpawnerForAutomation(Spawner, false, Code));
	TestEqual(TEXT("unknown spawner reason"), Code, FString(TEXT("unknown-spawner")));
	TestNull(TEXT("unknown spawner did not create a template"), Spawner->GetCachedTemplateNode());

	// T10 negative: a real, valid spawner can be marked untrusted after its
	// template is already cached.  The trust check must still run before any
	// template API, so the cache entry is neither mutated nor replaced.
	UFunction* AddIntInt = UKismetMathLibrary::StaticClass()->FindFunctionByName(TEXT("Add_IntInt"));
	if (!TestNotNull(TEXT("T10 function exists"), AddIntInt))
	{
		return false;
	}
	UBlueprintFunctionNodeSpawner* CachedSpawner =
		UBlueprintFunctionNodeSpawner::Create(AddIntInt, GetTransientPackage());
	if (!TestNotNull(TEXT("T10 cached spawner exists"), CachedSpawner))
	{
		return false;
	}
	UEdGraphNode* CachedBefore = CachedSpawner->GetTemplateNode();
	if (!TestNotNull(TEXT("T10 cached template exists"), CachedBefore))
	{
		return false;
	}
	const int32 DirtyBeforeCached = CountDirtyGamePackages();
	FString CachedCode;
	TestFalse(
		TEXT("cached untrusted spawner is still rejected"),
		Registry.DescribeSpawnerForAutomation(CachedSpawner, true, CachedCode));
	TestEqual(TEXT("cached untrusted reason"), CachedCode, FString(TEXT("untrusted-spawner-excluded")));
	const bool bCachedUnchanged = CachedSpawner->GetCachedTemplateNode() == CachedBefore;
	TestTrue(
		TEXT("cached untrusted template pointer is unchanged"),
		bCachedUnchanged);
	TestEqual(
		TEXT("cached untrusted spawner does not dirty packages"),
		CountDirtyGamePackages(),
		DirtyBeforeCached);

	// T10 budget negative: a valid official spawner with zero remaining
	// template budget must fail closed and must not fall through to Invoke().
	UBlueprintFunctionNodeSpawner* BudgetSpawner =
		UBlueprintFunctionNodeSpawner::Create(AddIntInt, GetTransientPackage());
	if (!TestNotNull(TEXT("T10 budget spawner exists"), BudgetSpawner))
	{
		return false;
	}
	// The function-spawner factory may have primed a cache entry; this negative
	// needs a genuinely uncached spawner so the zero budget is exercised first.
	BudgetSpawner->ClearCachedTemplateNode();
	TestNull(TEXT("T10 budget spawner starts uncached"), BudgetSpawner->GetCachedTemplateNode());
	const int32 DirtyBeforeBudget = CountDirtyGamePackages();
	FString BudgetCode;
	TestFalse(
		TEXT("budget-exhausted official spawner is rejected"),
		Registry.DescribeSpawnerForAutomation(BudgetSpawner, false, BudgetCode));
	TestEqual(TEXT("budget-exhausted reason"), BudgetCode, FString(TEXT("template-budget-exceeded")));
	TestNull(TEXT("budget-exhausted spawner did not create a template"), BudgetSpawner->GetCachedTemplateNode());
	TestEqual(
		TEXT("budget-exhausted spawner does not dirty packages"),
		CountDirtyGamePackages(),
		DirtyBeforeBudget);

	AddInfo(FString::Printf(
		TEXT("untrustedSpawnerEvidence code=%s templateCached=%s cachedUntrustedReason=%s cachedUnchanged=%s budgetReason=%s budgetTemplateCached=%s dirtyBefore=%d dirtyAfter=%d"),
		*Code,
		Spawner->GetCachedTemplateNode() != nullptr ? TEXT("yes") : TEXT("no"),
		*CachedCode,
		bCachedUnchanged ? TEXT("yes") : TEXT("no"),
		*BudgetCode,
		BudgetSpawner->GetCachedTemplateNode() != nullptr ? TEXT("yes") : TEXT("no"),
		DirtyBeforeCached,
		CountDirtyGamePackages()));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeCursorVectorsTest,
	"UEAgentKit.Native.R1CursorVectors",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeCursorVectorsTest::RunTest(const FString& Parameters)
{
	const FString Path = CursorVectorsPath();
	FString Text;
	if (!TestTrue(TEXT("cursor vector fixture exists"), !Path.IsEmpty() && FFileHelper::LoadFileToString(Text, *Path)))
	{
		return false;
	}
	TSharedPtr<FJsonObject> Fixture;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
	if (!TestTrue(TEXT("cursor vector fixture parses"), FJsonSerializer::Deserialize(Reader, Fixture) && Fixture.IsValid()))
	{
		return false;
	}

	FString KeyHex;
	Fixture->TryGetStringField(TEXT("keyHex"), KeyHex);
	TArray<uint8> Key;
	if (!TestTrue(TEXT("cursor vector key parses"), HexToBytes(KeyHex, Key) && Key.Num() == 32))
	{
		return false;
	}
	const int64 Now = static_cast<int64>(Fixture->GetNumberField(TEXT("nowUnix")));
	const TArray<TSharedPtr<FJsonValue>>* Vectors = nullptr;
	if (!TestTrue(TEXT("cursor vectors exist"), Fixture->TryGetArrayField(TEXT("vectors"), Vectors) && Vectors != nullptr))
	{
		return false;
	}

	for (const TSharedPtr<FJsonValue>& VectorValue : *Vectors)
	{
		const TSharedPtr<FJsonObject> Vector = VectorValue.IsValid() ? VectorValue->AsObject() : nullptr;
		if (!TestTrue(TEXT("cursor vector is an object"), Vector.IsValid()))
		{
			return false;
		}
		const TSharedPtr<FJsonObject> PayloadObject = Vector->GetObjectField(TEXT("payload"));
		FNativeCursorPayload Payload;
		Payload.Kind = PayloadObject->GetStringField(TEXT("k"));
		Payload.QueryHash = PayloadObject->GetStringField(TEXT("q"));
		Payload.SetFingerprint = PayloadObject->GetStringField(TEXT("s"));
		Payload.Epoch = PayloadObject->GetStringField(TEXT("e"));
		Payload.Offset = static_cast<int32>(PayloadObject->GetNumberField(TEXT("o")));
		Payload.Total = static_cast<int32>(PayloadObject->GetNumberField(TEXT("n")));
		Payload.PageSize = static_cast<int32>(PayloadObject->GetNumberField(TEXT("p")));
		Payload.IssuedAt = static_cast<int64>(PayloadObject->GetNumberField(TEXT("iat")));
		Payload.ExpiresAt = static_cast<int64>(PayloadObject->GetNumberField(TEXT("exp")));

		FString Token;
		FString ErrorCode;
		if (!TestTrue(
				TEXT("cursor vector encodes"),
				UEAgentKitNativeCursor::Encode(Payload, Key, Token, ErrorCode)))
		{
			return false;
		}
		TestEqual(TEXT("cursor vector token matches Python"), Token, Vector->GetStringField(TEXT("token")));

		FNativeCursorPayload Decoded;
		FString DecodeError;
		FString DecodeMessage;
		if (!TestTrue(
				TEXT("cursor vector decodes"),
				UEAgentKitNativeCursor::Decode(Token, Key, Now, Decoded, DecodeError, DecodeMessage)))
		{
			return false;
		}
		TestEqual(TEXT("decoded kind"), Decoded.Kind, Payload.Kind);
		TestEqual(TEXT("decoded query hash"), Decoded.QueryHash, Payload.QueryHash);
		TestEqual(TEXT("decoded set fingerprint"), Decoded.SetFingerprint, Payload.SetFingerprint);
		TestEqual(TEXT("decoded epoch"), Decoded.Epoch, Payload.Epoch);
		TestEqual(TEXT("decoded offset"), Decoded.Offset, Payload.Offset);
		TestEqual(TEXT("decoded total"), Decoded.Total, Payload.Total);
		TestEqual(TEXT("decoded page size"), Decoded.PageSize, Payload.PageSize);
		TestEqual(TEXT("decoded issued at"), Decoded.IssuedAt, Payload.IssuedAt);
		TestEqual(TEXT("decoded expires at"), Decoded.ExpiresAt, Payload.ExpiresAt);

		FString Tampered = Token;
		Tampered[Tampered.Len() - 1] = Tampered[Tampered.Len() - 1] == TEXT('A') ? TEXT('B') : TEXT('A');
		FNativeCursorPayload TamperedPayload;
		TestFalse(
			TEXT("tampered cursor is rejected"),
			UEAgentKitNativeCursor::Decode(Tampered, Key, Now, TamperedPayload, DecodeError, DecodeMessage));
	}

	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativePaginationTest,
	"UEAgentKit.Native.R1Pagination",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativePaginationTest::RunTest(const FString& Parameters)
{
	if (!EnsureRegistry(*this))
	{
		return false;
	}
	FNativeNodeRegistry& Registry = FNativeNodeRegistry::Get();

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	UK2Node_CallFunction* Node = nullptr;
	UEdGraphPin* ExecPin = nullptr;
	UEdGraphPin* DataPin = nullptr;
	if (!EnsureContextFixture(*this, Blueprint, Graph, Node, ExecPin, DataPin))
	{
		return false;
	}

	auto CatalogPage = [&](const int32 PageSize, const FString& Cursor, FString& OutErrorCode, const bool bReportFailure = true) {
		const TSharedPtr<FJsonObject> Arguments = MakeShared<FJsonObject>();
		Arguments->SetNumberField(TEXT("pageSize"), PageSize);
		if (!Cursor.IsEmpty())
		{
			Arguments->SetStringField(TEXT("cursor"), Cursor);
		}
		return InvokeToolData(*this, Registry, TEXT("graph.nodeRegistry.catalog"), Arguments, OutErrorCode, bReportFailure);
	};

	FString ErrorCode;
	const TSharedPtr<FJsonObject> FirstData = CatalogPage(64, FString(), ErrorCode);
	if (!TestNotNull(TEXT("catalog first page data"), FirstData.Get()))
	{
		return false;
	}
	const TSharedPtr<FJsonObject> FirstPagination = FirstData->GetObjectField(TEXT("pagination"));
	if (!TestTrue(TEXT("catalog first page has pagination"), FirstPagination.IsValid()))
	{
		return false;
	}
	TestEqual(TEXT("catalog page size"), static_cast<int32>(FirstPagination->GetNumberField(TEXT("pageSize"))), 64);
	TestEqual(TEXT("catalog page offset"), static_cast<int32>(FirstPagination->GetNumberField(TEXT("offset"))), 0);
	TestEqual(TEXT("catalog page returned"), static_cast<int32>(FirstPagination->GetNumberField(TEXT("returned"))), 64);
	TestEqual(TEXT("catalog page total"), static_cast<int32>(FirstPagination->GetNumberField(TEXT("total"))), 512);
	TestTrue(TEXT("catalog pagination has more"), FirstPagination->GetBoolField(TEXT("hasMore")));
	TestFalse(TEXT("catalog pagination is not exhausted on page 1"), FirstPagination->GetBoolField(TEXT("exhausted")));
	TestTrue(TEXT("catalog truncation is distinguished"), FirstPagination->GetBoolField(TEXT("catalogTruncated")));
	const int32 CatalogAvailable = static_cast<int32>(FirstPagination->GetNumberField(TEXT("catalogAvailable")));
	TestTrue(TEXT("catalog described availability is at least the 512 returned"), CatalogAvailable >= 512);
	TestTrue(TEXT("catalog availability never implies full 38k coverage"), CatalogAvailable < 38325);

	TArray<FString> Collected;
	FString FirstNextCursor;
	int32 Pages = 0;
	FString PageCursor;
	while (Pages < 32)
	{
		const TSharedPtr<FJsonObject> PageData = CatalogPage(64, PageCursor, ErrorCode);
		if (!TestNotNull(TEXT("catalog page data"), PageData.Get()))
		{
			return false;
		}
		const TArray<FString> PageIds = DescriptorIds(PageData);
		Collected.Append(PageIds);
		const TSharedPtr<FJsonObject> PagePagination = PageData->GetObjectField(TEXT("pagination"));
		if (Pages == 0)
		{
			PagePagination->TryGetStringField(TEXT("nextCursor"), FirstNextCursor);
		}
		FString NextCursor;
		if (!PagePagination->TryGetStringField(TEXT("nextCursor"), NextCursor) || !PagePagination->GetBoolField(TEXT("hasMore")))
		{
			break;
		}
		PageCursor = NextCursor;
		++Pages;
	}

	TestEqual(TEXT("catalog paging covers exactly 512"), Collected.Num(), 512);
	TestEqual(TEXT("catalog paging has no duplicates"), UniqueIdCount(Collected), 512);
	TArray<FString> SortedCollected = Collected;
	SortedCollected.Sort([](const FString& Left, const FString& Right) {
		return UEAgentKitCanonical::CompareUtf8(Left, Right) < 0;
	});
	TestTrue(TEXT("catalog paging is sorted"), Collected == SortedCollected);

	// Replayed cursor is idempotent.
	if (!FirstNextCursor.IsEmpty())
	{
		const TSharedPtr<FJsonObject> ReplayA = CatalogPage(64, FirstNextCursor, ErrorCode);
		const TSharedPtr<FJsonObject> ReplayB = CatalogPage(64, FirstNextCursor, ErrorCode);
		TestTrue(TEXT("replayed cursor returns the same page"), DescriptorIds(ReplayA) == DescriptorIds(ReplayB));

		FString Tampered = FirstNextCursor;
		Tampered[Tampered.Len() - 1] = Tampered[Tampered.Len() - 1] == TEXT('A') ? TEXT('B') : TEXT('A');
		const TSharedPtr<FJsonObject> TamperedData = CatalogPage(64, Tampered, ErrorCode, false);
		TestNull(TEXT("tampered cursor is rejected"), TamperedData.Get());
		TestEqual(TEXT("tampered cursor code"), ErrorCode, FString(TEXT("native-schema-invalid-arguments")));
	}

	// Context paging (exec pin).
	auto ContextPage = [&](const int32 PageSize, const FString& Cursor, FString& OutErrorCode, const bool bReportFailure = true) {
		const TSharedPtr<FJsonObject> Arguments = MakeShared<FJsonObject>();
		Arguments->SetStringField(TEXT("assetPath"), FixtureObjectPath);
		Arguments->SetStringField(TEXT("graphGuid"), GuidString(Graph->GraphGuid));
		Arguments->SetStringField(TEXT("nodeGuid"), GuidString(Node->NodeGuid));
		Arguments->SetStringField(TEXT("pinName"), ExecPin->PinName.ToString());
		Arguments->SetNumberField(TEXT("pageSize"), PageSize);
		if (!Cursor.IsEmpty())
		{
			Arguments->SetStringField(TEXT("cursor"), Cursor);
		}
		return InvokeToolData(*this, Registry, TEXT("graph.nodeRegistry.filterContext"), Arguments, OutErrorCode, bReportFailure);
	};

	FString ContextError;
	const TSharedPtr<FJsonObject> ContextFirst = ContextPage(32, FString(), ContextError);
	if (!TestNotNull(TEXT("context first page data"), ContextFirst.Get()))
	{
		return false;
	}
	const TSharedPtr<FJsonObject> ContextPagination = ContextFirst->GetObjectField(TEXT("pagination"));
	const int32 ContextTotal = static_cast<int32>(ContextPagination->GetNumberField(TEXT("total")));
	TestTrue(TEXT("context pages exist"), ContextTotal > 0);
	TArray<FString> ContextCollected;
	FString ContextCursor;
	int32 ContextPages = 0;
	while (ContextPages < 64)
	{
		const TSharedPtr<FJsonObject> PageData = ContextPage(32, ContextCursor, ContextError);
		if (!TestNotNull(TEXT("context page data"), PageData.Get()))
		{
			return false;
		}
		const TArray<TSharedPtr<FJsonValue>>* Actions = nullptr;
		if (PageData->TryGetArrayField(TEXT("actions"), Actions) && Actions != nullptr)
		{
			for (const TSharedPtr<FJsonValue>& Value : *Actions)
			{
				const TSharedPtr<FJsonObject> Item = Value.IsValid() ? Value->AsObject() : nullptr;
				FString ActionId;
				if (Item.IsValid() && Item->TryGetStringField(TEXT("actionId"), ActionId))
				{
					ContextCollected.Add(ActionId);
				}
			}
		}
		const TSharedPtr<FJsonObject> PagePagination = PageData->GetObjectField(TEXT("pagination"));
		FString NextCursor;
		if (!PagePagination->TryGetStringField(TEXT("nextCursor"), NextCursor))
		{
			break;
		}
		ContextCursor = NextCursor;
		++ContextPages;
	}
	TestEqual(TEXT("context paging covers the whole match set"), ContextCollected.Num(), ContextTotal);
	TestEqual(TEXT("context paging has no duplicates"), UniqueIdCount(ContextCollected), ContextTotal);

	// Query shape mismatch: catalog cursor used for a context request.
	if (!FirstNextCursor.IsEmpty())
	{
		const TSharedPtr<FJsonObject> Mismatch = ContextPage(64, FirstNextCursor, ErrorCode, false);
		TestNull(TEXT("cursor query mismatch is rejected"), Mismatch.Get());
		TestEqual(TEXT("cursor query mismatch code"), ErrorCode, FString(TEXT("native-schema-invalid-arguments")));
	}

	// Stale and expired cursors, minted with the session key.
	if (FirstPagination.IsValid())
	{
		const FString QueryHash = FirstPagination->GetStringField(TEXT("queryHash"));
		const FString SetFingerprint = FirstPagination->GetStringField(TEXT("setFingerprint"));
		const FString Epoch = FirstPagination->GetStringField(TEXT("epoch"));
		const int64 Now = FDateTime::UtcNow().ToUnixTimestamp();
		FString Minted;
		FString MintCode;
		TestTrue(
			TEXT("stale cursor can be minted for the negative test"),
			Registry.EncodeCursorForAutomation(
				TEXT("catalog"), QueryHash, TEXT("sha256:0000000000000000"), Epoch,
				64, 512, 64, Now, Now + 900, Minted, MintCode));
		const TSharedPtr<FJsonObject> StaleData = CatalogPage(64, Minted, ErrorCode, false);
		TestNull(TEXT("stale cursor is rejected"), StaleData.Get());
		TestEqual(TEXT("stale cursor code"), ErrorCode, FString(TEXT("native-gate-revision-conflict")));

		TestTrue(
			TEXT("expired cursor can be minted for the negative test"),
			Registry.EncodeCursorForAutomation(
				TEXT("catalog"), QueryHash, SetFingerprint, Epoch,
				64, 512, 64, Now - 1000, Now - 1, Minted, MintCode));
		const TSharedPtr<FJsonObject> ExpiredData = CatalogPage(64, Minted, ErrorCode, false);
		TestNull(TEXT("expired cursor is rejected"), ExpiredData.Get());
		TestEqual(TEXT("expired cursor code"), ErrorCode, FString(TEXT("native-schema-invalid-arguments")));
	}

	// Bounds and legacy compatibility.
	const TSharedPtr<FJsonObject> TooLargeArgs = MakeShared<FJsonObject>();
	TooLargeArgs->SetNumberField(TEXT("pageSize"), 257);
	const TSharedPtr<FJsonObject> TooLarge = InvokeToolData(*this, Registry, TEXT("graph.nodeRegistry.catalog"), TooLargeArgs, ErrorCode, false);
	TestNull(TEXT("pageSize above bound is rejected"), TooLarge.Get());

	const TSharedPtr<FJsonObject> MixedArgs = MakeShared<FJsonObject>();
	MixedArgs->SetNumberField(TEXT("pageSize"), 32);
	MixedArgs->SetNumberField(TEXT("maxItems"), 32);
	const TSharedPtr<FJsonObject> Mixed = InvokeToolData(*this, Registry, TEXT("graph.nodeRegistry.catalog"), MixedArgs, ErrorCode, false);
	TestNull(TEXT("maxItems plus pageSize is rejected"), Mixed.Get());

	const TSharedPtr<FJsonObject> LegacyArgs = MakeShared<FJsonObject>();
	LegacyArgs->SetNumberField(TEXT("maxItems"), 8);
	FString LegacyError;
	const TSharedPtr<FJsonObject> Legacy = InvokeToolData(*this, Registry, TEXT("graph.nodeRegistry.catalog"), LegacyArgs, LegacyError);
	if (TestNotNull(TEXT("legacy catalog call still works"), Legacy.Get()))
	{
		TestEqual(TEXT("legacy maxItems still caps at 8"), DescriptorIds(Legacy).Num(), 8);
		TestFalse(TEXT("legacy call has no pagination envelope"), Legacy->HasField(TEXT("pagination")));
	}

	AddInfo(FString::Printf(
		TEXT("paginationEvidence catalogPages=%d catalogItems=%d contextPages=%d contextItems=%d"),
		Pages + 1,
		Collected.Num(),
		ContextPages + 1,
		ContextCollected.Num()));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitSocketWarmupTest,
	"UEAgentKit.Native.SocketWarmup",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitSocketWarmupTest::RunTest(const FString& Parameters)
{
	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	UK2Node_CallFunction* Node = nullptr;
	UEdGraphPin* ExecPin = nullptr;
	UEdGraphPin* DataPin = nullptr;
	if (!EnsureContextFixture(*this, Blueprint, Graph, Node, ExecPin, DataPin))
	{
		return false;
	}
	// Keep the fixture loaded for the external socket harness after this test.
	Blueprint->AddToRoot();

	const TSharedRef<FJsonObject> Fixture = MakeShared<FJsonObject>();
	Fixture->SetStringField(TEXT("schemaVersion"), TEXT("ueak.native.socket-fixture/1.0"));
	Fixture->SetStringField(TEXT("assetPath"), FixtureObjectPath);
	Fixture->SetStringField(TEXT("graphGuid"), GuidString(Graph->GraphGuid));
	Fixture->SetStringField(TEXT("nodeGuid"), GuidString(Node->NodeGuid));
	Fixture->SetStringField(TEXT("execPin"), ExecPin->PinName.ToString());
	Fixture->SetStringField(TEXT("dataPin"), DataPin->PinName.ToString());
	Fixture->SetStringField(TEXT("graphFingerprint"), ComputeFixtureGraphFingerprint(Graph));
	Fixture->SetStringField(TEXT("assetRevision"), FileRevision(FixtureFilename(Blueprint)));
	Fixture->SetBoolField(TEXT("packageDirty"), Blueprint->GetOutermost()->IsDirty());
	Fixture->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());

	FString Text;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
	if (!FJsonSerializer::Serialize(Fixture, Writer))
	{
		AddError(TEXT("The socket fixture metadata could not be serialized."));
		return false;
	}
	const FString Path = FPaths::Combine(
		FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("R1Native"), TEXT("socket-fixture.json"));
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
	if (!FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
	{
		AddError(FString::Printf(TEXT("The socket fixture metadata could not be written to %s."), *Path));
		return false;
	}
	AddInfo(FString::Printf(TEXT("Socket warmup wrote %s; fixture rooted"), *Path));
	return true;
}

#endif // WITH_EDITOR && WITH_DEV_AUTOMATION_TESTS