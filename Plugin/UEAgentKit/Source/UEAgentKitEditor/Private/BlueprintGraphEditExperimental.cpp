#include "BlueprintGraphEditExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintRewireExperimental.h"

#include "BlueprintNodeBinder.h"
#include "BlueprintNodeSpawner.h"
#include "BlueprintVariableNodeSpawner.h"
#include "BlueprintBoundEventNodeSpawner.h"
#include "BlueprintComponentNodeSpawner.h"
#include "BlueprintDelegateNodeSpawner.h"
#include "Native/NativeNodeRegistry.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "K2Node.h"
#include "K2Node_CallFunction.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_Variable.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetStringLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "ScopedTransaction.h"
#include "HAL/FileManager.h"
#include "Misc/PackageName.h"
#include "UObject/SavePackage.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Guid.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitGraphEditExperimental, Log, All);

namespace
{
	constexpr int32 MaxNodes = UEAgentKitBlueprintGraphEditExperimental::MaxNodesPerApply;
	constexpr int32 MaxConnections = 24;
	constexpr int32 MaxConnectionsPerNode = 8;
	constexpr int32 MaxOwnedLiterals = 8;
	constexpr int32 MaxOwnedStringLiteral = 256;
	constexpr int32 MaxCoordinate = UEAgentKitBlueprintGraphEditExperimental::MaxNodeCoordinate;
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.graphpatch-edit-preview/1.0");
	const TCHAR* const BranchClassPath = TEXT("/Script/BlueprintGraph.K2Node_IfThenElse");
	const TCHAR* const SequenceClassPath = TEXT("/Script/BlueprintGraph.K2Node_ExecutionSequence");
	const TCHAR* const CallFunctionClassPath = TEXT("/Script/BlueprintGraph.K2Node_CallFunction");
	const TCHAR* const VariableGetClassPath = TEXT("/Script/BlueprintGraph.K2Node_VariableGet");
	const TCHAR* const VariableSetClassPath = TEXT("/Script/BlueprintGraph.K2Node_VariableSet");
	const TCHAR* const NotBoolFunctionName = TEXT("Not_PreBool");


	struct FOwnedLiteralSpec
	{
		FString PinName;
		FString Type;
		FString Value;
	};

	struct FNodeSpec
	{
		FString TempId;
		FString NodeType;
		FString NodeClassPath;
		FString FunctionName;
		int32 X = 0;
		int32 Y = 0;
		TArray<FString> InputPins;
		TArray<FString> OutputPins;
		TMap<FString, FString> InputPinTypes;
		TMap<FString, FString> OutputPinTypes;
		// Bounded member-variable accessor (variable_get / variable_set).  The
		// node is bound to one existing top-level member variable by GUID, name
		// and one of the four primitive types the rest of this slice supports.
		bool bVariableAccessor = false;
		FString VariableName;
		FString VariableGuid;
		FString VariableType;
		FString VariableAccess;
		// Bounded real UFUNCTION CallFunction node.  The owner/function/digest
		// must match the fixed allowlist below; no free-form path is accepted.
		bool bCallFunction = false;
		FString CallFunctionOwnerPath;
		FString CallFunctionSignatureDigest;
		// Bounded same-Blueprint pure function call.  The exact function graph,
		// function name and reflected signature are re-resolved against the
		// focused Blueprint before any mutation.
		bool bOwnedCall = false;
		FString OwnedBlueprintPath;
		FString OwnedFunctionGraphGuid;
		FString OwnedFunctionName;
		FString OwnedSignatureDigest;
		FString OwnedOwnerClassPath;
		TArray<FOwnedLiteralSpec> InputLiterals;
	};

	struct FCallFunctionPinDef
	{
		FString Name;
		FString Direction;
		FString Type;
	};

	struct FCallFunctionAllowlistEntry
	{
		FString OwnerClassPath;
		FString FunctionName;
		FString SignatureDigest;
		TArray<FCallFunctionPinDef> Pins;
	};

	const TCHAR* const CallFunctionOwnerPath = TEXT("/Script/Engine.KismetMathLibrary");



	TArray<FCallFunctionAllowlistEntry> GetCallFunctionAllowlist()
	{
		TArray<FCallFunctionAllowlistEntry> Entries;
		auto AddEntry = [&Entries](
			const TCHAR* FunctionName,
			const TCHAR* SignatureDigest,
			TArray<FCallFunctionPinDef> Pins)
		{
			FCallFunctionAllowlistEntry Entry;
			Entry.OwnerClassPath = CallFunctionOwnerPath;
			Entry.FunctionName = FunctionName;
			Entry.SignatureDigest = SignatureDigest;
			Entry.Pins = MoveTemp(Pins);
			Entries.Add(MoveTemp(Entry));
		};
		AddEntry(
			TEXT("Greater_IntInt"),
			TEXT("sha256:0f41d587fab8cfbbc431b8dcf4b6d150b0f3dff6438ae3e5e731a932ad26ea09"),
			{
				{ TEXT("A"), TEXT("input"), TEXT("int") },
				{ TEXT("B"), TEXT("input"), TEXT("int") },
				{ TEXT("ReturnValue"), TEXT("output"), TEXT("bool") },
			});
		AddEntry(
			TEXT("Less_IntInt"),
			TEXT("sha256:2537b266443bce7147ae6d77e0704479c65c69e4672034fea911909be2aab8ae"),
			{
				{ TEXT("A"), TEXT("input"), TEXT("int") },
				{ TEXT("B"), TEXT("input"), TEXT("int") },
				{ TEXT("ReturnValue"), TEXT("output"), TEXT("bool") },
			});
		AddEntry(
			TEXT("Clamp"),
			TEXT("sha256:ec7c282e64b63ce136c49c3e364661da32930b121b1b6a08e74df4fdeebcfb9d"),
			{
				{ TEXT("Value"), TEXT("input"), TEXT("int") },
				{ TEXT("Min"), TEXT("input"), TEXT("int") },
				{ TEXT("Max"), TEXT("input"), TEXT("int") },
				{ TEXT("ReturnValue"), TEXT("output"), TEXT("int") },
			});
		AddEntry(
			TEXT("Abs_Int"),
			TEXT("sha256:ea421dabd229f8c16d774b90a49746d56eb3358b4172e4c3796ef7f5de8a9fa7"),
			{
				{ TEXT("A"), TEXT("input"), TEXT("int") },
				{ TEXT("ReturnValue"), TEXT("output"), TEXT("int") },
			});
		return Entries;
	}

	const FCallFunctionAllowlistEntry* FindCallFunctionAllowlistEntry(
		const FString& OwnerClassPath,
		const FString& FunctionName)
	{
		static const TArray<FCallFunctionAllowlistEntry> Entries = GetCallFunctionAllowlist();
		return Entries.FindByPredicate([&OwnerClassPath, &FunctionName](
			const FCallFunctionAllowlistEntry& Entry)
		{
			return Entry.OwnerClassPath.Equals(OwnerClassPath, ESearchCase::CaseSensitive)
				&& Entry.FunctionName.Equals(FunctionName, ESearchCase::CaseSensitive);
		});
	}




	struct FApplyReceipt
	{
		FString AssetPath;
		FString GraphGuid;
		FString GraphFingerprint;
		FString VariableBindingHash;
		FString EdgeSetHash;
		FString OwnedLiteralHash;
		FString OwnedLiteralNodeGuid;
		TMap<FString, FString> OwnedLiteralValues;
		bool bRequiresEdgeSetHash = false;
		bool bRequiresOwnedLiteralHash = false;
		int32 NodeCount = 0;
		int32 LinkCount = 0;
	};

