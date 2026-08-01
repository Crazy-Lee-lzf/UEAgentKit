#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonValue.h"
#include "LiveWriteTransaction.h"

class FNumericProperty;
class FProperty;
class UObject;
class UPackage;

namespace UEAgentKitLiveWrite
{
	bool IsUnsignedIntegerProperty(const FNumericProperty* Property);
	bool IsSafeTopLevelPropertyPath(const FString& PropertyPath);
	bool IsSafeLiveWriteSelector(const FString& Selector);
	bool ReadScalarValue(FProperty* Property, const void* ValueAddress, TSharedPtr<FJsonValue>& OutValue);
	bool SetScalarValue(
		FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutError);

	TSharedPtr<FLiveWriteTransactionRecord> BuildLiveWriteTransactionRecord(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& Operation,
		const FString& ValueKind,
		const FString& SessionId,
		FLiveWriteEvidence& Evidence,
		TUniquePtr<ILiveWriteValueIO>& IO);
}
