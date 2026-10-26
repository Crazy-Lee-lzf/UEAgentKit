#include "BlueprintNodeRemoveExperimental.h"

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
#include "K2Node_CallFunction.h"
#include "K2Node_IfThenElse.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/DateTime.h"
#include "Misc/PackageName.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitBlueprintNodeRemoveExperimental, Log, All);

namespace
{
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.blueprint-node-remove-preview/1.0");
	const TCHAR* const ApplySchemaVersion = TEXT("ueak.blueprint-node-remove-apply/1.0");
	const TCHAR* const SaveSchemaVersion = TEXT("ueak.blueprint-node-remove-save/1.0");
	const TCHAR* const PlanFingerprintVersion = TEXT("ueak.blueprint-node-remove/1.0");
	const TCHAR* const EdgeSetVersion = TEXT("ueak.blueprint-node-remove-edge-set/1.0");
	const TCHAR* const UntouchedEdgeSetVersion =
		TEXT("ueak.blueprint-node-remove-untouched-edge-set/1.0");
	const TCHAR* const PinIdentityVersion =
		TEXT("ueak.blueprint-node-remove-pin-identity/1.0");
	const TCHAR* const NodeIdentityVersion =
		TEXT("ueak.blueprint-node-remove-node-identity/1.0");
	const TCHAR* const DirectionInput = TEXT("input");
	const TCHAR* const DirectionOutput = TEXT("output");
	const TCHAR* const CallFunctionClassPath = TEXT("/Script/BlueprintGraph.K2Node_CallFunction");
	const TCHAR* const BranchClassPath = TEXT("/Script/BlueprintGraph.K2Node_IfThenElse");
	const int32 MaxNodesPerPlan = 1;
	const int32 MaxIncidentEdges = 1;

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

	const TSet<FString>& AllowlistedFunctionNames()
	{
		static const TSet<FString> Names = {
			TEXT("Not_PreBool"),
			TEXT("Add_IntInt"),
			TEXT("Greater_IntInt"),
			TEXT("EqualEqual_BoolBool"),
			TEXT("BooleanAND"),
			TEXT("BooleanOR"),
		};
		return Names;
	}

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

	struct FEdgeRef
	{
		FPinRef From;
		FPinRef To;
	};

