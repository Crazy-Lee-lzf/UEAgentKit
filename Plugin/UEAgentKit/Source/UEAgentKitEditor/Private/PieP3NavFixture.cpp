#include "PieP3NavFixture.h"

#include "Components/SceneComponent.h"

UPieP3NavFixtureComponent::UPieP3NavFixtureComponent()
{
	PrimaryComponentTick.bCanEverTick = false;
}

APieP3NavFixtureActor::APieP3NavFixtureActor()
{
	PrimaryActorTick.bCanEverTick = false;

	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	NavComponent = CreateDefaultSubobject<UPieP3NavFixtureComponent>(TEXT("NavComponent"));
}
