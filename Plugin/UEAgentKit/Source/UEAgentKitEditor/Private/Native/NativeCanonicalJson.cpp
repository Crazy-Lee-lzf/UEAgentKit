// Copyright Epic Games, Inc. All Rights Reserved.

#include "Native/NativeCanonicalJson.h"

#include "BlueprintContextSha256.h"

namespace
{
	constexpr int32 MaxCanonicalDepth = 64;

	bool AppendEscapedJsonString(const FString& InValue, FString& OutText)
	{
		OutText.AppendChar(TEXT('"'));
		const int32 Length = InValue.Len();
		for (int32 Index = 0; Index < Length; ++Index)
		{
			const TCHAR Character = InValue[Index];
			switch (Character)
			{
			case TEXT('"'):
				OutText += TEXT("\\\"");
				break;
			case TEXT('\\'):
				OutText += TEXT("\\\\");
				break;
			case TEXT('\b'):
				OutText += TEXT("\\b");
				break;
			case TEXT('\f'):
				OutText += TEXT("\\f");
				break;
			case TEXT('\n'):
				OutText += TEXT("\\n");
				break;
			case TEXT('\r'):
				OutText += TEXT("\\r");
				break;
			case TEXT('\t'):
				OutText += TEXT("\\t");
				break;
			default:
				if (Character < 0x20)
				{
					OutText += FString::Printf(TEXT("\\u%04x"), static_cast<int32>(Character));
				}
				else if (Character >= 0xD800 && Character <= 0xDBFF)
				{
					if (Index + 1 >= Length)
					{
						return false;
					}
					const TCHAR LowSurrogate = InValue[Index + 1];
					if (LowSurrogate < 0xDC00 || LowSurrogate > 0xDFFF)
					{
						return false;
					}
					OutText.AppendChar(Character);
					OutText.AppendChar(LowSurrogate);
					++Index;
				}
				else if (Character >= 0xDC00 && Character <= 0xDFFF)
				{
					return false;
				}
				else
				{
					OutText.AppendChar(Character);
				}
				break;
			}
		}
		OutText.AppendChar(TEXT('"'));
		return true;
	}

	bool SerializeValue(const FNativeCanonicalValueRef& Value, FString& OutText, FString& OutError, int32 Depth)
	{
		if (Depth > MaxCanonicalDepth)
		{
			OutError = TEXT("canonical-depth-exceeded");
			return false;
		}

		switch (Value->GetType())
		{
		case FNativeCanonicalValue::EType::Null:
			OutText += TEXT("null");
			return true;
		case FNativeCanonicalValue::EType::Bool:
			OutText += Value->GetBool() ? TEXT("true") : TEXT("false");
			return true;
		case FNativeCanonicalValue::EType::Integer:
			OutText += FString::Printf(TEXT("%lld"), Value->GetInteger());
			return true;
		case FNativeCanonicalValue::EType::String:
			if (!AppendEscapedJsonString(Value->GetString(), OutText))
			{
				OutError = TEXT("canonical-invalid-string");
				return false;
			}
			return true;
		case FNativeCanonicalValue::EType::Array:
		{
			OutText.AppendChar(TEXT('['));
			bool bFirst = true;
			for (const FNativeCanonicalValueRef& Item : Value->GetArray())
			{
				if (!bFirst)
				{
					OutText.AppendChar(TEXT(','));
				}
				bFirst = false;
				if (!SerializeValue(Item, OutText, OutError, Depth + 1))
				{
					return false;
				}
			}
			OutText.AppendChar(TEXT(']'));
			return true;
		}
		case FNativeCanonicalValue::EType::Object:
		{
			const TArray<TPair<FString, FNativeCanonicalValueRef>>& Entries = Value->GetObjectValues();
			TArray<int32> Order;
			Order.Reserve(Entries.Num());
			for (int32 Index = 0; Index < Entries.Num(); ++Index)
			{
				Order.Add(Index);
			}
			Order.Reset(Entries.Num());
			for (int32 Index = 0; Index < Entries.Num(); ++Index)
			{
				Order.Add(Index);
			}
			Order.Sort([&Entries](const int32 Left, const int32 Right) {
				return UEAgentKitCanonical::CompareUtf8(Entries[Left].Key, Entries[Right].Key) < 0;
			});

			OutText.AppendChar(TEXT('{'));
			bool bFirst = true;
			for (const int32 EntryIndex : Order)
			{
				if (!bFirst)
				{
					OutText.AppendChar(TEXT(','));
				}
				bFirst = false;
				if (!AppendEscapedJsonString(Entries[EntryIndex].Key, OutText))
				{
					OutError = TEXT("canonical-invalid-string");
					return false;
				}
				OutText.AppendChar(TEXT(':'));
				if (!SerializeValue(Entries[EntryIndex].Value, OutText, OutError, Depth + 1))
				{
					return false;
				}
			}
			OutText.AppendChar(TEXT('}'));
			return true;
		}
		default:
			OutError = TEXT("canonical-unknown-type");
			return false;
		}
	}