	struct FRemovePlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString NodeGuid;
		FString NodeClass;
		FString NodeTitle;
		FString PackageRevision;
		FString GraphFingerprint;
		FString EdgeSetHashBefore;
		FString EdgeSetHashAfter;
		FString UntouchedEdgesHash;
		FString PinIdentityHashBefore;
		FString PinIdentityHashAfter;
		FString NodeIdentityHashBefore;
		FString NodeIdentityHashAfter;
		TArray<FEdgeRef> RemovedEdges;
		int32 NodeCountBefore = 0;
		int32 NodeCountAfter = 0;
		int32 LinkCountBefore = 0;
		int32 LinkCountAfter = 0;
		int32 DeletedNodePinCount = 0;
		FString PlanId;
		TSharedPtr<FJsonObject> Preview;
	};

	struct FPlanContext
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		const UEdGraphSchema_K2* Schema = nullptr;
	};

	struct FRemoveReceipt
	{
		FString AssetPath;
		FString GraphGuid;
		FString NodeGuid;
		FString NodeClass;
		FString PackageRevision;
		FString GraphFingerprint;
		FString EdgeSetHashBefore;
		FString EdgeSetHashAfter;
		FString PinIdentityHashAfter;
		FString NodeIdentityHashAfter;
		TArray<FString> RemovedEdgeKeys;
		int32 NodeCountBefore = 0;
		int32 NodeCountAfter = 0;
		int32 LinkCountBefore = 0;
		int32 LinkCountAfter = 0;
	};

	TMap<FString, FRemoveReceipt> RecentRemoves;

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
				TEXT("graph-remove-invalid-parameters"),
				TEXT("Node-removal parameters must be one JSON object."));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			if (!Allowed.Contains(Pair.Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-remove-unknown-parameter"),
					FString::Printf(TEXT("Unknown node-removal parameter: %s"), *Pair.Key));
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
				TEXT("graph-remove-invalid-parameters"),
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

	bool MakePinRef(UEdGraphPin* Pin, FPinRef& OutRef, FString& OutReason)
	{
		if (Pin == nullptr || Pin->GetOwningNode() == nullptr)
		{
			OutReason = TEXT("missing-pin");
			return false;
		}
		const EPinKind Kind = ClassifyPin(Pin, OutReason);
		if (Kind == EPinKind::Unsupported)
		{
			return false;
		}
		OutRef.Node = Pin->GetOwningNode();
		OutRef.Pin = Pin;
		OutRef.NodeGuid = OutRef.Node->NodeGuid;
		OutRef.PinGuid = Pin->PinId;
		OutRef.PinName = Pin->PinName.ToString();
		OutRef.Kind = Kind;
		return true;
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

	void BuildNodeIdentityKeys(const UEdGraph* Graph, TArray<FString>& OutKeys)
	{
		OutKeys.Reset();
		if (Graph == nullptr)
		{
			return;
		}
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				OutKeys.Add(
					GuidLower(Node->NodeGuid) + TEXT("|")
					+ (Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString()));
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

	/**
	 * Conservative node-class allowlist.  A node is removable only when it is a
	 * branch or an allowlisted pure Kismet math call; every comment group,
	 * event entry, timeline, knot, tunnel, macro, variable accessor and unknown
	 * class fails closed.
	 */
	bool IsRemovableNode(UEdGraphNode* Node, FString& OutClassPath, FString& OutReason)
	{
		OutClassPath = Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString();
		if (OutClassPath.Equals(CallFunctionClassPath, ESearchCase::CaseSensitive))
		{
			UK2Node_CallFunction* Call = Cast<UK2Node_CallFunction>(Node);
			UFunction* Function = Call != nullptr ? Call->GetTargetFunction() : nullptr;
			if (Function == nullptr)
			{
				OutReason = TEXT("function-unresolved");
				return false;
			}
			if (!Function->HasAnyFunctionFlags(FUNC_BlueprintPure)
				|| Function->HasAnyFunctionFlags(FUNC_BlueprintEvent)
				|| Function->HasAnyFunctionFlags(FUNC_Delegate))
			{
				OutReason = TEXT("function-not-pure");
				return false;
			}
			UClass* OwnerClass = Function->GetOwnerClass();
			if (OwnerClass == nullptr
				|| !OwnerClass->GetPathName().Equals(
					TEXT("/Script/Engine.KismetMathLibrary"),
					ESearchCase::CaseSensitive))
			{
				OutReason = TEXT("function-owner-not-allowlisted");
				return false;
			}
			if (!AllowlistedFunctionNames().Contains(Function->GetName()))
			{
				OutReason = FString::Printf(
					TEXT("function-not-allowlisted:%s"),
					*Function->GetName());
				return false;
			}
			return true;
		}
		if (OutClassPath.Equals(BranchClassPath, ESearchCase::CaseSensitive))
		{
			return true;
		}
		OutReason = TEXT("class-not-allowlisted");
		return false;
	}

	bool GatherIncidentEdges(
		UEdGraphNode* Node,
		TArray<FEdgeRef>& OutEdges,
		FString& OutReason)
	{
		OutEdges.Reset();
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr)
			{
				continue;
			}
			for (UEdGraphPin* Linked : Pin->LinkedTo)
			{
				if (Linked == nullptr || Linked->GetOwningNode() == nullptr)
				{
					continue;
				}
				FEdgeRef Edge;
				if (Pin->Direction == EGPD_Output)
				{
					FString FromReason;
					FString ToReason;
					if (!MakePinRef(Pin, Edge.From, FromReason)
						|| !MakePinRef(Linked, Edge.To, ToReason))
					{
						OutReason = TEXT("incident-edge-unsupported-pin");
						return false;
					}
				}
				else
				{
					FString FromReason;
					FString ToReason;
					if (!MakePinRef(Linked, Edge.From, FromReason)
						|| !MakePinRef(Pin, Edge.To, ToReason))
					{
						OutReason = TEXT("incident-edge-unsupported-pin");
						return false;
					}
				}
				OutEdges.Add(Edge);
			}
		}
		return true;
	}

	bool ReadRemovedEdgeKeys(
		const TSharedPtr<FJsonObject>& Params,
		TSet<FString>& OutKeys,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TArray<TSharedPtr<FJsonValue>>* Edges = nullptr;
		if (!Params.IsValid() || !Params->TryGetArrayField(TEXT("removedEdges"), Edges)
			|| Edges == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-parameters"),
				TEXT("removedEdges must be an array (empty for an isolated node)."));
		}
		if (Edges->Num() > MaxIncidentEdges)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-invalid"),
				FString::Printf(
					TEXT("A node-removal plan may list at most %d incident edge."),
					MaxIncidentEdges));
		}
		OutKeys.Reset();
		for (const TSharedPtr<FJsonValue>& Value : *Edges)
		{
			const TSharedPtr<FJsonObject> Edge = Value.IsValid() ? Value->AsObject() : nullptr;
			FString FromNode;
			FString FromPinName;
			FString FromPinGuid;
			FString ToNode;
			FString ToPinName;
			FString ToPinGuid;
			FGuid Parsed;
			if (!Edge.IsValid()
				|| !Edge->TryGetStringField(TEXT("fromNodeGuid"), FromNode)
				|| !Edge->TryGetStringField(TEXT("fromPinName"), FromPinName)
				|| !Edge->TryGetStringField(TEXT("fromPinGuid"), FromPinGuid)
				|| !Edge->TryGetStringField(TEXT("toNodeGuid"), ToNode)
				|| !Edge->TryGetStringField(TEXT("toPinName"), ToPinName)
				|| !Edge->TryGetStringField(TEXT("toPinGuid"), ToPinGuid)
				|| !IsCanonicalGuid(FromNode, Parsed)
				|| !IsCanonicalGuid(FromPinGuid, Parsed)
				|| !IsCanonicalGuid(ToNode, Parsed)
				|| !IsCanonicalGuid(ToPinGuid, Parsed)
				|| !IsSafePinName(FromPinName)
				|| !IsSafePinName(ToPinName))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-remove-removed-edges-invalid"),
					TEXT("Each removedEdges entry needs canonical from/to node and pin GUIDs and safe pin names."));
			}
			const FString Key =
				FromNode + TEXT("|") + FromPinName + TEXT("|") + FromPinGuid + TEXT(">")
				+ ToNode + TEXT("|") + ToPinName + TEXT("|") + ToPinGuid;
			if (OutKeys.Contains(Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-remove-removed-edges-invalid"),
					TEXT("removedEdges must not contain duplicate entries."));
			}
			OutKeys.Add(Key);
		}
		return true;
	}

	FString JoinKeys(const TArray<FString>& Keys)
	{
		TArray<FString> Sorted = Keys;
		Sorted.Sort();
		return FString::Join(Sorted, TEXT(", "));
	}

	bool BuildPlan(
		const TSharedPtr<FJsonObject>& Params,
		FRemovePlan& OutPlan,
		FPlanContext& OutContext,
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const bool bAllowApplyKeys = false)
	{
		static const TSet<FString> Allowed = {
			TEXT("assetPath"),
			TEXT("graphGuid"),
			TEXT("nodeGuid"),
			TEXT("expectedNodeClass"),
			TEXT("expectedEdgeSetHash"),
			TEXT("removedEdges"),
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

		FString NodeGuidText;
		if (!ReadString(Params, TEXT("assetPath"), OutPlan.AssetPath, OutErrorCode, OutErrorMessage)
			|| !ReadString(
				Params,
				TEXT("graphGuid"),
				OutPlan.GraphGuid,
				OutErrorCode,
				OutErrorMessage)
			|| !ReadString(Params, TEXT("nodeGuid"), NodeGuidText, OutErrorCode, OutErrorMessage)
			|| !ReadString(
				Params,
				TEXT("expectedNodeClass"),
				OutPlan.NodeClass,
				OutErrorCode,
				OutErrorMessage)
			|| !ReadString(
				Params,
				TEXT("expectedEdgeSetHash"),
				OutPlan.EdgeSetHashBefore,
				OutErrorCode,
				OutErrorMessage))
		{
			return false;
		}
		FGuid ParsedGuid;
		if (!IsCanonicalGuid(OutPlan.GraphGuid, ParsedGuid)
			|| !IsCanonicalGuid(NodeGuidText, ParsedGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("graphGuid and nodeGuid must be canonical lower-case GUIDs."));
		}
		if (!IsSha256(OutPlan.EdgeSetHashBefore))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("expectedEdgeSetHash must be the exact sha256 edge-set hash from Preview."));
		}
		OutPlan.NodeGuid = NodeGuidText;

		if (!UEAgentKitBlueprintNodeRemoveExperimental::IsExperimentalWriteAvailable())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-experimental-disabled"),
				TEXT("Experimental Blueprint node removal is not opted in for this project."));
		}

		if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-pie-active"),
				TEXT("Experimental Blueprint node removal is unavailable while PIE or SIE is active."));
		}

		TSharedPtr<FJsonObject> Preflight;
		if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
			Preflight,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (!Preflight->GetStringField(TEXT("assetPath")).Equals(
				OutPlan.AssetPath,
				ESearchCase::CaseSensitive)
			|| !Preflight->GetStringField(TEXT("graphGuid")).Equals(
				OutPlan.GraphGuid,
				ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-focused-graph-mismatch"),
				TEXT("The focused Blueprint editor is not showing the exact assetPath/graphGuid from the request."));
		}
		if (Preflight->GetBoolField(TEXT("packageDirty")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-package-dirty"),
				TEXT("Save the Blueprint before planning a node removal."));
		}
		if (!Preflight->GetBoolField(TEXT("editable")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-graph-not-editable"),
				TEXT("The focused graph is not editable."));
		}
		OutPlan.PackageRevision = Preflight->GetStringField(TEXT("packageRevision"));
		OutPlan.GraphFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
		OutPlan.NodeCountBefore = static_cast<int32>(Preflight->GetNumberField(TEXT("nodeCount")));
		if (!IsSha256(OutPlan.PackageRevision))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("Node removal requires the package to be saved on disk so a sha256 revision is available."));
		}
		if (!IsSha1(OutPlan.GraphFingerprint))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("The shared layout preflight returned no canonical graph fingerprint."));
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
		if (!IsEventGraph(OutContext.Blueprint, OutContext.Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-node-type-not-allowed"),
				TEXT("Only top-level K2 event graphs (UbergraphPages) may lose a node."));
		}
		const UEdGraphSchema_K2* Schema =
			Cast<UEdGraphSchema_K2>(OutContext.Graph->GetSchema());
		if (Schema == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-node-type-not-allowed"),
				TEXT("The focused graph is not a K2 graph."));
		}
		OutContext.Schema = Schema;

		TSet<FString> RequestedEdgeKeys;
		if (!ReadRemovedEdgeKeys(
				Params,
				RequestedEdgeKeys,
				OutErrorCode,
				OutErrorMessage))
		{
			return false;
		}

		FGuid NodeGuid;
		IsCanonicalGuid(OutPlan.NodeGuid, NodeGuid);
		UEdGraphNode* Node = FindNode(OutContext.Graph, NodeGuid);
		if (Node == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-node-not-found"),
				FString::Printf(TEXT("Node %s is not in the focused graph."), *OutPlan.NodeGuid));
		}
		FString ActualClass;
		FString Reason;
		if (!IsRemovableNode(Node, ActualClass, Reason))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-node-type-not-allowed"),
				FString::Printf(
					TEXT("Node %s of class %s is not a removable deterministic node (%s)."),
					*OutPlan.NodeGuid,
					*ActualClass,
					*Reason));
		}
		if (!ActualClass.Equals(OutPlan.NodeClass, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-node-class-mismatch"),
				FString::Printf(
					TEXT("Node %s is %s, not the requested %s."),
					*OutPlan.NodeGuid,
					*ActualClass,
					*OutPlan.NodeClass));
		}

		OutPlan.NodeTitle = Node->GetNodeTitle(ENodeTitleType::ListView).ToString();
		OutPlan.DeletedNodePinCount = Node->Pins.Num();
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr)
			{
				continue;
			}
			// Function-call nodes carry an unlinked hidden 'self' pin.  A hidden or
			// non-connectable pin is harmless only while it has no links; a linked
			// hidden pin would make the removal unverifiable.
			if (Pin->bHidden || Pin->bNotConnectable)
			{
				if (Pin->LinkedTo.Num() != 0)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-remove-node-pin-type-not-allowed"),
						FString::Printf(
							TEXT("Node %s carries a linked hidden/non-connectable pin %s."),
							*OutPlan.NodeGuid,
							*Pin->PinName.ToString()));
				}
				continue;
			}
			FString PinReason;
			if (ClassifyPin(Pin, PinReason) == EPinKind::Unsupported)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-remove-node-pin-type-not-allowed"),
					FString::Printf(
						TEXT("Node %s carries an unsupported pin %s (%s); the removal would be unverifiable."),
						*OutPlan.NodeGuid,
						Pin != nullptr ? *Pin->PinName.ToString() : TEXT("<null>"),
						*PinReason));
			}
		}

		TArray<FEdgeRef> LiveEdges;
		if (!GatherIncidentEdges(Node, LiveEdges, Reason))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-node-pin-type-not-allowed"),
				FString::Printf(
					TEXT("Node %s has an incident edge with an unsupported pin (%s)."),
					*OutPlan.NodeGuid,
					*Reason));
		}
		if (LiveEdges.Num() > MaxIncidentEdges)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-node-too-many-connections"),
				FString::Printf(
					TEXT("Node %s has %d incident edges; this slice only removes nodes with at most %d planned edge."),
					*OutPlan.NodeGuid,
					LiveEdges.Num(),
					MaxIncidentEdges));
		}
		OutPlan.RemovedEdges = LiveEdges;

		TSet<FString> LiveEdgeKeys;
		for (const FEdgeRef& Edge : LiveEdges)
		{
			LiveEdgeKeys.Add(MakeEdgeKey(Edge.From.Pin, Edge.To.Pin));
		}
		if (!LiveEdgeKeys.Includes(RequestedEdgeKeys)
			|| !RequestedEdgeKeys.Includes(LiveEdgeKeys)
			|| LiveEdgeKeys.Num() != RequestedEdgeKeys.Num())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-mismatch"),
				FString::Printf(
					TEXT("removedEdges does not exactly match the node's live incident edges. Live=[%s] requested=[%s]."),
					*JoinKeys(LiveEdgeKeys.Array()),
					*JoinKeys(RequestedEdgeKeys.Array())));
		}

		TArray<FString> BeforeEdgeKeys;
		BuildEdgeKeys(OutContext.Graph, BeforeEdgeKeys);
		FString ActualBeforeHash;
		if (!HashKeys(EdgeSetVersion, BeforeEdgeKeys, ActualBeforeHash))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("Could not hash the current edge set."));
		}
		OutPlan.LinkCountBefore = BeforeEdgeKeys.Num();
		if (!ActualBeforeHash.Equals(OutPlan.EdgeSetHashBefore, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-edge-set-stale"),
				FString::Printf(
					TEXT("The live edge-set hash %s does not match expectedEdgeSetHash %s."),
					*ActualBeforeHash,
					*OutPlan.EdgeSetHashBefore));
		}

		TArray<FString> AfterEdgeKeys = BeforeEdgeKeys;
		for (const FString& Key : LiveEdgeKeys)
		{
			AfterEdgeKeys.Remove(Key);
		}
		if (!HashKeys(EdgeSetVersion, AfterEdgeKeys, OutPlan.EdgeSetHashAfter)
			|| !HashKeys(UntouchedEdgeSetVersion, AfterEdgeKeys, OutPlan.UntouchedEdgesHash))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("Could not hash the planned after edge set."));
		}

		TArray<FString> BeforePinKeys;
		BuildPinIdentityKeys(OutContext.Graph, BeforePinKeys);
		if (!HashKeys(PinIdentityVersion, BeforePinKeys, OutPlan.PinIdentityHashBefore))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("Could not hash the current pin identity set."));
		}

		TArray<FString> BeforeNodeKeys;
		BuildNodeIdentityKeys(OutContext.Graph, BeforeNodeKeys);
		if (!HashKeys(NodeIdentityVersion, BeforeNodeKeys, OutPlan.NodeIdentityHashBefore))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("Could not hash the current node identity set."));
		}

		// Derive the planned after-state by simulation (remove the node's pins and
		// node identity entry), so Apply can be verified without trusting counts.
		const FString DeletedNodeGuid = GuidLower(Node->NodeGuid);
		TArray<FString> AfterPinKeys;
		for (const FString& Key : BeforePinKeys)
		{
			if (!Key.StartsWith(DeletedNodeGuid + TEXT("|"), ESearchCase::CaseSensitive))
			{
				AfterPinKeys.Add(Key);
			}
		}
		TArray<FString> AfterNodeKeys;
		for (const FString& Key : BeforeNodeKeys)
		{
			if (!Key.StartsWith(DeletedNodeGuid + TEXT("|"), ESearchCase::CaseSensitive))
			{
				AfterNodeKeys.Add(Key);
			}
		}
		if (!HashKeys(PinIdentityVersion, AfterPinKeys, OutPlan.PinIdentityHashAfter)
			|| !HashKeys(NodeIdentityVersion, AfterNodeKeys, OutPlan.NodeIdentityHashAfter))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("Could not hash the planned after identity sets."));
		}

		OutPlan.NodeCountAfter = OutPlan.NodeCountBefore - 1;
		OutPlan.LinkCountAfter = OutPlan.LinkCountBefore - LiveEdges.Num();

		FString Canonical;
		AppendField(Canonical, OutPlan.AssetPath);
		AppendField(Canonical, OutPlan.GraphGuid);
		AppendField(Canonical, OutPlan.NodeGuid);
		AppendField(Canonical, OutPlan.NodeClass);
		AppendField(Canonical, OutPlan.PackageRevision);
		AppendField(Canonical, OutPlan.GraphFingerprint);
		AppendField(Canonical, OutPlan.EdgeSetHashBefore);
		AppendField(Canonical, OutPlan.EdgeSetHashAfter);
		AppendField(Canonical, OutPlan.UntouchedEdgesHash);
		AppendField(Canonical, OutPlan.PinIdentityHashBefore);
		AppendField(Canonical, OutPlan.PinIdentityHashAfter);
		AppendField(Canonical, OutPlan.NodeIdentityHashBefore);
		AppendField(Canonical, OutPlan.NodeIdentityHashAfter);
		AppendField(Canonical, FString::FromInt(OutPlan.NodeCountBefore));
		AppendField(Canonical, FString::FromInt(OutPlan.NodeCountAfter));
		AppendField(Canonical, FString::FromInt(OutPlan.LinkCountBefore));
		AppendField(Canonical, FString::FromInt(OutPlan.LinkCountAfter));
		AppendField(Canonical, FString::FromInt(OutPlan.DeletedNodePinCount));
		TArray<FString> SortedEdgeKeys = LiveEdgeKeys.Array();
		SortedEdgeKeys.Sort();
		for (const FString& Key : SortedEdgeKeys)
		{
			AppendField(Canonical, Key);
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-invalid-identifier"),
				TEXT("Could not compute the node-removal plan hash."));
		}
		OutPlan.PlanId = FString(TEXT("sha256:")) + Digest.ToLower();

		UE_LOG(
			LogUEAgentKitBlueprintNodeRemoveExperimental,
			Display,
			TEXT("Node-removal plan node=%s class=%s planId=%s revision=%s edges=%d->%d nodes=%d->%d removedEdges=%d pins=%s"),
			*OutPlan.NodeGuid,
			*OutPlan.NodeClass,
			*OutPlan.PlanId,
			*OutPlan.PackageRevision,
			OutPlan.LinkCountBefore,
			OutPlan.LinkCountAfter,
			OutPlan.NodeCountBefore,
			OutPlan.NodeCountAfter,
			LiveEdges.Num(),
			*OutPlan.PinIdentityHashAfter);
		return true;
	}

	TSharedPtr<FJsonObject> EdgeToJson(const FEdgeRef& Edge)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("fromNodeGuid"), GuidLower(Edge.From.NodeGuid));
		Item->SetStringField(TEXT("fromPinName"), Edge.From.PinName);
		Item->SetStringField(TEXT("fromPinGuid"), GuidLower(Edge.From.PinGuid));
		Item->SetStringField(TEXT("fromKind"), PinKindLabel(Edge.From.Kind));
		Item->SetStringField(TEXT("toNodeGuid"), GuidLower(Edge.To.NodeGuid));
		Item->SetStringField(TEXT("toPinName"), Edge.To.PinName);
		Item->SetStringField(TEXT("toPinGuid"), GuidLower(Edge.To.PinGuid));
		Item->SetStringField(TEXT("toKind"), PinKindLabel(Edge.To.Kind));
		return Item;
	}

	bool BuildPreviewResult(
		const FRemovePlan& Plan,
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
		Preview->SetBoolField(TEXT("skeletonRegenerated"), false);
		Preview->SetBoolField(TEXT("packageDirty"), false);
		Preview->SetStringField(TEXT("planId"), Plan.PlanId);
		Preview->SetStringField(TEXT("assetPath"), Plan.AssetPath);
		Preview->SetStringField(TEXT("graphGuid"), Plan.GraphGuid);
		Preview->SetStringField(TEXT("nodeGuid"), Plan.NodeGuid);
		Preview->SetStringField(TEXT("nodeClass"), Plan.NodeClass);
		Preview->SetStringField(TEXT("nodeTitle"), Plan.NodeTitle);
		Preview->SetStringField(TEXT("packageRevision"), Plan.PackageRevision);
		Preview->SetStringField(TEXT("graphFingerprint"), Plan.GraphFingerprint);
		Preview->SetStringField(TEXT("edgeSetHashBefore"), Plan.EdgeSetHashBefore);
		Preview->SetStringField(TEXT("edgeSetHashAfter"), Plan.EdgeSetHashAfter);
		Preview->SetStringField(TEXT("untouchedEdgesHash"), Plan.UntouchedEdgesHash);
		Preview->SetStringField(TEXT("pinIdentityHashBefore"), Plan.PinIdentityHashBefore);
		Preview->SetStringField(TEXT("pinIdentityHashAfter"), Plan.PinIdentityHashAfter);
		Preview->SetStringField(TEXT("nodeIdentityHashAfter"), Plan.NodeIdentityHashAfter);
		Preview->SetNumberField(TEXT("nodePinCount"), Plan.DeletedNodePinCount);
		Preview->SetNumberField(TEXT("nodeCountBefore"), Plan.NodeCountBefore);
		Preview->SetNumberField(TEXT("nodeCountAfter"), Plan.NodeCountAfter);
		Preview->SetNumberField(TEXT("linkCountBefore"), Plan.LinkCountBefore);
		Preview->SetNumberField(TEXT("linkCountAfter"), Plan.LinkCountAfter);
		Preview->SetNumberField(
			TEXT("removedEdgeCount"),
			Plan.RemovedEdges.Num());
		Preview->SetNumberField(
			TEXT("untouchedEdgeCount"),
			Plan.LinkCountBefore - Plan.RemovedEdges.Num());
		Preview->SetBoolField(TEXT("isolatedNode"), Plan.RemovedEdges.Num() == 0);
		Preview->SetNumberField(TEXT("maxNodesPerPlan"), MaxNodesPerPlan);
		Preview->SetNumberField(TEXT("maxIncidentEdges"), MaxIncidentEdges);

		TArray<TSharedPtr<FJsonValue>> RemovedEdges;
		for (const FEdgeRef& Edge : Plan.RemovedEdges)
		{
			RemovedEdges.Add(MakeShared<FJsonValueObject>(EdgeToJson(Edge)));
		}
		Preview->SetArrayField(TEXT("removedEdges"), RemovedEdges);

		TArray<TSharedPtr<FJsonValue>> RemovableClasses;
		RemovableClasses.Add(MakeShared<FJsonValueString>(CallFunctionClassPath));
		RemovableClasses.Add(MakeShared<FJsonValueString>(BranchClassPath));
		Preview->SetArrayField(TEXT("removableNodeClasses"), RemovableClasses);
		TArray<TSharedPtr<FJsonValue>> RemovableFunctions;
		for (const FString& Name : AllowlistedFunctionNames())
		{
			RemovableFunctions.Add(MakeShared<FJsonValueString>(Name));
		}
		RemovableFunctions.Sort([](const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right)
		{
			return Left->AsString() < Right->AsString();
		});
		Preview->SetArrayField(TEXT("removableFunctions"), RemovableFunctions);
		Preview->SetStringField(
			TEXT("requiredConfirmation"),
			FString(UEAgentKitBlueprintNodeRemoveExperimental::ApplyConfirmationPrefix)
				+ Plan.PlanId);

		OutResult = Preview;
		return true;
	}

	bool VerifyAfterState(
		const FRemovePlan& Plan,
		FString& OutEdgeHash,
		FString& OutPinHash,
		FString& OutNodeHash,
		int32& OutNodeCount,
		int32& OutLinkCount)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		FString Code;
		FString Message;
		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
				Plan.AssetPath,
				Plan.GraphGuid,
				Blueprint,
				Graph,
				Code,
				Message))
		{
			return false;
		}
		FGuid NodeGuid;
		IsCanonicalGuid(Plan.NodeGuid, NodeGuid);
		if (FindNode(Graph, NodeGuid) != nullptr)
		{
			return false;
		}
		TArray<FString> EdgeKeys;
		BuildEdgeKeys(Graph, EdgeKeys);
		OutLinkCount = EdgeKeys.Num();
		OutNodeCount = Graph->Nodes.Num();
		TArray<FString> PinKeys;
		BuildPinIdentityKeys(Graph, PinKeys);
		TArray<FString> NodeKeys;
		BuildNodeIdentityKeys(Graph, NodeKeys);
		return HashKeys(EdgeSetVersion, EdgeKeys, OutEdgeHash)
			&& HashKeys(PinIdentityVersion, PinKeys, OutPinHash)
			&& HashKeys(NodeIdentityVersion, NodeKeys, OutNodeHash)
			&& OutEdgeHash.Equals(Plan.EdgeSetHashAfter, ESearchCase::CaseSensitive)
			&& OutPinHash.Equals(Plan.PinIdentityHashAfter, ESearchCase::CaseSensitive)
			&& OutNodeHash.Equals(Plan.NodeIdentityHashAfter, ESearchCase::CaseSensitive)
			&& OutNodeCount == Plan.NodeCountAfter
			&& OutLinkCount == Plan.LinkCountAfter;
	}

	bool VerifyBeforeState(
		const FRemovePlan& Plan,
		FString& OutEdgeHash,
		FString& OutPinHash,
		FString& OutNodeHash,
		int32& OutNodeCount,
		int32& OutLinkCount)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		FString Code;
		FString Message;
		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
				Plan.AssetPath,
				Plan.GraphGuid,
				Blueprint,
				Graph,
				Code,
				Message))
		{
			return false;
		}
		FGuid NodeGuid;
		IsCanonicalGuid(Plan.NodeGuid, NodeGuid);
		UEdGraphNode* Node = FindNode(Graph, NodeGuid);
		if (Node == nullptr || Node->GetClass() == nullptr
			|| !Node->GetClass()->GetPathName().Equals(Plan.NodeClass, ESearchCase::CaseSensitive)
			|| Node->Pins.Num() != Plan.DeletedNodePinCount)
		{
			return false;
		}
		for (const FEdgeRef& Edge : Plan.RemovedEdges)
		{
			// Undo may restore new UObject instances for the node/pins, so always
			// resolve the planned identities by GUID in the restored graph.
			UEdGraphNode* FromNode = FindNode(Graph, Edge.From.NodeGuid);
			UEdGraphNode* ToNode = FindNode(Graph, Edge.To.NodeGuid);
			UEdGraphPin* FromPin = FromNode != nullptr
				? FindPinByGuid(FromNode, Edge.From.PinGuid)
				: nullptr;
			UEdGraphPin* ToPin = ToNode != nullptr
				? FindPinByGuid(ToNode, Edge.To.PinGuid)
				: nullptr;
			if (FromPin == nullptr || ToPin == nullptr || !FromPin->LinkedTo.Contains(ToPin))
			{
				return false;
			}
		}
		TArray<FString> EdgeKeys;
		BuildEdgeKeys(Graph, EdgeKeys);
		OutLinkCount = EdgeKeys.Num();
		OutNodeCount = Graph->Nodes.Num();
		TArray<FString> PinKeys;
		BuildPinIdentityKeys(Graph, PinKeys);
		TArray<FString> NodeKeys;
		BuildNodeIdentityKeys(Graph, NodeKeys);
		return HashKeys(EdgeSetVersion, EdgeKeys, OutEdgeHash)
			&& HashKeys(PinIdentityVersion, PinKeys, OutPinHash)
			&& HashKeys(NodeIdentityVersion, NodeKeys, OutNodeHash)
			&& OutEdgeHash.Equals(Plan.EdgeSetHashBefore, ESearchCase::CaseSensitive)
			&& OutPinHash.Equals(Plan.PinIdentityHashBefore, ESearchCase::CaseSensitive)
			&& OutNodeHash.Equals(Plan.NodeIdentityHashBefore, ESearchCase::CaseSensitive)
			&& OutNodeCount == Plan.NodeCountBefore
			&& OutLinkCount == Plan.LinkCountBefore;
	}

	bool RollbackCommitted(
		const FRemovePlan& Plan,
		bool& bOutUndoUsed,
		FString& OutRestoredEdgeHash)
	{
		bOutUndoUsed = GEditor != nullptr && GEditor->UndoTransaction();
		if (!bOutUndoUsed)
		{
			return false;
		}
		FString PinHash;
		FString NodeHash;
		int32 NodeCount = 0;
		int32 LinkCount = 0;
		return VerifyBeforeState(
			Plan,
			OutRestoredEdgeHash,
			PinHash,
			NodeHash,
			NodeCount,
			LinkCount);
	}
}

