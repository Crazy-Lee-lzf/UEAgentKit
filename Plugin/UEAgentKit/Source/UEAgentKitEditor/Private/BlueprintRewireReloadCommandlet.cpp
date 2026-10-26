#include "BlueprintRewireReloadCommandlet.h"

#include "BlueprintContextSha256.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintRewireReload, Log, All);

namespace BlueprintRewireReloadPrivate
{
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
			+ From->PinName.ToString() + TEXT("|") + GuidLower(From->PinId) + TEXT(">")
			+ GuidLower(To->GetOwningNode()->NodeGuid) + TEXT("|")
			+ To->PinName.ToString() + TEXT("|") + GuidLower(To->PinId);
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

	bool ReadEdge(
		const TSharedPtr<FJsonValue>& Value,
		FString& OutFromNode,
		FString& OutFromPinName,
		FString& OutFromPinGuid,
		FString& OutToNode,
		FString& OutToPinName,
		FString& OutToPinGuid,
		FString& OutKind)
	{
		const TSharedPtr<FJsonObject> Edge = Value.IsValid() ? Value->AsObject() : nullptr;
		return Edge.IsValid()
			&& Edge->TryGetStringField(TEXT("fromNodeGuid"), OutFromNode)
			&& Edge->TryGetStringField(TEXT("fromPinName"), OutFromPinName)
			&& Edge->TryGetStringField(TEXT("fromPinGuid"), OutFromPinGuid)
			&& Edge->TryGetStringField(TEXT("toNodeGuid"), OutToNode)
			&& Edge->TryGetStringField(TEXT("toPinName"), OutToPinName)
			&& Edge->TryGetStringField(TEXT("toPinGuid"), OutToPinGuid)
			&& Edge->TryGetStringField(TEXT("kind"), OutKind);
	}

	FString CanonicalEdge(
		const FString& FromNode,
		const FString& FromPinName,
		const FString& FromPinGuid,
		const FString& ToNode,
		const FString& ToPinName,
		const FString& ToPinGuid)
	{
		return FromNode + TEXT("|") + FromPinName + TEXT("|") + FromPinGuid + TEXT(">")
			+ ToNode + TEXT("|") + ToPinName + TEXT("|") + ToPinGuid;
	}

	bool EdgeList(
		const TArray<TSharedPtr<FJsonValue>>& Values,
		const TCHAR* Label,
		TSet<FString>& OutKeys,
		TArray<TSharedPtr<FJsonValue>>& Mismatches)
	{
		for (const TSharedPtr<FJsonValue>& Value : Values)
		{
			FString FromNode;
			FString FromPinName;
			FString FromPinGuid;
			FString ToNode;
			FString ToPinName;
			FString ToPinGuid;
			FString Kind;
			if (!ReadEdge(
					Value,
					FromNode,
					FromPinName,
					FromPinGuid,
					ToNode,
					ToPinName,
					ToPinGuid,
					Kind))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					FString::Printf(TEXT("malformed %s entry"), Label)));
				continue;
			}
			OutKeys.Add(CanonicalEdge(
				FromNode,
				FromPinName,
				FromPinGuid,
				ToNode,
				ToPinName,
				ToPinGuid));
		}
		return true;
	}
}

UBlueprintRewireReloadCommandlet::UBlueprintRewireReloadCommandlet()
{
	IsClient = false;
	IsServer = false;
	LogToConsole = true;
}

