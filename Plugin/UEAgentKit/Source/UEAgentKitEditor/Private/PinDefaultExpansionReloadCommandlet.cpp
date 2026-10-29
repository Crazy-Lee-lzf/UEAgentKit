#include "PinDefaultExpansionReloadCommandlet.h"

#include "BlueprintContextSha256.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

DEFINE_LOG_CATEGORY_STATIC(LogPinDefaultExpansionReload, Log, All);

namespace PinDefaultExpansionReloadPrivate
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

	bool PinMatchesType(const UEdGraphPin* Pin, const FString& Type)
	{
		if (Pin == nullptr || Pin->PinType.IsContainer() || Pin->PinType.bIsReference)
		{
			return false;
		}
		if (Type == TEXT("bool"))
		{
			return Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Boolean;
		}
		if (Type == TEXT("int"))
		{
			return Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Int;
		}
		if (Type == TEXT("double"))
		{
			return (Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Real
					&& Pin->PinType.PinSubCategory == UEdGraphSchema_K2::PC_Double)
				|| Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Double;
		}
		if (Type == TEXT("string"))
		{
			return Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_String;
		}
		return false;
	}

	FString MakeLinkKey(
		const FGuid& FromNodeGuid,
		const FString& FromPinName,
		const FGuid& FromPinGuid,
		const FGuid& ToNodeGuid,
		const FString& ToPinName,
		const FGuid& ToPinGuid)
	{
		return GuidLower(FromNodeGuid) + TEXT("|") + FromPinName + TEXT("|") + GuidLower(FromPinGuid)
			+ TEXT("->") + GuidLower(ToNodeGuid) + TEXT("|") + ToPinName + TEXT("|")
			+ GuidLower(ToPinGuid);
	}
}

UPinDefaultExpansionReloadCommandlet::UPinDefaultExpansionReloadCommandlet()
{
	IsClient = false;
	IsServer = false;
	LogToConsole = true;
}