bool UEAgentKitBlueprintNodeRemoveExperimental::TryComputeLiveEdgeSetHash(
	UEdGraph* Graph,
	FString& OutHash)
{
	if (Graph == nullptr)
	{
		return false;
	}
	TArray<FString> Keys;
	BuildEdgeKeys(Graph, Keys);
	return HashKeys(EdgeSetVersion, Keys, OutHash);
}

bool UEAgentKitBlueprintNodeRemoveExperimental::IsExperimentalWriteAvailable()
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

void UEAgentKitBlueprintNodeRemoveExperimental::SetExperimentalWriteEnabledForTest(bool bEnabled)
{
	bTestOverrideSet = true;
	bTestOverrideEnabled = bEnabled;
}

void UEAgentKitBlueprintNodeRemoveExperimental::ResetExperimentalWriteForTest()
{
	bTestOverrideSet = false;
	bTestOverrideEnabled = false;
	RecentRemoves.Reset();
}

bool UEAgentKitBlueprintNodeRemoveExperimental::TryPreviewExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	FRemovePlan Plan;
	FPlanContext Context;
	if (!BuildPlan(Params, Plan, Context, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	return BuildPreviewResult(Plan, OutResult, OutErrorCode, OutErrorMessage);
}

bool UEAgentKitBlueprintNodeRemoveExperimental::TryApplyExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	static const TSet<FString> Allowed = {
		TEXT("assetPath"),
		TEXT("graphGuid"),
		TEXT("nodeGuid"),
		TEXT("expectedNodeClass"),
		TEXT("expectedEdgeSetHash"),
		TEXT("removedEdges"),
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
			TEXT("graph-remove-invalid-identifier"),
			TEXT("planId must be the exact sha256 plan id from Preview."));
	}
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintNodeRemoveExperimental::ApplyConfirmationPrefix)
		+ RequestedPlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-confirmation-required"),
			FString::Printf(TEXT("Apply requires the exact confirmation: %s"), *ExpectedConfirmation));
	}

	FRemovePlan Plan;
	FPlanContext Context;
	if (!BuildPlan(Params, Plan, Context, OutErrorCode, OutErrorMessage, true))
	{
		return false;
	}
	if (!Plan.PlanId.Equals(RequestedPlanId, ESearchCase::CaseSensitive))
	{
		UE_LOG(
			LogUEAgentKitBlueprintNodeRemoveExperimental,
			Warning,
			TEXT("Node-removal plan stale: requested=%s computed=%s"),
			*RequestedPlanId,
			*Plan.PlanId);
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-plan-stale"),
			TEXT("The Blueprint node-removal plan is stale; run Preview again."));
	}

	UEdGraph* Graph = Context.Graph;
	const int32 NodeCountBefore = Plan.NodeCountBefore;
	const int32 LinkCountBefore = Plan.LinkCountBefore;

	{
		FScopedTransaction Transaction(
			FText::FromString(TEXT("UEAgentKit Remove Blueprint Node")));
		Context.Blueprint->Modify();
		Graph->Modify();

		auto Abort = [&OutErrorCode, &OutErrorMessage, &Transaction](
			const TCHAR* Code,
			const FString& Message) -> bool
		{
			Transaction.Cancel();
			return Fail(OutErrorCode, OutErrorMessage, Code, Message);
		};

		FGuid NodeGuid;
		IsCanonicalGuid(Plan.NodeGuid, NodeGuid);
		UEdGraphNode* Node = FindNode(Graph, NodeGuid);
		if (Node == nullptr)
		{
			return Abort(
				TEXT("graph-remove-node-not-found"),
				TEXT("The planned node disappeared before the transaction could remove it."));
		}
		Node->Modify();

		TArray<FEdgeRef> LiveEdges;
		FString Reason;
		if (!GatherIncidentEdges(Node, LiveEdges, Reason)
			|| LiveEdges.Num() != Plan.RemovedEdges.Num()
			|| LiveEdges.Num() > MaxIncidentEdges)
		{
			return Abort(
				TEXT("graph-remove-removed-edges-mismatch"),
				TEXT("The planned incident edges changed before the transaction could remove them."));
		}
		for (int32 Index = 0; Index < LiveEdges.Num(); ++Index)
		{
			if (!MakeEdgeKey(LiveEdges[Index].From.Pin, LiveEdges[Index].To.Pin).Equals(
					MakeEdgeKey(Plan.RemovedEdges[Index].From.Pin, Plan.RemovedEdges[Index].To.Pin),
					ESearchCase::CaseSensitive))
			{
				return Abort(
					TEXT("graph-remove-removed-edges-mismatch"),
					TEXT("The planned incident edge set changed before it could be removed."));
			}
		}

		for (const FEdgeRef& Edge : Plan.RemovedEdges)
		{
			Edge.From.Node->Modify();
			Edge.To.Node->Modify();
			Edge.From.Pin->BreakLinkTo(Edge.To.Pin);
			Edge.From.Node->PinConnectionListChanged(Edge.From.Pin);
			Edge.To.Node->PinConnectionListChanged(Edge.To.Pin);
		}
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr && Pin->LinkedTo.Num() != 0)
			{
				return Abort(
					TEXT("graph-remove-break-failed"),
					TEXT("The planned node still has a link that was not enumerated; refusing to delete it."));
			}
		}

		FBlueprintEditorUtils::RemoveNode(Context.Blueprint, Node, true);
		if (FindNode(Graph, NodeGuid) != nullptr)
		{
			return Abort(
				TEXT("graph-remove-delete-failed"),
				TEXT("The planned node is still present after RemoveNode."));
		}

		FBlueprintEditorUtils::MarkBlueprintAsModified(Context.Blueprint);
		Context.Blueprint->GetOutermost()->MarkPackageDirty();
	}

	FString AfterEdgeHash;
	FString AfterPinHash;
	FString AfterNodeHash;
	int32 AfterNodeCount = 0;
	int32 AfterLinkCount = 0;
	const bool bPostVerified = VerifyAfterState(
		Plan,
		AfterEdgeHash,
		AfterPinHash,
		AfterNodeHash,
		AfterNodeCount,
		AfterLinkCount);
	if (!bPostVerified)
	{
		bool bUndoUsed = false;
		FString RestoredHash;
		const bool bRestored = RollbackCommitted(Plan, bUndoUsed, RestoredHash);
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-post-commit-mismatch"),
			FString::Printf(
				TEXT("The committed removal did not match the plan (edge=%s pinIdentity=%s nodeIdentity=%s nodes=%d links=%d); restored=%s viaUndo=%s restoredEdge=%s."),
				*AfterEdgeHash,
				*AfterPinHash,
				*AfterNodeHash,
				AfterNodeCount,
				AfterLinkCount,
				bRestored ? TEXT("true") : TEXT("false"),
				bUndoUsed ? TEXT("true") : TEXT("false"),
				*RestoredHash));
	}

	const bool bUndoCalled = GEditor != nullptr && GEditor->UndoTransaction();
	FString UndoEdgeHash;
	FString UndoPinHash;
	FString UndoNodeHash;
	int32 UndoNodeCount = 0;
	int32 UndoLinkCount = 0;
	const bool bUndoVerified = bUndoCalled
		&& VerifyBeforeState(
			Plan,
			UndoEdgeHash,
			UndoPinHash,
			UndoNodeHash,
			UndoNodeCount,
			UndoLinkCount);
	const bool bRedoCalled = bUndoVerified && GEditor != nullptr && GEditor->RedoTransaction();
	FString RedoEdgeHash;
	FString RedoPinHash;
	FString RedoNodeHash;
	int32 RedoNodeCount = 0;
	int32 RedoLinkCount = 0;
	const bool bRedoVerified = bRedoCalled
		&& VerifyAfterState(
			Plan,
			RedoEdgeHash,
			RedoPinHash,
			RedoNodeHash,
			RedoNodeCount,
			RedoLinkCount);
	if (!bUndoVerified || !bRedoVerified)
	{
		// Never undo twice: a second UndoTransaction would revert an unrelated
		// earlier transaction.  When Undo failed the state is unknown; report it
		// as fail-closed and leave the package dirty for explicit recovery.
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-undo-redo-failed"),
			FString::Printf(
				TEXT("Undo/Redo did not restore the exact node/pin/edge sets (undo=%s redo=%s undoEdge=%s redoEdge=%s undoNodes=%d redoNodes=%d)."),
				bUndoVerified ? TEXT("true") : TEXT("false"),
				bRedoVerified ? TEXT("true") : TEXT("false"),
				*UndoEdgeHash,
				*RedoEdgeHash,
				UndoNodeCount,
				RedoNodeCount));
	}

	FRemoveReceipt Receipt;
	Receipt.AssetPath = Plan.AssetPath;
	Receipt.GraphGuid = Plan.GraphGuid;
	Receipt.NodeGuid = Plan.NodeGuid;
	Receipt.NodeClass = Plan.NodeClass;
	Receipt.PackageRevision = Plan.PackageRevision;
	Receipt.GraphFingerprint = Plan.GraphFingerprint;
	Receipt.EdgeSetHashBefore = Plan.EdgeSetHashBefore;
	Receipt.EdgeSetHashAfter = Plan.EdgeSetHashAfter;
	Receipt.PinIdentityHashAfter = Plan.PinIdentityHashAfter;
	Receipt.NodeIdentityHashAfter = Plan.NodeIdentityHashAfter;
	Receipt.NodeCountBefore = Plan.NodeCountBefore;
	Receipt.NodeCountAfter = Plan.NodeCountAfter;
	Receipt.LinkCountBefore = Plan.LinkCountBefore;
	Receipt.LinkCountAfter = Plan.LinkCountAfter;
	for (const FEdgeRef& Edge : Plan.RemovedEdges)
	{
		Receipt.RemovedEdgeKeys.Add(MakeEdgeKey(Edge.From.Pin, Edge.To.Pin));
	}
	RecentRemoves.Add(Plan.PlanId, Receipt);

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
	Result->SetStringField(TEXT("nodeGuid"), Plan.NodeGuid);
	Result->SetStringField(TEXT("nodeClass"), Plan.NodeClass);
	Result->SetStringField(TEXT("nodeTitle"), Plan.NodeTitle);
	Result->SetStringField(TEXT("packageRevision"), Plan.PackageRevision);
	Result->SetStringField(TEXT("graphFingerprint"), Plan.GraphFingerprint);
	Result->SetStringField(TEXT("edgeSetHashBefore"), Plan.EdgeSetHashBefore);
	Result->SetStringField(TEXT("edgeSetHashAfter"), Plan.EdgeSetHashAfter);
	Result->SetStringField(TEXT("pinIdentityHashBefore"), Plan.PinIdentityHashBefore);
	Result->SetStringField(TEXT("pinIdentityHashAfter"), Plan.PinIdentityHashAfter);
	Result->SetStringField(TEXT("nodeIdentityHashAfter"), Plan.NodeIdentityHashAfter);
	Result->SetNumberField(TEXT("nodeCountBefore"), Plan.NodeCountBefore);
	Result->SetNumberField(TEXT("nodeCountAfter"), Plan.NodeCountAfter);
	Result->SetNumberField(TEXT("nodeCount"), Plan.NodeCountAfter);
	Result->SetNumberField(TEXT("linkCountBefore"), Plan.LinkCountBefore);
	Result->SetNumberField(TEXT("linkCountAfter"), Plan.LinkCountAfter);
	Result->SetNumberField(TEXT("linkCount"), Plan.LinkCountAfter);
	Result->SetNumberField(TEXT("removedEdgeCount"), Plan.RemovedEdges.Num());
	Result->SetNumberField(TEXT("nodePinCount"), Plan.DeletedNodePinCount);
	TArray<TSharedPtr<FJsonValue>> RemovedEdges;
	for (const FEdgeRef& Edge : Plan.RemovedEdges)
	{
		RemovedEdges.Add(MakeShared<FJsonValueObject>(EdgeToJson(Edge)));
	}
	Result->SetArrayField(TEXT("removedEdges"), RemovedEdges);
	Result->SetStringField(
		TEXT("requiredSaveConfirmation"),
		FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix) + Plan.PlanId);

	OutResult = Result;
	return true;
}

