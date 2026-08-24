#include "BlueprintWriteCommon.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "UObject/UnrealType.h"

namespace UEAgentKitBlueprintWrite
{
	bool CompileBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		if (Blueprint == nullptr)
		{
			OutError = TEXT("Blueprint is null.");
			return false;
		}

		// Test-only compile-failure seam. It is off by default and is used by
		// acceptance to exercise the exact compile-failure recovery path without
		// exposing a public MCP tool or destabilizing real Blueprint graphs.
		static bool bForceCompileFailureOnce =
			FPlatformMisc::GetEnvironmentVariable(TEXT("UEAK_TEST_FORCE_COMPILE_FAILURE_ONCE")) == TEXT("1");
		static const bool bForceCompileFailureAlways =
			FPlatformMisc::GetEnvironmentVariable(TEXT("UEAK_TEST_FORCE_COMPILE_FAILURE_ALWAYS")) == TEXT("1");
		if (bForceCompileFailureAlways || bForceCompileFailureOnce)
		{
			if (bForceCompileFailureOnce)
			{
				bForceCompileFailureOnce = false;
			}
			OutError = TEXT("Test-only forced Blueprint compile failure (UEAK_TEST_FORCE_COMPILE_FAILURE_ONCE/ALWAYS).");
			return false;
		}

		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("Blueprint compilation failed.");
			return false;
		}
		return true;
	}

	bool ResolvePropertyPath(
		UObject* OwnerObject,
		const FString& PropertyPath,
		FProperty*& OutProperty,
		void*& OutValueAddress,
		FString& OutError)
	{
		if (!OwnerObject || PropertyPath.IsEmpty())
		{
			OutError = TEXT("Property owner or property path is invalid.");
			return false;
		}

		TArray<FString> Segments;
		PropertyPath.ParseIntoArray(Segments, TEXT("."), true);
		if (Segments.IsEmpty())
		{
			OutError = TEXT("Property path is empty.");
			return false;
		}

		UStruct* CurrentStruct = OwnerObject->GetClass();
		void* CurrentContainer = OwnerObject;
		for (int32 Index = 0; Index < Segments.Num(); ++Index)
		{
			FProperty* Property = FindFProperty<FProperty>(CurrentStruct, FName(*Segments[Index]));
			if (!Property)
			{
				OutError = FString::Printf(
					TEXT("Property path segment was not found: %s"),
					*Segments[Index]);
				return false;
			}

			void* ValueAddress = Property->ContainerPtrToValuePtr<void>(CurrentContainer);
			if (Index == Segments.Num() - 1)
			{
				OutProperty = Property;
				OutValueAddress = ValueAddress;
				return true;
			}

			FStructProperty* StructProperty = CastField<FStructProperty>(Property);
			if (!StructProperty)
			{
				OutError = FString::Printf(
					TEXT("Intermediate property is not a struct: %s"),
					*Segments[Index]);
				return false;
			}
			CurrentStruct = StructProperty->Struct;
			CurrentContainer = ValueAddress;
		}
		return false;
	}

	bool JsonValueToPinDefault(
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutDefaultValue,
		FString& OutError)
	{
		if (!JsonValue.IsValid())
		{
			OutError = TEXT("Pin value is missing.");
			return false;
		}

		if (JsonValue->Type == EJson::Boolean)
		{
			bool Value = false;
			JsonValue->TryGetBool(Value);
			OutDefaultValue = Value ? TEXT("true") : TEXT("false");
			return true;
		}
		if (JsonValue->Type == EJson::Number)
		{
			double Value = 0.0;
			if (!JsonValue->TryGetNumber(Value) || !FMath::IsFinite(Value))
			{
				OutError = TEXT("Expected a finite pin number.");
				return false;
			}
			OutDefaultValue = FString::Printf(TEXT("%.17g"), Value);
			return true;
		}
		if (JsonValue->Type == EJson::String)
		{
			return JsonValue->TryGetString(OutDefaultValue);
		}

		OutError = TEXT("Pin defaults currently support JSON boolean, number, and string values.");
		return false;
	}

	USCS_Node* FindSCSNode(UBlueprint* Blueprint, const FString& ComponentName)
	{
		if (!Blueprint || !Blueprint->SimpleConstructionScript)
		{
			return nullptr;
		}

		for (USCS_Node* Node : Blueprint->SimpleConstructionScript->GetAllNodes())
		{
			if (Node && Node->GetVariableName().ToString().Equals(ComponentName, ESearchCase::CaseSensitive))
			{
				return Node;
			}
		}
		return nullptr;
	}

	UEdGraphPin* FindGraphPin(
		UBlueprint* Blueprint,
		const FString& GraphGuidText,
		const FString& NodeGuidText,
		const FString& PinName,
		FString& OutError)
	{
		FGuid GraphGuid;
		FGuid NodeGuid;
		if (!FGuid::Parse(GraphGuidText, GraphGuid) || !FGuid::Parse(NodeGuidText, NodeGuid))
		{
			OutError = TEXT("graphGuid or nodeGuid is invalid.");
			return nullptr;
		}

		TArray<UEdGraph*> Graphs;
		Blueprint->GetAllGraphs(Graphs);
		for (UEdGraph* Graph : Graphs)
		{
			if (!Graph || Graph->GraphGuid != GraphGuid)
			{
				continue;
			}

			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (!Node || Node->NodeGuid != NodeGuid)
				{
					continue;
				}

				for (UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin && Pin->PinName.ToString().Equals(PinName, ESearchCase::CaseSensitive))
					{
						return Pin;
					}
				}
				OutError = FString::Printf(TEXT("Pin was not found: %s"), *PinName);
				return nullptr;
			}
			OutError = FString::Printf(TEXT("Node was not found: %s"), *NodeGuidText);
			return nullptr;
		}

		OutError = FString::Printf(TEXT("Graph was not found: %s"), *GraphGuidText);
		return nullptr;
	}

	bool ResolveBlueprintTarget(
		UBlueprint* Blueprint,
		const FString& Operation,
		const TSharedPtr<FJsonObject>& TargetObject,
		FResolvedBlueprintTarget& OutTarget,
		FString& OutError)
	{
		if (!Blueprint || !TargetObject.IsValid())
		{
			OutError = TEXT("Blueprint or operation target is invalid.");
			return false;
		}

		if (Operation.Equals(TEXT("setVariableDefault"), ESearchCase::CaseSensitive))
		{
			FString VariableName;
			TargetObject->TryGetStringField(TEXT("variableName"), VariableName);
			FProperty* Property = FindFProperty<FProperty>(Blueprint->GeneratedClass, FName(*VariableName));
			UObject* DefaultObject = Blueprint->GeneratedClass->GetDefaultObject();
			if (!Property || !DefaultObject)
			{
				OutError = FString::Printf(TEXT("Variable was not found: %s"), *VariableName);
				return false;
			}
			if (!FBlueprintEditorUtils::IsVariableCreatedByBlueprint(Blueprint, Property))
			{
				OutError = TEXT("Only variables declared by the target Blueprint can be changed.");
				return false;
			}

			OutTarget.Kind = FResolvedBlueprintTarget::EKind::Property;
			OutTarget.OwnerObject = DefaultObject;
			OutTarget.Property = Property;
			OutTarget.ValueAddress = Property->ContainerPtrToValuePtr<void>(DefaultObject);
			OutTarget.TypeName = Property->GetClass()->GetName();
			OutTarget.Description = TEXT("variable:") + VariableName;
			return true;
		}

		if (Operation.Equals(TEXT("setComponentProperty"), ESearchCase::CaseSensitive))
		{
			FString ComponentName;
			FString PropertyPath;
			TargetObject->TryGetStringField(TEXT("componentName"), ComponentName);
			TargetObject->TryGetStringField(TEXT("propertyPath"), PropertyPath);

			USCS_Node* Node = FindSCSNode(Blueprint, ComponentName);
			if (!Node || !Node->ComponentTemplate)
			{
				OutError = FString::Printf(TEXT("SCS component was not found: %s"), *ComponentName);
				return false;
			}

			FProperty* Property = nullptr;
			void* ValueAddress = nullptr;
			if (!ResolvePropertyPath(Node->ComponentTemplate, PropertyPath, Property, ValueAddress, OutError))
			{
				return false;
			}

			OutTarget.Kind = FResolvedBlueprintTarget::EKind::Property;
			OutTarget.OwnerObject = Node->ComponentTemplate;
			OutTarget.Property = Property;
			OutTarget.ValueAddress = ValueAddress;
			OutTarget.TypeName = Property->GetClass()->GetName();
			OutTarget.Description = FString::Printf(
				TEXT("component:%s.%s"),
				*ComponentName,
				*PropertyPath);
			return true;
		}

		if (Operation.Equals(TEXT("setPinDefault"), ESearchCase::CaseSensitive))
		{
			FString GraphGuid;
			FString NodeGuid;
			FString PinName;
			TargetObject->TryGetStringField(TEXT("graphGuid"), GraphGuid);
			TargetObject->TryGetStringField(TEXT("nodeGuid"), NodeGuid);
			TargetObject->TryGetStringField(TEXT("pinName"), PinName);

			UEdGraphPin* Pin = FindGraphPin(Blueprint, GraphGuid, NodeGuid, PinName, OutError);
			if (!Pin)
			{
				return false;
			}
			if (Pin->Direction != EGPD_Input)
			{
				OutError = TEXT("Only input pin defaults can be changed.");
				return false;
			}
			if (!Pin->LinkedTo.IsEmpty())
			{
				OutError = TEXT("Connected pins cannot receive a default value.");
				return false;
			}
			if (Pin->bDefaultValueIsReadOnly || Pin->bDefaultValueIsIgnored)
			{
				OutError = TEXT("The target pin does not accept an editable default value.");
				return false;
			}

			OutTarget.Kind = FResolvedBlueprintTarget::EKind::Pin;
			OutTarget.Pin = Pin;
			OutTarget.TypeName = Pin->PinType.PinCategory.ToString();
			OutTarget.Description = FString::Printf(
				TEXT("pin:%s/%s/%s"),
				*GraphGuid,
				*NodeGuid,
				*PinName);
			return true;
		}

		OutError = FString::Printf(TEXT("Unsupported Blueprint live operation: %s"), *Operation);
		return false;
	}

	FString DescribeTarget(const FResolvedBlueprintTarget& Target)
	{
		return Target.Description;
	}
}