#include "ScalarWriteFixtureAsset.h"

UUEAgentKitScalarWriteFixtureAsset::UUEAgentKitScalarWriteFixtureAsset()
	: BoolValue(false)
	, ByteValue(7)
	, IntValue(-17)
	, Int64Value(1234567890123LL)
	, FloatValue(1.25f)
	, DoubleValue(-2.5)
	, StringValue(TEXT("Initial String"))
	, NameValue(TEXT("InitialName"))
	, TextValue(FText::FromString(TEXT("Initial Text")))
	, EnumValue(EUEAgentKitScalarFixtureMode::Alpha)
	, LegacyEnumValue(UEAK_LegacyAlpha)
{
}
