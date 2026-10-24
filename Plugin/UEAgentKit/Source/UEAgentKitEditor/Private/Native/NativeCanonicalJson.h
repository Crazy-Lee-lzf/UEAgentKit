// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

/**
 * Canonical JSON subset used by the existing Python contract
 * (``graph_contracts/canonical.py``): UTF-8 key ordering, no insignificant
 * whitespace, ``ensure_ascii=False`` escaping and integer/boolean/null support.
 *
 * Floats are deliberately rejected instead of approximated: R1 descriptors do
 * not contain floating point values, and a wrong ``.17g`` rendering would
 * silently break actionId parity.
 */
class FNativeCanonicalValue;

using FNativeCanonicalValueRef = TSharedRef<FNativeCanonicalValue>;

class FNativeCanonicalValue
{
public:
	enum class EType : uint8
	{
		Null = 0,
		Bool = 1,
		Integer = 2,
		String = 3,
		Array = 4,
		Object = 5,
	};

	static FNativeCanonicalValueRef MakeNull();
	static FNativeCanonicalValueRef MakeBool(bool bInValue);
	static FNativeCanonicalValueRef MakeInteger(int64 InValue);
	static FNativeCanonicalValueRef MakeString(const FString& InValue);
	static FNativeCanonicalValueRef MakeArray();
	static FNativeCanonicalValueRef MakeObject();

	EType GetType() const { return Type; }
	bool GetBool() const { return BoolValue; }
	int64 GetInteger() const { return IntegerValue; }
	const FString& GetString() const { return StringValue; }
	const TArray<FNativeCanonicalValueRef>& GetArray() const { return ArrayValues; }
	const TArray<TPair<FString, FNativeCanonicalValueRef>>& GetObjectValues() const { return ObjectValues; }

	void AddArrayValue(const FNativeCanonicalValueRef& Value);
	void SetObjectValue(const FString& Key, const FNativeCanonicalValueRef& Value);
	const FNativeCanonicalValueRef* FindObjectValue(const FString& Key) const;

	/** JSON output mirror; not used for hashing. */
	TSharedPtr<FJsonValue> ToJsonValue() const;
	TSharedPtr<FJsonObject> ToJsonObject() const;

	/** Removes a top level key (no-op for other types). */
	FNativeCanonicalValueRef CloneWithoutKeys(const TSet<FString>& Keys) const;

	/** Removes ``NestedKey`` from the object stored under ``ObjectKey``. */
	FNativeCanonicalValueRef CloneWithoutNestedKey(const FString& ObjectKey, const FString& NestedKey) const;

private:
	EType Type = EType::Null;
	bool BoolValue = false;
	int64 IntegerValue = 0;
	FString StringValue;
	TArray<FNativeCanonicalValueRef> ArrayValues;
	TArray<TPair<FString, FNativeCanonicalValueRef>> ObjectValues;
};

namespace UEAgentKitCanonical
{
	/** Strict JSON -> canonical conversion; fails on floats, NaN and non-scalar strings. */
	bool FromJsonValue(const TSharedPtr<FJsonValue>& Value, FNativeCanonicalValueRef& OutValue, FString& OutError);

	bool FromJsonObject(const TSharedPtr<FJsonObject>& Object, FNativeCanonicalValueRef& OutValue, FString& OutError);

	/** Byte-exact canonical JSON text (see canonical.py). */
	bool Serialize(const FNativeCanonicalValueRef& Value, FString& OutText, FString& OutError);

	/** sha256("<domain>\n" + canonical_json(value)) with the ``sha256:`` prefix. */
	bool DomainHash(const FNativeCanonicalValueRef& Value, const FString& Domain, FString& OutHash, FString& OutError);

	/** ``sha256:`` + first ``Length`` hex characters of the domain hash. */
	bool Fingerprint(const FNativeCanonicalValueRef& Value, const FString& Domain, int32 Length, FString& OutHash, FString& OutError);

	/** UTF-8 byte ordering (identical to Python's code point ordering for valid strings). */
	int32 CompareUtf8(const FString& A, const FString& B);
}
