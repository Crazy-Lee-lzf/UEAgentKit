#pragma once

#include "Components/ActorComponent.h"
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "P0PieScalarFixture.generated.h"

/**
 * Disposable PIE scalar fixture used by the native scalar-inspection acceptance
 * test.  It contains only immediate scalar fields plus deliberately unsupported
 * shapes so the tool can prove per-property rejection without touching any game
 * project content.
 */
UENUM(BlueprintType)
enum class EP0PieScalarMode : uint8
{
	Idle,
	Running,
	Dead
};

UCLASS()
class UEAGENTKITEDITOR_API UP0PieScalarFixtureComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UP0PieScalarFixtureComponent();

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	float Speed = 12.5f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	int32 Charge = 7;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	FString Note;

	/** Unsupported shape: rejected per property, never traversed. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	TArray<int32> Samples;
};

UCLASS()
class UEAGENTKITEDITOR_API AP0PieScalarFixtureActor : public AActor
{
	GENERATED_BODY()

public:
	AP0PieScalarFixtureActor();

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	float Health = 87.5f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	double Precision = 1.25;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	int32 Ammo = 30;

	/** Deliberately beyond 2^53 so int64 exactness is exercised. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	int64 Score = 9007199254740993LL;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	bool bIsDead = false;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	FName StateName = TEXT("Idle");

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	FString Description;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	EP0PieScalarMode Mode = EP0PieScalarMode::Idle;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	TArray<int32> UnsupportedArray;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	TMap<FName, int32> UnsupportedMap;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	TSet<int32> UnsupportedSet;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	FVector UnsupportedStruct;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	FText UnsupportedText;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "P0Pie")
	TObjectPtr<UObject> UnsupportedObject;

	/** Reflected but deliberately not Edit/Blueprint exposed. */
	UPROPERTY()
	int32 NotExposed = 3;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "P0Pie")
	TObjectPtr<UP0PieScalarFixtureComponent> ScalarComponent;

private:
	/** Edit-visible but native-private: the tool must reject it. */
	UPROPERTY(EditAnywhere, Category = "P0Pie")
	int32 PrivateEditable = 11;
};
