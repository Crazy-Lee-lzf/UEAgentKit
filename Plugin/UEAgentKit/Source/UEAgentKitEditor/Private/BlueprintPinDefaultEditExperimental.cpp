#include "BlueprintPinDefaultEditExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitPinDefaultExperimental, Log, All);

namespace
{
	constexpr int32 MaxDefaults = 16;
	constexpr int32 MaxPinName = 128;
	constexpr int32 MaxStringValue = 1024;
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.blueprint-pin-default-preview/1.0");
	const TCHAR* const DefaultsFingerprintVersion = TEXT("ueak.blueprint-pin-default-state/1");

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

	struct FPinDefaultTarget
	{
		FString NodeGuid;
		FString PinName;
		FString PinGuid;
		FString Type;
		FString Value;
		FString BeforeValue;
		FGuid NodeGuidValue;
		FGuid PinGuidValue;
	};

	struct FPinDefaultPlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString PackageRevision;
		FString GraphFingerprint;
		FString DefaultsFingerprintBefore;
		FString DefaultsFingerprintAfter;
		FString PlanId;
		TArray<FPinDefaultTarget> Targets;
	};

	struct FPinDefaultReceipt
	{
		FString AssetPath;
		FString GraphGuid;
		FString GraphFingerprint;
		FString DefaultsFingerprint;
		int32 NodeCount = 0;
		int32 LinkCount = 0;
	};

	TMap<FString, FPinDefaultReceipt> RecentDefaultApplies;

	bool Fail(
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const TCHAR* Code,
		const FString& Message)
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return false;
	}

	bool RejectUnknownParams(
		const TSharedPtr<FJsonObject>& Params,
		const TSet<FString>& Allowed,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Params.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-invalid-parameters"),
				TEXT("Pin default parameters must be one JSON object."));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			if (!Allowed.Contains(Pair.Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-unknown-parameter"),
					FString::Printf(TEXT("Unknown pin default parameter: %s"), *Pair.Key));
			}
		}
		return true;
	}

	bool ReadString(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Key,
		FString& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage,
		bool bAllowEmpty = false)
	{
		if (!Object.IsValid() || !Object->TryGetStringField(Key, OutValue)
			|| (!bAllowEmpty && OutValue.IsEmpty()))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-invalid-parameters"),
				FString::Printf(TEXT("%s must be a%s string."), Key, bAllowEmpty ? TEXT("") : TEXT(" non-empty")));
		}
		return true;
	}

	bool IsCanonicalGuid(const FString& Value, FGuid& OutGuid)
	{
		if (Value.Len() != 36 || !FGuid::Parse(Value, OutGuid))
		{
			return false;
		}
		return OutGuid.ToString(EGuidFormats::DigitsWithHyphensLower).Equals(Value, ESearchCase::CaseSensitive);
	}

	bool IsSha1(const FString& Value)
	{
		if (!Value.StartsWith(TEXT("sha1:"), ESearchCase::CaseSensitive) || Value.Len() != 45)
		{
			return false;
		}
		for (int32 Index = 5; Index < Value.Len(); ++Index)
		{
			if (!FChar::IsHexDigit(Value[Index]))
			{
				return false;
			}
		}
		return true;
	}

	bool IsSha256(const FString& Value)
	{
		if (!Value.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive) || Value.Len() != 71)
		{
			return false;
		}
		for (int32 Index = 7; Index < Value.Len(); ++Index)
		{
			if (!FChar::IsHexDigit(Value[Index]))
			{
				return false;
			}
		}
		return true;
	}

	bool CanonicalizeDefaultValue(
		const FString& Type,
		const FString& Raw,
		FString& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Type == TEXT("bool"))
		{
			if (Raw != TEXT("true") && Raw != TEXT("false"))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					TEXT("Boolean default must be exactly 'true' or 'false'."));
			}
			OutValue = Raw;
			return true;
		}
		if (Type == TEXT("int"))
		{
			int32 Index = 0;
			if (Raw.Len() > 0 && Raw[0] == TEXT('-'))
			{
				Index = 1;
			}
			if (Index >= Raw.Len())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					TEXT("Integer default must be canonical decimal."));
			}
			if (Raw[Index] == TEXT('0') && Raw.Len() > Index + 1)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					TEXT("Integer default must not have a leading zero."));
			}
			for (int32 CharIndex = Index; CharIndex < Raw.Len(); ++CharIndex)
			{
				if (!FChar::IsDigit(Raw[CharIndex]))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-pin-default-value-invalid"),
						TEXT("Integer default must contain only digits after its optional minus sign."));
				}
			}
			const int64 Parsed = FCString::Atoi64(*Raw);
			if (Parsed < MIN_int32 || Parsed > MAX_int32 || Raw == TEXT("-0"))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					TEXT("Integer default must fit signed 32-bit range and must not be '-0'."));
			}
			OutValue = Raw;
			return true;
		}
		if (Type == TEXT("double"))
		{
			int32 Index = 0;
			if (Raw.Len() > 0 && Raw[0] == TEXT('-'))
			{
				Index = 1;
			}
			if (Index >= Raw.Len())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					TEXT("Double default must be canonical decimal."));
			}
			bool bSawDot = false;
			bool bSawDigit = false;
			for (int32 CharIndex = Index; CharIndex < Raw.Len(); ++CharIndex)
			{
				const TCHAR Character = Raw[CharIndex];
				if (Character == TEXT('.'))
				{
					if (bSawDot || !bSawDigit)
					{
						return Fail(
							OutErrorCode,
							OutErrorMessage,
							TEXT("graph-pin-default-value-invalid"),
							TEXT("Double default must have one decimal point between digits."));
					}
					bSawDot = true;
				}
				else if (!FChar::IsDigit(Character))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-pin-default-value-invalid"),
						TEXT("Double default must contain only digits and one decimal point."));
				}
				else
				{
					bSawDigit = true;
				}
			}
			if (!bSawDigit || Raw.EndsWith(TEXT(".")) || Raw == TEXT("-0") || Raw == TEXT("-0.0"))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					TEXT("Double default must be canonical finite decimal without exponent or trailing dot."));
			}
			const double Parsed = FCString::Atod(*Raw);
			if (!FMath::IsFinite(Parsed))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					TEXT("Double default must be finite."));
			}
			OutValue = Raw;
			return true;
		}
		if (Type == TEXT("string"))
		{
			if (Raw.Len() > MaxStringValue)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-value-invalid"),
					FString::Printf(TEXT("String default must be at most %d characters."), MaxStringValue));
			}
			for (const TCHAR Character : Raw)
			{
				if (Character < 0x20 || Character == 0x7F)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-pin-default-value-invalid"),
						TEXT("String default must not contain control characters."));
				}
			}
			OutValue = Raw;
			return true;
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-type-not-allowed"),
			TEXT("Supported pin default types: bool, int, double, string."));
	}

	bool PinMatchesDefaultType(const UEdGraphPin* Pin, const FString& Type)
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

	bool FindExactInputPin(
		UEdGraph* Graph,
		const FPinDefaultTarget& Target,
		UEdGraphNode*& OutNode,
		UEdGraphPin*& OutPin,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutNode = nullptr;
		OutPin = nullptr;
		FGuid NodeGuid;
		FGuid PinGuid;
		if (!IsCanonicalGuid(Target.NodeGuid, NodeGuid)
			|| !IsCanonicalGuid(Target.PinGuid, PinGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-invalid-identifier"),
				TEXT("nodeGuid and pinGuid must be canonical lower-case GUIDs."));
		}
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == NodeGuid)
			{
				OutNode = Node;
				break;
			}
		}
		if (OutNode == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-node-not-found"),
				FString::Printf(TEXT("No live node has GUID %s."), *Target.NodeGuid));
		}
		TArray<UEdGraphPin*> Matches;
		for (UEdGraphPin* Pin : OutNode->Pins)
		{
			if (Pin != nullptr
				&& Pin->Direction == EGPD_Input
				&& Pin->PinName.ToString().Equals(Target.PinName, ESearchCase::CaseSensitive))
			{
				Matches.Add(Pin);
			}
		}
		if (Matches.Num() != 1)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-pin-ambiguous"),
				FString::Printf(
					TEXT("Expected exactly one input pin named %s on node %s; found %d."),
					*Target.PinName,
					*Target.NodeGuid,
					Matches.Num()));
		}
		OutPin = Matches[0];
		if (!OutPin->PinId.IsValid() || OutPin->PinId != PinGuid)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-pin-guid-mismatch"),
				TEXT("The live pin PinId does not match pinGuid."));
		}
		if (OutPin->HasAnyConnections()
			|| OutPin->LinkedTo.Num() != 0
			|| OutPin->ParentPin != nullptr
			|| OutPin->SubPins.Num() != 0)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-pin-connected"),
				TEXT("Only a top-level, unlinked input pin may have its default changed."));
		}
		if (OutPin->bHidden || OutPin->bDefaultValueIsReadOnly || OutPin->bDefaultValueIsIgnored)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-pin-not-editable"),
				TEXT("The input pin is hidden, read-only, or ignores its default value."));
		}
		if (!PinMatchesDefaultType(OutPin, Target.Type))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-pin-type-mismatch"),
				FString::Printf(
					TEXT("Pin %s does not carry the declared %s type."),
					*Target.PinName,
					*Target.Type));
		}
		return true;
	}

	FString AppendField(const FString& Builder, const FString& Field)
	{
		return Builder + FString::Printf(TEXT("%d:"), Field.Len()) + Field;
	}

	FString ComputeDefaultsFingerprint(UEdGraph* Graph)
	{
		if (Graph == nullptr)
		{
			return FString();
		}
		TArray<UEdGraphNode*> SortedNodes;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				SortedNodes.Add(Node);
			}
		}
		SortedNodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
		{
			return Left.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
				< Right.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
		});
		FString Builder;
		Builder = AppendField(Builder, DefaultsFingerprintVersion);
		Builder = AppendField(Builder, Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		for (const UEdGraphNode* Node : SortedNodes)
		{
			Builder = AppendField(Builder, Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			TArray<UEdGraphPin*> Pins;
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin != nullptr)
				{
					Pins.Add(Pin);
				}
			}
			Pins.Sort([](const UEdGraphPin& Left, const UEdGraphPin& Right)
			{
				if (Left.PinName != Right.PinName)
				{
					return Left.PinName.LexicalLess(Right.PinName);
				}
				return Left.Direction < Right.Direction;
			});
			for (const UEdGraphPin* Pin : Pins)
			{
				Builder = AppendField(Builder, Pin->PinName.ToString());
				Builder = AppendField(Builder, Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
				Builder = AppendField(Builder, Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower));
				Builder = AppendField(Builder, Pin->PinType.PinCategory.ToString());
				Builder = AppendField(Builder, Pin->PinType.PinSubCategory.ToString());
				Builder = AppendField(
					Builder,
					Pin->PinType.PinSubCategoryObject != nullptr
						? Pin->PinType.PinSubCategoryObject->GetPathName()
						: FString());
				Builder = AppendField(Builder, Pin->DefaultValue);
				Builder = AppendField(
					Builder,
					Pin->DefaultObject != nullptr ? Pin->DefaultObject->GetPathName() : FString());
				Builder = AppendField(Builder, Pin->DefaultTextValue.ToString());
				Builder = AppendField(Builder, FString::FromInt(Pin->LinkedTo.Num()));
			}
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Builder, Digest))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Digest;
	}

	bool ParseTargets(
		const TSharedPtr<FJsonObject>& Params,
		TArray<FPinDefaultTarget>& OutTargets,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Params->TryGetArrayField(TEXT("defaults"), Values)
			|| Values == nullptr
			|| Values->IsEmpty()
			|| Values->Num() > MaxDefaults)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-invalid-defaults"),
				FString::Printf(TEXT("defaults must contain between 1 and %d objects."), MaxDefaults));
		}
		TSet<FString> Seen;
		for (const TSharedPtr<FJsonValue>& Value : *Values)
		{
			const TSharedPtr<FJsonObject>* Object = nullptr;
			if (!Value.IsValid() || !Value->TryGetObject(Object) || Object == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-invalid-defaults"),
					TEXT("Each default entry must be an object."));
			}
			if (!RejectUnknownParams(
				*Object,
				{ TEXT("nodeGuid"), TEXT("pinName"), TEXT("pinGuid"), TEXT("type"), TEXT("value") },
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			FPinDefaultTarget Target;
			if (!ReadString(*Object, TEXT("nodeGuid"), Target.NodeGuid, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("pinName"), Target.PinName, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("pinGuid"), Target.PinGuid, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("type"), Target.Type, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			if (Target.PinName.Len() > MaxPinName)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-invalid-parameters"),
					FString::Printf(TEXT("pinName must be at most %d characters."), MaxPinName));
			}
			if (!Object->Get()->TryGetStringField(TEXT("value"), Target.Value))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-invalid-parameters"),
					TEXT("value must be a string, including an empty string for string defaults."));
			}
			FGuid NodeGuid;
			FGuid PinGuid;
			if (!IsCanonicalGuid(Target.NodeGuid, NodeGuid)
				|| !IsCanonicalGuid(Target.PinGuid, PinGuid))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-invalid-identifier"),
					TEXT("nodeGuid and pinGuid must be canonical lower-case GUIDs."));
			}
			if (Target.Type != TEXT("bool") && Target.Type != TEXT("int")
				&& Target.Type != TEXT("double") && Target.Type != TEXT("string"))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-type-not-allowed"),
					TEXT("Supported pin default types: bool, int, double, string."));
			}
			FString Canonical;
			if (!CanonicalizeDefaultValue(
				Target.Type,
				Target.Value,
				Canonical,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			Target.Value = Canonical;
			Target.NodeGuidValue = NodeGuid;
			Target.PinGuidValue = PinGuid;
			const FString Key = Target.NodeGuid + TEXT("|") + Target.PinName + TEXT("|") + Target.PinGuid;
			if (Seen.Contains(Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-duplicate"),
					FString::Printf(TEXT("Duplicate pin default target: %s"), *Key));
			}
			Seen.Add(Key);
			OutTargets.Add(MoveTemp(Target));
		}
		return true;
	}

	bool BuildPlan(
		const TSharedPtr<FJsonObject>& Params,
		FPinDefaultPlan& OutPlan,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!ReadString(Params, TEXT("assetPath"), OutPlan.AssetPath, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("graphGuid"), OutPlan.GraphGuid, OutErrorCode, OutErrorMessage)
			|| !ParseTargets(Params, OutPlan.Targets, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		FGuid GraphGuid;
		if (!IsCanonicalGuid(OutPlan.GraphGuid, GraphGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-invalid-identifier"),
				TEXT("graphGuid must be one canonical lower-case GUID."));
		}
		if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-pie-active"),
				TEXT("Pin default edit is unavailable while PIE or SIE is active."));
		}

		TSharedPtr<FJsonObject> Preflight;
		if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
			Preflight,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (!Preflight->GetStringField(TEXT("assetPath")).Equals(OutPlan.AssetPath, ESearchCase::CaseSensitive)
			|| !Preflight->GetStringField(TEXT("graphGuid")).Equals(OutPlan.GraphGuid, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-focused-graph-mismatch"),
				TEXT("The focused Blueprint editor is not showing the exact assetPath/graphGuid from the request."));
		}
		if (Preflight->GetBoolField(TEXT("packageDirty")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-package-dirty"),
				TEXT("Pin default edit requires a clean package before Apply."));
		}
		if (!Preflight->GetBoolField(TEXT("editable")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-graph-not-editable"),
				TEXT("The focused graph is not editable."));
		}

		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
			OutPlan.AssetPath,
			OutPlan.GraphGuid,
			Blueprint,
			Graph,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (Blueprint == nullptr || Graph == nullptr || Graph->GetSchema() == nullptr
			|| !Graph->GetSchema()->IsA<UEdGraphSchema_K2>())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-schema-not-supported"),
				TEXT("Pin default edit supports only K2 Blueprint graphs."));
		}
		const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
		if (Schema == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-schema-not-supported"),
				TEXT("The graph schema is not UEdGraphSchema_K2."));
		}

		OutPlan.PackageRevision = Preflight->GetStringField(TEXT("packageRevision"));
		OutPlan.GraphFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
		OutPlan.DefaultsFingerprintBefore = ComputeDefaultsFingerprint(Graph);
		if (OutPlan.DefaultsFingerprintBefore.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-fingerprint-failed"),
				TEXT("Could not fingerprint the current pin default state."));
		}

		for (FPinDefaultTarget& Target : OutPlan.Targets)
		{
			UEdGraphNode* Node = nullptr;
			UEdGraphPin* Pin = nullptr;
			if (!FindExactInputPin(
				Graph,
				Target,
				Node,
				Pin,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			Target.BeforeValue = Pin->DefaultValue;
			const FString ValidationMessage = Schema->IsPinDefaultValid(
				Pin,
				Target.Value,
				nullptr,
				FText::GetEmpty());
			if (!ValidationMessage.IsEmpty())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-schema-rejected"),
					FString::Printf(
						TEXT("K2 schema rejected the default for %s.%s: %s"),
						*Target.NodeGuid,
						*Target.PinName,
						*ValidationMessage));
			}
		}

		FString Canonical;
		Canonical = AppendField(Canonical, TEXT("ueak.blueprint-pin-default/1.0"));
		Canonical = AppendField(Canonical, OutPlan.AssetPath);
		Canonical = AppendField(Canonical, OutPlan.GraphGuid);
		Canonical = AppendField(Canonical, OutPlan.PackageRevision);
		Canonical = AppendField(Canonical, OutPlan.GraphFingerprint);
		Canonical = AppendField(Canonical, OutPlan.DefaultsFingerprintBefore);
		TArray<FPinDefaultTarget> SortedTargets = OutPlan.Targets;
		SortedTargets.Sort([](const FPinDefaultTarget& Left, const FPinDefaultTarget& Right)
		{
			if (Left.NodeGuid != Right.NodeGuid)
			{
				return Left.NodeGuid < Right.NodeGuid;
			}
			if (Left.PinName != Right.PinName)
			{
				return Left.PinName < Right.PinName;
			}
			return Left.PinGuid < Right.PinGuid;
		});
		for (const FPinDefaultTarget& Target : SortedTargets)
		{
			Canonical = AppendField(Canonical, Target.NodeGuid);
			Canonical = AppendField(Canonical, Target.PinName);
			Canonical = AppendField(Canonical, Target.PinGuid);
			Canonical = AppendField(Canonical, Target.Type);
			Canonical = AppendField(Canonical, Target.BeforeValue);
			Canonical = AppendField(Canonical, Target.Value);
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-plan-hash-failed"),
				TEXT("Could not compute the pin default plan hash."));
		}
		OutPlan.PlanId = FString(TEXT("sha256:")) + Digest;
		return true;
	}

	TSharedPtr<FJsonObject> BuildPreviewResult(const FPinDefaultPlan& Plan)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("schemaVersion"), PreviewSchemaVersion);
		Result->SetBoolField(TEXT("experimental"), true);
		Result->SetBoolField(TEXT("readOnly"), true);
		Result->SetBoolField(TEXT("wroteBlueprint"), false);
		Result->SetBoolField(TEXT("savedPackage"), false);
		Result->SetBoolField(TEXT("compiled"), false);
		Result->SetStringField(TEXT("planId"), Plan.PlanId);
		Result->SetStringField(TEXT("assetPath"), Plan.AssetPath);
		Result->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
		Result->SetStringField(TEXT("packageRevision"), Plan.PackageRevision);
		Result->SetStringField(TEXT("graphFingerprint"), Plan.GraphFingerprint);
		Result->SetStringField(TEXT("defaultsFingerprint"), Plan.DefaultsFingerprintBefore);
		Result->SetNumberField(TEXT("defaultCount"), Plan.Targets.Num());
		Result->SetStringField(
			TEXT("requiredConfirmation"),
			FString(UEAgentKitBlueprintPinDefaultExperimental::ApplyConfirmationPrefix) + Plan.PlanId);
		TArray<TSharedPtr<FJsonValue>> Values;
		for (const FPinDefaultTarget& Target : Plan.Targets)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("nodeGuid"), Target.NodeGuid);
			Item->SetStringField(TEXT("pinName"), Target.PinName);
			Item->SetStringField(TEXT("pinGuid"), Target.PinGuid);
			Item->SetStringField(TEXT("type"), Target.Type);
			Item->SetStringField(TEXT("beforeValue"), Target.BeforeValue);
			Item->SetStringField(TEXT("afterValue"), Target.Value);
			Values.Add(MakeShared<FJsonValueObject>(Item));
		}
		Result->SetArrayField(TEXT("defaults"), Values);
		return Result;
	}

	bool CheckCurrentValues(
		UEdGraph* Graph,
		const TArray<FPinDefaultTarget>& Targets,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		for (const FPinDefaultTarget& Target : Targets)
		{
			UEdGraphNode* Node = nullptr;
			UEdGraphPin* Pin = nullptr;
			if (!FindExactInputPin(
				Graph,
				Target,
				Node,
				Pin,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			const FString Expected = bExpectAfter ? Target.Value : Target.BeforeValue;
			if (!Pin->DefaultValue.Equals(Expected, ESearchCase::CaseSensitive))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-pin-default-readback-failed"),
					FString::Printf(
						TEXT("Pin %s.%s read back as '%s'; expected '%s'."),
						*Target.NodeGuid,
						*Target.PinName,
						*Pin->DefaultValue,
						*Expected));
			}
		}
		return true;
	}

	int32 CountLinks(UEdGraph* Graph)
	{
		int32 Count = 0;
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			for (const UEdGraphPin* Pin : Node->Pins)
			{
				if (Pin != nullptr && Pin->Direction == EGPD_Output)
				{
					Count += Pin->LinkedTo.Num();
				}
			}
		}
		return Count;
	}
}

