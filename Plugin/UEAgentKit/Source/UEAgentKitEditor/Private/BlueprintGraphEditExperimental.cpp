#include "BlueprintGraphEditExperimental.h"

#include "BlueprintContextSha256.h"
#include "BlueprintGraphLayoutApply.h"

#include "BlueprintNodeBinder.h"
#include "BlueprintNodeSpawner.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "K2Node_CallFunction.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_IfThenElse.h"
#include "Kismet/KismetMathLibrary.h"
#include "ScopedTransaction.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Guid.h"
#include "UObject/Package.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAgentKitGraphEditExperimental, Log, All);

namespace
{
	constexpr int32 MaxNodes = 8;
	constexpr int32 MaxConnections = 12;
	constexpr int32 MaxConnectionsPerNode = 6;
	constexpr int32 MaxCoordinate = 100000;
	const TCHAR* const PreviewSchemaVersion = TEXT("ueak.graphpatch-edit-preview/1.0");
	const TCHAR* const BranchClassPath = TEXT("/Script/BlueprintGraph.K2Node_IfThenElse");
	const TCHAR* const SequenceClassPath = TEXT("/Script/BlueprintGraph.K2Node_ExecutionSequence");
	const TCHAR* const CallFunctionClassPath = TEXT("/Script/BlueprintGraph.K2Node_CallFunction");
	const TCHAR* const NotBoolFunctionName = TEXT("Not_PreBool");

