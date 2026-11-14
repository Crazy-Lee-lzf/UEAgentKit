#include "BlueprintPatchExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphEditExperimental.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintPinDefaultEditExperimental.h"
#include "BlueprintVariableWriteExperimental.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitPatchExperimental, Log, All);

namespace
{
	constexpr int32 MaxOperations = 16;
	constexpr int32 MaxOpIdLength = 64;
	constexpr int32 MaxAssetPath = 512;
	const TCHAR* const PatchVersion = TEXT("ueak.blueprint-patch/1.0");
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.blueprint-patch-preview/1.0");
	const TCHAR* const ApplySchemaVersion = TEXT("ueak.blueprint-patch-apply/1.0");
	const TCHAR* const KindSetPinDefault = TEXT("setPinDefault");
	const TCHAR* const KindCreateVariable = TEXT("createVariable");
	const TCHAR* const KindCreateNode = TEXT("createNode");
	/** Exhaustive list of migrated kinds, in the order they were migrated. */
	const TCHAR* const MigratedKindList =
		TEXT("setPinDefault, createVariable, createNode");
	const TCHAR* const StatusValidated = TEXT("validated");
	const TCHAR* const StatusApplied = TEXT("applied");
	const TCHAR* const StatusFailed = TEXT("failed");
	const TCHAR* const StatusNotAttempted = TEXT("notAttempted");

	/**
	 * Kinds that are designed but not migrated yet.  They are named here so the
	 * rejection can say "designed, not migrated" instead of "unknown", and so a
	 * caller cannot mistake a typo for a future feature.
	 */
	const TCHAR* const ReservedKinds[] = {
		TEXT("connect"),
		TEXT("rewirePin"),
		TEXT("removeNode"),
	};

	/** One patch may not outgrow the single writers' own Apply bounds. */
	constexpr int32 MaxCreateVariableOperations =
		UEAgentKitBlueprintVariableExperimental::MaxVariablesPerApply;
	constexpr int32 MaxCreateNodeOperations =
		UEAgentKitBlueprintGraphEditExperimental::MaxNodesPerApply;

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

#if WITH_DEV_AUTOMATION_TESTS
	bool bFaultArmed = false;
	int32 FaultOperationIndex = INDEX_NONE;
#endif

	struct FPatchOperation
	{
		FString OpId;
		FString Kind;
		/** Kind-specific resolved operation; only one of them is ever set. */
		UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation PinDefault;
		UEAgentKitBlueprintVariableExperimental::FAtomicVariableOperation Variable;
		UEAgentKitBlueprintGraphEditExperimental::FAtomicNodeOperation Node;
		/** opId/kind/resolved fields; reused verbatim for the results array. */
		TSharedPtr<FJsonObject> Detail;
	};

