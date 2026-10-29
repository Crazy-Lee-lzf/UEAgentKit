#include "Native/NativeCursorCodec.h"

#include "BlueprintContextSha256.h"
#include "Native/NativeCanonicalJson.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	const TCHAR* const CursorErrorCode = TEXT("native-schema-invalid-arguments");

	void SetCursorError(FString& OutErrorCode, FString& OutErrorMessage, const TCHAR* Message)
	{
		OutErrorCode = CursorErrorCode;
		OutErrorMessage = Message;
	}

	bool IsBoundedIdentifier(const FString& Value)
	{
		return !Value.IsEmpty() && Value.Len() <= 128;
	}

	int32 Base64UrlValue(const TCHAR Character)
	{
		if (Character >= TEXT('A') && Character <= TEXT('Z')) { return Character - TEXT('A'); }
		if (Character >= TEXT('a') && Character <= TEXT('z')) { return Character - TEXT('a') + 26; }
		if (Character >= TEXT('0') && Character <= TEXT('9')) { return Character - TEXT('0') + 52; }
		if (Character == TEXT('-')) { return 62; }
		if (Character == TEXT('_')) { return 63; }
		return INDEX_NONE;
	}

	bool ReadIntegerField(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Field,
		int64& OutValue)
	{
		const TSharedPtr<FJsonValue>* Found = Object->Values.Find(Field);
		if (Found == nullptr || !Found->IsValid() || (*Found)->Type != EJson::Number)
		{
			return false;
		}
		const double Number = (*Found)->AsNumber();
		if (!FMath::IsFinite(Number) || Number != FMath::RoundToDouble(Number))
		{
			return false;
		}
		OutValue = static_cast<int64>(Number);
		return true;
	}

	bool ReadStringField(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Field,
		FString& OutValue)
	{
		const TSharedPtr<FJsonValue>* Found = Object->Values.Find(Field);
		if (Found == nullptr || !Found->IsValid() || (*Found)->Type != EJson::String)
		{
			return false;
		}
		OutValue = (*Found)->AsString();
		return true;
	}

	bool ConstantTimeEqual16(const uint8* Left, const uint8* Right)
	{
		uint8 Difference = 0;
		for (int32 Index = 0; Index < 16; ++Index)
		{
			Difference |= static_cast<uint8>(Left[Index] ^ Right[Index]);
		}
		return Difference == 0;
	}
}

FString UEAgentKitNativeCursor::Base64UrlEncode(const uint8* Data, const int64 Length)
{
	static const TCHAR* Alphabet = TEXT("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_");
	FString Result;
	if (Data == nullptr || Length <= 0)
	{
		return Result;
	}
	Result.Reserve(static_cast<int32>((Length * 4 + 2) / 3));
	int64 Index = 0;
	while (Index + 3 <= Length)
	{
		const uint32 Value = (static_cast<uint32>(Data[Index]) << 16)
			| (static_cast<uint32>(Data[Index + 1]) << 8)
			| static_cast<uint32>(Data[Index + 2]);
		Result.AppendChar(Alphabet[(Value >> 18) & 0x3f]);
		Result.AppendChar(Alphabet[(Value >> 12) & 0x3f]);
		Result.AppendChar(Alphabet[(Value >> 6) & 0x3f]);
		Result.AppendChar(Alphabet[Value & 0x3f]);
		Index += 3;
	}
	const int64 Remaining = Length - Index;
	if (Remaining == 1)
	{
		const uint32 Value = static_cast<uint32>(Data[Index]) << 16;
		Result.AppendChar(Alphabet[(Value >> 18) & 0x3f]);
		Result.AppendChar(Alphabet[(Value >> 12) & 0x3f]);
	}
	else if (Remaining == 2)
	{
		const uint32 Value = (static_cast<uint32>(Data[Index]) << 16)
			| (static_cast<uint32>(Data[Index + 1]) << 8);
		Result.AppendChar(Alphabet[(Value >> 18) & 0x3f]);
		Result.AppendChar(Alphabet[(Value >> 12) & 0x3f]);
		Result.AppendChar(Alphabet[(Value >> 6) & 0x3f]);
	}
	return Result;
}

