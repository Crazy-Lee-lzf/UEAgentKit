#include "BlueprintAgentE2EReloadCommandlet.h"

#include "BlueprintContextSha256.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintAgentE2EReload, Log, All);

namespace BlueprintAgentE2EReloadPrivate
{
	const TCHAR* const ExpectedRelativePath = TEXT("UEAgentKitAgentE2E/expected.json");
	const TCHAR* const ReloadRelativePath = TEXT("UEAgentKitAgentE2E/reload.json");

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	bool WriteJson(const FString& Path, const TSharedRef<FJsonObject>& Object)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object, Writer))
		{
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		return FFileHelper::SaveStringToFile(
			Text,
			*Path,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}


	FString EdgeKey(const UEdGraphPin* From, const UEdGraphPin* To)
	{
		return GuidLower(From->GetOwningNode()->NodeGuid) + TEXT("|")
			+ GuidLower(From->PinId) + TEXT(">")
			+ GuidLower(To->GetOwningNode()->NodeGuid) + TEXT("|")
			+ GuidLower(To->PinId);
	}

	void BuildEdgeKeys(const UEdGraph* Graph, TArray<FString>& OutKeys)
	{
		OutKeys.Reset();
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin == nullptr || Pin->Direction != EGPD_Output)
				{
					continue;
				}
				for (const UEdGraphPin* Linked : Pin->LinkedTo)
				{
					if (Linked != nullptr && Linked->GetOwningNode() != nullptr)
					{
						OutKeys.Add(EdgeKey(Pin, Linked));
					}
				}
			}
		}
		OutKeys.Sort();
	}

	UEdGraphNode* FindNode(UEdGraph* Graph, const FString& NodeGuidText)
	{
		FGuid NodeGuid;
		if (!FGuid::Parse(NodeGuidText, NodeGuid))
		{
			return nullptr;
		}
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == NodeGuid)
			{
				return Node;
			}
		}
		return nullptr;
	}

	UEdGraphPin* FindPin(UEdGraphNode* Node, const FString& PinGuidText)
	{
		FGuid PinGuid;
		if (!FGuid::Parse(PinGuidText, PinGuid))
		{
			return nullptr;
		}
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr && Pin->PinId == PinGuid)
			{
				return Pin;
			}
		}
		return nullptr;
	}

	/**
	 * UE 5.6 treats FBPVariableDescription::DefaultValue as a compile-time
	 * transfer field; after a load with the compilation manager the effective
	 * default lives on the generated class CDO.  Verify the pending string when
	 * present, otherwise the CDO value, and report which source proved it.
	 */
	bool MatchEffectiveDefault(
		const UBlueprint* Blueprint,
		const FBPVariableDescription& Variable,
		const FString& Expected,
		FString& OutActual,
		FString& OutSource)
	{
		if (!Variable.DefaultValue.IsEmpty())
		{
			OutSource = TEXT("pending-variable-string");
			OutActual = Variable.DefaultValue;
			return Variable.DefaultValue.Equals(Expected, ESearchCase::CaseSensitive);
		}
		OutSource = TEXT("generated-class-cdo");
		UClass* Class = Blueprint->GeneratedClass
			? Blueprint->GeneratedClass.Get()
			: Blueprint->SkeletonGeneratedClass.Get();
		if (Class == nullptr)
		{
			OutActual = TEXT("<no-generated-class>");
			return false;
		}
		UObject* CDO = Class->GetDefaultObject(false);
		if (CDO == nullptr)
		{
			OutActual = TEXT("<no-cdo>");
			return false;
		}
		FProperty* Property = FindFProperty<FProperty>(Class, Variable.VarName);
		if (Property == nullptr)
		{
			OutActual = TEXT("<missing-generated-property>");
			return false;
		}
		const void* Value = Property->ContainerPtrToValuePtr<void>(CDO);
		if (const FBoolProperty* BoolProperty = CastField<FBoolProperty>(Property))
		{
			const bool bActual = BoolProperty->GetPropertyValue(Value);
			OutActual = bActual ? TEXT("true") : TEXT("false");
			return OutActual.Equals(Expected, ESearchCase::CaseSensitive);
		}
		if (const FIntProperty* IntProperty = CastField<FIntProperty>(Property))
		{
			const int32 Actual = IntProperty->GetPropertyValue(Value);
			OutActual = FString::FromInt(Actual);
			return Actual == FCString::Atoi(*Expected);
		}
		if (const FDoubleProperty* DoubleProperty = CastField<FDoubleProperty>(Property))
		{
			const double Actual = DoubleProperty->GetPropertyValue(Value);
			OutActual = FString::Printf(TEXT("%.17g"), Actual);
			return FMath::IsFinite(Actual) && Actual == FCString::Atod(*Expected);
		}
		if (const FStrProperty* StringProperty = CastField<FStrProperty>(Property))
		{
			OutActual = StringProperty->GetPropertyValue(Value);
			return OutActual.Equals(Expected, ESearchCase::CaseSensitive);
		}
		OutActual = TEXT("<unsupported-property>");
		return false;
	}
}

