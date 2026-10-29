#include "BlueprintGraphEditExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"
#include "BlueprintRewireExperimental.h"

#include "BlueprintNodeBinder.h"
#include "BlueprintNodeSpawner.h"
#include "BlueprintVariableNodeSpawner.h"
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
	constexpr int32 MaxNodes = 16;
	constexpr int32 MaxConnections = 24;
	constexpr int32 MaxConnectionsPerNode = 8;
	constexpr int32 MaxOwnedLiterals = 8;
	constexpr int32 MaxOwnedStringLiteral = 256;
	constexpr int32 MaxCoordinate = 100000;
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.graphpatch-edit-preview/1.0");
	const TCHAR* const BranchClassPath = TEXT("/Script/BlueprintGraph.K2Node_IfThenElse");
	const TCHAR* const SequenceClassPath = TEXT("/Script/BlueprintGraph.K2Node_ExecutionSequence");
	const TCHAR* const CallFunctionClassPath = TEXT("/Script/BlueprintGraph.K2Node_CallFunction");
	const TCHAR* const VariableGetClassPath = TEXT("/Script/BlueprintGraph.K2Node_VariableGet");
	const TCHAR* const VariableSetClassPath = TEXT("/Script/BlueprintGraph.K2Node_VariableSet");
	const TCHAR* const NotBoolFunctionName = TEXT("Not_PreBool");

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

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

	FString CanonicalCallFunctionSignature(
		const FString& OwnerClassPath,
		const FString& FunctionName,
		const TArray<FCallFunctionPinDef>& Pins)
	{
		TArray<FCallFunctionPinDef> SortedPins = Pins;
		SortedPins.Sort([](const FCallFunctionPinDef& Left, const FCallFunctionPinDef& Right)
		{
			return Left.Name < Right.Name;
		});
		FString Canonical = OwnerClassPath + TEXT("|") + FunctionName + TEXT("|");
		for (int32 Index = 0; Index < SortedPins.Num(); ++Index)
		{
			if (Index > 0)
			{
				Canonical += TEXT("|");
			}
			Canonical += SortedPins[Index].Name
				+ TEXT(":")
				+ SortedPins[Index].Direction
				+ TEXT(":")
				+ SortedPins[Index].Type;
		}
		return Canonical;
	}

	bool ComputeCallFunctionSignatureDigest(
		const FString& OwnerClassPath,
		const FString& FunctionName,
		const TArray<FCallFunctionPinDef>& Pins,
		FString& OutDigest)
	{
		FString HexDigest;
		if (!FBlueprintContextSha256::HashText(
			CanonicalCallFunctionSignature(OwnerClassPath, FunctionName, Pins),
			HexDigest))
		{
			return false;
		}
		OutDigest = TEXT("sha256:") + HexDigest;
		return true;
	}

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

	struct FExistingPinSpec
	{
		FString NodeGuid;
		FString PinGuid;
		FString PinName;
		FString Direction;
		FString Category;
		FString SubCategory;
		FString TypeToken;
		int32 LinkCount = 0;
	};

	struct FConnectionSpec
	{
		FString From;
		FString FromPin;
		FString FromType;
		FString To;
		FString ToPin;
		FString ToType;
		bool bFromExisting = false;
		bool bToExisting = false;
		FExistingPinSpec FromExisting;
		FExistingPinSpec ToExisting;
		UEdGraphPin* FromExistingPin = nullptr;
		UEdGraphPin* ToExistingPin = nullptr;
	};

	struct FEditPlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString PackageRevision;
		FString GraphFingerprint;
		FString BeforeEdgeSetHash;
		FString PlanId;
		FString Operation;
		bool bDisconnect = false;
		bool bHasExistingPin = false;
		TArray<FNodeSpec> Nodes;
		TArray<FConnectionSpec> Connections;
		TSharedPtr<FJsonObject> Preview;
	};

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
				TEXT("graph-edit-invalid-parameters"),
				TEXT("Graph edit parameters must be one JSON object."));
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			if (!Allowed.Contains(Pair.Key))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-unknown-parameter"),
					FString::Printf(TEXT("Unknown graph edit parameter: %s"), *Pair.Key));
			}
		}
		return true;
	}

	bool ReadString(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Key,
		FString& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Object.IsValid() || !Object->TryGetStringField(Key, OutValue) || OutValue.IsEmpty())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-parameters"),
				FString::Printf(TEXT("%s must be a non-empty string."), Key));
		}
		return true;
	}

	bool ReadInt(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Key,
		int32& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		double Number = 0.0;
		if (!Object.IsValid() || !Object->TryGetNumberField(Key, Number))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-parameters"),
				FString::Printf(TEXT("%s must be an integer."), Key));
		}
		const int32 Rounded = FMath::RoundToInt(Number);
		if (FMath::Abs(Number - static_cast<double>(Rounded)) > 0.0001
			|| Rounded < -MaxCoordinate
			|| Rounded > MaxCoordinate)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-parameters"),
				FString::Printf(TEXT("%s must be an integer between -%d and %d."), Key, MaxCoordinate, MaxCoordinate));
		}
		OutValue = Rounded;
		return true;
	}

	bool IsTempId(const FString& Value)
	{
		if (!Value.StartsWith(TEXT("tmp:"), ESearchCase::CaseSensitive) || Value.Len() > 40)
		{
			return false;
		}
		for (int32 Index = 4; Index < Value.Len(); ++Index)
		{
			const TCHAR Character = Value[Index];
			if (!FChar::IsAlnum(Character) && Character != TEXT('_') && Character != TEXT('-'))
			{
				return false;
			}
		}
		return Value.Len() > 4;
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

	bool ResolveVariableNodes(
		TArray<FNodeSpec>& Nodes,
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		bool bHasVariableAccessor = false;
		for (const FNodeSpec& Node : Nodes)
		{
			bHasVariableAccessor = bHasVariableAccessor || Node.bVariableAccessor;
		}
		if (!bHasVariableAccessor)
		{
			return true;
		}
		if (!IsSupportedVariableAccessorGraph(Blueprint, Graph))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-variable-graph-not-supported"),
				TEXT("Variable accessor creation is limited to top-level event, function and macro graphs."));
		}
		for (FNodeSpec& Node : Nodes)
		{
			if (!ResolveVariableNode(Node, Blueprint, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
		}
		return true;
	}

	bool IsSafePinTransportName(const FString& Value)
	{
		if (Value.IsEmpty() || Value.Len() > 128)
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

	bool IsOwnedFunctionName(const FString& Value)
	{
		if (Value.IsEmpty() || Value.Len() > 128
			|| (!FChar::IsAlpha(Value[0]) && Value[0] != TEXT('_')))
		{
			return false;
		}
		for (const TCHAR Character : Value)
		{
			if (!FChar::IsAlnum(Character) && Character != TEXT('_'))
			{
				return false;
			}
		}
		return true;
	}

	FString OwnedCallCanonicalSignature(
		const FString& OwningBlueprintPath,
		const FString& FunctionGraphGuid,
		const FString& FunctionName,
		const TArray<FCallFunctionPinDef>& Pins)
	{
		TArray<FCallFunctionPinDef> SortedPins = Pins;
		SortedPins.Sort([](const FCallFunctionPinDef& Left, const FCallFunctionPinDef& Right)
		{
			return Left.Name < Right.Name;
		});
		FString Canonical = OwningBlueprintPath
			+ TEXT("|")
			+ FunctionGraphGuid
			+ TEXT("|")
			+ FunctionName
			+ TEXT("|");
		for (int32 Index = 0; Index < SortedPins.Num(); ++Index)
		{
			if (Index > 0)
			{
				Canonical += TEXT("|");
			}
			Canonical += SortedPins[Index].Name
				+ TEXT(":")
				+ SortedPins[Index].Direction
				+ TEXT(":")
				+ SortedPins[Index].Type;
		}
		return Canonical;
	}

	bool ComputeOwnedCallDigest(
		const FString& OwningBlueprintPath,
		const FString& FunctionGraphGuid,
		const FString& FunctionName,
		const TArray<FCallFunctionPinDef>& Pins,
		FString& OutDigest)
	{
		FString HexDigest;
		if (!FBlueprintContextSha256::HashText(
			OwnedCallCanonicalSignature(
				OwningBlueprintPath,
				FunctionGraphGuid,
				FunctionName,
				Pins),
			HexDigest))
		{
			return false;
		}
		OutDigest = TEXT("sha256:") + HexDigest;
		return true;
	}

	bool CanonicalizeOwnedLiteral(
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
					TEXT("graph-edit-owned-literal-value-invalid"),
					TEXT("Boolean literal must be exactly true or false."));
			}
			OutValue = Raw;
			return true;
		}
		if (Type == TEXT("int"))
		{
			int32 Index = Raw.StartsWith(TEXT("-")) ? 1 : 0;
			if (Index >= Raw.Len()
				|| (Raw[Index] == TEXT('0') && Raw.Len() > Index + 1)
				|| Raw == TEXT("-0"))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-literal-value-invalid"),
					TEXT("Integer literal must be canonical signed int32 decimal."));
			}
			for (int32 CharIndex = Index; CharIndex < Raw.Len(); ++CharIndex)
			{
				if (!FChar::IsDigit(Raw[CharIndex]))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-literal-value-invalid"),
						TEXT("Integer literal must contain only digits after its sign."));
				}
			}
			const int64 Parsed = FCString::Atoi64(*Raw);
			if (Parsed < MIN_int32 || Parsed > MAX_int32)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-literal-value-invalid"),
					TEXT("Integer literal must fit signed 32-bit range."));
			}
			OutValue = Raw;
			return true;
		}
		if (Type == TEXT("double"))
		{
			int32 Index = Raw.StartsWith(TEXT("-")) ? 1 : 0;
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
							TEXT("graph-edit-owned-literal-value-invalid"),
							TEXT("Double literal must have one decimal point between digits."));
					}
					bSawDot = true;
				}
				else if (!FChar::IsDigit(Character))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-literal-value-invalid"),
						TEXT("Double literal must be finite decimal without exponent."));
				}
				else
				{
					bSawDigit = true;
				}
			}
			if (!bSawDigit || Raw.EndsWith(TEXT("."))
				|| Raw == TEXT("-0") || Raw == TEXT("-0.0")
				|| !FMath::IsFinite(FCString::Atod(*Raw)))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-literal-value-invalid"),
					TEXT("Double literal must be finite canonical decimal."));
			}
			OutValue = Raw;
			return true;
		}
		if (Type == TEXT("string"))
		{
			if (Raw.Len() > MaxOwnedStringLiteral)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-literal-value-invalid"),
					FString::Printf(
						TEXT("String literal must be at most %d characters."),
						MaxOwnedStringLiteral));
			}
			for (const TCHAR Character : Raw)
			{
				if (Character < 0x20 || Character == 0x7F)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-literal-value-invalid"),
						TEXT("String literal must not contain control characters."));
				}
			}
			OutValue = Raw;
			return true;
		}
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-owned-literal-type-not-allowed"),
			TEXT("Owned literal type must be bool, int, double or string."));
	}

	void AppendPinDefs(
		const TArray<FString>& InputPins,
		const TMap<FString, FString>& InputTypes,
		const TArray<FString>& OutputPins,
		const TMap<FString, FString>& OutputTypes,
		TArray<FCallFunctionPinDef>& OutPins)
	{
		for (const FString& Name : InputPins)
		{
			OutPins.Add({ Name, TEXT("input"), InputTypes.FindRef(Name) });
		}
		for (const FString& Name : OutputPins)
		{
			OutPins.Add({ Name, TEXT("output"), OutputTypes.FindRef(Name) });
		}
	}

	FString ComputeOwnedLiteralHash(
		const FString& NodeGuid,
		const TMap<FString, FString>& Values)
	{
		TArray<FString> Keys;
		Values.GetKeys(Keys);
		Keys.Sort();
		FString Builder = FString::Printf(TEXT("ueak.owned-literal/1|%s|"), *NodeGuid);
		for (const FString& Key : Keys)
		{
			const FString& Value = Values.FindChecked(Key);
			Builder += FString::Printf(TEXT("%d:%s|%d:%s|"), Key.Len(), *Key, Value.Len(), *Value);
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Builder, Digest))
		{
			return FString();
		}
		return TEXT("sha256:") + Digest;
	}

	bool BuildOwnedFunctionPins(
		UFunction* Function,
		TArray<FCallFunctionPinDef>& OutPins,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutPins.Reset();
		if (Function == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-invalid"),
				TEXT("The owned function did not resolve."));
		}
		TSet<FString> SeenNames;
		int32 OutputCount = 0;
		for (TFieldIterator<FProperty> It(Function); It; ++It)
		{
			FProperty* Property = *It;
			if (Property == nullptr || !Property->HasAnyPropertyFlags(CPF_Parm))
			{
				continue;
			}
			const bool bReturn = Property->HasAnyPropertyFlags(CPF_ReturnParm);
			if (!bReturn
				&& Property->HasAnyPropertyFlags(
					CPF_OutParm | CPF_ReferenceParm | CPF_ConstParm))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-call-pin-mismatch"),
					TEXT("Owned pure calls reject out/ref/const input parameters."));
			}
			if (bReturn
				&& Property->HasAnyPropertyFlags(CPF_ReferenceParm | CPF_ConstParm))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-call-pin-mismatch"),
					TEXT("Owned pure calls reject ref/const return values."));
			}
			FString Type;
			if (CastField<FBoolProperty>(Property) != nullptr)
			{
				Type = TEXT("bool");
			}
			else if (CastField<FIntProperty>(Property) != nullptr)
			{
				Type = TEXT("int");
			}
			else if (CastField<FDoubleProperty>(Property) != nullptr)
			{
				Type = TEXT("double");
			}
			else if (CastField<FStrProperty>(Property) != nullptr)
			{
				Type = TEXT("string");
			}
			else
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-call-pin-mismatch"),
					FString::Printf(
						TEXT("Owned pure parameter %s is not bool/int/double/string."),
						*Property->GetName()));
			}
			const FString Name = bReturn ? TEXT("ReturnValue") : Property->GetName();
			if (!IsOwnedFunctionName(Name) || SeenNames.Contains(Name))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-call-pin-mismatch"),
					TEXT("Owned pure parameters need unique transport-safe names."));
			}
			SeenNames.Add(Name);
			if (bReturn)
			{
				++OutputCount;
			}
			OutPins.Add({ Name, bReturn ? TEXT("output") : TEXT("input"), Type });
		}
		if (OutPins.IsEmpty() || OutPins.Num() > 8 || OutputCount == 0)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-pin-mismatch"),
				TEXT("Owned pure calls need 1-8 primitive pins and at least one output."));
		}
		return true;
	}

	bool ResolveOwnedCall(
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FNodeSpec& Node,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Blueprint == nullptr || Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-invalid"),
				TEXT("The owned call target is unavailable."));
		}
		if (!Node.OwnedBlueprintPath.Equals(Blueprint->GetPathName(), ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-owner-mismatch"),
				TEXT("Owned pure calls must target the exact focused Blueprint."));
		}
		if (Blueprint->Status != BS_UpToDate || Blueprint->GeneratedClass == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-not-compiled"),
				TEXT("Owned pure calls require a fully up-to-date compiled Blueprint."));
		}
		UEdGraph* FunctionGraph = nullptr;
		for (UEdGraph* Candidate : Blueprint->FunctionGraphs)
		{
			if (Candidate != nullptr
				&& Candidate->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
					.Equals(Node.OwnedFunctionGraphGuid, ESearchCase::CaseSensitive))
			{
				FunctionGraph = Candidate;
				break;
			}
		}
		if (FunctionGraph == nullptr
			|| !FunctionGraph->GetName().Equals(Node.OwnedFunctionName, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-graph-mismatch"),
				TEXT("The exact owned function graph does not exist in this Blueprint."));
		}
		UFunction* Function = Blueprint->GeneratedClass->FindFunctionByName(
			FName(*Node.OwnedFunctionName));
		if (Function == nullptr
			|| Function->GetOuterUClass() != Blueprint->GeneratedClass)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-function-mismatch"),
				TEXT("The function is not owned by this Blueprint's generated class."));
		}
		if (!Function->HasAnyFunctionFlags(FUNC_BlueprintPure)
			|| Function->HasAnyFunctionFlags(FUNC_Net)
			|| Function->HasAnyFunctionFlags(FUNC_BlueprintAuthorityOnly)
			|| Function->HasAnyFunctionFlags(FUNC_Event)
			|| Function->HasAnyFunctionFlags(FUNC_Exec)
			|| Function->HasMetaData(TEXT("Latent"))
			|| Function->HasMetaData(TEXT("WorldContext")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-invalid"),
				TEXT("Owned calls require a pure non-latent non-world non-network function."));
		}
		TArray<FCallFunctionPinDef> RequestPins;
		AppendPinDefs(
			Node.InputPins,
			Node.InputPinTypes,
			Node.OutputPins,
			Node.OutputPinTypes,
			RequestPins);
		FString RequestDigest;
		if (!ComputeOwnedCallDigest(
				Node.OwnedBlueprintPath,
				Node.OwnedFunctionGraphGuid,
				Node.OwnedFunctionName,
				RequestPins,
				RequestDigest)
			|| !RequestDigest.Equals(Node.OwnedSignatureDigest, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-signature-mismatch"),
				TEXT("The request pins do not hash to the declared owned signature."));
		}
		TArray<FCallFunctionPinDef> ActualPins;
		if (!BuildOwnedFunctionPins(Function, ActualPins, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		const FString ActualDigest = [&]()
		{
			FString Digest;
			ComputeOwnedCallDigest(
				Node.OwnedBlueprintPath,
				Node.OwnedFunctionGraphGuid,
				Node.OwnedFunctionName,
				ActualPins,
				Digest);
			return Digest;
		}();
		if (!ActualDigest.Equals(Node.OwnedSignatureDigest, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-signature-mismatch"),
				TEXT("The reflected owned function signature does not match the plan."));
		}
		TArray<FCallFunctionPinDef> RequestSorted = RequestPins;
		TArray<FCallFunctionPinDef> ActualSorted = ActualPins;
		auto SortPins = [](TArray<FCallFunctionPinDef>& Pins)
		{
			Pins.Sort([](const FCallFunctionPinDef& Left, const FCallFunctionPinDef& Right)
			{
				if (Left.Name != Right.Name)
				{
					return Left.Name < Right.Name;
				}
				return Left.Direction < Right.Direction;
			});
		};
		SortPins(RequestSorted);
		SortPins(ActualSorted);
		bool bPinsMatch = RequestSorted.Num() == ActualSorted.Num();
		for (int32 Index = 0; bPinsMatch && Index < RequestSorted.Num(); ++Index)
		{
			bPinsMatch = RequestSorted[Index].Name.Equals(
					ActualSorted[Index].Name, ESearchCase::CaseSensitive)
				&& RequestSorted[Index].Direction.Equals(
					ActualSorted[Index].Direction, ESearchCase::CaseSensitive)
				&& RequestSorted[Index].Type.Equals(
					ActualSorted[Index].Type, ESearchCase::CaseSensitive);
		}
		if (!bPinsMatch)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-pin-mismatch"),
				TEXT("The request pins do not match the reflected owned function pins."));
		}
		Node.OwnedOwnerClassPath = Blueprint->GeneratedClass->GetPathName();
		// UK2Node_CallFunction resolves Blueprint-owned functions through the
		// skeleton class after SetFromFunction, even when the reflected function
		// was found on the generated class.  Bind the post-spawn validator to the
		// same Blueprint's skeleton path; both paths belong to this exact asset.
		Node.CallFunctionOwnerPath = Blueprint->SkeletonGeneratedClass != nullptr
			? Blueprint->SkeletonGeneratedClass->GetPathName()
			: Node.OwnedOwnerClassPath;
		Node.InputPins.Reset();
		Node.OutputPins.Reset();
		Node.InputPinTypes.Reset();
		Node.OutputPinTypes.Reset();
		for (const FCallFunctionPinDef& Pin : ActualPins)
		{
			if (Pin.Direction == TEXT("input"))
			{
				Node.InputPins.Add(Pin.Name);
				Node.InputPinTypes.Add(Pin.Name, Pin.Type);
			}
			else
			{
				Node.OutputPins.Add(Pin.Name);
				Node.OutputPinTypes.Add(Pin.Name, Pin.Type);
			}
		}
		for (const FOwnedLiteralSpec& Literal : Node.InputLiterals)
		{
			const FString* ActualType = Node.InputPinTypes.Find(Literal.PinName);
			if (ActualType == nullptr
				|| !ActualType->Equals(Literal.Type, ESearchCase::CaseSensitive))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-literal-pin-mismatch"),
					TEXT("An input literal no longer matches the reflected input pin."));
			}
		}
		return true;
	}

	bool ResolveOwnedCalls(
		TArray<FNodeSpec>& Nodes,
		UBlueprint* Blueprint,
		UEdGraph* Graph,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		int32 OwnedCount = 0;
		for (const FNodeSpec& Node : Nodes)
		{
			OwnedCount += Node.bOwnedCall ? 1 : 0;
		}
		if (OwnedCount > 1)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-limit"),
				TEXT("A plan may create at most one owned pure function node."));
		}
		if (OwnedCount == 1 && Nodes.Num() != 1)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-plan"),
				TEXT("An owned pure call plan must contain exactly that one new node."));
		}
		for (FNodeSpec& Node : Nodes)
		{
			if (Node.bOwnedCall
				&& !ResolveOwnedCall(
					Blueprint,
					Graph,
					Node,
					OutErrorCode,
					OutErrorMessage))
			{
				return false;
			}
		}
		return true;
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
		return VariableTypeToken(Type, OutType);
	}

	bool DeclaredExistingPinToken(
		const FString& Category,
		const FString& SubCategory,
		FString& OutType)
	{
		if (Category.Equals(TEXT("exec"), ESearchCase::CaseSensitive))
		{
			OutType = TEXT("exec");
			return true;
		}
		if (Category.Equals(TEXT("bool"), ESearchCase::CaseSensitive))
		{
			OutType = TEXT("bool");
			return true;
		}
		if (Category.Equals(TEXT("int"), ESearchCase::CaseSensitive))
		{
			OutType = TEXT("int");
			return true;
		}
		if (Category.Equals(TEXT("string"), ESearchCase::CaseSensitive))
		{
			OutType = TEXT("string");
			return true;
		}
		if (Category.Equals(TEXT("real"), ESearchCase::CaseSensitive)
			&& SubCategory.Equals(TEXT("double"), ESearchCase::CaseSensitive))
		{
			OutType = TEXT("double");
			return true;
		}
		if (Category.Equals(TEXT("double"), ESearchCase::CaseSensitive))
		{
			OutType = TEXT("double");
			return true;
		}
		return false;
	}

	bool ReadExistingPinSpec(
		const TSharedPtr<FJsonObject>& Object,
		FExistingPinSpec& OutSpec,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Object.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-existing-pin"),
				TEXT("fromExisting/toExisting must be one JSON object."));
		}
		if (!RejectUnknownParams(
			Object,
			{
				TEXT("nodeGuid"),
				TEXT("pinGuid"),
				TEXT("pinName"),
				TEXT("direction"),
				TEXT("category"),
				TEXT("subCategory"),
			},
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (!ReadString(Object, TEXT("nodeGuid"), OutSpec.NodeGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(Object, TEXT("pinGuid"), OutSpec.PinGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(Object, TEXT("pinName"), OutSpec.PinName, OutErrorCode, OutErrorMessage)
			|| !ReadString(Object, TEXT("direction"), OutSpec.Direction, OutErrorCode, OutErrorMessage)
			|| !ReadString(Object, TEXT("category"), OutSpec.Category, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		if (!Object->TryGetStringField(TEXT("subCategory"), OutSpec.SubCategory))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-existing-pin"),
				TEXT("subCategory must be a string (use an empty string when unused)."));
		}
		if (!IsCanonicalVariableGuid(OutSpec.NodeGuid)
			|| !IsCanonicalVariableGuid(OutSpec.PinGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-guid-invalid"),
				TEXT("nodeGuid and pinGuid must be canonical lowercase GUIDs."));
		}
		if (!OutSpec.Direction.Equals(TEXT("input"), ESearchCase::CaseSensitive)
			&& !OutSpec.Direction.Equals(TEXT("output"), ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-direction-invalid"),
				TEXT("direction must be input or output."));
		}
		if (!IsSafePinTransportName(OutSpec.PinName))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-name-invalid"),
				TEXT("pinName must be 1-128 characters without control characters or '|'."));
		}
		if (!DeclaredExistingPinToken(OutSpec.Category, OutSpec.SubCategory, OutSpec.TypeToken))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-type-not-supported"),
				TEXT("Existing pins must be exec/bool/int/double/string without containers, refs, const or weak pointers."));
		}
		return true;
	}

	/**
	 * Resolve one exact existing pin in the focused graph.  This only reads;
	 * the caller decides whether the direction/link state is legal for the plan.
	 */
	bool ResolveExistingPin(
		UEdGraph* Graph,
		FExistingPinSpec& Spec,
		UEdGraphPin*& OutPin,
		FString& OutErrorCode,
		FString& OutErrorMessage,
		const bool bAllowLinkedInput = false)
	{
		OutPin = nullptr;
		if (Graph == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-graph-unavailable"),
				TEXT("The exact focused graph must be resolved before an existing pin can be addressed."));
		}
		FGuid NodeGuid;
		FGuid PinGuid;
		if (!FGuid::ParseExact(Spec.NodeGuid, EGuidFormats::DigitsWithHyphensLower, NodeGuid)
			|| !FGuid::ParseExact(Spec.PinGuid, EGuidFormats::DigitsWithHyphensLower, PinGuid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-guid-invalid"),
				TEXT("nodeGuid/pinGuid could not be parsed as canonical GUIDs."));
		}

		UEdGraphNode* Node = nullptr;
		for (UEdGraphNode* Candidate : Graph->Nodes)
		{
			if (Candidate != nullptr && Candidate->NodeGuid == NodeGuid)
			{
				if (Node != nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-node-ambiguous"),
						TEXT("More than one node in the focused graph has the requested NodeGuid."));
				}
				Node = Candidate;
			}
		}
		if (Node == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-node-not-found"),
				FString::Printf(TEXT("No node with GUID %s exists in the focused graph."), *Spec.NodeGuid));
		}
		if (Cast<UK2Node>(Node) == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-node-not-k2"),
				TEXT("Existing endpoints must be K2 Blueprint nodes."));
		}

		for (UEdGraphPin* Candidate : Node->Pins)
		{
			if (Candidate != nullptr && Candidate->PinId == PinGuid)
			{
				if (OutPin != nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-pin-ambiguous"),
						TEXT("More than one pin on the node has the requested PinGuid."));
				}
				OutPin = Candidate;
			}
		}
		if (OutPin == nullptr)
		{
			bool bNameDirectionSeen = false;
			for (const UEdGraphPin* Candidate : Node->Pins)
			{
				if (Candidate == nullptr)
				{
					continue;
				}
				const FString CandidateDirection =
					Candidate->Direction == EGPD_Input ? TEXT("input") : TEXT("output");
				if (Candidate->PinName.ToString().Equals(Spec.PinName, ESearchCase::CaseSensitive)
					&& CandidateDirection.Equals(Spec.Direction, ESearchCase::CaseSensitive))
				{
					bNameDirectionSeen = true;
					break;
				}
			}
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				bNameDirectionSeen
					? TEXT("graph-edit-existing-pin-guid-mismatch")
					: TEXT("graph-edit-existing-pin-not-found"),
				FString::Printf(
					TEXT("Pin %s (%s) was not found on node %s."),
					*Spec.PinName,
					*Spec.Direction,
					*Spec.NodeGuid));
		}

		const FString ActualDirection = OutPin->Direction == EGPD_Input ? TEXT("input") : TEXT("output");
		if (!ActualDirection.Equals(Spec.Direction, ESearchCase::CaseSensitive)
			|| !OutPin->PinName.ToString().Equals(Spec.PinName, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-metadata-mismatch"),
				TEXT("The existing pin does not match the requested name/direction."));
		}
		const FString ActualCategory = OutPin->PinType.PinCategory.ToString();
		const FString ActualSubCategory = OutPin->PinType.PinSubCategory.ToString();
		if (!ActualCategory.Equals(Spec.Category, ESearchCase::CaseSensitive)
			|| !ActualSubCategory.Equals(Spec.SubCategory, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-metadata-mismatch"),
				FString::Printf(
					TEXT("Pin type is %s/%s, not the declared %s/%s."),
					*ActualCategory,
					*ActualSubCategory,
					*Spec.Category,
					*Spec.SubCategory));
		}
		FString ActualType;
		if (!ExistingPinTypeToken(OutPin->PinType, ActualType))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-type-not-supported"),
				TEXT("The existing pin is not a plain exec/bool/int/double/string pin."));
		}
		if (!ActualType.Equals(Spec.TypeToken, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-type-mismatch"),
				TEXT("The existing pin's supported type token changed after Preview."));
		}
		if (OutPin->bHidden || OutPin->bOrphanedPin || OutPin->bNotConnectable)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-not-connectable"),
				TEXT("Hidden, orphaned or non-connectable pins cannot be endpoints."));
		}
		if (OutPin->ParentPin != nullptr || OutPin->SubPins.Num() > 0)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-split"),
				TEXT("Split/sub pins cannot be endpoints in this slice."));
		}
		if (OutPin->Direction == EGPD_Input && OutPin->LinkedTo.Num() != 0 && !bAllowLinkedInput)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-pin-already-linked"),
				TEXT("The existing input pin is already connected; this slice never breaks existing links."));
		}
		Spec.LinkCount = OutPin->LinkedTo.Num();
		return true;
	}

	bool ConnectionsUseExistingPins(const TSharedPtr<FJsonObject>& Params)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Params.IsValid()
			|| !Params->TryGetArrayField(TEXT("connections"), Values)
			|| Values == nullptr)
		{
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Value : *Values)
		{
			const TSharedPtr<FJsonObject>* Object = nullptr;
			if (Value.IsValid() && Value->TryGetObject(Object) && Object != nullptr
				&& ((*Object)->HasField(TEXT("fromExisting"))
					|| (*Object)->HasField(TEXT("toExisting"))))
			{
				return true;
			}
		}
		return false;
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

	bool ParseCallFunction(
		const TSharedPtr<FJsonObject>& Object,
		FNodeSpec& OutNode,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TSharedPtr<FJsonObject>* CallFunctionObject = nullptr;
		if (!Object->TryGetObjectField(TEXT("callFunction"), CallFunctionObject)
			|| CallFunctionObject == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-call-function-invalid"),
				TEXT("call_function requires one exact callFunction object."));
		}
		if (!RejectUnknownParams(
			*CallFunctionObject,
			{
				TEXT("ownerClassPath"),
				TEXT("functionName"),
				TEXT("signatureDigest"),
				TEXT("pins"),
			},
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		FString OwnerClassPath;
		FString FunctionName;
		FString SignatureDigest;
		if (!ReadString(
				*CallFunctionObject,
				TEXT("ownerClassPath"),
				OwnerClassPath,
				OutErrorCode,
				OutErrorMessage)
			|| !ReadString(
				*CallFunctionObject,
				TEXT("functionName"),
				FunctionName,
				OutErrorCode,
				OutErrorMessage)
			|| !ReadString(
				*CallFunctionObject,
				TEXT("signatureDigest"),
				SignatureDigest,
				OutErrorCode,
				OutErrorMessage))
		{
			return false;
		}
		const FCallFunctionAllowlistEntry* Entry = FindCallFunctionAllowlistEntry(
			OwnerClassPath,
			FunctionName);
		if (Entry == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-call-function-not-allowlisted"),
				TEXT("The owner/function is not in the fixed engine pure static allowlist."));
		}
		if (!IsSha256(SignatureDigest)
			|| !SignatureDigest.Equals(Entry->SignatureDigest, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-call-function-signature-mismatch"),
				TEXT("signatureDigest does not match the allowlisted reflected signature."));
		}

		const TArray<TSharedPtr<FJsonValue>>* PinValues = nullptr;
		if (!(*CallFunctionObject)->TryGetArrayField(TEXT("pins"), PinValues)
			|| PinValues == nullptr
			|| PinValues->IsEmpty()
			|| PinValues->Num() > 16)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-call-function-pin-mismatch"),
				TEXT("pins must contain the exact reflected input/output pin schema."));
		}
		TArray<FCallFunctionPinDef> Pins;
		for (const TSharedPtr<FJsonValue>& PinValue : *PinValues)
		{
			const TSharedPtr<FJsonObject>* PinObject = nullptr;
			if (!PinValue.IsValid()
				|| !PinValue->TryGetObject(PinObject)
				|| PinObject == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-call-function-pin-mismatch"),
					TEXT("Each callFunction pin must be an object."));
			}
			if (!RejectUnknownParams(
				*PinObject,
				{ TEXT("name"), TEXT("direction"), TEXT("type") },
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			FCallFunctionPinDef Pin;
			if (!ReadString(*PinObject, TEXT("name"), Pin.Name, OutErrorCode, OutErrorMessage)
				|| !ReadString(
					*PinObject,
					TEXT("direction"),
					Pin.Direction,
					OutErrorCode,
					OutErrorMessage)
				|| !ReadString(*PinObject, TEXT("type"), Pin.Type, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			if (!IsSafePinTransportName(Pin.Name)
				|| (Pin.Direction != TEXT("input") && Pin.Direction != TEXT("output"))
				|| (Pin.Type != TEXT("exec")
					&& Pin.Type != TEXT("bool")
					&& Pin.Type != TEXT("int")
					&& Pin.Type != TEXT("double")
					&& Pin.Type != TEXT("string")))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-call-function-pin-mismatch"),
					TEXT("callFunction pins must be exact named primitive input/output pins."));
			}
			Pins.Add(MoveTemp(Pin));
		}
		auto SortPins = [](const TArray<FCallFunctionPinDef>& Source)
		{
			TArray<FCallFunctionPinDef> Sorted = Source;
			Sorted.Sort([](const FCallFunctionPinDef& Left, const FCallFunctionPinDef& Right)
			{
				if (Left.Name != Right.Name)
				{
					return Left.Name < Right.Name;
				}
				return Left.Direction < Right.Direction;
			});
			return Sorted;
		};
		const TArray<FCallFunctionPinDef> ActualPins = SortPins(Pins);
		const TArray<FCallFunctionPinDef> ExpectedPins = SortPins(Entry->Pins);
		bool bPinsMatch = ActualPins.Num() == ExpectedPins.Num();
		if (bPinsMatch)
		{
			for (int32 Index = 0; Index < ActualPins.Num(); ++Index)
			{
				bPinsMatch = bPinsMatch
					&& ActualPins[Index].Name.Equals(ExpectedPins[Index].Name, ESearchCase::CaseSensitive)
					&& ActualPins[Index].Direction.Equals(ExpectedPins[Index].Direction, ESearchCase::CaseSensitive)
					&& ActualPins[Index].Type.Equals(ExpectedPins[Index].Type, ESearchCase::CaseSensitive);
			}
		}
		if (!bPinsMatch)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-call-function-pin-mismatch"),
				TEXT("The declared pins do not match the allowlisted reflected pin schema."));
		}
		FString ComputedDigest;
		if (!ComputeCallFunctionSignatureDigest(
				OwnerClassPath,
				FunctionName,
				Pins,
				ComputedDigest)
			|| !ComputedDigest.Equals(Entry->SignatureDigest, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-call-function-signature-mismatch"),
				TEXT("The declared pins do not hash to the allowlisted signature."));
		}

		OutNode.bCallFunction = true;
		OutNode.NodeClassPath = CallFunctionClassPath;
		OutNode.CallFunctionOwnerPath = OwnerClassPath;
		OutNode.FunctionName = FunctionName;
		OutNode.CallFunctionSignatureDigest = SignatureDigest;
		OutNode.InputPins.Reset();
		OutNode.OutputPins.Reset();
		OutNode.InputPinTypes.Reset();
		OutNode.OutputPinTypes.Reset();
		for (const FCallFunctionPinDef& Pin : Pins)
		{
			if (Pin.Direction == TEXT("input"))
			{
				OutNode.InputPins.Add(Pin.Name);
				OutNode.InputPinTypes.Add(Pin.Name, Pin.Type);
			}
			else
			{
				OutNode.OutputPins.Add(Pin.Name);
				OutNode.OutputPinTypes.Add(Pin.Name, Pin.Type);
			}
		}
		return true;
	}

	bool ParseOwnedCall(
		const TSharedPtr<FJsonObject>& Object,
		FNodeSpec& OutNode,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TSharedPtr<FJsonObject>* OwnedObject = nullptr;
		if (!Object->TryGetObjectField(TEXT("ownedCall"), OwnedObject)
			|| OwnedObject == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-invalid"),
				TEXT("call_owned_pure_function requires one exact ownedCall object."));
		}
		if (!RejectUnknownParams(
			*OwnedObject,
			{
				TEXT("owningBlueprintPath"),
				TEXT("functionGraphGuid"),
				TEXT("functionName"),
				TEXT("signatureDigest"),
				TEXT("pins"),
			},
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		FString OwnerPath;
		FString GraphGuid;
		FString FunctionName;
		FString SignatureDigest;
		if (!ReadString(*OwnedObject, TEXT("owningBlueprintPath"), OwnerPath, OutErrorCode, OutErrorMessage)
			|| !ReadString(*OwnedObject, TEXT("functionGraphGuid"), GraphGuid, OutErrorCode, OutErrorMessage)
			|| !ReadString(*OwnedObject, TEXT("functionName"), FunctionName, OutErrorCode, OutErrorMessage)
			|| !ReadString(*OwnedObject, TEXT("signatureDigest"), SignatureDigest, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		FGuid ParsedGraphGuid;
		if (!FGuid::ParseExact(GraphGuid, EGuidFormats::DigitsWithHyphensLower, ParsedGraphGuid)
			|| !ParsedGraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
				.Equals(GraphGuid, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-invalid"),
				TEXT("functionGraphGuid must be one canonical lowercase GUID."));
		}
		if (!IsOwnedFunctionName(FunctionName)
			|| !IsSha256(SignatureDigest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-invalid"),
				TEXT("functionName/signatureDigest must be canonical."));
		}

		const TArray<TSharedPtr<FJsonValue>>* PinValues = nullptr;
		if (!(*OwnedObject)->TryGetArrayField(TEXT("pins"), PinValues)
			|| PinValues == nullptr
			|| PinValues->IsEmpty()
			|| PinValues->Num() > 8)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-pin-mismatch"),
				TEXT("Owned calls need 1-8 exact primitive pins."));
		}
		TArray<FCallFunctionPinDef> Pins;
		TSet<FString> SeenNames;
		int32 OutputCount = 0;
		for (const TSharedPtr<FJsonValue>& PinValue : *PinValues)
		{
			const TSharedPtr<FJsonObject>* PinObject = nullptr;
			if (!PinValue.IsValid()
				|| !PinValue->TryGetObject(PinObject)
				|| PinObject == nullptr
				|| !RejectUnknownParams(
					*PinObject,
					{ TEXT("name"), TEXT("direction"), TEXT("type") },
					OutErrorCode,
					OutErrorMessage))
			{
				return false;
			}
			FCallFunctionPinDef Pin;
			if (!ReadString(*PinObject, TEXT("name"), Pin.Name, OutErrorCode, OutErrorMessage)
				|| !ReadString(*PinObject, TEXT("direction"), Pin.Direction, OutErrorCode, OutErrorMessage)
				|| !ReadString(*PinObject, TEXT("type"), Pin.Type, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			if (!IsOwnedFunctionName(Pin.Name)
				|| SeenNames.Contains(Pin.Name)
				|| (Pin.Direction != TEXT("input") && Pin.Direction != TEXT("output"))
				|| (Pin.Type != TEXT("bool")
					&& Pin.Type != TEXT("int")
					&& Pin.Type != TEXT("double")
					&& Pin.Type != TEXT("string")))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-call-pin-mismatch"),
					TEXT("Owned call pins must be unique primitive input/output pins."));
			}
			SeenNames.Add(Pin.Name);
			OutputCount += Pin.Direction == TEXT("output") ? 1 : 0;
			Pins.Add(MoveTemp(Pin));
		}
		if (OutputCount == 0)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-pin-mismatch"),
				TEXT("Owned calls require at least one primitive output pin."));
		}
		FString ComputedDigest;
		if (!ComputeOwnedCallDigest(
				OwnerPath,
				GraphGuid,
				FunctionName,
				Pins,
				ComputedDigest)
			|| !ComputedDigest.Equals(SignatureDigest, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-call-signature-mismatch"),
				TEXT("signatureDigest does not match the declared owned signature."));
		}

		OutNode.bOwnedCall = true;
		OutNode.NodeClassPath = CallFunctionClassPath;
		OutNode.FunctionName = FunctionName;
		OutNode.OwnedBlueprintPath = OwnerPath;
		OutNode.OwnedFunctionGraphGuid = GraphGuid;
		OutNode.OwnedFunctionName = FunctionName;
		OutNode.OwnedSignatureDigest = SignatureDigest;
		OutNode.InputPins.Reset();
		OutNode.OutputPins.Reset();
		OutNode.InputPinTypes.Reset();
		OutNode.OutputPinTypes.Reset();
		for (const FCallFunctionPinDef& Pin : Pins)
		{
			if (Pin.Direction == TEXT("input"))
			{
				OutNode.InputPins.Add(Pin.Name);
				OutNode.InputPinTypes.Add(Pin.Name, Pin.Type);
			}
			else
			{
				OutNode.OutputPins.Add(Pin.Name);
				OutNode.OutputPinTypes.Add(Pin.Name, Pin.Type);
			}
		}
		return true;
	}

	bool ParseOwnedLiterals(
		const TSharedPtr<FJsonObject>& Object,
		FNodeSpec& OutNode,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Object->HasField(TEXT("inputLiterals")))
		{
			return true;
		}
		if (!OutNode.bOwnedCall)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-literal-field-not-allowed"),
				TEXT("inputLiterals is allowed only on call_owned_pure_function."));
		}
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Object->TryGetArrayField(TEXT("inputLiterals"), Values)
			|| Values == nullptr
			|| Values->Num() > MaxOwnedLiterals)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-owned-literal-limit"),
				TEXT("inputLiterals must contain at most 8 entries."));
		}
		TSet<FString> SeenPins;
		OutNode.InputLiterals.Reset();
		for (const TSharedPtr<FJsonValue>& Value : *Values)
		{
			const TSharedPtr<FJsonObject>* LiteralObject = nullptr;
			if (!Value.IsValid()
				|| !Value->TryGetObject(LiteralObject)
				|| LiteralObject == nullptr
				|| !RejectUnknownParams(
					*LiteralObject,
					{ TEXT("pin"), TEXT("type"), TEXT("value") },
					OutErrorCode,
					OutErrorMessage))
			{
				return false;
			}
			FOwnedLiteralSpec Literal;
			if (!ReadString(
					*LiteralObject,
					TEXT("pin"),
					Literal.PinName,
					OutErrorCode,
					OutErrorMessage)
				|| !ReadString(
					*LiteralObject,
					TEXT("type"),
					Literal.Type,
					OutErrorCode,
					OutErrorMessage)
				|| !ReadString(
					*LiteralObject,
					TEXT("value"),
					Literal.Value,
					OutErrorCode,
					OutErrorMessage))
			{
				return false;
			}
			const FString* DeclaredType = OutNode.InputPinTypes.Find(Literal.PinName);
			if (DeclaredType == nullptr
				|| !DeclaredType->Equals(Literal.Type, ESearchCase::CaseSensitive)
				|| SeenPins.Contains(Literal.PinName))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-owned-literal-pin-mismatch"),
					TEXT("Each literal must target one exact declared input pin once."));
			}
			SeenPins.Add(Literal.PinName);
			FString Canonical;
			if (!CanonicalizeOwnedLiteral(
				Literal.Type,
				Literal.Value,
				Canonical,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			Literal.Value = Canonical;
			OutNode.InputLiterals.Add(MoveTemp(Literal));
		}
		return true;
	}

	bool ParseNodes(
		const TSharedPtr<FJsonObject>& Params,
		TArray<FNodeSpec>& OutNodes,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Params->TryGetArrayField(TEXT("nodes"), Values)
			|| Values == nullptr
			|| Values->Num() > MaxNodes)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-nodes"),
				FString::Printf(TEXT("nodes must contain between 0 and %d node objects."), MaxNodes));
		}

		TSet<FString> SeenTempIds;
		for (const TSharedPtr<FJsonValue>& Value : *Values)
		{
			const TSharedPtr<FJsonObject>* Object = nullptr;
			if (!Value.IsValid() || !Value->TryGetObject(Object) || Object == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-invalid-nodes"),
					TEXT("Each node entry must be an object."));
			}
			if (!RejectUnknownParams(
				*Object,
				{
					TEXT("tempId"),
					TEXT("nodeType"),
					TEXT("x"),
					TEXT("y"),
					TEXT("variableName"),
					TEXT("variableGuid"),
					TEXT("variableType"),
					TEXT("callFunction"),
					TEXT("ownedCall"),
					TEXT("inputLiterals"),
				},
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			FNodeSpec Node;
			if (!ReadString(*Object, TEXT("tempId"), Node.TempId, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("nodeType"), Node.NodeType, OutErrorCode, OutErrorMessage)
				|| !ReadInt(*Object, TEXT("x"), Node.X, OutErrorCode, OutErrorMessage)
				|| !ReadInt(*Object, TEXT("y"), Node.Y, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			if (!IsTempId(Node.TempId))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-temp-id-invalid"),
					TEXT("tempId must be 'tmp:' followed by 1-36 ASCII alphanumeric, '_' or '-' characters."));
			}
			if (SeenTempIds.Contains(Node.TempId))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-temp-id-duplicate"),
					FString::Printf(TEXT("Duplicate tempId: %s"), *Node.TempId));
			}
			SeenTempIds.Add(Node.TempId);
			if (!ConfigureNodeType(Node.NodeType, Node, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
			const bool bHasVariableFields = (*Object)->HasField(TEXT("variableName"))
				|| (*Object)->HasField(TEXT("variableGuid"))
				|| (*Object)->HasField(TEXT("variableType"));
			const bool bHasCallFunctionField = (*Object)->HasField(TEXT("callFunction"));
			const bool bHasOwnedCallField = (*Object)->HasField(TEXT("ownedCall"));
			if (Node.bCallFunction)
			{
				if (bHasVariableFields)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-variable-field-not-allowed"),
						TEXT("call_function does not accept variableName/variableGuid/variableType."));
				}
				if (bHasOwnedCallField)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-call-field-not-allowed"),
						TEXT("call_function does not accept ownedCall."));
				}
				if ((*Object)->HasField(TEXT("inputLiterals")))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-literal-field-not-allowed"),
						TEXT("call_function does not accept inputLiterals."));
				}
				if (!ParseCallFunction(
					*Object,
					Node,
					OutErrorCode,
					OutErrorMessage))
				{
					return false;
				}
			}
			else if (Node.bOwnedCall)
			{
				if (bHasVariableFields || bHasCallFunctionField)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-call-field-not-allowed"),
						TEXT("call_owned_pure_function accepts only ownedCall."));
				}
				if (!ParseOwnedCall(
					*Object,
					Node,
					OutErrorCode,
					OutErrorMessage)
					|| !ParseOwnedLiterals(
						*Object,
						Node,
						OutErrorCode,
						OutErrorMessage))
				{
					return false;
				}
			}
			else if (Node.bVariableAccessor)
			{
				if (bHasCallFunctionField || bHasOwnedCallField
					|| (*Object)->HasField(TEXT("inputLiterals")))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-literal-field-not-allowed"),
						TEXT("variable accessor nodeTypes do not accept call/literal fields."));
				}
				if (!ReadString(*Object, TEXT("variableName"), Node.VariableName, OutErrorCode, OutErrorMessage)
					|| !ReadString(*Object, TEXT("variableGuid"), Node.VariableGuid, OutErrorCode, OutErrorMessage)
					|| !ReadString(*Object, TEXT("variableType"), Node.VariableType, OutErrorCode, OutErrorMessage))
				{
					return false;
				}
			}
			else if (bHasVariableFields || bHasCallFunctionField || bHasOwnedCallField
				|| (*Object)->HasField(TEXT("inputLiterals")))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					(*Object)->HasField(TEXT("inputLiterals"))
						? TEXT("graph-edit-owned-literal-field-not-allowed")
						: (bHasOwnedCallField
							? TEXT("graph-edit-owned-call-field-not-allowed")
							: (bHasCallFunctionField
								? TEXT("graph-edit-call-function-field-not-allowed")
								: TEXT("graph-edit-variable-field-not-allowed"))),
					FString::Printf(
						TEXT("nodeType %s does not accept variable/callFunction/ownedCall/inputLiterals fields."),
						*Node.NodeType));
			}
			OutNodes.Add(MoveTemp(Node));
		}
		return true;
	}

	bool ParseConnections(
		const TSharedPtr<FJsonObject>& Params,
		const TArray<FNodeSpec>& Nodes,
		UEdGraph* Graph,
		const bool bDisconnect,
		TArray<FConnectionSpec>& OutConnections,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Params->TryGetArrayField(TEXT("connections"), Values) || Values == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-connections"),
				TEXT("connections must be an array (it may be empty)."));
		}
		if (Values->Num() > MaxConnections)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-connections"),
				FString::Printf(TEXT("connections may contain at most %d entries."), MaxConnections));
		}
		if (bDisconnect && (!Nodes.IsEmpty() || Values->Num() != 1))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-disconnect-invalid-plan"),
				TEXT("A disconnect plan requires exactly one connection and nodes=[]."));
		}

		TSet<FString> SeenTargets;
		TMap<FString, int32> ConnectionCounts;
		for (const TSharedPtr<FJsonValue>& Value : *Values)
		{
			const TSharedPtr<FJsonObject>* Object = nullptr;
			if (!Value.IsValid() || !Value->TryGetObject(Object) || Object == nullptr)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-invalid-connections"),
					TEXT("Each connection entry must be an object."));
			}
			if (!RejectUnknownParams(
				*Object,
				{
					TEXT("from"),
					TEXT("fromPin"),
					TEXT("to"),
					TEXT("toPin"),
					TEXT("fromExisting"),
					TEXT("toExisting"),
				},
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}

			const bool bHasFromExistingField = (*Object)->HasField(TEXT("fromExisting"));
			const bool bHasToExistingField = (*Object)->HasField(TEXT("toExisting"));
			if (bDisconnect && !(bHasFromExistingField && bHasToExistingField))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-disconnect-endpoints"),
					TEXT("A disconnect connection requires both fromExisting and toExisting."));
			}
			if (bHasFromExistingField && bHasToExistingField)
			{
				if ((*Object)->HasField(TEXT("from"))
					|| (*Object)->HasField(TEXT("fromPin"))
					|| (*Object)->HasField(TEXT("to"))
					|| (*Object)->HasField(TEXT("toPin")))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-endpoint-conflict"),
						TEXT("A pure existing-to-existing connection cannot carry temp-id endpoints."));
				}
				FConnectionSpec ExistingConnection;
				const TSharedPtr<FJsonObject>* FromObject = nullptr;
				const TSharedPtr<FJsonObject>* ToObject = nullptr;
				if (!(*Object)->TryGetObjectField(TEXT("fromExisting"), FromObject)
					|| FromObject == nullptr
					|| !(*Object)->TryGetObjectField(TEXT("toExisting"), ToObject)
					|| ToObject == nullptr
					|| !ReadExistingPinSpec(
						*FromObject,
						ExistingConnection.FromExisting,
						OutErrorCode,
						OutErrorMessage)
					|| !ReadExistingPinSpec(
						*ToObject,
						ExistingConnection.ToExisting,
						OutErrorCode,
						OutErrorMessage))
				{
					return false;
				}
				if (!ExistingConnection.FromExisting.Direction.Equals(TEXT("output"), ESearchCase::CaseSensitive)
					|| !ExistingConnection.ToExisting.Direction.Equals(TEXT("input"), ESearchCase::CaseSensitive))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-pin-direction"),
						TEXT("Pure existing connections require fromExisting=output and toExisting=input."));
				}
				if (ExistingConnection.FromExisting.NodeGuid.Equals(
					ExistingConnection.ToExisting.NodeGuid,
					ESearchCase::CaseSensitive))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-self-connection"),
						TEXT("The two existing endpoints must belong to different nodes."));
				}
				ExistingConnection.bFromExisting = true;
				ExistingConnection.bToExisting = true;
				if (!ResolveExistingPin(
					Graph,
					ExistingConnection.FromExisting,
					ExistingConnection.FromExistingPin,
					OutErrorCode,
					OutErrorMessage)
					|| !ResolveExistingPin(
						Graph,
						ExistingConnection.ToExisting,
						ExistingConnection.ToExistingPin,
						OutErrorCode,
						OutErrorMessage,
						bDisconnect))
				{
					return false;
				}
				ExistingConnection.FromType = ExistingConnection.FromExisting.TypeToken;
				ExistingConnection.ToType = ExistingConnection.ToExisting.TypeToken;
				if (!ExistingConnection.FromType.Equals(
					ExistingConnection.ToType,
					ESearchCase::CaseSensitive))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-pin-type-mismatch"),
						FString::Printf(
							TEXT("Cannot connect existing %s (%s) to existing %s (%s)."),
							*ExistingConnection.FromExisting.PinName,
							*ExistingConnection.FromType,
							*ExistingConnection.ToExisting.PinName,
							*ExistingConnection.ToType));
				}
				const FString TargetKey = FString::Printf(
					TEXT("existing:%s|%s"),
					*ExistingConnection.ToExisting.NodeGuid,
					*ExistingConnection.ToExisting.PinGuid);
				if (SeenTargets.Contains(TargetKey))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-connection-duplicate"),
						FString::Printf(TEXT("Input pin already has a connection in this plan: %s"), *TargetKey));
				}
				SeenTargets.Add(TargetKey);
				if (bDisconnect)
				{
					if (!ExistingConnection.FromExistingPin->LinkedTo.Contains(
							ExistingConnection.ToExistingPin)
						|| !ExistingConnection.ToExistingPin->LinkedTo.Contains(
							ExistingConnection.FromExistingPin))
					{
						return Fail(
							OutErrorCode,
							OutErrorMessage,
							TEXT("graph-edit-disconnect-edge-not-found"),
							TEXT("The exact existing edge does not exist between the two declared pins."));
					}
					if (ExistingConnection.ToExistingPin->LinkedTo.Num() != 1)
					{
						return Fail(
							OutErrorCode,
							OutErrorMessage,
							TEXT("graph-edit-disconnect-ambiguous"),
							TEXT("The declared existing input has more than one link; refusing an ambiguous disconnect."));
					}
				}
				else if (!ValidateSchemaConnection(
					Graph,
					ExistingConnection.FromExistingPin,
					ExistingConnection.ToExistingPin,
					OutErrorCode,
					OutErrorMessage))
				{
					return false;
				}
				OutConnections.Add(MoveTemp(ExistingConnection));
				continue;
			}

			FConnectionSpec Connection;
			if (bHasFromExistingField)
			{
				if ((*Object)->HasField(TEXT("from")) || (*Object)->HasField(TEXT("fromPin")))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-endpoint-conflict"),
						TEXT("fromExisting cannot be combined with from/fromPin."));
				}
				const TSharedPtr<FJsonObject>* ExistingObject = nullptr;
				if (!(*Object)->TryGetObjectField(TEXT("fromExisting"), ExistingObject)
					|| ExistingObject == nullptr
					|| !ReadExistingPinSpec(
						*ExistingObject,
						Connection.FromExisting,
						OutErrorCode,
						OutErrorMessage))
				{
					return false;
				}
				if (!Connection.FromExisting.Direction.Equals(TEXT("output"), ESearchCase::CaseSensitive))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-pin-direction"),
						TEXT("fromExisting must be an output pin."));
				}
				Connection.bFromExisting = true;
			}
			else if (!ReadString(*Object, TEXT("from"), Connection.From, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("fromPin"), Connection.FromPin, OutErrorCode, OutErrorMessage))
			{
				return false;
			}

			if (bHasToExistingField)
			{
				if ((*Object)->HasField(TEXT("to")) || (*Object)->HasField(TEXT("toPin")))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-endpoint-conflict"),
						TEXT("toExisting cannot be combined with to/toPin."));
				}
				const TSharedPtr<FJsonObject>* ExistingObject = nullptr;
				if (!(*Object)->TryGetObjectField(TEXT("toExisting"), ExistingObject)
					|| ExistingObject == nullptr
					|| !ReadExistingPinSpec(
						*ExistingObject,
						Connection.ToExisting,
						OutErrorCode,
						OutErrorMessage))
				{
					return false;
				}
				if (!Connection.ToExisting.Direction.Equals(TEXT("input"), ESearchCase::CaseSensitive))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-pin-direction"),
						TEXT("toExisting must be an input pin."));
				}
				Connection.bToExisting = true;
			}
			else if (!ReadString(*Object, TEXT("to"), Connection.To, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("toPin"), Connection.ToPin, OutErrorCode, OutErrorMessage))
			{
				return false;
			}

			if (Connection.bFromExisting)
			{
				if (Connection.FromExisting.TypeToken.IsEmpty())
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-pin-type-not-supported"),
						TEXT("The existing source pin type is unsupported."));
				}
				const FNodeSpec* To = FindNode(Nodes, Connection.To);
				if (To == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-node-reference-unknown"),
						FString::Printf(
							TEXT("Connection references an unknown tempId: %s"),
							*Connection.To));
				}
				const FString* ToType = To->InputPinTypes.Find(Connection.ToPin);
				if (ToType == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-pin-not-allowed"),
						FString::Printf(
							TEXT("%s is not a supported input pin of %s; allowed: %s."),
							*Connection.ToPin,
							*To->NodeType,
							*FString::Join(To->InputPins, TEXT(", "))));
				}
				if (!ResolveExistingPin(
					Graph,
					Connection.FromExisting,
					Connection.FromExistingPin,
					OutErrorCode,
					OutErrorMessage))
				{
					return false;
				}
				Connection.FromType = Connection.FromExisting.TypeToken;
				Connection.ToType = *ToType;
			}
			else if (Connection.bToExisting)
			{
				if (Connection.ToExisting.TypeToken.IsEmpty())
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-existing-pin-type-not-supported"),
						TEXT("The existing target pin type is unsupported."));
				}
				const FNodeSpec* From = FindNode(Nodes, Connection.From);
				if (From == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-node-reference-unknown"),
						FString::Printf(
							TEXT("Connection references an unknown tempId: %s"),
							*Connection.From));
				}
				const FString* FromType = From->OutputPinTypes.Find(Connection.FromPin);
				if (FromType == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-pin-not-allowed"),
						FString::Printf(
							TEXT("%s is not a supported output pin of %s; allowed: %s."),
							*Connection.FromPin,
							*From->NodeType,
							*FString::Join(From->OutputPins, TEXT(", "))));
				}
				if (!ResolveExistingPin(
					Graph,
					Connection.ToExisting,
					Connection.ToExistingPin,
					OutErrorCode,
					OutErrorMessage))
				{
					return false;
				}
				Connection.FromType = *FromType;
				Connection.ToType = Connection.ToExisting.TypeToken;
			}
			else
			{
				const FNodeSpec* From = FindNode(Nodes, Connection.From);
				const FNodeSpec* To = FindNode(Nodes, Connection.To);
				if (From == nullptr || To == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-node-reference-unknown"),
						FString::Printf(
							TEXT("Connection references an unknown tempId: %s -> %s"),
							*Connection.From,
							*Connection.To));
				}
				if (Connection.From.Equals(Connection.To, ESearchCase::CaseSensitive))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-self-connection"),
						TEXT("A node cannot connect to itself in this experimental slice."));
				}
				const FString* FromType = From->OutputPinTypes.Find(Connection.FromPin);
				if (FromType == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-pin-not-allowed"),
						FString::Printf(
							TEXT("%s is not a supported output pin of %s; allowed: %s."),
							*Connection.FromPin,
							*From->NodeType,
							*FString::Join(From->OutputPins, TEXT(", "))));
				}
				const FString* ToType = To->InputPinTypes.Find(Connection.ToPin);
				if (ToType == nullptr)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-pin-not-allowed"),
						FString::Printf(
							TEXT("%s is not a supported input pin of %s; allowed: %s."),
							*Connection.ToPin,
							*To->NodeType,
							*FString::Join(To->InputPins, TEXT(", "))));
				}
				Connection.FromType = *FromType;
				Connection.ToType = *ToType;
			}

			if (!Connection.FromType.Equals(Connection.ToType, ESearchCase::CaseSensitive))
			{
				const FString FromLabel = Connection.bFromExisting
					? FString::Printf(
						TEXT("existing:%s/%s"),
						*Connection.FromExisting.NodeGuid,
						*Connection.FromExisting.PinName)
					: Connection.FromPin;
				const FString ToLabel = Connection.bToExisting
					? FString::Printf(
						TEXT("existing:%s/%s"),
						*Connection.ToExisting.NodeGuid,
						*Connection.ToExisting.PinName)
					: Connection.ToPin;
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-pin-type-mismatch"),
					FString::Printf(
						TEXT("Cannot connect %s (%s) to %s (%s); data kinds must match exactly."),
						*FromLabel,
						*Connection.FromType,
						*ToLabel,
						*Connection.ToType));
			}

			const FString TargetKey = Connection.bToExisting
				? FString::Printf(
					TEXT("existing:%s|%s"),
					*Connection.ToExisting.NodeGuid,
					*Connection.ToExisting.PinGuid)
				: Connection.To + TEXT("|") + Connection.ToPin;
			if (SeenTargets.Contains(TargetKey))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-connection-duplicate"),
					FString::Printf(TEXT("Input pin already has a connection in this plan: %s"), *TargetKey));
			}
			SeenTargets.Add(TargetKey);

			TArray<FString> NewTempIds;
			if (!Connection.From.IsEmpty())
			{
				NewTempIds.Add(Connection.From);
			}
			if (!Connection.To.IsEmpty())
			{
				NewTempIds.Add(Connection.To);
			}
			for (const FString& TempId : NewTempIds)
			{
				int32& Count = ConnectionCounts.FindOrAdd(TempId);
				++Count;
				if (Count > MaxConnectionsPerNode)
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-connection-limit"),
						FString::Printf(
							TEXT("%s would exceed the %d-connection per-node limit."),
							*TempId,
							MaxConnectionsPerNode));
				}
			}
			OutConnections.Add(MoveTemp(Connection));
		}
		return true;
	}

	bool BuildPreview(
		const TSharedPtr<FJsonObject>& Params,
		FEditPlan& OutPlan,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutPlan.Operation = TEXT("connect");
		if (Params->HasField(TEXT("operation")))
		{
			FString OperationValue;
			if (!Params->TryGetStringField(TEXT("operation"), OperationValue)
				|| (OperationValue != TEXT("connect") && OperationValue != TEXT("disconnect")))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-invalid-operation"),
					TEXT("operation must be connect or disconnect."));
			}
			OutPlan.Operation = OperationValue;
		}
		OutPlan.bDisconnect = OutPlan.Operation == TEXT("disconnect");
		if (!ReadString(Params, TEXT("assetPath"), OutPlan.AssetPath, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("graphGuid"), OutPlan.GraphGuid, OutErrorCode, OutErrorMessage)
			|| !ParseNodes(Params, OutPlan.Nodes, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		// Static plans keep the historical validation order: connections are
		// fully checked before any Editor/focus lookup.  Variable accessors only
		// learn their pins after the exact Blueprint is resolved, so their
		// connections are checked in the second pass below.
		bool bHasVariableAccessor = false;
		for (const FNodeSpec& Node : OutPlan.Nodes)
		{
			bHasVariableAccessor = bHasVariableAccessor || Node.bVariableAccessor;
		}
		const bool bHasExistingPin = ConnectionsUseExistingPins(Params);
		OutPlan.bHasExistingPin = bHasExistingPin;
		if (!bHasVariableAccessor
			&& !bHasExistingPin
			&& !ParseConnections(
				Params,
				OutPlan.Nodes,
				nullptr,
				false,
				OutPlan.Connections,
				OutErrorCode,
				OutErrorMessage))
		{
			return false;
		}

		if (GEditor == nullptr || (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-pie-active"),
				TEXT("Experimental graph edit is unavailable while PIE or SIE is active."));
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
				TEXT("graph-edit-focused-graph-mismatch"),
				TEXT("The focused Blueprint editor is not showing the exact assetPath/graphGuid from the request."));
		}
		if (Preflight->GetBoolField(TEXT("packageDirty")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-package-dirty"),
				TEXT("Experimental graph edit requires a clean package before Apply."));
		}
		if (!Preflight->GetBoolField(TEXT("editable")))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-graph-not-editable"),
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
				TEXT("graph-edit-schema-not-supported"),
				TEXT("Experimental graph edit supports only K2 Blueprint graphs."));
		}

		// Variable accessor pins depend on the exact existing member variable,
		// so resolve them against the exact Blueprint before validating any
		// connection that names those pins.
		if (!ResolveVariableNodes(
			OutPlan.Nodes,
			Blueprint,
			Graph,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (!ResolveOwnedCalls(
			OutPlan.Nodes,
			Blueprint,
			Graph,
			OutErrorCode,
			OutErrorMessage))
		{
			return false;
		}
		if (bHasVariableAccessor
			|| bHasExistingPin)
		{
			if (!ParseConnections(
				Params,
				OutPlan.Nodes,
				Graph,
				OutPlan.bDisconnect,
				OutPlan.Connections,
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
		}

		for (const FNodeSpec& OwnedNode : OutPlan.Nodes)
		{
			if (!OwnedNode.bOwnedCall || OwnedNode.InputLiterals.IsEmpty())
			{
				continue;
			}
			TSet<FString> LiteralPins;
			for (const FOwnedLiteralSpec& Literal : OwnedNode.InputLiterals)
			{
				LiteralPins.Add(Literal.PinName);
			}
			for (const FConnectionSpec& Connection : OutPlan.Connections)
			{
				if (Connection.To.Equals(OwnedNode.TempId, ESearchCase::CaseSensitive)
					&& LiteralPins.Contains(Connection.ToPin))
				{
					return Fail(
						OutErrorCode,
						OutErrorMessage,
						TEXT("graph-edit-owned-literal-connected"),
						TEXT("A literal input cannot also have a connection."));
				}
			}
		}

		int32 PureExistingConnectionCount = 0;
		for (const FConnectionSpec& Connection : OutPlan.Connections)
		{
			if (Connection.bFromExisting && Connection.bToExisting)
			{
				++PureExistingConnectionCount;
			}
		}
		if (PureExistingConnectionCount > 1)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-existing-to-existing-limit"),
				TEXT("A plan may add at most one existing-to-existing connection."));
		}
		if (OutPlan.bDisconnect)
		{
			if (!OutPlan.Nodes.IsEmpty()
				|| OutPlan.Connections.Num() != 1
				|| PureExistingConnectionCount != 1)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-disconnect-invalid-plan"),
					TEXT("A disconnect plan requires nodes=[] and exactly one existing-to-existing edge."));
			}
		}
		else
		{
			if (PureExistingConnectionCount == 1
				&& (!OutPlan.Nodes.IsEmpty() || OutPlan.Connections.Num() != 1))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-existing-to-existing-plan"),
					TEXT("A pure existing-to-existing connection must be the only connection and requires nodes=[]."));
			}
			if (OutPlan.Nodes.IsEmpty() && OutPlan.Connections.IsEmpty())
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-empty-plan"),
					TEXT("A graph edit plan must create at least one node or connect at least one existing edge."));
			}
			if (OutPlan.Nodes.IsEmpty() && PureExistingConnectionCount == 0)
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-nodes-required"),
					TEXT("nodes may be empty only for one pure existing-to-existing connection."));
			}
		}

		OutPlan.PackageRevision = Preflight->GetStringField(TEXT("packageRevision"));
		OutPlan.GraphFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
		if (bHasExistingPin)
		{
			if (!UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(
				Graph,
				OutPlan.BeforeEdgeSetHash))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-edge-hash-failed"),
					TEXT("Could not compute the before edge-set hash for the existing-pin plan."));
			}
		}

		FString Canonical;
		Canonical.Reserve(4096);
		Canonical += TEXT("ueak.graphpatch-edit/1.0\n");
		Canonical += OutPlan.AssetPath + TEXT("\n");
		Canonical += OutPlan.GraphGuid + TEXT("\n");
		Canonical += OutPlan.PackageRevision + TEXT("\n");
		Canonical += OutPlan.GraphFingerprint + TEXT("\n");
		if (bHasExistingPin)
		{
			Canonical += FString::Printf(
				TEXT("E|%s\n"),
				*OutPlan.BeforeEdgeSetHash);
		}
		if (OutPlan.bDisconnect && OutPlan.Connections.Num() == 1)
		{
			const FConnectionSpec& Disconnect = OutPlan.Connections[0];
			Canonical += FString::Printf(
				TEXT("D|%s|%s|%s|%s\n"),
				*Disconnect.FromExisting.NodeGuid,
				*Disconnect.FromExisting.PinGuid,
				*Disconnect.ToExisting.NodeGuid,
				*Disconnect.ToExisting.PinGuid);
		}

		TArray<FNodeSpec> SortedNodes = OutPlan.Nodes;
		SortedNodes.Sort([](const FNodeSpec& Left, const FNodeSpec& Right)
		{
			return Left.TempId < Right.TempId;
		});
		for (const FNodeSpec& Node : SortedNodes)
		{
			Canonical += FString::Printf(
				TEXT("N|%s|%s|%d|%d\n"),
				*Node.TempId,
				*Node.NodeType,
				Node.X,
				Node.Y);
			if (Node.bVariableAccessor)
			{
				// The accessor is bound to one exact existing member variable:
				// GUID + name + primitive type + read/write direction.  The name
				// is length-prefixed so it cannot inject a canonical delimiter.
				Canonical += FString::Printf(
					TEXT("V|%s|%s|%s|%s|%d:%s\n"),
					*Node.TempId,
					*Node.VariableGuid,
					*Node.VariableType,
					*Node.VariableAccess,
					Node.VariableName.Len(),
					*Node.VariableName);
			}
			if (Node.bCallFunction)
			{
				Canonical += FString::Printf(
					TEXT("CF|%s|%s|%s|%s\n"),
					*Node.TempId,
					*Node.CallFunctionOwnerPath,
					*Node.FunctionName,
					*Node.CallFunctionSignatureDigest);
				TArray<FCallFunctionPinDef> Pins;
				for (const FString& PinName : Node.InputPins)
				{
					Pins.Add({ PinName, TEXT("input"), Node.InputPinTypes.FindRef(PinName) });
				}
				for (const FString& PinName : Node.OutputPins)
				{
					Pins.Add({ PinName, TEXT("output"), Node.OutputPinTypes.FindRef(PinName) });
				}
				Pins.Sort([](const FCallFunctionPinDef& Left, const FCallFunctionPinDef& Right)
				{
					if (Left.Name != Right.Name)
					{
						return Left.Name < Right.Name;
					}
					return Left.Direction < Right.Direction;
				});
				for (const FCallFunctionPinDef& Pin : Pins)
				{
					Canonical += FString::Printf(
						TEXT("CP|%s|%s|%s|%s\n"),
						*Node.TempId,
						*Pin.Name,
						*Pin.Direction,
						*Pin.Type);
				}
			}
			if (Node.bOwnedCall)
			{
				Canonical += FString::Printf(
					TEXT("OC|%s|%s|%s|%s|%s\n"),
					*Node.TempId,
					*Node.OwnedBlueprintPath,
					*Node.OwnedFunctionGraphGuid,
					*Node.OwnedFunctionName,
					*Node.OwnedSignatureDigest);
				TArray<FCallFunctionPinDef> Pins;
				AppendPinDefs(
					Node.InputPins,
					Node.InputPinTypes,
					Node.OutputPins,
					Node.OutputPinTypes,
					Pins);
				Pins.Sort([](const FCallFunctionPinDef& Left, const FCallFunctionPinDef& Right)
				{
					if (Left.Name != Right.Name)
					{
						return Left.Name < Right.Name;
					}
					return Left.Direction < Right.Direction;
				});
				for (const FCallFunctionPinDef& Pin : Pins)
				{
					Canonical += FString::Printf(
						TEXT("OP|%s|%s|%s|%s\n"),
						*Node.TempId,
						*Pin.Name,
						*Pin.Direction,
						*Pin.Type);
				}
				TArray<FOwnedLiteralSpec> Literals = Node.InputLiterals;
				Literals.Sort([](const FOwnedLiteralSpec& Left, const FOwnedLiteralSpec& Right)
				{
					return Left.PinName < Right.PinName;
				});
				for (const FOwnedLiteralSpec& Literal : Literals)
				{
					Canonical += FString::Printf(
						TEXT("OL|%s|%s|%s|%d:%s\n"),
						*Node.TempId,
						*Literal.PinName,
						*Literal.Type,
						Literal.Value.Len(),
						*Literal.Value);
				}
			}
		}
		TArray<FConnectionSpec> SortedConnections = OutPlan.Connections;
		SortedConnections.Sort([](const FConnectionSpec& Left, const FConnectionSpec& Right)
		{
			if (Left.From != Right.From)
			{
				return Left.From < Right.From;
			}
			if (Left.FromPin != Right.FromPin)
			{
				return Left.FromPin < Right.FromPin;
			}
			if (Left.To != Right.To)
			{
				return Left.To < Right.To;
			}
			return Left.ToPin < Right.ToPin;
		});
		for (const FConnectionSpec& Connection : SortedConnections)
		{
			const FString FromLabel = Connection.bFromExisting
				? FString::Printf(
					TEXT("existing:%s:%s"),
					*Connection.FromExisting.NodeGuid,
					*Connection.FromExisting.PinGuid)
				: Connection.From;
			const FString ToLabel = Connection.bToExisting
				? FString::Printf(
					TEXT("existing:%s:%s"),
					*Connection.ToExisting.NodeGuid,
					*Connection.ToExisting.PinGuid)
				: Connection.To;
			Canonical += FString::Printf(
				TEXT("C|%s|%s|%s|%s\n"),
				*FromLabel,
				Connection.bFromExisting ? *Connection.FromExisting.PinName : *Connection.FromPin,
				*ToLabel,
				Connection.bToExisting ? *Connection.ToExisting.PinName : *Connection.ToPin);
			if (Connection.bFromExisting)
			{
				Canonical += FString::Printf(
					TEXT("X|from|%s|%s|%s|%s|%s|%s|%s|%d\n"),
					*Connection.FromExisting.NodeGuid,
					*Connection.FromExisting.PinGuid,
					*Connection.FromExisting.PinName,
					*Connection.FromExisting.Direction,
					*Connection.FromExisting.Category,
					*Connection.FromExisting.SubCategory,
					*Connection.FromExisting.TypeToken,
					Connection.FromExisting.LinkCount);
			}
			if (Connection.bToExisting)
			{
				Canonical += FString::Printf(
					TEXT("X|to|%s|%s|%s|%s|%s|%s|%s|%d\n"),
					*Connection.ToExisting.NodeGuid,
					*Connection.ToExisting.PinGuid,
					*Connection.ToExisting.PinName,
					*Connection.ToExisting.Direction,
					*Connection.ToExisting.Category,
					*Connection.ToExisting.SubCategory,
					*Connection.ToExisting.TypeToken,
					Connection.ToExisting.LinkCount);
			}
		}
		FString Digest;
		if (!FBlueprintContextSha256::HashText(Canonical, Digest))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-plan-hash-failed"),
				TEXT("Could not compute the graph edit plan hash."));
		}
		OutPlan.PlanId = TEXT("sha256:") + Digest;

		TSharedRef<FJsonObject> Preview = MakeShared<FJsonObject>();
		Preview->SetStringField(TEXT("schemaVersion"), PreviewSchemaVersion);
		Preview->SetBoolField(TEXT("experimental"), true);
		Preview->SetBoolField(TEXT("readOnly"), true);
		Preview->SetBoolField(TEXT("wroteBlueprint"), false);
		Preview->SetBoolField(TEXT("savedPackage"), false);
		Preview->SetBoolField(TEXT("compiled"), false);
		Preview->SetStringField(TEXT("planId"), OutPlan.PlanId);
		Preview->SetStringField(TEXT("assetPath"), OutPlan.AssetPath);
		Preview->SetStringField(TEXT("graphGuid"), OutPlan.GraphGuid);
		Preview->SetStringField(TEXT("packageRevision"), OutPlan.PackageRevision);
		Preview->SetStringField(TEXT("graphFingerprint"), OutPlan.GraphFingerprint);
		Preview->SetStringField(TEXT("operation"), OutPlan.Operation);
		if (bHasExistingPin)
		{
			Preview->SetStringField(TEXT("beforeEdgeSetHash"), OutPlan.BeforeEdgeSetHash);
		}
		if (OutPlan.bDisconnect)
		{
			Preview->SetNumberField(TEXT("disconnectCount"), 1);
		}
		Preview->SetNumberField(TEXT("nodeCountBefore"), Preflight->GetNumberField(TEXT("nodeCount")));
		Preview->SetNumberField(TEXT("nodeCountAfter"), Preflight->GetNumberField(TEXT("nodeCount")) + OutPlan.Nodes.Num());
		Preview->SetNumberField(TEXT("connectionCount"), OutPlan.Connections.Num());
		Preview->SetStringField(
			TEXT("requiredConfirmation"),
			FString(UEAgentKitBlueprintGraphEditExperimental::ApplyConfirmationPrefix) + OutPlan.PlanId);

		TArray<TSharedPtr<FJsonValue>> NodeValues;
		for (const FNodeSpec& Node : OutPlan.Nodes)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("tempId"), Node.TempId);
			Item->SetStringField(TEXT("nodeType"), Node.NodeType);
			Item->SetStringField(TEXT("nodeClass"), Node.NodeClassPath);
			if (!Node.FunctionName.IsEmpty())
			{
				Item->SetStringField(TEXT("function"), Node.FunctionName);
			}
			if (Node.bVariableAccessor)
			{
				Item->SetStringField(TEXT("variableName"), Node.VariableName);
				Item->SetStringField(TEXT("variableGuid"), Node.VariableGuid);
				Item->SetStringField(TEXT("variableType"), Node.VariableType);
				Item->SetStringField(TEXT("variableAccess"), Node.VariableAccess);
			}
			if (Node.bCallFunction)
			{
				TSharedRef<FJsonObject> CallFunction = MakeShared<FJsonObject>();
				CallFunction->SetStringField(TEXT("ownerClassPath"), Node.CallFunctionOwnerPath);
				CallFunction->SetStringField(TEXT("functionName"), Node.FunctionName);
				CallFunction->SetStringField(
					TEXT("signatureDigest"),
					Node.CallFunctionSignatureDigest);
				TArray<TSharedPtr<FJsonValue>> CallPins;
				for (const FString& PinName : Node.InputPins)
				{
					TSharedRef<FJsonObject> Pin = MakeShared<FJsonObject>();
					Pin->SetStringField(TEXT("name"), PinName);
					Pin->SetStringField(TEXT("direction"), TEXT("input"));
					Pin->SetStringField(TEXT("type"), Node.InputPinTypes.FindRef(PinName));
					CallPins.Add(MakeShared<FJsonValueObject>(Pin));
				}
				for (const FString& PinName : Node.OutputPins)
				{
					TSharedRef<FJsonObject> Pin = MakeShared<FJsonObject>();
					Pin->SetStringField(TEXT("name"), PinName);
					Pin->SetStringField(TEXT("direction"), TEXT("output"));
					Pin->SetStringField(TEXT("type"), Node.OutputPinTypes.FindRef(PinName));
					CallPins.Add(MakeShared<FJsonValueObject>(Pin));
				}
				CallFunction->SetArrayField(TEXT("pins"), CallPins);
				Item->SetObjectField(TEXT("callFunction"), CallFunction);
			}
			if (Node.bOwnedCall)
			{
				TSharedRef<FJsonObject> OwnedCall = MakeShared<FJsonObject>();
				OwnedCall->SetStringField(TEXT("owningBlueprintPath"), Node.OwnedBlueprintPath);
				OwnedCall->SetStringField(
					TEXT("functionGraphGuid"),
					Node.OwnedFunctionGraphGuid);
				OwnedCall->SetStringField(TEXT("functionName"), Node.OwnedFunctionName);
				OwnedCall->SetStringField(
					TEXT("signatureDigest"),
					Node.OwnedSignatureDigest);
				TArray<TSharedPtr<FJsonValue>> OwnedPins;
				for (const FString& PinName : Node.InputPins)
				{
					TSharedRef<FJsonObject> Pin = MakeShared<FJsonObject>();
					Pin->SetStringField(TEXT("name"), PinName);
					Pin->SetStringField(TEXT("direction"), TEXT("input"));
					Pin->SetStringField(TEXT("type"), Node.InputPinTypes.FindRef(PinName));
					OwnedPins.Add(MakeShared<FJsonValueObject>(Pin));
				}
				for (const FString& PinName : Node.OutputPins)
				{
					TSharedRef<FJsonObject> Pin = MakeShared<FJsonObject>();
					Pin->SetStringField(TEXT("name"), PinName);
					Pin->SetStringField(TEXT("direction"), TEXT("output"));
					Pin->SetStringField(TEXT("type"), Node.OutputPinTypes.FindRef(PinName));
					OwnedPins.Add(MakeShared<FJsonValueObject>(Pin));
				}
				OwnedCall->SetArrayField(TEXT("pins"), OwnedPins);
				Item->SetObjectField(TEXT("ownedCall"), OwnedCall);
				TArray<TSharedPtr<FJsonValue>> LiteralValues;
				for (const FOwnedLiteralSpec& Literal : Node.InputLiterals)
				{
					TSharedRef<FJsonObject> LiteralObject = MakeShared<FJsonObject>();
					LiteralObject->SetStringField(TEXT("pin"), Literal.PinName);
					LiteralObject->SetStringField(TEXT("type"), Literal.Type);
					LiteralObject->SetStringField(TEXT("value"), Literal.Value);
					LiteralValues.Add(MakeShared<FJsonValueObject>(LiteralObject));
				}
				Item->SetArrayField(TEXT("inputLiterals"), LiteralValues);
			}
			TSharedRef<FJsonObject> Position = MakeShared<FJsonObject>();
			Position->SetNumberField(TEXT("x"), Node.X);
			Position->SetNumberField(TEXT("y"), Node.Y);
			Item->SetObjectField(TEXT("position"), Position);
			TArray<TSharedPtr<FJsonValue>> Inputs;
			for (const FString& Pin : Node.InputPins)
			{
				Inputs.Add(MakeShared<FJsonValueString>(Pin));
			}
			TArray<TSharedPtr<FJsonValue>> Outputs;
			for (const FString& Pin : Node.OutputPins)
			{
				Outputs.Add(MakeShared<FJsonValueString>(Pin));
			}
			Item->SetArrayField(TEXT("inputPins"), Inputs);
			Item->SetArrayField(TEXT("outputPins"), Outputs);
			TSharedRef<FJsonObject> InputTypes = MakeShared<FJsonObject>();
			for (const TPair<FString, FString>& Pair : Node.InputPinTypes)
			{
				InputTypes->SetStringField(Pair.Key, Pair.Value);
			}
			TSharedRef<FJsonObject> OutputTypes = MakeShared<FJsonObject>();
			for (const TPair<FString, FString>& Pair : Node.OutputPinTypes)
			{
				OutputTypes->SetStringField(Pair.Key, Pair.Value);
			}
			Item->SetObjectField(TEXT("inputPinTypes"), InputTypes);
			Item->SetObjectField(TEXT("outputPinTypes"), OutputTypes);
			NodeValues.Add(MakeShared<FJsonValueObject>(Item));
		}
		Preview->SetArrayField(TEXT("nodes"), NodeValues);

		auto MakeExistingPinObject = [](const FExistingPinSpec& Spec)
		{
			TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
			Object->SetStringField(TEXT("nodeGuid"), Spec.NodeGuid);
			Object->SetStringField(TEXT("pinGuid"), Spec.PinGuid);
			Object->SetStringField(TEXT("pinName"), Spec.PinName);
			Object->SetStringField(TEXT("direction"), Spec.Direction);
			Object->SetStringField(TEXT("category"), Spec.Category);
			Object->SetStringField(TEXT("subCategory"), Spec.SubCategory);
			return Object;
		};
		TArray<TSharedPtr<FJsonValue>> ConnectionValues;
		for (const FConnectionSpec& Connection : OutPlan.Connections)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			if (Connection.bFromExisting)
			{
				Item->SetObjectField(
					TEXT("fromExisting"),
					MakeExistingPinObject(Connection.FromExisting));
			}
			else
			{
				Item->SetStringField(TEXT("from"), Connection.From);
				Item->SetStringField(TEXT("fromPin"), Connection.FromPin);
			}
			Item->SetStringField(TEXT("fromType"), Connection.FromType);
			if (Connection.bToExisting)
			{
				Item->SetObjectField(
					TEXT("toExisting"),
					MakeExistingPinObject(Connection.ToExisting));
			}
			else
			{
				Item->SetStringField(TEXT("to"), Connection.To);
				Item->SetStringField(TEXT("toPin"), Connection.ToPin);
			}
			Item->SetStringField(TEXT("toType"), Connection.ToType);
			ConnectionValues.Add(MakeShared<FJsonValueObject>(Item));
		}
		Preview->SetArrayField(TEXT("connections"), ConnectionValues);

		OutPlan.Preview = Preview;
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

	bool HasNodeGuid(const UEdGraph* Graph, const FGuid& Guid)
	{
		for (const UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr && Node->NodeGuid == Guid)
			{
				return true;
			}
		}
		return false;
	}

	/**
	 * Canonical hash of every UK2Node_Variable binding and accessor pin in the
	 * graph.  The shared layout fingerprint covers node/pin identity but not
	 * FMemberReference, so Save uses this receipt-side hash to refuse a same-type
	 * variable retarget that happened after Apply.
	 */
	FString ComputeVariableBindingHash(const UEdGraph* Graph)
	{
		if (Graph == nullptr)
		{
			return FString();
		}
		auto AppendField = [](FString& Base, const FString& Value)
		{
			Base += FString::Printf(TEXT("%d:%s|"), Value.Len(), *Value);
		};

		TArray<UEdGraphNode*> SortedNodes;
		SortedNodes.Reserve(Graph->Nodes.Num());
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node != nullptr)
			{
				SortedNodes.Add(Node);
			}
		}
		SortedNodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
		{
			return Left.NodeGuid.ToString(EGuidFormats::Digits)
				< Right.NodeGuid.ToString(EGuidFormats::Digits);
		});

		FString Builder;
		AppendField(Builder, TEXT("ueak.graph-edit-variable-binding/1"));
		AppendField(Builder, Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		int32 VariableNodeCount = 0;
		for (const UEdGraphNode* Node : SortedNodes)
		{
			const UK2Node_Variable* VariableNode = Cast<UK2Node_Variable>(Node);
			if (VariableNode == nullptr)
			{
				continue;
			}
			++VariableNodeCount;
			AppendField(Builder, Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			AppendField(Builder, Node->GetClass()->GetPathName());
			AppendField(Builder, VariableNode->VariableReference.IsSelfContext()
				? TEXT("self")
				: TEXT("external"));
			AppendField(Builder, VariableNode->GetVarName().ToString());
			AppendField(
				Builder,
				VariableNode->VariableReference.GetMemberGuid().ToString(
					EGuidFormats::DigitsWithHyphensLower));
			AppendField(
				Builder,
				Node->GetClass()->IsChildOf(UK2Node_VariableSet::StaticClass())
					? TEXT("write")
					: TEXT("read"));

			TArray<UEdGraphPin*> Pins;
			Pins.Reserve(Node->Pins.Num());
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
			AppendField(Builder, FString::FromInt(Pins.Num()));
			for (const UEdGraphPin* Pin : Pins)
			{
				AppendField(Builder, Pin->PinName.ToString());
				AppendField(Builder, Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
				AppendField(Builder, Pin->PinType.PinCategory.ToString());
				AppendField(Builder, Pin->PinType.PinSubCategory.ToString());
			}
		}
		AppendField(Builder, FString::FromInt(VariableNodeCount));

		FString Digest;
		if (!FBlueprintContextSha256::HashText(Builder, Digest))
		{
			return FString();
		}
		return TEXT("sha256:") + Digest;
	}

	bool ArePinsLinked(const UEdGraphPin* FromPin, const UEdGraphPin* ToPin)
	{
		return FromPin != nullptr
			&& ToPin != nullptr
			&& FromPin->LinkedTo.Contains(ToPin)
			&& ToPin->LinkedTo.Contains(FromPin);
	}

	FString MakeEdgeKey(const UEdGraphPin* FromPin, const UEdGraphPin* ToPin)
	{
		if (FromPin == nullptr || ToPin == nullptr
			|| FromPin->GetOwningNode() == nullptr || ToPin->GetOwningNode() == nullptr)
		{
			return FString();
		}
		return FString::Printf(
			TEXT("%s|%s>%s|%s"),
			*FromPin->GetOwningNode()->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
			*FromPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower),
			*ToPin->GetOwningNode()->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower),
			*ToPin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower));
	}

	void CollectEdgeKeys(const UEdGraph* Graph, TSet<FString>& OutKeys)
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
					const FString Key = MakeEdgeKey(Pin, Linked);
					if (!Key.IsEmpty())
					{
						OutKeys.Add(Key);
					}
				}
			}
		}
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

	bool ValidateCallFunctionNode(
		UK2Node_CallFunction* Node,
		const FNodeSpec& Spec,
		FString& OutReason)
	{
		if (Node == nullptr)
		{
			OutReason = TEXT("The spawned CallFunction node is null.");
			return false;
		}
		UFunction* Function = Node->GetTargetFunction();
		if (Function == nullptr)
		{
			OutReason = TEXT("The CallFunction node has no resolved reflected function.");
			return false;
		}
		if (!Function->GetName().Equals(Spec.FunctionName, ESearchCase::CaseSensitive))
		{
			OutReason = TEXT("The resolved function name does not match the plan.");
			return false;
		}
		UClass* OwnerClass = Function->GetOuterUClass();
		if (OwnerClass == nullptr
			|| !OwnerClass->GetPathName().Equals(
				Spec.CallFunctionOwnerPath,
				ESearchCase::CaseSensitive))
		{
			OutReason = FString::Printf(
				TEXT("The resolved function owner class %s does not match the plan %s."),
				OwnerClass != nullptr ? *OwnerClass->GetPathName() : TEXT("<null>"),
				*Spec.CallFunctionOwnerPath);
			return false;
		}
		if (!Function->HasAnyFunctionFlags(FUNC_BlueprintPure)
			|| (!Spec.bOwnedCall && !Function->HasAnyFunctionFlags(FUNC_Static)))
		{
			OutReason = Spec.bOwnedCall
				? TEXT("The owned function is not BlueprintPure.")
				: TEXT("The function is not a static BlueprintPure function.");
			return false;
		}
		if (Function->HasAnyFunctionFlags(FUNC_Net)
			|| Function->HasAnyFunctionFlags(FUNC_BlueprintAuthorityOnly)
			|| Function->HasAnyFunctionFlags(
				Spec.bOwnedCall ? FUNC_Event : FUNC_BlueprintEvent)
			|| Function->HasAnyFunctionFlags(FUNC_Exec)
			|| Function->HasMetaData(TEXT("Latent")))
		{
			OutReason = TEXT("The function has network/latent/authority/event/exec flags.");
			return false;
		}

		const int32 ExpectedPinCount = Spec.InputPins.Num() + Spec.OutputPins.Num();
		int32 VisiblePinCount = 0;
		int32 HiddenSelfCount = 0;
		TArray<FString> PinInventory;
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
					OutReason = TEXT("The hidden CallFunction self pin unexpectedly has links.");
					return false;
				}
				continue;
			}
			if (Pin->bHidden)
			{
				OutReason = FString::Printf(
					TEXT("The CallFunction node has an unexpected hidden pin %s."),
					*Pin->PinName.ToString());
				return false;
			}
			++VisiblePinCount;
			PinInventory.Add(FString::Printf(
				TEXT("%s:%s"),
				*Pin->PinName.ToString(),
				Pin->Direction == EGPD_Input ? TEXT("in") : TEXT("out")));
		}
		if (VisiblePinCount != ExpectedPinCount || HiddenSelfCount > 1)
		{
			OutReason = FString::Printf(
				TEXT("The CallFunction node has %d visible pins (hidden self=%d), expected %d: %s"),
				VisiblePinCount,
				HiddenSelfCount,
				ExpectedPinCount,
				*FString::Join(PinInventory, TEXT(",")));
			return false;
		}
		for (const FString& PinName : Spec.InputPins)
		{
			const UEdGraphPin* Pin = Node->FindPin(FName(*PinName), EGPD_Input);
			if (Pin == nullptr || !PinMatchesType(Pin, Spec.InputPinTypes.FindRef(PinName)))
			{
				OutReason = FString::Printf(
					TEXT("The CallFunction input pin %s is missing or has the wrong type."),
					*PinName);
				return false;
			}
		}
		for (const FString& PinName : Spec.OutputPins)
		{
			const UEdGraphPin* Pin = Node->FindPin(FName(*PinName), EGPD_Output);
			if (Pin == nullptr || !PinMatchesType(Pin, Spec.OutputPinTypes.FindRef(PinName)))
			{
				OutReason = FString::Printf(
					TEXT("The CallFunction output pin %s is missing or has the wrong type."),
					*PinName);
				return false;
			}
		}
		return true;
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
}

