#include "NativeLayoutBrokerPlanId.h"

#include "BlueprintContextSha256.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
	void AppendJsonString(FString& Out, const FString& Value)
	{
		Out.AppendChar(TEXT('"'));
		for (const TCHAR Character : Value)
		{
			switch (Character)
			{
			case TEXT('"'):
				Out += TEXT("\\\"");
				break;
			case TEXT('\\'):
				Out += TEXT("\\\\");
				break;
			case TEXT('\b'):
				Out += TEXT("\\b");
				break;
			case TEXT('\f'):
				Out += TEXT("\\f");
				break;
			case TEXT('\n'):
				Out += TEXT("\\n");
				break;
			case TEXT('\r'):
				Out += TEXT("\\r");
				break;
			case TEXT('\t'):
				Out += TEXT("\\t");
				break;
			default:
				if (Character < 0x20)
				{
					Out += FString::Printf(TEXT("\\u%04x"), static_cast<uint32>(Character));
				}
				else
				{
					Out.AppendChar(Character);
				}
				break;
			}
		}
		Out.AppendChar(TEXT('"'));
	}

	bool TryGetExactInt(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, int32& Out)
	{
		double Number = 0.0;
		if (!Object.IsValid() || !Object->TryGetNumberField(Field, Number))
		{
			return false;
		}
		const double Rounded = FMath::RoundToDouble(Number);
		if (!FMath::IsNearlyEqual(Number, Rounded))
		{
			return false;
		}
		Out = static_cast<int32>(Rounded);
		return true;
	}

	bool BuildProjection(
		const TSharedPtr<FJsonObject>& Result,
		int32 SpacingX,
		int32 SpacingY,
		int32 TargetWidth,
		int32 Seed,
		FString& OutCanonical,
		FString& OutError)
	{
		if (!Result.IsValid())
		{
			OutError = TEXT("The preview result is not a JSON object.");
			return false;
		}
		FString BaseGraphHash;
		if (!Result->TryGetStringField(TEXT("baseGraphHash"), BaseGraphHash) || BaseGraphHash.IsEmpty())
		{
			OutError = TEXT("The result has no baseGraphHash.");
			return false;
		}
		const TSharedPtr<FJsonObject>* PlanPtr = nullptr;
		if (!Result->TryGetObjectField(TEXT("plan"), PlanPtr) || PlanPtr == nullptr || !PlanPtr->IsValid())
		{
			OutError = TEXT("The result has no plan object.");
			return false;
		}
		const TSharedPtr<FJsonObject> Plan = *PlanPtr;
		FString Mode;
		if (!Plan->TryGetStringField(TEXT("mode"), Mode) || Mode.IsEmpty())
		{
			OutError = TEXT("The plan has no mode.");
			return false;
		}
		int32 PlanSeed = 0;
		if (!TryGetExactInt(Plan, TEXT("seed"), PlanSeed) || PlanSeed != Seed)
		{
			OutError = TEXT("The plan seed does not match the request seed.");
			return false;
		}
		const TSharedPtr<FJsonObject>* PackingPtr = nullptr;
		if (!Plan->TryGetObjectField(TEXT("packing"), PackingPtr) || PackingPtr == nullptr || !PackingPtr->IsValid())
		{
			OutError = TEXT("The plan has no packing object.");
			return false;
		}
		const TSharedPtr<FJsonObject> Packing = *PackingPtr;
		int32 PackingSpacing = 0;
		int32 PackingTargetWidth = 0;
		FString PackingAlgorithm;
		if (!TryGetExactInt(Packing, TEXT("spacing"), PackingSpacing)
			|| !TryGetExactInt(Packing, TEXT("targetWidth"), PackingTargetWidth)
			|| !Packing->TryGetStringField(TEXT("algorithm"), PackingAlgorithm)
			|| PackingSpacing != SpacingX
			|| PackingTargetWidth != TargetWidth
			|| PackingAlgorithm != TEXT("skyline-v1")
			|| Packing->Values.Num() != 3)
		{
			OutError = TEXT("The plan packing does not match the request options.");
			return false;
		}
		const TArray<TSharedPtr<FJsonValue>>* PositionsPtr = nullptr;
		if (!Plan->TryGetArrayField(TEXT("positions"), PositionsPtr) || PositionsPtr == nullptr)
		{
			OutError = TEXT("The plan has no positions array.");
			return false;
		}

		FString Canonical;
		Canonical += TEXT("{\"baseGraphHash\":");
		AppendJsonString(Canonical, BaseGraphHash);
		Canonical += TEXT(",\"mode\":");
		AppendJsonString(Canonical, Mode);
		Canonical += TEXT(",\"options\":{\"seed\":");
		Canonical += FString::FromInt(Seed);
		Canonical += TEXT(",\"spacingX\":");
		Canonical += FString::FromInt(SpacingX);
		Canonical += TEXT(",\"spacingY\":");
		Canonical += FString::FromInt(SpacingY);
		Canonical += TEXT("},\"packing\":{\"algorithm\":\"skyline-v1\",\"spacing\":");
		Canonical += FString::FromInt(SpacingX);
		Canonical += TEXT(",\"targetWidth\":");
		Canonical += FString::FromInt(TargetWidth);
		Canonical += TEXT("},\"positions\":[");
		for (int32 Index = 0; Index < PositionsPtr->Num(); ++Index)
		{
			const TSharedPtr<FJsonValue>& Entry = (*PositionsPtr)[Index];
			if (!Entry.IsValid() || Entry->Type != EJson::Object)
			{
				OutError = TEXT("A plan position is not an object.");
				return false;
			}
			const TSharedPtr<FJsonObject> Position = Entry->AsObject();
			FString NodeId;
			int32 X = 0;
			int32 Y = 0;
			if (!Position.IsValid()
				|| !Position->TryGetStringField(TEXT("nodeId"), NodeId)
				|| NodeId.IsEmpty()
				|| !TryGetExactInt(Position, TEXT("x"), X)
				|| !TryGetExactInt(Position, TEXT("y"), Y))
			{
				OutError = TEXT("A plan position has an invalid shape.");
				return false;
			}
			if (Index > 0)
			{
				Canonical.AppendChar(TEXT(','));
			}
			Canonical += TEXT("{\"nodeId\":");
			AppendJsonString(Canonical, NodeId);
			Canonical += TEXT(",\"x\":");
			Canonical += FString::FromInt(X);
			Canonical += TEXT(",\"y\":");
			Canonical += FString::FromInt(Y);
			Canonical.AppendChar(TEXT('}'));
		}
		Canonical += TEXT("]}");
		OutCanonical = MoveTemp(Canonical);
		return true;
	}
}

