#include "LiveWriteOperationCommon.h"

#include "Editor.h"
#include "Editor/Transactor.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

namespace UEAgentKitLiveWrite
{
bool IsUnsignedIntegerProperty(const FNumericProperty* Property)
{
	return Property != nullptr && (
		Property->IsA<FByteProperty>()
		|| Property->IsA<FUInt16Property>()
		|| Property->IsA<FUInt32Property>()
		|| Property->IsA<FUInt64Property>());
}

bool IsSafeTopLevelPropertyPath(const FString& PropertyPath)
{
	if (PropertyPath.IsEmpty() || PropertyPath.Len() > 128 || PropertyPath.Contains(TEXT(".")))
	{
		return false;
	}
	for (const TCHAR Character : PropertyPath)
	{
		if (!(FChar::IsAlnum(Character) || Character == TEXT('_')))
		{
			return false;
		}
	}
	return true;
}

bool ReadScalarValue(FProperty* Property, const void* ValueAddress, TSharedPtr<FJsonValue>& OutValue)
{
	if (const FBoolProperty* BoolProperty = CastField<FBoolProperty>(Property))
	{
		OutValue = MakeShared<FJsonValueBoolean>(BoolProperty->GetPropertyValue(ValueAddress));
		return true;
	}
	if (const FEnumProperty* EnumProperty = CastField<FEnumProperty>(Property))
	{
		const int64 Value = EnumProperty->GetUnderlyingProperty()->GetSignedIntPropertyValue(ValueAddress);
		OutValue = MakeShared<FJsonValueString>(EnumProperty->GetEnum()->GetNameStringByValue(Value));
		return true;
	}
	if (const FNumericProperty* NumericProperty = CastField<FNumericProperty>(Property))
	{
		if (const UEnum* Enum = NumericProperty->GetIntPropertyEnum())
		{
			OutValue = MakeShared<FJsonValueString>(Enum->GetNameStringByValue(NumericProperty->GetSignedIntPropertyValue(ValueAddress)));
			return true;
		}
		if (NumericProperty->IsFloatingPoint())
		{
			OutValue = MakeShared<FJsonValueNumber>(NumericProperty->GetFloatingPointPropertyValue(ValueAddress));
		}
		else if (IsUnsignedIntegerProperty(NumericProperty))
		{
			OutValue = MakeShared<FJsonValueNumber>(static_cast<double>(NumericProperty->GetUnsignedIntPropertyValue(ValueAddress)));
		}
		else
		{
			OutValue = MakeShared<FJsonValueNumber>(static_cast<double>(NumericProperty->GetSignedIntPropertyValue(ValueAddress)));
		}
		return true;
	}
	if (const FStrProperty* StringProperty = CastField<FStrProperty>(Property))
	{
		OutValue = MakeShared<FJsonValueString>(StringProperty->GetPropertyValue(ValueAddress));
		return true;
	}
	if (const FNameProperty* NameProperty = CastField<FNameProperty>(Property))
	{
		OutValue = MakeShared<FJsonValueString>(NameProperty->GetPropertyValue(ValueAddress).ToString());
		return true;
	}
	if (const FTextProperty* TextProperty = CastField<FTextProperty>(Property))
	{
		OutValue = MakeShared<FJsonValueString>(TextProperty->GetPropertyValue(ValueAddress).ToString());
		return true;
	}
	return false;
}

bool SetScalarValue(
	FProperty* Property,
	void* ValueAddress,
	const TSharedPtr<FJsonValue>& JsonValue,
	FString& OutError)
{
	if (!JsonValue.IsValid())
	{
		OutError = TEXT("value is required.");
		return false;
	}
	if (FBoolProperty* BoolProperty = CastField<FBoolProperty>(Property))
	{
		bool Value = false;
		if (!JsonValue->TryGetBool(Value))
		{
			OutError = TEXT("The property requires a JSON boolean.");
			return false;
		}
		BoolProperty->SetPropertyValue(ValueAddress, Value);
		return true;
	}
	if (FEnumProperty* EnumProperty = CastField<FEnumProperty>(Property))
	{
		FString Value;
		if (!JsonValue->TryGetString(Value))
		{
			OutError = TEXT("The enum property requires a JSON string.");
			return false;
		}
		const int64 EnumValue = EnumProperty->GetEnum()->GetValueByNameString(Value);
		if (EnumValue == INDEX_NONE)
		{
			OutError = TEXT("The enum value is unknown.");
			return false;
		}
		EnumProperty->GetUnderlyingProperty()->SetIntPropertyValue(ValueAddress, EnumValue);
		return true;
	}
	if (FNumericProperty* NumericProperty = CastField<FNumericProperty>(Property))
	{
		if (UEnum* Enum = NumericProperty->GetIntPropertyEnum())
		{
			FString Value;
			if (!JsonValue->TryGetString(Value))
			{
				OutError = TEXT("The enum-backed property requires a JSON string.");
				return false;
			}
			const int64 EnumValue = Enum->GetValueByNameString(Value);
			if (EnumValue == INDEX_NONE || !NumericProperty->CanHoldValue(EnumValue))
			{
				OutError = TEXT("The enum value is unknown or outside the property range.");
				return false;
			}
			NumericProperty->SetIntPropertyValue(ValueAddress, EnumValue);
			return true;
		}
		double Value = 0.0;
		if (!JsonValue->TryGetNumber(Value) || !FMath::IsFinite(Value))
		{
			OutError = TEXT("The numeric property requires a finite JSON number.");
			return false;
		}
		if (NumericProperty->IsFloatingPoint())
		{
			if (!NumericProperty->CanHoldValue(Value))
			{
				OutError = TEXT("The floating-point value is outside the property range.");
				return false;
			}
			NumericProperty->SetFloatingPointPropertyValue(ValueAddress, Value);
			return true;
		}
		if (Value != FMath::TruncToDouble(Value))
		{
			OutError = TEXT("The integer property requires a whole JSON number.");
			return false;
		}
		if (IsUnsignedIntegerProperty(NumericProperty))
		{
			if (Value < 0.0 || Value >= 18446744073709551616.0)
			{
				OutError = TEXT("The unsigned integer value is outside the property range.");
				return false;
			}
			const uint64 IntegerValue = static_cast<uint64>(Value);
			if (!NumericProperty->CanHoldValue(IntegerValue))
			{
				OutError = TEXT("The unsigned integer value is outside the property range.");
				return false;
			}
			NumericProperty->SetIntPropertyValue(ValueAddress, IntegerValue);
			return true;
		}
		if (Value < -9223372036854775808.0 || Value >= 9223372036854775808.0)
		{
			OutError = TEXT("The signed integer value is outside the property range.");
			return false;
		}
		const int64 IntegerValue = static_cast<int64>(Value);
		if (!NumericProperty->CanHoldValue(IntegerValue))
		{
			OutError = TEXT("The signed integer value is outside the property range.");
			return false;
		}
		NumericProperty->SetIntPropertyValue(ValueAddress, IntegerValue);
		return true;
	}
	FString Value;
	if (!JsonValue->TryGetString(Value))
	{
		OutError = TEXT("The property requires a JSON string.");
		return false;
	}
	if (FStrProperty* StringProperty = CastField<FStrProperty>(Property))
	{
		StringProperty->SetPropertyValue(ValueAddress, Value);
		return true;
	}
	if (FNameProperty* NameProperty = CastField<FNameProperty>(Property))
	{
		NameProperty->SetPropertyValue(ValueAddress, FName(*Value));
		return true;
	}
	if (FTextProperty* TextProperty = CastField<FTextProperty>(Property))
	{
		TextProperty->SetPropertyValue(ValueAddress, FText::FromString(Value));
		return true;
	}
	OutError = FString::Printf(TEXT("Unsupported live scalar property type: %s"), *Property->GetClass()->GetName());
	return false;
}