bool UEAgentKitBlueprintGraphEditExperimental::IsExperimentalWriteAvailable()
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

void UEAgentKitBlueprintGraphEditExperimental::SetExperimentalWriteEnabledForTest(bool bEnabled)
{
	bTestOverrideSet = true;
	bTestOverrideEnabled = bEnabled;
}

void UEAgentKitBlueprintGraphEditExperimental::ResetExperimentalWriteForTest()
{
	bTestOverrideSet = false;
	bTestOverrideEnabled = false;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryPreviewExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!RejectUnknownParams(
		Params,
		{ TEXT("assetPath"), TEXT("graphGuid"), TEXT("nodes"), TEXT("connections"), TEXT("operation") },
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
			TEXT("graph-edit-experimental-disabled"),
			TEXT("Experimental graph structure edit is not opted in for this project. "
				 "Set [UEAgentKit] ExperimentalGraphPatchEdit=True in the project config."));
	}
	FEditPlan Plan;
	if (!BuildPreview(Params, Plan, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	OutResult = Plan.Preview;
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TryApplyExperimental(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!RejectUnknownParams(
		Params,
		{ TEXT("assetPath"), TEXT("graphGuid"), TEXT("nodes"), TEXT("connections"), TEXT("planId"), TEXT("confirmation"), TEXT("operation") },
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
			TEXT("graph-edit-experimental-disabled"),
			TEXT("Experimental graph structure edit is not opted in for this project. "
				 "Set [UEAgentKit] ExperimentalGraphPatchEdit=True in the project config."));
	}
	if (GEditor == nullptr || GEditor->Trans == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-transaction-unavailable"),
			TEXT("The Editor transaction history is unavailable."));
	}

	FString RequestedPlanId;
	FString Confirmation;
	if (!ReadString(Params, TEXT("planId"), RequestedPlanId, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	FEditPlan Plan;
	if (!BuildPreview(Params, Plan, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!RequestedPlanId.Equals(Plan.PlanId, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-plan-stale"),
			FString::Printf(
				TEXT("The graph or request changed after Preview; request a fresh preview before Apply. "
					 "requested=%s current=%s packageRevision=%s graphFingerprint=%s"),
				*RequestedPlanId,
				*Plan.PlanId,
				*Plan.PackageRevision,
				*Plan.GraphFingerprint));
	}
	const FString ExpectedConfirmation =
		FString(ApplyConfirmationPrefix) + Plan.PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-confirmation-required"),
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

	const int32 NodeCountBefore = Graph->Nodes.Num();
	const int32 LinkCountBefore = CountLinks(Graph);
	TSet<FString> BeforeEdges;
	TSet<FString> AfterEdges;
	if (Plan.bDisconnect)
	{
		CollectEdgeKeys(Graph, BeforeEdges);
	}
	if (Plan.bHasExistingPin)
	{
		FString CurrentEdgeSetHash;
		if (!UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(
				Graph,
				CurrentEdgeSetHash)
			|| !CurrentEdgeSetHash.Equals(Plan.BeforeEdgeSetHash, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-edge-set-stale"),
				TEXT("The live edge set changed after Preview; refusing to connect to an existing pin."));
		}
	}
	FString AfterEdgeSetHash;
	TMap<FString, UEdGraphNode*> CreatedNodes;
	TMap<FString, FGuid> CreatedGuidByTempId;
	TArray<FGuid> CreatedGuids;

	{
		FScopedTransaction Transaction(
			FText::FromString(TEXT("UEAgentKit Create Blueprint Nodes")));
		Blueprint->Modify();
		Graph->Modify();

		auto Abort = [&OutErrorCode, &OutErrorMessage, &Transaction](
			const TCHAR* Code,
			const FString& Message) -> bool
		{
			Transaction.Cancel();
			return Fail(OutErrorCode, OutErrorMessage, Code, Message);
		};

		for (FNodeSpec& Spec : Plan.Nodes)
		{
			if (Spec.bOwnedCall
				&& !ResolveOwnedCall(
					Blueprint,
					Graph,
					Spec,
					OutErrorCode,
					OutErrorMessage))
			{
				return Abort(*OutErrorCode, OutErrorMessage);
			}
		}

		for (const FNodeSpec& Spec : Plan.Nodes)
		{
			UEdGraphNode* Node = nullptr;
			if (Spec.bVariableAccessor)
			{
				FProperty* Property = FindMemberVariableProperty(Blueprint, FName(*Spec.VariableName));
				if (Property == nullptr)
				{
					return Abort(
						TEXT("graph-edit-variable-property-unavailable"),
						FString::Printf(
							TEXT("Variable %s no longer has a generated/skeleton property."),
							*Spec.VariableName));
				}
				FEdGraphPinType PropertyType;
				FString ActualType;
				if (!GetDefault<UEdGraphSchema_K2>()->ConvertPropertyToPinType(Property, PropertyType)
					|| !VariableTypeToken(PropertyType, ActualType)
					|| !ActualType.Equals(Spec.VariableType, ESearchCase::CaseSensitive))
				{
					return Abort(
						TEXT("graph-edit-variable-type-mismatch"),
						FString::Printf(
							TEXT("Variable %s changed type after Preview."),
							*Spec.VariableName));
				}
				TSubclassOf<UK2Node_Variable> VariableNodeClass =
					Spec.NodeType.Equals(TEXT("variable_get"), ESearchCase::CaseSensitive)
						? UK2Node_VariableGet::StaticClass()
						: UK2Node_VariableSet::StaticClass();
				UBlueprintVariableNodeSpawner* VariableSpawner =
					UBlueprintVariableNodeSpawner::CreateFromMemberOrParam(
						VariableNodeClass,
						Property);
				if (VariableSpawner == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-spawn-failed"),
						FString::Printf(
							TEXT("Could not create a variable spawner for %s."),
							*Spec.VariableName));
				}
				Node = VariableSpawner->Invoke(
					Graph,
					IBlueprintNodeBinder::FBindingSet(),
					FVector2D(Spec.X, Spec.Y));
			}
			else if (!Spec.FunctionName.IsEmpty())
			{
				UClass* FunctionOwner = nullptr;
				if (Spec.bOwnedCall)
				{
					FunctionOwner = Blueprint != nullptr ? Blueprint->GeneratedClass : nullptr;
				}
				else if (Spec.bCallFunction)
				{
					FunctionOwner = LoadObject<UClass>(
						nullptr,
						*Spec.CallFunctionOwnerPath);
				}
				else
				{
					FunctionOwner = FunctionOwnerForNodeType(Spec.NodeType);
				}
				if (FunctionOwner == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-spawn-failed"),
						TEXT("The function owner class is unavailable."));
				}
				UFunction* Function = FunctionOwner->FindFunctionByName(
					FName(*Spec.FunctionName));
				if (Function == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-spawn-failed"),
						FString::Printf(TEXT("The function %s is unavailable."), *Spec.FunctionName));
				}
				if (Spec.bCallFunction)
				{
					const FCallFunctionAllowlistEntry* Entry = FindCallFunctionAllowlistEntry(
						Spec.CallFunctionOwnerPath,
						Spec.FunctionName);
					if (Entry == nullptr
						|| !Entry->SignatureDigest.Equals(
							Spec.CallFunctionSignatureDigest,
							ESearchCase::CaseSensitive))
					{
						return Abort(
							TEXT("graph-edit-call-function-not-allowlisted"),
							TEXT("The CallFunction plan is no longer in the fixed allowlist."));
					}
				}
				UK2Node_CallFunction* CallNode = NewObject<UK2Node_CallFunction>(Graph);
				if (CallNode == nullptr)
				{
					return Abort(
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
				if (Spec.bCallFunction || Spec.bOwnedCall)
				{
					FString PinReason;
					if (!ValidateCallFunctionNode(CallNode, Spec, PinReason))
					{
						return Abort(
							Spec.bOwnedCall
								? TEXT("graph-edit-owned-call-pin-mismatch")
								: TEXT("graph-edit-call-function-pin-mismatch"),
							PinReason);
					}
				}
				if (Spec.bOwnedCall)
				{
					for (const FOwnedLiteralSpec& Literal : Spec.InputLiterals)
					{
						UEdGraphPin* LiteralPin = CallNode->FindPin(
							FName(*Literal.PinName),
							EGPD_Input);
						if (LiteralPin == nullptr
							|| LiteralPin->bHidden
							|| LiteralPin->bDefaultValueIsReadOnly
							|| LiteralPin->bDefaultValueIsIgnored
							|| LiteralPin->LinkedTo.Num() != 0
							|| !PinMatchesType(LiteralPin, Literal.Type))
						{
							return Abort(
								TEXT("graph-edit-owned-literal-pin-mismatch"),
								TEXT("The literal input pin is missing, hidden, connected or of the wrong type."));
						}
						LiteralPin->Modify();
						LiteralPin->DefaultValue = Literal.Value;
						if (!LiteralPin->DefaultValue.Equals(
							Literal.Value,
							ESearchCase::CaseSensitive))
						{
							return Abort(
								TEXT("graph-edit-owned-literal-readback-failed"),
								TEXT("The literal default did not read back exactly."));
						}
					}
				}
				Node = CallNode;
			}
			else
			{
				UClass* NodeClass = NodeClassForType(Spec.NodeType);
				if (NodeClass == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-type-not-allowed"),
						FString::Printf(TEXT("Unsupported nodeType: %s"), *Spec.NodeType));
				}
				UBlueprintNodeSpawner* Spawner = UBlueprintNodeSpawner::Create(NodeClass, GetTransientPackage());
				if (Spawner == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-spawn-failed"),
						FString::Printf(TEXT("Could not create a node spawner for %s."), *Spec.NodeType));
				}
				Node = Spawner->Invoke(
					Graph,
					IBlueprintNodeBinder::FBindingSet(),
					FVector2D(Spec.X, Spec.Y));
			}
			// Keep every node created by this tool transactional.  Undo/Redo
			// verification recreates nodes, and later bounded tools (pin defaults,
			// rewire, node removal) must be able to record an undoable change on
			// them in the same Editor session.
			if (Node == nullptr)
			{
				return Abort(
					TEXT("graph-edit-node-spawn-failed"),
					FString::Printf(TEXT("The Editor failed to spawn %s."), *Spec.NodeType));
			}
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
				return Abort(
					TEXT("graph-edit-node-spawn-failed"),
					FString::Printf(TEXT("Spawned %s has no pins or no valid NodeGuid."), *Spec.NodeType));
			}
			if (Spec.bVariableAccessor)
			{
				FString ContractReason;
				if (!VariableNodeMatchesSpec(Node, Spec, ContractReason))
				{
					return Abort(
						TEXT("graph-edit-variable-pin-contract-failed"),
						ContractReason);
				}
			}
			CreatedNodes.Add(Spec.TempId, Node);
			CreatedGuidByTempId.Add(Spec.TempId, Node->NodeGuid);
			CreatedGuids.Add(Node->NodeGuid);
		}

		for (const FConnectionSpec& Connection : Plan.Connections)
		{
			UEdGraphPin* FromPin = nullptr;
			UEdGraphPin* ToPin = nullptr;
			if (Plan.bDisconnect)
			{
				FExistingPinSpec ResolvedFrom = Connection.FromExisting;
				FExistingPinSpec ResolvedTo = Connection.ToExisting;
				if (!ResolveExistingPin(
						Graph,
						ResolvedFrom,
						FromPin,
						OutErrorCode,
						OutErrorMessage,
						true)
					|| !ResolveExistingPin(
						Graph,
						ResolvedTo,
						ToPin,
						OutErrorCode,
						OutErrorMessage,
						true))
				{
					return Abort(*OutErrorCode, OutErrorMessage);
				}
				if (!FromPin->LinkedTo.Contains(ToPin)
					|| ToPin->LinkedTo.Num() != 1
					|| !ToPin->LinkedTo.Contains(FromPin))
				{
					return Abort(
						TEXT("graph-edit-disconnect-edge-not-found"),
						TEXT("The exact edge is no longer present or the input has become ambiguous."));
				}
				FromPin->BreakLinkTo(ToPin);
				if (FromPin->LinkedTo.Contains(ToPin) || ToPin->LinkedTo.Contains(FromPin))
				{
					return Abort(
						TEXT("graph-edit-disconnect-failed"),
						TEXT("The Editor did not remove the exact edge."));
				}
				continue;
			}
			if (Connection.bFromExisting && Connection.bToExisting)
			{
				FExistingPinSpec ResolvedFrom = Connection.FromExisting;
				FExistingPinSpec ResolvedTo = Connection.ToExisting;
				if (!ResolveExistingPin(
						Graph,
						ResolvedFrom,
						FromPin,
						OutErrorCode,
						OutErrorMessage)
					|| !ResolveExistingPin(
						Graph,
						ResolvedTo,
						ToPin,
						OutErrorCode,
						OutErrorMessage))
				{
					return Abort(*OutErrorCode, OutErrorMessage);
				}
				if (FromPin->LinkedTo.Num() != Connection.FromExisting.LinkCount)
				{
					return Abort(
						TEXT("graph-edit-edge-set-stale"),
						TEXT("The existing output pin fan-out changed after Preview."));
				}
				if (ToPin->LinkedTo.Num() != 0)
				{
					return Abort(
						TEXT("graph-edit-existing-pin-already-linked"),
						TEXT("The existing input pin became connected after Preview."));
				}
				if (FromPin->GetOwningNode() == ToPin->GetOwningNode())
				{
					return Abort(
						TEXT("graph-edit-self-connection"),
						TEXT("The two existing endpoints must belong to different nodes."));
				}
			}
			else if (Connection.bFromExisting)
			{
				FExistingPinSpec Resolved = Connection.FromExisting;
				if (!ResolveExistingPin(
					Graph,
					Resolved,
					FromPin,
					OutErrorCode,
					OutErrorMessage))
				{
					return Abort(*OutErrorCode, OutErrorMessage);
				}
				if (FromPin->LinkedTo.Num() != Connection.FromExisting.LinkCount)
				{
					return Abort(
						TEXT("graph-edit-edge-set-stale"),
						TEXT("The existing output pin fan-out changed after Preview."));
				}
				UEdGraphNode** ToNode = CreatedNodes.Find(Connection.To);
				if (ToNode == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-reference-unknown"),
						TEXT("A connection referenced a node that was not created."));
				}
				ToPin = (*ToNode)->FindPin(FName(*Connection.ToPin), EGPD_Input);
			}
			else if (Connection.bToExisting)
			{
				UEdGraphNode** FromNode = CreatedNodes.Find(Connection.From);
				if (FromNode == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-reference-unknown"),
						TEXT("A connection referenced a node that was not created."));
				}
				FromPin = (*FromNode)->FindPin(FName(*Connection.FromPin), EGPD_Output);
				FExistingPinSpec Resolved = Connection.ToExisting;
				if (!ResolveExistingPin(
					Graph,
					Resolved,
					ToPin,
					OutErrorCode,
					OutErrorMessage))
				{
					return Abort(*OutErrorCode, OutErrorMessage);
				}
				if (ToPin->LinkedTo.Num() != 0)
				{
					return Abort(
						TEXT("graph-edit-existing-pin-already-linked"),
						TEXT("The existing input pin became connected after Preview."));
				}
			}
			else
			{
				UEdGraphNode** FromNode = CreatedNodes.Find(Connection.From);
				UEdGraphNode** ToNode = CreatedNodes.Find(Connection.To);
				if (FromNode == nullptr || ToNode == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-reference-unknown"),
						TEXT("A connection referenced a node that was not created."));
				}
				FromPin = (*FromNode)->FindPin(FName(*Connection.FromPin), EGPD_Output);
				ToPin = (*ToNode)->FindPin(FName(*Connection.ToPin), EGPD_Input);
			}
			if (FromPin == nullptr || ToPin == nullptr)
			{
				return Abort(
					TEXT("graph-edit-pin-not-found"),
					TEXT("The planned nodes do not expose the declared input/output pins."));
			}
			if (!PinMatchesType(FromPin, Connection.FromType)
				|| !PinMatchesType(ToPin, Connection.ToType))
			{
				return Abort(
					TEXT("graph-edit-pin-type-mismatch"),
					FString::Printf(
						TEXT("The resolved pins do not match the declared types %s -> %s."),
						*Connection.FromType,
						*Connection.ToType));
			}
			if (Connection.bFromExisting || Connection.bToExisting)
			{
				if (!ValidateSchemaConnection(
					Graph,
					FromPin,
					ToPin,
					OutErrorCode,
					OutErrorMessage))
				{
					return Abort(*OutErrorCode, OutErrorMessage);
				}
			}
			if (!Graph->GetSchema()->TryCreateConnection(FromPin, ToPin)
				|| !ArePinsLinked(FromPin, ToPin))
			{
				return Abort(
					TEXT("graph-edit-connection-failed"),
					TEXT("The Editor rejected the planned connection."));
			}
		}

		Blueprint->GetOutermost()->MarkPackageDirty();
	}

	auto FailCommitted = [&](const TCHAR* Code, const FString& Message) -> bool
	{
		bool bHasAppliedState = Graph->Nodes.Num() != NodeCountBefore;
		for (const FGuid& Guid : CreatedGuids)
		{
			bHasAppliedState = bHasAppliedState || HasNodeGuid(Graph, Guid);
		}
		if (bHasAppliedState && GEditor != nullptr && GEditor->Trans != nullptr)
		{
			GEditor->UndoTransaction();
		}
		const bool bRestored = Graph->Nodes.Num() == NodeCountBefore
			&& CountLinks(Graph) == LinkCountBefore;
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			Code,
			Message + (bRestored
				? TEXT(" The applied changes were rolled back.")
				: TEXT(" WARNING: automatic rollback did not restore the original graph.")));
	};

	TSharedPtr<FJsonObject> PostPreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		PostPreflight,
		OutErrorCode,
		OutErrorMessage))
	{
		return FailCommitted(
			TEXT("graph-edit-readback-failed"),
			TEXT("Post-Apply preflight failed: ") + OutErrorCode + TEXT(" ") + OutErrorMessage);
	}
	if (!PostPreflight->GetBoolField(TEXT("packageDirty")))
	{
		return FailCommitted(
			TEXT("graph-edit-readback-failed"),
			TEXT("The package did not become dirty after Apply."));
	}
	if (PostPreflight->GetNumberField(TEXT("nodeCount")) != NodeCountBefore + Plan.Nodes.Num())
	{
		return FailCommitted(
			TEXT("graph-edit-readback-failed"),
			TEXT("The graph node count does not match the previewed node count after Apply."));
	}

	const int32 NodeCountAfter = Graph->Nodes.Num();
	const int32 LinkCountAfter = CountLinks(Graph);
	const int32 ExpectedLinkDelta = Plan.bDisconnect
		? -Plan.Connections.Num()
		: Plan.Connections.Num();
	if (LinkCountAfter != LinkCountBefore + ExpectedLinkDelta)
	{
		return FailCommitted(
			TEXT("graph-edit-readback-failed"),
			TEXT("The graph link count does not match the previewed edge delta after Apply."));
	}
	if (Plan.bDisconnect)
	{
		CollectEdgeKeys(Graph, AfterEdges);
		int32 RemovedEdges = 0;
		int32 AddedEdges = 0;
		for (const FString& Edge : BeforeEdges)
		{
			RemovedEdges += AfterEdges.Contains(Edge) ? 0 : 1;
		}
		for (const FString& Edge : AfterEdges)
		{
			AddedEdges += BeforeEdges.Contains(Edge) ? 0 : 1;
		}
		const FConnectionSpec& Disconnect = Plan.Connections[0];
		const FString RemovedKey = FString::Printf(
			TEXT("%s|%s>%s|%s"),
			*Disconnect.FromExisting.NodeGuid,
			*Disconnect.FromExisting.PinGuid,
			*Disconnect.ToExisting.NodeGuid,
			*Disconnect.ToExisting.PinGuid);
		if (RemovedEdges != 1
			|| AddedEdges != 0
			|| !BeforeEdges.Contains(RemovedKey)
			|| AfterEdges.Contains(RemovedKey))
		{
			return FailCommitted(
				TEXT("graph-edit-disconnect-delta-failed"),
				TEXT("Disconnect did not remove exactly the declared edge while preserving all others."));
		}
	}
	if (Plan.bHasExistingPin)
	{
		if (!UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(
			Graph,
			AfterEdgeSetHash))
		{
			return FailCommitted(
				TEXT("graph-edit-readback-failed"),
				TEXT("Could not compute the after edge-set hash."));
		}
	}
	for (const FGuid& Guid : CreatedGuids)
	{
		if (!HasNodeGuid(Graph, Guid))
		{
			return FailCommitted(
				TEXT("graph-edit-readback-failed"),
				TEXT("At least one previewed node is missing from the live graph after Apply."));
		}
	}

	auto OwnedLiteralContractsMatch = [&](FString& OutReason) -> bool
	{
		for (const FNodeSpec& Spec : Plan.Nodes)
		{
			if (!Spec.bOwnedCall || Spec.InputLiterals.IsEmpty())
			{
				continue;
			}
			const FGuid* NodeGuid = CreatedGuidByTempId.Find(Spec.TempId);
			const UEdGraphNode* OwnedNode = nullptr;
			if (NodeGuid != nullptr)
			{
				for (const UEdGraphNode* Candidate : Graph->Nodes)
				{
					if (Candidate != nullptr && Candidate->NodeGuid == *NodeGuid)
					{
						OwnedNode = Candidate;
						break;
					}
				}
			}
			if (OwnedNode == nullptr)
			{
				OutReason = TEXT("The owned call node is missing for literal readback.");
				return false;
			}
			for (const FOwnedLiteralSpec& Literal : Spec.InputLiterals)
			{
				const UEdGraphPin* Pin = OwnedNode->FindPin(
					FName(*Literal.PinName),
					EGPD_Input);
				if (Pin == nullptr
					|| Pin->bHidden
					|| !Pin->DefaultValue.Equals(Literal.Value, ESearchCase::CaseSensitive))
				{
					OutReason = FString::Printf(
						TEXT("Owned literal %s did not read back exactly."),
						*Literal.PinName);
					return false;
				}
			}
		}
		return true;
	};
	FString OwnedLiteralReason;
	if (!OwnedLiteralContractsMatch(OwnedLiteralReason))
	{
		return FailCommitted(
			TEXT("graph-edit-owned-literal-readback-failed"),
			OwnedLiteralReason);
	}

	const bool bUndoCalled = GEditor->UndoTransaction();
	const bool bUndoNodes = Graph->Nodes.Num() == NodeCountBefore;
	const bool bUndoLinks = CountLinks(Graph) == LinkCountBefore;
	bool bUndoGuids = true;
	for (const FGuid& Guid : CreatedGuids)
	{
		bUndoGuids = bUndoGuids && !HasNodeGuid(Graph, Guid);
	}
	bool bUndoEdgeSet = true;
	if (Plan.bHasExistingPin)
	{
		FString UndoEdgeSetHash;
		bUndoEdgeSet = UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(
				Graph,
				UndoEdgeSetHash)
			&& UndoEdgeSetHash.Equals(Plan.BeforeEdgeSetHash, ESearchCase::CaseSensitive);
	}
	const bool bUndoVerified = bUndoCalled
		&& bUndoNodes
		&& bUndoLinks
		&& bUndoGuids
		&& bUndoEdgeSet;

	const bool bRedoCalled = bUndoVerified && GEditor->RedoTransaction();
	const bool bRedoNodes = Graph->Nodes.Num() == NodeCountAfter;
	const bool bRedoLinks = CountLinks(Graph) == LinkCountAfter;
	bool bRedoGuids = true;
	for (const FGuid& Guid : CreatedGuids)
	{
		bRedoGuids = bRedoGuids && HasNodeGuid(Graph, Guid);
	}
	bool bRedoEdgeSet = true;
	if (Plan.bHasExistingPin)
	{
		FString RedoEdgeSetHash;
		bRedoEdgeSet = UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(
				Graph,
				RedoEdgeSetHash)
			&& !AfterEdgeSetHash.IsEmpty()
			&& RedoEdgeSetHash.Equals(AfterEdgeSetHash, ESearchCase::CaseSensitive);
	}
	// Redo must restore not only the GUID but the exact member-variable binding
	// and engine pin contract, otherwise a renamed/retyped variable would look
	// like a successful accessor creation.
	bool bRedoVariableContracts = true;
	FString RedoVariableReason;
	for (const FNodeSpec& Spec : Plan.Nodes)
	{
		if (!Spec.bVariableAccessor)
		{
			continue;
		}
		const FGuid* CreatedGuid = CreatedGuidByTempId.Find(Spec.TempId);
		const UEdGraphNode* RestoredNode = nullptr;
		if (CreatedGuid != nullptr)
		{
			for (const UEdGraphNode* Candidate : Graph->Nodes)
			{
				if (Candidate != nullptr && Candidate->NodeGuid == *CreatedGuid)
				{
					RestoredNode = Candidate;
					break;
				}
			}
		}
		if (!VariableNodeMatchesSpec(RestoredNode, Spec, RedoVariableReason))
		{
			bRedoVariableContracts = false;
			break;
		}
	}
	const bool bRedoVerified = bRedoCalled
		&& bRedoNodes
		&& bRedoLinks
		&& bRedoGuids
		&& bRedoEdgeSet
		&& bRedoVariableContracts
		&& [&]()
		{
			FString Reason;
			return OwnedLiteralContractsMatch(Reason);
		}();
	if (!bUndoVerified || !bRedoVerified)
	{
		return FailCommitted(
			TEXT("graph-edit-undo-redo-failed"),
			FString::Printf(
				TEXT("Undo/Redo verification failed: undo=%s nodes=%s links=%s guids=%s edges=%s redo=%s edges=%s variables=%s %s"),
				bUndoCalled ? TEXT("true") : TEXT("false"),
				bUndoNodes ? TEXT("true") : TEXT("false"),
				bUndoLinks ? TEXT("true") : TEXT("false"),
				bUndoGuids ? TEXT("true") : TEXT("false"),
				bUndoEdgeSet ? TEXT("true") : TEXT("false"),
				bRedoVerified ? TEXT("true") : TEXT("false"),
				bRedoEdgeSet ? TEXT("true") : TEXT("false"),
				bRedoVariableContracts ? TEXT("true") : TEXT("false"),
				*RedoVariableReason));
	}

	TSharedPtr<FJsonObject> FinalPreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		FinalPreflight,
		OutErrorCode,
		OutErrorMessage))
	{
		return FailCommitted(
			TEXT("graph-edit-readback-failed"),
			TEXT("Final preflight failed: ") + OutErrorCode + TEXT(" ") + OutErrorMessage);
	}
	if (!FinalPreflight->GetBoolField(TEXT("packageDirty")))
	{
		return FailCommitted(
			TEXT("graph-edit-readback-failed"),
			TEXT("The package is not dirty after Redo."));
	}

	TArray<TSharedPtr<FJsonValue>> CreatedNodeValues;
	for (const FNodeSpec& Spec : Plan.Nodes)
	{
		const FGuid* NodeGuid = CreatedGuidByTempId.Find(Spec.TempId);
		if (NodeGuid == nullptr)
		{
			continue;
		}
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("tempId"), Spec.TempId);
		Item->SetStringField(TEXT("nodeId"), FString(TEXT("n:")) + NodeGuid->ToString(EGuidFormats::DigitsWithHyphensLower));
		Item->SetStringField(TEXT("nodeClass"), Spec.NodeClassPath);
		if (!Spec.FunctionName.IsEmpty())
		{
			Item->SetStringField(TEXT("function"), Spec.FunctionName);
		}
		if (Spec.bVariableAccessor)
		{
			Item->SetStringField(TEXT("variableName"), Spec.VariableName);
			Item->SetStringField(TEXT("variableGuid"), Spec.VariableGuid);
			Item->SetStringField(TEXT("variableType"), Spec.VariableType);
			Item->SetStringField(TEXT("variableAccess"), Spec.VariableAccess);
		}
		Item->SetNumberField(TEXT("x"), Spec.X);
		Item->SetNumberField(TEXT("y"), Spec.Y);
		CreatedNodeValues.Add(MakeShared<FJsonValueObject>(Item));
	}

	TArray<TSharedPtr<FJsonValue>> ConnectionValues;
	auto MakeExistingPinObject = [](const FExistingPinSpec& Spec)
	{
		TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
		Object->SetStringField(TEXT("nodeGuid"), Spec.NodeGuid);
		Object->SetStringField(TEXT("pinGuid"), Spec.PinGuid);
		Object->SetStringField(TEXT("pinName"), Spec.PinName);
		Object->SetStringField(TEXT("direction"), Spec.Direction);
		Object->SetStringField(TEXT("category"), Spec.Category);
		Object->SetStringField(TEXT("subCategory"), Spec.SubCategory);
		return Object;
	};
	for (const FConnectionSpec& Connection : Plan.Connections)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		if (Connection.bFromExisting)
		{
			Item->SetObjectField(TEXT("fromExisting"), MakeExistingPinObject(Connection.FromExisting));
		}
		else
		{
			Item->SetStringField(TEXT("from"), Connection.From);
			Item->SetStringField(TEXT("fromPin"), Connection.FromPin);
		}
		Item->SetStringField(TEXT("fromType"), Connection.FromType);
		if (Connection.bToExisting)
		{
			Item->SetObjectField(TEXT("toExisting"), MakeExistingPinObject(Connection.ToExisting));
		}
		else
		{
			Item->SetStringField(TEXT("to"), Connection.To);
			Item->SetStringField(TEXT("toPin"), Connection.ToPin);
		}
		Item->SetStringField(TEXT("toType"), Connection.ToType);
		ConnectionValues.Add(MakeShared<FJsonValueObject>(Item));
	}

	if (RecentApplies.Num() > 16)
	{
		RecentApplies.Reset();
	}
	FApplyReceipt Receipt;
	Receipt.AssetPath = Plan.AssetPath;
	Receipt.GraphGuid = Plan.GraphGuid;
	Receipt.GraphFingerprint = FinalPreflight->GetStringField(TEXT("graphFingerprint"));
	Receipt.NodeCount = NodeCountAfter;
	Receipt.LinkCount = LinkCountAfter;
	Receipt.VariableBindingHash = ComputeVariableBindingHash(Graph);
	if (Receipt.VariableBindingHash.IsEmpty())
	{
		return FailCommitted(
			TEXT("graph-edit-readback-failed"),
			TEXT("Could not compute the variable binding hash after Apply."));
	}
	if (Plan.bHasExistingPin)
	{
		Receipt.bRequiresEdgeSetHash = true;
		Receipt.EdgeSetHash = AfterEdgeSetHash;
		if (Receipt.EdgeSetHash.IsEmpty())
		{
			return FailCommitted(
				TEXT("graph-edit-readback-failed"),
				TEXT("Could not bind the after edge-set hash to the Apply receipt."));
		}
	}
	for (const FNodeSpec& Spec : Plan.Nodes)
	{
		if (!Spec.bOwnedCall || Spec.InputLiterals.IsEmpty())
		{
			continue;
		}
		const FGuid* OwnedGuid = CreatedGuidByTempId.Find(Spec.TempId);
		if (OwnedGuid == nullptr)
		{
			return FailCommitted(
				TEXT("graph-edit-owned-literal-readback-failed"),
				TEXT("The owned call node GUID is missing from the Apply receipt."));
		}
		Receipt.OwnedLiteralNodeGuid = OwnedGuid->ToString(
			EGuidFormats::DigitsWithHyphensLower);
		for (const FOwnedLiteralSpec& Literal : Spec.InputLiterals)
		{
			Receipt.OwnedLiteralValues.Add(
				Literal.PinName + TEXT(":") + Literal.Type,
				Literal.Value);
		}
		Receipt.OwnedLiteralHash = ComputeOwnedLiteralHash(
			Receipt.OwnedLiteralNodeGuid,
			Receipt.OwnedLiteralValues);
		Receipt.bRequiresOwnedLiteralHash = true;
		if (Receipt.OwnedLiteralHash.IsEmpty())
		{
			return FailCommitted(
				TEXT("graph-edit-owned-literal-readback-failed"),
				TEXT("Could not bind owned literal defaults to the Apply receipt."));
		}
	}
	RecentApplies.Add(Plan.PlanId, Receipt);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphpatch-edit-apply/1.0"));
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
	Result->SetNumberField(TEXT("nodeCountBefore"), NodeCountBefore);
	Result->SetNumberField(TEXT("nodeCountAfter"), NodeCountAfter);
	Result->SetStringField(TEXT("operation"), Plan.Operation);
	Result->SetNumberField(TEXT("createdNodeCount"), Plan.Nodes.Num());
	Result->SetNumberField(
		TEXT("createdConnectionCount"),
		Plan.bDisconnect ? 0 : Plan.Connections.Num());
	Result->SetNumberField(
		TEXT("removedConnectionCount"),
		Plan.bDisconnect ? Plan.Connections.Num() : 0);
	Result->SetNumberField(TEXT("linkCountBefore"), LinkCountBefore);
	Result->SetNumberField(TEXT("linkCountAfter"), LinkCountAfter);
	Result->SetBoolField(TEXT("packageDirtyAfter"), true);
	Result->SetStringField(TEXT("packageRevision"), FinalPreflight->GetStringField(TEXT("packageRevision")));
	Result->SetStringField(TEXT("graphFingerprintAfter"), FinalPreflight->GetStringField(TEXT("graphFingerprint")));
	Result->SetStringField(TEXT("variableBindingHash"), Receipt.VariableBindingHash);
	if (Plan.bHasExistingPin)
	{
		Result->SetStringField(TEXT("beforeEdgeSetHash"), Plan.BeforeEdgeSetHash);
		Result->SetStringField(TEXT("afterEdgeSetHash"), AfterEdgeSetHash);
		Result->SetStringField(TEXT("edgeSetHash"), AfterEdgeSetHash);
	}
	Result->SetArrayField(TEXT("createdNodes"), CreatedNodeValues);
	if (Plan.bDisconnect)
	{
		Result->SetArrayField(TEXT("removedConnections"), ConnectionValues);
	}
	else
	{
		Result->SetArrayField(TEXT("createdConnections"), ConnectionValues);
	}
	OutResult = Result;
	return true;
}