	struct FPatchPlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString PackageRevision;
		FString GraphFingerprint;
		/** Graph-level (pin default) state, the slice 8 fingerprint. */
		FString StateFingerprintBefore;
		FString StateFingerprintAfter;
		/** Blueprint-level variable state, the variable writer's own fingerprint. */
		FString VariablesFingerprintBefore;
		FString VariablesFingerprintAfter;
		/** Graph-level node structure, the graph-edit writer's own fingerprint. */
		FString NodesFingerprintBefore;
		FString NodesFingerprintAfter;
		FString PlanId;
		TArray<FPatchOperation> Operations;
		int32 NodeCountBefore = 0;
		int32 LinkCountBefore = 0;
		int32 VariableCountBefore = 0;
		int32 VariableCountAfter = 0;
		/** First operation that failed during the build, or INDEX_NONE. */
		int32 FailedIndex = INDEX_NONE;
	};

	struct FPatchReceipt
	{
		FString AssetPath;
		FString GraphGuid;
		FString GraphFingerprint;
		FString StateFingerprint;
		FString VariablesFingerprint;
		FString NodesFingerprint;
		int32 NodeCount = 0;
		int32 LinkCount = 0;
		int32 VariableCount = 0;
		int32 OperationCount = 0;
	};

	TMap<FString, FPatchReceipt> RecentPatchApplies;

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
				TEXT("graph-patch-invalid-parameters"),
				TEXT("Patch parameters must be one JSON object."));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			if (!Allowed.Contains(Pair.Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-unknown-parameter"),
					FString::Printf(TEXT("Unknown patch parameter: %s"), *Pair.Key));
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
				TEXT("graph-patch-invalid-parameters"),
				FString::Printf(
					TEXT("%s must be a%s string."),
					Key,
					bAllowEmpty ? TEXT("") : TEXT(" non-empty")));
		}
		return true;
	}

	bool IsCanonicalGuid(const FString& Value, FGuid& OutGuid)
	{
		if (Value.Len() != 36 || !FGuid::Parse(Value, OutGuid))
		{
			return false;
		}
		return OutGuid.ToString(EGuidFormats::DigitsWithHyphensLower).Equals(
			Value,
			ESearchCase::CaseSensitive);
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

	FString AppendField(const FString& Builder, const FString& Field)
	{
		return Builder + FString::Printf(TEXT("%d:"), Field.Len()) + Field;
	}

	/**
	 * Integer coordinate with the graph-edit writer's own bound and error code.
	 *
	 * The bound comes from the writer's header, so a patch cannot accept a
	 * coordinate the single-writer tool would refuse.
	 */
	bool ReadCoordinate(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Key,
		int32& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		constexpr int32 Bound =
			UEAgentKitBlueprintGraphEditExperimental::MaxNodeCoordinate;
		double Number = 0.0;
		if (!Object.IsValid() || !Object->TryGetNumberField(Key, Number))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-parameters"),
				FString::Printf(
					TEXT("%s must be an integer between -%d and %d."),
					Key,
					Bound,
					Bound));
		}
		const int32 Rounded = FMath::RoundToInt(Number);
		if (FMath::Abs(Number - static_cast<double>(Rounded)) > 0.0001
			|| Rounded < -Bound
			|| Rounded > Bound)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-parameters"),
				FString::Printf(
					TEXT("%s must be an integer between -%d and %d."),
					Key,
					Bound,
					Bound));
		}
		OutValue = Rounded;
		return true;
	}

	int32 CountLinks(UEdGraph* Graph)
	{
		int32 Count = 0;
		if (Graph == nullptr)
		{
			return Count;
		}
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

	bool IsMigratedKind(const FString& Kind)
	{
		return Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive)
			|| Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive)
			|| Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive);
	}

	bool IsReservedKind(const FString& Kind)
	{
		for (const TCHAR* Reserved : ReservedKinds)
		{
			if (Kind.Equals(Reserved, ESearchCase::CaseSensitive))
			{
				return true;
			}
		}
		return false;
	}

	TSharedPtr<FJsonObject> MakeOperationDetail(const FString& OpId, const FString& Kind)
	{
		TSharedRef<FJsonObject> Detail = MakeShared<FJsonObject>();
		Detail->SetStringField(TEXT("opId"), OpId);
		Detail->SetStringField(TEXT("kind"), Kind);
		return Detail;
	}

	/**
	 * One result row per operation.  Operations before the failure point carry
	 * `SuccessStatus` (`validated` for a plan build, `applied` for a write), the
	 * failing one carries `failed` and its exact code, and the rest are honestly
	 * `notAttempted` instead of being dropped.
	 */
	TArray<TSharedPtr<FJsonValue>> BuildOperationResults(
		const TArray<FPatchOperation>& Operations,
		const int32 FailedIndex,
		const FString& FailureCode,
		const FString& FailureMessage,
		const FString& SuccessStatus,
		const bool bRolledBack)
	{
		TArray<TSharedPtr<FJsonValue>> Results;
		Results.Reserve(Operations.Num());
		for (int32 Index = 0; Index < Operations.Num(); ++Index)
		{
			const FPatchOperation& Operation = Operations[Index];
			TSharedRef<FJsonObject> Item = Operation.Detail.IsValid()
				? MakeShared<FJsonObject>(*Operation.Detail)
				: MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("opId"), Operation.OpId);
			Item->SetStringField(TEXT("kind"), Operation.Kind);
			if (FailedIndex != INDEX_NONE && Index == FailedIndex)
			{
				Item->SetStringField(TEXT("status"), StatusFailed);
				Item->SetStringField(TEXT("errorCode"), FailureCode);
				Item->SetStringField(TEXT("errorMessage"), FailureMessage);
				Item->SetBoolField(TEXT("rolledBack"), false);
			}
			else if (FailedIndex != INDEX_NONE && Index > FailedIndex)
			{
				Item->SetStringField(TEXT("status"), StatusNotAttempted);
				Item->SetBoolField(TEXT("rolledBack"), false);
			}
			else
			{
				Item->SetStringField(TEXT("status"), SuccessStatus);
				Item->SetBoolField(TEXT("rolledBack"), bRolledBack);
			}
			Results.Add(MakeShared<FJsonValueObject>(Item));
		}
		return Results;
	}

	TSharedPtr<FJsonObject> BuildInactiveResult(
		const TCHAR* SchemaVersion,
		const FPatchPlan& Plan,
		const TArray<TSharedPtr<FJsonValue>>& Results,
		const FString& SuccessStatus,
		const bool bTransactionOpened,
		const bool bRolledBack)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("schemaVersion"), SchemaVersion);
		Result->SetBoolField(TEXT("experimental"), true);
		Result->SetBoolField(TEXT("applied"), false);
		Result->SetBoolField(TEXT("writeOccurred"), false);
		Result->SetBoolField(TEXT("transactionOpened"), bTransactionOpened);
		Result->SetBoolField(TEXT("rolledBack"), bRolledBack);
		Result->SetStringField(TEXT("assetPath"), Plan.AssetPath);
		Result->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
		if (!Plan.PlanId.IsEmpty())
		{
			Result->SetStringField(TEXT("planId"), Plan.PlanId);
		}
		Result->SetNumberField(TEXT("operationCount"), Plan.Operations.Num());
		Result->SetStringField(TEXT("successStatus"), SuccessStatus);
		Result->SetArrayField(TEXT("results"), Results);
		return Result;
	}

	/**
	 * Parse `operations[]` into a partially filled list.  The failing operation is
	 * still appended (with its opId/kind when they could be read) so the caller
	 * can report a status for it; OutFailedIndex says where validation stopped.
	 */
	bool ParseOperations(
		const TSharedPtr<FJsonObject>& Params,
		TArray<FPatchOperation>& OutOperations,
		int32& OutFailedIndex,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutFailedIndex = INDEX_NONE;
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Params->TryGetArrayField(TEXT("operations"), Values)
			|| Values == nullptr
			|| Values->IsEmpty()
			|| Values->Num() > MaxOperations)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-invalid-operations"),
				FString::Printf(
					TEXT("operations must contain between 1 and %d objects."),
					MaxOperations));
		}

		TSet<FString> SeenOpIds;
		TSet<FString> SeenTargets;
		TSet<FString> SeenVariableNames;
		int32 VariableOperationCount = 0;
		int32 NodeOperationCount = 0;
		for (int32 Index = 0; Index < Values->Num(); ++Index)
		{
			OutOperations.Add(FPatchOperation());
			FPatchOperation& Operation = OutOperations.Last();
			OutFailedIndex = Index;

			const TSharedPtr<FJsonObject>* Object = nullptr;
			if (!(*Values)[Index].IsValid()
				|| !(*Values)[Index]->TryGetObject(Object)
				|| Object == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-invalid-operations"),
					FString::Printf(TEXT("operations[%d] must be an object."), Index));
			}
			if (!ReadString(
				*Object,
				TEXT("opId"),
				Operation.OpId,
				OutErrorCode,
				OutErrorMessage)
				|| !ReadString(
					*Object,
					TEXT("kind"),
					Operation.Kind,
					OutErrorCode,
					OutErrorMessage))
			{
				Operation.Detail = MakeOperationDetail(Operation.OpId, Operation.Kind);
				return false;
			}
			Operation.Detail = MakeOperationDetail(Operation.OpId, Operation.Kind);
			if (Operation.OpId.Len() > MaxOpIdLength)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-invalid-parameters"),
					FString::Printf(
						TEXT("opId must be at most %d characters."),
						MaxOpIdLength));
			}
			for (const TCHAR Character : Operation.OpId)
			{
				if (Character < 0x21 || Character > 0x7E)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-invalid-parameters"),
						FString::Printf(
							TEXT("operations[%d].opId must be printable ASCII without spaces."),
							Index));
				}
			}
			if (SeenOpIds.Contains(Operation.OpId))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-duplicate-op-id"),
					FString::Printf(TEXT("Duplicate opId: %s"), *Operation.OpId));
			}
			SeenOpIds.Add(Operation.OpId);

			if (!IsMigratedKind(Operation.Kind))
			{
				if (IsReservedKind(Operation.Kind))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-kind-not-migrated"),
						FString::Printf(
							TEXT("Operation kind '%s' is designed but not migrated to the atomic patch yet; this build accepts: %s."),
							*Operation.Kind,
							MigratedKindList));
				}
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-kind-not-allowed"),
					FString::Printf(
						TEXT("Operation kind '%s' is not an atomic patch kind."),
						*Operation.Kind));
			}

			if (Operation.Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive))
			{
				// setPinDefault carries exactly the pin-default fields; anything else
				// is a typo that must not be silently ignored.
				if (!RejectUnknownParams(
					*Object,
					{
						TEXT("opId"),
						TEXT("kind"),
						TEXT("nodeGuid"),
						TEXT("pinName"),
						TEXT("pinGuid"),
						TEXT("type"),
						TEXT("value"),
					},
					OutErrorCode,
					OutErrorMessage))
				{
					return false;
				}
				FString NodeGuid;
				FString PinName;
				FString PinGuid;
				FString Type;
				FString Value;
				FGuid NodeGuidValue;
				FGuid PinGuidValue;
				if (!ReadString(*Object, TEXT("nodeGuid"), NodeGuid, OutErrorCode, OutErrorMessage)
					|| !ReadString(*Object, TEXT("pinName"), PinName, OutErrorCode, OutErrorMessage)
					|| !ReadString(*Object, TEXT("pinGuid"), PinGuid, OutErrorCode, OutErrorMessage)
					|| !ReadString(*Object, TEXT("type"), Type, OutErrorCode, OutErrorMessage))
				{
					return false;
				}
				if (!Object->Get()->TryGetStringField(TEXT("value"), Value))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-invalid-parameters"),
						TEXT("value must be a string, including an empty string for string defaults."));
				}
				if (!IsCanonicalGuid(NodeGuid, NodeGuidValue)
					|| !IsCanonicalGuid(PinGuid, PinGuidValue))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-pin-default-invalid-identifier"),
						TEXT("nodeGuid and pinGuid must be canonical lower-case GUIDs."));
				}
				Operation.PinDefault.NodeGuid = NodeGuid;
				Operation.PinDefault.PinName = PinName;
				Operation.PinDefault.PinGuid = PinGuid;
				Operation.PinDefault.Type = Type;
				Operation.PinDefault.Value = Value;
				// Two operations writing one pin would make the read-back of the first
				// one fail by construction, so the ambiguity is refused up front.
				const FString TargetKey = NodeGuid + TEXT("|") + PinName + TEXT("|") + PinGuid;
				if (SeenTargets.Contains(TargetKey))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-duplicate-target"),
						FString::Printf(TEXT("Duplicate pin default target: %s"), *TargetKey));
				}
				SeenTargets.Add(TargetKey);
			}
			else if (Operation.Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive))
			{
				// createVariable carries exactly the variable fields: the same three
				// the single writer accepts, and nothing else.
				if (!RejectUnknownParams(
					*Object,
					{
						TEXT("opId"),
						TEXT("kind"),
						TEXT("name"),
						TEXT("type"),
						TEXT("defaultValue"),
					},
					OutErrorCode,
					OutErrorMessage))
				{
					return false;
				}
				FString Name;
				FString Type;
				FString DefaultValue;
				if (!ReadString(*Object, TEXT("name"), Name, OutErrorCode, OutErrorMessage)
					|| !ReadString(*Object, TEXT("type"), Type, OutErrorCode, OutErrorMessage)
					|| !ReadString(
						*Object,
						TEXT("defaultValue"),
						DefaultValue,
						OutErrorCode,
						OutErrorMessage,
						/*bAllowEmpty=*/true))
				{
					return false;
				}
				// A patch may not create more variables than the single writer may.
				++VariableOperationCount;
				if (VariableOperationCount > MaxCreateVariableOperations)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-create-variable-limit"),
						FString::Printf(
							TEXT("A patch may create at most %d variables, the same bound as the single-writer variable tool."),
							MaxCreateVariableOperations));
				}
				Operation.Variable.Name = Name;
				Operation.Variable.Type = Type;
				Operation.Variable.DefaultValue = DefaultValue;
				// FName semantics: variable names are unique case-insensitively, so two
				// operations that only differ in case would collide at write time.
				const FString NameKey = Name.ToLower();
				if (SeenVariableNames.Contains(NameKey))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-duplicate-target"),
						FString::Printf(TEXT("Duplicate variable name: %s"), *Name));
				}
				SeenVariableNames.Add(NameKey);
			}
			else if (Operation.Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive))
			{
				// createNode carries exactly nodeType/x/y: no tempId (nothing can
				// reference it inside this patch), no literal, no variable field.
				if (!RejectUnknownParams(
					*Object,
					{
						TEXT("opId"),
						TEXT("kind"),
						TEXT("nodeType"),
						TEXT("x"),
						TEXT("y"),
					},
					OutErrorCode,
					OutErrorMessage))
				{
					return false;
				}
				FString NodeType;
				int32 X = 0;
				int32 Y = 0;
				if (!ReadString(*Object, TEXT("nodeType"), NodeType, OutErrorCode, OutErrorMessage)
					|| !ReadCoordinate(*Object, TEXT("x"), X, OutErrorCode, OutErrorMessage)
					|| !ReadCoordinate(*Object, TEXT("y"), Y, OutErrorCode, OutErrorMessage))
				{
					return false;
				}
				// A patch may not create more nodes than the single writer may.
				++NodeOperationCount;
				if (NodeOperationCount > MaxCreateNodeOperations)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-create-node-limit"),
						FString::Printf(
							TEXT("A patch may create at most %d nodes, the same bound as the single-writer graph edit tool."),
							MaxCreateNodeOperations));
				}
				Operation.Node.NodeType = NodeType;
				Operation.Node.X = X;
				Operation.Node.Y = Y;
			}
			else
			{
				// Unreachable while IsMigratedKind and these branches agree; kept so a
				// future kind cannot silently fall into the variable field parser.
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-kind-not-migrated"),
					FString::Printf(
						TEXT("Operation kind '%s' has no atomic field parser."),
						*Operation.Kind));
			}
		}
		OutFailedIndex = INDEX_NONE;
		return true;
	}

	/** Resolve one parsed operation against the live Blueprint.  Read-only. */
	bool ResolveOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FPatchOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Operation.Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive))
		{
			if (!UEAgentKitBlueprintPinDefaultExperimental::TryResolveAtomicOperation(
				Graph,
				Operation.PinDefault,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			Operation.Detail->SetStringField(TEXT("nodeGuid"), Operation.PinDefault.NodeGuid);
			Operation.Detail->SetStringField(TEXT("pinName"), Operation.PinDefault.PinName);
			Operation.Detail->SetStringField(TEXT("pinGuid"), Operation.PinDefault.PinGuid);
			Operation.Detail->SetStringField(TEXT("type"), Operation.PinDefault.Type);
			Operation.Detail->SetStringField(TEXT("beforeValue"), Operation.PinDefault.BeforeValue);
			Operation.Detail->SetStringField(TEXT("afterValue"), Operation.PinDefault.Value);
			return true;
		}
		if (Operation.Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive))
		{
			// A variable lives on the Blueprint, not in a graph, so this resolution
			// deliberately receives the Blueprint only.
			if (!UEAgentKitBlueprintVariableExperimental::TryResolveAtomicVariableOperation(
				Blueprint,
				Operation.Variable,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			Operation.Detail->SetStringField(TEXT("name"), Operation.Variable.Name);
			Operation.Detail->SetStringField(TEXT("type"), Operation.Variable.Type);
			Operation.Detail->SetStringField(
				TEXT("defaultValue"),
				Operation.Variable.DefaultValue);
			Operation.Detail->SetBoolField(
				TEXT("existedBefore"),
				Operation.Variable.bExistedBefore);
			return true;
		}
		if (Operation.Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive))
		{
			if (!UEAgentKitBlueprintGraphEditExperimental::TryResolveAtomicNodeOperation(
				Graph,
				Operation.Node,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			Operation.Detail->SetStringField(TEXT("nodeType"), Operation.Node.NodeType);
			Operation.Detail->SetStringField(TEXT("nodeClass"), Operation.Node.NodeClassPath);
			if (!Operation.Node.FunctionName.IsEmpty())
			{
				Operation.Detail->SetStringField(TEXT("function"), Operation.Node.FunctionName);
			}
			Operation.Detail->SetNumberField(TEXT("x"), Operation.Node.X);
			Operation.Detail->SetNumberField(TEXT("y"), Operation.Node.Y);
			TArray<TSharedPtr<FJsonValue>> Inputs;
			for (const FString& PinName : Operation.Node.InputPins)
			{
				Inputs.Add(MakeShared<FJsonValueString>(PinName));
			}
			TArray<TSharedPtr<FJsonValue>> Outputs;
			for (const FString& PinName : Operation.Node.OutputPins)
			{
				Outputs.Add(MakeShared<FJsonValueString>(PinName));
			}
			TSharedRef<FJsonObject> InputTypes = MakeShared<FJsonObject>();
			for (const TPair<FString, FString>& Pair : Operation.Node.InputPinTypes)
			{
				InputTypes->SetStringField(Pair.Key, Pair.Value);
			}
			TSharedRef<FJsonObject> OutputTypes = MakeShared<FJsonObject>();
			for (const TPair<FString, FString>& Pair : Operation.Node.OutputPinTypes)
			{
				OutputTypes->SetStringField(Pair.Key, Pair.Value);
			}
			Operation.Detail->SetArrayField(TEXT("inputPins"), Inputs);
			Operation.Detail->SetArrayField(TEXT("outputPins"), Outputs);
			Operation.Detail->SetObjectField(TEXT("inputPinTypes"), InputTypes);
			Operation.Detail->SetObjectField(TEXT("outputPinTypes"), OutputTypes);
			return true;
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-kind-not-migrated"),
			FString::Printf(
				TEXT("Operation kind '%s' has no atomic implementation."),
				*Operation.Kind));
	}

	/** Apply one resolved operation inside the caller's transaction. */
	bool ApplyOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FPatchOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Operation.Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation> Single;
			Single.Add(Operation.PinDefault);
			int32 AppliedCount = 0;
			return UEAgentKitBlueprintPinDefaultExperimental::TryApplyAtomicOperations(
				Graph,
				Single,
				AppliedCount,
				OutErrorCode,
				OutErrorMessage);
		}
		if (Operation.Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintVariableExperimental::FAtomicVariableOperation> Single;
			Single.Add(Operation.Variable);
			TArray<FGuid> CreatedGuids;
			if (!UEAgentKitBlueprintVariableExperimental::TryApplyAtomicVariableOperations(
				Blueprint,
				Single,
				CreatedGuids,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			if (CreatedGuids.Num() == 1)
			{
				Operation.Detail->SetStringField(
					TEXT("guid"),
					CreatedGuids[0].ToString(EGuidFormats::DigitsWithHyphensLower));
			}
			return true;
		}
		if (Operation.Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintGraphEditExperimental::FAtomicNodeOperation> Single;
			Single.Add(Operation.Node);
			TArray<FGuid> CreatedGuids;
			if (!UEAgentKitBlueprintGraphEditExperimental::TryApplyAtomicNodeOperations(
				Blueprint,
				Graph,
				Single,
				CreatedGuids,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			if (CreatedGuids.Num() == 1)
			{
				// The read-back, the restore and the caller's next patch all use
				// this exact GUID; it is assigned inside the transaction and is
				// deliberately not part of the previewed plan hash.
				Operation.Node.NodeGuid =
					CreatedGuids[0].ToString(EGuidFormats::DigitsWithHyphensLower);
				Operation.Detail->SetStringField(TEXT("nodeGuid"), Operation.Node.NodeGuid);
			}
			return true;
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-kind-not-migrated"),
			FString::Printf(
				TEXT("Operation kind '%s' has no atomic implementation."),
				*Operation.Kind));
	}

	/** Read one applied operation back; bExpectAfter selects the expected state. */
	bool CheckOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FPatchOperation& Operation,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Operation.Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation> Single;
			Single.Add(Operation.PinDefault);
			return UEAgentKitBlueprintPinDefaultExperimental::TryCheckAtomicOperations(
				Graph,
				Single,
				bExpectAfter,
				OutErrorCode,
				OutErrorMessage);
		}
		if (Operation.Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintVariableExperimental::FAtomicVariableOperation> Single;
			Single.Add(Operation.Variable);
			return UEAgentKitBlueprintVariableExperimental::TryCheckAtomicVariableOperations(
				Blueprint,
				Single,
				bExpectAfter,
				OutErrorCode,
				OutErrorMessage);
		}
		if (Operation.Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintGraphEditExperimental::FAtomicNodeOperation> Single;
			Single.Add(Operation.Node);
			return UEAgentKitBlueprintGraphEditExperimental::TryCheckAtomicNodeOperations(
				Graph,
				Single,
				bExpectAfter,
				OutErrorCode,
				OutErrorMessage);
		}
		return true;
	}

	/** Put one applied operation back to its pre-patch state. */
	bool RestoreOperation(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FPatchOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Operation.Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation> Single;
			Single.Add(Operation.PinDefault);
			return UEAgentKitBlueprintPinDefaultExperimental::TryRestoreAtomicOperations(
				Graph,
				Single,
				1,
				OutErrorCode,
				OutErrorMessage);
		}
		if (Operation.Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintVariableExperimental::FAtomicVariableOperation> Single;
			Single.Add(Operation.Variable);
			return UEAgentKitBlueprintVariableExperimental::TryRestoreAtomicVariableOperations(
				Blueprint,
				Single,
				1,
				OutErrorCode,
				OutErrorMessage);
		}
		if (Operation.Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive))
		{
			TArray<UEAgentKitBlueprintGraphEditExperimental::FAtomicNodeOperation> Single;
			Single.Add(Operation.Node);
			return UEAgentKitBlueprintGraphEditExperimental::TryRestoreAtomicNodeOperations(
				Blueprint,
				Graph,
				Single,
				1,
				OutErrorCode,
				OutErrorMessage);
		}
		return true;
	}

	FString ComputeOperationCanonical(const FPatchOperation& Operation)
	{
		FString Canonical;
		Canonical = AppendField(Canonical, Operation.OpId);
		Canonical = AppendField(Canonical, Operation.Kind);
		if (Operation.Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive))
		{
			Canonical = AppendField(Canonical, Operation.PinDefault.NodeGuid);
			Canonical = AppendField(Canonical, Operation.PinDefault.PinName);
			Canonical = AppendField(Canonical, Operation.PinDefault.PinGuid);
			Canonical = AppendField(Canonical, Operation.PinDefault.Type);
			Canonical = AppendField(Canonical, Operation.PinDefault.BeforeValue);
			Canonical = AppendField(Canonical, Operation.PinDefault.Value);
		}
		else if (Operation.Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive))
		{
			// The created GUID is deliberately absent: it is assigned inside the
			// transaction, so it cannot be part of a plan the caller previewed.
			Canonical = AppendField(Canonical, Operation.Variable.Name);
			Canonical = AppendField(Canonical, Operation.Variable.Type);
			Canonical = AppendField(Canonical, Operation.Variable.DefaultValue);
		}
		else if (Operation.Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive))
		{
			// The created node GUID is deliberately absent: it is assigned inside
			// the transaction, so it cannot be part of a plan the caller previewed.
			Canonical = AppendField(Canonical, Operation.Node.NodeType);
			Canonical = AppendField(Canonical, Operation.Node.NodeClassPath);
			Canonical = AppendField(Canonical, Operation.Node.FunctionName);
			Canonical = AppendField(Canonical, FString::FromInt(Operation.Node.X));
			Canonical = AppendField(Canonical, FString::FromInt(Operation.Node.Y));
		}
		return Canonical;
	}

	/** Read every operation of one plan back; used after Apply, Undo and Redo. */
	bool CheckAllOperations(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FPatchPlan& Plan,
		bool bExpectAfter,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		for (const FPatchOperation& Operation : Plan.Operations)
		{
			if (!CheckOperation(
				Blueprint,
				Graph,
				Operation,
				bExpectAfter,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
		}
		return true;
	}

	/**
	 * Parse parameters, prove the focused graph and the clean package, resolve
	 * every operation against the live graph and derive the plan hash.
	 *
	 * On failure OutPlan.Operations still holds every parsed operation (with its
	 * resolved detail where resolution got that far) so the caller can report a
	 * per-operation status list instead of a bare error.
	 */
	bool BuildPlan(
		const TSharedPtr<FJsonObject>& Params,
		FPatchPlan& OutPlan,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FString AssetPath;
		FString GraphGuid;
		if (!ReadString(Params, TEXT("assetPath"), AssetPath, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("graphGuid"), GraphGuid, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (AssetPath.Len() > MaxAssetPath)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-invalid-parameters"),
				FString::Printf(TEXT("assetPath must be at most %d characters."), MaxAssetPath));
		}
		FGuid GraphGuidValue;
		if (!IsCanonicalGuid(GraphGuid, GraphGuidValue))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-invalid-identifier"),
				TEXT("graphGuid must be one canonical lower-case GUID."));
		}
		OutPlan.AssetPath = AssetPath;
		OutPlan.GraphGuid = GraphGuid;

		bool bParsed = ParseOperations(
			Params,
			OutPlan.Operations,
			OutPlan.FailedIndex,
			OutErrorCode,
			OutErrorMessage);
		if (!bParsed)
		{
			return false;
		}

		if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-pie-active"),
				TEXT("A Blueprint patch is unavailable while PIE or SIE is active."));
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
			|| !Preflight->GetStringField(TEXT("graphGuid")).Equals(GraphGuid, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-focused-graph-mismatch"),
				TEXT("The focused Blueprint editor is not showing the exact assetPath/graphGuid from the request."));
		}
		if (Preflight->GetBoolField(TEXT("packageDirty")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-package-dirty"),
				TEXT("A Blueprint patch requires a clean package before Apply."));
		}
		if (!Preflight->GetBoolField(TEXT("editable")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-graph-not-editable"),
				TEXT("The focused graph is not editable."));
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
		if (Graph == nullptr || Blueprint == nullptr || Graph->GetSchema() == nullptr
			|| !Graph->GetSchema()->IsA<UEdGraphSchema_K2>())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-schema-not-supported"),
				TEXT("Blueprint patches support only K2 Blueprint graphs."));
		}

		OutPlan.PackageRevision = Preflight->GetStringField(TEXT("packageRevision"));
		OutPlan.GraphFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
		// A variable is asset-level: creating one on a Blueprint that has never been
		// saved would have no on-disk revision to bind, which is exactly what the
		// single-writer variable tool refuses too.  Only patches that create
		// variables pay this precondition.
		bool bCreatesVariables = false;
		for (const FPatchOperation& Operation : OutPlan.Operations)
		{
			if (Operation.Kind.Equals(KindCreateVariable, ESearchCase::CaseSensitive))
			{
				bCreatesVariables = true;
				break;
			}
		}
		if (bCreatesVariables && !IsSha256(OutPlan.PackageRevision))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("blueprint-variable-revision-unavailable"),
				TEXT("The exact Blueprint package needs a sha256 disk revision; save it to disk first."));
		}
		OutPlan.StateFingerprintBefore =
			UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
		if (OutPlan.StateFingerprintBefore.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-fingerprint-failed"),
				TEXT("Could not fingerprint the current graph state."));
		}
		OutPlan.VariablesFingerprintBefore =
			UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(Blueprint);
		if (OutPlan.VariablesFingerprintBefore.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-fingerprint-failed"),
				TEXT("Could not fingerprint the current Blueprint variable state."));
		}
		// The graph-level pin-default hash covers node identity and pins but not
		// the node class or the node position, so a createNode patch also binds
		// the graph-edit writer's own node-structure hash.
		OutPlan.NodesFingerprintBefore =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		if (OutPlan.NodesFingerprintBefore.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-fingerprint-failed"),
				TEXT("Could not fingerprint the current graph node structure."));
		}
		OutPlan.NodeCountBefore = Graph->Nodes.Num();
		OutPlan.LinkCountBefore = CountLinks(Graph);
		OutPlan.VariableCountBefore = Blueprint->NewVariables.Num();

		for (int32 Index = 0; Index < OutPlan.Operations.Num(); ++Index)
		{
			if (!ResolveOperation(
				Blueprint,
				Graph,
				OutPlan.Operations[Index],
				OutErrorCode,
				OutErrorMessage))
			{
				OutPlan.FailedIndex = Index;
				return false;
			}
		}

		FString Canonical;
		Canonical = AppendField(Canonical, PatchVersion);
		Canonical = AppendField(Canonical, OutPlan.AssetPath);
		Canonical = AppendField(Canonical, OutPlan.GraphGuid);
		Canonical = AppendField(Canonical, OutPlan.PackageRevision);
		Canonical = AppendField(Canonical, OutPlan.GraphFingerprint);
		Canonical = AppendField(Canonical, OutPlan.StateFingerprintBefore);
		Canonical = AppendField(Canonical, OutPlan.VariablesFingerprintBefore);
		Canonical = AppendField(Canonical, OutPlan.NodesFingerprintBefore);
		for (const FPatchOperation& Operation : OutPlan.Operations)
		{
			Canonical += ComputeOperationCanonical(Operation);
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-plan-hash-failed"),
				TEXT("Could not compute the patch plan hash."));
		}
		OutPlan.PlanId = FString(TEXT("sha256:")) + Digest;
		return true;
	}

	TArray<TSharedPtr<FJsonValue>> BuildValidatedResults(const FPatchPlan& Plan)
	{
		TArray<TSharedPtr<FJsonValue>> Results;
		Results.Reserve(Plan.Operations.Num());
		for (const FPatchOperation& Operation : Plan.Operations)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>(*Operation.Detail);
			Item->SetStringField(TEXT("status"), StatusValidated);
			Results.Add(MakeShared<FJsonValueObject>(Item));
		}
		return Results;
	}

	TSharedPtr<FJsonObject> BuildPreviewResult(const FPatchPlan& Plan)
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
		Result->SetStringField(TEXT("stateFingerprint"), Plan.StateFingerprintBefore);
		Result->SetStringField(TEXT("variablesFingerprint"), Plan.VariablesFingerprintBefore);
		Result->SetStringField(TEXT("nodesFingerprint"), Plan.NodesFingerprintBefore);
		Result->SetNumberField(TEXT("operationCount"), Plan.Operations.Num());
		Result->SetNumberField(TEXT("nodeCount"), Plan.NodeCountBefore);
		Result->SetNumberField(TEXT("linkCount"), Plan.LinkCountBefore);
		Result->SetNumberField(TEXT("variableCount"), Plan.VariableCountBefore);
		Result->SetStringField(
			TEXT("requiredConfirmation"),
			FString(UEAgentKitBlueprintPatchExperimental::ApplyConfirmationPrefix) + Plan.PlanId);
		Result->SetArrayField(
			TEXT("migratedKinds"),
			{
				MakeShared<FJsonValueString>(KindSetPinDefault),
				MakeShared<FJsonValueString>(KindCreateVariable),
				MakeShared<FJsonValueString>(KindCreateNode),
			});
		Result->SetArrayField(TEXT("operations"), BuildValidatedResults(Plan));
		return Result;
	}
}