bool UEAgentKitNativeCursor::Base64UrlDecode(const FString& Text, TArray<uint8>& OutBytes, FString& OutErrorCode)
{
	OutBytes.Reset();
	if (Text.IsEmpty() || Text.Len() > 8192 || Text.Contains(TEXT("=")))
	{
		OutErrorCode = CursorErrorCode;
		return false;
	}
	if (Text.Len() % 4 == 1)
	{
		OutErrorCode = CursorErrorCode;
		return false;
	}
	uint32 Accumulator = 0;
	int32 Bits = 0;
	for (const TCHAR Character : Text)
	{
		const int32 Value = Base64UrlValue(Character);
		if (Value == INDEX_NONE)
		{
			OutErrorCode = CursorErrorCode;
			return false;
		}
		Accumulator = (Accumulator << 6) | static_cast<uint32>(Value);
		Bits += 6;
		if (Bits >= 8)
		{
			Bits -= 8;
			OutBytes.Add(static_cast<uint8>((Accumulator >> Bits) & 0xff));
		}
		if (OutBytes.Num() > MaxPayloadBytes)
		{
			OutErrorCode = CursorErrorCode;
			return false;
		}
	}
	// Reject non-canonical encodings: different trailing padding bits could
	// otherwise decode to the same bytes and produce an apparently valid MAC.
	if (Base64UrlEncode(OutBytes.GetData(), OutBytes.Num()) != Text)
	{
		OutErrorCode = CursorErrorCode;
		return false;
	}
	return true;
}

bool UEAgentKitNativeCursor::Encode(
	const FNativeCursorPayload& Payload,
	const TArray<uint8>& Key,
	FString& OutToken,
	FString& OutErrorCode)
{
	OutToken.Reset();
	OutErrorCode.Reset();
	if (Payload.Version != CursorVersion
		|| (Payload.Kind != TEXT("catalog") && Payload.Kind != TEXT("context"))
		|| !IsBoundedIdentifier(Payload.QueryHash)
		|| !IsBoundedIdentifier(Payload.SetFingerprint)
		|| !IsBoundedIdentifier(Payload.Epoch)
		|| Payload.Offset < 0
		|| Payload.Total < 0
		|| Payload.Offset > Payload.Total
		|| Payload.PageSize < 1
		|| Payload.PageSize > MaxPageSize
		|| Payload.IssuedAt <= 0
		|| Payload.ExpiresAt < Payload.IssuedAt
		|| Key.Num() < 32)
	{
		OutErrorCode = CursorErrorCode;
		return false;
	}

	using namespace UEAgentKitCanonical;
	const FNativeCanonicalValueRef Object = FNativeCanonicalValue::MakeObject();
	Object->SetObjectValue(TEXT("v"), FNativeCanonicalValue::MakeInteger(Payload.Version));
	Object->SetObjectValue(TEXT("k"), FNativeCanonicalValue::MakeString(Payload.Kind));
	Object->SetObjectValue(TEXT("q"), FNativeCanonicalValue::MakeString(Payload.QueryHash));
	Object->SetObjectValue(TEXT("s"), FNativeCanonicalValue::MakeString(Payload.SetFingerprint));
	Object->SetObjectValue(TEXT("e"), FNativeCanonicalValue::MakeString(Payload.Epoch));
	Object->SetObjectValue(TEXT("o"), FNativeCanonicalValue::MakeInteger(Payload.Offset));
	Object->SetObjectValue(TEXT("n"), FNativeCanonicalValue::MakeInteger(Payload.Total));
	Object->SetObjectValue(TEXT("p"), FNativeCanonicalValue::MakeInteger(Payload.PageSize));
	Object->SetObjectValue(TEXT("iat"), FNativeCanonicalValue::MakeInteger(Payload.IssuedAt));
	Object->SetObjectValue(TEXT("exp"), FNativeCanonicalValue::MakeInteger(Payload.ExpiresAt));

	FString PayloadText;
	FString CanonicalError;
	if (!Serialize(Object, PayloadText, CanonicalError))
	{
		OutErrorCode = CursorErrorCode;
		return false;
	}
	FTCHARToUTF8 PayloadUtf8(*PayloadText);
	if (PayloadUtf8.Length() <= 0 || PayloadUtf8.Length() > MaxPayloadBytes)
	{
		OutErrorCode = CursorErrorCode;
		return false;
	}

	const FString PayloadBase64 = Base64UrlEncode(
		reinterpret_cast<const uint8*>(PayloadUtf8.Get()),
		PayloadUtf8.Length());
	const FString MacInput = FString(TEXT("v1.")) + PayloadBase64;
	FTCHARToUTF8 MacUtf8(*MacInput);
	uint8 Mac[32];
	if (!FBlueprintContextSha256::HmacSha256(
			Key.GetData(),
			Key.Num(),
			reinterpret_cast<const uint8*>(MacUtf8.Get()),
			MacUtf8.Length(),
			Mac))
	{
		OutErrorCode = CursorErrorCode;
		return false;
	}
	const FString MacBase64 = Base64UrlEncode(Mac, MacBytes);
	OutToken = FString(TEXT("v1.")) + PayloadBase64 + TEXT(".") + MacBase64;
	if (OutToken.Len() > MaxCursorLength)
	{
		OutToken.Reset();
		OutErrorCode = CursorErrorCode;
		return false;
	}
	return true;
}

