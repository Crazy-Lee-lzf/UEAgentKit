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
	const int32 MaxNodesPerPlan =
		UEAgentKitBlueprintNodeRemoveExperimental::MaxNodesPerPlan;
	const int32 MaxIncidentEdges =
		UEAgentKitBlueprintNodeRemoveExperimental::MaxIncidentEdgesPerNode;


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
			+ From->PinName.ToString().ToLower() + TEXT("|") + GuidLower(From->PinId) + TEXT(">")
			+ GuidLower(To->GetOwningNode()->NodeGuid) + TEXT("|")
			+ To->PinName.ToString().ToLower() + TEXT("|") + GuidLower(To->PinId);
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
					NodeGuid + TEXT("|") + Pin->PinName.ToString().ToLower() + TEXT("|")
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

	FString JoinKeys(const TArray<FString>& Keys)
	{
		TArray<FString> Sorted = Keys;
		Sorted.Sort();
		return FString::Join(Sorted, TEXT(", "));
	}

	bool MakeAtomicEdgeKey(
		const UEAgentKitBlueprintNodeRemoveExperimental::FAtomicRemovedEdge& Edge,
		FString& OutKey,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		FGuid Parsed;
		if (!IsCanonicalGuid(Edge.FromNodeGuid, Parsed)
			|| !IsCanonicalGuid(Edge.FromPinGuid, Parsed)
			|| !IsCanonicalGuid(Edge.ToNodeGuid, Parsed)
			|| !IsCanonicalGuid(Edge.ToPinGuid, Parsed)
			|| !IsSafePinName(Edge.FromPinName)
			|| !IsSafePinName(Edge.ToPinName))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-invalid"),
				TEXT("Each removedEdges entry needs canonical from/to node and pin GUIDs and safe pin names."));
		}
		OutKey = Edge.FromNodeGuid + TEXT("|") + Edge.FromPinName + TEXT("|") + Edge.FromPinGuid
			+ TEXT(">") + Edge.ToNodeGuid + TEXT("|") + Edge.ToPinName + TEXT("|")
			+ Edge.ToPinGuid;
		return true;
	}

	/** Resolve one declared edge's two live pins by node GUID + pin GUID + name. */
	bool FindAtomicEdgePins(
		UEdGraph* Graph,
		const UEAgentKitBlueprintNodeRemoveExperimental::FAtomicRemovedEdge& Edge,
		UEdGraphPin*& OutFromPin,
		UEdGraphPin*& OutToPin)
	{
		OutFromPin = nullptr;
		OutToPin = nullptr;
		FGuid FromNodeGuid;
		FGuid FromPinGuid;
		FGuid ToNodeGuid;
		FGuid ToPinGuid;
		if (Graph == nullptr
			|| !IsCanonicalGuid(Edge.FromNodeGuid, FromNodeGuid)
			|| !IsCanonicalGuid(Edge.FromPinGuid, FromPinGuid)
			|| !IsCanonicalGuid(Edge.ToNodeGuid, ToNodeGuid)
			|| !IsCanonicalGuid(Edge.ToPinGuid, ToPinGuid))
		{
			return false;
		}
		UEdGraphNode* FromNode = FindNode(Graph, FromNodeGuid);
		UEdGraphNode* ToNode = FindNode(Graph, ToNodeGuid);
		if (FromNode == nullptr || ToNode == nullptr)
		{
			return false;
		}
		OutFromPin = FindPinByGuid(FromNode, FromPinGuid);
		OutToPin = FindPinByGuid(ToNode, ToPinGuid);
		if (OutFromPin == nullptr || OutToPin == nullptr)
		{
			return false;
		}
		// Both pins were resolved by GUID above; the names are only cross-checked.
		// FName equality ignores case, and a wildcard node that rebuilt its pins
		// can print "Option 0" where the plan recorded "option 0".
		return OutFromPin->PinName.ToString().Equals(Edge.FromPinName, ESearchCase::IgnoreCase)
			&& OutToPin->PinName.ToString().Equals(Edge.ToPinName, ESearchCase::IgnoreCase);
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

bool UEAgentKitBlueprintNodeRemoveExperimental::TryResolveAtomicRemoveOperation(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	FAtomicRemoveOperation& InOutOperation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-parameters"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	if (Cast<UEdGraphSchema_K2>(Graph->GetSchema()) == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-node-type-not-allowed"),
			TEXT("The focused graph is not a K2 graph."));
	}
	FGuid NodeGuid;
	if (!IsCanonicalGuid(InOutOperation.NodeGuid, NodeGuid))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-identifier"),
			TEXT("nodeGuid must be one canonical lower-case GUID."));
	}
	UEdGraphNode* Node = FindNode(Graph, NodeGuid);
	if (Node == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-node-not-found"),
			FString::Printf(TEXT("Node %s is not in the focused graph."), *InOutOperation.NodeGuid));
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
				*InOutOperation.NodeGuid,
				*ActualClass,
				*Reason));
	}
	if (!ActualClass.Equals(InOutOperation.NodeClass, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-node-class-mismatch"),
			FString::Printf(
				TEXT("Node %s is %s, not the requested %s."),
				*InOutOperation.NodeGuid,
				*ActualClass,
				*InOutOperation.NodeClass));
	}
	for (UEdGraphPin* Pin : Node->Pins)
	{
		if (Pin == nullptr)
		{
			continue;
		}
		// A function-call node carries an unlinked hidden 'self' pin; a hidden or
		// non-connectable pin is harmless only while it has no links.
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
						*InOutOperation.NodeGuid,
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
					*InOutOperation.NodeGuid,
					*Pin->PinName.ToString(),
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
				*InOutOperation.NodeGuid,
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
				*InOutOperation.NodeGuid,
				LiveEdges.Num(),
				MaxIncidentEdges));
	}
	// The caller must enumerate every incident edge exactly; the patch verifies
	// that against the live graph instead of trusting the request.
	TSet<FString> RequestedKeys;
	for (const FAtomicRemovedEdge& Edge : InOutOperation.RemovedEdges)
	{
		FString Key;
		if (!MakeAtomicEdgeKey(Edge, Key, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (RequestedKeys.Contains(Key))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-invalid"),
				TEXT("removedEdges must not contain duplicate entries."));
		}
		RequestedKeys.Add(Key);
	}
	TSet<FString> LiveKeys;
	for (const FEdgeRef& Edge : LiveEdges)
	{
		LiveKeys.Add(MakeEdgeKey(Edge.From.Pin, Edge.To.Pin));
	}
	if (LiveKeys.Num() != RequestedKeys.Num()
		|| !LiveKeys.Includes(RequestedKeys)
		|| !RequestedKeys.Includes(LiveKeys))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-removed-edges-mismatch"),
			FString::Printf(
				TEXT("removedEdges does not exactly match the node's live incident edges. Live=[%s] requested=[%s]."),
				*JoinKeys(LiveKeys.Array()),
				*JoinKeys(RequestedKeys.Array())));
	}
	// Fill the resolved identity back into the caller's list, in its own order.
	for (FAtomicRemovedEdge& Edge : InOutOperation.RemovedEdges)
	{
		FPinRef FromRef;
		FPinRef ToRef;
		FString PinReason;
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		if (!FindAtomicEdgePins(Graph, Edge, FromPin, ToPin)
			|| !MakePinRef(FromPin, FromRef, PinReason)
			|| !MakePinRef(ToPin, ToRef, PinReason))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-mismatch"),
				TEXT("A declared removedEdges entry no longer resolves to its live pins."));
		}
		Edge.FromNodeGuid = GuidLower(FromRef.NodeGuid);
		Edge.FromPinName = FromRef.PinName;
		Edge.FromPinGuid = GuidLower(FromRef.PinGuid);
		Edge.FromKind = PinKindLabel(FromRef.Kind);
		Edge.ToNodeGuid = GuidLower(ToRef.NodeGuid);
		Edge.ToPinName = ToRef.PinName;
		Edge.ToPinGuid = GuidLower(ToRef.PinGuid);
		Edge.ToKind = PinKindLabel(ToRef.Kind);
	}
	InOutOperation.NodeClass = ActualClass;
	InOutOperation.NodeTitle = Node->GetNodeTitle(ENodeTitleType::ListView).ToString();
	InOutOperation.NodePinCount = Node->Pins.Num();
	return true;
}