	bool ValidateSchemaConnection(
		UEdGraph* Graph,
		UEdGraphPin* FromPin,
		UEdGraphPin* ToPin,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// Save is only valid for an Apply completed in this same Editor session.
	// The receipt is the authority for plan -> post-Apply graph binding.
	TMap<FString, FApplyReceipt> RecentApplies;

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







	/**
	 * Canonical lowercase 8-4-4-4-12 GUID, the same shape the Blueprint
	 * canonical context emits for FBPVariableDescription::VarGuid and
	 * FMemberReference::MemberGuid.  The Editor plan binds the variable by this
	 * GUID as well as its name so a rename/recreate cannot silently retarget the
	 * accessor between Preview and Apply.
	 */
	bool IsCanonicalVariableGuid(const FString& Value)
	{
		if (Value.Len() != 36)
		{
			return false;
		}
		for (int32 Index = 0; Index < Value.Len(); ++Index)
		{
			const TCHAR Character = Value[Index];
			if (Index == 8 || Index == 13 || Index == 18 || Index == 23)
			{
				if (Character != TEXT('-'))
				{
					return false;
				}
				continue;
			}
			if (!((Character >= TEXT('0') && Character <= TEXT('9'))
				|| (Character >= TEXT('a') && Character <= TEXT('f'))))
			{
				return false;
			}
		}
		return true;
	}

	bool IsSupportedVariableTypeToken(const FString& Value)
	{
		return Value.Equals(TEXT("bool"), ESearchCase::CaseSensitive)
			|| Value.Equals(TEXT("int"), ESearchCase::CaseSensitive)
			|| Value.Equals(TEXT("double"), ESearchCase::CaseSensitive)
			|| Value.Equals(TEXT("string"), ESearchCase::CaseSensitive);
	}

	/**
	 * FName-safe transport spelling.  The actual variable must already exist in
	 * Blueprint->NewVariables, so this only prevents control characters and the
	 * plan canonical delimiters from entering the plan text.  It intentionally
	 * does not invent a stricter naming policy than the engine.
	 */
	bool IsSafeVariableName(const FString& Value)
	{
		if (Value.IsEmpty() || Value.Len() > 128)
		{
			return false;
		}
		// These names are already used by the variable accessor exec/value pins;
		// a variable with one of them would create an ambiguous pin inventory.
		if (Value.Equals(TEXT("execute"), ESearchCase::CaseSensitive)
			|| Value.Equals(TEXT("then"), ESearchCase::CaseSensitive)
			|| Value.Equals(TEXT("Output_Get"), ESearchCase::CaseSensitive))
		{
			return false;
		}
		for (const TCHAR Character : Value)
		{
			if (Character == TEXT('|')
				|| Character == TEXT('\n')
				|| Character == TEXT('\r')
				|| Character < 0x20
				|| Character == 0x7f)
			{
				return false;
			}
		}
		return true;
	}

	/** Map only the four primitive variable kinds supported by this slice. */
	bool VariableTypeToken(const FEdGraphPinType& Type, FString& OutType)
	{
		if (Type.ContainerType != EPinContainerType::None
			|| Type.bIsReference
			|| Type.bIsWeakPointer
			|| Type.bIsConst)
		{
			return false;
		}
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Boolean)
		{
			OutType = TEXT("bool");
			return true;
		}
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Int)
		{
			OutType = TEXT("int");
			return true;
		}
		if ((Type.PinCategory == UEdGraphSchema_K2::PC_Real
				&& Type.PinSubCategory == UEdGraphSchema_K2::PC_Double)
			|| Type.PinCategory == UEdGraphSchema_K2::PC_Double)
		{
			OutType = TEXT("double");
			return true;
		}
		if (Type.PinCategory == UEdGraphSchema_K2::PC_String)
		{
			OutType = TEXT("string");
			return true;
		}
		return false;
	}

	FProperty* FindMemberVariableProperty(UBlueprint* Blueprint, const FName Name)
	{
		if (Blueprint == nullptr)
		{
			return nullptr;
		}
		// Favor the skeleton property, matching UK2Node_Variable::GetPropertyForVariable.
		if (Blueprint->SkeletonGeneratedClass != nullptr)
		{
			if (FProperty* Property = FindFProperty<FProperty>(Blueprint->SkeletonGeneratedClass, Name))
			{
				return Property;
			}
		}
		if (Blueprint->GeneratedClass != nullptr)
		{
			return FindFProperty<FProperty>(Blueprint->GeneratedClass, Name);
		}
		return nullptr;
	}

	/**
	 * Variable accessors are limited to the Blueprint's own top-level event,
	 * function and macro graphs.  Interface/extension graphs and nested
	 * UEdGraph::SubGraphs stay read-only in this slice.
	 */
	bool IsSupportedVariableAccessorGraph(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		return Blueprint != nullptr
			&& Graph != nullptr
			&& (Blueprint->UbergraphPages.Contains(Graph)
				|| Blueprint->FunctionGraphs.Contains(Graph)
				|| Blueprint->MacroGraphs.Contains(Graph));
	}

	/**
	 * Resolve one existing member variable and fill the exact engine pin
	 * contract.  Nothing here mutates the Blueprint or the graph.
	 */
	bool ResolveVariableNode(
		FNodeSpec& Node,
		UBlueprint* Blueprint,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Node.bVariableAccessor)
		{
			return true;
		}
		if (!IsSafeVariableName(Node.VariableName))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-name-invalid"),
				TEXT("variableName must be 1-128 characters without control characters or '|'."));
		}
		if (!IsCanonicalVariableGuid(Node.VariableGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-guid-invalid"),
				TEXT("variableGuid must be one canonical lowercase GUID."));
		}
		if (!IsSupportedVariableTypeToken(Node.VariableType))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-type-invalid"),
				TEXT("variableType must be bool, int, double or string."));
		}

		FGuid Guid;
		if (!FGuid::ParseExact(Node.VariableGuid, EGuidFormats::DigitsWithHyphensLower, Guid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-guid-invalid"),
				TEXT("variableGuid could not be parsed as a canonical GUID."));
		}

		const FBPVariableDescription* Found = nullptr;
		bool bNameSeenWithOtherGuid = false;
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			if (Variable.VarGuid == Guid)
			{
				if (!Variable.VarName.ToString().Equals(Node.VariableName, ESearchCase::CaseSensitive))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-variable-identity-mismatch"),
						FString::Printf(
							TEXT("variableGuid %s is bound to name %s, not %s."),
							*Node.VariableGuid,
							*Variable.VarName.ToString(),
							*Node.VariableName));
				}
				Found = &Variable;
				break;
			}
			if (Variable.VarName.ToString().Equals(Node.VariableName, ESearchCase::CaseSensitive))
			{
				bNameSeenWithOtherGuid = true;
			}
		}
		if (Found == nullptr)
		{
			if (bNameSeenWithOtherGuid)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-variable-identity-mismatch"),
					FString::Printf(
						TEXT("variableName %s exists with a different GUID than %s."),
						*Node.VariableName,
						*Node.VariableGuid));
			}
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-not-found"),
				FString::Printf(
					TEXT("No top-level member variable %s (%s) exists on the exact Blueprint."),
					*Node.VariableName,
					*Node.VariableGuid));
		}

		FString ActualType;
		if (!VariableTypeToken(Found->VarType, ActualType))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-type-not-supported"),
				FString::Printf(
					TEXT("Variable %s is not a plain bool/int/double/string member variable."),
					*Node.VariableName));
		}
		if (!ActualType.Equals(Node.VariableType, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-type-mismatch"),
				FString::Printf(
					TEXT("Variable %s is %s, not the declared %s."),
					*Node.VariableName,
					*ActualType,
					*Node.VariableType));
		}

		FProperty* Property = FindMemberVariableProperty(Blueprint, Found->VarName);
		if (Property == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-property-unavailable"),
				FString::Printf(
					TEXT("Variable %s has no generated/skeleton property; compile the Blueprint before adding an accessor."),
					*Node.VariableName));
		}
		FEdGraphPinType PropertyType;
		if (!GetDefault<UEdGraphSchema_K2>()->ConvertPropertyToPinType(Property, PropertyType))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-property-unavailable"),
				FString::Printf(
					TEXT("The generated property for %s has no K2 pin type."),
					*Node.VariableName));
		}
		FString PropertyTypeToken;
		if (!VariableTypeToken(PropertyType, PropertyTypeToken)
			|| !PropertyTypeToken.Equals(Node.VariableType, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-type-mismatch"),
				FString::Printf(
					TEXT("The generated property for %s does not match the declared %s type (stale skeleton?)."),
					*Node.VariableName,
					*Node.VariableType));
		}

		Node.InputPins.Reset();
		Node.OutputPins.Reset();
		Node.InputPinTypes.Reset();
		Node.OutputPinTypes.Reset();
		if (Node.NodeType.Equals(TEXT("variable_get"), ESearchCase::CaseSensitive))
		{
			Node.NodeClassPath = VariableGetClassPath;
			Node.VariableAccess = TEXT("read");
			Node.OutputPins.Add(Node.VariableName);
			Node.OutputPinTypes.Add(Node.VariableName, ActualType);
			return true;
		}
		if (Node.NodeType.Equals(TEXT("variable_set"), ESearchCase::CaseSensitive))
		{
			Node.NodeClassPath = VariableSetClassPath;
			Node.VariableAccess = TEXT("write");
			Node.InputPins.Add(TEXT("execute"));
			Node.InputPins.Add(Node.VariableName);
			Node.OutputPins.Add(TEXT("then"));
			Node.OutputPins.Add(TEXT("Output_Get"));
			Node.InputPinTypes.Add(TEXT("execute"), TEXT("exec"));
			Node.InputPinTypes.Add(Node.VariableName, ActualType);
			Node.OutputPinTypes.Add(TEXT("then"), TEXT("exec"));
			Node.OutputPinTypes.Add(TEXT("Output_Get"), ActualType);
			return true;
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-node-type-not-allowed"),
			FString::Printf(TEXT("Unsupported variable accessor nodeType: %s"), *Node.NodeType));
	}












	bool ExistingPinTypeToken(const FEdGraphPinType& Type, FString& OutType)
	{
		if (Type.ContainerType != EPinContainerType::None
			|| Type.bIsReference
			|| Type.bIsWeakPointer
			|| Type.bIsConst)
		{
			return false;
		}
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Exec)
		{
			OutType = TEXT("exec");
			return true;
		}
		// Phase B: a wildcard pin has no type of its own until the K2 schema derives
		// one from a connection (K2Node_Select / K2Node_PromotableOperator create
		// their option/A/B/ReturnValue pins as PC_Wildcard).  It gets its own token
		// so "unknown yet" can never be mistaken for "any concrete type".
		if (Type.PinCategory == UEdGraphSchema_K2::PC_Wildcard)
		{
			OutType = TEXT("wildcard");
			return true;
		}
		return VariableTypeToken(Type, OutType);
	}





	bool ConfigureNodeType(
		const FString& NodeType,
		FNodeSpec& OutNode,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (NodeType == TEXT("branch"))
		{
			OutNode.NodeClassPath = BranchClassPath;
			OutNode.InputPins = { TEXT("execute"), TEXT("Condition") };
			OutNode.OutputPins = { TEXT("then"), TEXT("else") };
			OutNode.InputPinTypes.Add(TEXT("execute"), TEXT("exec"));
			OutNode.InputPinTypes.Add(TEXT("Condition"), TEXT("bool"));
			OutNode.OutputPinTypes.Add(TEXT("then"), TEXT("exec"));
			OutNode.OutputPinTypes.Add(TEXT("else"), TEXT("exec"));
			return true;
		}
		if (NodeType == TEXT("sequence"))
		{
			OutNode.NodeClassPath = SequenceClassPath;
			OutNode.InputPins = { TEXT("execute") };
			OutNode.OutputPins = { TEXT("then_0"), TEXT("then_1") };
			OutNode.InputPinTypes.Add(TEXT("execute"), TEXT("exec"));
			OutNode.OutputPinTypes.Add(TEXT("then_0"), TEXT("exec"));
			OutNode.OutputPinTypes.Add(TEXT("then_1"), TEXT("exec"));
			return true;
		}
		if (NodeType == TEXT("not_bool"))
		{
			OutNode.NodeClassPath = CallFunctionClassPath;
			OutNode.FunctionName = NotBoolFunctionName;
			OutNode.InputPins = { TEXT("A") };
			OutNode.OutputPins = { TEXT("ReturnValue") };
			OutNode.InputPinTypes.Add(TEXT("A"), TEXT("bool"));
			OutNode.OutputPinTypes.Add(TEXT("ReturnValue"), TEXT("bool"));
			return true;
		}
		if (NodeType == TEXT("bool_or"))
		{
			OutNode.NodeClassPath = CallFunctionClassPath;
			OutNode.FunctionName = TEXT("BooleanOR");
			OutNode.InputPins = { TEXT("A"), TEXT("B") };
			OutNode.OutputPins = { TEXT("ReturnValue") };
			OutNode.InputPinTypes.Add(TEXT("A"), TEXT("bool"));
			OutNode.InputPinTypes.Add(TEXT("B"), TEXT("bool"));
			OutNode.OutputPinTypes.Add(TEXT("ReturnValue"), TEXT("bool"));
			return true;
		}
		if (NodeType == TEXT("int_add"))
		{
			OutNode.NodeClassPath = CallFunctionClassPath;
			OutNode.FunctionName = TEXT("Add_IntInt");
			OutNode.InputPins = { TEXT("A"), TEXT("B") };
			OutNode.OutputPins = { TEXT("ReturnValue") };
			OutNode.InputPinTypes.Add(TEXT("A"), TEXT("int"));
			OutNode.InputPinTypes.Add(TEXT("B"), TEXT("int"));
			OutNode.OutputPinTypes.Add(TEXT("ReturnValue"), TEXT("int"));
			return true;
		}
		if (NodeType == TEXT("int_equal"))
		{
			OutNode.NodeClassPath = CallFunctionClassPath;
			OutNode.FunctionName = TEXT("EqualEqual_IntInt");
			OutNode.InputPins = { TEXT("A"), TEXT("B") };
			OutNode.OutputPins = { TEXT("ReturnValue") };
			OutNode.InputPinTypes.Add(TEXT("A"), TEXT("int"));
			OutNode.InputPinTypes.Add(TEXT("B"), TEXT("int"));
			OutNode.OutputPinTypes.Add(TEXT("ReturnValue"), TEXT("bool"));
			return true;
		}
		if (NodeType == TEXT("double_add"))
		{
			OutNode.NodeClassPath = CallFunctionClassPath;
			OutNode.FunctionName = TEXT("Add_DoubleDouble");
			OutNode.InputPins = { TEXT("A"), TEXT("B") };
			OutNode.OutputPins = { TEXT("ReturnValue") };
			OutNode.InputPinTypes.Add(TEXT("A"), TEXT("double"));
			OutNode.InputPinTypes.Add(TEXT("B"), TEXT("double"));
			OutNode.OutputPinTypes.Add(TEXT("ReturnValue"), TEXT("double"));
			return true;
		}
		if (NodeType == TEXT("string_concat"))
		{
			OutNode.NodeClassPath = CallFunctionClassPath;
			OutNode.FunctionName = TEXT("Concat_StrStr");
			OutNode.InputPins = { TEXT("A"), TEXT("B") };
			OutNode.OutputPins = { TEXT("ReturnValue") };
			OutNode.InputPinTypes.Add(TEXT("A"), TEXT("string"));
			OutNode.InputPinTypes.Add(TEXT("B"), TEXT("string"));
			OutNode.OutputPinTypes.Add(TEXT("ReturnValue"), TEXT("string"));
			return true;
		}
		if (NodeType == TEXT("call_function"))
		{
			OutNode.bCallFunction = true;
			OutNode.NodeClassPath = CallFunctionClassPath;
			return true;
		}
		if (NodeType == TEXT("call_owned_pure_function"))
		{
			OutNode.bOwnedCall = true;
			OutNode.NodeClassPath = CallFunctionClassPath;
			return true;
		}
		if (NodeType == TEXT("variable_get") || NodeType == TEXT("variable_set"))
		{
			// Pins and exact types depend on the existing member variable and
			// are filled by ResolveVariableNode after the exact Blueprint is
			// resolved.  No graph or Blueprint object is touched here.
			OutNode.bVariableAccessor = true;
			OutNode.NodeClassPath = NodeType == TEXT("variable_get")
				? VariableGetClassPath
				: VariableSetClassPath;
			return true;
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-node-type-not-allowed"),
			TEXT("Supported nodeTypes: branch, sequence, not_bool, bool_or, int_add, int_equal, double_add, string_concat, call_function, call_owned_pure_function, variable_get, variable_set."));
	}

	const FNodeSpec* FindNode(const TArray<FNodeSpec>& Nodes, const FString& TempId)
	{
		return Nodes.FindByPredicate([&TempId](const FNodeSpec& Node)
		{
			return Node.TempId.Equals(TempId, ESearchCase::CaseSensitive);
		});
	}













	bool ValidateSchemaConnection(
		UEdGraph* Graph,
		UEdGraphPin* FromPin,
		UEdGraphPin* ToPin,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Graph == nullptr || Graph->GetSchema() == nullptr || FromPin == nullptr || ToPin == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-connection-invalid"),
				TEXT("The graph or pins are unavailable for connection validation."));
		}
		const FPinConnectionResponse Response =
			Graph->GetSchema()->CanCreateConnection(FromPin, ToPin);
		if (Response.Response == CONNECT_RESPONSE_BREAK_OTHERS_A
			|| Response.Response == CONNECT_RESPONSE_BREAK_OTHERS_B
			|| Response.Response == CONNECT_RESPONSE_BREAK_OTHERS_AB)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-break-others-rejected"),
				TEXT("The schema would break an existing link; this slice never does that."));
		}
		if (Response.Response == CONNECT_RESPONSE_MAKE_WITH_CONVERSION_NODE)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-conversion-rejected"),
				TEXT("The schema would insert a conversion node; this slice requires exact types."));
		}
		if (Response.Response == CONNECT_RESPONSE_MAKE_WITH_PROMOTION)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-promotion-rejected"),
				TEXT("The schema would promote a pin type; this slice requires exact types."));
		}
		if (Response.Response != CONNECT_RESPONSE_MAKE)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-connection-rejected"),
				FString::Printf(
					TEXT("CanCreateConnection did not return MAKE: %s"),
					*Response.Message.ToString()));
		}
		return true;
	}

	bool PinMatchesType(const UEdGraphPin* Pin, const FString& ExpectedType)
	{
		if (Pin == nullptr)
		{
			return false;
		}
		if (ExpectedType.Equals(TEXT("exec"), ESearchCase::CaseSensitive))
		{
			return Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec;
		}
		if (ExpectedType.Equals(TEXT("bool"), ESearchCase::CaseSensitive))
		{
			return Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Boolean;
		}
		if (ExpectedType.Equals(TEXT("int"), ESearchCase::CaseSensitive))
		{
			return Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Int;
		}
		if (ExpectedType.Equals(TEXT("double"), ESearchCase::CaseSensitive))
		{
			return (Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Real
					&& Pin->PinType.PinSubCategory == UEdGraphSchema_K2::PC_Double)
				|| Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_Double;
		}
		if (ExpectedType.Equals(TEXT("string"), ESearchCase::CaseSensitive))
		{
			return Pin->PinType.PinCategory == UEdGraphSchema_K2::PC_String;
		}
		return false;
	}

	/**
	 * Prove one spawned variable accessor is the exact planned node: class,
	 * self-context member identity (GUID + name) and the engine pin inventory
	 * this slice declares.  Used both immediately after spawn and after Redo.
	 */
	bool VariableNodeMatchesSpec(
		const UEdGraphNode* Node,
		const FNodeSpec& Spec,
		FString& OutReason)
	{
		if (Node == nullptr)
		{
			OutReason = TEXT("The variable accessor node is missing.");
			return false;
		}
		if (!Node->GetClass()->GetPathName().Equals(Spec.NodeClassPath, ESearchCase::CaseSensitive))
		{
			OutReason = FString::Printf(
				TEXT("The variable accessor class is %s, not %s."),
				*Node->GetClass()->GetPathName(),
				*Spec.NodeClassPath);
			return false;
		}
		const UK2Node_Variable* VariableNode = Cast<UK2Node_Variable>(Node);
		if (VariableNode == nullptr)
		{
			OutReason = TEXT("The spawned node is not a UK2Node_Variable.");
			return false;
		}
		if (!VariableNode->VariableReference.IsSelfContext())
		{
			OutReason = TEXT("The variable accessor is not a self-context member reference.");
			return false;
		}
		if (!VariableNode->GetVarName().ToString().Equals(Spec.VariableName, ESearchCase::CaseSensitive))
		{
			OutReason = FString::Printf(
				TEXT("The variable accessor names %s, not %s."),
				*VariableNode->GetVarName().ToString(),
				*Spec.VariableName);
			return false;
		}
		FGuid Guid;
		if (!FGuid::ParseExact(Spec.VariableGuid, EGuidFormats::DigitsWithHyphensLower, Guid)
			|| VariableNode->VariableReference.GetMemberGuid() != Guid)
		{
			OutReason = TEXT("The variable accessor member GUID does not match the plan.");
			return false;
		}

		auto RequirePin = [Node, &OutReason](
			const FString& Name,
			const EEdGraphPinDirection Direction,
			const FString& Type) -> bool
		{
			const UEdGraphPin* Pin = Node->FindPin(FName(*Name), Direction);
			if (Pin == nullptr)
			{
				OutReason = FString::Printf(
					TEXT("The variable accessor has no %s pin %s."),
					Direction == EGPD_Input ? TEXT("input") : TEXT("output"),
					*Name);
				return false;
			}
			if (!PinMatchesType(Pin, Type))
			{
				OutReason = FString::Printf(
					TEXT("Pin %s does not have the declared %s type."),
					*Name,
					*Type);
				return false;
			}
			return true;
		};

		if (Spec.NodeType.Equals(TEXT("variable_get"), ESearchCase::CaseSensitive))
		{
			return RequirePin(Spec.VariableName, EGPD_Output, Spec.VariableType);
		}
		return RequirePin(TEXT("execute"), EGPD_Input, TEXT("exec"))
			&& RequirePin(TEXT("then"), EGPD_Output, TEXT("exec"))
			&& RequirePin(Spec.VariableName, EGPD_Input, Spec.VariableType)
			&& RequirePin(TEXT("Output_Get"), EGPD_Output, Spec.VariableType);
	}

	UClass* FunctionOwnerForNodeType(const FString& NodeType)
	{
		if (NodeType == TEXT("string_concat"))
		{
			return UKismetStringLibrary::StaticClass();
		}
		return UKismetMathLibrary::StaticClass();
	}


	UClass* NodeClassForType(const FString& NodeType)
	{
		if (NodeType == TEXT("branch"))
		{
			return UK2Node_IfThenElse::StaticClass();
		}
		if (NodeType == TEXT("sequence"))
		{
			return UK2Node_ExecutionSequence::StaticClass();
		}
		if (NodeType == TEXT("not_bool")
			|| NodeType == TEXT("bool_or")
			|| NodeType == TEXT("int_add")
			|| NodeType == TEXT("int_equal")
			|| NodeType == TEXT("double_add")
			|| NodeType == TEXT("string_concat"))
		{
			return UK2Node_CallFunction::StaticClass();
		}
		return nullptr;
	}

	FString AppendField(const FString& Builder, const FString& Field)
	{
		return Builder + FString::Printf(TEXT("%d:"), Field.Len()) + Field;
	}

	const TCHAR* const NodeStateFingerprintVersion = TEXT("ueak.blueprint-node-state/1");

	/**
	 * Node structure fingerprint: identity, class, position and pin inventory.
	 *
	 * Pin defaults and link counts belong to the pin-default writer's own
	 * fingerprint, so they are deliberately not repeated here; the two hashes are
	 * reported side by side by the atomic patch.
	 */
	FString ComputeNodeStateFingerprint(UEdGraph* Graph)
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
		Builder = AppendField(Builder, NodeStateFingerprintVersion);
		Builder = AppendField(
			Builder,
			Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		for (const UEdGraphNode* Node : SortedNodes)
		{
			Builder = AppendField(
				Builder,
				Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Builder = AppendField(Builder, Node->GetClass()->GetPathName());
			Builder = AppendField(Builder, FString::FromInt(Node->NodePosX));
			Builder = AppendField(Builder, FString::FromInt(Node->NodePosY));
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
				// Order by the case-insensitive FName comparison, not by the
				// interned spelling: a rebuilt wildcard pin can flip "Index" to
				// "index", which would reorder the list and change the hash even
				// though nothing in the graph changed.
				const int32 NameOrder = Left.PinName.Compare(Right.PinName);
				if (NameOrder != 0)
				{
					return NameOrder < 0;
				}
				return Left.Direction < Right.Direction;
			});
			for (const UEdGraphPin* Pin : Pins)
			{
				// Same FName normalisation as the pin-default hash: the name table
				// keeps the first interned spelling, so a reconstructed node may
				// print "index" where it printed "Index" before Undo/Redo, and
				// "Object" where it printed "object".
				Builder = AppendField(Builder, Pin->PinName.ToString().ToLower());
				Builder = AppendField(
					Builder,
					Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
				Builder = AppendField(
					Builder,
					Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower));
				// Same FName normalisation as the pin-default hash: the name table
				// keeps the first interned spelling, so a reconstructed node may
				// print "Object" where it printed "object" before Undo/Redo.
				Builder = AppendField(Builder, Pin->PinType.PinCategory.ToString().ToLower());
				Builder = AppendField(Builder, Pin->PinType.PinSubCategory.ToString().ToLower());
				Builder = AppendField(
					Builder,
					Pin->PinType.PinSubCategoryObject != nullptr
						? Pin->PinType.PinSubCategoryObject->GetPathName()
						: FString());
			}
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Builder, Digest))
		{
			return FString();
		}
		return FString(TEXT("sha256:")) + Digest;
	}

	/**
	 * Shared creation path for the eight primitive nodeTypes.
	 *
	 * Both the single-writer Apply and the atomic patch seam call this function,
	 * so the class lookup, the spawner vs CallFunction choice, the
	 * RF_Transactional flag, the position and the default pins cannot drift
	 * between the two entry points.  `call_function`, `call_owned_pure_function`
	 * and the variable accessors keep their own branches in the single writer and
	 * are not reachable here.
	 *
	 * OutNode is set as soon as the node exists in the graph, so a caller that
	 * gets `false` can still remove the partially created node.
	 */
	/**
	 * Create one bounded member-variable accessor through the engine's spawner.
	 *
	 * 0-3.4 slice 15: extracted from the single writer's Apply so the atomic patch
	 * creates accessors through exactly the same path -- same generated-property
	 * lookup, same post-Preview type re-check, same
	 * `UBlueprintVariableNodeSpawner::CreateFromMemberOrParam` + Invoke.  It never
	 * creates, renames or retypes the variable itself.
	 */
	bool CreateVariableAccessorNode(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		const FString& VariableName,
		const FString& VariableType,
		const bool bGet,
		const int32 X,
		const int32 Y,
		UEdGraphNode*& OutNode,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutNode = nullptr;
		if (Blueprint == nullptr || Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-graph-unavailable"),
				TEXT("The exact Blueprint graph is unavailable."));
		}
		FProperty* Property = FindMemberVariableProperty(Blueprint, FName(*VariableName));
		if (Property == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-property-unavailable"),
				FString::Printf(
					TEXT("Variable %s no longer has a generated/skeleton property."),
					*VariableName));
		}
		FEdGraphPinType PropertyType;
		FString ActualType;
		if (!GetDefault<UEdGraphSchema_K2>()->ConvertPropertyToPinType(Property, PropertyType)
			|| !VariableTypeToken(PropertyType, ActualType)
			|| !ActualType.Equals(VariableType, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-type-mismatch"),
				FString::Printf(
					TEXT("Variable %s changed type after Preview."),
					*VariableName));
		}
		TSubclassOf<UK2Node_Variable> VariableNodeClass = bGet
			? UK2Node_VariableGet::StaticClass()
			: UK2Node_VariableSet::StaticClass();
		UBlueprintVariableNodeSpawner* VariableSpawner =
			UBlueprintVariableNodeSpawner::CreateFromMemberOrParam(
				VariableNodeClass,
				Property);
		if (VariableSpawner == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-spawn-failed"),
				FString::Printf(
					TEXT("Could not create a variable spawner for %s."),
					*VariableName));
		}
		UEdGraphNode* Node = VariableSpawner->Invoke(
			Graph,
			IBlueprintNodeBinder::FBindingSet(),
			FVector2D(X, Y));
		if (Node == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-spawn-failed"),
				FString::Printf(
					TEXT("The Editor failed to spawn the %s accessor for %s."),
					bGet ? TEXT("get") : TEXT("set"),
					*VariableName));
		}
		OutNode = Node;
		return true;
	}

	bool CreatePrimitiveNode(
		UEdGraph* Graph,
		const FNodeSpec& Spec,
		UEdGraphNode*& OutNode,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutNode = nullptr;
		if (Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-graph-unavailable"),
				TEXT("The exact graph is unavailable."));
		}
		UEdGraphNode* Node = nullptr;
		if (!Spec.FunctionName.IsEmpty())
		{
			UClass* FunctionOwner = FunctionOwnerForNodeType(Spec.NodeType);
			if (FunctionOwner == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-node-spawn-failed"),
					TEXT("The function owner class is unavailable."));
			}
			UFunction* Function = FunctionOwner->FindFunctionByName(FName(*Spec.FunctionName));
			if (Function == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-node-spawn-failed"),
					FString::Printf(TEXT("The function %s is unavailable."), *Spec.FunctionName));
			}
			UK2Node_CallFunction* CallNode = NewObject<UK2Node_CallFunction>(Graph);
			if (CallNode == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-node-spawn-failed"),
					TEXT("Could not allocate the CallFunction node."));
			}
			CallNode->Modify();
			Graph->AddNode(CallNode, true, false);
			CallNode->SetFromFunction(Function);
			if (CallNode->Pins.Num() == 0)
			{
				CallNode->AllocateDefaultPins();
			}
			Node = CallNode;
		}
		else
		{
			UClass* NodeClass = NodeClassForType(Spec.NodeType);
			if (NodeClass == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-node-type-not-allowed"),
					FString::Printf(TEXT("Unsupported nodeType: %s"), *Spec.NodeType));
			}
			UBlueprintNodeSpawner* Spawner = UBlueprintNodeSpawner::Create(
				NodeClass,
				GetTransientPackage());
			if (Spawner == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-node-spawn-failed"),
					FString::Printf(
						TEXT("Could not create a node spawner for %s."),
						*Spec.NodeType));
			}
			Node = Spawner->Invoke(
				Graph,
				IBlueprintNodeBinder::FBindingSet(),
				FVector2D(Spec.X, Spec.Y));
		}
		if (Node == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-spawn-failed"),
				FString::Printf(TEXT("The Editor failed to spawn %s."), *Spec.NodeType));
		}
		OutNode = Node;
		Node->SetFlags(RF_Transactional);
		if (!Node->NodeGuid.IsValid())
		{
			Node->CreateNewGuid();
		}
		Node->NodePosX = Spec.X;
		Node->NodePosY = Spec.Y;
		if (Node->Pins.Num() == 0)
		{
			Node->AllocateDefaultPins();
		}
		if (Node->Pins.Num() == 0 || !Node->NodeGuid.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-spawn-failed"),
				FString::Printf(
					TEXT("Spawned %s has no pins or no valid NodeGuid."),
					*Spec.NodeType));
		}
		return true;
	}

	/**
	 * Phase A admission for one registry action.
	 *
	 * Every refused field is one the descriptor already publishes, so this is not
	 * a second policy engine: latent, world-context and variadic actions cannot be
	 * placed by a plain spawner call, and the spawner families below need editor
	 * context (a level actor, the selected actor, a delegate property) that this
	 * surface deliberately does not have.  "This spawner needs something I do not
	 * have" -- not "this node type is banned".
	 */
	bool CheckActionSpawnAdmissible(
		const FNativeSpawnerResolution& Resolution,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Resolution.Spawner == nullptr || !Resolution.Descriptor.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-spawn-action-unknown"),
				TEXT("The action resolved without a describable spawner."));
		}
		const TSharedPtr<FJsonObject>* Constraints = nullptr;
		const bool bHasConstraints = Resolution.Descriptor->TryGetObjectField(TEXT("constraints"), Constraints)
			&& Constraints != nullptr
			&& Constraints->IsValid();
		auto Constraint = [&Constraints, bHasConstraints](const TCHAR* Field) -> bool
		{
			return bHasConstraints && (*Constraints)->GetBoolField(Field);
		};
		const TCHAR* UnsupportedField = !Constraint(TEXT("latent")) ? nullptr
			: TEXT("latent");
		if (UnsupportedField == nullptr && Constraint(TEXT("requiresWorldContext")))
		{
			UnsupportedField = TEXT("requiresWorldContext");
		}
		if (UnsupportedField == nullptr && Constraint(TEXT("variadic")))
		{
			UnsupportedField = TEXT("variadic");
		}
		if (UnsupportedField != nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-spawn-action-unsupported"),
				FString::Printf(
					TEXT("This action cannot be created by a plain spawner call: %s is set."),
					UnsupportedField));
		}
		if (Resolution.Spawner->IsA<UBlueprintBoundEventNodeSpawner>()
			|| Resolution.Spawner->IsA<UBlueprintComponentNodeSpawner>()
			|| Resolution.Spawner->IsA<UBlueprintDelegateNodeSpawner>())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-spawn-requires-binding"),
				FString::Printf(
					TEXT("The spawner %s requires a binding or an editor selection that this request does not carry."),
					*Resolution.SpawnerClassPath));
		}
		if (!Resolution.ActionKind.Equals(TEXT("callFunction"), ESearchCase::CaseSensitive)
			&& !Resolution.ActionKind.Equals(TEXT("cast"), ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-spawn-action-unsupported"),
				FString::Printf(
					TEXT("Phase A creates callFunction and cast actions only; this action is '%s'."),
					*Resolution.ActionKind));
		}
		return true;
	}

	/**
	 * Create one node through the engine spawner behind a registry action id.
	 *
	 * The spawner is invoked with an EMPTY binding set: the customize delegate the
	 * action database installed is the only thing that sets CallFunction's
	 * UFunction or DynamicCast's TargetType.  Re-implementing either here would be
	 * a second, drifting copy of engine behaviour.
	 */
	bool CreateActionNode(
		UEdGraph* Graph,
		const UEAgentKitBlueprintGraphEditExperimental::FAtomicNodeOperation& Operation,
		UEdGraphNode*& OutNode,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutNode = nullptr;
		if (Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-graph-unavailable"),
				TEXT("The exact graph is unavailable."));
		}
		const FNativeSpawnerResolution Resolution =
			FNativeNodeRegistry::Get().ResolveSpawnerForActionId(Operation.ActionId);
		if (!Resolution.bOk)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				Resolution.ErrorCode.IsEmpty() ? TEXT("graph-spawn-action-unknown") : *Resolution.ErrorCode,
				Resolution.ErrorMessage.IsEmpty()
					? FString(TEXT("The action could not be resolved."))
					: Resolution.ErrorMessage);
		}
		if (!CheckActionSpawnAdmissible(Resolution, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (!Resolution.NodeClassPath.Equals(Operation.NodeClassPath, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-spawn-action-unknown"),
				FString::Printf(
					TEXT("The action now resolves to %s, not the planned %s."),
					*Resolution.NodeClassPath,
					*Operation.NodeClassPath));
		}
		UEdGraphNode* Node = Resolution.Spawner->Invoke(
			Graph,
			IBlueprintNodeBinder::FBindingSet(),
			FVector2D(Operation.X, Operation.Y));
		if (Node == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-spawn-action-failed"),
				FString::Printf(
					TEXT("The Editor failed to spawn the action %s."),
					*Operation.ActionId));
		}
		OutNode = Node;
		Node->SetFlags(RF_Transactional);
		if (!Node->NodeGuid.IsValid())
		{
			Node->CreateNewGuid();
		}
		// The engine snaps a spawned node to the graph grid (9000 -> 8992); this
		// writer's contract is the exact requested coordinate, exactly like the
		// primitive path below which assigns x/y after spawning.
		Node->NodePosX = Operation.X;
		Node->NodePosY = Operation.Y;
		return true;
	}

	/**
	 * Read one primitive node back against the exact plan.
	 *
	 * This is the atomic seam's contract check.  The single writer's own read-back
	 * (node count, created GUID present, undo/redo) is unchanged; the patch adds
	 * this stricter per-field proof because it returns one result per operation.
	 */
	bool NodeMatchesAtomicOperation(
		const UEdGraphNode* Node,
		const UEAgentKitBlueprintGraphEditExperimental::FAtomicNodeOperation& Operation,
		FString& OutReason)
	{
		if (Node == nullptr)
		{
			OutReason = TEXT("The created node is missing from the live graph.");
			return false;
		}
		if (!Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower).Equals(
				Operation.NodeGuid,
				ESearchCase::CaseSensitive))
		{
			OutReason = TEXT("The created node no longer carries the resolved NodeGuid.");
			return false;
		}
		if (!Node->GetClass()->GetPathName().Equals(
				Operation.NodeClassPath,
				ESearchCase::CaseSensitive))
		{
			OutReason = FString::Printf(
				TEXT("The created node class %s does not match the resolved class %s."),
				*Node->GetClass()->GetPathName(),
				*Operation.NodeClassPath);
			return false;
		}
		if (Node->NodePosX != Operation.X || Node->NodePosY != Operation.Y)
		{
			OutReason = FString::Printf(
				TEXT("The created node position (%d,%d) does not match the plan (%d,%d)."),
				Node->NodePosX,
				Node->NodePosY,
				Operation.X,
				Operation.Y);
			return false;
		}
		if (Operation.bActionSpawn)
		{
			// Phase A action spawn: identity is the class the action resolved to,
			// plus the planned position.  The pin set is whatever the engine
			// produced and is read back by the caller after creation; asserting a
			// predicted pin contract here would be a shadow contract, not proof.
			if (Node->Pins.Num() == 0)
			{
				OutReason = TEXT("The action-spawned node has no pins.");
				return false;
			}
			return true;
		}
		if (Operation.bVariableAccessor)
		{
			// Slice 15: the accessor's own read-back -- class, self-context, member
			// name, member GUID and the four/one pin contract -- is the single
			// writer's check, reused here.
			FNodeSpec AccessorSpec;
			AccessorSpec.bVariableAccessor = true;
			AccessorSpec.NodeType = Operation.NodeType;
			AccessorSpec.NodeClassPath = Operation.NodeClassPath;
			AccessorSpec.VariableName = Operation.VariableName;
			AccessorSpec.VariableGuid = Operation.VariableGuid;
			AccessorSpec.VariableType = Operation.VariableType;
			return VariableNodeMatchesSpec(Node, AccessorSpec, OutReason);
		}

		FNodeSpec Expected;
		Expected.NodeType = Operation.NodeType;
		Expected.X = Operation.X;
		Expected.Y = Operation.Y;
		FString IgnoredCode;
		FString IgnoredMessage;
		if (!ConfigureNodeType(
				Operation.NodeType,
				Expected,
				IgnoredCode,
				IgnoredMessage))
		{
			OutReason = FString::Printf(
				TEXT("The resolved nodeType %s has no contract."),
				*Operation.NodeType);
			return false;
		}
		const int32 ExpectedPinCount = Expected.InputPins.Num() + Expected.OutputPins.Num();
		int32 VisiblePinCount = 0;
		int32 HiddenSelfCount = 0;
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin == nullptr)
			{
				continue;
			}
			const bool bIsHiddenSelf =
				Pin->bHidden
				&& Pin->Direction == EGPD_Input
				&& Pin->PinName == UEdGraphSchema_K2::PN_Self;
			if (bIsHiddenSelf)
			{
				++HiddenSelfCount;
				if (Pin->LinkedTo.Num() != 0)
				{
					OutReason = TEXT("The hidden self pin unexpectedly has links.");
					return false;
				}
				continue;
			}
			if (Pin->bHidden)
			{
				OutReason = FString::Printf(
					TEXT("The created node has an unexpected hidden pin %s."),
					*Pin->PinName.ToString());
				return false;
			}
			++VisiblePinCount;
		}
		if (VisiblePinCount != ExpectedPinCount || HiddenSelfCount > 1)
		{
			OutReason = FString::Printf(
				TEXT("The created %s node has %d visible pins, expected %d."),
				*Operation.NodeType,
				VisiblePinCount,
				ExpectedPinCount);
			return false;
		}
		for (const FString& PinName : Expected.InputPins)
		{
			const UEdGraphPin* Pin = Node->FindPin(FName(*PinName), EGPD_Input);
			if (Pin == nullptr || !PinMatchesType(Pin, Expected.InputPinTypes.FindRef(PinName)))
			{
				OutReason = FString::Printf(
					TEXT("The created node input pin %s is missing or has the wrong type."),
					*PinName);
				return false;
			}
		}
		for (const FString& PinName : Expected.OutputPins)
		{
			const UEdGraphPin* Pin = Node->FindPin(FName(*PinName), EGPD_Output);
			if (Pin == nullptr || !PinMatchesType(Pin, Expected.OutputPinTypes.FindRef(PinName)))
			{
				OutReason = FString::Printf(
					TEXT("The created node output pin %s is missing or has the wrong type."),
					*PinName);
				return false;
			}
		}
		if (!Expected.FunctionName.IsEmpty())
		{
			const UK2Node_CallFunction* CallNode = Cast<UK2Node_CallFunction>(Node);
			const UFunction* Function = CallNode != nullptr ? CallNode->GetTargetFunction() : nullptr;
			if (Function == nullptr
				|| !Function->GetName().Equals(Expected.FunctionName, ESearchCase::CaseSensitive))
			{
				OutReason = FString::Printf(
					TEXT("The created node no longer calls %s."),
					*Expected.FunctionName);
				return false;
			}
			UClass* OwnerClass = Function->GetOuterUClass();
			UClass* ExpectedOwner = FunctionOwnerForNodeType(Operation.NodeType);
			if (OwnerClass == nullptr
				|| ExpectedOwner == nullptr
				|| !OwnerClass->GetPathName().Equals(
					ExpectedOwner->GetPathName(),
					ESearchCase::CaseSensitive))
			{
				OutReason = TEXT("The created function node has the wrong owner class.");
				return false;
			}
			if (!Function->HasAnyFunctionFlags(FUNC_BlueprintPure)
				|| !Function->HasAnyFunctionFlags(FUNC_Static))
			{
				OutReason = TEXT("The created function node is not a static BlueprintPure call.");
				return false;
			}
		}
		return true;
	}

	UEdGraphNode* FindNodeByGuid(UEdGraph* Graph, const FGuid& Guid)
	{
		if (Graph == nullptr)
		{
			return nullptr;
		}
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == Guid)
			{
				return Node;
			}
		}
		return nullptr;
	}

	/** Remove nodes in reverse order; returns false when one survives. */
	bool RemoveCreatedNodes(UBlueprint* Blueprint, UEdGraph* Graph, TArray<UEdGraphNode*>& Nodes)
	{
		bool bAllRemoved = true;
		for (int32 Index = Nodes.Num() - 1; Index >= 0; --Index)
		{
			UEdGraphNode* Node = Nodes[Index];
			if (Node == nullptr)
			{
				continue;
			}
			Node->Modify();
			FBlueprintEditorUtils::RemoveNode(Blueprint, Node, true);
			if (Graph != nullptr && Graph->Nodes.Contains(Node))
			{
				bAllRemoved = false;
			}
		}
		Nodes.Reset();
		return bAllRemoved;
	}

	/** Exactly one pin with this name and direction, or a writer-coded failure. */
	UEdGraphPin* FindUniquePin(
		UEdGraphNode* Node,
		const FString& PinName,
		const EEdGraphPinDirection Direction,
		const TCHAR* Role,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Node == nullptr)
		{
			Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-reference-unknown"),
				TEXT("The edge endpoint node is unavailable."));
			return nullptr;
		}
		UEdGraphPin* Match = nullptr;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin != nullptr
				&& Pin->Direction == Direction
				// Pin names are FNames: case-insensitive, and their printed spelling
				// can change after a node rebuilds its pins (Index -> index).
				&& Pin->PinName.ToString().Equals(PinName, ESearchCase::IgnoreCase))
			{
				if (Match != nullptr)
				{
					Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-pin-ambiguous"),
						FString::Printf(
							TEXT("More than one %s pin named %s exists on node %s."),
							Role,
							*PinName,
							*Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)));
					return nullptr;
				}
				Match = Pin;
			}
		}
		if (Match == nullptr)
		{
			Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-pin-not-found"),
				FString::Printf(
					TEXT("Node %s has no %s pin named %s."),
					*Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
					Role,
					*PinName));
		}
		return Match;
	}

	/** Resolve one non-tempId endpoint against the live graph. */
	bool ResolveLiveEdgeEndpoint(
		UEdGraph* Graph,
		const bool bFrom,
		UEAgentKitBlueprintGraphEditExperimental::FAtomicEdgeOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const FString& NodeGuidText = bFrom ? InOutOperation.FromNodeGuid : InOutOperation.ToNodeGuid;
		const FString& PinName = bFrom ? InOutOperation.FromPin : InOutOperation.ToPin;
		FGuid NodeGuid;
		if (!FGuid::Parse(NodeGuidText, NodeGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-reference-unknown"),
				FString::Printf(TEXT("'%s' is not a canonical node GUID."), *NodeGuidText));
		}
		UEdGraphNode* Node = FindNodeByGuid(Graph, NodeGuid);
		if (Node == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-node-not-found"),
				FString::Printf(TEXT("No node with GUID %s exists in the focused graph."), *NodeGuidText));
		}
		if (Cast<UK2Node>(Node) == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-node-not-k2"),
				TEXT("Edge endpoints must be K2 Blueprint nodes."));
		}
		UEdGraphPin* Pin = FindUniquePin(
			Node,
			PinName,
			bFrom ? EGPD_Output : EGPD_Input,
			bFrom ? TEXT("output") : TEXT("input"),
			OutErrorCode,
			OutErrorMessage);
		if (Pin == nullptr)
		{
			return false;
		}
		FString Type;
		if (!ExistingPinTypeToken(Pin->PinType, Type))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-type-not-supported"),
				FString::Printf(
					TEXT("Pin %s carries a type this slice cannot connect (only exec/bool/int/double/string are supported)."),
					*PinName));
		}
		if (!bFrom && !InOutOperation.bDisconnect)
		{
			// A connect needs a free target input; a disconnect needs exactly the
			// opposite, so the "not already linked" rule belongs to connect only.
			if (Pin->LinkedTo.Num() != 0)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-existing-pin-already-linked"),
					TEXT("The target input pin is already connected; this slice never breaks an existing link."));
			}
		}
		if (bFrom)
		{
			InOutOperation.FromPinId = Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
			InOutOperation.FromType = Type;
		}
		else
		{
			InOutOperation.ToPinId = Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
			InOutOperation.ToType = Type;
		}
		return true;
	}

	/** Resolve one tempId endpoint from the declaring createNode operation. */
	bool ResolveDeclaredEdgeEndpoint(
		const UEAgentKitBlueprintGraphEditExperimental::FAtomicNodeOperation& Node,
		const bool bFrom,
		UEAgentKitBlueprintGraphEditExperimental::FAtomicEdgeOperation& InOutOperation,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const FString& PinName = bFrom ? InOutOperation.FromPin : InOutOperation.ToPin;
		const TArray<FString>& Pins = bFrom ? Node.OutputPins : Node.InputPins;
		const TMap<FString, FString>& Types = bFrom ? Node.OutputPinTypes : Node.InputPinTypes;
		if (!Pins.Contains(PinName))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-pin-not-allowed"),
				FString::Printf(
					TEXT("%s is not a supported %s pin of %s; allowed: %s."),
					*PinName,
					bFrom ? TEXT("output") : TEXT("input"),
					*Node.NodeType,
					*FString::Join(Pins, TEXT(", "))));
		}
		const FString Type = Types.FindRef(PinName);
		if (Type.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-pin-not-allowed"),
				FString::Printf(TEXT("%s has no declared type on %s."), *PinName, *Node.NodeType));
		}
		if (bFrom)
		{
			InOutOperation.FromType = Type;
		}
		else
		{
			InOutOperation.ToType = Type;
		}
		return true;
	}

	/** Break one edge and verify both pins stopped pointing at each other. */
	bool BreakEdge(
		UEdGraphPin* FromPin,
		UEdGraphPin* ToPin,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (FromPin == nullptr || ToPin == nullptr)
		{
			return true;
		}
		FromPin->BreakLinkTo(ToPin);
		if (FromPin->GetOwningNode() != nullptr)
		{
			FromPin->GetOwningNode()->PinConnectionListChanged(FromPin);
		}
		if (ToPin->GetOwningNode() != nullptr)
		{
			ToPin->GetOwningNode()->PinConnectionListChanged(ToPin);
		}
		if (FromPin->LinkedTo.Contains(ToPin) || ToPin->LinkedTo.Contains(FromPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-disconnect-failed"),
				TEXT("The Editor did not remove the exact edge."));
		}
		return true;
	}

	/**
	 * Re-create one exact edge and verify both pins point at each other.
	 *
	 * This is the inverse of BreakEdge and is only used to put an edge back after
	 * a failed Apply (rollback) or to undo a planned disconnect; the forward path
	 * always goes through the K2 schema instead.
	 */
	bool RelinkEdge(
		UEdGraphPin* FromPin,
		UEdGraphPin* ToPin,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (FromPin == nullptr || ToPin == nullptr)
		{
			return true;
		}
		if (!FromPin->LinkedTo.Contains(ToPin))
		{
			FromPin->MakeLinkTo(ToPin);
		}
		if (FromPin->GetOwningNode() != nullptr)
		{
			FromPin->GetOwningNode()->PinConnectionListChanged(FromPin);
		}
		if (ToPin->GetOwningNode() != nullptr)
		{
			ToPin->GetOwningNode()->PinConnectionListChanged(ToPin);
		}
		if (!FromPin->LinkedTo.Contains(ToPin) || !ToPin->LinkedTo.Contains(FromPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-disconnect-failed"),
				TEXT("The Editor did not restore the exact edge."));
		}
		return true;
	}

	/** Locate one edge's two pins by resolved identity; either may be null. */
	void FindEdgePins(
		UEdGraph* Graph,
		const UEAgentKitBlueprintGraphEditExperimental::FAtomicEdgeOperation& Operation,
		UEdGraphPin*& OutFromPin,
		UEdGraphPin*& OutToPin)
	{
		OutFromPin = nullptr;
		OutToPin = nullptr;
		FGuid FromGuid;
		FGuid ToGuid;
		if (Graph == nullptr
			|| !FGuid::Parse(Operation.FromNodeGuid, FromGuid)
			|| !FGuid::Parse(Operation.ToNodeGuid, ToGuid))
		{
			return;
		}
		UEdGraphNode* FromNode = FindNodeByGuid(Graph, FromGuid);
		UEdGraphNode* ToNode = FindNodeByGuid(Graph, ToGuid);
		if (FromNode != nullptr)
		{
			for (UEdGraphPin* Pin : FromNode->Pins)
			{
				if (Pin != nullptr
					&& Pin->Direction == EGPD_Output
					&& Pin->PinName.ToString().Equals(Operation.FromPin, ESearchCase::IgnoreCase))
				{
					OutFromPin = Pin;
					break;
				}
			}
		}
		if (ToNode != nullptr)
		{
			for (UEdGraphPin* Pin : ToNode->Pins)
			{
				if (Pin != nullptr
					&& Pin->Direction == EGPD_Input
					&& Pin->PinName.ToString().Equals(Operation.ToPin, ESearchCase::IgnoreCase))
				{
					OutToPin = Pin;
					break;
				}
			}
		}
	}

}

