#include "BlueprintRewireExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/DateTime.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitBlueprintRewireExperimental, Log, All);

namespace
{
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.blueprint-rewire-preview/1.0");
	const TCHAR* const ApplySchemaVersion = TEXT("ueak.blueprint-rewire-apply/1.0");
	const TCHAR* const SaveSchemaVersion = TEXT("ueak.blueprint-rewire-save/1.0");
	const TCHAR* const PlanFingerprintVersion = TEXT("ueak.blueprint-rewire/1.0");
	const TCHAR* const EdgeSetVersion = TEXT("ueak.blueprint-rewire-edge-set/1.0");
	const TCHAR* const UntouchedEdgeSetVersion = TEXT("ueak.blueprint-rewire-untouched-edge-set/1.0");
	const TCHAR* const PinIdentityVersion = TEXT("ueak.blueprint-rewire-pin-identity/1.0");
	const TCHAR* const OperationRewire = TEXT("rewire");
	const TCHAR* const OperationDisconnect = TEXT("disconnect");
	const TCHAR* const DirectionInput = TEXT("input");
	const TCHAR* const DirectionOutput = TEXT("output");

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

	enum class EPinKind : uint8
	{
		Unsupported,
		Exec,
		Bool,
		Int,
		Double,
		String,
	};

	struct FPinRef
	{
		UEdGraphNode* Node = nullptr;
		UEdGraphPin* Pin = nullptr;
		FGuid NodeGuid;
		FGuid PinGuid;
		FString PinName;
		EPinKind Kind = EPinKind::Unsupported;
	};