bool UEAgentKitBlueprintNodeRemoveExperimental::TryApplyAtomicRemoveOperation(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	FAtomicRemoveOperation& InOutOperation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-parameters"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	FGuid NodeGuid;
	if (!IsCanonicalGuid(InOutOperation.NodeGuid, NodeGuid))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-identifier"),
			TEXT("nodeGuid must be one canonical lower-case GUID."));
	}
	UEdGraphNode* Node = FindNode(Graph, NodeGuid);
	if (Node == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-node-not-found"),
			TEXT("The planned node disappeared before the transaction could remove it."));
	}
	Node->Modify();

	// Re-prove the incident edge set immediately before the break, exactly like
	// the writer's own Apply: same count, and each declared edge must be one of
	// the node's live edges.
	TArray<FEdgeRef> LiveEdges;
	FString Reason;
	if (!GatherIncidentEdges(Node, LiveEdges, Reason)
		|| LiveEdges.Num() != InOutOperation.RemovedEdges.Num())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-removed-edges-mismatch"),
			TEXT("The planned incident edges changed before the transaction could remove them."));
	}
	for (const FAtomicRemovedEdge& Edge : InOutOperation.RemovedEdges)
	{
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		bool bIsLiveEdge = false;
		if (FindAtomicEdgePins(Graph, Edge, FromPin, ToPin))
		{
			for (const FEdgeRef& Live : LiveEdges)
			{
				if (Live.From.Pin == FromPin && Live.To.Pin == ToPin)
				{
					bIsLiveEdge = true;
					break;
				}
			}
		}
		if (!bIsLiveEdge)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-mismatch"),
				TEXT("The planned incident edge set changed before it could be removed."));
		}
	}
	for (const FAtomicRemovedEdge& Edge : InOutOperation.RemovedEdges)
	{
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		if (!FindAtomicEdgePins(Graph, Edge, FromPin, ToPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-mismatch"),
				TEXT("A planned incident edge no longer resolves to its live pins."));
		}
		FromPin->GetOwningNode()->Modify();
		ToPin->GetOwningNode()->Modify();
		FromPin->BreakLinkTo(ToPin);
		FromPin->GetOwningNode()->PinConnectionListChanged(FromPin);
		ToPin->GetOwningNode()->PinConnectionListChanged(ToPin);
	}
	for (UEdGraphPin* Pin : Node->Pins)
	{
		if (Pin != nullptr && Pin->LinkedTo.Num() != 0)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-break-failed"),
				TEXT("The planned node still has a link that was not enumerated; refusing to delete it."));
		}
	}

	FBlueprintEditorUtils::RemoveNode(Blueprint, Node, true);
	if (FindNode(Graph, NodeGuid) != nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-delete-failed"),
			TEXT("The planned node is still present after RemoveNode."));
	}
	// The object survives (RemoveNode only detaches it) and is what the rollback
	// puts back, so the restored node keeps its exact NodeGuid and pin GUIDs.
	InOutOperation.DetachedNode = Node;
	return true;
}