	FNativeCanonicalValueRef CloneValue(const FNativeCanonicalValue& Source)
	{
		switch (Source.GetType())
		{
		case FNativeCanonicalValue::EType::Null:
			return FNativeCanonicalValue::MakeNull();
		case FNativeCanonicalValue::EType::Bool:
			return FNativeCanonicalValue::MakeBool(Source.GetBool());
		case FNativeCanonicalValue::EType::Integer:
			return FNativeCanonicalValue::MakeInteger(Source.GetInteger());
		case FNativeCanonicalValue::EType::String:
			return FNativeCanonicalValue::MakeString(Source.GetString());
		case FNativeCanonicalValue::EType::Array:
		{
			const FNativeCanonicalValueRef Result = FNativeCanonicalValue::MakeArray();
			for (const FNativeCanonicalValueRef& Item : Source.GetArray())
			{
				Result->AddArrayValue(CloneValue(*Item));
			}
			return Result;
		}
		case FNativeCanonicalValue::EType::Object:
		{
			const FNativeCanonicalValueRef Result = FNativeCanonicalValue::MakeObject();
			for (const TPair<FString, FNativeCanonicalValueRef>& Entry : Source.GetObjectValues())
			{
				Result->SetObjectValue(Entry.Key, CloneValue(*Entry.Value));
			}
			return Result;
		}
		default:
			return FNativeCanonicalValue::MakeNull();
		}
	}
}

FNativeCanonicalValueRef FNativeCanonicalValue::MakeNull()
{
	const FNativeCanonicalValueRef Value = MakeShared<FNativeCanonicalValue>();
	Value->Type = EType::Null;
	return Value;
}

FNativeCanonicalValueRef FNativeCanonicalValue::MakeBool(const bool bInValue)
{
	const FNativeCanonicalValueRef Value = MakeShared<FNativeCanonicalValue>();
	Value->Type = EType::Bool;
	Value->BoolValue = bInValue;
	return Value;
}

FNativeCanonicalValueRef FNativeCanonicalValue::MakeInteger(const int64 InValue)
{
	const FNativeCanonicalValueRef Value = MakeShared<FNativeCanonicalValue>();
	Value->Type = EType::Integer;
	Value->IntegerValue = InValue;
	return Value;
}

FNativeCanonicalValueRef FNativeCanonicalValue::MakeString(const FString& InValue)
{
	const FNativeCanonicalValueRef Value = MakeShared<FNativeCanonicalValue>();
	Value->Type = EType::String;
	Value->StringValue = InValue;
	return Value;
}

FNativeCanonicalValueRef FNativeCanonicalValue::MakeArray()
{
	const FNativeCanonicalValueRef Value = MakeShared<FNativeCanonicalValue>();
	Value->Type = EType::Array;
	return Value;
}

FNativeCanonicalValueRef FNativeCanonicalValue::MakeObject()
{
	const FNativeCanonicalValueRef Value = MakeShared<FNativeCanonicalValue>();
	Value->Type = EType::Object;
	return Value;
}

void FNativeCanonicalValue::AddArrayValue(const FNativeCanonicalValueRef& Value)
{
	ArrayValues.Add(Value);
}

void FNativeCanonicalValue::SetObjectValue(const FString& Key, const FNativeCanonicalValueRef& Value)
{
	for (TPair<FString, FNativeCanonicalValueRef>& Entry : ObjectValues)
	{
		if (Entry.Key == Key)
		{
			Entry.Value = Value;
			return;
		}
	}
	ObjectValues.Emplace(Key, Value);
}