int32 UPinDefaultExpansionReloadCommandlet::Main(const FString& Params)
{
	using namespace PinDefaultExpansionReloadPrivate;
	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("PinDefaultExpansion"),
		TEXT("evidence.json"));
	const FString OutputPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("PinDefaultExpansion"),
		TEXT("reload.json"));

	FString EvidenceText;
	if (!FFileHelper::LoadFileToString(EvidenceText, *EvidencePath))
	{
		UE_LOG(LogPinDefaultExpansionReload, Error, TEXT("Missing evidence file: %s"), *EvidencePath);
		return 1;
	}
	TSharedPtr<FJsonObject> Evidence;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(EvidenceText);
	if (!FJsonSerializer::Deserialize(Reader, Evidence) || !Evidence.IsValid())
	{
		UE_LOG(LogPinDefaultExpansionReload, Error, TEXT("Evidence JSON is invalid."));
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
		UE_LOG(
			LogPinDefaultExpansionReload,
			Error,
			TEXT("Evidence lacks assetPath/graphGuid/assetFilename/assetSha256."));
		return 1;
	}
	FGuid GraphGuid;
	if (!FGuid::Parse(GraphGuidText, GraphGuid))
	{
		UE_LOG(LogPinDefaultExpansionReload, Error, TEXT("Evidence graphGuid is invalid."));
		return 1;
	}

	const TArray<TSharedPtr<FJsonValue>>* ExpectedDefaults = nullptr;
	if (!Evidence->TryGetArrayField(TEXT("expectedDefaults"), ExpectedDefaults)
		|| ExpectedDefaults == nullptr
		|| ExpectedDefaults->IsEmpty())
	{
		UE_LOG(LogPinDefaultExpansionReload, Error, TEXT("Evidence lacks expectedDefaults."));
		return 1;
	}

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (Blueprint == nullptr)
	{
		UE_LOG(LogPinDefaultExpansionReload, Error, TEXT("Could not load Blueprint: %s"), *AssetPath);
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
		UE_LOG(LogPinDefaultExpansionReload, Error, TEXT("Could not find graph GUID %s."), *GraphGuidText);
		return 1;
	}

	TArray<TSharedPtr<FJsonValue>> ActualDefaults;
	TArray<TSharedPtr<FJsonValue>> Mismatches;
	for (const TSharedPtr<FJsonValue>& Value : *ExpectedDefaults)
	{
		const TSharedPtr<FJsonObject> Expected = Value.IsValid() ? Value->AsObject() : nullptr;
		FString NodeGuidText;
		FString PinName;
		FString PinGuidText;
		FString Type;
		FString ExpectedValue;
		if (!Expected.IsValid()
			|| !Expected->TryGetStringField(TEXT("nodeGuid"), NodeGuidText)
			|| !Expected->TryGetStringField(TEXT("pinName"), PinName)
			|| !Expected->TryGetStringField(TEXT("pinGuid"), PinGuidText)
			|| !Expected->TryGetStringField(TEXT("type"), Type)
			|| !Expected->TryGetStringField(TEXT("value"), ExpectedValue))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(TEXT("malformed expectedDefaults entry")));
			continue;
		}
		FGuid NodeGuid;
		FGuid PinGuid;
		if (!FGuid::Parse(NodeGuidText, NodeGuid) || !FGuid::Parse(PinGuidText, PinGuid))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("invalid node/pin GUID for ") + PinName));
			continue;
		}
		UEdGraphNode* Node = nullptr;
		for (UEdGraphNode* Candidate : Graph->Nodes)
		{
			if (Candidate != nullptr && Candidate->NodeGuid == NodeGuid)
			{
				Node = Candidate;
				break;
			}
		}
		if (Node == nullptr)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("missing node ") + NodeGuidText));
			continue;
		}
		TArray<UEdGraphPin*> Matches;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr
				&& Pin->Direction == EGPD_Input
				&& Pin->PinName.ToString().Equals(PinName, ESearchCase::CaseSensitive))
			{
				Matches.Add(Pin);
			}
		}
		if (Matches.Num() != 1)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("pin count is not one for ") + NodeGuidText + TEXT(".") + PinName));
			continue;
		}
		UEdGraphPin* Pin = Matches[0];
		if (!Pin->PinId.IsValid() || Pin->PinId != PinGuid)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("pin GUID changed for ") + NodeGuidText + TEXT(".") + PinName));
			continue;
		}
		if (!PinMatchesType(Pin, Type))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("pin type changed for ") + NodeGuidText + TEXT(".") + PinName));
			continue;
		}
		if (!Pin->DefaultValue.Equals(ExpectedValue, ESearchCase::CaseSensitive))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("default changed for ") + NodeGuidText + TEXT(".") + PinName
				+ TEXT(" expected='") + ExpectedValue + TEXT("' actual='") + Pin->DefaultValue + TEXT("'")));
			continue;
		}
		TSharedRef<FJsonObject> Actual = MakeShared<FJsonObject>();
		Actual->SetStringField(TEXT("nodeGuid"), NodeGuidText);
		Actual->SetStringField(TEXT("pinName"), PinName);
		Actual->SetStringField(TEXT("pinGuid"), PinGuidText);
		Actual->SetStringField(TEXT("type"), Type);
		Actual->SetStringField(TEXT("value"), Pin->DefaultValue);
		ActualDefaults.Add(MakeShared<FJsonValueObject>(Actual));
	}

	const TArray<TSharedPtr<FJsonValue>>* ExpectedNodeValues = nullptr;
	TSet<FString> ActualNodeGuids;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr && Node->NodeGuid.IsValid())
		{
			ActualNodeGuids.Add(GuidLower(Node->NodeGuid));
		}
	}
	if (Evidence->TryGetArrayField(TEXT("expectedNodeGuids"), ExpectedNodeValues)
		&& ExpectedNodeValues != nullptr)
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
	}
	else
	{
		Mismatches.Add(MakeShared<FJsonValueString>(TEXT("evidence lacks expectedNodeGuids")));
	}

	// Exact link set: every output->input edge must survive the save/reload with
	// the same node GUIDs, pin names and pin GUIDs on both ends.
	const TArray<TSharedPtr<FJsonValue>>* ExpectedLinks = nullptr;
	TSet<FString> ActualLinkKeys;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
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
				ActualLinkKeys.Add(MakeLinkKey(
					Node->NodeGuid,
					Pin->PinName.ToString(),
					Pin->PinId,
					Linked->GetOwningNode()->NodeGuid,
					Linked->PinName.ToString(),
					Linked->PinId));
			}
		}
	}
	if (!Evidence->TryGetArrayField(TEXT("expectedLinks"), ExpectedLinks)
		|| ExpectedLinks == nullptr)
	{
		Mismatches.Add(MakeShared<FJsonValueString>(TEXT("evidence lacks expectedLinks")));
	}
	else
	{
		TSet<FString> ExpectedLinkKeys;
		for (const TSharedPtr<FJsonValue>& Value : *ExpectedLinks)
		{
			const TSharedPtr<FJsonObject> Link = Value.IsValid() ? Value->AsObject() : nullptr;
			FString FromNode;
			FString FromPin;
			FString FromPinGuid;
			FString ToNode;
			FString ToPin;
			FString ToPinGuid;
			FGuid ParsedFromNode;
			FGuid ParsedFromPin;
			FGuid ParsedToNode;
			FGuid ParsedToPin;
			if (!Link.IsValid()
				|| !Link->TryGetStringField(TEXT("fromNodeGuid"), FromNode)
				|| !Link->TryGetStringField(TEXT("fromPinName"), FromPin)
				|| !Link->TryGetStringField(TEXT("fromPinGuid"), FromPinGuid)
				|| !Link->TryGetStringField(TEXT("toNodeGuid"), ToNode)
				|| !Link->TryGetStringField(TEXT("toPinName"), ToPin)
				|| !Link->TryGetStringField(TEXT("toPinGuid"), ToPinGuid)
				|| !FGuid::Parse(FromNode, ParsedFromNode)
				|| !FGuid::Parse(FromPinGuid, ParsedFromPin)
				|| !FGuid::Parse(ToNode, ParsedToNode)
				|| !FGuid::Parse(ToPinGuid, ParsedToPin))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(TEXT("malformed expectedLinks entry")));
				continue;
			}
			ExpectedLinkKeys.Add(MakeLinkKey(
				ParsedFromNode,
				FromPin,
				ParsedFromPin,
				ParsedToNode,
				ToPin,
				ParsedToPin));
		}
		for (const FString& Key : ExpectedLinkKeys)
		{
			if (!ActualLinkKeys.Contains(Key))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(TEXT("missing link ") + Key));
			}
		}
		for (const FString& Key : ActualLinkKeys)
		{
			if (!ExpectedLinkKeys.Contains(Key))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(TEXT("unexpected link ") + Key));
			}
		}
	}

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

	const bool bDefaultsComplete = ActualDefaults.Num() == ExpectedDefaults->Num();
	if (!bDefaultsComplete)
	{
		Mismatches.Add(MakeShared<FJsonValueString>(TEXT("not every expected default was physically verified")));
	}
	const bool bVerified = Mismatches.IsEmpty() && bDefaultsComplete && bAssetShaVerified;

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.pin-default-reload/1.0"));
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
	Result->SetNumberField(TEXT("expectedDefaultCount"), ExpectedDefaults->Num());
	Result->SetNumberField(TEXT("actualDefaultCount"), ActualDefaults.Num());
	Result->SetNumberField(TEXT("nodeCount"), ActualNodeGuids.Num());
	Result->SetNumberField(TEXT("linkCount"), ActualLinkKeys.Num());
	Result->SetArrayField(TEXT("defaults"), ActualDefaults);
	Result->SetArrayField(TEXT("mismatches"), Mismatches);
	if (!WriteJson(OutputPath, Result))
	{
		UE_LOG(LogPinDefaultExpansionReload, Error, TEXT("Could not write %s."), *OutputPath);
		return 1;
	}
	UE_LOG(
		LogPinDefaultExpansionReload,
		Display,
		TEXT("PinDefault reload verified=%s defaults=%d/%d nodes=%d links=%d"),
		bVerified ? TEXT("true") : TEXT("false"),
		ActualDefaults.Num(),
		ExpectedDefaults->Num(),
		ActualNodeGuids.Num(),
		ActualLinkKeys.Num());
	return bVerified ? 0 : 1;
}
