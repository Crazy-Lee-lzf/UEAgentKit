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
		// The pin was already found by GUID; its NAME is only cross-checked.  FName
		// equality ignores case, and a wildcard node that rebuilt its pins can
		// print "Option 0" where the plan recorded "option 0", so compare the way
		// FName does instead of demanding the interned spelling.
		if (!Pin->PinName.ToString().Equals(PinNameText, ESearchCase::IgnoreCase))
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

	/**
	 * Break OldSource -> Target and make NewSource -> Target, in that order.
	 *
	 * This is the one break+connect sequence of this writer: the single writer's
	 * Apply and the atomic seam's `rewirePin` both call it, so "exactly one old
	 * edge", the post-break schema response and the "target ends with exactly the
	 * new source" read-back cannot drift between the two paths.  The caller owns
	 * the transaction and the Modify() calls; on failure the graph is left with
	 * the old edge missing and nothing new, which is what the caller's rollback /
	 * Undo is for.
	 */
	bool ReplaceSingleEdge(
		UEdGraph* Graph,
		const UEdGraphSchema_K2* Schema,
		const FPinRef& OldSource,
		const FPinRef& Target,
		const FPinRef& NewSource,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Graph == nullptr || Schema == nullptr || Target.Pin == nullptr || OldSource.Pin == nullptr
			|| NewSource.Pin == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-old-edge-mismatch"),
				TEXT("The planned edge endpoints are no longer available."));
		}
		if (Target.Pin->LinkedTo.Num() != 1 || Target.Pin->LinkedTo[0] != OldSource.Pin)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-old-edge-mismatch"),
				TEXT("The planned old edge changed before the transaction could replace it."));
		}

		OldSource.Pin->BreakLinkTo(Target.Pin);
		OldSource.Node->PinConnectionListChanged(OldSource.Pin);
		Target.Node->PinConnectionListChanged(Target.Pin);
		if (Target.Pin->LinkedTo.Num() != 0 || OldSource.Pin->LinkedTo.Contains(Target.Pin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-break-failed"),
				TEXT("The single planned edge was not removed cleanly."));
		}

		const FPinConnectionResponse Response =
			Schema->CanCreateConnection(NewSource.Pin, Target.Pin);
		if (Response.Response.GetValue() != CONNECT_RESPONSE_MAKE)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-schema-rejected"),
				FString::Printf(
					TEXT("The K2 schema does not allow a plain connection after the planned break: %s"),
					*Response.Message.ToString()));
		}
		if (!Schema->TryCreateConnection(NewSource.Pin, Target.Pin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-connect-failed"),
				TEXT("The K2 schema refused the planned connection."));
		}
		if (Target.Pin->LinkedTo.Num() != 1 || Target.Pin->LinkedTo[0] != NewSource.Pin)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-rewire-connect-failed"),
				TEXT("The target pin is not connected to exactly the new source pin."));
		}
		return true;
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

bool UEAgentKitBlueprintRewireExperimental::TryResolveAtomicRewireOperation(
	UEdGraph* Graph,
	FAtomicRewireOperation& InOutOperation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-invalid-parameters"),
			TEXT("The exact graph is unavailable."));
	}
	const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
	if (Schema == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-schema-not-supported"),
			TEXT("Experimental Blueprint rewire supports only K2 Blueprint graphs."));
	}

	FPinRef OldSource;
	FPinRef Target;
	FPinRef NewSource;
	if (!ResolvePinRef(
			Graph,
			InOutOperation.OldSourceNodeGuid,
			InOutOperation.OldSourcePinName,
			InOutOperation.OldSourcePinGuid,
			EGPD_Output,
			TEXT("old source"),
			OldSource,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			InOutOperation.TargetNodeGuid,
			InOutOperation.TargetPinName,
			InOutOperation.TargetPinGuid,
			EGPD_Input,
			TEXT("target"),
			Target,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			InOutOperation.NewSourceNodeGuid,
			InOutOperation.NewSourcePinName,
			InOutOperation.NewSourcePinGuid,
			EGPD_Output,
			TEXT("new source"),
			NewSource,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}

	if (Target.Kind != OldSource.Kind || NewSource.Kind != Target.Kind)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-pin-kind-mismatch"),
			FString::Printf(
				TEXT("The old source, target and new source pins must share one kind (%s/%s/%s)."),
				PinKindLabel(OldSource.Kind),
				PinKindLabel(Target.Kind),
				PinKindLabel(NewSource.Kind)));
	}
	if (Target.Pin->LinkedTo.Num() == 0)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-target-unlinked"),
			TEXT("The target input pin has no existing connection; rewire requires exactly one."));
	}
	if (Target.Pin->LinkedTo.Num() != 1)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-target-multiple-links"),
			TEXT("The target input pin has more than one connection; this slice requires exactly one."));
	}
	if (Target.Pin->LinkedTo[0] != OldSource.Pin || !OldSource.Pin->LinkedTo.Contains(Target.Pin))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-old-edge-mismatch"),
			TEXT("The target pin is not connected to the declared old source pin."));
	}
	if (OldSource.Pin == NewSource.Pin)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-no-op"),
			TEXT("The new source pin is the current source pin."));
	}
	if (NewSource.Pin->LinkedTo.Contains(Target.Pin))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-already-linked"),
			TEXT("The new source pin is already connected to the target pin."));
	}
	// Judged before the break, exactly like Preview: the old edge is still there,
	// so BREAK_OTHERS_B is this plan's own edge and is allowed, while a response
	// touching the new source's links is not.
	const FPinConnectionResponse Response =
		Schema->CanCreateConnection(NewSource.Pin, Target.Pin);
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

	InOutOperation.PinKind = PinKindLabel(Target.Kind);
	return true;
}

