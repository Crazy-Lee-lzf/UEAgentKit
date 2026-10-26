#include "GraphEditExpansionReloadCommandlet.h"

#include "BlueprintContextSha256.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

DEFINE_LOG_CATEGORY_STATIC(LogGraphEditExpansionReload, Log, All);

namespace GraphEditExpansionReloadPrivate
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

	FString Sha256Text(const FString& Text)
	{
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Text, Digest))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Digest.ToLower();
	}
}

UGraphEditExpansionReloadCommandlet::UGraphEditExpansionReloadCommandlet()
{
	IsClient = false;
	IsServer = false;
	LogToConsole = true;
}

int32 UGraphEditExpansionReloadCommandlet::Main(const FString& Params)
{	using namespace GraphEditExpansionReloadPrivate;
	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("GraphEditExpansion"),
		TEXT("evidence.json"));
	const FString OutputPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("GraphEditExpansion"),
		TEXT("reload.json"));

	FString EvidenceText;
	if (!FFileHelper::LoadFileToString(EvidenceText, *EvidencePath))
	{
		UE_LOG(LogGraphEditExpansionReload, Error, TEXT("Missing evidence file: %s"), *EvidencePath);
		return 1;
	}
	TSharedPtr<FJsonObject> Evidence;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(EvidenceText);
	if (!FJsonSerializer::Deserialize(Reader, Evidence) || !Evidence.IsValid())
	{
		UE_LOG(LogGraphEditExpansionReload, Error, TEXT("Evidence JSON is invalid."));
		return 1;
	}

	FString AssetPath;
	FString GraphGuidText;
	FString AssetFilename;
	FString ExpectedAssetSha256;
	if (!Evidence->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Evidence->TryGetStringField(TEXT("graphGuid"), GraphGuidText)
		|| !Evidence->TryGetStringField(TEXT("assetFilename"), AssetFilename)
		|| !Evidence->TryGetStringField(TEXT("assetSha256"), ExpectedAssetSha256))
	{
		UE_LOG(LogGraphEditExpansionReload, Error, TEXT("Evidence lacks assetPath/graphGuid/assetFilename/assetSha256."));
		return 1;
	}
	FGuid GraphGuid;
	if (!FGuid::Parse(GraphGuidText, GraphGuid))
	{
		UE_LOG(LogGraphEditExpansionReload, Error, TEXT("Evidence graphGuid is invalid."));
		return 1;
	}

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (Blueprint == nullptr)
	{
		UE_LOG(LogGraphEditExpansionReload, Error, TEXT("Could not load Blueprint: %s"), *AssetPath);
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
		UE_LOG(LogGraphEditExpansionReload, Error, TEXT("Could not find graph GUID %s."), *GraphGuidText);
		return 1;
	}

	TSet<FString> ActualNodeGuids;
	TArray<FString> ActualEdgeStrings;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr || !Node->NodeGuid.IsValid())
		{
			continue;
		}
		const FString NodeGuid = GuidLower(Node->NodeGuid);
		ActualNodeGuids.Add(NodeGuid);
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr || Pin->Direction != EGPD_Output)
			{
				continue;
			}
			for (UEdGraphPin* Linked : Pin->LinkedTo)
			{
				if (Linked == nullptr || Linked->GetOwningNode() == nullptr)
				{
					continue;
				}
				ActualEdgeStrings.Add(
					NodeGuid + TEXT(".") + Pin->PinName.ToString()
					+ TEXT("->") + GuidLower(Linked->GetOwningNode()->NodeGuid)
					+ TEXT(".") + Linked->PinName.ToString());
			}
		}
	}
	ActualEdgeStrings.Sort();

	TArray<TSharedPtr<FJsonValue>> NodeValues;
	for (const FString& NodeGuid : ActualNodeGuids)
	{
		NodeValues.Add(MakeShared<FJsonValueString>(NodeGuid));
	}
	TArray<TSharedPtr<FJsonValue>> EdgeValues;
	for (const FString& Edge : ActualEdgeStrings)
	{
		EdgeValues.Add(MakeShared<FJsonValueString>(Edge));
	}

	TArray<TSharedPtr<FJsonValue>> Mismatches;
	FString ActualAssetDigest;
	bool bAssetShaVerified = false;
	if (!FBlueprintContextSha256::HashFile(AssetFilename, ActualAssetDigest))
	{
		Mismatches.Add(MakeShared<FJsonValueString>(TEXT("could not hash saved asset")));
	}
	else
	{
		const FString ActualAssetSha = FString(TEXT("sha256:")) + ActualAssetDigest.ToLower();
		bAssetShaVerified = ActualAssetSha.Equals(ExpectedAssetSha256, ESearchCase::CaseSensitive);
		if (!bAssetShaVerified)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("saved asset sha256 changed")));
		}
	}
	const TArray<TSharedPtr<FJsonValue>>* ExpectedNodeValues = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* ExpectedEdgeValues = nullptr;
	if (!Evidence->TryGetArrayField(TEXT("expectedNodeGuids"), ExpectedNodeValues)
		|| !Evidence->TryGetArrayField(TEXT("expectedEdges"), ExpectedEdgeValues)
		|| ExpectedNodeValues == nullptr
		|| ExpectedEdgeValues == nullptr)
	{
		Mismatches.Add(MakeShared<FJsonValueString>(TEXT("evidence lacks expectedNodeGuids/expectedEdges")));
	}
	else
	{
		TSet<FString> ExpectedNodeGuids;
		for (const TSharedPtr<FJsonValue>& Value : *ExpectedNodeValues)
		{
			FString NodeGuid;
			if (Value.IsValid() && Value->TryGetString(NodeGuid))
			{
				ExpectedNodeGuids.Add(NodeGuid);
			}
		}
		for (const FString& NodeGuid : ExpectedNodeGuids)
		{
			if (!ActualNodeGuids.Contains(NodeGuid))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing node ") + NodeGuid));
			}
		}
		for (const FString& NodeGuid : ActualNodeGuids)
		{
			if (!ExpectedNodeGuids.Contains(NodeGuid))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(TEXT("unexpected node ") + NodeGuid));
			}
		}

		TSet<FString> ExpectedEdges;
		for (const TSharedPtr<FJsonValue>& Value : *ExpectedEdgeValues)
		{
			FString Edge;
			if (Value.IsValid() && Value->TryGetString(Edge))
			{
				ExpectedEdges.Add(Edge);
			}
		}
		TSet<FString> ActualEdgeSet(ActualEdgeStrings);
		for (const FString& Edge : ExpectedEdges)
		{
			if (!ActualEdgeSet.Contains(Edge))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing edge ") + Edge));
			}
		}
		for (const FString& Edge : ActualEdgeSet)
		{
			if (!ExpectedEdges.Contains(Edge))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(TEXT("unexpected edge ") + Edge));
			}
		}
	}

	TArray<FString> HashParts;
	for (const FString& NodeGuid : ActualNodeGuids)
	{
		HashParts.Add(TEXT("N|") + NodeGuid);
	}
	for (const FString& Edge : ActualEdgeStrings)
	{
		HashParts.Add(TEXT("E|") + Edge);
	}
	HashParts.Sort();
	const FString GraphSha256 = Sha256Text(FString::Join(HashParts, TEXT("\n")));
	const bool bVerified = Mismatches.IsEmpty() && !GraphSha256.IsEmpty();

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graph-edit-reload/1.0"));
	Result->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Result->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("graphGuid"), GraphGuidText);
	Result->SetBoolField(TEXT("verified"), bVerified);
	Result->SetBoolField(TEXT("readOnly"), true);
	Result->SetBoolField(TEXT("assetSha256Verified"), bAssetShaVerified);
	Result->SetStringField(
		TEXT("assetSha256"),
		ActualAssetDigest.IsEmpty() ? FString() : FString(TEXT("sha256:")) + ActualAssetDigest.ToLower());
	Result->SetNumberField(TEXT("nodeCount"), ActualNodeGuids.Num());
	Result->SetNumberField(TEXT("edgeCount"), ActualEdgeStrings.Num());
	Result->SetStringField(TEXT("graphSha256"), GraphSha256);
	Result->SetArrayField(TEXT("nodeGuids"), NodeValues);
	Result->SetArrayField(TEXT("edges"), EdgeValues);
	Result->SetArrayField(TEXT("mismatches"), Mismatches);
	if (!WriteJson(OutputPath, Result))
	{
		UE_LOG(LogGraphEditExpansionReload, Error, TEXT("Could not write %s."), *OutputPath);
		return 1;
	}
	UE_LOG(
		LogGraphEditExpansionReload,
		Display,
		TEXT("GraphEdit reload verified=%s nodes=%d edges=%d sha=%s"),
		bVerified ? TEXT("true") : TEXT("false"),
		ActualNodeGuids.Num(),
		ActualEdgeStrings.Num(),
		*GraphSha256);
	return bVerified ? 0 : 1;
}