bool UEAgentKitBlueprintPatchExperimental::IsExperimentalWriteAvailable()
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

void UEAgentKitBlueprintPatchExperimental::SetExperimentalWriteEnabledForTest(bool bEnabled)
{
	bTestOverrideSet = true;
	bTestOverrideEnabled = bEnabled;
}

void UEAgentKitBlueprintPatchExperimental::ResetExperimentalWriteForTest()
{
	bTestOverrideSet = false;
	bTestOverrideEnabled = false;
}

#if WITH_DEV_AUTOMATION_TESTS
void UEAgentKitBlueprintPatchExperimental::SetNextApplyFaultForTest(int32 OperationIndex)
{
	bFaultArmed = true;
	FaultOperationIndex = OperationIndex;
}
#endif

bool UEAgentKitBlueprintPatchExperimental::TryPreviewExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!RejectUnknownParams(
		Params,
		{ TEXT("assetPath"), TEXT("graphGuid"), TEXT("operations") },
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
			TEXT("graph-patch-experimental-disabled"),
			TEXT("Experimental atomic Blueprint patching is not opted in for this project."));
	}
	FPatchPlan Plan;
	if (!BuildPlan(Params, Plan, OutErrorCode, OutErrorMessage))
	{
		if (!Plan.Operations.IsEmpty())
		{
			OutResult = BuildInactiveResult(
				PreviewSchemaVersion,
				Plan,
				BuildOperationResults(
					Plan.Operations,
					Plan.FailedIndex,
					OutErrorCode,
					OutErrorMessage,
					StatusValidated,
					/*bRolledBack=*/false),
				StatusValidated,
				/*bTransactionOpened=*/false,
				/*bRolledBack=*/false);
		}
		return false;
	}
	OutResult = BuildPreviewResult(Plan);
	return true;
}

