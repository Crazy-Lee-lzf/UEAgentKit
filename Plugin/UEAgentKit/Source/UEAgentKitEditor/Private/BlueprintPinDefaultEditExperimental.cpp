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
				Builder = AppendField(Builder, Pin->PinName.ToString().ToLower());
				Builder = AppendField(Builder, Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
				Builder = AppendField(Builder, Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower));
				// FNames are case-insensitive and the name table keeps whichever
				// spelling was interned first, so the SAME pin can print as "Index"
				// before an Undo/Redo and "index" after it (a wildcard node rebuilds
				// its pins while connecting), and the SAME pin type can print as
				// "object" before and "Object" after.  Hash the canonical lower-case
				// spelling; otherwise a reconstructed node looks like a changed one.
				Builder = AppendField(Builder, Pin->PinType.PinCategory.ToString().ToLower());
				Builder = AppendField(Builder, Pin->PinType.PinSubCategory.ToString().ToLower());
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


	FPinDefaultTarget ToPinDefaultTarget(
		const UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation& Operation)
	{
		FPinDefaultTarget Target;
		Target.NodeGuid = Operation.NodeGuid;
		Target.PinName = Operation.PinName;
		Target.PinGuid = Operation.PinGuid;
		Target.Type = Operation.Type;
		Target.Value = Operation.Value;
		Target.BeforeValue = Operation.BeforeValue;
		IsCanonicalGuid(Target.NodeGuid, Target.NodeGuidValue);
		IsCanonicalGuid(Target.PinGuid, Target.PinGuidValue);
		return Target;
	}
}

bool UEAgentKitBlueprintPinDefaultExperimental::TryResolveAtomicOperation(
	UEdGraph* Graph,
	FAtomicOperation& InOutOperation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-graph-unavailable"),
			TEXT("The exact graph must be loaded before a pin default can be resolved."));
	}
	if (InOutOperation.PinName.IsEmpty() || InOutOperation.PinName.Len() > MaxPinName)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-invalid-parameters"),
			FString::Printf(TEXT("pinName must be 1..%d characters."), MaxPinName));
	}
	FString Canonical;
	if (!CanonicalizeDefaultValue(
		InOutOperation.Type,
		InOutOperation.Value,
		Canonical,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	InOutOperation.Value = Canonical;
	FPinDefaultTarget Target = ToPinDefaultTarget(InOutOperation);
	UEdGraphNode* Node = nullptr;
	UEdGraphPin* Pin = nullptr;
	if (!FindExactInputPin(Graph, Target, Node, Pin, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
	if (Schema == nullptr || Pin == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-schema-not-supported"),
			TEXT("Pin default edit supports only K2 Blueprint graphs."));
	}
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
	InOutOperation.BeforeValue = Pin->DefaultValue;
	return true;
}

bool UEAgentKitBlueprintPinDefaultExperimental::TryApplyAtomicOperations(
	UEdGraph* Graph,
	const TArray<FAtomicOperation>& Operations,
	int32& OutAppliedCount,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	OutAppliedCount = 0;
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-graph-unavailable"),
			TEXT("The exact graph must be loaded before a pin default can be written."));
	}
	const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
	if (Schema == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-schema-not-supported"),
			TEXT("Pin default edit supports only K2 Blueprint graphs."));
	}
	TArray<TPair<UEdGraphPin*, FString>> AppliedDefaults;
	auto RestoreApplied = [Schema, &AppliedDefaults]()
	{
		for (int32 Index = AppliedDefaults.Num() - 1; Index >= 0; --Index)
		{
			UEdGraphPin* AppliedPin = AppliedDefaults[Index].Key;
			if (AppliedPin != nullptr)
			{
				Schema->TrySetDefaultValue(*AppliedPin, AppliedDefaults[Index].Value, true);
			}
		}
		AppliedDefaults.Reset();
	};

	for (const FAtomicOperation& Operation : Operations)
	{
		FPinDefaultTarget Target = ToPinDefaultTarget(Operation);
		UEdGraphNode* Node = nullptr;
		UEdGraphPin* Pin = nullptr;
		if (!FindExactInputPin(Graph, Target, Node, Pin, OutErrorCode, OutErrorMessage))
		{
			RestoreApplied();
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
			RestoreApplied();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-apply-rejected"),
				FString::Printf(
					TEXT("K2 did not store the requested default for %s.%s."),
					*Target.NodeGuid,
					*Target.PinName));
		}
		AppliedDefaults.Emplace(Pin, Target.BeforeValue);
	}
	OutAppliedCount = AppliedDefaults.Num();
	return true;
}

bool UEAgentKitBlueprintPinDefaultExperimental::TryCheckAtomicOperations(
	UEdGraph* Graph,
	const TArray<FAtomicOperation>& Operations,
	bool bExpectAfter,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	for (const FAtomicOperation& Operation : Operations)
	{
		FPinDefaultTarget Target = ToPinDefaultTarget(Operation);
		UEdGraphNode* Node = nullptr;
		UEdGraphPin* Pin = nullptr;
		if (!FindExactInputPin(Graph, Target, Node, Pin, OutErrorCode, OutErrorMessage))
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

bool UEAgentKitBlueprintPinDefaultExperimental::TryRestoreAtomicOperations(
	UEdGraph* Graph,
	const TArray<FAtomicOperation>& Operations,
	int32 AppliedCount,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	const int32 Count = FMath::Clamp(AppliedCount, 0, Operations.Num());
	if (Graph == nullptr || Count == 0)
	{
		return true;
	}
	const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
	if (Schema == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-pin-default-schema-not-supported"),
			TEXT("Pin default edit supports only K2 Blueprint graphs."));
	}
	for (int32 Index = Count - 1; Index >= 0; --Index)
	{
		FPinDefaultTarget Target = ToPinDefaultTarget(Operations[Index]);
		UEdGraphNode* Node = nullptr;
		UEdGraphPin* Pin = nullptr;
		if (!FindExactInputPin(Graph, Target, Node, Pin, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		Schema->TrySetDefaultValue(*Pin, Target.BeforeValue, true);
		if (!Pin->DefaultValue.Equals(Target.BeforeValue, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-pin-default-restore-failed"),
				FString::Printf(
					TEXT("Pin %s.%s did not restore to '%s'."),
					*Target.NodeGuid,
					*Target.PinName,
					*Target.BeforeValue));
		}
	}
	return true;
}

FString UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(UEdGraph* Graph)
{
	return ComputeDefaultsFingerprint(Graph);
}