FString UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicNodeStateFingerprint(UEdGraph* Graph)
{
	return ComputeNodeStateFingerprint(Graph);
}

bool UEAgentKitBlueprintGraphEditExperimental::TryResolveAtomicNodeOperation(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	FAtomicNodeOperation& InOutOperation,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	// Everything here is read-only: nodeType whitelist, coordinate bound and the
	// exact K2 schema.  The resolved class/function fields are what the
	// transaction is later allowed to write.
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact graph is unavailable."));
	}
	if (Graph->GetSchema() == nullptr || !Graph->GetSchema()->IsA<UEdGraphSchema_K2>())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-schema-not-supported"),
			TEXT("Experimental graph edit supports only K2 Blueprint graphs."));
	}
	const bool bAccessorType = InOutOperation.NodeType.Equals(TEXT("variable_get"), ESearchCase::CaseSensitive)
		|| InOutOperation.NodeType.Equals(TEXT("variable_set"), ESearchCase::CaseSensitive);
	const bool bActionSpawn = !InOutOperation.ActionId.IsEmpty();
	if (!bAccessorType && !bActionSpawn && NodeClassForType(InOutOperation.NodeType) == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-node-type-not-allowed"),
			FString::Printf(
				TEXT("The atomic patch creates only these nodeTypes: branch, sequence, not_bool, bool_or, int_add, int_equal, double_add, string_concat, variable_get, variable_set (got '%s')."),
				*InOutOperation.NodeType));
	}
	if (InOutOperation.X < -MaxCoordinate || InOutOperation.X > MaxCoordinate
		|| InOutOperation.Y < -MaxCoordinate || InOutOperation.Y > MaxCoordinate)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-parameters"),
			FString::Printf(
				TEXT("x and y must be integers between -%d and %d."),
				MaxCoordinate,
				MaxCoordinate));
	}

	FNodeSpec Spec;
	Spec.NodeType = InOutOperation.NodeType;
	Spec.X = InOutOperation.X;
	Spec.Y = InOutOperation.Y;
	if (bActionSpawn)
	{
		// Phase A: the action id is the whole identity.  Resolve it through the
		// registry, apply the descriptor's own admission fields, and record what
		// the engine will spawn.  No pin contract is predicted here: the created
		// node's pins are read back after creation.
		if (!InOutOperation.NodeType.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-spawn-action-id-conflict"),
				TEXT("A createNode operation takes exactly one of nodeType and actionId."));
		}
		const FNativeSpawnerResolution Resolution =
			FNativeNodeRegistry::Get().ResolveSpawnerForActionId(InOutOperation.ActionId);
		if (!Resolution.bOk)
		{
			OutErrorCode = Resolution.ErrorCode.IsEmpty()
				? TEXT("graph-spawn-action-unknown")
				: Resolution.ErrorCode;
			OutErrorMessage = Resolution.ErrorMessage.IsEmpty()
				? FString(TEXT("The action could not be resolved."))
				: Resolution.ErrorMessage;
			return false;
		}
		if (!CheckActionSpawnAdmissible(Resolution, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		InOutOperation.bActionSpawn = true;
		InOutOperation.NodeClassPath = Resolution.NodeClassPath;
		InOutOperation.SpawnerClass = Resolution.SpawnerClassPath;
		InOutOperation.ActionKind = Resolution.ActionKind;
		InOutOperation.ActionPayload = Resolution.PayloadValue;
		InOutOperation.FunctionName.Reset();
		InOutOperation.InputPins.Reset();
		InOutOperation.OutputPins.Reset();
		InOutOperation.InputPinTypes.Reset();
		InOutOperation.OutputPinTypes.Reset();
		return true;
	}
	if (bAccessorType)
	{
		// Slice 15: resolve the accessor through the single writer's own rules, so
		// the identity/type checks and the engine pin contract cannot drift.  A
		// variable that does not exist yet is refused here, before any transaction.
		if (!IsSupportedVariableAccessorGraph(Blueprint, Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-graph-not-supported"),
				TEXT("Variable accessor creation is limited to top-level event, function and macro graphs."));
		}
		Spec.bVariableAccessor = true;
		Spec.VariableName = InOutOperation.VariableName;
		Spec.VariableGuid = InOutOperation.VariableGuid;
		Spec.VariableType = InOutOperation.VariableType;
		if (!ResolveVariableNode(Spec, Blueprint, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		InOutOperation.bVariableAccessor = true;
		InOutOperation.VariableName = Spec.VariableName;
		InOutOperation.VariableGuid = Spec.VariableGuid;
		InOutOperation.VariableType = Spec.VariableType;
		InOutOperation.VariableAccess = Spec.VariableAccess;
		InOutOperation.NodeClassPath = Spec.NodeClassPath;
		InOutOperation.FunctionName.Reset();
		InOutOperation.InputPins = Spec.InputPins;
		InOutOperation.OutputPins = Spec.OutputPins;
		InOutOperation.InputPinTypes = Spec.InputPinTypes;
		InOutOperation.OutputPinTypes = Spec.OutputPinTypes;
		return true;
	}
	if (!ConfigureNodeType(
			InOutOperation.NodeType,
			Spec,
			OutErrorCode,
			OutErrorMessage))
	{
		return false;
	}
	// The fixed functions must exist in this engine before the transaction opens;
	// a missing function is not something the rollback path should discover.
	if (!Spec.FunctionName.IsEmpty())
	{
		UClass* FunctionOwner = FunctionOwnerForNodeType(Spec.NodeType);
		if (FunctionOwner == nullptr
			|| FunctionOwner->FindFunctionByName(FName(*Spec.FunctionName)) == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-spawn-failed"),
				FString::Printf(TEXT("The function %s is unavailable."), *Spec.FunctionName));
		}
	}
	InOutOperation.NodeClassPath = Spec.NodeClassPath;
	InOutOperation.FunctionName = Spec.FunctionName;
	InOutOperation.InputPins = Spec.InputPins;
	InOutOperation.OutputPins = Spec.OutputPins;
	InOutOperation.InputPinTypes = Spec.InputPinTypes;
	InOutOperation.OutputPinTypes = Spec.OutputPinTypes;
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryApplyAtomicNodeOperations(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	const TArray<FAtomicNodeOperation>& Operations,
	TArray<FGuid>& OutCreatedGuids,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	OutCreatedGuids.Reset();
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	if (Operations.IsEmpty() || Operations.Num() > MaxNodes)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-nodes"),
			FString::Printf(
				TEXT("An atomic node Apply takes between 1 and %d operations."),
				MaxNodes));
	}

	TArray<UEdGraphNode*> CreatedNodes;
	for (const FAtomicNodeOperation& Operation : Operations)
	{
		FNodeSpec Spec;
		Spec.NodeType = Operation.NodeType;
		Spec.FunctionName = Operation.FunctionName;
		Spec.X = Operation.X;
		Spec.Y = Operation.Y;
		Spec.bVariableAccessor = Operation.bVariableAccessor;
		Spec.VariableName = Operation.VariableName;
		Spec.VariableGuid = Operation.VariableGuid;
		Spec.VariableType = Operation.VariableType;
		Spec.VariableAccess = Operation.VariableAccess;
		UEdGraphNode* Node = nullptr;
		if (Operation.bActionSpawn)
		{
			if (!CreateActionNode(Graph, Operation, Node, OutErrorCode, OutErrorMessage))
			{
				if (Node != nullptr && Graph->Nodes.Contains(Node))
				{
					CreatedNodes.Add(Node);
				}
				if (!RemoveCreatedNodes(Blueprint, Graph, CreatedNodes))
				{
					OutErrorMessage += TEXT(" The atomic Apply could not remove a partially created node.");
				}
				return false;
			}
		}
		else if (Operation.bVariableAccessor)
		{
			if (!CreateVariableAccessorNode(
					Blueprint,
					Graph,
					Operation.VariableName,
					Operation.VariableType,
					Operation.NodeType.Equals(TEXT("variable_get"), ESearchCase::CaseSensitive),
					Operation.X,
					Operation.Y,
					Node,
					OutErrorCode,
					OutErrorMessage))
			{
				if (!RemoveCreatedNodes(Blueprint, Graph, CreatedNodes))
				{
					OutErrorMessage += TEXT(" The atomic Apply could not remove a partially created node.");
				}
				return false;
			}
		}
		else if (!CreatePrimitiveNode(Graph, Spec, Node, OutErrorCode, OutErrorMessage))
		{
			// A partially created node is still a write; remove it before the
			// caller can observe an empty result.
			if (Node != nullptr && Graph->Nodes.Contains(Node))
			{
				CreatedNodes.Add(Node);
			}
			if (!RemoveCreatedNodes(Blueprint, Graph, CreatedNodes))
			{
				OutErrorMessage += TEXT(" The atomic Apply could not remove a partially created node.");
			}
			return false;
		}
		FString Reason;
		FAtomicNodeOperation Checked = Operation;
		Checked.NodeGuid = Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
		if (!NodeMatchesAtomicOperation(Node, Checked, Reason))
		{
			CreatedNodes.Add(Node);
			RemoveCreatedNodes(Blueprint, Graph, CreatedNodes);
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-contract-failed"),
				Reason);
		}
		CreatedNodes.Add(Node);
		OutCreatedGuids.Add(Node->NodeGuid);
	}
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryCheckAtomicNodeOperations(
	UEdGraph* Graph,
	const TArray<FAtomicNodeOperation>& Operations,
	bool bExpectAfter,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact graph is unavailable."));
	}
	for (const FAtomicNodeOperation& Operation : Operations)
	{
		FGuid NodeGuid;
		if (Operation.NodeGuid.Len() != 36
			|| !FGuid::Parse(Operation.NodeGuid, NodeGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-contract-failed"),
				TEXT("The resolved NodeGuid is not a canonical GUID."));
		}
		const UEdGraphNode* Node = FindNodeByGuid(Graph, NodeGuid);
		if (!bExpectAfter)
		{
			if (Node != nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-node-restore-failed"),
					FString::Printf(
						TEXT("Node %s is still present after the restore."),
						*Operation.NodeGuid));
			}
			continue;
		}
		FString Reason;
		if (!NodeMatchesAtomicOperation(Node, Operation, Reason))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-contract-failed"),
				Reason);
		}
	}
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryRestoreAtomicNodeOperations(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	const TArray<FAtomicNodeOperation>& Operations,
	int32 AppliedCount,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	if (AppliedCount < 0 || AppliedCount > Operations.Num())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-nodes"),
			TEXT("AppliedCount must address the prefix of the operation list."));
	}
	TArray<UEdGraphNode*> NodesToRemove;
	for (int32 Index = 0; Index < AppliedCount; ++Index)
	{
		FGuid NodeGuid;
		if (Operations[Index].NodeGuid.Len() != 36
			|| !FGuid::Parse(Operations[Index].NodeGuid, NodeGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-restore-failed"),
				TEXT("The applied operation has no canonical NodeGuid to remove."));
		}
		UEdGraphNode* Node = FindNodeByGuid(Graph, NodeGuid);
		if (Node != nullptr)
		{
			NodesToRemove.Add(Node);
		}
	}
	if (!RemoveCreatedNodes(Blueprint, Graph, NodesToRemove))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-node-restore-failed"),
			TEXT("At least one created node survived its removal."));
	}
	return true;
}