bool UEAgentKitBlueprintRewireExperimental::TryApplyAtomicRewireOperation(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	const FAtomicRewireOperation& Operation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-invalid-parameters"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
	if (Schema == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-schema-not-supported"),
			TEXT("Experimental Blueprint rewire supports only K2 Blueprint graphs."));
	}
	FPinRef OldSource;
	FPinRef Target;
	FPinRef NewSource;
	if (!ResolvePinRef(
			Graph,
			Operation.OldSourceNodeGuid,
			Operation.OldSourcePinName,
			Operation.OldSourcePinGuid,
			EGPD_Output,
			TEXT("old source"),
			OldSource,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			Operation.TargetNodeGuid,
			Operation.TargetPinName,
			Operation.TargetPinGuid,
			EGPD_Input,
			TEXT("target"),
			Target,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			Operation.NewSourceNodeGuid,
			Operation.NewSourcePinName,
			Operation.NewSourcePinGuid,
			EGPD_Output,
			TEXT("new source"),
			NewSource,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	OldSource.Node->Modify();
	Target.Node->Modify();
	NewSource.Node->Modify();
	if (!ReplaceSingleEdge(
			Graph,
			Schema,
			OldSource,
			Target,
			NewSource,
			OutErrorCode,
			OutErrorMessage))
	{
		// Best-effort put-back so a single failed operation leaves the graph as it
		// found it; the caller's transaction Undo is the authoritative rollback.
		Target.Pin->BreakLinkTo(NewSource.Pin);
		if (!Target.Pin->LinkedTo.Contains(OldSource.Pin))
		{
			OldSource.Pin->MakeLinkTo(Target.Pin);
		}
		OldSource.Node->PinConnectionListChanged(OldSource.Pin);
		Target.Node->PinConnectionListChanged(Target.Pin);
		return false;
	}
	return true;
}

bool UEAgentKitBlueprintRewireExperimental::TryCheckAtomicRewireOperation(
	UEdGraph* Graph,
	const FAtomicRewireOperation& Operation,
	bool bExpectAfter,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	FPinRef OldSource;
	FPinRef Target;
	FPinRef NewSource;
	if (!ResolvePinRef(
			Graph,
			Operation.OldSourceNodeGuid,
			Operation.OldSourcePinName,
			Operation.OldSourcePinGuid,
			EGPD_Output,
			TEXT("old source"),
			OldSource,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			Operation.TargetNodeGuid,
			Operation.TargetPinName,
			Operation.TargetPinGuid,
			EGPD_Input,
			TEXT("target"),
			Target,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			Operation.NewSourceNodeGuid,
			Operation.NewSourcePinName,
			Operation.NewSourcePinGuid,
			EGPD_Output,
			TEXT("new source"),
			NewSource,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	const FPinRef& Want = bExpectAfter ? NewSource : OldSource;
	const FPinRef& Gone = bExpectAfter ? OldSource : NewSource;
	if (Target.Pin->LinkedTo.Num() != 1 || Target.Pin->LinkedTo[0] != Want.Pin
		|| Gone.Pin->LinkedTo.Contains(Target.Pin))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-post-commit-mismatch"),
			FString::Printf(
				TEXT("The target pin is not linked to exactly the %s source pin."),
				bExpectAfter ? TEXT("new") : TEXT("old")));
	}
	return true;
}

bool UEAgentKitBlueprintRewireExperimental::TryRestoreAtomicRewireOperation(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	const FAtomicRewireOperation& Operation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-invalid-parameters"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	FPinRef OldSource;
	FPinRef Target;
	FPinRef NewSource;
	if (!ResolvePinRef(
			Graph,
			Operation.OldSourceNodeGuid,
			Operation.OldSourcePinName,
			Operation.OldSourcePinGuid,
			EGPD_Output,
			TEXT("old source"),
			OldSource,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			Operation.TargetNodeGuid,
			Operation.TargetPinName,
			Operation.TargetPinGuid,
			EGPD_Input,
			TEXT("target"),
			Target,
			OutErrorCode,
			OutErrorMessage)
		|| !ResolvePinRef(
			Graph,
			Operation.NewSourceNodeGuid,
			Operation.NewSourcePinName,
			Operation.NewSourcePinGuid,
			EGPD_Output,
			TEXT("new source"),
			NewSource,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	Target.Pin->BreakLinkTo(NewSource.Pin);
	if (!Target.Pin->LinkedTo.Contains(OldSource.Pin))
	{
		OldSource.Pin->MakeLinkTo(Target.Pin);
	}
	OldSource.Node->PinConnectionListChanged(OldSource.Pin);
	Target.Node->PinConnectionListChanged(Target.Pin);
	if (!Target.Pin->LinkedTo.Contains(OldSource.Pin) || NewSource.Pin->LinkedTo.Contains(Target.Pin))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-rewire-post-commit-mismatch"),
			TEXT("The planned old edge was not restored."));
	}
	return true;
}
