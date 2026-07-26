#include "StructuredPropertyJson.h"

#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace UEAgentKit::StructuredPropertyJson
{
	namespace
	{
		constexpr double MaxSafeInteger = 9007199254740991.0;

		bool IsSkippedProperty(const FProperty* Property)
		{
			const EPropertyFlags SkipFlags =
				CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient | CPF_Deprecated;
			return Property == nullptr || Property->HasAnyPropertyFlags(SkipFlags) || Property->ArrayDim != 1;
		}

		bool Utf8Less(const FString& Left, const FString& Right)
		{
			FTCHARToUTF8 LeftUtf8(*Left);
			FTCHARToUTF8 RightUtf8(*Right);
			const int32 CommonLength = FMath::Min(LeftUtf8.Length(), RightUtf8.Length());
			const int32 Comparison = CommonLength > 0
				? FMemory::Memcmp(LeftUtf8.Get(), RightUtf8.Get(), CommonLength)
				: 0;
			return Comparison < 0 || (Comparison == 0 && LeftUtf8.Length() < RightUtf8.Length());
		}

		bool Utf8LessOrEqual(const FString& Left, const FString& Right)
		{
			return Left.Equals(Right, ESearchCase::CaseSensitive) || Utf8Less(Left, Right);
		}

		FString QuoteJsonString(const FString& Value)
		{
			FString Result;
			const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
				TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Result);
			Writer->WriteValue(Value);
			Writer->Close();
			return Result;
		}

		void AppendCanonicalJson(const TSharedPtr<FJsonValue>& Value, FString& Out)
		{
			if (!Value.IsValid() || Value->Type == EJson::Null)
			{
				Out += TEXT("null");
				return;
			}
			switch (Value->Type)
			{
			case EJson::String:
				Out += QuoteJsonString(Value->AsString());
				return;
			case EJson::Number:
			{
				const double Number = Value->AsNumber();
				if (Number == 0.0)
				{
					Out += TEXT("0");
				}
				else
				{
					Out += FString::Printf(TEXT("%.17g"), Number);
				}
				return;
			}
			case EJson::Boolean:
				Out += Value->AsBool() ? TEXT("true") : TEXT("false");
				return;
			case EJson::Array:
			{
				Out += TEXT("[");
				const TArray<TSharedPtr<FJsonValue>>& Values = Value->AsArray();
				for (int32 Index = 0; Index < Values.Num(); ++Index)
				{
					if (Index > 0)
					{
						Out += TEXT(",");
					}
					AppendCanonicalJson(Values[Index], Out);
				}
				Out += TEXT("]");
				return;
			}
			case EJson::Object:
			{
				Out += TEXT("{");
				const TSharedPtr<FJsonObject> Object = Value->AsObject();
				TArray<FString> Keys;
				if (Object.IsValid())
				{
					Object->Values.GetKeys(Keys);
				}
				Keys.Sort(Utf8Less);
				for (int32 Index = 0; Index < Keys.Num(); ++Index)
				{
					if (Index > 0)
					{
						Out += TEXT(",");
					}
					Out += QuoteJsonString(Keys[Index]);
					Out += TEXT(":");
					AppendCanonicalJson(Object->Values[Keys[Index]], Out);
				}
				Out += TEXT("}");
				return;
			}
			default:
				Out += TEXT("null");
				return;
			}
		}

		bool GetScalarType(const FProperty* Property, FString& OutType, UEnum*& OutEnum)
		{
			OutType.Reset();
			OutEnum = nullptr;
			if (CastField<FBoolProperty>(Property)) OutType = TEXT("Bool");
			else if (CastField<FInt8Property>(Property)) OutType = TEXT("Int8");
			else if (const FByteProperty* ByteProperty = CastField<FByteProperty>(Property))
			{
				if (ByteProperty->Enum)
				{
					OutType = TEXT("Enum");
					OutEnum = ByteProperty->Enum;
				}
				else OutType = TEXT("UInt8");
			}
			else if (CastField<FInt16Property>(Property)) OutType = TEXT("Int16");
			else if (CastField<FUInt16Property>(Property)) OutType = TEXT("UInt16");
			else if (CastField<FIntProperty>(Property)) OutType = TEXT("Int32");
			else if (CastField<FUInt32Property>(Property)) OutType = TEXT("UInt32");
			else if (CastField<FFloatProperty>(Property)) OutType = TEXT("Float");
			else if (CastField<FDoubleProperty>(Property)) OutType = TEXT("Double");
			else if (CastField<FStrProperty>(Property)) OutType = TEXT("String");
			else if (CastField<FNameProperty>(Property)) OutType = TEXT("Name");
			else if (const FEnumProperty* EnumProperty = CastField<FEnumProperty>(Property))
			{
				OutType = TEXT("Enum");
				OutEnum = EnumProperty->GetEnum();
			}
			return !OutType.IsEmpty();
		}

		TArray<FProperty*> GetStructFields(const UScriptStruct* Struct)
		{
			TArray<FProperty*> Fields;
			if (!Struct)
			{
				return Fields;
			}
			for (TFieldIterator<FProperty> Iterator(Struct, EFieldIterationFlags::IncludeSuper); Iterator; ++Iterator)
			{
				FProperty* Field = *Iterator;
				if (!IsSkippedProperty(Field))
				{
					Fields.Add(Field);
				}
			}
			Fields.Sort([](const FProperty& Left, const FProperty& Right)
			{
				return Left.GetName() < Right.GetName();
			});
			return Fields;
		}

		bool BuildSchemaInternal(
			const FProperty* Property,
			const int32 Depth,
			TSharedPtr<FJsonValue>& OutSchema,
			FString& OutError)
		{
			if (Depth > MaxDepth)
			{
				OutError = TEXT("Structured property nesting exceeds the maximum depth.");
				return false;
			}
			if (IsSkippedProperty(Property))
			{
				OutError = TEXT("Structured property contains a skipped, fixed-array, or transient field.");
				return false;
			}

			FString ScalarType;
			UEnum* Enum = nullptr;
			if (GetScalarType(Property, ScalarType, Enum))
			{
				TSharedRef<FJsonObject> Schema = MakeShared<FJsonObject>();
				Schema->SetStringField(TEXT("kind"), TEXT("Scalar"));
				Schema->SetStringField(TEXT("scalarType"), ScalarType);
				if (Enum)
				{
					Schema->SetStringField(TEXT("enumPath"), Enum->GetPathName());
					TArray<TSharedPtr<FJsonValue>> Values;
					for (int32 Index = 0; Index < Enum->NumEnums(); ++Index)
					{
						if (!Enum->HasMetaData(TEXT("Hidden"), Index))
						{
							Values.Add(MakeShared<FJsonValueString>(Enum->GetNameStringByIndex(Index)));
						}
					}
					Schema->SetArrayField(TEXT("values"), Values);
				}
				OutSchema = MakeShared<FJsonValueObject>(Schema);
				return true;
			}

			if (const FStructProperty* StructProperty = CastField<FStructProperty>(Property))
			{
				TSharedRef<FJsonObject> Schema = MakeShared<FJsonObject>();
				Schema->SetStringField(TEXT("kind"), TEXT("Struct"));
				Schema->SetStringField(TEXT("structPath"), StructProperty->Struct->GetPathName());
				TArray<TSharedPtr<FJsonValue>> FieldValues;
				for (FProperty* Field : GetStructFields(StructProperty->Struct))
				{
					TSharedPtr<FJsonValue> FieldSchema;
					if (!BuildSchemaInternal(Field, Depth + 1, FieldSchema, OutError))
					{
						OutError = FString::Printf(TEXT("Struct field %s is unsupported: %s"), *Field->GetName(), *OutError);
						return false;
					}
					TSharedRef<FJsonObject> FieldObject = MakeShared<FJsonObject>();
					FieldObject->SetStringField(TEXT("name"), Field->GetName());
					FieldObject->SetField(TEXT("schema"), FieldSchema);
					FieldValues.Add(MakeShared<FJsonValueObject>(FieldObject));
				}
				Schema->SetArrayField(TEXT("fields"), FieldValues);
				OutSchema = MakeShared<FJsonValueObject>(Schema);
				return true;
			}

			if (const FArrayProperty* ArrayProperty = CastField<FArrayProperty>(Property))
			{
				TSharedPtr<FJsonValue> ElementSchema;
				if (!BuildSchemaInternal(ArrayProperty->Inner, Depth + 1, ElementSchema, OutError))
				{
					OutError = TEXT("Array element is unsupported: ") + OutError;
					return false;
				}
				TSharedRef<FJsonObject> Schema = MakeShared<FJsonObject>();
				Schema->SetStringField(TEXT("kind"), TEXT("Array"));
				Schema->SetField(TEXT("element"), ElementSchema);
				OutSchema = MakeShared<FJsonValueObject>(Schema);
				return true;
			}

			if (const FSetProperty* SetProperty = CastField<FSetProperty>(Property))
			{
				TSharedPtr<FJsonValue> ElementSchema;
				if (!BuildSchemaInternal(SetProperty->ElementProp, Depth + 1, ElementSchema, OutError))
				{
					OutError = TEXT("Set element is unsupported: ") + OutError;
					return false;
				}
				TSharedRef<FJsonObject> Schema = MakeShared<FJsonObject>();
				Schema->SetStringField(TEXT("kind"), TEXT("Set"));
				Schema->SetField(TEXT("element"), ElementSchema);
				OutSchema = MakeShared<FJsonValueObject>(Schema);
				return true;
			}

			if (const FMapProperty* MapProperty = CastField<FMapProperty>(Property))
			{
				TSharedPtr<FJsonValue> KeySchema;
				TSharedPtr<FJsonValue> ValueSchema;
				if (!BuildSchemaInternal(MapProperty->KeyProp, Depth + 1, KeySchema, OutError))
				{
					OutError = TEXT("Map key is unsupported: ") + OutError;
					return false;
				}
				if (!BuildSchemaInternal(MapProperty->ValueProp, Depth + 1, ValueSchema, OutError))
				{
					OutError = TEXT("Map value is unsupported: ") + OutError;
					return false;
				}
				TSharedRef<FJsonObject> Schema = MakeShared<FJsonObject>();
				Schema->SetStringField(TEXT("kind"), TEXT("Map"));
				Schema->SetField(TEXT("key"), KeySchema);
				Schema->SetField(TEXT("value"), ValueSchema);
				OutSchema = MakeShared<FJsonValueObject>(Schema);
				return true;
			}

			OutError = FString::Printf(TEXT("Unsupported structured leaf property type: %s"), *Property->GetClass()->GetName());
			return false;
		}

		bool ExportScalar(
			const FProperty* Property,
			const void* ValueAddress,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutError)
		{
			if (const FBoolProperty* Typed = CastField<FBoolProperty>(Property))
			{
				OutValue = MakeShared<FJsonValueBoolean>(Typed->GetPropertyValue(ValueAddress));
				return true;
			}
			if (const FInt8Property* Typed = CastField<FInt8Property>(Property))
			{
				OutValue = MakeShared<FJsonValueNumber>(Typed->GetPropertyValue(ValueAddress));
				return true;
			}
			if (const FByteProperty* Typed = CastField<FByteProperty>(Property))
			{
				const uint8 Raw = Typed->GetPropertyValue(ValueAddress);
				if (Typed->Enum)
				{
					OutValue = MakeShared<FJsonValueString>(Typed->Enum->GetNameStringByValue(Raw));
				}
				else
				{
					OutValue = MakeShared<FJsonValueNumber>(Raw);
				}
				return true;
			}
			if (const FInt16Property* Typed = CastField<FInt16Property>(Property))
			{
				OutValue = MakeShared<FJsonValueNumber>(Typed->GetPropertyValue(ValueAddress));
				return true;
			}
			if (const FUInt16Property* Typed = CastField<FUInt16Property>(Property))
			{
				OutValue = MakeShared<FJsonValueNumber>(Typed->GetPropertyValue(ValueAddress));
				return true;
			}
			if (const FIntProperty* Typed = CastField<FIntProperty>(Property))
			{
				OutValue = MakeShared<FJsonValueNumber>(Typed->GetPropertyValue(ValueAddress));
				return true;
			}
			if (const FUInt32Property* Typed = CastField<FUInt32Property>(Property))
			{
				OutValue = MakeShared<FJsonValueNumber>(Typed->GetPropertyValue(ValueAddress));
				return true;
			}
			if (const FFloatProperty* Typed = CastField<FFloatProperty>(Property))
			{
				const float Number = Typed->GetPropertyValue(ValueAddress);
				if (!FMath::IsFinite(Number))
				{
					OutError = TEXT("Non-finite float values are unsupported.");
					return false;
				}
				OutValue = MakeShared<FJsonValueNumber>(Number);
				return true;
			}
			if (const FDoubleProperty* Typed = CastField<FDoubleProperty>(Property))
			{
				const double Number = Typed->GetPropertyValue(ValueAddress);
				if (!FMath::IsFinite(Number))
				{
					OutError = TEXT("Non-finite double values are unsupported.");
					return false;
				}
				OutValue = MakeShared<FJsonValueNumber>(Number);
				return true;
			}
			if (const FStrProperty* Typed = CastField<FStrProperty>(Property))
			{
				OutValue = MakeShared<FJsonValueString>(Typed->GetPropertyValue(ValueAddress));
				return true;
			}
			if (const FNameProperty* Typed = CastField<FNameProperty>(Property))
			{
				OutValue = MakeShared<FJsonValueString>(Typed->GetPropertyValue(ValueAddress).ToString());
				return true;
			}
			if (const FEnumProperty* Typed = CastField<FEnumProperty>(Property))
			{
				const int64 Raw = Typed->GetUnderlyingProperty()->GetSignedIntPropertyValue(ValueAddress);
				OutValue = MakeShared<FJsonValueString>(Typed->GetEnum()->GetNameStringByValue(Raw));
				return true;
			}
			OutError = FString::Printf(TEXT("Unsupported scalar property type: %s"), *Property->GetClass()->GetName());
			return false;
		}

		bool ExportValueInternal(
			const FProperty* Property,
			const void* ValueAddress,
			const int32 Depth,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutError)
		{
			if (Depth > MaxDepth)
			{
				OutError = TEXT("Structured property nesting exceeds the maximum depth.");
				return false;
			}
			FString ScalarType;
			UEnum* Enum = nullptr;
			if (GetScalarType(Property, ScalarType, Enum))
			{
				return ExportScalar(Property, ValueAddress, OutValue, OutError);
			}
			if (const FStructProperty* StructProperty = CastField<FStructProperty>(Property))
			{
				TSharedRef<FJsonObject> FieldsObject = MakeShared<FJsonObject>();
				for (FProperty* Field : GetStructFields(StructProperty->Struct))
				{
					TSharedPtr<FJsonValue> FieldValue;
					if (!ExportValueInternal(
							Field,
							Field->ContainerPtrToValuePtr<void>(ValueAddress),
							Depth + 1,
							FieldValue,
							OutError))
					{
						OutError = FString::Printf(TEXT("Could not export struct field %s: %s"), *Field->GetName(), *OutError);
						return false;
					}
					FieldsObject->SetField(Field->GetName(), FieldValue);
				}
				TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
				Envelope->SetStringField(TEXT("valueType"), TEXT("Struct"));
				Envelope->SetObjectField(TEXT("fields"), FieldsObject);
				OutValue = MakeShared<FJsonValueObject>(Envelope);
				return true;
			}
			if (const FArrayProperty* ArrayProperty = CastField<FArrayProperty>(Property))
			{
				FScriptArrayHelper Helper(ArrayProperty, ValueAddress);
				if (Helper.Num() > MaxContainerEntries)
				{
					OutError = TEXT("Array exceeds the maximum entry count.");
					return false;
				}
				TArray<TSharedPtr<FJsonValue>> Items;
				Items.Reserve(Helper.Num());
				for (int32 Index = 0; Index < Helper.Num(); ++Index)
				{
					TSharedPtr<FJsonValue> Item;
					if (!ExportValueInternal(ArrayProperty->Inner, Helper.GetRawPtr(Index), Depth + 1, Item, OutError))
					{
						OutError = FString::Printf(TEXT("Could not export array item %d: %s"), Index, *OutError);
						return false;
					}
					Items.Add(Item);
				}
				TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
				Envelope->SetStringField(TEXT("valueType"), TEXT("Array"));
				Envelope->SetArrayField(TEXT("items"), Items);
				OutValue = MakeShared<FJsonValueObject>(Envelope);
				return true;
			}
			if (const FSetProperty* SetProperty = CastField<FSetProperty>(Property))
			{
				FScriptSetHelper Helper(SetProperty, ValueAddress);
				if (Helper.Num() > MaxContainerEntries)
				{
					OutError = TEXT("Set exceeds the maximum entry count.");
					return false;
				}
				TArray<TPair<FString, TSharedPtr<FJsonValue>>> SortedItems;
				for (int32 Index = 0; Index < Helper.GetMaxIndex(); ++Index)
				{
					if (!Helper.IsValidIndex(Index)) continue;
					TSharedPtr<FJsonValue> Item;
					if (!ExportValueInternal(SetProperty->ElementProp, Helper.GetElementPtr(Index), Depth + 1, Item, OutError))
					{
						OutError = FString::Printf(TEXT("Could not export set item %d: %s"), Index, *OutError);
						return false;
					}
					SortedItems.Emplace(CanonicalJson(Item), Item);
				}
				SortedItems.Sort([](const TPair<FString, TSharedPtr<FJsonValue>>& Left, const TPair<FString, TSharedPtr<FJsonValue>>& Right)
				{
					return Utf8Less(Left.Key, Right.Key);
				});
				TArray<TSharedPtr<FJsonValue>> Items;
				for (const TPair<FString, TSharedPtr<FJsonValue>>& Item : SortedItems) Items.Add(Item.Value);
				TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
				Envelope->SetStringField(TEXT("valueType"), TEXT("Set"));
				Envelope->SetArrayField(TEXT("items"), Items);
				OutValue = MakeShared<FJsonValueObject>(Envelope);
				return true;
			}
			if (const FMapProperty* MapProperty = CastField<FMapProperty>(Property))
			{
				FScriptMapHelper Helper(MapProperty, ValueAddress);
				if (Helper.Num() > MaxContainerEntries)
				{
					OutError = TEXT("Map exceeds the maximum entry count.");
					return false;
				}
				TArray<TPair<FString, TSharedPtr<FJsonValue>>> SortedEntries;
				for (int32 Index = 0; Index < Helper.GetMaxIndex(); ++Index)
				{
					if (!Helper.IsValidIndex(Index)) continue;
					TSharedPtr<FJsonValue> Key;
					TSharedPtr<FJsonValue> ItemValue;
					if (!ExportValueInternal(MapProperty->KeyProp, Helper.GetKeyPtr(Index), Depth + 1, Key, OutError)
						|| !ExportValueInternal(MapProperty->ValueProp, Helper.GetValuePtr(Index), Depth + 1, ItemValue, OutError))
					{
						OutError = FString::Printf(TEXT("Could not export map entry %d: %s"), Index, *OutError);
						return false;
					}
					TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
					Entry->SetField(TEXT("key"), Key);
					Entry->SetField(TEXT("value"), ItemValue);
					SortedEntries.Emplace(CanonicalJson(Key), MakeShared<FJsonValueObject>(Entry));
				}
				SortedEntries.Sort([](const TPair<FString, TSharedPtr<FJsonValue>>& Left, const TPair<FString, TSharedPtr<FJsonValue>>& Right)
				{
					return Utf8Less(Left.Key, Right.Key);
				});
				TArray<TSharedPtr<FJsonValue>> Entries;
				for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : SortedEntries) Entries.Add(Entry.Value);
				TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
				Envelope->SetStringField(TEXT("valueType"), TEXT("Map"));
				Envelope->SetArrayField(TEXT("entries"), Entries);
				OutValue = MakeShared<FJsonValueObject>(Envelope);
				return true;
			}
			OutError = FString::Printf(TEXT("Unsupported property type: %s"), *Property->GetClass()->GetName());
			return false;
		}

		bool ReadEnvelope(
			const TSharedPtr<FJsonValue>& JsonValue,
			const FString& ExpectedType,
			const TSet<FString>& ExpectedFields,
			TSharedPtr<FJsonObject>& OutObject,
			FString& OutError)
		{
			if (!JsonValue.IsValid() || JsonValue->Type != EJson::Object)
			{
				OutError = ExpectedType + TEXT(" value must be a JSON object envelope.");
				return false;
			}
			OutObject = JsonValue->AsObject();
			if (!OutObject.IsValid())
			{
				OutError = ExpectedType + TEXT(" envelope is invalid.");
				return false;
			}
			TSet<FString> ActualFields;
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : OutObject->Values) ActualFields.Add(Pair.Key);
			if (ActualFields.Difference(ExpectedFields).Num() > 0 || ExpectedFields.Difference(ActualFields).Num() > 0)
			{
				OutError = ExpectedType + TEXT(" envelope fields do not match the stable value model.");
				return false;
			}
			FString ValueType;
			if (!OutObject->TryGetStringField(TEXT("valueType"), ValueType)
				|| !ValueType.Equals(ExpectedType, ESearchCase::CaseSensitive))
			{
				OutError = FString::Printf(TEXT("Expected valueType %s."), *ExpectedType);
				return false;
			}
			return true;
		}

		bool ReadFiniteNumber(const TSharedPtr<FJsonValue>& JsonValue, double& OutNumber, FString& OutError)
		{
			if (!JsonValue.IsValid() || !JsonValue->TryGetNumber(OutNumber) || !FMath::IsFinite(OutNumber))
			{
				OutError = TEXT("Expected a finite JSON number.");
				return false;
			}
			return true;
		}

		bool ReadInteger(const TSharedPtr<FJsonValue>& JsonValue, double& OutNumber, FString& OutError)
		{
			if (!ReadFiniteNumber(JsonValue, OutNumber, OutError)
				|| FMath::TruncToDouble(OutNumber) != OutNumber
				|| FMath::Abs(OutNumber) > MaxSafeInteger)
			{
				OutError = TEXT("Expected a safe integral JSON number.");
				return false;
			}
			return true;
		}

		bool ImportScalar(
			const FProperty* Property,
			void* ValueAddress,
			const TSharedPtr<FJsonValue>& JsonValue,
			FString& OutError)
		{
			if (const FBoolProperty* Typed = CastField<FBoolProperty>(Property))
			{
				bool Value = false;
				if (!JsonValue.IsValid() || !JsonValue->TryGetBool(Value))
				{
					OutError = TEXT("Expected a JSON boolean.");
					return false;
				}
				Typed->SetPropertyValue(ValueAddress, Value);
				return true;
			}

			double Number = 0.0;
			if (const FInt8Property* Typed = CastField<FInt8Property>(Property))
			{
				if (!ReadInteger(JsonValue, Number, OutError) || Number < MIN_int8 || Number > MAX_int8) return false;
				Typed->SetPropertyValue(ValueAddress, static_cast<int8>(Number));
				return true;
			}
			if (const FByteProperty* Typed = CastField<FByteProperty>(Property))
			{
				if (Typed->Enum)
				{
					FString EnumName;
					if (!JsonValue.IsValid() || !JsonValue->TryGetString(EnumName))
					{
						OutError = TEXT("Expected an enum name string.");
						return false;
					}
					const int64 EnumValue = Typed->Enum->GetValueByNameString(EnumName, EGetByNameFlags::None);
					if (EnumValue == INDEX_NONE || EnumValue < 0 || EnumValue > MAX_uint8)
					{
						OutError = TEXT("Enum name is invalid for the property.");
						return false;
					}
					Typed->SetPropertyValue(ValueAddress, static_cast<uint8>(EnumValue));
					return true;
				}
				if (!ReadInteger(JsonValue, Number, OutError) || Number < 0 || Number > MAX_uint8) return false;
				Typed->SetPropertyValue(ValueAddress, static_cast<uint8>(Number));
				return true;
			}
			if (const FInt16Property* Typed = CastField<FInt16Property>(Property))
			{
				if (!ReadInteger(JsonValue, Number, OutError) || Number < MIN_int16 || Number > MAX_int16) return false;
				Typed->SetPropertyValue(ValueAddress, static_cast<int16>(Number));
				return true;
			}
			if (const FUInt16Property* Typed = CastField<FUInt16Property>(Property))
			{
				if (!ReadInteger(JsonValue, Number, OutError) || Number < 0 || Number > MAX_uint16) return false;
				Typed->SetPropertyValue(ValueAddress, static_cast<uint16>(Number));
				return true;
			}
			if (const FIntProperty* Typed = CastField<FIntProperty>(Property))
			{
				if (!ReadInteger(JsonValue, Number, OutError) || Number < MIN_int32 || Number > MAX_int32) return false;
				Typed->SetPropertyValue(ValueAddress, static_cast<int32>(Number));
				return true;
			}
			if (const FUInt32Property* Typed = CastField<FUInt32Property>(Property))
			{
				if (!ReadInteger(JsonValue, Number, OutError) || Number < 0 || Number > MAX_uint32) return false;
				Typed->SetPropertyValue(ValueAddress, static_cast<uint32>(Number));
				return true;
			}
			if (const FFloatProperty* Typed = CastField<FFloatProperty>(Property))
			{
				if (!ReadFiniteNumber(JsonValue, Number, OutError) || Number < -MAX_flt || Number > MAX_flt) return false;
				Typed->SetPropertyValue(ValueAddress, static_cast<float>(Number));
				return true;
			}
			if (const FDoubleProperty* Typed = CastField<FDoubleProperty>(Property))
			{
				if (!ReadFiniteNumber(JsonValue, Number, OutError)) return false;
				Typed->SetPropertyValue(ValueAddress, Number);
				return true;
			}
			if (const FStrProperty* Typed = CastField<FStrProperty>(Property))
			{
				FString Value;
				if (!JsonValue.IsValid() || !JsonValue->TryGetString(Value))
				{
					OutError = TEXT("Expected a JSON string.");
					return false;
				}
				Typed->SetPropertyValue(ValueAddress, Value);
				return true;
			}
			if (const FNameProperty* Typed = CastField<FNameProperty>(Property))
			{
				FString Value;
				if (!JsonValue.IsValid() || !JsonValue->TryGetString(Value))
				{
					OutError = TEXT("Expected a JSON string for Name.");
					return false;
				}
				Typed->SetPropertyValue(ValueAddress, FName(*Value));
				return true;
			}
			if (const FEnumProperty* Typed = CastField<FEnumProperty>(Property))
			{
				FString EnumName;
				if (!JsonValue.IsValid() || !JsonValue->TryGetString(EnumName))
				{
					OutError = TEXT("Expected an enum name string.");
					return false;
				}
				const int64 EnumValue = Typed->GetEnum()->GetValueByNameString(EnumName, EGetByNameFlags::None);
				if (EnumValue == INDEX_NONE)
				{
					OutError = TEXT("Enum name is invalid for the property.");
					return false;
				}
				Typed->GetUnderlyingProperty()->SetIntPropertyValue(ValueAddress, EnumValue);
				return true;
			}
			OutError = FString::Printf(TEXT("Unsupported scalar property type: %s"), *Property->GetClass()->GetName());
			return false;
		}

		bool ImportValueInternal(
			const FProperty* Property,
			void* ValueAddress,
			const TSharedPtr<FJsonValue>& JsonValue,
			const int32 Depth,
			FString& OutError)
		{
			if (Depth > MaxDepth)
			{
				OutError = TEXT("Structured property nesting exceeds the maximum depth.");
				return false;
			}
			FString ScalarType;
			UEnum* Enum = nullptr;
			if (GetScalarType(Property, ScalarType, Enum))
			{
				return ImportScalar(Property, ValueAddress, JsonValue, OutError);
			}
			if (const FStructProperty* StructProperty = CastField<FStructProperty>(Property))
			{
				TSharedPtr<FJsonObject> Envelope;
				if (!ReadEnvelope(JsonValue, TEXT("Struct"), {TEXT("valueType"), TEXT("fields")}, Envelope, OutError)) return false;
				const TSharedPtr<FJsonObject>* Fields = nullptr;
				if (!Envelope->TryGetObjectField(TEXT("fields"), Fields) || !Fields || !Fields->IsValid())
				{
					OutError = TEXT("Struct fields must be a JSON object.");
					return false;
				}
				const TArray<FProperty*> StructFields = GetStructFields(StructProperty->Struct);
				if ((*Fields)->Values.Num() != StructFields.Num())
				{
					OutError = TEXT("Struct value must contain every supported field exactly once.");
					return false;
				}
				for (FProperty* Field : StructFields)
				{
					const TSharedPtr<FJsonValue>* FieldValue = (*Fields)->Values.Find(Field->GetName());
					if (!FieldValue || !ImportValueInternal(
							Field,
							Field->ContainerPtrToValuePtr<void>(ValueAddress),
							*FieldValue,
							Depth + 1,
							OutError))
					{
						OutError = FString::Printf(TEXT("Could not import struct field %s: %s"), *Field->GetName(), *OutError);
						return false;
					}
				}
				return true;
			}
			if (const FArrayProperty* ArrayProperty = CastField<FArrayProperty>(Property))
			{
				TSharedPtr<FJsonObject> Envelope;
				if (!ReadEnvelope(JsonValue, TEXT("Array"), {TEXT("valueType"), TEXT("items")}, Envelope, OutError)) return false;
				const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
				if (!Envelope->TryGetArrayField(TEXT("items"), Items) || !Items || Items->Num() > MaxContainerEntries)
				{
					OutError = TEXT("Array items are invalid or exceed the maximum entry count.");
					return false;
				}
				FScriptArrayHelper Helper(ArrayProperty, ValueAddress);
				Helper.Resize(Items->Num());
				for (int32 Index = 0; Index < Items->Num(); ++Index)
				{
					if (!ImportValueInternal(ArrayProperty->Inner, Helper.GetRawPtr(Index), (*Items)[Index], Depth + 1, OutError))
					{
						OutError = FString::Printf(TEXT("Could not import array item %d: %s"), Index, *OutError);
						return false;
					}
				}
				return true;
			}
			if (const FSetProperty* SetProperty = CastField<FSetProperty>(Property))
			{
				TSharedPtr<FJsonObject> Envelope;
				if (!ReadEnvelope(JsonValue, TEXT("Set"), {TEXT("valueType"), TEXT("items")}, Envelope, OutError)) return false;
				const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
				if (!Envelope->TryGetArrayField(TEXT("items"), Items) || !Items || Items->Num() > MaxContainerEntries)
				{
					OutError = TEXT("Set items are invalid or exceed the maximum entry count.");
					return false;
				}
				FString Previous;
				for (const TSharedPtr<FJsonValue>& Item : *Items)
				{
					const FString Current = CanonicalJson(Item);
					if (!Previous.IsEmpty() && Utf8LessOrEqual(Current, Previous))
					{
						OutError = TEXT("Set items must be unique and sorted by Canonical JSON.");
						return false;
					}
					Previous = Current;
				}
				FScriptSetHelper Helper(SetProperty, ValueAddress);
				Helper.EmptyElements(Items->Num());
				for (int32 Index = 0; Index < Items->Num(); ++Index)
				{
					const int32 NewIndex = Helper.AddDefaultValue_Invalid_NeedsRehash();
					if (!ImportValueInternal(SetProperty->ElementProp, Helper.GetElementPtr(NewIndex), (*Items)[Index], Depth + 1, OutError))
					{
						OutError = FString::Printf(TEXT("Could not import set item %d: %s"), Index, *OutError);
						return false;
					}
				}
				Helper.Rehash();
				return true;
			}
			if (const FMapProperty* MapProperty = CastField<FMapProperty>(Property))
			{
				TSharedPtr<FJsonObject> Envelope;
				if (!ReadEnvelope(JsonValue, TEXT("Map"), {TEXT("valueType"), TEXT("entries")}, Envelope, OutError)) return false;
				const TArray<TSharedPtr<FJsonValue>>* Entries = nullptr;
				if (!Envelope->TryGetArrayField(TEXT("entries"), Entries) || !Entries || Entries->Num() > MaxContainerEntries)
				{
					OutError = TEXT("Map entries are invalid or exceed the maximum entry count.");
					return false;
				}
				FString PreviousKey;
				for (const TSharedPtr<FJsonValue>& EntryValue : *Entries)
				{
					if (!EntryValue.IsValid() || EntryValue->Type != EJson::Object)
					{
						OutError = TEXT("Map entries must be JSON objects.");
						return false;
					}
					const TSharedPtr<FJsonObject> Entry = EntryValue->AsObject();
					if (!Entry.IsValid() || Entry->Values.Num() != 2 || !Entry->HasField(TEXT("key")) || !Entry->HasField(TEXT("value")))
					{
						OutError = TEXT("Map entries must contain exactly key and value.");
						return false;
					}
					const FString CurrentKey = CanonicalJson(Entry->TryGetField(TEXT("key")));
					if (!PreviousKey.IsEmpty() && Utf8LessOrEqual(CurrentKey, PreviousKey))
					{
						OutError = TEXT("Map entries must have unique keys sorted by Canonical JSON.");
						return false;
					}
					PreviousKey = CurrentKey;
				}
				FScriptMapHelper Helper(MapProperty, ValueAddress);
				Helper.EmptyValues(Entries->Num());
				for (int32 Index = 0; Index < Entries->Num(); ++Index)
				{
					const TSharedPtr<FJsonObject> Entry = (*Entries)[Index]->AsObject();
					const int32 NewIndex = Helper.AddDefaultValue_Invalid_NeedsRehash();
					if (!ImportValueInternal(MapProperty->KeyProp, Helper.GetKeyPtr(NewIndex), Entry->TryGetField(TEXT("key")), Depth + 1, OutError)
						|| !ImportValueInternal(MapProperty->ValueProp, Helper.GetValuePtr(NewIndex), Entry->TryGetField(TEXT("value")), Depth + 1, OutError))
					{
						OutError = FString::Printf(TEXT("Could not import map entry %d: %s"), Index, *OutError);
						return false;
					}
				}
				Helper.Rehash();
				return true;
			}
			OutError = FString::Printf(TEXT("Unsupported property type: %s"), *Property->GetClass()->GetName());
			return false;
		}

		void AddDiffRecord(
			TArray<TSharedPtr<FJsonValue>>& OutDiff,
			bool& bOutTruncated,
			const FString& Change,
			const FString& Path,
			const TSharedPtr<FJsonValue>& Before,
			const TSharedPtr<FJsonValue>& After,
			const TSharedPtr<FJsonValue>& Key = nullptr,
			const int32 Index = INDEX_NONE)
		{
			if (OutDiff.Num() >= MaxDiffEntries)
			{
				bOutTruncated = true;
				return;
			}
			TSharedRef<FJsonObject> Record = MakeShared<FJsonObject>();
			Record->SetStringField(TEXT("change"), Change);
			Record->SetStringField(TEXT("path"), Path);
			if (Before.IsValid()) Record->SetField(TEXT("before"), Before);
			if (After.IsValid()) Record->SetField(TEXT("after"), After);
			if (Key.IsValid()) Record->SetField(TEXT("key"), Key);
			if (Index != INDEX_NONE) Record->SetNumberField(TEXT("index"), Index);
			OutDiff.Add(MakeShared<FJsonValueObject>(Record));
		}

		FString EscapePathSegment(const FString& Segment)
		{
			return Segment.Replace(TEXT("~"), TEXT("~0")).Replace(TEXT("/"), TEXT("~1"));
		}

		void BuildDiffInternal(
			const TSharedPtr<FJsonValue>& Before,
			const TSharedPtr<FJsonValue>& After,
			const FString& Path,
			TArray<TSharedPtr<FJsonValue>>& OutDiff,
			bool& bOutTruncated)
		{
			if (bOutTruncated || JsonEqual(Before, After)) return;
			if (!Before.IsValid() || !After.IsValid() || Before->Type != EJson::Object || After->Type != EJson::Object)
			{
				AddDiffRecord(OutDiff, bOutTruncated, TEXT("replace"), Path, Before, After);
				return;
			}
			const TSharedPtr<FJsonObject> BeforeObject = Before->AsObject();
			const TSharedPtr<FJsonObject> AfterObject = After->AsObject();
			FString BeforeType;
			FString AfterType;
			if (!BeforeObject.IsValid() || !AfterObject.IsValid()
				|| !BeforeObject->TryGetStringField(TEXT("valueType"), BeforeType)
				|| !AfterObject->TryGetStringField(TEXT("valueType"), AfterType)
				|| BeforeType != AfterType)
			{
				AddDiffRecord(OutDiff, bOutTruncated, TEXT("replace"), Path, Before, After);
				return;
			}
			if (BeforeType == TEXT("Struct"))
			{
				const TSharedPtr<FJsonObject>* BeforeFields = nullptr;
				const TSharedPtr<FJsonObject>* AfterFields = nullptr;
				if (!BeforeObject->TryGetObjectField(TEXT("fields"), BeforeFields) || !BeforeFields
					|| !AfterObject->TryGetObjectField(TEXT("fields"), AfterFields) || !AfterFields)
				{
					AddDiffRecord(OutDiff, bOutTruncated, TEXT("replace"), Path, Before, After);
					return;
				}
				TArray<FString> Keys;
				(*BeforeFields)->Values.GetKeys(Keys);
				Keys.Sort(Utf8Less);
				for (const FString& Key : Keys)
				{
					BuildDiffInternal(
						(*BeforeFields)->TryGetField(Key),
						(*AfterFields)->TryGetField(Key),
						Path + TEXT("/fields/") + EscapePathSegment(Key),
						OutDiff,
						bOutTruncated);
				}
				return;
			}
			if (BeforeType == TEXT("Array"))
			{
				const TArray<TSharedPtr<FJsonValue>>* BeforeItems = nullptr;
				const TArray<TSharedPtr<FJsonValue>>* AfterItems = nullptr;
				if (!BeforeObject->TryGetArrayField(TEXT("items"), BeforeItems) || !BeforeItems
					|| !AfterObject->TryGetArrayField(TEXT("items"), AfterItems) || !AfterItems)
				{
					AddDiffRecord(OutDiff, bOutTruncated, TEXT("replace"), Path, Before, After);
					return;
				}
				const int32 CommonCount = FMath::Min(BeforeItems->Num(), AfterItems->Num());
				for (int32 Index = 0; Index < CommonCount; ++Index)
				{
					BuildDiffInternal((*BeforeItems)[Index], (*AfterItems)[Index], Path + TEXT("/items/") + LexToString(Index), OutDiff, bOutTruncated);
				}
				for (int32 Index = BeforeItems->Num() - 1; Index >= AfterItems->Num(); --Index)
				{
					AddDiffRecord(OutDiff, bOutTruncated, TEXT("array-remove"), Path, (*BeforeItems)[Index], nullptr, nullptr, Index);
				}
				for (int32 Index = BeforeItems->Num(); Index < AfterItems->Num(); ++Index)
				{
					AddDiffRecord(OutDiff, bOutTruncated, TEXT("array-add"), Path, nullptr, (*AfterItems)[Index], nullptr, Index);
				}
				return;
			}
			if (BeforeType == TEXT("Set"))
			{
				const TArray<TSharedPtr<FJsonValue>>* BeforeItems = nullptr;
				const TArray<TSharedPtr<FJsonValue>>* AfterItems = nullptr;
				if (!BeforeObject->TryGetArrayField(TEXT("items"), BeforeItems) || !BeforeItems
					|| !AfterObject->TryGetArrayField(TEXT("items"), AfterItems) || !AfterItems)
				{
					AddDiffRecord(OutDiff, bOutTruncated, TEXT("replace"), Path, Before, After);
					return;
				}
				TMap<FString, TSharedPtr<FJsonValue>> BeforeMap;
				TMap<FString, TSharedPtr<FJsonValue>> AfterMap;
				for (const TSharedPtr<FJsonValue>& Item : *BeforeItems) BeforeMap.Add(CanonicalJson(Item), Item);
				for (const TSharedPtr<FJsonValue>& Item : *AfterItems) AfterMap.Add(CanonicalJson(Item), Item);
				TArray<FString> Keys;
				BeforeMap.GetKeys(Keys);
				Keys.Sort(Utf8Less);
				for (const FString& Key : Keys)
				{
					if (!AfterMap.Contains(Key)) AddDiffRecord(OutDiff, bOutTruncated, TEXT("set-remove"), Path, BeforeMap[Key], nullptr);
				}
				AfterMap.GetKeys(Keys);
				Keys.Sort(Utf8Less);
				for (const FString& Key : Keys)
				{
					if (!BeforeMap.Contains(Key)) AddDiffRecord(OutDiff, bOutTruncated, TEXT("set-add"), Path, nullptr, AfterMap[Key]);
				}
				return;
			}
			if (BeforeType == TEXT("Map"))
			{
				const TArray<TSharedPtr<FJsonValue>>* BeforeEntries = nullptr;
				const TArray<TSharedPtr<FJsonValue>>* AfterEntries = nullptr;
				if (!BeforeObject->TryGetArrayField(TEXT("entries"), BeforeEntries) || !BeforeEntries
					|| !AfterObject->TryGetArrayField(TEXT("entries"), AfterEntries) || !AfterEntries)
				{
					AddDiffRecord(OutDiff, bOutTruncated, TEXT("replace"), Path, Before, After);
					return;
				}
				TMap<FString, TSharedPtr<FJsonObject>> BeforeMap;
				TMap<FString, TSharedPtr<FJsonObject>> AfterMap;
				for (const TSharedPtr<FJsonValue>& Entry : *BeforeEntries)
				{
					const TSharedPtr<FJsonObject> Object = Entry->AsObject();
					BeforeMap.Add(CanonicalJson(Object->TryGetField(TEXT("key"))), Object);
				}
				for (const TSharedPtr<FJsonValue>& Entry : *AfterEntries)
				{
					const TSharedPtr<FJsonObject> Object = Entry->AsObject();
					AfterMap.Add(CanonicalJson(Object->TryGetField(TEXT("key"))), Object);
				}
				TArray<FString> Keys;
				BeforeMap.GetKeys(Keys);
				Keys.Sort(Utf8Less);
				for (const FString& Key : Keys)
				{
					if (!AfterMap.Contains(Key))
					{
						AddDiffRecord(OutDiff, bOutTruncated, TEXT("map-remove"), Path, BeforeMap[Key]->TryGetField(TEXT("value")), nullptr, BeforeMap[Key]->TryGetField(TEXT("key")));
					}
					else
					{
						BuildDiffInternal(
							BeforeMap[Key]->TryGetField(TEXT("value")),
							AfterMap[Key]->TryGetField(TEXT("value")),
							Path + TEXT("/entries/") + EscapePathSegment(Key) + TEXT("/value"),
							OutDiff,
							bOutTruncated);
					}
				}
				AfterMap.GetKeys(Keys);
				Keys.Sort(Utf8Less);
				for (const FString& Key : Keys)
				{
					if (!BeforeMap.Contains(Key))
					{
						AddDiffRecord(OutDiff, bOutTruncated, TEXT("map-add"), Path, nullptr, AfterMap[Key]->TryGetField(TEXT("value")), AfterMap[Key]->TryGetField(TEXT("key")));
					}
				}
				return;
			}
			AddDiffRecord(OutDiff, bOutTruncated, TEXT("replace"), Path, Before, After);
		}
	}

	EKind GetKind(const FProperty* Property)
	{
		if (CastField<FStructProperty>(Property)) return EKind::Struct;
		if (CastField<FArrayProperty>(Property)) return EKind::Array;
		if (CastField<FSetProperty>(Property)) return EKind::Set;
		if (CastField<FMapProperty>(Property)) return EKind::Map;
		return EKind::Invalid;
	}

	FString KindName(const EKind Kind)
	{
		switch (Kind)
		{
		case EKind::Struct: return TEXT("Struct");
		case EKind::Array: return TEXT("Array");
		case EKind::Set: return TEXT("Set");
		case EKind::Map: return TEXT("Map");
		default: return FString();
		}
	}

	bool BuildSchema(const FProperty* Property, TSharedPtr<FJsonValue>& OutSchema, FString& OutError)
	{
		return BuildSchemaInternal(Property, 0, OutSchema, OutError);
	}

	bool ExportValue(const FProperty* Property, const void* ValueAddress, TSharedPtr<FJsonValue>& OutValue, FString& OutError)
	{
		if (GetKind(Property) == EKind::Invalid)
		{
			OutError = TEXT("Property is not Struct, Array, Set, or Map.");
			return false;
		}
		return ExportValueInternal(Property, ValueAddress, 0, OutValue, OutError);
	}

	bool ImportValue(const FProperty* Property, void* ValueAddress, const TSharedPtr<FJsonValue>& JsonValue, FString& OutError)
	{
		if (GetKind(Property) == EKind::Invalid)
		{
			OutError = TEXT("Property is not Struct, Array, Set, or Map.");
			return false;
		}
		return ImportValueInternal(Property, ValueAddress, JsonValue, 0, OutError);
	}

	FString CanonicalJson(const TSharedPtr<FJsonValue>& Value)
	{
		FString Result;
		AppendCanonicalJson(Value, Result);
		return Result;
	}

	bool JsonEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right)
	{
		return CanonicalJson(Left).Equals(CanonicalJson(Right), ESearchCase::CaseSensitive);
	}

	void BuildDiff(
		const TSharedPtr<FJsonValue>& Before,
		const TSharedPtr<FJsonValue>& After,
		TArray<TSharedPtr<FJsonValue>>& OutDiff,
		bool& bOutTruncated)
	{
		OutDiff.Reset();
		bOutTruncated = false;
		BuildDiffInternal(Before, After, TEXT("$"), OutDiff, bOutTruncated);
	}
}
