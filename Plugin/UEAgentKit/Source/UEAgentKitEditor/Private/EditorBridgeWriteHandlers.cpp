#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace
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
}

bool FUEAgentKitEditorBridge::TryApplyAssetPropertyLiveResult(
	const FString& AssetPath,
	const FString& PropertyPath,
	const TSharedPtr<FJsonValue>& Value,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (GEditor == nullptr)
	{
		OutErrorCode = TEXT("live-editor-unavailable");
		OutErrorMessage = TEXT("The Unreal Editor is unavailable.");
		return false;
	}
	if (GEditor->PlayWorld != nullptr)
	{
		OutErrorCode = TEXT("live-editor-pie-active");
		OutErrorMessage = TEXT("Live writes are unavailable while PIE or SIE is active.");
		return false;
	}
	if (!IsSafeGameAssetPath(AssetPath) || !IsSafeTopLevelPropertyPath(PropertyPath))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("assetPath and one top-level propertyPath are required.");
		return false;
	}
	UObject* Asset = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	if (Asset == nullptr || !Asset->IsAsset())
	{
		OutErrorCode = TEXT("live-editor-write-asset-not-loaded");
		OutErrorMessage = TEXT("Live write accepts only an already loaded exact asset.");
		return false;
	}
	if (!IsAssetOpenInEditor(Asset))
	{
		OutErrorCode = TEXT("live-editor-write-asset-not-open");
		OutErrorMessage = TEXT("Open the exact asset in the Editor before applying a live write.");
		return false;
	}
	if (Asset->IsA<UBlueprint>())
	{
		OutErrorCode = TEXT("live-editor-write-blueprint-unsupported");
		OutErrorMessage = TEXT("The first Live Write capability accepts only non-Blueprint assets.");
		return false;
	}
	UPackage* Package = Asset->GetOutermost();
	if (Package == nullptr || !Package->GetName().StartsWith(TEXT("/Game/")) || Package->ContainsMap())
	{
		OutErrorCode = TEXT("live-editor-write-package-invalid");
		OutErrorMessage = TEXT("Live write accepts only one non-map project Content package.");
		return false;
	}
	if (Package->IsDirty())
	{
		OutErrorCode = TEXT("live-editor-write-package-dirty");
		OutErrorMessage = TEXT("Save or revert the target package before applying an AI live write.");
		return false;
	}
	FProperty* Property = FindFProperty<FProperty>(Asset->GetClass(), FName(*PropertyPath));
	if (Property == nullptr)
	{
		OutErrorCode = TEXT("live-editor-write-property-not-found");
		OutErrorMessage = TEXT("The exact top-level property was not found on the loaded asset.");
		return false;
	}
	if (!Property->HasAnyPropertyFlags(CPF_Edit)
		|| Property->HasAnyPropertyFlags(CPF_EditConst | CPF_Transient | CPF_DisableEditOnInstance))
	{
		OutErrorCode = TEXT("live-editor-write-property-not-editable");
		OutErrorMessage = TEXT("The exact property is not editable on this asset instance.");
		return false;
	}
	void* ValueAddress = Property->ContainerPtrToValuePtr<void>(Asset);
	TSharedPtr<FJsonValue> BeforeValue;
	if (!ReadScalarValue(Property, ValueAddress, BeforeValue))
	{
		OutErrorCode = TEXT("live-editor-write-property-type-unsupported");
		OutErrorMessage = TEXT("The first Live Write capability supports only scalar, enum, string, name, and text properties.");
		return false;
	}
	FString BeforeText;
	Property->ExportTextItem_Direct(BeforeText, ValueAddress, ValueAddress, Asset, PPF_SerializedAsImportText);

	FScopedTransaction Transaction(FText::FromString(TEXT("UE Agent Kit: Set Asset Property")));
	Asset->Modify();
	FString SetError;
	if (!SetScalarValue(Property, ValueAddress, Value, SetError))
	{
		Transaction.Cancel();
		OutErrorCode = TEXT("live-editor-write-value-invalid");
		OutErrorMessage = SetError;
		return false;
	}
	FString AfterText;
	Property->ExportTextItem_Direct(AfterText, ValueAddress, ValueAddress, Asset, PPF_SerializedAsImportText);
	if (BeforeText == AfterText)
	{
		Transaction.Cancel();
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("action"), TEXT("apply-asset-property-live"));
		Result->SetStringField(TEXT("assetPath"), AssetPath);
		Result->SetStringField(TEXT("propertyPath"), PropertyPath);
		Result->SetStringField(TEXT("propertyType"), Property->GetClass()->GetName());
		Result->SetField(TEXT("beforeValue"), BeforeValue);
		Result->SetField(TEXT("afterValue"), BeforeValue);
		Result->SetBoolField(TEXT("changed"), false);
		Result->SetBoolField(TEXT("transactionRecorded"), false);
		Result->SetBoolField(TEXT("packageDirtyBefore"), false);
		Result->SetBoolField(TEXT("packageDirtyAfter"), Package->IsDirty());
		Result->SetBoolField(TEXT("saved"), false);
		Result->SetStringField(TEXT("editorSessionId"), SessionId);
		OutResult = Result;
		return true;
	}

	FPropertyChangedEvent ChangedEvent(Property, EPropertyChangeType::ValueSet);
	Asset->PostEditChangeProperty(ChangedEvent);
	Asset->MarkPackageDirty();
	TSharedPtr<FJsonValue> AfterValue;
	if (!ReadScalarValue(Property, ValueAddress, AfterValue) || !Package->IsDirty())
	{
		Property->ImportText_Direct(*BeforeText, ValueAddress, Asset, PPF_SerializedAsImportText);
		FPropertyChangedEvent RestoreEvent(Property, EPropertyChangeType::ValueSet);
		Asset->PostEditChangeProperty(RestoreEvent);
		Package->SetDirtyFlag(false);
		Transaction.Cancel();
		OutErrorCode = TEXT("live-editor-write-apply-failed");
		OutErrorMessage = TEXT("The Editor did not confirm the changed Dirty package state.");
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("apply-asset-property-live"));
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("packageName"), Package->GetName());
	Result->SetStringField(TEXT("classPath"), Asset->GetClass()->GetPathName());
	Result->SetStringField(TEXT("propertyPath"), PropertyPath);
	Result->SetStringField(TEXT("propertyType"), Property->GetClass()->GetName());
	Result->SetField(TEXT("beforeValue"), BeforeValue);
	Result->SetField(TEXT("afterValue"), AfterValue);
	Result->SetBoolField(TEXT("changed"), true);
	Result->SetBoolField(TEXT("transactionRecorded"), true);
	Result->SetStringField(TEXT("transactionTitle"), TEXT("UE Agent Kit: Set Asset Property"));
	Result->SetBoolField(TEXT("assetOpen"), true);
	Result->SetBoolField(TEXT("loadedByBridge"), false);
	Result->SetBoolField(TEXT("packageDirtyBefore"), false);
	Result->SetBoolField(TEXT("packageDirtyAfter"), Package->IsDirty());
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}