UBlueprintAgentE2EReloadCommandlet::UBlueprintAgentE2EReloadCommandlet()
{
	IsClient = false;
	IsServer = false;
	LogToConsole = true;
}

int32 UBlueprintAgentE2EReloadCommandlet::Main(const FString& Params)
{
	using namespace BlueprintAgentE2EReloadPrivate;
	const FString ExpectedPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		ExpectedRelativePath);
	const FString OutputPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		ReloadRelativePath);

	FString ExpectedText;
	if (!FFileHelper::LoadFileToString(ExpectedText, *ExpectedPath))
	{
		UE_LOG(LogBlueprintAgentE2EReload, Error, TEXT("Missing expected file: %s"), *ExpectedPath);
		return 1;
	}
	TSharedPtr<FJsonObject> Expected;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ExpectedText);
	if (!FJsonSerializer::Deserialize(Reader, Expected) || !Expected.IsValid())
	{
		UE_LOG(LogBlueprintAgentE2EReload, Error, TEXT("Expected JSON is invalid."));
		return 1;
	}

	FString AssetPath;
	FString AssetFilename;
	FString ExpectedAssetSha256;
	FString GraphGuidText;
	if (!Expected->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Expected->TryGetStringField(TEXT("assetFilename"), AssetFilename)
		|| !Expected->TryGetStringField(TEXT("assetSha256"), ExpectedAssetSha256)
		|| !Expected->TryGetStringField(TEXT("graphGuid"), GraphGuidText))
	{
		UE_LOG(
			LogBlueprintAgentE2EReload,
			Error,
			TEXT("Expected lacks assetPath/assetFilename/assetSha256/graphGuid."));
		return 1;
	}
	FGuid GraphGuid;
	if (!FGuid::Parse(GraphGuidText, GraphGuid))
	{
		UE_LOG(LogBlueprintAgentE2EReload, Error, TEXT("Expected graphGuid is invalid."));
		return 1;
	}

	const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Edges = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Variables = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* DeletedNodes = nullptr;
	if (!Expected->TryGetArrayField(TEXT("nodes"), Nodes)
		|| Nodes == nullptr
		|| !Expected->TryGetArrayField(TEXT("edges"), Edges)
		|| Edges == nullptr
		|| !Expected->TryGetArrayField(TEXT("variables"), Variables)
		|| Variables == nullptr
		|| !Expected->TryGetArrayField(TEXT("deletedNodeGuids"), DeletedNodes)
		|| DeletedNodes == nullptr)
	{
		UE_LOG(
			LogBlueprintAgentE2EReload,
			Error,
			TEXT("Expected lacks nodes/edges/variables/deletedNodeGuids."));
		return 1;
	}

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (Blueprint == nullptr)
	{
		UE_LOG(LogBlueprintAgentE2EReload, Error, TEXT("Could not load Blueprint: %s"), *AssetPath);
		return 1;
	}
	TArray<UEdGraph*> Graphs;
	Blueprint->GetAllGraphs(Graphs);
	UEdGraph** FoundGraph = Graphs.FindByPredicate([&GraphGuid](const UEdGraph* Candidate)
	{
		return Candidate != nullptr && Candidate->GraphGuid == GraphGuid;
	});
	UEdGraph* Graph = FoundGraph != nullptr ? *FoundGraph : nullptr;
	if (Graph == nullptr)
	{
		UE_LOG(LogBlueprintAgentE2EReload, Error, TEXT("Could not find graph GUID %s."), *GraphGuidText);
		return 1;
	}

	TArray<TSharedPtr<FJsonValue>> Mismatches;
	TArray<TSharedPtr<FJsonValue>> VerifiedVariables;

	for (const TSharedPtr<FJsonValue>& Value : *DeletedNodes)
	{
		const FString DeletedGuid = Value.IsValid() ? Value->AsString() : FString();
		if (DeletedGuid.IsEmpty() || FindNode(Graph, DeletedGuid) != nullptr)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("deleted node is still present: ") + DeletedGuid));
		}
	}

	TSet<FString> ExpectedNodeGuids;
	for (const TSharedPtr<FJsonValue>& Value : *Nodes)
	{
		const TSharedPtr<FJsonObject> NodeItem = Value.IsValid() ? Value->AsObject() : nullptr;
		FString NodeGuid;
		FString NodeClass;
		if (!NodeItem.IsValid()
			|| !NodeItem->TryGetStringField(TEXT("guid"), NodeGuid)
			|| !NodeItem->TryGetStringField(TEXT("class"), NodeClass))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("malformed nodes entry")));
			continue;
		}
		ExpectedNodeGuids.Add(NodeGuid);
		UEdGraphNode* Node = FindNode(Graph, NodeGuid);
		if (Node == nullptr)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing node ") + NodeGuid));
			continue;
		}
		const FString ActualClass =
			Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString();
		if (!ActualClass.Equals(NodeClass, ESearchCase::CaseSensitive))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("node class changed for ") + NodeGuid));
			continue;
		}
		const TArray<TSharedPtr<FJsonValue>>* Pins = nullptr;
		if (!NodeItem->TryGetArrayField(TEXT("pins"), Pins) || Pins == nullptr)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("pins entry lacks pins for ") + NodeGuid));
			continue;
		}
		if (Node->Pins.Num() != Pins->Num())
		{
			Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
				TEXT("pin count changed for %s expected=%d actual=%d"),
				*NodeGuid,
				Pins->Num(),
				Node->Pins.Num())));
			continue;
		}
		for (const TSharedPtr<FJsonValue>& PinValue : *Pins)
		{
			const TSharedPtr<FJsonObject> PinItem =
				PinValue.IsValid() ? PinValue->AsObject() : nullptr;
			FString PinName;
			FString PinGuid;
			FString Direction;
			FString Category;
			FString SubCategory;
			FString DefaultValue;
			if (!PinItem.IsValid()
				|| !PinItem->TryGetStringField(TEXT("name"), PinName)
				|| !PinItem->TryGetStringField(TEXT("id"), PinGuid)
				|| !PinItem->TryGetStringField(TEXT("direction"), Direction)
				|| !PinItem->TryGetStringField(TEXT("category"), Category)
				|| !PinItem->TryGetStringField(TEXT("subcategory"), SubCategory)
				|| !PinItem->TryGetStringField(TEXT("defaultValue"), DefaultValue))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("malformed pin entry for ") + NodeGuid));
				continue;
			}
			UEdGraphPin* Pin = FindPin(Node, PinGuid);
			if (Pin == nullptr)
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("missing pin ") + PinGuid + TEXT(" on ") + NodeGuid));
				continue;
			}
			const FString ActualDirection =
				Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output");
			const FString ActualCategory = Pin->PinType.PinCategory.ToString();
			const FString ActualSubCategory = Pin->PinType.PinSubCategory.ToString();
			if (!Pin->PinName.ToString().Equals(PinName, ESearchCase::CaseSensitive)
				|| !ActualDirection.Equals(Direction, ESearchCase::CaseSensitive)
				|| !ActualCategory.Equals(Category, ESearchCase::CaseSensitive)
				|| !ActualSubCategory.Equals(SubCategory, ESearchCase::CaseSensitive))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("pin identity changed for %s.%s"),
					*NodeGuid,
					*PinName)));
				continue;
			}
			if (!Pin->DefaultValue.Equals(DefaultValue, ESearchCase::CaseSensitive))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("pin default changed for %s.%s expected='%s' actual='%s'"),
					*NodeGuid,
					*PinName,
					*DefaultValue,
					*Pin->DefaultValue)));
			}
		}
	}
	for (const UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr && !ExpectedNodeGuids.Contains(GuidLower(Node->NodeGuid)))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("unexpected node ") + GuidLower(Node->NodeGuid)));
		}
	}
	if (Graph->Nodes.Num() != Nodes->Num() || ExpectedNodeGuids.Num() != Nodes->Num())
	{
		Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
			TEXT("node count changed: expected=%d actual=%d"),
			Nodes->Num(),
			Graph->Nodes.Num())));
	}

	TArray<FString> ActualEdgeKeys;
	BuildEdgeKeys(Graph, ActualEdgeKeys);
	TSet<FString> ActualEdges;
	for (const FString& Key : ActualEdgeKeys)
	{
		ActualEdges.Add(Key);
	}
	TSet<FString> ExpectedEdges;
	for (const TSharedPtr<FJsonValue>& Value : *Edges)
	{
		const TSharedPtr<FJsonObject> Edge = Value.IsValid() ? Value->AsObject() : nullptr;
		FString FromNode;
		FString FromPin;
		FString ToNode;
		FString ToPin;
		if (!Edge.IsValid()
			|| !Edge->TryGetStringField(TEXT("fromNodeGuid"), FromNode)
			|| !Edge->TryGetStringField(TEXT("fromPinId"), FromPin)
			|| !Edge->TryGetStringField(TEXT("toNodeGuid"), ToNode)
			|| !Edge->TryGetStringField(TEXT("toPinId"), ToPin))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("malformed edges entry")));
			continue;
		}
		ExpectedEdges.Add(FromNode + TEXT("|") + FromPin + TEXT(">") + ToNode + TEXT("|") + ToPin);
	}
	for (const FString& Key : ExpectedEdges)
	{
		if (!ActualEdges.Contains(Key))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing edge ") + Key));
		}
	}
	for (const FString& Key : ActualEdges)
	{
		if (!ExpectedEdges.Contains(Key))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("unexpected edge ") + Key));
		}
	}
	if (ActualEdgeKeys.Num() != Edges->Num() || ExpectedEdges.Num() != Edges->Num())
	{
		Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
			TEXT("edge count changed: expected=%d actual=%d"),
			Edges->Num(),
			ActualEdgeKeys.Num())));
	}

	TSet<FString> ExpectedVariableNames;
	for (const TSharedPtr<FJsonValue>& Value : *Variables)
	{
		const TSharedPtr<FJsonObject> VariableItem =
			Value.IsValid() ? Value->AsObject() : nullptr;
		FString Name;
		FString Category;
		FString SubCategory;
		FString DefaultValue;
		if (!VariableItem.IsValid()
			|| !VariableItem->TryGetStringField(TEXT("name"), Name)
			|| !VariableItem->TryGetStringField(TEXT("category"), Category)
			|| !VariableItem->TryGetStringField(TEXT("subcategory"), SubCategory)
			|| !VariableItem->TryGetStringField(TEXT("defaultValue"), DefaultValue))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("malformed variables entry")));
			continue;
		}
		ExpectedVariableNames.Add(Name);
		const FBPVariableDescription* Match = Blueprint->NewVariables.FindByPredicate(
			[&Name](const FBPVariableDescription& Candidate)
			{
				return Candidate.VarName.ToString().Equals(Name, ESearchCase::CaseSensitive);
			});
		if (Match == nullptr)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing variable ") + Name));
			continue;
		}
		if (!Match->VarType.PinCategory.ToString().Equals(Category, ESearchCase::CaseSensitive)
			|| !Match->VarType.PinSubCategory.ToString().Equals(SubCategory, ESearchCase::CaseSensitive))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("variable type changed for ") + Name));
			continue;
		}
		FString ActualDefault;
		FString DefaultSource;
		if (!MatchEffectiveDefault(
				Blueprint,
				*Match,
				DefaultValue,
				ActualDefault,
				DefaultSource))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
				TEXT("variable default changed for %s expected='%s' actual='%s'"),
				*Name,
				*DefaultValue,
				*ActualDefault)));
			continue;
		}
		TSharedRef<FJsonObject> Verified = MakeShared<FJsonObject>();
		Verified->SetStringField(TEXT("name"), Name);
		Verified->SetStringField(TEXT("defaultSource"), DefaultSource);
		Verified->SetStringField(TEXT("actualDefaultValue"), ActualDefault);
		VerifiedVariables.Add(MakeShared<FJsonValueObject>(Verified));
	}
	if (Blueprint->NewVariables.Num() != Variables->Num()
		|| ExpectedVariableNames.Num() != Variables->Num())
	{
		Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
			TEXT("variable count changed: expected=%d actual=%d"),
			Variables->Num(),
			Blueprint->NewVariables.Num())));
	}

	FString ActualAssetDigest;
	bool bAssetShaVerified = false;
	if (!FBlueprintContextSha256::HashFile(AssetFilename, ActualAssetDigest))
	{
		Mismatches.Add(MakeShared<FJsonValueString>(TEXT("could not hash the saved asset")));
	}
	else
	{
		const FString ActualAssetSha =
			FString(TEXT("sha256:")) + ActualAssetDigest.ToLower();
		bAssetShaVerified = ActualAssetSha.Equals(
			ExpectedAssetSha256,
			ESearchCase::CaseSensitive);
		if (!bAssetShaVerified)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("saved asset sha256 changed")));
		}
	}

	const bool bVerified = Mismatches.IsEmpty() && bAssetShaVerified;
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprint-agent-e2e-reload/1.0"));
	Result->SetNumberField(
		TEXT("processId"),
		static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Result->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("graphGuid"), GraphGuidText);
	Result->SetBoolField(TEXT("verified"), bVerified);
	Result->SetBoolField(TEXT("readOnly"), true);
	Result->SetBoolField(TEXT("assetSha256Verified"), bAssetShaVerified);
	Result->SetStringField(
		TEXT("assetSha256"),
		ActualAssetDigest.IsEmpty()
			? FString()
			: FString(TEXT("sha256:")) + ActualAssetDigest.ToLower());
	Result->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
	Result->SetNumberField(TEXT("edgeCount"), ActualEdgeKeys.Num());
	Result->SetNumberField(TEXT("variableCount"), Blueprint->NewVariables.Num());
	Result->SetNumberField(TEXT("deletedNodeCount"), DeletedNodes->Num());
	Result->SetArrayField(TEXT("verifiedVariables"), VerifiedVariables);
	TArray<TSharedPtr<FJsonValue>> EdgeValues;
	for (const FString& Key : ActualEdgeKeys)
	{
		EdgeValues.Add(MakeShared<FJsonValueString>(Key));
	}
	Result->SetArrayField(TEXT("edges"), EdgeValues);
	Result->SetArrayField(TEXT("mismatches"), Mismatches);
	if (!WriteJson(OutputPath, Result))
	{
		UE_LOG(LogBlueprintAgentE2EReload, Error, TEXT("Could not write %s."), *OutputPath);
		return 1;
	}
	UE_LOG(
		LogBlueprintAgentE2EReload,
		Display,
		TEXT("Agent E2E reload verified=%s nodes=%d edges=%d variables=%d mismatches=%d"),
		bVerified ? TEXT("true") : TEXT("false"),
		Graph->Nodes.Num(),
		ActualEdgeKeys.Num(),
		Blueprint->NewVariables.Num(),
		Mismatches.Num());
	return bVerified ? 0 : 1;
}