bool FUEAgentKitLayoutBrokerPlanId::Compute(
	const TSharedPtr<FJsonObject>& Result,
	int32 SpacingX,
	int32 SpacingY,
	int32 TargetWidth,
	int32 Seed,
	FString& OutPlanId,
	FString& OutError)
{
	FString Canonical;
	if (!BuildProjection(Result, SpacingX, SpacingY, TargetWidth, Seed, Canonical, OutError))
	{
		return false;
	}
	const FString Payload = FString(TEXT("ueak.layoutplan/1\n")) + Canonical;
	FString Digest;
	if (!FBlueprintContextSha256::HashText(Payload, Digest) || Digest.Len() < 16)
	{
		OutError = TEXT("The canonical planId hash could not be computed.");
		return false;
	}
	OutPlanId = FString(TEXT("layout:")) + Digest.Left(16).ToLower();
	return true;
}

bool FUEAgentKitLayoutBrokerPlanId::Validate(
	const TSharedPtr<FJsonObject>& Result,
	int32 SpacingX,
	int32 SpacingY,
	int32 TargetWidth,
	int32 Seed,
	FString& OutError)
{
	FString Expected;
	if (!Compute(Result, SpacingX, SpacingY, TargetWidth, Seed, Expected, OutError))
	{
		return false;
	}
	FString Actual;
	if (!Result.IsValid() || !Result->TryGetStringField(TEXT("planId"), Actual) || Actual != Expected)
	{
		OutError = TEXT("The layout planId is not canonical for the request options.");
		return false;
	}
	return true;
}
