#include "StructuredWriteFixtureAsset.h"

UUEAgentKitStructuredWriteFixtureAsset::UUEAgentKitStructuredWriteFixtureAsset()
{
	StructValue.Count = 1;
	StructValue.Label = TEXT("Initial");
	StructValue.bEnabled = true;

	ArrayValue = {1, 2, 3};
	SetValue = {FName(TEXT("Alpha")), FName(TEXT("Beta"))};

	FUEAgentKitStructuredFixtureRecord Primary;
	Primary.Count = 10;
	Primary.Label = TEXT("Primary");
	Primary.bEnabled = true;
	MapValue.Add(FName(TEXT("Primary")), Primary);

	FUEAgentKitStructuredFixtureRecord Secondary;
	Secondary.Count = 20;
	Secondary.Label = TEXT("Secondary");
	Secondary.bEnabled = false;
	MapValue.Add(FName(TEXT("Secondary")), Secondary);
}