bool UEAgentKitBlueprintNodeRemoveExperimental::TryCheckAtomicRemoveOperation(
	UEdGraph* Graph,
	const FAtomicRemoveOperation& Operation,
	bool bExpectAfter,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	FGuid NodeGuid;
	if (Graph == nullptr || !IsCanonicalGuid(Operation.NodeGuid, NodeGuid))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-identifier"),
			TEXT("The exact graph or the canonical nodeGuid is unavailable."));
	}
	UEdGraphNode* Node = FindNode(Graph, NodeGuid);
	if (bExpectAfter)
	{
		if (Node != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-post-commit-mismatch"),
				FString::Printf(TEXT("Node %s is still in the graph."), *Operation.NodeGuid));
		}
		for (const FAtomicRemovedEdge& Edge : Operation.RemovedEdges)
		{
			UEdGraphPin* FromPin = nullptr;
			UEdGraphPin* ToPin = nullptr;
			if (FindAtomicEdgePins(Graph, Edge, FromPin, ToPin)
				&& (FromPin->LinkedTo.Contains(ToPin) || ToPin->LinkedTo.Contains(FromPin)))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-remove-post-commit-mismatch"),
					TEXT("A removed node's edge is still present."));
			}
		}
		return true;
	}
	if (Node == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-post-commit-mismatch"),
			FString::Printf(
				TEXT("Node %s did not come back."),
				*Operation.NodeGuid));
	}
	if (Node->GetClass() == nullptr
		|| !Node->GetClass()->GetPathName().Equals(Operation.NodeClass, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-post-commit-mismatch"),
			FString::Printf(TEXT("Node %s came back with a different class."), *Operation.NodeGuid));
	}
	for (const FAtomicRemovedEdge& Edge : Operation.RemovedEdges)
	{
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		if (!FindAtomicEdgePins(Graph, Edge, FromPin, ToPin)
			|| !FromPin->LinkedTo.Contains(ToPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-post-commit-mismatch"),
				FString::Printf(
					TEXT("Edge %s.%s -> %s.%s did not come back."),
					*Edge.FromNodeGuid,
					*Edge.FromPinName,
					*Edge.ToNodeGuid,
					*Edge.ToPinName));
		}
	}
	return true;
}

