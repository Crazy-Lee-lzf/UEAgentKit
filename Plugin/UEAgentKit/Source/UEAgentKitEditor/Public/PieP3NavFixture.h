#pragma once

#include "Components/ActorComponent.h"
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/Object.h"
#include "PieP3NavFixture.generated.h"

/**
 * Disposable PIE fixture for the P3 object-navigation contract.
 *
 * It exists to make a three-hop chain real and cheap:
 *
 *   Actor --(NavComponent)--> Component --(Payload)--> UObject
 *
 * The payload is a plain UObject with ordinary reflected scalars, so the third
 * hop is read by exactly the same reflective reader the first two hops use.
 * Nothing here references a project asset and nothing writes.
 */
UCLASS()
class UEAGENTKITEDITOR_API UPieP3PayloadObject : public UObject
{
	GENERATED_BODY()

public:
	UPROPERTY()
	FString Label = TEXT("payload");

	UPROPERTY()
	int32 Count = 33;

	UPROPERTY()
	float Ratio = 0.75f;

	UPROPERTY()
	bool bFlag = true;

	/** Optional second payload, so a caller can walk further if it wants to. */
	UPROPERTY()
	TObjectPtr<UPieP3PayloadObject> Next;
};

UCLASS()
class UEAGENTKITEDITOR_API UPieP3NavFixtureComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPieP3NavFixtureComponent();

	/**
	 * Hop 2 of the chain: a component-owned reference to a plain UObject.  Typed
	 * as UObject on purpose, so the reference is an ordinary object reference and
	 * not a class the reader could special-case.
	 */
	UPROPERTY()
	TObjectPtr<UObject> Payload;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP3")
	float Gauge = 12.5f;
};

UCLASS()
class UEAGENTKITEDITOR_API APieP3NavFixtureActor : public AActor
{
	GENERATED_BODY()

public:
	APieP3NavFixtureActor();

	/** Hop 1 of the chain: the Actor-owned component. */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "PieP3")
	TObjectPtr<UPieP3NavFixtureComponent> NavComponent;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "PieP3")
	float Health = 64.0f;
};