bool UEAgentKitBlueprintPatchExperimental::TryApplyExperimental(
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
			TEXT("operations"),
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
			TEXT("graph-patch-experimental-disabled"),
			TEXT("Experimental atomic Blueprint patching is not opted in for this project."));
	}
	if (GEditor == nullptr || GEditor->Trans == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-transaction-unavailable"),
			TEXT("The Editor transaction history is unavailable."));
	}
	if (!GEditor->CanTransact())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-transaction-unavailable"),
			TEXT("The Editor cannot begin a transaction in its current loading/post-load state."));
	}
	if (GEditor->Trans->IsActive())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-transaction-active"),
			TEXT("Another Editor transaction is already active; the patch must be its own Undo/Redo unit."));
	}
	FString RequestedPlanId;
	FString Confirmation;
	if (!ReadString(Params, TEXT("planId"), RequestedPlanId, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	FPatchPlan Plan;
	if (!BuildPlan(Params, Plan, OutErrorCode, OutErrorMessage))
	{
		if (!Plan.Operations.IsEmpty())
		{
			OutResult = BuildInactiveResult(
				ApplySchemaVersion,
				Plan,
				BuildOperationResults(
					Plan.Operations,
					Plan.FailedIndex,
					OutErrorCode,
					OutErrorMessage,
					StatusValidated,
					/*bRolledBack=*/false),
				StatusValidated,
				/*bTransactionOpened=*/false,
				/*bRolledBack=*/false);
		}
		return false;
	}
	if (!RequestedPlanId.Equals(Plan.PlanId, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-plan-stale"),
			FString::Printf(
				TEXT("The graph or request changed after Preview; request a fresh preview. requested=%s current=%s"),
				*RequestedPlanId,
				*Plan.PlanId));
	}
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintPatchExperimental::ApplyConfirmationPrefix) + Plan.PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-confirmation-required"),
			FString::Printf(
				TEXT("Explicit Apply confirmation must be exactly %s."),
				*ExpectedConfirmation));
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
	UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
	if (Package == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-package-unavailable"),
			TEXT("The exact Blueprint has no package."));
	}

	const bool bPackageDirtyBefore = Package->IsDirty();
	const TEnumAsByte<EBlueprintStatus> BlueprintStatusBefore = Blueprint->Status;
	const bool bCachedDependenciesBefore = Blueprint->bCachedDependenciesUpToDate;

	TArray<TSharedPtr<FJsonValue>> AppliedResults;
	{
		FScopedTransaction Transaction(
			FText::FromString(TEXT("UEAgentKit Apply Blueprint Patch")));
		if (!Transaction.IsOutstanding())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-transaction-unavailable"),
				TEXT("The Editor did not open an Undo transaction; nothing was written."));
		}
		Blueprint->Modify();
		Graph->Modify();

		int32 AppliedCount = 0;
		int32 FailedIndex = INDEX_NONE;
		auto RestoreAllAndCancel = [&]()
		{
			for (int32 Index = AppliedCount - 1; Index >= 0; --Index)
			{
				FString RestoreCode;
				FString RestoreMessage;
				if (!RestoreOperation(
						Blueprint,
						Graph,
						Plan.Operations[Index],
						RestoreCode,
						RestoreMessage))
				{
					// A pin that can no longer be resolved, or a variable that
					// survived its own removal, cannot be restored by this layer;
					// report it instead of claiming a clean rollback.
					OutErrorCode = RestoreCode;
					OutErrorMessage = FString::Printf(
						TEXT("%s The patch also failed to restore operation %s."),
						*RestoreMessage,
						*Plan.Operations[Index].OpId);
				}
			}
			Blueprint->Status = BlueprintStatusBefore;
			Blueprint->bCachedDependenciesUpToDate = bCachedDependenciesBefore;
			Package->SetDirtyFlag(bPackageDirtyBefore);
			Transaction.Cancel();
		};

		/**
		 * Prove the rollback actually returned to the previewed before state.
		 *
		 * The per-writer restore functions already report their own failure; this
		 * is the whole-state check on top of it (counts plus all three shared
		 * fingerprints), so a failed Apply cannot report rolledBack=true while the
		 * graph is still changed.
		 */
		auto IsBeforeStateRestored = [&]() -> bool
		{
			return Graph->Nodes.Num() == Plan.NodeCountBefore
				&& CountLinks(Graph) == Plan.LinkCountBefore
				&& UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
					.Equals(Plan.StateFingerprintBefore, ESearchCase::CaseSensitive)
				&& UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(
					Blueprint)
					.Equals(Plan.VariablesFingerprintBefore, ESearchCase::CaseSensitive)
				&& UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph)
					.Equals(Plan.NodesFingerprintBefore, ESearchCase::CaseSensitive);
		};

		bool bApplied = true;
		for (int32 Index = 0; Index < Plan.Operations.Num(); ++Index)
		{
#if WITH_DEV_AUTOMATION_TESTS
			if (bFaultArmed && FaultOperationIndex == Index)
			{
				bFaultArmed = false;
				FaultOperationIndex = INDEX_NONE;
				Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-apply-fault-injected"),
					FString::Printf(
						TEXT("Injected test fault on operation %s."),
						*Plan.Operations[Index].OpId));
				AppliedCount = Index;
				FailedIndex = Index;
				bApplied = false;
				break;
			}