bool UEAgentKitBlueprintGraphEditExperimental::TrySaveExperimental(
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
			TEXT("graph-edit-experimental-disabled"),
			TEXT("Experimental graph structure edit is not opted in for this project. "
				 "Set [UEAgentKit] ExperimentalGraphPatchEdit=True in the project config."));
	}
	if (GEditor == nullptr || (GEditor->IsPlaySessionInProgress() || GEditor->PlayWorld != nullptr))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-pie-active"),
			TEXT("Experimental graph edit Save is unavailable while PIE or SIE is active."));
	}

	FString AssetPath;
	FString GraphGuid;
	FString PlanId;
	FString ExpectedFingerprint;
	FString Confirmation;
	if (!ReadString(Params, TEXT("assetPath"), AssetPath, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("graphGuid"), GraphGuid, OutErrorCode, OutErrorMessage)
		|| !ReadString(Params, TEXT("planId"), PlanId, OutErrorCode, OutErrorMessage)
		|| !ReadString(
			Params,
			TEXT("expectedGraphFingerprint"),
			ExpectedFingerprint,
			OutErrorCode,
			OutErrorMessage)
		|| !ReadString(Params, TEXT("confirmation"), Confirmation, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!IsSha256(PlanId) || !IsSha1(ExpectedFingerprint))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-parameters"),
			TEXT("Save requires a canonical sha256 planId and sha1 expectedGraphFingerprint."));
	}

	double NodeCountNumber = 0.0;
	double LinkCountNumber = 0.0;
	if (!Params->TryGetNumberField(TEXT("expectedNodeCount"), NodeCountNumber)
		|| !Params->TryGetNumberField(TEXT("expectedLinkCount"), LinkCountNumber)
		|| FMath::Abs(NodeCountNumber - FMath::RoundToDouble(NodeCountNumber)) > 0.0001
		|| FMath::Abs(LinkCountNumber - FMath::RoundToDouble(LinkCountNumber)) > 0.0001
		|| NodeCountNumber < 0.0
		|| LinkCountNumber < 0.0)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-invalid-parameters"),
			TEXT("Save requires non-negative expectedNodeCount and expectedLinkCount integers."));
	}
	const int32 ExpectedNodeCount = static_cast<int32>(NodeCountNumber);
	const int32 ExpectedLinkCount = static_cast<int32>(LinkCountNumber);
	const FString ExpectedConfirmation =
		FString(SaveConfirmationPrefix) + PlanId;
	if (!Confirmation.Equals(ExpectedConfirmation, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-confirmation-required"),
			FString::Printf(TEXT("Explicit Save confirmation must be exactly %s."), *ExpectedConfirmation));
	}

	const FApplyReceipt* Receipt = RecentApplies.Find(PlanId);
	if (Receipt == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-requires-apply"),
			TEXT("Save requires a successful Apply of the exact plan in this Editor session."));
	}
	if (!Receipt->AssetPath.Equals(AssetPath, ESearchCase::CaseSensitive)
		|| !Receipt->GraphGuid.Equals(GraphGuid, ESearchCase::CaseSensitive)
		|| !Receipt->GraphFingerprint.Equals(ExpectedFingerprint, ESearchCase::CaseSensitive)
		|| Receipt->NodeCount != ExpectedNodeCount
		|| Receipt->LinkCount != ExpectedLinkCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-plan-mismatch"),
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
		|| !Preflight->GetStringField(TEXT("graphGuid")).Equals(GraphGuid, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-focused-graph-mismatch"),
			TEXT("The focused Blueprint editor is not showing the exact saved asset/graph."));
	}
	if (!Preflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-not-dirty"),
			TEXT("Experimental graph edit Save requires the exact package to be dirty."));
	}
	if (!Preflight->GetStringField(TEXT("graphFingerprint")).Equals(
		ExpectedFingerprint,
		ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-fingerprint-conflict"),
			TEXT("The focused graph changed after the graph edit Apply; refusing to save unrelated edits."));
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
		|| CountLinks(Graph) != ExpectedLinkCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-plan-mismatch"),
			TEXT("The live graph node/link counts no longer match the Apply receipt."));
	}
	const FString LiveVariableBindingHash = ComputeVariableBindingHash(Graph);
	if (LiveVariableBindingHash.IsEmpty()
		|| !LiveVariableBindingHash.Equals(Receipt->VariableBindingHash, ESearchCase::CaseSensitive))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-variable-binding-mismatch"),
			TEXT("A variable accessor binding changed after Apply; refusing to save it under this plan."));
	}
	if (Receipt->bRequiresEdgeSetHash)
	{
		FString LiveEdgeSetHash;
		if (!UEAgentKitBlueprintRewireExperimental::TryComputeLiveEdgeSetHash(
				Graph,
				LiveEdgeSetHash)
			|| !LiveEdgeSetHash.Equals(Receipt->EdgeSetHash, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-save-edge-set-mismatch"),
				TEXT("The edge set changed after Apply; refusing to save the existing-pin plan."));
		}
	}

	if (Receipt->bRequiresOwnedLiteralHash)
	{
		const UEdGraphNode* OwnedNode = nullptr;
		for (const UEdGraphNode* Candidate : Graph->Nodes)
		{
			if (Candidate != nullptr
				&& Candidate->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
					.Equals(Receipt->OwnedLiteralNodeGuid, ESearchCase::CaseSensitive))
			{
				OwnedNode = Candidate;
				break;
			}
		}
		if (OwnedNode == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-save-owned-literal-mismatch"),
				TEXT("The owned call node disappeared after Apply."));
		}
		TMap<FString, FString> LiveValues;
		for (const TPair<FString, FString>& Pair : Receipt->OwnedLiteralValues)
		{
			FString PinName;
			FString PinType;
			if (!Pair.Key.Split(TEXT(":"), &PinName, &PinType))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-save-owned-literal-mismatch"),
					TEXT("The owned literal receipt is malformed."));
			}
			const UEdGraphPin* Pin = OwnedNode->FindPin(
				FName(*PinName),
				EGPD_Input);
			if (Pin == nullptr
				|| Pin->bHidden
				|| !Pin->DefaultValue.Equals(Pair.Value, ESearchCase::CaseSensitive)
				|| !PinMatchesType(Pin, PinType))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-save-owned-literal-mismatch"),
					FString::Printf(
						TEXT("Owned literal %s changed after Apply."),
						*PinName));
			}
			LiveValues.Add(Pair.Key, Pin->DefaultValue);
		}
		const FString LiveHash = ComputeOwnedLiteralHash(
			Receipt->OwnedLiteralNodeGuid,
			LiveValues);
		if (LiveHash.IsEmpty()
			|| !LiveHash.Equals(Receipt->OwnedLiteralHash, ESearchCase::CaseSensitive))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-save-owned-literal-mismatch"),
				TEXT("Owned literal defaults changed after Apply; refusing to save."));
		}
	}

	UPackage* Package = Blueprint->GetOutermost();
	if (Package == nullptr)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-package-invalid"),
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
			TEXT("graph-edit-save-package-file-missing"),
			TEXT("The exact Blueprint package file does not exist on disk."));
	}
	const int64 FileSizeBefore = IFileManager::Get().FileSize(*Filename);

	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	SaveArgs.Error = GError;
	if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs)
		|| Package->IsDirty())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-failed"),
			TEXT("SavePackage did not confirm a clean saved package."));
	}

	FString AssetDigest;
	if (!FBlueprintContextSha256::HashFile(Filename, AssetDigest))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-hash-failed"),
			TEXT("Could not hash the saved Blueprint package."));
	}
	AssetDigest = AssetDigest.ToLower();

	TSharedPtr<FJsonObject> PostSavePreflight;
	FString PostCode;
	FString PostMessage;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		PostSavePreflight,
		PostCode,
		PostMessage))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-readback-failed"),
			TEXT("Post-save preflight failed: ") + PostCode + TEXT(" ") + PostMessage);
	}
	if (PostSavePreflight->GetBoolField(TEXT("packageDirty"))
		|| !PostSavePreflight->GetStringField(TEXT("graphFingerprint")).Equals(
			ExpectedFingerprint,
			ESearchCase::CaseSensitive)
		|| PostSavePreflight->GetNumberField(TEXT("nodeCount")) != ExpectedNodeCount)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-readback-failed"),
			TEXT("The saved graph fingerprint/node count does not match the Apply receipt."));
	}

	const int64 FileSizeAfter = IFileManager::Get().FileSize(*Filename);
	if (FileSizeAfter < 0)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-save-readback-failed"),
			TEXT("The saved package file could not be read back."));
	}

	TSharedRef<FJsonObject> Readback = MakeShared<FJsonObject>();
	Readback->SetStringField(TEXT("assetPath"), AssetPath);
	Readback->SetStringField(TEXT("graphGuid"), GraphGuid);
	Readback->SetStringField(TEXT("assetSha256"), FString(TEXT("sha256:")) + AssetDigest);
	Readback->SetStringField(
		TEXT("packageRevision"),
		PostSavePreflight->GetStringField(TEXT("packageRevision")));
	Readback->SetStringField(
		TEXT("graphFingerprint"),
		PostSavePreflight->GetStringField(TEXT("graphFingerprint")));
	Readback->SetBoolField(TEXT("packageDirty"), false);
	Readback->SetNumberField(TEXT("nodeCount"), ExpectedNodeCount);
	Readback->SetNumberField(TEXT("linkCount"), ExpectedLinkCount);
	Readback->SetBoolField(TEXT("independentReloadVerified"), false);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("save-experimental-graph-edit"));
	Result->SetBoolField(TEXT("experimental"), true);
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("graphGuid"), GraphGuid);
	Result->SetStringField(TEXT("planId"), PlanId);
	Result->SetStringField(TEXT("confirmation"), Confirmation);
	Result->SetStringField(TEXT("assetSha256"), FString(TEXT("sha256:")) + AssetDigest);
	Result->SetStringField(
		TEXT("packageRevision"),
		PostSavePreflight->GetStringField(TEXT("packageRevision")));
	Result->SetStringField(
		TEXT("graphFingerprint"),
		PostSavePreflight->GetStringField(TEXT("graphFingerprint")));
	Result->SetStringField(TEXT("variableBindingHash"), Receipt->VariableBindingHash);
	if (Receipt->bRequiresEdgeSetHash)
	{
		Result->SetStringField(TEXT("edgeSetHash"), Receipt->EdgeSetHash);
	}
	if (Receipt->bRequiresOwnedLiteralHash)
	{
		Result->SetStringField(TEXT("ownedLiteralHash"), Receipt->OwnedLiteralHash);
	}
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
	RecentApplies.Remove(PlanId);
	OutResult = Result;
	return true;
}