bool UEAgentKitBlueprintNodeRemoveExperimental::TryRestoreAtomicRemoveOperation(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	FAtomicRemoveOperation& InOutOperation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-parameters"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	FGuid NodeGuid;
	if (!IsCanonicalGuid(InOutOperation.NodeGuid, NodeGuid))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-remove-invalid-identifier"),
			TEXT("nodeGuid must be one canonical lower-case GUID."));
	}
	UEdGraphNode* Node = FindNode(Graph, NodeGuid);
	if (Node != nullptr)
	{
		// Already back (nothing to detach): keep going so the edges are re-checked.
		InOutOperation.DetachedNode = nullptr;
	}
	else
	{
		Node = InOutOperation.DetachedNode;
		if (!IsValid(Node) || Node->NodeGuid != NodeGuid)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-delete-failed"),
				TEXT("The detached node object is no longer available, so the removal cannot be restored."));
		}
		Node->Modify();
		Graph->AddNode(Node, /*bFromUI=*/false, /*bSelectNewNode=*/false);
		if (FindNode(Graph, NodeGuid) == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-delete-failed"),
				TEXT("The detached node could not be put back into the graph."));
		}
	}
	for (const FAtomicRemovedEdge& Edge : InOutOperation.RemovedEdges)
	{
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		if (!FindAtomicEdgePins(Graph, Edge, FromPin, ToPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-mismatch"),
				TEXT("A removed edge's pins are missing after the node came back."));
		}
		if (!FromPin->LinkedTo.Contains(ToPin))
		{
			FromPin->MakeLinkTo(ToPin);
		}
		FromPin->GetOwningNode()->PinConnectionListChanged(FromPin);
		ToPin->GetOwningNode()->PinConnectionListChanged(ToPin);
		if (!FromPin->LinkedTo.Contains(ToPin) || !ToPin->LinkedTo.Contains(FromPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-remove-removed-edges-mismatch"),
				TEXT("A removed edge could not be re-created."));
		}
	}
	InOutOperation.DetachedNode = nullptr;
	return true;
}