bool UEAgentKitBlueprintPinDefaultExperimental::IsExperimentalWriteAvailable()
{
	if (bTestOverrideSet)
	{
		return bTestOverrideEnabled;
	}
	bool bEnabled = false;
	if (GConfig == nullptr)
	{
		return false;
	}
	return GConfig->GetBool(
		TEXT("UEAgentKit"),
		TEXT("ExperimentalGraphPatchEdit"),
		bEnabled,
		GEngineIni)
		&& bEnabled;
}

void UEAgentKitBlueprintPinDefaultExperimental::SetExperimentalWriteEnabledForTest(bool bEnabled)
{
	bTestOverrideSet = true;
	bTestOverrideEnabled = bEnabled;
}

void UEAgentKitBlueprintPinDefaultExperimental::ResetExperimentalWriteForTest()
{
	bTestOverrideSet = false;
	bTestOverrideEnabled = false;
}

bool UEAgentKitBlueprintPinDefaultExperimental::TryPreviewExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	UE_LOG(
		LogUEAgentKitPinDefaultExperimental,
		Display,
		TEXT("TryPreviewExperimental received valid=%s fields=%d"),
		Params.IsValid() ? TEXT("true") : TEXT("false"),
		Params.IsValid() ? Params->Values.Num() : -1);
	if (!RejectUnknownParams(
		Params,
		{ TEXT("assetPath"), TEXT("graphGuid"), TEXT("defaults") },
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-experimental-disabled"),
			TEXT("Experimental Blueprint pin default edit is not opted in for this project."));
	}
	FPinDefaultPlan Plan;
	if (!BuildPlan(Params, Plan, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	OutResult = BuildPreviewResult(Plan);
	return true;
}

bool UEAgentKitBlueprintPinDefaultExperimental::TryApplyExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!RejectUnknownParams(
		Params,
		{
			TEXT("assetPath"),
			TEXT("graphGuid"),
			TEXT("defaults"),
			TEXT("planId"),
			TEXT("confirmation"),
		},
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-experimental-disabled"),
			TEXT("Experimental Blueprint pin default edit is not opted in for this project."));
	}
	if (GEditor == nullptr || GEditor->Trans == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-transaction-unavailable"),
			TEXT("The Editor transaction history is unavailable."));
	}
	FString RequestedPlanId;
	FString Confirmation;
	if (!ReadString(Params, TEXT("planId"), RequestedPlanId, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	FPinDefaultPlan Plan;
	if (!BuildPlan(Params, Plan, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!RequestedPlanId.Equals(Plan.PlanId, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-plan-stale"),
			FString::Printf(
				TEXT("The graph or request changed after Preview; request a fresh preview. requested=%s current=%s"),
				*RequestedPlanId,
				*Plan.PlanId));
	}
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintPinDefaultExperimental::ApplyConfirmationPrefix) + Plan.PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-confirmation-required"),
			FString::Printf(TEXT("Explicit Apply confirmation must be exactly %s."), *ExpectedConfirmation));
	}

	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		Plan.AssetPath,
		Plan.GraphGuid,
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	const int32 NodeCountBefore = Graph != nullptr ? Graph->Nodes.Num() : 0;
	const int32 LinkCountBefore = Graph != nullptr ? CountLinks(Graph) : 0;
	{
		FScopedTransaction Transaction(
			FText::FromString(TEXT("UEAgentKit Set Blueprint Pin Defaults")));
		Blueprint->Modify();
		Graph->Modify();
		const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
		TArray<TPair<UEdGraphPin*, FString>> AppliedDefaults;
		// Cancelling the transaction discards the undo record but not the live pin
		// values, so restore every previously written pin before cancelling.
		auto Rollback = [Schema, &AppliedDefaults]()
		{
			for (int32 Index = AppliedDefaults.Num() - 1; Index >= 0; --Index)
			{
				UEdGraphPin* AppliedPin = AppliedDefaults[Index].Key;
				if (AppliedPin != nullptr && Schema != nullptr)
				{
					Schema->TrySetDefaultValue(*AppliedPin, AppliedDefaults[Index].Value, true);
				}
			}
			AppliedDefaults.Reset();
		};
		auto Abort = [&OutErrorCode, &OutErrorMessage, &Transaction, &Rollback](
			const TCHAR* Code,
			const FString& Message) -> bool
		{
			Rollback();
			Transaction.Cancel();
			return Fail(OutErrorCode, OutErrorMessage, Code, Message);
		};

		for (const FPinDefaultTarget& Target : Plan.Targets)
		{
			UEdGraphNode* Node = nullptr;
			UEdGraphPin* Pin = nullptr;
			if (!FindExactInputPin(
				Graph,
				Target,
				Node,
				Pin,
				OutErrorCode,
				OutErrorMessage))
			{
				Rollback();
				Transaction.Cancel();
				return false;
			}
			// Nodes created or recreated outside the Editor's node factory do not
			// necessarily carry RF_Transactional.  Without it UObject::Modify() is a
			// no-op, the transaction serializes no changed node, and the whole pin
			// default edit is discarded as empty.  Mark the exact target node so its
			// pin default change is a real undoable transaction.
			Node->SetFlags(RF_Transactional);
			Node->Modify();
			Pin->Modify();
			Schema->TrySetDefaultValue(*Pin, Target.Value, true);
			if (!Pin->DefaultValue.Equals(Target.Value, ESearchCase::CaseSensitive))
			{
				return Abort(
					TEXT("graph-pin-default-apply-rejected"),
					FString::Printf(
						TEXT("K2 did not store the requested default for %s.%s."),
						*Target.NodeGuid,
						*Target.PinName));
			}
			AppliedDefaults.Emplace(Pin, Target.BeforeValue);
		}
		Blueprint->GetOutermost()->MarkPackageDirty();
	}

	const FString DefaultsFingerprintAfter = ComputeDefaultsFingerprint(Graph);
	const int32 NodeCountAfter = Graph->Nodes.Num();
	const int32 LinkCountAfter = CountLinks(Graph);
	bool bUndoVerified = false;
	bool bRedoVerified = false;
	TSharedPtr<FJsonObject> PostPreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		PostPreflight,
		OutErrorCode,
		OutErrorMessage)
		|| !PostPreflight->GetBoolField(TEXT("packageDirty"))
		|| !CheckCurrentValues(
			Graph,
			Plan.Targets,
			true,
			OutErrorCode,
			OutErrorMessage)
		|| DefaultsFingerprintAfter.IsEmpty())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-readback-failed"),
			TEXT("Post-Apply pin default readback or package dirty check failed."));
	}

	bUndoVerified = GEditor->UndoTransaction()
		&& CheckCurrentValues(
			Graph,
			Plan.Targets,
			false,
			OutErrorCode,
			OutErrorMessage)
		&& ComputeDefaultsFingerprint(Graph).Equals(
			Plan.DefaultsFingerprintBefore,
			ESearchCase::CaseSensitive);
	bRedoVerified = bUndoVerified
		&& GEditor->RedoTransaction()
		&& CheckCurrentValues(
			Graph,
			Plan.Targets,
			true,
			OutErrorCode,
			OutErrorMessage)
		&& ComputeDefaultsFingerprint(Graph).Equals(
			DefaultsFingerprintAfter,
			ESearchCase::CaseSensitive);
	if (!bUndoVerified || !bRedoVerified)
	{
		if (GEditor->Trans != nullptr)
		{
			GEditor->UndoTransaction();
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-undo-redo-failed"),
			TEXT("Undo/Redo verification failed for the pin default edit."));
	}

	TSharedPtr<FJsonObject> FinalPreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		FinalPreflight,
		OutErrorCode,
		OutErrorMessage)
		|| !FinalPreflight->GetBoolField(TEXT("packageDirty")))
	{
		GEditor->UndoTransaction();
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-readback-failed"),
			TEXT("Final package dirty check failed for the pin default edit."));
	}

	if (RecentDefaultApplies.Num() > 16)
	{
		RecentDefaultApplies.Reset();
	}
	FPinDefaultReceipt Receipt;
	Receipt.AssetPath = Plan.AssetPath;
	Receipt.GraphGuid = Plan.GraphGuid;
	Receipt.GraphFingerprint = Plan.GraphFingerprint;
	Receipt.DefaultsFingerprint = DefaultsFingerprintAfter;
	Receipt.NodeCount = NodeCountAfter;
	Receipt.LinkCount = LinkCountAfter;
	RecentDefaultApplies.Add(Plan.PlanId, Receipt);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.blueprint-pin-default-apply/1.0"));
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetBoolField(TEXT("applied"), true);
	Result->SetBoolField(TEXT("singleTransaction"), true);
	Result->SetBoolField(TEXT("undoVerified"), bUndoVerified);
	Result->SetBoolField(TEXT("redoVerified"), bRedoVerified);
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("autoSave"), false);
	Result->SetStringField(TEXT("planId"), Plan.PlanId);
	Result->SetStringField(TEXT("assetPath"), Plan.AssetPath);
	Result->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
	Result->SetStringField(TEXT("confirmation"), Confirmation);
	Result->SetStringField(
		TEXT("requiredSaveConfirmation"),
		FString(SaveConfirmationPrefix) + Plan.PlanId);
	Result->SetStringField(TEXT("graphFingerprintAfter"), Plan.GraphFingerprint);
	Result->SetStringField(TEXT("defaultsFingerprintAfter"), DefaultsFingerprintAfter);
	Result->SetNumberField(TEXT("nodeCountBefore"), NodeCountBefore);
	Result->SetNumberField(TEXT("nodeCountAfter"), NodeCountAfter);
	Result->SetNumberField(TEXT("linkCountBefore"), LinkCountBefore);
	Result->SetNumberField(TEXT("linkCountAfter"), LinkCountAfter);
	Result->SetNumberField(TEXT("defaultCount"), Plan.Targets.Num());
	TArray<TSharedPtr<FJsonValue>> Values;
	for (const FPinDefaultTarget& Target : Plan.Targets)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("nodeGuid"), Target.NodeGuid);
		Item->SetStringField(TEXT("pinName"), Target.PinName);
		Item->SetStringField(TEXT("pinGuid"), Target.PinGuid);
		Item->SetStringField(TEXT("type"), Target.Type);
		Item->SetStringField(TEXT("beforeValue"), Target.BeforeValue);
		Item->SetStringField(TEXT("afterValue"), Target.Value);
		Values.Add(MakeShared<FJsonValueObject>(Item));
	}
	Result->SetArrayField(TEXT("defaults"), Values);
	OutResult = Result;
	return true;
}