const FNativeCanonicalValueRef* FNativeCanonicalValue::FindObjectValue(const FString& Key) const
{
	for (const TPair<FString, FNativeCanonicalValueRef>& Entry : ObjectValues)
	{
		if (Entry.Key == Key)
		{
			return &Entry.Value;
		}
	}
	return nullptr;
}

TSharedPtr<FJsonValue> FNativeCanonicalValue::ToJsonValue() const
{
	switch (Type)
	{
	case EType::Null:
		return MakeShared<FJsonValueNull>();
	case EType::Bool:
		return MakeShared<FJsonValueBoolean>(BoolValue);
	case EType::Integer:
		return MakeShared<FJsonValueNumber>(static_cast<double>(IntegerValue));
	case EType::String:
		return MakeShared<FJsonValueString>(StringValue);
	case EType::Array:
	{
		TArray<TSharedPtr<FJsonValue>> Values;
		Values.Reserve(ArrayValues.Num());
		for (const FNativeCanonicalValueRef& Item : ArrayValues)
		{
			Values.Add(Item->ToJsonValue());
		}
		return MakeShared<FJsonValueArray>(Values);
	}
	case EType::Object:
	{
		const TSharedPtr<FJsonObject> Object = MakeShared<FJsonObject>();
		for (const TPair<FString, FNativeCanonicalValueRef>& Entry : ObjectValues)
		{
			Object->SetField(Entry.Key, Entry.Value->ToJsonValue());
		}
		return MakeShared<FJsonValueObject>(Object);
	}
	default:
		return MakeShared<FJsonValueNull>();
	}
}

TSharedPtr<FJsonObject> FNativeCanonicalValue::ToJsonObject() const
{
	const TSharedPtr<FJsonValue> Value = ToJsonValue();
	return Value.IsValid() ? Value->AsObject() : nullptr;
}

FNativeCanonicalValueRef FNativeCanonicalValue::CloneWithoutKeys(const TSet<FString>& Keys) const
{
	if (Type != EType::Object)
	{
		return CloneValue(*this);
	}
	const FNativeCanonicalValueRef Result = MakeObject();
	for (const TPair<FString, FNativeCanonicalValueRef>& Entry : ObjectValues)
	{
		if (!Keys.Contains(Entry.Key))
		{
			Result->SetObjectValue(Entry.Key, CloneValue(*Entry.Value));
		}
	}
	return Result;
}

FNativeCanonicalValueRef FNativeCanonicalValue::CloneWithoutNestedKey(const FString& ObjectKey, const FString& NestedKey) const
{
	const FNativeCanonicalValueRef Result = CloneWithoutKeys(TSet<FString>());
	if (Type != EType::Object)
	{
		return Result;
	}
	const FNativeCanonicalValueRef* Nested = FindObjectValue(ObjectKey);
	if (Nested == nullptr || (*Nested)->GetType() != EType::Object)
	{
		return Result;
	}
	Result->SetObjectValue(ObjectKey, (*Nested)->CloneWithoutKeys({NestedKey}));
	return Result;
}

bool UEAgentKitCanonical::FromJsonValue(const TSharedPtr<FJsonValue>& Value, FNativeCanonicalValueRef& OutValue, FString& OutError)
{
	if (!Value.IsValid())
	{
		OutError = TEXT("json-value-missing");
		return false;
	}

	switch (Value->Type)
	{
	case EJson::None:
	case EJson::Null:
		OutValue = FNativeCanonicalValue::MakeNull();
		return true;
	case EJson::Boolean:
		OutValue = FNativeCanonicalValue::MakeBool(Value->AsBool());
		return true;
	case EJson::String:
		OutValue = FNativeCanonicalValue::MakeString(Value->AsString());
		return true;
	case EJson::Number:
	{
		const double Number = Value->AsNumber();
		if (!FMath::IsFinite(Number))
		{
			OutError = TEXT("canonical-non-finite-number");
			return false;
		}
		const double Rounded = FMath::RoundToDouble(Number);
		if (Number != Rounded || FMath::Abs(Number) > 9007199254740992.0)
		{
			OutError = TEXT("canonical-unsupported-float");
			return false;
		}
		OutValue = FNativeCanonicalValue::MakeInteger(static_cast<int64>(Rounded));
		return true;
	}
	case EJson::Array:
	{
		const FNativeCanonicalValueRef Result = FNativeCanonicalValue::MakeArray();
		for (const TSharedPtr<FJsonValue>& Item : Value->AsArray())
		{
			FNativeCanonicalValueRef ItemValue = FNativeCanonicalValue::MakeNull();
			if (!FromJsonValue(Item, ItemValue, OutError))
			{
				return false;
			}
			Result->AddArrayValue(ItemValue);
		}
		OutValue = Result;
		return true;
	}
	case EJson::Object:
		return FromJsonObject(Value->AsObject(), OutValue, OutError);
	default:
		OutError = TEXT("canonical-unknown-type");
		return false;
	}
}

