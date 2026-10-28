#include "P0PieScalarFixture.h"

#include "Components/SceneComponent.h"

UP0PieScalarFixtureComponent::UP0PieScalarFixtureComponent()
{
	PrimaryComponentTick.bCanEverTick = false;
}

AP0PieScalarFixtureActor::AP0PieScalarFixtureActor()
{
	PrimaryActorTick.bCanEverTick = false;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	ScalarComponent = CreateDefaultSubobject<UP0PieScalarFixtureComponent>(TEXT("ScalarComponent"));
}
