#pragma once

#include "Components/ActorComponent.h"
#include "Components/StaticMeshComponent.h"
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "PieP2PropertyFixture.generated.h"

/**
 * Disposable PIE fixture for the P2 reflective property reader.
 *
 * Every field below exists to prove one property of the read contract:
 *
 *  - scalars of every supported width,
 *  - reflected structs (Vector / Rotator / Transform) that must be expanded,
 *  - Array / Map / Set that must be paged,
 *  - object references that must return identity only,
 *  - types the tool must refuse instead of fabricating a value
 *    (FText, delegates, bitfield, nested container),
 *  - a property that is reflected but neither Edit nor BlueprintVisible, which
 *    P2 must still read because it is reflection driven.
 *
 * It owns no asset reference and writes nothing, so it stays disposable.
 */
UENUM(BlueprintType)
enum class EPieP2Mode : uint8
{
	Idle,
	Running,
	Dead
};

DECLARE_DYNAMIC_DELEGATE(FPieP2SingleSignature);

DECLARE_DYNAMIC_MULTICAST_DELEGATE(FPieP2MulticastSignature);

UCLASS()
class UEAGENTKITEDITOR_API UPieP2PropertyFixtureComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPieP2PropertyFixtureComponent();

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	float Speed = 12.5f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	TArray<int32> Samples;
};

UCLASS()
class UEAGENTKITEDITOR_API APieP2PropertyFixtureActor : public AActor
{
	GENERATED_BODY()

public:
	APieP2PropertyFixtureActor();

	/** Scalars. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	float Health = 42.5f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	int32 Ammo = 9;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	bool bIsDead = false;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	FName StateName = TEXT("Running");

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	FString Description;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	EPieP2Mode Mode = EPieP2Mode::Running;

	/** Reflected structs that must be expanded member by member. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	FVector VectorValue = FVector(1.5, -2.25, 3.75);

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	FRotator RotatorValue = FRotator(10.0f, 20.0f, 30.0f);

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	FTransform TransformValue;

	/** Containers that must be paged, never dumped whole. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	TArray<int32> IntArray;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	TMap<FName, int32> NameToIntMap;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	TSet<int32> IntSet;

	/** Object references: identity only, never recursion. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	TObjectPtr<UObject> ObjectRefValue;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	TObjectPtr<UStaticMeshComponent> MeshRefValue;

	/** Deliberately unsupported shapes: refused with a reason, never faked. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP2")
	FText TextValue;

	UPROPERTY()
	FPieP2SingleSignature DelegateValue;

	UPROPERTY()
	FPieP2MulticastSignature MulticastValue;

	/** Reflected but neither Edit nor BlueprintVisible: P2 must still read it. */
	UPROPERTY()
	int32 NotExposed = 3;

	/** Bitfield: reflected, but the tool reports it as unsupported instead of guessing. */
	UPROPERTY()
	uint8 bBitfieldFlag : 1;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "PieP2")
	TObjectPtr<UStaticMeshComponent> Mesh;

	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "PieP2")
	TObjectPtr<UPieP2PropertyFixtureComponent> FixtureComponent;

private:
	/** Edit-visible but native-private: P2 reads it because it is reflected. */
	UPROPERTY(EditAnywhere, Category = "PieP2")
	int32 PrivateEditable = 11;
};
