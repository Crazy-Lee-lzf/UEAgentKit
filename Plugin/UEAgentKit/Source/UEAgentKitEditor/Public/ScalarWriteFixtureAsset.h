#pragma once

#include "Engine/DataAsset.h"
#include "ScalarWriteFixtureAsset.generated.h"

UENUM()
enum class EUEAgentKitScalarFixtureMode : uint8
{
	Alpha,
	Beta,
	Gamma
};

UENUM()
enum EUEAgentKitLegacyScalarFixtureMode : uint8
{
	UEAK_LegacyAlpha,
	UEAK_LegacyBeta,
	UEAK_LegacyGamma
};

UCLASS(BlueprintType)
class UEAGENTKITEDITOR_API UUEAgentKitScalarWriteFixtureAsset : public UDataAsset
{
	GENERATED_BODY()

public:
	UUEAgentKitScalarWriteFixtureAsset();

	UPROPERTY(EditAnywhere, Category = "Scalar")
	bool BoolValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	uint8 ByteValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	int32 IntValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	int64 Int64Value;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	float FloatValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	double DoubleValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	FString StringValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	FName NameValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	FText TextValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	EUEAgentKitScalarFixtureMode EnumValue;

	UPROPERTY(EditAnywhere, Category = "Scalar")
	TEnumAsByte<EUEAgentKitLegacyScalarFixtureMode> LegacyEnumValue;
};