bool UEAgentKitNativeCursor::Decode(
	const FString& Token,
	const TArray<uint8>& Key,
	const int64 NowUnix,
	FNativeCursorPayload& OutPayload,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	OutPayload = FNativeCursorPayload();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();
	if (Token.IsEmpty() || Token.Len() > MaxCursorLength || Key.Num() < 32)
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor is invalid"));
		return false;
	}
	TArray<FString> Parts;
	Token.ParseIntoArray(Parts, TEXT("."), false);
	if (Parts.Num() != 3 || Parts[0] != TEXT("v1") || Parts[1].IsEmpty() || Parts[2].IsEmpty())
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor is malformed"));
		return false;
	}

	TArray<uint8> PayloadBytes;
	if (!Base64UrlDecode(Parts[1], PayloadBytes, OutErrorCode))
	{
		OutErrorMessage = TEXT("cursor is malformed");
		return false;
	}
	TArray<uint8> MacRaw;
	if (!Base64UrlDecode(Parts[2], MacRaw, OutErrorCode) || MacRaw.Num() != MacBytes)
	{
		OutErrorMessage = TEXT("cursor is malformed");
		return false;
	}

	const FString MacInput = FString(TEXT("v1.")) + Parts[1];
	FTCHARToUTF8 MacUtf8(*MacInput);
	uint8 ExpectedMac[32];
	if (!FBlueprintContextSha256::HmacSha256(
			Key.GetData(),
			Key.Num(),
			reinterpret_cast<const uint8*>(MacUtf8.Get()),
			MacUtf8.Length(),
			ExpectedMac))
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor authentication failed"));
		return false;
	}
	if (!ConstantTimeEqual16(ExpectedMac, MacRaw.GetData()))
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor authentication failed"));
		return false;
	}

	FUTF8ToTCHAR Converted(reinterpret_cast<const ANSICHAR*>(PayloadBytes.GetData()), PayloadBytes.Num());
	const FString PayloadText(Converted.Length(), Converted.Get());
	TSharedPtr<FJsonObject> Object;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(PayloadText);
	if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid())
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor payload is invalid"));
		return false;
	}

	static const TSet<FString> AllowedKeys = {
		TEXT("v"), TEXT("k"), TEXT("q"), TEXT("s"), TEXT("e"),
		TEXT("o"), TEXT("n"), TEXT("p"), TEXT("iat"), TEXT("exp"),
	};
	if (Object->Values.Num() != AllowedKeys.Num())
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor payload has unknown fields"));
		return false;
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Object->Values)
	{
		if (!AllowedKeys.Contains(Pair.Key))
		{
			SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor payload has unknown fields"));
			return false;
		}
	}

	int64 Version = 0;
	int64 Offset = 0;
	int64 Total = 0;
	int64 PageSize = 0;
	int64 IssuedAt = 0;
	int64 ExpiresAt = 0;
	if (!ReadIntegerField(Object, TEXT("v"), Version)
		|| !ReadIntegerField(Object, TEXT("o"), Offset)
		|| !ReadIntegerField(Object, TEXT("n"), Total)
		|| !ReadIntegerField(Object, TEXT("p"), PageSize)
		|| !ReadIntegerField(Object, TEXT("iat"), IssuedAt)
		|| !ReadIntegerField(Object, TEXT("exp"), ExpiresAt)
		|| !ReadStringField(Object, TEXT("k"), OutPayload.Kind)
		|| !ReadStringField(Object, TEXT("q"), OutPayload.QueryHash)
		|| !ReadStringField(Object, TEXT("s"), OutPayload.SetFingerprint)
		|| !ReadStringField(Object, TEXT("e"), OutPayload.Epoch))
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor payload is invalid"));
		return false;
	}

	if (Version != CursorVersion
		|| (OutPayload.Kind != TEXT("catalog") && OutPayload.Kind != TEXT("context"))
		|| !IsBoundedIdentifier(OutPayload.QueryHash)
		|| !IsBoundedIdentifier(OutPayload.SetFingerprint)
		|| !IsBoundedIdentifier(OutPayload.Epoch)
		|| Offset < 0
		|| Total < 0
		|| Total > 1000000
		|| Offset > Total
		|| PageSize < 1
		|| PageSize > MaxPageSize
		|| IssuedAt <= 0
		|| ExpiresAt < IssuedAt
		|| ExpiresAt - IssuedAt > 86400)
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor payload is invalid"));
		return false;
	}
	if (NowUnix > ExpiresAt)
	{
		SetCursorError(OutErrorCode, OutErrorMessage, TEXT("cursor is expired"));
		return false;
	}

	OutPayload.Version = static_cast<int32>(Version);
	OutPayload.Offset = static_cast<int32>(Offset);
	OutPayload.Total = static_cast<int32>(Total);
	OutPayload.PageSize = static_cast<int32>(PageSize);
	OutPayload.IssuedAt = IssuedAt;
	OutPayload.ExpiresAt = ExpiresAt;
	return true;
}