	bool IsSafeLiveWriteSelector(const FString& Selector)
	{
		if (Selector.IsEmpty() || Selector.Len() > 256 || Selector.Contains(TEXT(".")))
		{
			return false;
		}
		for (const TCHAR Character : Selector)
		{
			if (Character < TEXT(' '))
			{
				return false;
			}
		}
		return true;
	}

TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord> BuildLiveWriteTransactionRecord(
	UObject* Asset,
	UPackage* Package,
	const FString& AssetPath,
	const FString& Operation,
	const FString& ValueKind,
	const FString& SessionId,
	UEAgentKitLiveWrite::FLiveWriteEvidence& Evidence,
	TUniquePtr<UEAgentKitLiveWrite::ILiveWriteValueIO>& IO)
{
	if (!Evidence.bChanged || GEditor == nullptr || GEditor->Trans == nullptr)
	{
		return nullptr;
	}
	const FTransactionContext UndoContext = GEditor->Trans->GetUndoContext(false);
	if (!UndoContext.TransactionId.IsValid() || UndoContext.PrimaryObject != Asset)
	{
		return nullptr;
	}
	TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord> Record =
		MakeShared<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>();
	Record->SessionId = SessionId;
	Record->PackageName = Package->GetName();
	Record->AssetPath = AssetPath;
	Record->ClassPath = Asset->GetClass()->GetPathName();
	Record->Operation = Operation;
	Record->ValueKind = ValueKind;
	Record->TransactionTitle = Evidence.TransactionTitle;
	Record->TransactionId = UndoContext.TransactionId;
	Record->bDirtyBefore = Evidence.bPackageDirtyBefore;
	Record->bDirtyAfter = Evidence.bPackageDirtyAfter;
	Record->Asset = Asset;
	Record->BeforeValue = Evidence.BeforeValue;
	Record->AfterValue = Evidence.AfterValue;
	Record->IO = MoveTemp(IO);
	Evidence.TransactionId = UndoContext.TransactionId.ToString(EGuidFormats::DigitsWithHyphens);
	return Record;
}

}
