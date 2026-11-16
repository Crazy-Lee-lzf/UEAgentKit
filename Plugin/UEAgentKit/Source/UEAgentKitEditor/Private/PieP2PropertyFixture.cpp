#include "PieP2PropertyFixture.h"

#include "Components/SceneComponent.h"

UPieP2PropertyFixtureComponent::UPieP2PropertyFixtureComponent()
{
	PrimaryComponentTick.bCanEverTick = false;
	Samples = { 1, 2, 3 };
}

APieP2PropertyFixtureActor::APieP2PropertyFixtureActor()
{
	PrimaryActorTick.bCanEverTick = false;

	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	Mesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Mesh"));
	if (Mesh != nullptr)
	{
		Mesh->SetupAttachment(RootComponent);
	}
	FixtureComponent = CreateDefaultSubobject<UPieP2PropertyFixtureComponent>(TEXT("FixtureComponent"));

	// Deterministic content so the native test can assert exact values and
	// exact container paging without touching any project asset.
	IntArray = { 10, 20, 30, 40, 50, 60, 70 };
	NameToIntMap.Add(TEXT("Alpha"), 1);
	NameToIntMap.Add(TEXT("Beta"), 2);
	NameToIntMap.Add(TEXT("Gamma"), 3);
	IntSet = { 5, 15, 25, 35 };
	Description = TEXT("PieP2 fixture");
	TextValue = FText::FromString(TEXT("read me"));
	TransformValue = FTransform(FRotator(0.0, 90.0, 0.0), FVector(100.0, 200.0, 300.0), FVector(2.0, 2.0, 2.0));
	bBitfieldFlag = 1;
}