bool UEAgentKitCanonical::FromJsonObject(const TSharedPtr<FJsonObject>& Object, FNativeCanonicalValueRef& OutValue, FString& OutError)
{
	if (!Object.IsValid())
	{
		OutError = TEXT("json-object-missing");
		return false;
	}
	const FNativeCanonicalValueRef Result = FNativeCanonicalValue::MakeObject();
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Object->Values)
	{
		FNativeCanonicalValueRef EntryValue = FNativeCanonicalValue::MakeNull();
		if (!FromJsonValue(Entry.Value, EntryValue, OutError))
		{
			return false;
		}
		Result->SetObjectValue(Entry.Key, EntryValue);
	}
	OutValue = Result;
	return true;
}

int32 UEAgentKitCanonical::CompareUtf8(const FString& A, const FString& B)
{
	FTCHARToUTF8 Utf8A(*A);
	FTCHARToUTF8 Utf8B(*B);
	const int32 LengthA = Utf8A.Length();
	const int32 LengthB = Utf8B.Length();
	const int32 Common = FMath::Min(LengthA, LengthB);
	if (Common > 0)
	{
		const int32 Compare = FMemory::Memcmp(Utf8A.Get(), Utf8B.Get(), Common);
		if (Compare != 0)
		{
			return Compare;
		}
	}
	return LengthA - LengthB;
}

bool UEAgentKitCanonical::Serialize(const FNativeCanonicalValueRef& Value, FString& OutText, FString& OutError)
{
	OutText.Reset();
	OutText.Reserve(1024);
	return SerializeValue(Value, OutText, OutError, 0);
}

bool UEAgentKitCanonical::DomainHash(const FNativeCanonicalValueRef& Value, const FString& Domain, FString& OutHash, FString& OutError)
{
	if (Domain.IsEmpty() || Domain.Contains(TEXT("\n")))
	{
		OutError = TEXT("canonical-invalid-domain");
		return false;
	}

	FString CanonicalText;
	if (!Serialize(Value, CanonicalText, OutError))
	{
		return false;
	}

	FTCHARToUTF8 DomainUtf8(*Domain);
	FTCHARToUTF8 TextUtf8(*CanonicalText);
	TArray<uint8> Bytes;
	Bytes.Reserve(DomainUtf8.Length() + 1 + TextUtf8.Length());
	Bytes.Append(reinterpret_cast<const uint8*>(DomainUtf8.Get()), DomainUtf8.Length());
	Bytes.Add(0x0a);
	Bytes.Append(reinterpret_cast<const uint8*>(TextUtf8.Get()), TextUtf8.Length());

	FString Hex;
	if (!FBlueprintContextSha256::HashUtf8Bytes(Bytes.GetData(), Bytes.Num(), Hex))
	{
		OutError = TEXT("canonical-hash-failed");
		return false;
	}
	OutHash = FString(TEXT("sha256:")) + Hex;
	return true;
}

bool UEAgentKitCanonical::Fingerprint(
	const FNativeCanonicalValueRef& Value,
	const FString& Domain,
	const int32 Length,
	FString& OutHash,
	FString& OutError)
{
	if (Length < 8 || Length > 64)
	{
		OutError = TEXT("canonical-invalid-fingerprint-length");
		return false;
	}
	FString Full;
	if (!DomainHash(Value, Domain, Full, OutError))
	{
		return false;
	}
	OutHash = Full.Left(7 + Length);
	return true;
}
