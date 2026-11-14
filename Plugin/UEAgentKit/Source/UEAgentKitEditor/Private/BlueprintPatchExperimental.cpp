#include "BlueprintPatchExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintPinDefaultEditExperimental.h"

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
		TEXT("createVariable"),
		TEXT("createNode"),
		TEXT("connect"),
		TEXT("rewirePin"),
		TEXT("removeNode"),
	};

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
		/** Kind-specific resolved operation; only set for migrated kinds. */
		UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation PinDefault;
		/** opId/kind/resolved fields; reused verbatim for the results array. */
		TSharedPtr<FJsonObject> Detail;
	};

	struct FPatchPlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString PackageRevision;
		FString GraphFingerprint;
		FString StateFingerprintBefore;
		FString StateFingerprintAfter;
		FString PlanId;
		TArray<FPatchOperation> Operations;
		int32 NodeCountBefore = 0;
		int32 LinkCountBefore = 0;
		/** First operation that failed during the build, or INDEX_NONE. */
		int32 FailedIndex = INDEX_NONE;
	};

	struct FPatchReceipt
	{
		FString AssetPath;
		FString GraphGuid;
		FString GraphFingerprint;
		FString StateFingerprint;
		int32 NodeCount = 0;
		int32 LinkCount = 0;
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
		return Kind.Equals(KindSetPinDefault, ESearchCase::CaseSensitive);
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
							KindSetPinDefault));
				}
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-patch-kind-not-allowed"),
					FString::Printf(
						TEXT("Operation kind '%s' is not an atomic patch kind."),
						*Operation.Kind));
			}

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
		OutFailedIndex = INDEX_NONE;
		return true;
	}

	/** Resolve one parsed operation against the live graph.  Read-only. */
	bool ResolveOperation(
		UEdGraph* Graph,
		FPatchOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (IsMigratedKind(Operation.Kind))
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
		UEdGraph* Graph,
		FPatchOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (IsMigratedKind(Operation.Kind))
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
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-kind-not-migrated"),
			FString::Printf(
				TEXT("Operation kind '%s' has no atomic implementation."),
				*Operation.Kind));
	}

	/** Read one applied operation back. */
	bool CheckOperation(
		UEdGraph* Graph,
		const FPatchOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (IsMigratedKind(Operation.Kind))
		{
			TArray<UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation> Single;
			Single.Add(Operation.PinDefault);
			return UEAgentKitBlueprintPinDefaultExperimental::TryCheckAtomicOperations(
				Graph,
				Single,
				/*bExpectAfter=*/true,
				OutErrorCode,
				OutErrorMessage);
		}
		return true;
	}

	/** Put one applied operation back to its pre-patch value. */
	bool RestoreOperation(
		UEdGraph* Graph,
		const FPatchOperation& Operation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (IsMigratedKind(Operation.Kind))
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
		return true;
	}

	FString ComputeOperationCanonical(const FPatchOperation& Operation)
	{
		FString Canonical;
		Canonical = AppendField(Canonical, Operation.OpId);
		Canonical = AppendField(Canonical, Operation.Kind);
		if (IsMigratedKind(Operation.Kind))
		{
			Canonical = AppendField(Canonical, Operation.PinDefault.NodeGuid);
			Canonical = AppendField(Canonical, Operation.PinDefault.PinName);
			Canonical = AppendField(Canonical, Operation.PinDefault.PinGuid);
			Canonical = AppendField(Canonical, Operation.PinDefault.Type);
			Canonical = AppendField(Canonical, Operation.PinDefault.BeforeValue);
			Canonical = AppendField(Canonical, Operation.PinDefault.Value);
		}
		return Canonical;
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
		OutPlan.NodeCountBefore = Graph->Nodes.Num();
		OutPlan.LinkCountBefore = CountLinks(Graph);

		for (int32 Index = 0; Index < OutPlan.Operations.Num(); ++Index)
		{
			if (!ResolveOperation(
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
		Result->SetNumberField(TEXT("operationCount"), Plan.Operations.Num());
		Result->SetNumberField(TEXT("nodeCount"), Plan.NodeCountBefore);
		Result->SetNumberField(TEXT("linkCount"), Plan.LinkCountBefore);
		Result->SetStringField(
			TEXT("requiredConfirmation"),
			FString(UEAgentKitBlueprintPatchExperimental::ApplyConfirmationPrefix) + Plan.PlanId);
		Result->SetArrayField(TEXT("migratedKinds"), { MakeShared<FJsonValueString>(KindSetPinDefault) });
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
				if (!RestoreOperation(Graph, Plan.Operations[Index], RestoreCode, RestoreMessage))
				{
					// A pin that can no longer be resolved cannot be restored by
					// this layer; report it instead of claiming a clean rollback.
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
			OutResult = BuildInactiveResult(
				ApplySchemaVersion,
				Plan,
				BuildOperationResults(
					Plan.Operations,
					FailedIndex,
					OutErrorCode,
					OutErrorMessage,
					StatusApplied,
					/*bRolledBack=*/true),
				StatusApplied,
				/*bTransactionOpened=*/true,
				/*bRolledBack=*/true);
			OutResult->SetNumberField(TEXT("appliedOperationCount"), 0);
			return false;
		}

		// Every operation must read back through its own writer before the
		// transaction is allowed to commit.
		for (int32 Index = 0; Index < Plan.Operations.Num(); ++Index)
		{
			if (!CheckOperation(Graph, Plan.Operations[Index], OutErrorCode, OutErrorMessage))
			{
				RestoreAllAndCancel();
				OutResult = BuildInactiveResult(
					ApplySchemaVersion,
					Plan,
					BuildOperationResults(
						Plan.Operations,
						Index,
						OutErrorCode,
						OutErrorMessage,
						StatusApplied,
						/*bRolledBack=*/true),
					StatusApplied,
					/*bTransactionOpened=*/true,
					/*bRolledBack=*/true);
				OutResult->SetNumberField(TEXT("appliedOperationCount"), 0);
				return false;
			}
		}

		Package->MarkPackageDirty();
		if (!Package->IsDirty())
		{
			RestoreAllAndCancel();
			OutResult = BuildInactiveResult(
				ApplySchemaVersion,
				Plan,
				BuildOperationResults(
					Plan.Operations,
					INDEX_NONE,
					FString(),
					FString(),
					StatusApplied,
					/*bRolledBack=*/true),
				StatusApplied,
				/*bTransactionOpened=*/true,
				/*bRolledBack=*/true);
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-dirty-not-confirmed"),
				TEXT("The Editor did not confirm the Blueprint package became dirty; the whole patch was restored and its transaction cancelled."));
		}
		Plan.StateFingerprintAfter =
			UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph);
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
	bool bUndoVerified = false;
	bool bRedoVerified = false;
	{
		bool bBeforeStateVerified = false;
		if (GEditor->UndoTransaction())
		{
			TArray<UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation> UndoOperations;
			for (const FPatchOperation& Operation : Plan.Operations)
			{
				UndoOperations.Add(Operation.PinDefault);
			}
			// Undo must put every kind back to its before value; for pin defaults
			// that is the same readback the single writer performs.
			bBeforeStateVerified =
				UEAgentKitBlueprintPinDefaultExperimental::TryCheckAtomicOperations(
					Graph,
					UndoOperations,
					/*bExpectAfter=*/false,
					OutErrorCode,
					OutErrorMessage)
				&& UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
					.Equals(Plan.StateFingerprintBefore, ESearchCase::CaseSensitive);
		}
		bUndoVerified = bBeforeStateVerified;
		bool bAfterStateVerified = false;
		if (bUndoVerified && GEditor->RedoTransaction())
		{
			TArray<UEAgentKitBlueprintPinDefaultExperimental::FAtomicOperation> RedoOperations;
			for (const FPatchOperation& Operation : Plan.Operations)
			{
				RedoOperations.Add(Operation.PinDefault);
			}
			bAfterStateVerified =
				UEAgentKitBlueprintPinDefaultExperimental::TryCheckAtomicOperations(
					Graph,
					RedoOperations,
					/*bExpectAfter=*/true,
					OutErrorCode,
					OutErrorMessage)
				&& UEAgentKitBlueprintPinDefaultExperimental::ComputeAtomicStateFingerprint(Graph)
					.Equals(Plan.StateFingerprintAfter, ESearchCase::CaseSensitive);
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

	if (RecentPatchApplies.Num() > 16)
	{
		RecentPatchApplies.Reset();
	}
	FPatchReceipt Receipt;
	Receipt.AssetPath = Plan.AssetPath;
	Receipt.GraphGuid = Plan.GraphGuid;
	Receipt.GraphFingerprint = Plan.GraphFingerprint;
	Receipt.StateFingerprint = Plan.StateFingerprintAfter;
	Receipt.NodeCount = NodeCountAfter;
	Receipt.LinkCount = LinkCountAfter;
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
	Result->SetStringField(TEXT("graphFingerprintAfter"), Plan.GraphFingerprint);
	Result->SetStringField(TEXT("stateFingerprintAfter"), Plan.StateFingerprintAfter);
	Result->SetNumberField(TEXT("nodeCountBefore"), Plan.NodeCountBefore);
	Result->SetNumberField(TEXT("nodeCountAfter"), NodeCountAfter);
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
			.Equals(ExpectedStateFingerprint, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-patch-save-plan-mismatch"),
			TEXT("The live graph state no longer matches the Apply receipt."));
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
			.Equals(ExpectedStateFingerprint, ESearchCase::CaseSensitive))
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
