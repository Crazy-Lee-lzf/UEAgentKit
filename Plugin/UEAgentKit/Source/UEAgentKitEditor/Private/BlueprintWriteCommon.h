#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

class UBlueprint;
class UEdGraphPin;
class FProperty;
class UObject;

namespace UEAgentKitBlueprintWrite
{
	struct FResolvedBlueprintTarget
	{
		enum class EKind : uint8
		{
			Property,
			Pin
		};

		EKind Kind = EKind::Property;
		UObject* OwnerObject = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		UEdGraphPin* Pin = nullptr;
		FString TypeName;
		FString Description;
	};

	bool CompileBlueprint(UBlueprint* Blueprint, FString& OutError);
	bool ResolvePropertyPath(
		UObject* OwnerObject,
		const FString& PropertyPath,
		FProperty*& OutProperty,
		void*& OutValueAddress,
		FString& OutError);
	bool JsonValueToPinDefault(
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutDefaultValue,
		FString& OutError);
	bool ResolveBlueprintTarget(
		UBlueprint* Blueprint,
		const FString& Operation,
		const TSharedPtr<FJsonObject>& TargetObject,
		FResolvedBlueprintTarget& OutTarget,
		FString& OutError);
	FString DescribeTarget(const FResolvedBlueprintTarget& Target);
}