#endif
			if (!ApplyOperation(
				Blueprint,
				Graph,
				Plan.Operations[Index],
				OutErrorCode,
				OutErrorMessage))
			{
				AppliedCount = Index;
				FailedIndex = Index;
				bApplied = false;
				break;
			}
			++AppliedCount;
		}

		if (!bApplied)
		{
			RestoreAllAndCancel();
			const bool bStateRestored = IsBeforeStateRestored();
			if (!bStateRestored)
			{
				Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-restore-mismatch"),
					TEXT("The rollback did not restore the exact pre-patch graph state."));
			}
			OutResult = BuildInactiveResult(
				ApplySchemaVersion,
				Plan,
				BuildOperationResults(
					Plan.Operations,
					FailedIndex,
					OutErrorCode,
					OutErrorMessage,
					StatusApplied,
					bStateRestored),
				StatusApplied,
				/*bTransactionOpened=*/true,
				bStateRestored);
			OutResult->SetNumberField(TEXT("appliedOperationCount"), 0);
			OutResult->SetBoolField(TEXT("stateRestored"), bStateRestored);
			return false;
		}

		// Every operation must read back through its own writer before the
		// transaction is allowed to commit.
		for (int32 Index = 0; Index < Plan.Operations.Num(); ++Index)
		{
			if (!CheckOperation(
				Blueprint,
				Graph,
				Plan.Operations[Index],
				/*bExpectAfter=*/true,
				OutErrorCode,
				OutErrorMessage))
			{
				RestoreAllAndCancel();
				const bool bStateRestored = IsBeforeStateRestored();
				if (!bStateRestored)
				{
					Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-patch-restore-mismatch"),
						TEXT("The rollback did not restore the exact pre-patch graph state."));
				}
				OutResult = BuildInactiveResult(
					ApplySchemaVersion,
					Plan,
					BuildOperationResults(
						Plan.Operations,
						Index,
						OutErrorCode,
						OutErrorMessage,
						StatusApplied,
						bStateRestored),
					StatusApplied,
					/*bTransactionOpened=*/true,
					bStateRestored);
				OutResult->SetNumberField(TEXT("appliedOperationCount"), 0);
				OutResult->SetBoolField(TEXT("stateRestored"), bStateRestored);
				return false;
			}
		}

		Package->MarkPackageDirty();
		if (!Package->IsDirty())
		{
			RestoreAllAndCancel();
			const bool bStateRestored = IsBeforeStateRestored();
			OutResult = BuildInactiveResult(
				ApplySchemaVersion,
				Plan,
				BuildOperationResults(
					Plan.Operations,
					INDEX_NONE,
					FString(),
					FString(),
					StatusApplied,
					bStateRestored),
				StatusApplied,
				/*bTransactionOpened=*/true,
				bStateRestored);
			OutResult->SetBoolField(TEXT("stateRestored"), bStateRestored);
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-dirty-not-confirmed"),
				TEXT("The Editor did not confirm the Blueprint package became dirty; the whole patch was restored and its transaction cancelled."));
		}
		Plan.StateFingerprintAfter =
			UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
		Plan.VariablesFingerprintAfter =
			UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(Blueprint);
		Plan.NodesFingerprintAfter =
			UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph);
		if (Plan.NodesFingerprintAfter.IsEmpty())
		{
			RestoreAllAndCancel();
			const bool bStateRestored = IsBeforeStateRestored();
			OutResult = BuildInactiveResult(
				ApplySchemaVersion,
				Plan,
				BuildOperationResults(
					Plan.Operations,
					INDEX_NONE,
					FString(),
					FString(),
					StatusApplied,
					bStateRestored),
				StatusApplied,
				/*bTransactionOpened=*/true,
				bStateRestored);
			OutResult->SetBoolField(TEXT("stateRestored"), bStateRestored);
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-fingerprint-failed"),
				TEXT("Could not fingerprint the graph node structure after Apply; the whole patch was restored and its transaction cancelled."));
		}
		Plan.VariableCountAfter = Blueprint->NewVariables.Num();
		AppliedResults = BuildOperationResults(
			Plan.Operations,
			INDEX_NONE,
			FString(),
			FString(),
			StatusApplied,
			/*bRolledBack=*/false);
	}

	// One transaction must be one Undo/Redo unit that reproduces both the exact
	// before state and the exact after state.
	const int32 NodeCountAfter = Graph->Nodes.Num();
	const int32 LinkCountAfter = CountLinks(Graph);
	int32 CreatedNodeCount = 0;
	for (const FPatchOperation& Operation : Plan.Operations)
	{
		if (Operation.Kind.Equals(KindCreateNode, ESearchCase::CaseSensitive))
		{
			++CreatedNodeCount;
		}
	}
	// createNode is the only migrated kind that changes the graph structure, and it
	// must never change an edge: the delta is exactly the number of node operations.
	if (NodeCountAfter != Plan.NodeCountBefore + CreatedNodeCount
		|| LinkCountAfter != Plan.LinkCountBefore)
	{
		if (GEditor->Trans != nullptr)
		{
			GEditor->UndoTransaction();
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-readback-failed"),
			TEXT("The graph node/link counts do not match the plan after Apply."));
	}
	bool bUndoVerified = false;
	bool bRedoVerified = false;
	{
		bool bBeforeStateVerified = false;
		if (GEditor->UndoTransaction())
		{
			// Undo must put every kind back to its before state; each kind is read
			// back through its own writer, and the graph-level, Blueprint-level and
			// node-structure fingerprints must all return to the previewed values.
			bBeforeStateVerified =
				CheckAllOperations(
					Blueprint,
					Graph,
					Plan,
					/*bExpectAfter=*/false,
					OutErrorCode,
					OutErrorMessage)
				&& UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
					.Equals(Plan.StateFingerprintBefore, ESearchCase::CaseSensitive)
				&& UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(
					Blueprint)
					.Equals(Plan.VariablesFingerprintBefore, ESearchCase::CaseSensitive)
				&& UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph)
					.Equals(Plan.NodesFingerprintBefore, ESearchCase::CaseSensitive);
		}
		bUndoVerified = bBeforeStateVerified;
		bool bAfterStateVerified = false;
		if (bUndoVerified && GEditor->RedoTransaction())
		{
			bAfterStateVerified =
				CheckAllOperations(
					Blueprint,
					Graph,
					Plan,
					/*bExpectAfter=*/true,
					OutErrorCode,
					OutErrorMessage)
				&& UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
					.Equals(Plan.StateFingerprintAfter, ESearchCase::CaseSensitive)
				&& UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(
					Blueprint)
					.Equals(Plan.VariablesFingerprintAfter, ESearchCase::CaseSensitive)
				&& UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph)
					.Equals(Plan.NodesFingerprintAfter, ESearchCase::CaseSensitive);
		}
		bRedoVerified = bAfterStateVerified;
	}
	if (!bUndoVerified || !bRedoVerified)
	{
		if (GEditor->Trans != nullptr)
		{
			GEditor->UndoTransaction();
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-undo-redo-failed"),
			TEXT("Undo/Redo verification failed for the Blueprint patch."));
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
			TEXT("graph-patch-readback-failed"),
			TEXT("Final package dirty check failed for the Blueprint patch."));
	}
	// The layout graph fingerprint includes the node set and node positions, so a
	// structural patch (createNode) must report the live post-Apply value instead
	// of the previewed one.  Without this, Save's "is this still the dirty
	// post-Apply graph" check would reject every createNode patch.
	const FString GraphFingerprintAfter =
		FinalPreflight->GetStringField(TEXT("graphFingerprint"));
	if (GraphFingerprintAfter.IsEmpty())
	{
		GEditor->UndoTransaction();
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-readback-failed"),
			TEXT("The post-Apply graph fingerprint is unavailable; the patch was undone."));
	}

	if (RecentPatchApplies.Num() > 16)
	{
		RecentPatchApplies.Reset();
	}
	FPatchReceipt Receipt;
	Receipt.AssetPath = Plan.AssetPath;
	Receipt.GraphGuid = Plan.GraphGuid;
	Receipt.GraphFingerprint = GraphFingerprintAfter;
	Receipt.StateFingerprint = Plan.StateFingerprintAfter;
	Receipt.VariablesFingerprint = Plan.VariablesFingerprintAfter;
	Receipt.NodesFingerprint = Plan.NodesFingerprintAfter;
	Receipt.NodeCount = NodeCountAfter;
	Receipt.LinkCount = LinkCountAfter;
	Receipt.VariableCount = Plan.VariableCountAfter;
	Receipt.OperationCount = Plan.Operations.Num();
	RecentPatchApplies.Add(Plan.PlanId, Receipt);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), ApplySchemaVersion);
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetBoolField(TEXT("applied"), true);
	Result->SetBoolField(TEXT("writeOccurred"), true);
	Result->SetBoolField(TEXT("singleTransaction"), true);
	Result->SetNumberField(TEXT("transactionCount"), 1);
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
		FString(UEAgentKitBlueprintPatchExperimental::SaveConfirmationPrefix) + Plan.PlanId);
	Result->SetStringField(TEXT("graphFingerprintAfter"), GraphFingerprintAfter);
	Result->SetStringField(TEXT("graphFingerprintBefore"), Plan.GraphFingerprint);
	Result->SetStringField(TEXT("stateFingerprintAfter"), Plan.StateFingerprintAfter);
	Result->SetStringField(TEXT("variablesFingerprintBefore"), Plan.VariablesFingerprintBefore);
	Result->SetStringField(TEXT("variablesFingerprintAfter"), Plan.VariablesFingerprintAfter);
	Result->SetStringField(TEXT("nodesFingerprintBefore"), Plan.NodesFingerprintBefore);
	Result->SetStringField(TEXT("nodesFingerprintAfter"), Plan.NodesFingerprintAfter);
	Result->SetNumberField(TEXT("variableCountBefore"), Plan.VariableCountBefore);
	Result->SetNumberField(TEXT("variableCountAfter"), Plan.VariableCountAfter);
	Result->SetNumberField(TEXT("nodeCountBefore"), Plan.NodeCountBefore);
	Result->SetNumberField(TEXT("nodeCountAfter"), NodeCountAfter);
	Result->SetNumberField(TEXT("createdNodeCount"), CreatedNodeCount);
	Result->SetNumberField(TEXT("linkCountBefore"), Plan.LinkCountBefore);
	Result->SetNumberField(TEXT("linkCountAfter"), LinkCountAfter);
	Result->SetNumberField(TEXT("operationCount"), Plan.Operations.Num());
	Result->SetNumberField(TEXT("appliedOperationCount"), Plan.Operations.Num());
	Result->SetArrayField(TEXT("results"), AppliedResults);
	OutResult = Result;
	return true;
}