bool UEAgentKitBlueprintNodeRemoveExperimental::TrySaveExperimental(
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
		|| !ReadString(
			Params,
			TEXT("expectedEdgeSetHashAfter"),
			ExpectedEdgeSetHashAfter,
			OutErrorCode,
			OutErrorMessage)
		|| !ReadString(
			Params,
			TEXT("expectedPackageRevision"),
			ExpectedPackageRevision,
			OutErrorCode,
			OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage)
		|| !Params->TryGetNumberField(TEXT("expectedNodeCount"), NodeCountNumber)
		|| !Params->TryGetNumberField(TEXT("expectedLinkCount"), LinkCountNumber))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-parameters"),
			TEXT("Save requires assetPath, graphGuid, planId, expectedEdgeSetHashAfter, expectedPackageRevision, expectedNodeCount, expectedLinkCount and confirmation."));
	}
	if (!IsSha256(PlanId) || !IsSha256(ExpectedEdgeSetHashAfter)
		|| !IsSha256(ExpectedPackageRevision))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-identifier"),
			TEXT("planId, expectedEdgeSetHashAfter and expectedPackageRevision must be sha256 identifiers."));
	}
	const int32 ExpectedNodeCount = static_cast<int32>(NodeCountNumber);
	const int32 ExpectedLinkCount = static_cast<int32>(LinkCountNumber);
	const FString ExpectedConfirmation =
		FString(UEAgentKitBlueprintNodeRemoveExperimental::SaveConfirmationPrefix) + PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-confirmation-required"),
			FString::Printf(TEXT("Save requires the exact confirmation: %s"), *ExpectedConfirmation));
	}
	if (!UEAgentKitBlueprintNodeRemoveExperimental::IsExperimentalWriteAvailable())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-experimental-disabled"),
			TEXT("Experimental Blueprint node removal is not opted in for this project."));
	}
	if (GEditor == nullptr || GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-pie-active"),
			TEXT("Experimental Blueprint node removal is unavailable while PIE or SIE is active."));
	}

	const FRemoveReceipt* Receipt = RecentRemoves.Find(PlanId);
	if (Receipt == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-requires-apply"),
			TEXT("Save requires the successful Apply receipt from this Editor process."));
	}
	if (!Receipt->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive)
		|| !Receipt->GraphGuid.Equals(GraphGuid, ESearchCase::CaseSensitive)
		|| !Receipt->EdgeSetHashAfter.Equals(
			ExpectedEdgeSetHashAfter,
			ESearchCase::CaseSensitive)
		|| !Receipt->PackageRevision.Equals(
			ExpectedPackageRevision,
			ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-plan-mismatch"),
			TEXT("The Save request does not match the Apply receipt."));
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
			TEXT("graph-remove-focused-graph-mismatch"),
			TEXT("The focused Blueprint editor is not showing the exact asset/graph being saved."));
	}
	if (!Preflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-not-dirty"),
			TEXT("Save requires the dirty package produced by the Apply receipt."));
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

	const FString Filename = FPackageName::LongPackageNameToFilename(
		Blueprint->GetOutermost()->GetName(),
		FPackageName::GetAssetPackageExtension());
	FString DiskDigestBefore;
	if (!FBlueprintContextSha256::HashFile(Filename, DiskDigestBefore))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-revision-mismatch"),
			FString::Printf(TEXT("Could not hash the on-disk package %s."), *Filename));
	}
	const FString DiskRevisionBefore = FString(TEXT("sha256:")) + DiskDigestBefore.ToLower();
	if (!DiskRevisionBefore.Equals(ExpectedPackageRevision, ESearchCase::CaseSensitive)
		|| !DiskRevisionBefore.Equals(Receipt->PackageRevision, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-revision-mismatch"),
			TEXT("The on-disk package revision changed since Apply."));
	}

	FString LiveEdgeHash;
	FString LivePinHash;
	FString LiveNodeHash;
	int32 LiveNodeCount = 0;
	int32 LiveLinkCount = 0;
	{
		FString Code;
		FString Message;
		UBlueprint* LiveBlueprint = nullptr;
		UEdGraph* LiveGraph = nullptr;
		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
				AssetPath,
				GraphGuid,
				LiveBlueprint,
				LiveGraph,
				Code,
				Message))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-focused-graph-mismatch"),
				Message);
		}
		FGuid NodeGuid;
		IsCanonicalGuid(Receipt->NodeGuid, NodeGuid);
		if (FindNode(LiveGraph, NodeGuid) != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-save-node-mismatch"),
				TEXT("The removed node is present again; refusing to save."));
		}
		TArray<FString> EdgeKeys;
		BuildEdgeKeys(LiveGraph, EdgeKeys);
		TArray<FString> PinKeys;
		BuildPinIdentityKeys(LiveGraph, PinKeys);
		TArray<FString> NodeKeys;
		BuildNodeIdentityKeys(LiveGraph, NodeKeys);
		if (!HashKeys(EdgeSetVersion, EdgeKeys, LiveEdgeHash)
			|| !HashKeys(PinIdentityVersion, PinKeys, LivePinHash)
			|| !HashKeys(NodeIdentityVersion, NodeKeys, LiveNodeHash))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-save-edge-set-mismatch"),
				TEXT("Could not hash the live graph for Save verification."));
		}
		LiveNodeCount = LiveGraph->Nodes.Num();
		LiveLinkCount = EdgeKeys.Num();
	}
	if (!LiveEdgeHash.Equals(Receipt->EdgeSetHashAfter, ESearchCase::CaseSensitive)
		|| !LivePinHash.Equals(Receipt->PinIdentityHashAfter, ESearchCase::CaseSensitive)
		|| !LiveNodeHash.Equals(Receipt->NodeIdentityHashAfter, ESearchCase::CaseSensitive)
		|| LiveNodeCount != Receipt->NodeCountAfter
		|| LiveLinkCount != Receipt->LinkCountAfter
		|| LiveNodeCount != ExpectedNodeCount
		|| LiveLinkCount != ExpectedLinkCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-edge-set-mismatch"),
			FString::Printf(
				TEXT("The live graph changed after Apply (edge=%s nodes=%d links=%d)."),
				*LiveEdgeHash,
				LiveNodeCount,
				LiveLinkCount));
	}

	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	if (!UPackage::SavePackage(Blueprint->GetOutermost(), Blueprint, *Filename, SaveArgs))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-failed"),
			FString::Printf(TEXT("UPackage::SavePackage failed for %s."), *Filename));
	}

	FString DiskDigestAfter;
	if (!FBlueprintContextSha256::HashFile(Filename, DiskDigestAfter))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-failed"),
			TEXT("The saved package could not be hashed back from disk."));
	}
	const FString DiskRevisionAfter = FString(TEXT("sha256:")) + DiskDigestAfter.ToLower();
	const int64 FileSizeAfter = IFileManager::Get().FileSize(*Filename);

	bool bReadbackVerified = true;
	{
		UBlueprint* ReadbackBlueprint = nullptr;
		UEdGraph* ReadbackGraph = nullptr;
		FString Code;
		FString Message;
		if (!UEAgentKitBlueprintGraphLayout::ResolveExactTarget(
				AssetPath,
				GraphGuid,
				ReadbackBlueprint,
				ReadbackGraph,
				Code,
				Message))
		{
			bReadbackVerified = false;
		}
		else
		{
			FGuid NodeGuid;
			IsCanonicalGuid(Receipt->NodeGuid, NodeGuid);
			TArray<FString> EdgeKeys;
			BuildEdgeKeys(ReadbackGraph, EdgeKeys);
			TArray<FString> PinKeys;
			BuildPinIdentityKeys(ReadbackGraph, PinKeys);
			FString EdgeHash;
			FString PinHash;
			bReadbackVerified = FindNode(ReadbackGraph, NodeGuid) == nullptr
				&& HashKeys(EdgeSetVersion, EdgeKeys, EdgeHash)
				&& HashKeys(PinIdentityVersion, PinKeys, PinHash)
				&& EdgeHash.Equals(Receipt->EdgeSetHashAfter, ESearchCase::CaseSensitive)
				&& PinHash.Equals(Receipt->PinIdentityHashAfter, ESearchCase::CaseSensitive)
				&& EdgeKeys.Num() == Receipt->LinkCountAfter
				&& ReadbackGraph->Nodes.Num() == Receipt->NodeCountAfter;
		}
	}
	if (!bReadbackVerified)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-readback-mismatch"),
			TEXT("The saved package did not read back as the exact planned node/edge state."));
	}

	TSharedPtr<FJsonObject> PostPreflight;
	FString PostCode;
	FString PostMessage;
	const bool bPostPreflight = UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		PostPreflight,
		PostCode,
		PostMessage)
		&& PostPreflight->GetBoolField(TEXT("packageDirty")) == false
		&& PostPreflight->GetStringField(TEXT("assetPath")).Equals(
			AssetPath,
			ESearchCase::CaseSensitive)
		&& PostPreflight->GetStringField(TEXT("graphGuid")).Equals(
			GraphGuid,
			ESearchCase::CaseSensitive)
		&& static_cast<int32>(PostPreflight->GetNumberField(TEXT("nodeCount")))
			== Receipt->NodeCountAfter;
	if (!bPostPreflight)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-save-postflight-failed"),
			FString::Printf(
				TEXT("The saved package preflight is inconsistent (%s: %s)."),
				*PostCode,
				*PostMessage));
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
	Result->SetStringField(TEXT("nodeGuid"), Receipt->NodeGuid);
	Result->SetStringField(TEXT("nodeClass"), Receipt->NodeClass);
	Result->SetStringField(TEXT("packageRevision"), ExpectedPackageRevision);
	Result->SetStringField(TEXT("assetSha256"), DiskRevisionAfter);
	Result->SetNumberField(TEXT("assetSizeBytes"), static_cast<double>(FileSizeAfter));
	Result->SetStringField(TEXT("edgeSetHashBefore"), Receipt->EdgeSetHashBefore);
	Result->SetStringField(TEXT("edgeSetHashAfter"), Receipt->EdgeSetHashAfter);
	Result->SetStringField(TEXT("pinIdentityHashAfter"), Receipt->PinIdentityHashAfter);
	Result->SetStringField(TEXT("nodeIdentityHashAfter"), Receipt->NodeIdentityHashAfter);
	Result->SetNumberField(TEXT("nodeCountBefore"), Receipt->NodeCountBefore);
	Result->SetNumberField(TEXT("nodeCountAfter"), Receipt->NodeCountAfter);
	Result->SetNumberField(TEXT("nodeCount"), Receipt->NodeCountAfter);
	Result->SetNumberField(TEXT("linkCountBefore"), Receipt->LinkCountBefore);
	Result->SetNumberField(TEXT("linkCountAfter"), Receipt->LinkCountAfter);
	Result->SetNumberField(TEXT("linkCount"), Receipt->LinkCountAfter);
	Result->SetNumberField(TEXT("removedEdgeCount"), Receipt->RemovedEdgeKeys.Num());
	TArray<TSharedPtr<FJsonValue>> RemovedEdges;
	for (const FString& Key : Receipt->RemovedEdgeKeys)
	{
		RemovedEdges.Add(MakeShared<FJsonValueString>(Key));
	}
	Result->SetArrayField(TEXT("removedEdges"), RemovedEdges);

	OutResult = Result;
	// Receipt points into the TMap, so keep it alive until every result
	// field copied its data; removing it before this point is a
	// use-after-free that silently corrupts the returned hashes.
	RecentRemoves.Remove(PlanId);
	return true;
}
