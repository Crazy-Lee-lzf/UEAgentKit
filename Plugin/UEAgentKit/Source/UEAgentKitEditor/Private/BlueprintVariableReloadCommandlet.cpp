#include "BlueprintVariableReloadCommandlet.h"

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

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintVariableReload, Log, All);

namespace BlueprintVariableReloadPrivate
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

	FString DescribePinType(const FEdGraphPinType& Type)
	{
		FString Label;
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Boolean)
		{
			Label = TEXT("bool");
		}
		else if (Type.PinCategory == UEdGraphSchema_K2::PC_Int)
		{
			Label = TEXT("int");
		}
		else if (
			(Type.PinCategory == UEdGraphSchema_K2::PC_Real
				&& Type.PinSubCategory == UEdGraphSchema_K2::PC_Double)
			|| Type.PinCategory == UEdGraphSchema_K2::PC_Double)
		{
			Label = TEXT("double");
		}
		else if (Type.PinCategory == UEdGraphSchema_K2::PC_String)
		{
			Label = TEXT("string");
		}
		else
		{
			Label = FString::Printf(
				TEXT("unsupported:%s:%s"),
				*Type.PinCategory.ToString(),
				*Type.PinSubCategory.ToString());
		}
		if (Type.ContainerType == EPinContainerType::Array)
		{
			Label += TEXT("[]");
		}
		else if (Type.ContainerType == EPinContainerType::Set)
		{
			Label += TEXT("{}");
		}
		else if (Type.ContainerType == EPinContainerType::Map)
		{
			Label += TEXT("{:}");
		}
		if (Type.bIsReference)
		{
			Label += TEXT("&");
		}
		if (Type.bIsConst)
		{
			Label += TEXT(":const");
		}
		if (Type.bIsWeakPointer)
		{
			Label += TEXT(":weak");
		}
		if (Type.PinSubCategoryObject != nullptr)
		{
			Label += TEXT(":") + Type.PinSubCategoryObject->GetPathName();
		}
		return Label;
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

	const FBPVariableDescription* FindVariable(const UBlueprint* Blueprint, const FString& Name)
	{
		if (Blueprint == nullptr)
		{
			return nullptr;
		}
		const FName NameValue(*Name);
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			if (Variable.VarName == NameValue)
			{
				return &Variable;
			}
		}
		return nullptr;
	}

	/**
	 * UE 5.6 treats FBPVariableDescription::DefaultValue as a compile-time
	 * transfer field: the compiler copies it into the generated class CDO
	 * property and then clears it.  A Blueprint that was loaded in a process
	 * with the compilation manager may therefore expose an empty
	 * DefaultValue while the effective default lives on the CDO property.
	 * Verify the pending string when present, otherwise the effective CDO
	 * value, and report which source proved it.
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
		OutActual = TEXT("<unsupported-generated-property>");
		return false;
	}

	void VerifyVariableList(
		const UBlueprint* Blueprint,
		const TArray<TSharedPtr<FJsonValue>>& Expected,
		const FString& Kind,
		TSet<FString>& OutSeenNames,
		TArray<TSharedPtr<FJsonValue>>& Mismatches,
		TArray<TSharedPtr<FJsonValue>>& Actual)
	{
		for (const TSharedPtr<FJsonValue>& Value : Expected)
		{
			const TSharedPtr<FJsonObject> ExpectedVariable = Value.IsValid() ? Value->AsObject() : nullptr;
			FString Name;
			FString Type;
			FString DefaultValue;
			FString GuidText;
			if (!ExpectedVariable.IsValid()
				|| !ExpectedVariable->TryGetStringField(TEXT("name"), Name)
				|| !ExpectedVariable->TryGetStringField(TEXT("type"), Type)
				|| !ExpectedVariable->TryGetStringField(TEXT("defaultValue"), DefaultValue)
				|| !ExpectedVariable->TryGetStringField(TEXT("guid"), GuidText))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("malformed ") + Kind + TEXT(" entry")));
				continue;
			}
			FGuid ExpectedGuid;
			if (!FGuid::Parse(GuidText, ExpectedGuid))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("invalid ") + Kind + TEXT(" GUID for ") + Name));
				continue;
			}
			const FBPVariableDescription* Variable = FindVariable(Blueprint, Name);
			if (Variable == nullptr)
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("missing ") + Kind + TEXT(" variable ") + Name));
				continue;
			}
			if (!Variable->VarName.ToString().Equals(Name, ESearchCase::CaseSensitive))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("variable name spelling changed for ") + Name));
				continue;
			}
			if (Variable->VarGuid != ExpectedGuid)
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("variable GUID changed for ") + Name));
				continue;
			}
			const FString ActualType = DescribePinType(Variable->VarType);
			if (!ActualType.Equals(Type, ESearchCase::CaseSensitive))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("variable type changed for ") + Name + TEXT(" expected=") + Type
					+ TEXT(" actual=") + ActualType));
				continue;
			}
			FString ActualDefault;
			FString DefaultSource;
			if (!MatchEffectiveDefault(
					Blueprint,
					*Variable,
					DefaultValue,
					ActualDefault,
					DefaultSource))
			{
				Mismatches.Add(MakeShared<FJsonValueString>(
					TEXT("variable default changed for ") + Name + TEXT(" expected='") + DefaultValue
					+ TEXT("' actual='") + ActualDefault + TEXT("' source=") + DefaultSource));
				continue;
			}
			UE_LOG(
				LogBlueprintVariableReload,
				Display,
				TEXT("Variable %s default verified source=%s actual=%s"),
				*Name,
				*DefaultSource,
				*ActualDefault);
			OutSeenNames.Add(Name);
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("name"), Name);
			Item->SetStringField(TEXT("type"), ActualType);
			Item->SetStringField(TEXT("defaultValue"), DefaultValue);
			Item->SetStringField(TEXT("actualDefaultValue"), ActualDefault);
			Item->SetStringField(TEXT("defaultSource"), DefaultSource);
			Item->SetStringField(TEXT("guid"), GuidLower(Variable->VarGuid));
			Item->SetStringField(TEXT("kind"), Kind);
			Actual.Add(MakeShared<FJsonValueObject>(Item));
		}
	}
}

UBlueprintVariableReloadCommandlet::UBlueprintVariableReloadCommandlet()
{
	IsClient = false;
	IsServer = false;
	LogToConsole = true;
}

int32 UBlueprintVariableReloadCommandlet::Main(const FString& Params)
{
	using namespace BlueprintVariableReloadPrivate;
	const FString EvidencePath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("BlueprintVariableExpansion"),
		TEXT("evidence.json"));
	const FString OutputPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("BlueprintVariableExpansion"),
		TEXT("reload.json"));

	FString EvidenceText;
	if (!FFileHelper::LoadFileToString(EvidenceText, *EvidencePath))
	{
		UE_LOG(LogBlueprintVariableReload, Error, TEXT("Missing evidence file: %s"), *EvidencePath);
		return 1;
	}
	TSharedPtr<FJsonObject> Evidence;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(EvidenceText);
	if (!FJsonSerializer::Deserialize(Reader, Evidence) || !Evidence.IsValid())
	{
		UE_LOG(LogBlueprintVariableReload, Error, TEXT("Evidence JSON is invalid."));
		return 1;
	}

	FString AssetPath;
	FString GraphGuidText;
	FString AssetFilename;
	FString ExpectedAssetSha256;
	int32 ExpectedVariableCount = 0;
	if (!Evidence->TryGetStringField(TEXT("assetPath"), AssetPath)
		|| !Evidence->TryGetStringField(TEXT("graphGuid"), GraphGuidText)
		|| !Evidence->TryGetStringField(TEXT("assetFilename"), AssetFilename)
		|| !Evidence->TryGetStringField(TEXT("assetSha256"), ExpectedAssetSha256))
	{
		UE_LOG(
			LogBlueprintVariableReload,
			Error,
			TEXT("Evidence lacks assetPath/graphGuid/assetFilename/assetSha256."));
		return 1;
	}
	{
		const double Count = Evidence->GetNumberField(TEXT("expectedVariableCount"));
		ExpectedVariableCount = static_cast<int32>(Count);
	}
	FGuid GraphGuid;
	if (!FGuid::Parse(GraphGuidText, GraphGuid))
	{
		UE_LOG(LogBlueprintVariableReload, Error, TEXT("Evidence graphGuid is invalid."));
		return 1;
	}

	const TArray<TSharedPtr<FJsonValue>>* ExpectedVariables = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* ExpectedExisting = nullptr;
	if (!Evidence->TryGetArrayField(TEXT("expectedVariables"), ExpectedVariables)
		|| ExpectedVariables == nullptr
		|| ExpectedVariables->IsEmpty()
		|| !Evidence->TryGetArrayField(TEXT("expectedExistingVariables"), ExpectedExisting)
		|| ExpectedExisting == nullptr)
	{
		UE_LOG(LogBlueprintVariableReload, Error, TEXT("Evidence lacks expectedVariables/expectedExistingVariables."));
		return 1;
	}

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (Blueprint == nullptr)
	{
		UE_LOG(LogBlueprintVariableReload, Error, TEXT("Could not load Blueprint: %s"), *AssetPath);
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
		UE_LOG(LogBlueprintVariableReload, Error, TEXT("Could not find graph GUID %s."), *GraphGuidText);
		return 1;
	}

	TArray<TSharedPtr<FJsonValue>> Mismatches;
	TArray<TSharedPtr<FJsonValue>> ActualVariables;
	TSet<FString> SeenNames;
	VerifyVariableList(
		Blueprint,
		*ExpectedVariables,
		TEXT("created"),
		SeenNames,
		Mismatches,
		ActualVariables);
	VerifyVariableList(
		Blueprint,
		*ExpectedExisting,
		TEXT("existing"),
		SeenNames,
		Mismatches,
		ActualVariables);

	if (Blueprint->NewVariables.Num() != ExpectedVariableCount)
	{
		Mismatches.Add(MakeShared<FJsonValueString>(FString::Printf(
			TEXT("variable count changed: expected=%d actual=%d"),
			ExpectedVariableCount,
			Blueprint->NewVariables.Num())));
	}
	for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
	{
		const FString Name = Variable.VarName.ToString();
		if (!SeenNames.Contains(Name))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("unexpected variable ") + Name));
			continue;
		}
		if ((Variable.PropertyFlags & (CPF_Edit | CPF_BlueprintVisible)) != (CPF_Edit | CPF_BlueprintVisible))
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("variable lost its standard property flags: ") + Name));
		}
		if (DescribePinType(Variable.VarType) == TEXT("string")
			&& Variable.FindMetaDataEntryIndexForKey(TEXT("MultiLine")) == INDEX_NONE)
		{
			Mismatches.Add(MakeShared<FJsonValueString>(
				TEXT("string variable lost its MultiLine metadata: ") + Name));
		}
	}

	TSet<FString> ActualNodeGuids;
	for (const UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node != nullptr && Node->NodeGuid.IsValid())
		{
			ActualNodeGuids.Add(GuidLower(Node->NodeGuid));
		}
	}
	const TArray<TSharedPtr<FJsonValue>>* ExpectedNodeValues = nullptr;
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

	TSet<FString> ActualLinkKeys;
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
	const TArray<TSharedPtr<FJsonValue>>* ExpectedLinks = nullptr;
	if (!Evidence->TryGetArrayField(TEXT("expectedLinks"), ExpectedLinks) || ExpectedLinks == nullptr)
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

	const int32 ExpectedMatchedVariables = ExpectedVariables->Num() + ExpectedExisting->Num();
	const bool bVariablesComplete = ActualVariables.Num() == ExpectedMatchedVariables
		&& ExpectedMatchedVariables == ExpectedVariableCount;
	if (!bVariablesComplete)
	{
		Mismatches.Add(MakeShared<FJsonValueString>(TEXT("not every expected variable was physically verified")));
	}
	const bool bVerified = Mismatches.IsEmpty() && bVariablesComplete && bAssetShaVerified;

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprint-variable-reload/1.0"));
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
	Result->SetNumberField(TEXT("expectedVariableCount"), ExpectedVariableCount);
	Result->SetNumberField(TEXT("actualVariableCount"), Blueprint->NewVariables.Num());
	Result->SetNumberField(TEXT("expectedCreatedCount"), ExpectedVariables->Num());
	Result->SetNumberField(TEXT("expectedExistingCount"), ExpectedExisting->Num());
	Result->SetNumberField(TEXT("nodeCount"), ActualNodeGuids.Num());
	Result->SetNumberField(TEXT("linkCount"), ActualLinkKeys.Num());
	Result->SetArrayField(TEXT("variables"), ActualVariables);
	Result->SetArrayField(TEXT("mismatches"), Mismatches);
	if (!WriteJson(OutputPath, Result))
	{
		UE_LOG(LogBlueprintVariableReload, Error, TEXT("Could not write %s."), *OutputPath);
		return 1;
	}
	UE_LOG(
		LogBlueprintVariableReload,
		Display,
		TEXT("Blueprint variable reload verified=%s variables=%d/%d nodes=%d links=%d"),
		bVerified ? TEXT("true") : TEXT("false"),
		Blueprint->NewVariables.Num(),
		ExpectedVariableCount,
		ActualNodeGuids.Num(),
		ActualLinkKeys.Num());
	return bVerified ? 0 : 1;
}