bool UEAgentKitBlueprintPatchExperimental::TrySaveExperimental(
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
			TEXT("expectedStateFingerprint"),
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
			TEXT("graph-patch-experimental-disabled"),
			TEXT("Experimental atomic Blueprint patching is not opted in for this project."));
	}
	FString AssetPath;
	FString GraphGuid;
	FString PlanId;
	FString ExpectedGraphFingerprint;
	FString ExpectedStateFingerprint;
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
			TEXT("expectedStateFingerprint"),
			ExpectedStateFingerprint,
			OutErrorCode,
			OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!IsSha256(PlanId)
		|| !IsSha1(ExpectedGraphFingerprint)
		|| !IsSha256(ExpectedStateFingerprint))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-invalid-parameters"),
			TEXT("Save requires sha256 planId/stateFingerprint and sha1 graphFingerprint."));
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
			TEXT("graph-patch-invalid-parameters"),
			TEXT("Save requires non-negative integer node/link counts."));
	}
	const int32 ExpectedNodeCount = static_cast<int32>(NodeCountNumber);
	const int32 ExpectedLinkCount = static_cast<int32>(LinkCountNumber);
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintPatchExperimental::SaveConfirmationPrefix) + PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-confirmation-required"),
			FString::Printf(
				TEXT("Explicit Save confirmation must be exactly %s."),
				*ExpectedConfirmation));
	}
	const FPatchReceipt* Receipt = RecentPatchApplies.Find(PlanId);
	if (Receipt == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-requires-apply"),
			TEXT("Save requires a successful Apply of the exact patch plan in this Editor session."));
	}
	if (!Receipt->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive)
		|| !Receipt->GraphGuid.Equals(GraphGuid, ESearchCase::CaseSensitive)
		|| !Receipt->GraphFingerprint.Equals(ExpectedGraphFingerprint, ESearchCase::CaseSensitive)
		|| !Receipt->StateFingerprint.Equals(ExpectedStateFingerprint, ESearchCase::CaseSensitive)
		|| Receipt->NodeCount != ExpectedNodeCount
		|| Receipt->LinkCount != ExpectedLinkCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-plan-mismatch"),
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
			TEXT("graph-patch-save-plan-mismatch"),
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
		|| !UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
			.Equals(ExpectedStateFingerprint, ESearchCase::CaseSensitive)
		// stateFingerprint covers pin defaults and links but neither the node class
		// nor the node position, so Save also re-proves the node structure.
		|| !UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph)
			.Equals(Receipt->NodesFingerprint, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-plan-mismatch"),
			TEXT("The live graph state no longer matches the Apply receipt."));
	}
	// The graph fingerprint does not cover variables, so the Blueprint-level state
	// is proven separately against the receipt with the same shared function.
	if (!UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(Blueprint)
			.Equals(Receipt->VariablesFingerprint, ESearchCase::CaseSensitive)
		|| Blueprint->NewVariables.Num() != Receipt->VariableCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-plan-mismatch"),
			TEXT("The live Blueprint variable state no longer matches the Apply receipt."));
	}

	UPackage* Package = Blueprint->GetOutermost();
	if (Package == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-package-invalid"),
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
			TEXT("graph-patch-save-package-file-missing"),
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
			TEXT("graph-patch-save-failed"),
			TEXT("SavePackage did not confirm a clean saved package."));
	}
	FString AssetDigest;
	if (!FBlueprintContextSha256::HashFile(Filename, AssetDigest))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-hash-failed"),
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
		|| !UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
			.Equals(ExpectedStateFingerprint, ESearchCase::CaseSensitive)
		|| !UEAgentKitBlueprintVariableExperimental::ComputeAtomicVariableStateFingerprint(Blueprint)
			.Equals(Receipt->VariablesFingerprint, ESearchCase::CaseSensitive)
		|| !UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(Graph)
			.Equals(Receipt->NodesFingerprint, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-readback-failed"),
			TEXT("Post-save graph readback did not match the Apply receipt."));
	}
	const int64 FileSizeAfter = IFileManager::Get().FileSize(*Filename);
	if (FileSizeAfter < 0)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-readback-failed"),
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
	Readback->SetStringField(TEXT("stateFingerprint"), ExpectedStateFingerprint);
	Readback->SetStringField(TEXT("variablesFingerprint"), Receipt->VariablesFingerprint);
	Readback->SetStringField(TEXT("nodesFingerprint"), Receipt->NodesFingerprint);
	Readback->SetNumberField(TEXT("variableCount"), Receipt->VariableCount);
	Readback->SetBoolField(TEXT("packageDirty"), false);
	Readback->SetNumberField(TEXT("nodeCount"), ExpectedNodeCount);
	Readback->SetNumberField(TEXT("linkCount"), ExpectedLinkCount);
	Readback->SetBoolField(TEXT("independentReloadVerified"), false);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("save-experimental-blueprint-patch"));
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
	Result->SetStringField(TEXT("stateFingerprint"), ExpectedStateFingerprint);
	Result->SetStringField(TEXT("variablesFingerprint"), Receipt->VariablesFingerprint);
	Result->SetStringField(TEXT("nodesFingerprint"), Receipt->NodesFingerprint);
	Result->SetNumberField(TEXT("variableCount"), Receipt->VariableCount);
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
	RecentPatchApplies.Remove(PlanId);
	OutResult = Result;
	return true;
}