int32 UBlueprintRewireReloadCommandlet::Main(const FString& Params)
{
	using namespace BlueprintRewireReloadPrivate;
	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("BlueprintRewire"),
		TEXT("evidence.json"));
	const FString OutputPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("BlueprintRewire"),
		TEXT("reload.json"));

	FString EvidenceText;
	if (!FFileHelper::LoadFileToString(EvidenceText, *EvidencePath))
	{
		UE_LOG(LogBlueprintRewireReload, Error, TEXT("Missing evidence file: %s"), *EvidencePath);
		return 1;
	}
	TSharedPtr<FJsonObject> Evidence;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(EvidenceText);
	if (!FJsonSerializer::Deserialize(Reader, Evidence) || !Evidence.IsValid())
	{
		UE_LOG(LogBlueprintRewireReload, Error, TEXT("Evidence JSON is invalid."));
		return 1;
	}

	FString AssetPath;
	FString AssetFilename;
	FString ExpectedAssetSha256;
	FString GraphGuidText;
	int32 ExpectedNodeCount = 0;
	int32 ExpectedEdgeCount = 0;
	if (!Evidence->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Evidence->TryGetStringField(TEXT("assetFilename"), AssetFilename)
		|| !Evidence->TryGetStringField(TEXT("assetSha256"), ExpectedAssetSha256)
		|| !Evidence->TryGetStringField(TEXT("graphGuid"), GraphGuidText))
	{
		UE_LOG(
			LogBlueprintRewireReload,
			Error,
			TEXT("Evidence lacks assetPath/assetFilename/assetSha256/graphGuid."));
		return 1;
	}
	{
		const double NodeCount = Evidence->GetNumberField(TEXT("expectedNodeCount"));
		const double EdgeCount = Evidence->GetNumberField(TEXT("expectedEdgeCount"));
		ExpectedNodeCount = static_cast<int32>(NodeCount);
		ExpectedEdgeCount = static_cast<int32>(EdgeCount);
	}

	FGuid GraphGuid;
	if (!FGuid::Parse(GraphGuidText, GraphGuid))
	{
		UE_LOG(LogBlueprintRewireReload, Error, TEXT("Evidence graphGuid is invalid."));
		return 1;
	}

	const TArray<TSharedPtr<FJsonValue>>* ExpectedEdges = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* RemovedEdges = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* AddedEdges = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* UntouchedEdges = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* ExpectedPins = nullptr;
	if (!Evidence->TryGetArrayField(TEXT("expectedEdges"), ExpectedEdges)
		|| ExpectedEdges == nullptr
		|| !Evidence->TryGetArrayField(TEXT("removedEdges"), RemovedEdges)
		|| RemovedEdges == nullptr
		|| !Evidence->TryGetArrayField(TEXT("addedEdges"), AddedEdges)
		|| AddedEdges == nullptr
		|| !Evidence->TryGetArrayField(TEXT("untouchedEdges"), UntouchedEdges)
		|| UntouchedEdges == nullptr
		|| !Evidence->TryGetArrayField(TEXT("pins"), ExpectedPins)
		|| ExpectedPins == nullptr)
	{
		UE_LOG(
			LogBlueprintRewireReload,
			Error,
			TEXT("Evidence lacks expectedEdges/removedEdges/addedEdges/untouchedEdges/pins."));
		return 1;
	}

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (Blueprint == nullptr)
	{
		UE_LOG(LogBlueprintRewireReload, Error, TEXT("Could not load Blueprint: %s"), *AssetPath);
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
		UE_LOG(
			LogBlueprintRewireReload,
			Error,
			TEXT("Could not find graph GUID %s."),
			*GraphGuidText);
		return 1;
	}

	TArray<TSharedPtr<FJsonValue>> Mismatches;
	TSet<FString> ExpectedNodeGuids;
	TSet<FString> SeenNodeGuids;
	for (const TSharedPtr<FJsonValue>& Value : *ExpectedPins)
	{
		const TSharedPtr<FJsonObject> NodeItem = Value.IsValid() ? Value->AsObject() : nullptr;
		FString NodeGuid;
		FString NodeClass;
		if (!NodeItem.IsValid()
			|| !NodeItem->TryGetStringField(TEXT("nodeGuid"), NodeGuid)
			|| !NodeItem->TryGetStringField(TEXT("nodeClass"), NodeClass))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("malformed pins entry")));
			continue;
		}
		ExpectedNodeGuids.Add(NodeGuid);
		UEdGraphNode* Node = FindNode(Graph, NodeGuid);
		if (Node == nullptr)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing node ") + NodeGuid));
			continue;
		}
		SeenNodeGuids.Add(NodeGuid);
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
			if (!PinItem.IsValid()
				|| !PinItem->TryGetStringField(TEXT("pinName"), PinName)
				|| !PinItem->TryGetStringField(TEXT("pinGuid"), PinGuid)
				|| !PinItem->TryGetStringField(TEXT("direction"), Direction)
				|| !PinItem->TryGetStringField(TEXT("category"), Category))
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
			if (!Pin->PinName.ToString().Equals(PinName, ESearchCase::CaseSensitive)
				|| !ActualDirection.Equals(Direction, ESearchCase::CaseSensitive)
				|| !ActualCategory.Equals(Category, ESearchCase::CaseSensitive))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
					TEXT("pin identity changed for %s.%s"),
					*NodeGuid,
					*PinName)));
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
	if (Graph->Nodes.Num() != ExpectedNodeCount
		|| SeenNodeGuids.Num() != ExpectedNodeCount
		|| ExpectedNodeGuids.Num() != ExpectedNodeCount)
	{
		Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
			TEXT("node count changed: expected=%d actual=%d"),
			ExpectedNodeCount,
			Graph->Nodes.Num())));
	}

	TArray<FString> ActualEdgeKeys;
	BuildEdgeKeys(Graph, ActualEdgeKeys);
	TSet<FString> ActualEdges;
	for (const FString& Key : ActualEdgeKeys)
	{
		ActualEdges.Add(Key);
	}
	TSet<FString> ExpectedEdgeKeys;
	EdgeList(*ExpectedEdges, TEXT("expectedEdges"), ExpectedEdgeKeys, Mismatches);
	TSet<FString> RemovedEdgeKeys;
	EdgeList(*RemovedEdges, TEXT("removedEdges"), RemovedEdgeKeys, Mismatches);
	TSet<FString> AddedEdgeKeys;
	EdgeList(*AddedEdges, TEXT("addedEdges"), AddedEdgeKeys, Mismatches);
	TSet<FString> UntouchedEdgeKeys;
	EdgeList(*UntouchedEdges, TEXT("untouchedEdges"), UntouchedEdgeKeys, Mismatches);

	for (const FString& Key : ExpectedEdgeKeys)
	{
		if (!ActualEdges.Contains(Key))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing edge ") + Key));
		}
	}
	for (const FString& Key : ActualEdges)
	{
		if (!ExpectedEdgeKeys.Contains(Key))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("unexpected edge ") + Key));
		}
	}
	for (const FString& Key : RemovedEdgeKeys)
	{
		if (ActualEdges.Contains(Key))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("removed edge is still present ") + Key));
		}
	}
	for (const FString& Key : AddedEdgeKeys)
	{
		if (!ActualEdges.Contains(Key))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("added edge is missing ") + Key));
		}
	}
	for (const FString& Key : UntouchedEdgeKeys)
	{
		if (!ActualEdges.Contains(Key))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("untouched edge is missing ") + Key));
		}
	}
	if (ActualEdgeKeys.Num() != ExpectedEdgeCount
		|| ExpectedEdgeKeys.Num() != ExpectedEdgeCount)
	{
		Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
			TEXT("edge count changed: expected=%d actual=%d"),
			ExpectedEdgeCount,
			ActualEdgeKeys.Num())));
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
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprint-rewire-reload/1.0"));
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
	Result->SetNumberField(TEXT("expectedNodeCount"), ExpectedNodeCount);
	Result->SetNumberField(TEXT("actualNodeCount"), Graph->Nodes.Num());
	Result->SetNumberField(TEXT("expectedEdgeCount"), ExpectedEdgeCount);
	Result->SetNumberField(TEXT("actualEdgeCount"), ActualEdgeKeys.Num());
	Result->SetNumberField(TEXT("removedEdgeCount"), RemovedEdgeKeys.Num());
	Result->SetNumberField(TEXT("addedEdgeCount"), AddedEdgeKeys.Num());
	Result->SetNumberField(TEXT("untouchedEdgeCount"), UntouchedEdgeKeys.Num());
	TArray<TSharedPtr<FJsonValue>> EdgeValues;
	for (const FString& Key : ActualEdgeKeys)
	{
		EdgeValues.Add(MakeShared<FJsonValueString>(Key));
	}
	Result->SetArrayField(TEXT("edges"), EdgeValues);
	Result->SetArrayField(TEXT("mismatches"), Mismatches);
	if (!WriteJson(OutputPath, Result))
	{
		UE_LOG(LogBlueprintRewireReload, Error, TEXT("Could not write %s."), *OutputPath);
		return 1;
	}
	UE_LOG(
		LogBlueprintRewireReload,
		Display,
		TEXT("Blueprint rewire reload verified=%s nodes=%d/%d edges=%d/%d mismatches=%d"),
		bVerified ? TEXT("true") : TEXT("false"),
		Graph->Nodes.Num(),
		ExpectedNodeCount,
		ActualEdgeKeys.Num(),
		ExpectedEdgeCount,
		Mismatches.Num());
	return bVerified ? 0 : 1;
}