bool UEAgentKitBlueprintPinDefaultExperimental::TrySaveExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!RejectUnknownParams(
		Params,
		{
			TEXT("assetPath"),
			TEXT("graphGuid"),
			TEXT("planId"),
			TEXT("expectedGraphFingerprint"),
			TEXT("expectedDefaultsFingerprint"),
			TEXT("expectedNodeCount"),
			TEXT("expectedLinkCount"),
			TEXT("confirmation"),
		},
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-experimental-disabled"),
			TEXT("Experimental Blueprint pin default edit is not opted in for this project."));
	}
	FString AssetPath;
	FString GraphGuid;
	FString PlanId;
	FString ExpectedGraphFingerprint;
	FString ExpectedDefaultsFingerprint;
	FString Confirmation;
	if (!ReadString(Params, TEXT("assetPath"), AssetPath, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("graphGuid"), GraphGuid, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("planId"), PlanId, OutErrorCode, OutErrorMessage)
		|| !ReadString(
			Params,
			TEXT("expectedGraphFingerprint"),
			ExpectedGraphFingerprint,
			OutErrorCode,
			OutErrorMessage)
		|| !ReadString(
			Params,
			TEXT("expectedDefaultsFingerprint"),
			ExpectedDefaultsFingerprint,
			OutErrorCode,
			OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!IsSha256(PlanId)
		|| !IsSha1(ExpectedGraphFingerprint)
		|| !IsSha256(ExpectedDefaultsFingerprint))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-invalid-parameters"),
			TEXT("Save requires sha256 planId/defaultsFingerprint and sha1 graphFingerprint."));
	}
	double NodeCountNumber = 0.0;
	double LinkCountNumber = 0.0;
	if (!Params->TryGetNumberField(TEXT("expectedNodeCount"), NodeCountNumber)
		|| !Params->TryGetNumberField(TEXT("expectedLinkCount"), LinkCountNumber)
		|| NodeCountNumber < 0.0
		|| LinkCountNumber < 0.0
		|| NodeCountNumber > static_cast<double>(MAX_int32)
		|| LinkCountNumber > static_cast<double>(MAX_int32)
		|| !FMath::IsNearlyEqual(
			NodeCountNumber,
			static_cast<double>(static_cast<int32>(NodeCountNumber)))
		|| !FMath::IsNearlyEqual(
			LinkCountNumber,
			static_cast<double>(static_cast<int32>(LinkCountNumber))))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-invalid-parameters"),
			TEXT("Save requires non-negative integer node/link counts."));
	}
	const int32 ExpectedNodeCount = static_cast<int32>(NodeCountNumber);
	const int32 ExpectedLinkCount = static_cast<int32>(LinkCountNumber);
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintPinDefaultExperimental::SaveConfirmationPrefix) + PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-confirmation-required"),
			FString::Printf(TEXT("Explicit Save confirmation must be exactly %s."), *ExpectedConfirmation));
	}
	FPinDefaultReceipt* Receipt = RecentDefaultApplies.Find(PlanId);
	if (Receipt == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-requires-apply"),
			TEXT("Save requires a successful Apply of the exact plan in this Editor session."));
	}
	if (!Receipt->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive)
		|| !Receipt->GraphGuid.Equals(GraphGuid, ESearchCase::CaseSensitive)
		|| !Receipt->GraphFingerprint.Equals(ExpectedGraphFingerprint, ESearchCase::CaseSensitive)
		|| !Receipt->DefaultsFingerprint.Equals(ExpectedDefaultsFingerprint, ESearchCase::CaseSensitive)
		|| Receipt->NodeCount != ExpectedNodeCount
		|| Receipt->LinkCount != ExpectedLinkCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-plan-mismatch"),
			TEXT("Save parameters do not match the receipt recorded by the preceding Apply."));
	}

	TSharedPtr<FJsonObject> Preflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		Preflight,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!Preflight->GetStringField(TEXT("assetPath")).Equals(AssetPath, ESearchCase::CaseSensitive)
		|| !Preflight->GetStringField(TEXT("graphGuid")).Equals(GraphGuid, ESearchCase::CaseSensitive)
		|| !Preflight->GetBoolField(TEXT("packageDirty"))
		|| !Preflight->GetStringField(TEXT("graphFingerprint")).Equals(
			ExpectedGraphFingerprint,
			ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-plan-mismatch"),
			TEXT("The focused graph is no longer the dirty post-Apply graph."));
	}
	UBlueprint* Blueprint = nullptr;
	UEdGraph* Graph = nullptr;
	if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
		AssetPath,
		GraphGuid,
		Blueprint,
		Graph,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (Blueprint == nullptr || Graph == nullptr
		|| Graph->Nodes.Num() != ExpectedNodeCount
		|| CountLinks(Graph) != ExpectedLinkCount
		|| !ComputeDefaultsFingerprint(Graph).Equals(
			ExpectedDefaultsFingerprint,
			ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-plan-mismatch"),
			TEXT("The live graph node/link/default state no longer matches the Apply receipt."));
	}

	UPackage* Package = Blueprint->GetOutermost();
	if (Package == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-package-invalid"),
			TEXT("The Blueprint package is unavailable."));
	}
	const FString Filename = FPackageName::LongPackageNameToFilename(
		Package->GetName(),
		FPackageName::GetAssetPackageExtension());
	if (Filename.IsEmpty() || !IFileManager::Get().FileExists(*Filename))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-package-file-missing"),
			TEXT("The exact Blueprint package file does not exist on disk."));
	}
	const int64 FileSizeBefore = IFileManager::Get().FileSize(*Filename);
	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	SaveArgs.Error = GError;
	if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs) || Package->IsDirty())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-failed"),
			TEXT("SavePackage did not confirm a clean saved package."));
	}
	FString AssetDigest;
	if (!FBlueprintContextSha256::HashFile(Filename, AssetDigest))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-hash-failed"),
			TEXT("Could not hash the saved Blueprint package."));
	}
	AssetDigest = AssetDigest.ToLower();
	TSharedPtr<FJsonObject> PostSavePreflight;
	FString PostCode;
	FString PostMessage;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		PostSavePreflight,
		PostCode,
		PostMessage)
		|| PostSavePreflight->GetBoolField(TEXT("packageDirty"))
		|| !PostSavePreflight->GetStringField(TEXT("graphFingerprint")).Equals(
			ExpectedGraphFingerprint,
			ESearchCase::CaseSensitive)
		|| PostSavePreflight->GetNumberField(TEXT("nodeCount")) != ExpectedNodeCount
		|| !ComputeDefaultsFingerprint(Graph).Equals(
			ExpectedDefaultsFingerprint,
			ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-readback-failed"),
			TEXT("Post-save graph/default readback did not match the Apply receipt."));
	}
	const int64 FileSizeAfter = IFileManager::Get().FileSize(*Filename);
	if (FileSizeAfter < 0)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-save-readback-failed"),
			TEXT("The saved package file could not be read back."));
	}

	TSharedRef<FJsonObject> Readback = MakeShared<FJsonObject>();
	Readback->SetStringField(TEXT("assetPath"), AssetPath);
	Readback->SetStringField(TEXT("graphGuid"), GraphGuid);
	Readback->SetStringField(TEXT("assetSha256"), FString(TEXT("sha256:")) + AssetDigest);
	Readback->SetStringField(
		TEXT("packageRevision"),
		PostSavePreflight->GetStringField(TEXT("packageRevision")));
	Readback->SetStringField(TEXT("graphFingerprint"), ExpectedGraphFingerprint);
	Readback->SetStringField(TEXT("defaultsFingerprint"), ExpectedDefaultsFingerprint);
	Readback->SetBoolField(TEXT("packageDirty"), false);
	Readback->SetNumberField(TEXT("nodeCount"), ExpectedNodeCount);
	Readback->SetNumberField(TEXT("linkCount"), ExpectedLinkCount);
	Readback->SetBoolField(TEXT("independentReloadVerified"), false);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("save-experimental-pin-defaults"));
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("graphGuid"), GraphGuid);
	Result->SetStringField(TEXT("planId"), PlanId);
	Result->SetStringField(TEXT("confirmation"), Confirmation);
	Result->SetStringField(TEXT("assetSha256"), FString(TEXT("sha256:")) + AssetDigest);
	Result->SetStringField(
		TEXT("packageRevision"),
		PostSavePreflight->GetStringField(TEXT("packageRevision")));
	Result->SetStringField(TEXT("graphFingerprint"), ExpectedGraphFingerprint);
	Result->SetStringField(TEXT("defaultsFingerprint"), ExpectedDefaultsFingerprint);
	Result->SetBoolField(TEXT("saved"), true);
	Result->SetBoolField(TEXT("packageDirty"), false);
	Result->SetBoolField(TEXT("readbackVerified"), true);
	Result->SetBoolField(TEXT("independentReloadVerified"), false);
	Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
	Result->SetNumberField(TEXT("fileSizeBefore"), static_cast<double>(FileSizeBefore));
	Result->SetNumberField(TEXT("fileSizeAfter"), static_cast<double>(FileSizeAfter));
	Result->SetBoolField(TEXT("autoSave"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetObjectField(TEXT("readback"), Readback);
	RecentDefaultApplies.Remove(PlanId);
	OutResult = Result;
	return true;
}