FString UEAgentKitBlueprintGraphEditExperimental::ComputeAtomicEdgeStateFingerprint(UEdGraph* Graph)
{
	// One implementation, already used by the rewire writer's Preview/Apply/Save:
	// its hash covers the exact endpoints (node GUID + pin name + pin GUID), which
	// is the property the atomic patch needs.
	FString Hash;
	if (!UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(Graph, Hash))
	{
		return FString();
	}
	return Hash;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryResolveAtomicEdgeOperation(
	UEdGraph* Graph,
	FAtomicEdgeOperation& InOutOperation,
	FTempNodeResolver ResolveTempNode,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact graph is unavailable."));
	}
	if (Graph->GetSchema() == nullptr || !Graph->GetSchema()->IsA<UEdGraphSchema_K2>())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-schema-not-supported"),
			TEXT("Experimental graph edit supports only K2 Blueprint graphs."));
	}
	if (InOutOperation.FromPin.IsEmpty() || InOutOperation.ToPin.IsEmpty())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-parameters"),
			TEXT("fromPin and toPin must be non-empty pin names."));
	}
	const bool bFromDeclared = !InOutOperation.FromTempId.IsEmpty();
	const bool bToDeclared = !InOutOperation.ToTempId.IsEmpty();
	const bool bFromGuid = !InOutOperation.FromNodeGuid.IsEmpty();
	const bool bToGuid = !InOutOperation.ToNodeGuid.IsEmpty();
	if (bFromDeclared == bFromGuid || bToDeclared == bToGuid)
	{
		// Exactly one identity form per endpoint.  A patch that fills both (or
		// neither) is an internal contract violation, never a caller-visible shape.
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-parameters"),
			TEXT("Each edge endpoint must carry exactly one of nodeGuid or tempId."));
	}

	if (bFromDeclared)
	{
		const FAtomicNodeOperation* Declared = ResolveTempNode(InOutOperation.FromTempId);
		if (Declared == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-unknown-temp-id"),
				FString::Printf(
					TEXT("tempId '%s' was not declared by an earlier createNode operation."),
					*InOutOperation.FromTempId));
		}
		if (!ResolveDeclaredEdgeEndpoint(*Declared, /*bFrom=*/true, InOutOperation, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
	}
	else if (!ResolveLiveEdgeEndpoint(Graph, /*bFrom=*/true, InOutOperation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	if (bToDeclared)
	{
		const FAtomicNodeOperation* Declared = ResolveTempNode(InOutOperation.ToTempId);
		if (Declared == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-patch-unknown-temp-id"),
				FString::Printf(
					TEXT("tempId '%s' was not declared by an earlier createNode operation."),
					*InOutOperation.ToTempId));
		}
		if (!ResolveDeclaredEdgeEndpoint(*Declared, /*bFrom=*/false, InOutOperation, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
	}
	else if (!ResolveLiveEdgeEndpoint(Graph, /*bFrom=*/false, InOutOperation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}

	// A node cannot connect to itself.  Two tempIds compare by name; two GUIDs
	// compare by GUID; a mixed pair cannot be the same node because the tempId
	// node does not exist yet.
	if (bFromDeclared && bToDeclared)
	{
		if (InOutOperation.FromTempId.Equals(InOutOperation.ToTempId, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-self-connection"),
				TEXT("A node cannot connect to itself in this experimental slice."));
		}
	}
	else if (!bFromDeclared && !bToDeclared)
	{
		if (InOutOperation.FromNodeGuid.Equals(InOutOperation.ToNodeGuid, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-self-connection"),
				TEXT("A node cannot connect to itself in this experimental slice."));
		}
	}

	// A wildcard endpoint has no type yet: the K2 schema derives it from the other
	// side when the edge is created (and the node then rebuilds its pins).  Two
	// concrete types still have to match exactly, and wildcard-vs-wildcard is
	// allowed because the schema resolves it.
	const bool bFromWildcard = InOutOperation.FromType.Equals(TEXT("wildcard"), ESearchCase::CaseSensitive);
	const bool bToWildcard = InOutOperation.ToType.Equals(TEXT("wildcard"), ESearchCase::CaseSensitive);
	if (!bFromWildcard
		&& !bToWildcard
		&& !InOutOperation.FromType.Equals(InOutOperation.ToType, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-pin-type-mismatch"),
			FString::Printf(
				TEXT("Cannot connect %s (%s) to %s (%s); data kinds must match exactly."),
				*InOutOperation.FromPin,
				*InOutOperation.FromType,
				*InOutOperation.ToPin,
				*InOutOperation.ToType));
	}

	// A disconnect removes an edge that must already be there, so it never
	// consults the create-connection response: it proves the exact edge instead.
	if (InOutOperation.bDisconnect)
	{
		if (bFromDeclared || bToDeclared)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-disconnect-edge-not-found"),
				TEXT("A disconnect endpoint must be an existing node; a node this patch creates has no edge to remove."));
		}
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		FindEdgePins(Graph, InOutOperation, FromPin, ToPin);
		if (FromPin == nullptr || ToPin == nullptr || !FromPin->LinkedTo.Contains(ToPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-disconnect-edge-not-found"),
				FString::Printf(
					TEXT("The edge %s.%s -> %s.%s is not in the graph."),
					*InOutOperation.FromNodeGuid,
					*InOutOperation.FromPin,
					*InOutOperation.ToNodeGuid,
					*InOutOperation.ToPin));
		}
		if (ToPin->LinkedTo.Num() != 1)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-disconnect-ambiguous"),
				TEXT("The declared existing input has more than one link; refusing an ambiguous disconnect."));
		}
		return true;
	}

	// The K2 schema is only consulted when both endpoints exist live.  A tempId
	// endpoint mirrors the single writer's new-node path, which also relies on the
	// exact type match above plus TryCreateConnection.
	if (!bFromDeclared && !bToDeclared)
	{
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		FindEdgePins(Graph, InOutOperation, FromPin, ToPin);
		if (!ValidateSchemaConnection(Graph, FromPin, ToPin, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
	}
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryApplyAtomicEdgeOperations(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	TArray<FAtomicEdgeOperation>& InOutOperations,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Blueprint == nullptr || Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact Blueprint graph is unavailable."));
	}
	if (InOutOperations.IsEmpty() || InOutOperations.Num() > MaxConnections)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-connections"),
			FString::Printf(
				TEXT("An atomic edge Apply takes between 1 and %d operations."),
				MaxConnections));
	}

	// Edges this call changed, so a failure inside the same call can put them
	// back: created edges are broken again, removed (disconnected) edges are
	// re-created.  Both lists undo in reverse order.
	TArray<TPair<UEdGraphPin*, UEdGraphPin*>> CreatedEdges;
	TArray<TPair<UEdGraphPin*, UEdGraphPin*>> RemovedEdges;
	auto RollbackChangedEdges = [&]()
	{
		for (int32 Index = RemovedEdges.Num() - 1; Index >= 0; --Index)
		{
			FString IgnoredCode;
			FString IgnoredMessage;
			RelinkEdge(RemovedEdges[Index].Key, RemovedEdges[Index].Value, IgnoredCode, IgnoredMessage);
		}
		RemovedEdges.Reset();
		for (int32 Index = CreatedEdges.Num() - 1; Index >= 0; --Index)
		{
			FString IgnoredCode;
			FString IgnoredMessage;
			BreakEdge(CreatedEdges[Index].Key, CreatedEdges[Index].Value, IgnoredCode, IgnoredMessage);
		}
		CreatedEdges.Reset();
	};

	for (FAtomicEdgeOperation& Operation : InOutOperations)
	{
		// tempIds must already be translated by the caller; a leftover one means
		// the patch tried to write an edge to a node that does not exist.
		if (Operation.FromTempId.IsEmpty() == false || Operation.ToTempId.IsEmpty() == false)
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-reference-unknown"),
				TEXT("An unresolved tempId reached the edge Apply; nothing was written."));
		}
		FGuid FromGuid;
		FGuid ToGuid;
		if (!FGuid::Parse(Operation.FromNodeGuid, FromGuid)
			|| !FGuid::Parse(Operation.ToNodeGuid, ToGuid))
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-node-reference-unknown"),
				TEXT("An edge endpoint did not carry a canonical node GUID."));
		}
		UEdGraphNode* FromNode = FindNodeByGuid(Graph, FromGuid);
		UEdGraphNode* ToNode = FindNodeByGuid(Graph, ToGuid);
		if (FromNode == nullptr || ToNode == nullptr)
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-node-not-found"),
				TEXT("An edge endpoint node disappeared before the transaction could connect it."));
		}
		if (Cast<UK2Node>(FromNode) == nullptr || Cast<UK2Node>(ToNode) == nullptr)
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-node-not-k2"),
				TEXT("Edge endpoints must be K2 Blueprint nodes."));
		}
		if (FromNode == ToNode)
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-self-connection"),
				TEXT("A node cannot connect to itself in this experimental slice."));
		}
		UEdGraphPin* FromPin = FindUniquePin(
			FromNode,
			Operation.FromPin,
			EGPD_Output,
			TEXT("output"),
			OutErrorCode,
			OutErrorMessage);
		if (FromPin == nullptr)
		{
			RollbackChangedEdges();
			return false;
		}
		UEdGraphPin* ToPin = FindUniquePin(
			ToNode,
			Operation.ToPin,
			EGPD_Input,
			TEXT("input"),
			OutErrorCode,
			OutErrorMessage);
		if (ToPin == nullptr)
		{
			RollbackChangedEdges();
			return false;
		}
		FString FromType;
		FString ToType;
		if (!ExistingPinTypeToken(FromPin->PinType, FromType)
			|| !ExistingPinTypeToken(ToPin->PinType, ToType))
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-type-not-supported"),
				TEXT("An edge pin carries a type this slice cannot connect."));
		}
		// The PLAN may have seen a wildcard: at that point the schema had not
		// derived the type yet.  In that case the exact comparison here would be
		// wrong (the first edge may already have typed this node), so the K2
		// schema decides below -- and its four-way response is still honoured
		// (MAKE only; conversion/promotion/break-others stay rejected).
		const bool bPlanWildcard =
			Operation.FromType.Equals(TEXT("wildcard"), ESearchCase::CaseSensitive)
			|| Operation.ToType.Equals(TEXT("wildcard"), ESearchCase::CaseSensitive);
		if (!bPlanWildcard
			&& !FromType.Equals(ToType, ESearchCase::CaseSensitive)
			&& !FromType.Equals(TEXT("wildcard"), ESearchCase::CaseSensitive)
			&& !ToType.Equals(TEXT("wildcard"), ESearchCase::CaseSensitive))
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-pin-type-mismatch"),
				FString::Printf(
					TEXT("Cannot connect %s (%s) to %s (%s); data kinds must match exactly."),
					*Operation.FromPin,
					*FromType,
					*Operation.ToPin,
					*ToType));
		}
		// A disconnect is the same two pins read the other way round: the edge
		// must be the only link on its target, and removing it is the whole write.
		if (Operation.bDisconnect)
		{
			if (!FromPin->LinkedTo.Contains(ToPin))
			{
				RollbackChangedEdges();
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-disconnect-edge-not-found"),
					TEXT("The edge to remove disappeared before the transaction could break it."));
			}
			if (ToPin->LinkedTo.Num() != 1)
			{
				RollbackChangedEdges();
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-disconnect-ambiguous"),
					TEXT("The target input has more than one link; refusing an ambiguous disconnect."));
			}
			if (!BreakEdge(FromPin, ToPin, OutErrorCode, OutErrorMessage))
			{
				RollbackChangedEdges();
				return false;
			}
			Operation.FromNodeGuid =
				FromNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
			Operation.ToNodeGuid =
				ToNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
			Operation.FromPinId = FromPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
			Operation.ToPinId = ToPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
			Operation.FromType = FromType;
			Operation.ToType = ToType;
			RemovedEdges.Add(TPair<UEdGraphPin*, UEdGraphPin*>(FromPin, ToPin));
			continue;
		}
		if (ToPin->LinkedTo.Num() != 0)
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-already-linked"),
				TEXT("The target input pin became connected; this slice never breaks an existing link."));
		}
		if (FromPin->LinkedTo.Contains(ToPin))
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-connection-duplicate"),
				TEXT("The exact edge already exists."));
		}
		// Both endpoints were live at plan time exactly when the patch already
		// resolved their pin GUIDs; that is the case the single writer also sends
		// through the schema check.  A plan-time wildcard also needs the check even
		// for a tempId endpoint: the type it will actually connect to only exists
		// now, and conversion/promotion must still be refused before any write.
		if ((!Operation.FromPinId.IsEmpty() && !Operation.ToPinId.IsEmpty()) || bPlanWildcard)
		{
			if (!ValidateSchemaConnection(Graph, FromPin, ToPin, OutErrorCode, OutErrorMessage))
			{
				RollbackChangedEdges();
				return false;
			}
		}
		if (!Graph->GetSchema()->TryCreateConnection(FromPin, ToPin))
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-connection-failed"),
				TEXT("The Editor rejected the planned connection."));
		}
		// A wildcard node REBUILDS its pins inside TryCreateConnection (that is how
		// the type is derived), so the pin objects captured above may already be
		// gone.  Re-find both endpoints by node GUID + pin name and read the edge
		// and the resolved types back from the live pins.
		UEdGraphPin* CreatedFromPin = FindUniquePin(
			FromNode,
			Operation.FromPin,
			EGPD_Output,
			TEXT("output"),
			OutErrorCode,
			OutErrorMessage);
		UEdGraphPin* CreatedToPin = FindUniquePin(
			ToNode,
			Operation.ToPin,
			EGPD_Input,
			TEXT("input"),
			OutErrorCode,
			OutErrorMessage);
		if (CreatedFromPin == nullptr || CreatedToPin == nullptr)
		{
			RollbackChangedEdges();
			return false;
		}
		if (!CreatedFromPin->LinkedTo.Contains(CreatedToPin)
			|| CreatedToPin->LinkedTo.Num() != 1
			|| !CreatedToPin->LinkedTo.Contains(CreatedFromPin))
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-connection-failed"),
				TEXT("The created edge did not read back as the only link on its target input."));
		}
		FString AppliedFromType;
		FString AppliedToType;
		if (!ExistingPinTypeToken(CreatedFromPin->PinType, AppliedFromType)
			|| !ExistingPinTypeToken(CreatedToPin->PinType, AppliedToType))
		{
			RollbackChangedEdges();
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-type-not-supported"),
				TEXT("The connected pins carry a type this slice cannot read back."));
		}
		Operation.FromNodeGuid = FromNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
		Operation.ToNodeGuid = ToNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
		Operation.FromPinId = CreatedFromPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
		Operation.ToPinId = CreatedToPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
		// Report the ACTUAL types after the schema derived them (a wildcard plan
		// endpoint becomes the concrete type here).
		Operation.FromType = AppliedFromType;
		Operation.ToType = AppliedToType;
		CreatedEdges.Add(TPair<UEdGraphPin*, UEdGraphPin*>(CreatedFromPin, CreatedToPin));
	}
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryCheckAtomicEdgeOperations(
	UEdGraph* Graph,
	const TArray<FAtomicEdgeOperation>& Operations,
	bool bExpectAfter,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact graph is unavailable."));
	}
	for (const FAtomicEdgeOperation& Operation : Operations)
	{
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		FindEdgePins(Graph, Operation, FromPin, ToPin);
		if (Operation.bDisconnect)
		{
			// A disconnect expects the opposite of a connect: gone after Apply,
			// back after Undo.  Both nodes always pre-exist for this operation, so
			// a missing endpoint is a real read-back failure either way.
			const bool bWantLinked = !bExpectAfter;
			const bool bLinked = FromPin != nullptr && ToPin != nullptr
				&& (FromPin->LinkedTo.Contains(ToPin) || ToPin->LinkedTo.Contains(FromPin));
			if (bWantLinked
				&& (!bLinked || ToPin->LinkedTo.Num() != 1
					|| !ToPin->LinkedTo.Contains(FromPin)))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-readback-failed"),
					FString::Printf(
						TEXT("Edge %s.%s -> %s.%s is not restored as the only link on its target input."),
						*Operation.FromNodeGuid,
						*Operation.FromPin,
						*Operation.ToNodeGuid,
						*Operation.ToPin));
			}
			if (!bWantLinked && bLinked)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-readback-failed"),
					FString::Printf(
						TEXT("Edge %s.%s -> %s.%s is still present after the disconnect."),
						*Operation.FromNodeGuid,
						*Operation.FromPin,
						*Operation.ToNodeGuid,
						*Operation.ToPin));
			}
			continue;
		}
		if (!bExpectAfter)
		{
			// Undo of a patch that also created the nodes removes the nodes, so a
			// missing endpoint is exactly the restored state.
			if (FromPin != nullptr && ToPin != nullptr
				&& (FromPin->LinkedTo.Contains(ToPin) || ToPin->LinkedTo.Contains(FromPin)))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-readback-failed"),
					FString::Printf(
						TEXT("Edge %s.%s -> %s.%s is still present after the restore."),
						*Operation.FromNodeGuid,
						*Operation.FromPin,
						*Operation.ToNodeGuid,
						*Operation.ToPin));
			}
			continue;
		}
		if (FromPin == nullptr || ToPin == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-readback-failed"),
				FString::Printf(
					TEXT("Edge %s.%s -> %s.%s has a missing endpoint after Apply."),
					*Operation.FromNodeGuid,
					*Operation.FromPin,
					*Operation.ToNodeGuid,
					*Operation.ToPin));
		}
		if (!Operation.FromPinId.IsEmpty()
			&& !FromPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower).Equals(
				Operation.FromPinId,
				ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-readback-failed"),
				TEXT("The source pin no longer carries the resolved PinId."));
		}
		if (!Operation.ToPinId.IsEmpty()
			&& !ToPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower).Equals(
				Operation.ToPinId,
				ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-readback-failed"),
				TEXT("The target pin no longer carries the resolved PinId."));
		}
		if (!FromPin->LinkedTo.Contains(ToPin)
			|| ToPin->LinkedTo.Num() != 1
			|| !ToPin->LinkedTo.Contains(FromPin))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-readback-failed"),
				FString::Printf(
					TEXT("Edge %s.%s -> %s.%s is not the only link on its target input."),
					*Operation.FromNodeGuid,
					*Operation.FromPin,
					*Operation.ToNodeGuid,
					*Operation.ToPin));
		}
	}
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryRestoreAtomicEdgeOperations(
	UBlueprint* Blueprint,
	UEdGraph* Graph,
	const TArray<FAtomicEdgeOperation>& Operations,
	int32 AppliedCount,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Graph == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-graph-unavailable"),
			TEXT("The exact graph is unavailable."));
	}
	if (AppliedCount < 0 || AppliedCount > Operations.Num())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-connections"),
			TEXT("AppliedCount must address the prefix of the operation list."));
	}
	for (int32 Index = AppliedCount - 1; Index >= 0; --Index)
	{
		UEdGraphPin* FromPin = nullptr;
		UEdGraphPin* ToPin = nullptr;
		FindEdgePins(Graph, Operations[Index], FromPin, ToPin);
		if (FromPin == nullptr || ToPin == nullptr)
		{
			continue;
		}
		if (Operations[Index].bDisconnect)
		{
			// Restoring a disconnect means putting the removed edge back.
			if (FromPin->LinkedTo.Contains(ToPin))
			{
				continue;
			}
			if (!RelinkEdge(FromPin, ToPin, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			continue;
		}
		if (!FromPin->LinkedTo.Contains(ToPin) && !ToPin->LinkedTo.Contains(FromPin))
		{
			continue;
		}
		if (!BreakEdge(FromPin, ToPin, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
	}
	return true;
}