	struct FRewirePlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString Operation;
		FString PackageRevision;
		FString GraphFingerprint;
		FString EdgeSetHashBefore;
		FString EdgeSetHashAfter;
		FString UntouchedEdgesHash;
		FString PinIdentityHash;
		FString PinKind;
		FPinRef OldSource;
		FPinRef Target;
		FPinRef NewSource;
		int32 NodeCount = 0;
		int32 LinkCountBefore = 0;
		int32 LinkCountAfter = 0;
		FString PlanId;
		TSharedPtr<FJsonObject> Preview;
	};

	struct FPlanContext
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		const UEdGraphSchema_K2* Schema = nullptr;
	};

	struct FRewireReceipt
	{
		FString AssetPath;
		FString GraphGuid;
		FString Operation;
		FString PackageRevision;
		FString GraphFingerprint;
		FString EdgeSetHashBefore;
		FString EdgeSetHashAfter;
		FString PinIdentityHash;
		FString PinKind;
		FString OldSourceNodeGuid;
		FString OldSourcePinName;
		FString OldSourcePinGuid;
		FString TargetNodeGuid;
		FString TargetPinName;
		FString TargetPinGuid;
		FString NewSourceNodeGuid;
		FString NewSourcePinName;
		FString NewSourcePinGuid;
		int32 NodeCount = 0;
		int32 LinkCountBefore = 0;
		int32 LinkCountAfter = 0;
	};

	TMap<FString, FRewireReceipt> RecentRewireApplies;

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

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	bool IsCanonicalGuid(const FString& Value, FGuid& OutGuid)
	{
		if (Value.Len() != 36 || !FGuid::Parse(Value, OutGuid))
		{
			return false;
		}
		return GuidLower(OutGuid).Equals(Value, ESearchCase::CaseSensitive);
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

	bool IsSafePinName(const FString& Value)
	{
		if (Value.IsEmpty() || Value.Len() > 128)
		{
			return false;
		}
		for (const TCHAR Character : Value)
		{
			if (Character == TEXT('|') || Character == TEXT('>') || Character == TEXT('\n')
				|| Character == TEXT('\r') || Character == TEXT('\t') || Character < 0x20)
			{
				return false;
			}
		}
		return true;
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
				TEXT("graph-rewire-invalid-parameters"),
				TEXT("Rewire parameters must be one JSON object."));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			if (!Allowed.Contains(Pair.Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-rewire-unknown-parameter"),
					FString::Printf(TEXT("Unknown rewire parameter: %s"), *Pair.Key));
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
				TEXT("graph-rewire-invalid-parameters"),
				FString::Printf(
					TEXT("%s must be a%s string."),
					Key,
					bAllowEmpty ? TEXT("") : TEXT(" non-empty")));
		}
		return true;
	}

	const TCHAR* PinKindLabel(const EPinKind Kind)
	{
		switch (Kind)
		{
		case EPinKind::Exec:
			return TEXT("exec");
		case EPinKind::Bool:
			return TEXT("bool");
		case EPinKind::Int:
			return TEXT("int");
		case EPinKind::Double:
			return TEXT("double");
		case EPinKind::String:
			return TEXT("string");
		default:
			return TEXT("unsupported");
		}
	}

	EPinKind ClassifyPin(const UEdGraphPin* Pin, FString& OutReason)
	{
		if (Pin == nullptr)
		{
			OutReason = TEXT("missing-pin");
			return EPinKind::Unsupported;
		}
		if (Pin->bHidden)
		{
			OutReason = TEXT("hidden");
			return EPinKind::Unsupported;
		}
		if (Pin->bNotConnectable)
		{
			OutReason = TEXT("not-connectable");
			return EPinKind::Unsupported;
		}
		if (Pin->bOrphanedPin)
		{
			OutReason = TEXT("orphaned");
			return EPinKind::Unsupported;
		}
		if (Pin->ParentPin != nullptr || Pin->SubPins.Num() > 0)
		{
			OutReason = TEXT("split-pin");
			return EPinKind::Unsupported;
		}
		if (Pin->PinType.ContainerType != EPinContainerType::None)
		{
			OutReason = TEXT("container");
			return EPinKind::Unsupported;
		}
		if (Pin->PinType.bIsReference)
		{
			OutReason = TEXT("by-reference");
			return EPinKind::Unsupported;
		}
		if (Pin->PinType.bIsConst)
		{
			OutReason = TEXT("const");
			return EPinKind::Unsupported;
		}
		if (Pin->PinType.bIsWeakPointer)
		{
			OutReason = TEXT("weak-pointer");
			return EPinKind::Unsupported;
		}

		const FName Category = Pin->PinType.PinCategory;
		if (Category == UEdGraphSchema_K2::PC_Exec)
		{
			if (!Pin->PinType.PinSubCategory.IsNone()
				|| Pin->PinType.PinSubCategoryObject.IsValid()
				|| Pin->PinType.PinValueType.TerminalCategory != NAME_None)
			{
				OutReason = TEXT("exec-with-subtype");
				return EPinKind::Unsupported;
			}
			return EPinKind::Exec;
		}
		if (Category == UEdGraphSchema_K2::PC_Wildcard)
		{
			OutReason = TEXT("wildcard");
			return EPinKind::Unsupported;
		}
		if (Category == UEdGraphSchema_K2::PC_Delegate
			|| Category == UEdGraphSchema_K2::PC_MCDelegate)
		{
			OutReason = TEXT("delegate");
			return EPinKind::Unsupported;
		}
		if (!Pin->PinType.PinSubCategoryObject.IsValid() && Category == UEdGraphSchema_K2::PC_Boolean)
		{
			return EPinKind::Bool;
		}
		if (!Pin->PinType.PinSubCategoryObject.IsValid() && Category == UEdGraphSchema_K2::PC_Int)
		{
			return EPinKind::Int;
		}
		if (!Pin->PinType.PinSubCategoryObject.IsValid()
			&& ((Category == UEdGraphSchema_K2::PC_Real
					&& Pin->PinType.PinSubCategory == UEdGraphSchema_K2::PC_Double)
				|| Category == UEdGraphSchema_K2::PC_Double))
		{
			return EPinKind::Double;
		}
		if (!Pin->PinType.PinSubCategoryObject.IsValid() && Category == UEdGraphSchema_K2::PC_String)
		{
			return EPinKind::String;
		}
		OutReason = FString::Printf(
			TEXT("unsupported-category:%s:%s"),
			*Category.ToString(),
			*Pin->PinType.PinSubCategory.ToString());
		return EPinKind::Unsupported;
	}

	FString MakeEdgeKey(const UEdGraphPin* From, const UEdGraphPin* To)
	{
		return GuidLower(From->GetOwningNode()->NodeGuid) + TEXT("|")
			+ From->PinName.ToString() + TEXT("|") + GuidLower(From->PinId) + TEXT(">")
			+ GuidLower(To->GetOwningNode()->NodeGuid) + TEXT("|")
			+ To->PinName.ToString() + TEXT("|") + GuidLower(To->PinId);
	}

	void BuildEdgeKeys(const UEdGraph* Graph, TArray<FString>& OutKeys)
	{
		OutKeys.Reset();
		if (Graph == nullptr)
		{
			return;
		}
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
						OutKeys.Add(MakeEdgeKey(Pin, Linked));
					}
				}
			}
		}
		OutKeys.Sort();
	}

	void BuildPinIdentityKeys(const UEdGraph* Graph, TArray<FString>& OutKeys)
	{
		OutKeys.Reset();
		if (Graph == nullptr)
		{
			return;
		}
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node == nullptr)
			{
				continue;
			}
			const FString NodeGuid = GuidLower(Node->NodeGuid);
			TArray<const UEdGraphPin*> Pins;
			for (const UEdGraphPin* Pin : Node->Pins)
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
				return Left.PinId.ToString(EGuidFormats::Digits)
					< Right.PinId.ToString(EGuidFormats::Digits);
			});
			for (const UEdGraphPin* Pin : Pins)
			{
				OutKeys.Add(
					NodeGuid + TEXT("|") + Pin->PinName.ToString() + TEXT("|")
					+ GuidLower(Pin->PinId) + TEXT("|")
					+ (Pin->Direction == EGPD_Input ? DirectionInput : DirectionOutput));
			}
		}
		OutKeys.Sort();
	}

	bool HashKeys(
		const TCHAR* Version,
		const TArray<FString>& Keys,
		FString& OutHash)
	{
		TArray<FString> Sorted = Keys;
		Sorted.Sort();
		FString Canonical = FString(Version) + TEXT("\n");
		for (const FString& Key : Sorted)
		{
			Canonical += Key + TEXT("\n");
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return false;
		}
		OutHash = FString(TEXT("sha256:")) + Digest.ToLower();
		return true;
	}

	bool HashEdgeKeys(const TArray<FString>& Keys, FString& OutHash)
	{
		return HashKeys(EdgeSetVersion, Keys, OutHash);
	}

	void AppendField(FString& Out, const FString& Value)
	{
		Out += FString::Printf(TEXT("%d:%s"), Value.Len(), *Value);
	}

	UEdGraphNode* FindNode(UEdGraph* Graph, const FGuid& NodeGuid)
	{
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == NodeGuid)
			{
				return Node;
			}
		}
		return nullptr;
	}

	UEdGraphPin* FindPinByGuid(UEdGraphNode* Node, const FGuid& PinGuid)
	{
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr && Pin->PinId == PinGuid)
			{
				return Pin;
			}
		}
		return nullptr;
	}

	bool ResolvePinRef(
		UEdGraph* Graph,
		const FString& NodeGuidText,
		const FString& PinNameText,
		const FString& PinGuidText,
		const EEdGraphPinDirection Direction,
		const TCHAR* Role,
		FPinRef& OutRef,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FGuid NodeGuid;
		FGuid PinGuid;
		if (!IsCanonicalGuid(NodeGuidText, NodeGuid) || !IsCanonicalGuid(PinGuidText, PinGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-invalid-identifier"),
				FString::Printf(TEXT("%s must carry canonical GUIDs."), Role));
		}
		UEdGraphNode* Node = FindNode(Graph, NodeGuid);
		if (Node == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-node-not-found"),
				FString::Printf(TEXT("The %s node %s is not in the focused graph."), Role, *NodeGuidText));
		}
		UEdGraphPin* Pin = FindPinByGuid(Node, PinGuid);
		if (Pin == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-pin-not-found"),
				FString::Printf(TEXT("The %s pin GUID %s is not on node %s."), Role, *PinGuidText, *NodeGuidText));
		}
		if (Pin->Direction != Direction)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-pin-direction-mismatch"),
				FString::Printf(
					TEXT("The %s pin %s must be an %s pin."),
					Role,
					*PinGuidText,
					Direction == EGPD_Input ? DirectionInput : DirectionOutput));
		}
		if (!Pin->PinName.ToString().Equals(PinNameText, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-pin-name-mismatch"),
				FString::Printf(
					TEXT("The %s pin GUID %s is named %s, not %s."),
					Role,
					*PinGuidText,
					*Pin->PinName.ToString(),
					*PinNameText));
		}

		FString Reason;
		const EPinKind Kind = ClassifyPin(Pin, Reason);
		if (Kind == EPinKind::Unsupported)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-pin-type-not-allowed"),
				FString::Printf(
					TEXT("The %s pin %s is not a supported K2 rewire pin (%s)."),
					Role,
					*PinNameText,
					*Reason));
		}

		OutRef.Node = Node;
		OutRef.Pin = Pin;
		OutRef.NodeGuid = NodeGuid;
		OutRef.PinGuid = PinGuid;
		OutRef.PinName = PinNameText;
		OutRef.Kind = Kind;
		return true;
	}

	bool IsEventGraph(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		for (const TObjectPtr<UEdGraph>& Page : Blueprint->UbergraphPages)
		{
			if (Page.Get() == Graph)
			{
				return true;
			}
		}
		return false;
	}

	bool BuildPlan(
		const TSharedPtr<FJsonObject>& Params,
		FRewirePlan& OutPlan,
		FPlanContext& OutContext,
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const bool bAllowApplyKeys = false)
	{
		static const TSet<FString> Allowed = {
			TEXT("assetPath"),
			TEXT("graphGuid"),
			TEXT("operation"),
			TEXT("targetNodeGuid"),
			TEXT("targetPinName"),
			TEXT("targetPinGuid"),
			TEXT("oldSourceNodeGuid"),
			TEXT("oldSourcePinName"),
			TEXT("oldSourcePinGuid"),
			TEXT("newSourceNodeGuid"),
			TEXT("newSourcePinName"),
			TEXT("newSourcePinGuid"),
			TEXT("expectedEdgeSetHash"),
		};
		TSet<FString> EffectiveAllowed = Allowed;
		if (bAllowApplyKeys)
		{
			EffectiveAllowed.Add(TEXT("planId"));
			EffectiveAllowed.Add(TEXT("confirmation"));
		}
		if (!RejectUnknownParams(Params, EffectiveAllowed, OutErrorCode, OutErrorMessage))
		{
			return false;
		}

		FString TargetNodeGuid;
		FString TargetPinName;
		FString TargetPinGuid;
		FString OldSourceNodeGuid;
		FString OldSourcePinName;
		FString OldSourcePinGuid;
		FString NewSourceNodeGuid;
		FString NewSourcePinName;
		FString NewSourcePinGuid;
		if (!ReadString(Params, TEXT("assetPath"), OutPlan.AssetPath, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("graphGuid"), OutPlan.GraphGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("operation"), OutPlan.Operation, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("targetNodeGuid"), TargetNodeGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("targetPinName"), TargetPinName, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("targetPinGuid"), TargetPinGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("oldSourceNodeGuid"), OldSourceNodeGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("oldSourcePinName"), OldSourcePinName, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("oldSourcePinGuid"), OldSourcePinGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("expectedEdgeSetHash"), OutPlan.EdgeSetHashBefore, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (!OutPlan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive)
			&& !OutPlan.Operation.Equals(OperationDisconnect, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-invalid-parameters"),
				TEXT("operation must be exactly 'rewire' or 'disconnect'."));
		}
		if (!IsSafePinName(TargetPinName) || !IsSafePinName(OldSourcePinName))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-invalid-parameters"),
				TEXT("Pin names must be 1-128 printable ASCII characters without '|', '>' or control characters."));
		}
		if (OutPlan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
		{
			if (!ReadString(Params, TEXT("newSourceNodeGuid"), NewSourceNodeGuid, OutErrorCode, OutErrorMessage)
				|| !ReadString(Params, TEXT("newSourcePinName"), NewSourcePinName, OutErrorCode, OutErrorMessage)
				|| !ReadString(Params, TEXT("newSourcePinGuid"), NewSourcePinGuid, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			if (!IsSafePinName(NewSourcePinName))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-rewire-invalid-parameters"),
					TEXT("Pin names must be 1-128 printable ASCII characters without '|', '>' or control characters."));
			}
		}
		else if (Params->HasField(TEXT("newSourceNodeGuid"))
			|| Params->HasField(TEXT("newSourcePinName"))
			|| Params->HasField(TEXT("newSourcePinGuid")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-invalid-parameters"),
				TEXT("A disconnect request must not carry newSource* fields."));
		}

		if (!IsSha256(OutPlan.EdgeSetHashBefore))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-invalid-identifier"),
				TEXT("expectedEdgeSetHash must be the exact sha256 edge-set hash from Preview."));
		}

		if (!UEAgentKitBlueprintRewireExperimental::IsExperimentalWriteAvailable())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-experimental-disabled"),
				TEXT("Experimental Blueprint rewire is not opted in for this project."));
		}

		if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-pie-active"),
				TEXT("Experimental Blueprint rewire is unavailable while PIE or SIE is active."));
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
				TEXT("graph-rewire-focused-graph-mismatch"),
				TEXT("The focused Blueprint editor is not showing the exact assetPath/graphGuid from the request."));
		}
		if (Preflight->GetBoolField(TEXT("packageDirty")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-package-dirty"),
				TEXT("Experimental Blueprint rewire requires a clean package before Apply."));
		}
		if (!Preflight->GetBoolField(TEXT("editable")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-graph-not-editable"),
				TEXT("The focused graph is not editable."));
		}
		OutPlan.PackageRevision = Preflight->GetStringField(TEXT("packageRevision"));
		OutPlan.GraphFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
		if (!IsSha256(OutPlan.PackageRevision))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-revision-unavailable"),
				TEXT("Rewire requires the package to be saved on disk so a sha256 revision is available."));
		}
		if (!IsSha1(OutPlan.GraphFingerprint))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-revision-unavailable"),
				TEXT("Rewire requires the live graph fingerprint."));
		}

		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
			OutPlan.AssetPath,
			OutPlan.GraphGuid,
			OutContext.Blueprint,
			OutContext.Graph,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (OutContext.Blueprint == nullptr || OutContext.Graph == nullptr
			|| OutContext.Graph->GetSchema() == nullptr
			|| !OutContext.Graph->GetSchema()->IsA<UEdGraphSchema_K2>())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-schema-not-supported"),
				TEXT("Experimental Blueprint rewire supports only K2 Blueprint graphs."));
		}
		OutContext.Schema = Cast<UEdGraphSchema_K2>(OutContext.Graph->GetSchema());
		if (!IsEventGraph(OutContext.Blueprint, OutContext.Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-graph-not-event-graph"),
				TEXT("Experimental Blueprint rewire is limited to top-level event graphs."));
		}

		TArray<FString> BeforeKeys;
		BuildEdgeKeys(OutContext.Graph, BeforeKeys);
		FString LiveEdgeHash;
		if (!HashEdgeKeys(BeforeKeys, LiveEdgeHash))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-fingerprint-failed"),
				TEXT("Could not compute the live edge-set hash."));
		}
		if (!LiveEdgeHash.Equals(OutPlan.EdgeSetHashBefore, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-edge-set-stale"),
				TEXT("The live edge set no longer matches expectedEdgeSetHash; run Preview again."));
		}

		if (!ResolvePinRef(
				OutContext.Graph,
				OldSourceNodeGuid,
				OldSourcePinName,
				OldSourcePinGuid,
				EGPD_Output,
				TEXT("old source"),
				OutPlan.OldSource,
				OutErrorCode,
				OutErrorMessage)
			|| !ResolvePinRef(
				OutContext.Graph,
				TargetNodeGuid,
				TargetPinName,
				TargetPinGuid,
				EGPD_Input,
				TEXT("target"),
				OutPlan.Target,
				OutErrorCode,
				OutErrorMessage)
			|| (OutPlan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive)
				&& !ResolvePinRef(
					OutContext.Graph,
					NewSourceNodeGuid,
					NewSourcePinName,
					NewSourcePinGuid,
					EGPD_Output,
					TEXT("new source"),
					OutPlan.NewSource,
					OutErrorCode,
					OutErrorMessage)))
		{
			return false;
		}

		if (OutPlan.Target.Kind != OutPlan.OldSource.Kind)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-pin-kind-mismatch"),
				FString::Printf(
					TEXT("The old source pin is %s but the target pin is %s."),
					PinKindLabel(OutPlan.OldSource.Kind),
					PinKindLabel(OutPlan.Target.Kind)));
		}
		if (OutPlan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive)
			&& OutPlan.NewSource.Kind != OutPlan.Target.Kind)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-pin-kind-mismatch"),
				FString::Printf(
					TEXT("The new source pin is %s but the target pin is %s."),
					PinKindLabel(OutPlan.NewSource.Kind),
					PinKindLabel(OutPlan.Target.Kind)));
		}
		OutPlan.PinKind = PinKindLabel(OutPlan.Target.Kind);

		if (OutPlan.Target.Pin->LinkedTo.Num() == 0)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-target-unlinked"),
				TEXT("The target input pin has no existing connection; rewire requires exactly one."));
		}
		if (OutPlan.Target.Pin->LinkedTo.Num() != 1)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-target-multiple-links"),
				TEXT("The target input pin has more than one connection; this slice requires exactly one."));
		}
		if (OutPlan.Target.Pin->LinkedTo[0] != OutPlan.OldSource.Pin
			|| !OutPlan.OldSource.Pin->LinkedTo.Contains(OutPlan.Target.Pin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-old-edge-mismatch"),
				TEXT("The target pin is not connected to the declared old source pin."));
		}

		const FString RemovedKey = MakeEdgeKey(OutPlan.OldSource.Pin, OutPlan.Target.Pin);

		if (OutPlan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
		{
			if (OutPlan.OldSource.Node == OutPlan.NewSource.Node
				&& OutPlan.OldSource.Pin == OutPlan.NewSource.Pin)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-rewire-no-op"),
					TEXT("The new source pin is the current source pin."));
			}
			if (OutPlan.NewSource.Pin->LinkedTo.Contains(OutPlan.Target.Pin))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-rewire-already-linked"),
					TEXT("The new source pin is already connected to the target pin."));
			}
			const FPinConnectionResponse Response =
				OutContext.Schema->CanCreateConnection(OutPlan.NewSource.Pin, OutPlan.Target.Pin);
			switch (Response.Response.GetValue())
			{
			case CONNECT_RESPONSE_MAKE:
			case CONNECT_RESPONSE_BREAK_OTHERS_B:
				break;
			case CONNECT_RESPONSE_BREAK_OTHERS_A:
			case CONNECT_RESPONSE_BREAK_OTHERS_AB:
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-rewire-schema-would-break-other-links"),
					TEXT("The K2 schema would break connections on the new source pin, which is outside this plan."));
			case CONNECT_RESPONSE_MAKE_WITH_CONVERSION_NODE:
			case CONNECT_RESPONSE_MAKE_WITH_PROMOTION:
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-rewire-conversion-not-allowed"),
					TEXT("The K2 schema would insert a conversion or promotion node, which is outside this plan."));
			case CONNECT_RESPONSE_DISALLOW:
			default:
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-rewire-schema-rejected"),
					FString::Printf(
						TEXT("The K2 schema rejected the new connection: %s"),
						*Response.Message.ToString()));
			}
		}

		TArray<FString> AfterKeys;
		for (const FString& Key : BeforeKeys)
		{
			if (!Key.Equals(RemovedKey, ESearchCase::CaseSensitive))
			{
				AfterKeys.Add(Key);
			}
		}
		TArray<FString> UntouchedKeys = AfterKeys;
		if (OutPlan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
		{
			AfterKeys.Add(MakeEdgeKey(OutPlan.NewSource.Pin, OutPlan.Target.Pin));
		}
		AfterKeys.Sort();

		if (!HashEdgeKeys(AfterKeys, OutPlan.EdgeSetHashAfter)
			|| !HashKeys(UntouchedEdgeSetVersion, UntouchedKeys, OutPlan.UntouchedEdgesHash))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-fingerprint-failed"),
				TEXT("Could not compute the planned edge-set hash."));
		}

		TArray<FString> PinIdentityKeys;
		BuildPinIdentityKeys(OutContext.Graph, PinIdentityKeys);
		if (!HashKeys(PinIdentityVersion, PinIdentityKeys, OutPlan.PinIdentityHash))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-fingerprint-failed"),
				TEXT("Could not compute the pin identity hash."));
		}

		OutPlan.NodeCount = OutContext.Graph->Nodes.Num();
		OutPlan.LinkCountBefore = BeforeKeys.Num();
		OutPlan.LinkCountAfter = AfterKeys.Num();

		FString Canonical;
		Canonical.Reserve(2048);
		AppendField(Canonical, PlanFingerprintVersion);
		AppendField(Canonical, OutPlan.AssetPath);
		AppendField(Canonical, OutPlan.GraphGuid);
		AppendField(Canonical, OutPlan.PackageRevision);
		AppendField(Canonical, OutPlan.GraphFingerprint);
		AppendField(Canonical, OutPlan.Operation);
		AppendField(Canonical, OutPlan.EdgeSetHashBefore);
		AppendField(Canonical, OutPlan.EdgeSetHashAfter);
		AppendField(Canonical, OutPlan.UntouchedEdgesHash);
		AppendField(Canonical, OutPlan.PinIdentityHash);
		AppendField(Canonical, OutPlan.PinKind);
		AppendField(Canonical, OutPlan.OldSource.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		AppendField(Canonical, OutPlan.OldSource.PinName);
		AppendField(Canonical, OutPlan.OldSource.PinGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		AppendField(Canonical, OutPlan.Target.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		AppendField(Canonical, OutPlan.Target.PinName);
		AppendField(Canonical, OutPlan.Target.PinGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		if (OutPlan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
		{
			AppendField(Canonical, OutPlan.NewSource.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			AppendField(Canonical, OutPlan.NewSource.PinName);
			AppendField(Canonical, OutPlan.NewSource.PinGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		}
		else
		{
			AppendField(Canonical, TEXT("-"));
			AppendField(Canonical, TEXT("-"));
			AppendField(Canonical, TEXT("-"));
		}
		AppendField(Canonical, FString::FromInt(OutPlan.NodeCount));
		AppendField(Canonical, FString::FromInt(OutPlan.LinkCountBefore));
		AppendField(Canonical, FString::FromInt(OutPlan.LinkCountAfter));

		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-fingerprint-failed"),
				TEXT("Could not compute the rewire plan hash."));
		}
		OutPlan.PlanId = FString(TEXT("sha256:")) + Digest.ToLower();

		UE_LOG(
			LogUEAgentKitBlueprintRewireExperimental,
			Display,
			TEXT("Rewire plan operation=%s planId=%s revision=%s edgeBefore=%s edgeAfter=%s links=%d->%d pins=%s"),
			*OutPlan.Operation,
			*OutPlan.PlanId,
			*OutPlan.PackageRevision,
			*OutPlan.EdgeSetHashBefore,
			*OutPlan.EdgeSetHashAfter,
			OutPlan.LinkCountBefore,
			OutPlan.LinkCountAfter,
			*OutPlan.PinKind);

		return true;
	}

	TSharedPtr<FJsonObject> EdgeJson(const FPinRef& From, const FPinRef& To, const TCHAR* Kind)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("fromNodeGuid"), GuidLower(From.NodeGuid));
		Item->SetStringField(TEXT("fromPinName"), From.PinName);
		Item->SetStringField(TEXT("fromPinGuid"), GuidLower(From.PinGuid));
		Item->SetStringField(TEXT("toNodeGuid"), GuidLower(To.NodeGuid));
		Item->SetStringField(TEXT("toPinName"), To.PinName);
		Item->SetStringField(TEXT("toPinGuid"), GuidLower(To.PinGuid));
		Item->SetStringField(TEXT("kind"), Kind);
		return Item;
	}

	TSharedPtr<FJsonObject> PinJson(const FPinRef& Ref, const TCHAR* Direction)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("nodeGuid"), GuidLower(Ref.NodeGuid));
		Item->SetStringField(TEXT("pinName"), Ref.PinName);
		Item->SetStringField(TEXT("pinGuid"), GuidLower(Ref.PinGuid));
		Item->SetStringField(TEXT("direction"), Direction);
		Item->SetStringField(TEXT("kind"), PinKindLabel(Ref.Kind));
		return Item;
	}

	void AddEdgeArray(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Key,
		const TArray<TSharedPtr<FJsonValue>>& Values)
	{
		Object->SetArrayField(Key, Values);
	}

	bool BuildPreviewResult(
		const FRewirePlan& Plan,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		TSharedRef<FJsonObject> Preview = MakeShared<FJsonObject>();
		Preview->SetStringField(TEXT("schemaVersion"), PreviewSchemaVersion);
		Preview->SetBoolField(TEXT("experimental"), true);
		Preview->SetBoolField(TEXT("readOnly"), true);
		Preview->SetBoolField(TEXT("wroteBlueprint"), false);
		Preview->SetBoolField(TEXT("savedPackage"), false);
		Preview->SetBoolField(TEXT("compiled"), false);
		Preview->SetStringField(TEXT("planId"), Plan.PlanId);
		Preview->SetStringField(TEXT("assetPath"), Plan.AssetPath);
		Preview->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
		Preview->SetStringField(TEXT("operation"), Plan.Operation);
		Preview->SetStringField(TEXT("packageRevision"), Plan.PackageRevision);
		Preview->SetStringField(TEXT("graphFingerprint"), Plan.GraphFingerprint);
		Preview->SetStringField(TEXT("edgeSetHashBefore"), Plan.EdgeSetHashBefore);
		Preview->SetStringField(TEXT("edgeSetHashAfter"), Plan.EdgeSetHashAfter);
		Preview->SetStringField(TEXT("untouchedEdgesHash"), Plan.UntouchedEdgesHash);
		Preview->SetStringField(TEXT("pinIdentityHash"), Plan.PinIdentityHash);
		Preview->SetStringField(TEXT("pinKind"), Plan.PinKind);
		Preview->SetNumberField(TEXT("nodeCountBefore"), Plan.NodeCount);
		Preview->SetNumberField(TEXT("nodeCountAfter"), Plan.NodeCount);
		Preview->SetNumberField(TEXT("linkCountBefore"), Plan.LinkCountBefore);
		Preview->SetNumberField(TEXT("linkCountAfter"), Plan.LinkCountAfter);
		Preview->SetNumberField(TEXT("untouchedEdgeCount"), Plan.LinkCountBefore - 1);

		TArray<TSharedPtr<FJsonValue>> Removed;
		Removed.Add(MakeShared<FJsonValueObject>(
			EdgeJson(Plan.OldSource, Plan.Target, *Plan.PinKind)));
		AddEdgeArray(Preview, TEXT("removedEdges"), Removed);

		TArray<TSharedPtr<FJsonValue>> Added;
		if (Plan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
		{
			Added.Add(MakeShared<FJsonValueObject>(
				EdgeJson(Plan.NewSource, Plan.Target, *Plan.PinKind)));
		}
		AddEdgeArray(Preview, TEXT("addedEdges"), Added);

		Preview->SetObjectField(TEXT("oldSourcePin"), PinJson(Plan.OldSource, DirectionOutput));
		Preview->SetObjectField(TEXT("targetPin"), PinJson(Plan.Target, DirectionInput));
		if (Plan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
		{
			Preview->SetObjectField(TEXT("newSourcePin"), PinJson(Plan.NewSource, DirectionOutput));
		}
		else
		{
			Preview->SetField(TEXT("newSourcePin"), MakeShared<FJsonValueNull>());
		}
		Preview->SetStringField(
			TEXT("requiredConfirmation"),
			FString(UEAgentKitBlueprintRewireExperimental::ApplyConfirmationPrefix) + Plan.PlanId);

		OutResult = Preview;
		return true;
	}

	int32 CountLinks(const UEdGraph* Graph)
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

	bool VerifyTopology(
		const UEdGraph* Graph,
		const FString& ExpectedEdgeHash,
		const FString& ExpectedPinIdentityHash,
		const int32 ExpectedNodeCount,
		const int32 ExpectedLinkCount,
		FString& OutActualEdgeHash,
		FString& OutActualPinIdentityHash,
		int32& OutNodeCount,
		int32& OutLinkCount)
	{
		TArray<FString> EdgeKeys;
		BuildEdgeKeys(Graph, EdgeKeys);
		if (!HashEdgeKeys(EdgeKeys, OutActualEdgeHash))
		{
			return false;
		}
		TArray<FString> PinKeys;
		BuildPinIdentityKeys(Graph, PinKeys);
		if (!HashKeys(PinIdentityVersion, PinKeys, OutActualPinIdentityHash))
		{
			return false;
		}
		OutNodeCount = Graph->Nodes.Num();
		OutLinkCount = EdgeKeys.Num();
		return OutActualEdgeHash.Equals(ExpectedEdgeHash, ESearchCase::CaseSensitive)
			&& OutActualPinIdentityHash.Equals(ExpectedPinIdentityHash, ESearchCase::CaseSensitive)
			&& OutNodeCount == ExpectedNodeCount
			&& OutLinkCount == ExpectedLinkCount;
	}

	/**
	 * Last-resort manual restoration used only after an engine-side rollback
	 * failed.  It removes every unplanned link from the target input and
	 * re-creates only the exact old edge, then proves the before edge hash.
	 */
	bool RestoreBeforeTopology(const FRewirePlan& Plan, FString& OutActualEdgeHash)
	{
		TArray<UEdGraphPin*> TargetLinks = Plan.Target.Pin->LinkedTo;
		for (UEdGraphPin* Linked : TargetLinks)
		{
			if (Linked != nullptr && Linked != Plan.OldSource.Pin)
			{
				Plan.Target.Pin->BreakLinkTo(Linked);
			}
		}
		if (!Plan.Target.Pin->LinkedTo.Contains(Plan.OldSource.Pin))
		{
			Plan.OldSource.Pin->MakeLinkTo(Plan.Target.Pin);
		}
		Plan.Target.Node->PinConnectionListChanged(Plan.Target.Pin);
		Plan.OldSource.Node->PinConnectionListChanged(Plan.OldSource.Pin);

		TArray<FString> EdgeKeys;
		BuildEdgeKeys(Plan.Target.Node->GetGraph(), EdgeKeys);
		if (!HashEdgeKeys(EdgeKeys, OutActualEdgeHash))
		{
			return false;
		}
		return OutActualEdgeHash.Equals(Plan.EdgeSetHashBefore, ESearchCase::CaseSensitive);
	}

	bool RollbackCommittedTransaction(
		const FRewirePlan& Plan,
		bool& bOutUndoUsed,
		FString& OutActualEdgeHash)
	{
		bOutUndoUsed = GEditor != nullptr && GEditor->UndoTransaction();
		TArray<FString> EdgeKeys;
		BuildEdgeKeys(Plan.Target.Node->GetGraph(), EdgeKeys);
		if (HashEdgeKeys(EdgeKeys, OutActualEdgeHash)
			&& OutActualEdgeHash.Equals(Plan.EdgeSetHashBefore, ESearchCase::CaseSensitive))
		{
			return true;
		}
		return RestoreBeforeTopology(Plan, OutActualEdgeHash);
	}
}

bool UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(
	UEdGraph* Graph,
	FString& OutHash)
{
	if (Graph == nullptr)
	{
		return false;
	}
	TArray<FString> Keys;
	BuildEdgeKeys(Graph, Keys);
	return HashEdgeKeys(Keys, OutHash);
}

bool UEAgentKitBlueprintRewireExperimental::IsExperimentalWriteAvailable()
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

void UEAgentKitBlueprintRewireExperimental::SetExperimentalWriteEnabledForTest(bool bEnabled)
{
	bTestOverrideSet = true;
	bTestOverrideEnabled = bEnabled;
}

void UEAgentKitBlueprintRewireExperimental::ResetExperimentalWriteForTest()
{
	bTestOverrideSet = false;
	bTestOverrideEnabled = false;
	RecentRewireApplies.Reset();
}

bool UEAgentKitBlueprintRewireExperimental::TryPreviewExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	FRewirePlan Plan;
	FPlanContext Context;
	if (!BuildPlan(Params, Plan, Context, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!BuildPreviewResult(Plan, OutResult, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	Plan.Preview = OutResult;
	return true;
}

bool UEAgentKitBlueprintRewireExperimental::TryApplyExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	static const TSet<FString> Allowed = {
		TEXT("assetPath"),
		TEXT("graphGuid"),
		TEXT("operation"),
		TEXT("targetNodeGuid"),
		TEXT("targetPinName"),
		TEXT("targetPinGuid"),
		TEXT("oldSourceNodeGuid"),
		TEXT("oldSourcePinName"),
		TEXT("oldSourcePinGuid"),
		TEXT("newSourceNodeGuid"),
		TEXT("newSourcePinName"),
		TEXT("newSourcePinGuid"),
		TEXT("expectedEdgeSetHash"),
		TEXT("planId"),
		TEXT("confirmation"),
	};
	if (!RejectUnknownParams(Params, Allowed, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	FString RequestedPlanId;
	FString Confirmation;
	if (!ReadString(Params, TEXT("planId"), RequestedPlanId, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!IsSha256(RequestedPlanId))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-invalid-identifier"),
			TEXT("planId must be the exact sha256 plan id from Preview."));
	}
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintRewireExperimental::ApplyConfirmationPrefix) + RequestedPlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-confirmation-required"),
			FString::Printf(TEXT("Apply requires the exact confirmation: %s"), *ExpectedConfirmation));
	}

	FRewirePlan Plan;
	FPlanContext Context;
	if (!BuildPlan(Params, Plan, Context, OutErrorCode, OutErrorMessage, true))
	{
		return false;
	}
	if (!Plan.PlanId.Equals(RequestedPlanId, ESearchCase::CaseSensitive))
	{
		UE_LOG(
			LogUEAgentKitBlueprintRewireExperimental,
			Warning,
			TEXT("Rewire plan stale: requested=%s computed=%s"),
			*RequestedPlanId,
			*Plan.PlanId);
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-plan-stale"),
			TEXT("The Blueprint rewire plan is stale; run Preview again."));
	}

	UEdGraph* Graph = Context.Graph;
	const UEdGraphSchema_K2* Schema = Context.Schema;
	const int32 NodeCountBefore = Plan.NodeCount;
	const int32 LinkCountBefore = Plan.LinkCountBefore;

	{
		FScopedTransaction Transaction(
			FText::FromString(TEXT("UEAgentKit Rewire Blueprint Pin")));
		Context.Blueprint->Modify();
		Graph->Modify();
		Plan.OldSource.Node->Modify();
		Plan.Target.Node->Modify();
		if (Plan.NewSource.Node != nullptr)
		{
			Plan.NewSource.Node->Modify();
		}

		auto Abort = [&OutErrorCode, &OutErrorMessage, &Transaction](
			const TCHAR* Code,
			const FString& Message) -> bool
		{
			Transaction.Cancel();
			return Fail(OutErrorCode, OutErrorMessage, Code, Message);
		};

		if (Plan.Target.Pin->LinkedTo.Num() != 1
			|| Plan.Target.Pin->LinkedTo[0] != Plan.OldSource.Pin)
		{
			return Abort(
				TEXT("graph-rewire-old-edge-mismatch"),
				TEXT("The planned old edge changed before the transaction could replace it."));
		}

		Plan.OldSource.Pin->BreakLinkTo(Plan.Target.Pin);
		Plan.OldSource.Node->PinConnectionListChanged(Plan.OldSource.Pin);
		Plan.Target.Node->PinConnectionListChanged(Plan.Target.Pin);
		if (Plan.Target.Pin->LinkedTo.Num() != 0
			|| Plan.OldSource.Pin->LinkedTo.Contains(Plan.Target.Pin))
		{
			return Abort(
				TEXT("graph-rewire-break-failed"),
				TEXT("The single planned edge was not removed cleanly."));
		}

		if (Plan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
		{
			const FPinConnectionResponse Response =
				Schema->CanCreateConnection(Plan.NewSource.Pin, Plan.Target.Pin);
			if (Response.Response.GetValue() != CONNECT_RESPONSE_MAKE)
			{
				return Abort(
					TEXT("graph-rewire-schema-rejected"),
					FString::Printf(
						TEXT("The K2 schema does not allow a plain connection after the planned break: %s"),
						*Response.Message.ToString()));
			}
			if (!Schema->TryCreateConnection(Plan.NewSource.Pin, Plan.Target.Pin))
			{
				return Abort(
					TEXT("graph-rewire-connect-failed"),
					TEXT("The K2 schema refused the planned connection."));
			}
			if (Plan.Target.Pin->LinkedTo.Num() != 1
				|| Plan.Target.Pin->LinkedTo[0] != Plan.NewSource.Pin)
			{
				return Abort(
					TEXT("graph-rewire-connect-failed"),
					TEXT("The target pin is not connected to exactly the new source pin."));
			}
		}

		FBlueprintEditorUtils::MarkBlueprintAsModified(Context.Blueprint);
		Context.Blueprint->GetOutermost()->MarkPackageDirty();
	}

	FString AfterEdgeHash;
	FString AfterPinHash;
	int32 AfterNodeCount = 0;
	int32 AfterLinkCount = 0;
	const bool bPostVerified = VerifyTopology(
		Graph,
		Plan.EdgeSetHashAfter,
		Plan.PinIdentityHash,
		Plan.NodeCount,
		Plan.LinkCountAfter,
		AfterEdgeHash,
		AfterPinHash,
		AfterNodeCount,
		AfterLinkCount);
	if (!bPostVerified)
	{
		bool bUndoUsed = false;
		FString RestoredHash;
		const bool bRestored = RollbackCommittedTransaction(Plan, bUndoUsed, RestoredHash);
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-post-commit-mismatch"),
			FString::Printf(
				TEXT("The committed topology did not match the plan (edge=%s pinIdentity=%s nodes=%d links=%d); restored=%s viaUndo=%s.")
				,
				*AfterEdgeHash,
				*AfterPinHash,
				AfterNodeCount,
				AfterLinkCount,
				bRestored ? TEXT("true") : TEXT("false"),
				bUndoUsed ? TEXT("true") : TEXT("false")));
	}

	const bool bUndoCalled = GEditor != nullptr && GEditor->UndoTransaction();
	FString UndoEdgeHash;
	FString UndoPinHash;
	int32 UndoNodeCount = 0;
	int32 UndoLinkCount = 0;
	const bool bUndoVerified = bUndoCalled
		&& VerifyTopology(
			Graph,
			Plan.EdgeSetHashBefore,
			Plan.PinIdentityHash,
			NodeCountBefore,
			LinkCountBefore,
			UndoEdgeHash,
			UndoPinHash,
			UndoNodeCount,
			UndoLinkCount);
	const bool bRedoCalled = bUndoVerified && GEditor != nullptr && GEditor->RedoTransaction();
	FString RedoEdgeHash;
	FString RedoPinHash;
	int32 RedoNodeCount = 0;
	int32 RedoLinkCount = 0;
	const bool bRedoVerified = bRedoCalled
		&& VerifyTopology(
			Graph,
			Plan.EdgeSetHashAfter,
			Plan.PinIdentityHash,
			Plan.NodeCount,
			Plan.LinkCountAfter,
			RedoEdgeHash,
			RedoPinHash,
			RedoNodeCount,
			RedoLinkCount);
	if (!bUndoVerified || !bRedoVerified)
	{
		// Never undo again here: a second UndoTransaction would revert an
		// unrelated earlier transaction.  A verified Undo already proves the
		// before topology; otherwise restore the single planned edge manually.
		bool bRestored = bUndoVerified && !bRedoVerified;
		FString RestoredHash = UndoEdgeHash;
		if (!bUndoVerified)
		{
			bRestored = RestoreBeforeTopology(Plan, RestoredHash);
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-undo-redo-failed"),
			FString::Printf(
				TEXT("Undo/Redo did not restore the exact topology (undo=%s redo=%s undoEdge=%s redoEdge=%s); restored=%s restoredEdge=%s.")
				,
				bUndoVerified ? TEXT("true") : TEXT("false"),
				bRedoVerified ? TEXT("true") : TEXT("false"),
				*UndoEdgeHash,
				*RedoEdgeHash,
				bRestored ? TEXT("true") : TEXT("false"),
				*RestoredHash));
	}

	FRewireReceipt Receipt;
	Receipt.AssetPath = Plan.AssetPath;
	Receipt.GraphGuid = Plan.GraphGuid;
	Receipt.Operation = Plan.Operation;
	Receipt.PackageRevision = Plan.PackageRevision;
	Receipt.GraphFingerprint = Plan.GraphFingerprint;
	Receipt.EdgeSetHashBefore = Plan.EdgeSetHashBefore;
	Receipt.EdgeSetHashAfter = Plan.EdgeSetHashAfter;
	Receipt.PinIdentityHash = Plan.PinIdentityHash;
	Receipt.PinKind = Plan.PinKind;
	Receipt.OldSourceNodeGuid = GuidLower(Plan.OldSource.NodeGuid);
	Receipt.OldSourcePinName = Plan.OldSource.PinName;
	Receipt.OldSourcePinGuid = GuidLower(Plan.OldSource.PinGuid);
	Receipt.TargetNodeGuid = GuidLower(Plan.Target.NodeGuid);
	Receipt.TargetPinName = Plan.Target.PinName;
	Receipt.TargetPinGuid = GuidLower(Plan.Target.PinGuid);
	if (Plan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
	{
		Receipt.NewSourceNodeGuid = GuidLower(Plan.NewSource.NodeGuid);
		Receipt.NewSourcePinName = Plan.NewSource.PinName;
		Receipt.NewSourcePinGuid = GuidLower(Plan.NewSource.PinGuid);
	}
	Receipt.NodeCount = Plan.NodeCount;
	Receipt.LinkCountBefore = Plan.LinkCountBefore;
	Receipt.LinkCountAfter = Plan.LinkCountAfter;
	RecentRewireApplies.Add(Plan.PlanId, Receipt);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), ApplySchemaVersion);
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetBoolField(TEXT("applied"), true);
	Result->SetBoolField(TEXT("undoVerified"), bUndoVerified);
	Result->SetBoolField(TEXT("redoVerified"), bRedoVerified);
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("skeletonRegenerated"), false);
	Result->SetBoolField(TEXT("packageDirty"), true);
	Result->SetStringField(TEXT("planId"), Plan.PlanId);
	Result->SetStringField(TEXT("assetPath"), Plan.AssetPath);
	Result->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
	Result->SetStringField(TEXT("operation"), Plan.Operation);
	Result->SetStringField(TEXT("packageRevision"), Plan.PackageRevision);
	Result->SetStringField(TEXT("graphFingerprint"), Plan.GraphFingerprint);
	Result->SetStringField(TEXT("edgeSetHashBefore"), Plan.EdgeSetHashBefore);
	Result->SetStringField(TEXT("edgeSetHashAfter"), Plan.EdgeSetHashAfter);
	Result->SetStringField(TEXT("pinIdentityHash"), Plan.PinIdentityHash);
	Result->SetStringField(TEXT("pinKind"), Plan.PinKind);
	Result->SetNumberField(TEXT("nodeCount"), Plan.NodeCount);
	Result->SetNumberField(TEXT("linkCountBefore"), Plan.LinkCountBefore);
	Result->SetNumberField(TEXT("linkCountAfter"), Plan.LinkCountAfter);
	Result->SetNumberField(TEXT("linkCount"), Plan.LinkCountAfter);

	TArray<TSharedPtr<FJsonValue>> Removed;
	Removed.Add(MakeShared<FJsonValueObject>(
		EdgeJson(Plan.OldSource, Plan.Target, *Plan.PinKind)));
	Result->SetArrayField(TEXT("removedEdges"), Removed);
	TArray<TSharedPtr<FJsonValue>> Added;
	if (Plan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
	{
		Added.Add(MakeShared<FJsonValueObject>(
			EdgeJson(Plan.NewSource, Plan.Target, *Plan.PinKind)));
	}
	Result->SetArrayField(TEXT("addedEdges"), Added);
	Result->SetObjectField(TEXT("oldSourcePin"), PinJson(Plan.OldSource, DirectionOutput));
	Result->SetObjectField(TEXT("targetPin"), PinJson(Plan.Target, DirectionInput));
	if (Plan.Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
	{
		Result->SetObjectField(TEXT("newSourcePin"), PinJson(Plan.NewSource, DirectionOutput));
	}
	else
	{
		Result->SetField(TEXT("newSourcePin"), MakeShared<FJsonValueNull>());
	}
	Result->SetStringField(
		TEXT("requiredSaveConfirmation"),
		FString(UEAgentKitBlueprintRewireExperimental::SaveConfirmationPrefix) + Plan.PlanId);

	OutResult = Result;
	return true;
}

bool UEAgentKitBlueprintRewireExperimental::TrySaveExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	static const TSet<FString> Allowed = {
		TEXT("assetPath"),
		TEXT("graphGuid"),
		TEXT("planId"),
		TEXT("expectedEdgeSetHashAfter"),
		TEXT("expectedPackageRevision"),
		TEXT("expectedNodeCount"),
		TEXT("expectedLinkCount"),
		TEXT("confirmation"),
	};
	if (!RejectUnknownParams(Params, Allowed, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	FString AssetPath;
	FString GraphGuid;
	FString PlanId;
	FString ExpectedEdgeSetHashAfter;
	FString ExpectedPackageRevision;
	FString Confirmation;
	double NodeCountNumber = 0.0;
	double LinkCountNumber = 0.0;
	if (!ReadString(Params, TEXT("assetPath"), AssetPath, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("graphGuid"), GraphGuid, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("planId"), PlanId, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("expectedEdgeSetHashAfter"), ExpectedEdgeSetHashAfter, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("expectedPackageRevision"), ExpectedPackageRevision, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage)
		|| !Params->TryGetNumberField(TEXT("expectedNodeCount"), NodeCountNumber)
		|| !Params->TryGetNumberField(TEXT("expectedLinkCount"), LinkCountNumber))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-invalid-parameters"),
			TEXT("Save requires assetPath, graphGuid, planId, expectedEdgeSetHashAfter, expectedPackageRevision, expectedNodeCount, expectedLinkCount and confirmation."));
	}
	if (!IsSha256(PlanId) || !IsSha256(ExpectedEdgeSetHashAfter) || !IsSha256(ExpectedPackageRevision))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-invalid-identifier"),
			TEXT("Save identifiers must be exact sha256 values."));
	}
	const int32 ExpectedNodeCount = static_cast<int32>(NodeCountNumber);
	const int32 ExpectedLinkCount = static_cast<int32>(LinkCountNumber);
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintRewireExperimental::SaveConfirmationPrefix) + PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-confirmation-required"),
			FString::Printf(TEXT("Save requires the exact confirmation: %s"), *ExpectedConfirmation));
	}
	if (!UEAgentKitBlueprintRewireExperimental::IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-experimental-disabled"),
			TEXT("Experimental Blueprint rewire is not opted in for this project."));
	}
	if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-pie-active"),
			TEXT("Experimental Blueprint rewire is unavailable while PIE or SIE is active."));
	}

	const FRewireReceipt* Receipt = RecentRewireApplies.Find(PlanId);
	if (Receipt == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-requires-apply"),
			TEXT("Save requires the Apply receipt from the same Editor process."));
	}
	if (!Receipt->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive)
		|| !Receipt->GraphGuid.Equals(GraphGuid, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-plan-mismatch"),
			TEXT("The Apply receipt belongs to a different asset/graph."));
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
			TEXT("graph-rewire-focused-graph-mismatch"),
			TEXT("The focused Blueprint editor is not showing the exact asset/graph being saved."));
	}
	if (!Preflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-not-dirty"),
			TEXT("Save requires the dirty package produced by the preceding Apply."));
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
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-schema-not-supported"),
			TEXT("Could not resolve the exact Blueprint graph for Save."));
	}

	const FString Filename = FPackageName::LongPackageNameToFilename(
		Blueprint->GetOutermost()->GetName(),
		FPackageName::GetAssetPackageExtension());
	FString DiskDigestBefore;
	if (!FBlueprintContextSha256::HashFile(Filename, DiskDigestBefore))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-hash-failed"),
			TEXT("Could not hash the on-disk package before Save."));
	}
	const FString DiskRevisionBefore = FString(TEXT("sha256:")) + DiskDigestBefore.ToLower();
	if (!DiskRevisionBefore.Equals(ExpectedPackageRevision, ESearchCase::CaseSensitive)
		|| !DiskRevisionBefore.Equals(Receipt->PackageRevision, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-revision-mismatch"),
			TEXT("The on-disk package revision does not match the Apply receipt or the request."));
	}

	TArray<FString> EdgeKeys;
	BuildEdgeKeys(Graph, EdgeKeys);
	FString LiveEdgeHash;
	if (!HashEdgeKeys(EdgeKeys, LiveEdgeHash)
		|| !LiveEdgeHash.Equals(ExpectedEdgeSetHashAfter, ESearchCase::CaseSensitive)
		|| !LiveEdgeHash.Equals(Receipt->EdgeSetHashAfter, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-edge-set-mismatch"),
			TEXT("The live edge set does not match the post-Apply edge-set hash."));
	}
	if (Graph->Nodes.Num() != ExpectedNodeCount
		|| Graph->Nodes.Num() != Receipt->NodeCount
		|| EdgeKeys.Num() != ExpectedLinkCount
		|| EdgeKeys.Num() != Receipt->LinkCountAfter)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-count-mismatch"),
			TEXT("The live node/link counts do not match the Apply receipt or the request."));
	}

	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	if (!UPackage::SavePackage(Blueprint->GetOutermost(), Blueprint, *Filename, SaveArgs))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-failed"),
			TEXT("UPackage::SavePackage failed."));
	}
	FString DiskDigestAfter;
	if (!FBlueprintContextSha256::HashFile(Filename, DiskDigestAfter))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-hash-failed"),
			TEXT("Could not hash the saved package."));
	}
	const FString DiskRevisionAfter = FString(TEXT("sha256:")) + DiskDigestAfter.ToLower();
	const int64 FileSizeAfter = IFileManager::Get().FileSize(*Filename);
	if (FileSizeAfter <= 0)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-readback-failed"),
			TEXT("The saved package file is missing or empty."));
	}

	FString PostEdgeHash;
	FString PostPinHash;
	int32 PostNodeCount = 0;
	int32 PostLinkCount = 0;
	const bool bReadbackVerified = VerifyTopology(
		Graph,
		Receipt->EdgeSetHashAfter,
		Receipt->PinIdentityHash,
		Receipt->NodeCount,
		Receipt->LinkCountAfter,
		PostEdgeHash,
		PostPinHash,
		PostNodeCount,
		PostLinkCount);
	if (!bReadbackVerified)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-readback-failed"),
			TEXT("The saved graph no longer matches the post-Apply edge set."));
	}

	TSharedPtr<FJsonObject> PostPreflight;
	FString PostCode;
	FString PostMessage;
	const bool bPostPreflight = UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		PostPreflight,
		PostCode,
		PostMessage)
		&& PostPreflight->GetBoolField(TEXT("packageDirty")) == false
		&& PostPreflight->GetStringField(TEXT("assetPath")).Equals(AssetPath, ESearchCase::CaseSensitive)
		&& PostPreflight->GetStringField(TEXT("graphGuid")).Equals(GraphGuid, ESearchCase::CaseSensitive)
		&& static_cast<int32>(PostPreflight->GetNumberField(TEXT("nodeCount"))) == Receipt->NodeCount;
	if (!bPostPreflight)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-save-readback-failed"),
			TEXT("The post-Save preflight did not report the same clean graph."));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), SaveSchemaVersion);
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetBoolField(TEXT("saved"), true);
	Result->SetBoolField(TEXT("readbackVerified"), bReadbackVerified);
	Result->SetBoolField(TEXT("packageDirty"), false);
	Result->SetBoolField(TEXT("compiled"), false);
	Result->SetBoolField(TEXT("skeletonRegenerated"), false);
	Result->SetBoolField(TEXT("independentReloadVerified"), false);
	Result->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
	Result->SetStringField(TEXT("planId"), PlanId);
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("graphGuid"), GraphGuid);
	Result->SetStringField(TEXT("operation"), Receipt->Operation);
	Result->SetStringField(TEXT("packageRevision"), ExpectedPackageRevision);
	Result->SetStringField(TEXT("assetSha256"), DiskRevisionAfter);
	Result->SetNumberField(TEXT("assetSizeBytes"), static_cast<double>(FileSizeAfter));
	Result->SetStringField(TEXT("edgeSetHashBefore"), Receipt->EdgeSetHashBefore);
	Result->SetStringField(TEXT("edgeSetHashAfter"), Receipt->EdgeSetHashAfter);
	Result->SetStringField(TEXT("pinIdentityHash"), Receipt->PinIdentityHash);
	Result->SetNumberField(TEXT("nodeCount"), Receipt->NodeCount);
	Result->SetNumberField(TEXT("linkCount"), Receipt->LinkCountAfter);
	Result->SetNumberField(TEXT("linkCountBefore"), Receipt->LinkCountBefore);

	TSharedRef<FJsonObject> Removed = MakeShared<FJsonObject>();
	Removed->SetStringField(TEXT("fromNodeGuid"), Receipt->OldSourceNodeGuid);
	Removed->SetStringField(TEXT("fromPinName"), Receipt->OldSourcePinName);
	Removed->SetStringField(TEXT("fromPinGuid"), Receipt->OldSourcePinGuid);
	Removed->SetStringField(TEXT("toNodeGuid"), Receipt->TargetNodeGuid);
	Removed->SetStringField(TEXT("toPinName"), Receipt->TargetPinName);
	Removed->SetStringField(TEXT("toPinGuid"), Receipt->TargetPinGuid);
	Removed->SetStringField(TEXT("kind"), Receipt->PinKind);
	TArray<TSharedPtr<FJsonValue>> RemovedValues;
	RemovedValues.Add(MakeShared<FJsonValueObject>(Removed));
	Result->SetArrayField(TEXT("removedEdges"), RemovedValues);

	TArray<TSharedPtr<FJsonValue>> AddedValues;
	if (Receipt->Operation.Equals(OperationRewire, ESearchCase::CaseSensitive))
	{
		TSharedRef<FJsonObject> Added = MakeShared<FJsonObject>();
		Added->SetStringField(TEXT("fromNodeGuid"), Receipt->NewSourceNodeGuid);
		Added->SetStringField(TEXT("fromPinName"), Receipt->NewSourcePinName);
		Added->SetStringField(TEXT("fromPinGuid"), Receipt->NewSourcePinGuid);
		Added->SetStringField(TEXT("toNodeGuid"), Receipt->TargetNodeGuid);
		Added->SetStringField(TEXT("toPinName"), Receipt->TargetPinName);
		Added->SetStringField(TEXT("toPinGuid"), Receipt->TargetPinGuid);
		Added->SetStringField(TEXT("kind"), Receipt->PinKind);
		AddedValues.Add(MakeShared<FJsonValueObject>(Added));
	}
	Result->SetArrayField(TEXT("addedEdges"), AddedValues);

	OutResult = Result;
	// Receipt points into the TMap, so keep it alive until every result
	// field copied its data; removing it before this point is a
	// use-after-free that silently corrupts the returned hashes.
	RecentRewireApplies.Remove(PlanId);
	return true;
}