	bool bTestOverrideSet = false;
	bool bTestOverrideEnabled = false;

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
	};

	struct FConnectionSpec
	{
		FString From;
		FString FromPin;
		FString FromType;
		FString To;
		FString ToPin;
		FString ToType;
	};

	struct FEditPlan
	{
		FString AssetPath;
		FString GraphGuid;
		FString PackageRevision;
		FString GraphFingerprint;
		FString PlanId;
		TArray<FNodeSpec> Nodes;
		TArray<FConnectionSpec> Connections;
		TSharedPtr<FJsonObject> Preview;
	};

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
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-node-type-not-allowed"),
			TEXT("Only nodeType 'branch', 'sequence' and 'not_bool' are supported by this experimental slice."));
	}

	const FNodeSpec* FindNode(const TArray<FNodeSpec>& Nodes, const FString& TempId)
	{
		return Nodes.FindByPredicate([&TempId](const FNodeSpec& Node)
		{
			return Node.TempId.Equals(TempId, ESearchCase::CaseSensitive);
		});
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
			|| Values->IsEmpty()
			|| Values->Num() > MaxNodes)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-invalid-nodes"),
				FString::Printf(TEXT("nodes must contain between 1 and %d node objects."), MaxNodes));
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
				{ TEXT("tempId"), TEXT("nodeType"), TEXT("x"), TEXT("y") },
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
			OutNodes.Add(MoveTemp(Node));
		}
		return true;
	}

	bool ParseConnections(
		const TSharedPtr<FJsonObject>& Params,
		const TArray<FNodeSpec>& Nodes,
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

		TSet<FString> SeenSources;
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
				{ TEXT("from"), TEXT("fromPin"), TEXT("to"), TEXT("toPin") },
				OutErrorCode,
				OutErrorMessage))
			{
				return false;
			}
			FConnectionSpec Connection;
			if (!ReadString(*Object, TEXT("from"), Connection.From, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("fromPin"), Connection.FromPin, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("to"), Connection.To, OutErrorCode, OutErrorMessage)
				|| !ReadString(*Object, TEXT("toPin"), Connection.ToPin, OutErrorCode, OutErrorMessage))
			{
				return false;
			}
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
			if (!FromType->Equals(*ToType, ESearchCase::CaseSensitive))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-pin-type-mismatch"),
					FString::Printf(
						TEXT("Cannot connect %s.%s (%s) to %s.%s (%s); data kinds must match exactly."),
						*Connection.From,
						*Connection.FromPin,
						**FromType,
						*Connection.To,
						*Connection.ToPin,
						**ToType));
			}
			const FString SourceKey = Connection.From + TEXT("|") + Connection.FromPin;
			if (SeenSources.Contains(SourceKey))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-connection-duplicate"),
					FString::Printf(TEXT("Output pin already has a connection in this plan: %s"), *SourceKey));
			}
			const FString TargetKey = Connection.To + TEXT("|") + Connection.ToPin;
			if (SeenTargets.Contains(TargetKey))
			{
				return Fail(
					OutErrorCode,
					OutErrorMessage,
					TEXT("graph-edit-connection-duplicate"),
					FString::Printf(TEXT("Input pin already has a connection in this plan: %s"), *TargetKey));
			}
			SeenSources.Add(SourceKey);
			SeenTargets.Add(TargetKey);
			for (const FString& TempId : { Connection.From, Connection.To })
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
			Connection.FromType = *FromType;
			Connection.ToType = *ToType;
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
		if (!ReadString(Params, TEXT("assetPath"), OutPlan.AssetPath, OutErrorCode, OutErrorMessage)
			|| !ReadString(Params, TEXT("graphGuid"), OutPlan.GraphGuid, OutErrorCode, OutErrorMessage)
			|| !ParseNodes(Params, OutPlan.Nodes, OutErrorCode, OutErrorMessage)
			|| !ParseConnections(Params, OutPlan.Nodes, OutPlan.Connections, OutErrorCode, OutErrorMessage))
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

		OutPlan.PackageRevision = Preflight->GetStringField(TEXT("packageRevision"));
		OutPlan.GraphFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));

		FString Canonical;
		Canonical.Reserve(4096);
		Canonical += TEXT("ueak.graphpatch-edit/1.0\n");
		Canonical += OutPlan.AssetPath + TEXT("\n");
		Canonical += OutPlan.GraphGuid + TEXT("\n");
		Canonical += OutPlan.PackageRevision + TEXT("\n");
		Canonical += OutPlan.GraphFingerprint + TEXT("\n");

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
			Canonical += FString::Printf(
				TEXT("C|%s|%s|%s|%s\n"),
				*Connection.From,
				*Connection.FromPin,
				*Connection.To,
				*Connection.ToPin);
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

		TArray<TSharedPtr<FJsonValue>> ConnectionValues;
		for (const FConnectionSpec& Connection : OutPlan.Connections)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("from"), Connection.From);
			Item->SetStringField(TEXT("fromPin"), Connection.FromPin);
			Item->SetStringField(TEXT("fromType"), Connection.FromType);
			Item->SetStringField(TEXT("to"), Connection.To);
			Item->SetStringField(TEXT("toPin"), Connection.ToPin);
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

	bool ArePinsConnected(
		const UEdGraphNode* FromNode,
		const FString& FromPin,
		const UEdGraphNode* ToNode,
		const FString& ToPin)
	{
		if (FromNode == nullptr || ToNode == nullptr)
		{
			return false;
		}
		UEdGraphPin* Output = FromNode->FindPin(FName(*FromPin), EGPD_Output);
		UEdGraphPin* Input = ToNode->FindPin(FName(*ToPin), EGPD_Input);
		return Output != nullptr
			&& Input != nullptr
			&& Output->LinkedTo.Contains(Input)
			&& Input->LinkedTo.Contains(Output);
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
		return false;
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
		if (NodeType == TEXT("not_bool"))
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
		{ TEXT("assetPath"), TEXT("graphGuid"), TEXT("nodes"), TEXT("connections") },
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
		{ TEXT("assetPath"), TEXT("graphGuid"), TEXT("nodes"), TEXT("connections"), TEXT("planId"), TEXT("confirmation") },
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
			TEXT("The graph or request changed after Preview; request a fresh preview before Apply."));
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

		for (const FNodeSpec& Spec : Plan.Nodes)
		{
			UEdGraphNode* Node = nullptr;
			if (Spec.NodeType == TEXT("not_bool"))
			{
				UFunction* Function = UKismetMathLibrary::StaticClass()->FindFunctionByName(
					FName(*Spec.FunctionName));
				if (Function == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-spawn-failed"),
						FString::Printf(TEXT("The bool function %s is unavailable."), *Spec.FunctionName));
				}
				UK2Node_CallFunction* CallNode = NewObject<UK2Node_CallFunction>(Graph);
				if (CallNode == nullptr)
				{
					return Abort(
						TEXT("graph-edit-node-spawn-failed"),
						TEXT("Could not allocate the bool CallFunction node."));
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
			if (Node == nullptr)
			{
				return Abort(
					TEXT("graph-edit-node-spawn-failed"),
					FString::Printf(TEXT("The Editor failed to spawn %s."), *Spec.NodeType));
			}
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
			CreatedNodes.Add(Spec.TempId, Node);
			CreatedGuidByTempId.Add(Spec.TempId, Node->NodeGuid);
			CreatedGuids.Add(Node->NodeGuid);
		}

		for (const FConnectionSpec& Connection : Plan.Connections)
		{
			UEdGraphNode** FromNode = CreatedNodes.Find(Connection.From);
			UEdGraphNode** ToNode = CreatedNodes.Find(Connection.To);
			if (FromNode == nullptr || ToNode == nullptr)
			{
				return Abort(
					TEXT("graph-edit-node-reference-unknown"),
					TEXT("A connection referenced a node that was not created."));
			}
			UEdGraphPin* FromPin = (*FromNode)->FindPin(FName(*Connection.FromPin), EGPD_Output);
			UEdGraphPin* ToPin = (*ToNode)->FindPin(FName(*Connection.ToPin), EGPD_Input);
			if (FromPin == nullptr || ToPin == nullptr)
			{
				return Abort(
					TEXT("graph-edit-pin-not-found"),
					FString::Printf(
						TEXT("The spawned nodes do not expose %s -> %s."),
						*Connection.FromPin,
						*Connection.ToPin));
			}
			if (!PinMatchesType(FromPin, Connection.FromType)
				|| !PinMatchesType(ToPin, Connection.ToType))
			{
				return Abort(
					TEXT("graph-edit-pin-type-mismatch"),
					FString::Printf(
						TEXT("The spawned pins do not match the declared types %s -> %s."),
						*Connection.FromType,
						*Connection.ToType));
			}
			if (!Graph->GetSchema()->TryCreateConnection(FromPin, ToPin)
				|| !ArePinsConnected(*FromNode, Connection.FromPin, *ToNode, Connection.ToPin))
			{
				return Abort(
					TEXT("graph-edit-connection-failed"),
					FString::Printf(
						TEXT("The Editor rejected the connection %s.%s -> %s.%s."),
						*Connection.From,
						*Connection.FromPin,
						*Connection.To,
						*Connection.ToPin));
			}
		}

		Blueprint->GetOutermost()->MarkPackageDirty();
	}

	TSharedPtr<FJsonObject> PostPreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		PostPreflight,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!PostPreflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-readback-failed"),
			TEXT("The package did not become dirty after Apply."));
	}
	if (PostPreflight->GetNumberField(TEXT("nodeCount")) != NodeCountBefore + Plan.Nodes.Num())
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-readback-failed"),
			TEXT("The graph node count does not match the previewed node count after Apply."));
	}

	const int32 NodeCountAfter = Graph->Nodes.Num();
	const int32 LinkCountAfter = CountLinks(Graph);
	for (const FGuid& Guid : CreatedGuids)
	{
		if (!HasNodeGuid(Graph, Guid))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("graph-edit-readback-failed"),
				TEXT("At least one previewed node is missing from the live graph after Apply."));
		}
	}

	const bool bUndoCalled = GEditor->UndoTransaction();
	const bool bUndoNodes = Graph->Nodes.Num() == NodeCountBefore;
	const bool bUndoLinks = CountLinks(Graph) == LinkCountBefore;
	bool bUndoGuids = true;
	for (const FGuid& Guid : CreatedGuids)
	{
		bUndoGuids = bUndoGuids && !HasNodeGuid(Graph, Guid);
	}
	const bool bUndoVerified = bUndoCalled && bUndoNodes && bUndoLinks && bUndoGuids;

	const bool bRedoCalled = bUndoVerified && GEditor->RedoTransaction();
	const bool bRedoNodes = Graph->Nodes.Num() == NodeCountAfter;
	const bool bRedoLinks = CountLinks(Graph) == LinkCountAfter;
	bool bRedoGuids = true;
	for (const FGuid& Guid : CreatedGuids)
	{
		bRedoGuids = bRedoGuids && HasNodeGuid(Graph, Guid);
	}
	const bool bRedoVerified = bRedoCalled && bRedoNodes && bRedoLinks && bRedoGuids;
	if (!bUndoVerified || !bRedoVerified)
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
			TEXT("graph-edit-undo-redo-failed"),
			FString::Printf(
				TEXT("Undo/Redo verification failed: undo=%s nodes=%s links=%s guids=%s redo=%s."),
				bUndoCalled ? TEXT("true") : TEXT("false"),
				bUndoNodes ? TEXT("true") : TEXT("false"),
				bUndoLinks ? TEXT("true") : TEXT("false"),
				bUndoGuids ? TEXT("true") : TEXT("false"),
				bRedoVerified ? TEXT("true") : TEXT("false")));
	}

	TSharedPtr<FJsonObject> FinalPreflight;
	if (!UEAgentKitBlueprintGraphLayout::BuildFocusedEditorPreflight(
		FinalPreflight,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	if (!FinalPreflight->GetBoolField(TEXT("packageDirty")))
	{
		return Fail(
			OutErrorCode,
			OutErrorMessage,
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
		Item->SetNumberField(TEXT("x"), Spec.X);
		Item->SetNumberField(TEXT("y"), Spec.Y);
		CreatedNodeValues.Add(MakeShared<FJsonValueObject>(Item));
	}

	TArray<TSharedPtr<FJsonValue>> ConnectionValues;
	for (const FConnectionSpec& Connection : Plan.Connections)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("from"), Connection.From);
		Item->SetStringField(TEXT("fromPin"), Connection.FromPin);
		Item->SetStringField(TEXT("fromType"), Connection.FromType);
		Item->SetStringField(TEXT("to"), Connection.To);
		Item->SetStringField(TEXT("toPin"), Connection.ToPin);
		Item->SetStringField(TEXT("toType"), Connection.ToType);
		ConnectionValues.Add(MakeShared<FJsonValueObject>(Item));
	}

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
	Result->SetNumberField(TEXT("nodeCountBefore"), NodeCountBefore);
	Result->SetNumberField(TEXT("nodeCountAfter"), NodeCountAfter);
	Result->SetNumberField(TEXT("createdNodeCount"), Plan.Nodes.Num());
	Result->SetNumberField(TEXT("createdConnectionCount"), Plan.Connections.Num());
	Result->SetNumberField(TEXT("linkCountBefore"), LinkCountBefore);
	Result->SetNumberField(TEXT("linkCountAfter"), LinkCountAfter);
	Result->SetBoolField(TEXT("packageDirtyAfter"), true);
	Result->SetStringField(TEXT("packageRevision"), FinalPreflight->GetStringField(TEXT("packageRevision")));
	Result->SetStringField(TEXT("graphFingerprintAfter"), FinalPreflight->GetStringField(TEXT("graphFingerprint")));
	Result->SetArrayField(TEXT("createdNodes"), CreatedNodeValues);
	Result->SetArrayField(TEXT("createdConnections"), ConnectionValues);
	OutResult = Result;
	return true;
}
