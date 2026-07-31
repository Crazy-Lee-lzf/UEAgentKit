#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "UObject/UnrealType.h"

class UObject;
class UPackage;

namespace UEAgentKitLiveWrite
{
	// Deep-copy snapshot of a property value used to restore the exact pre-write state.
	class FLiveWriteSnapshot
	{
	public:
		FLiveWriteSnapshot() = default;
		~FLiveWriteSnapshot();

		FLiveWriteSnapshot(const FLiveWriteSnapshot&) = delete;
		FLiveWriteSnapshot& operator=(const FLiveWriteSnapshot&) = delete;

		bool Capture(const FProperty* InProperty, const void* Source);
		bool IsValid() const { return Property != nullptr && Storage != nullptr; }
		void Restore(void* Destination) const;

	private:
		const FProperty* Property = nullptr;
		void* Storage = nullptr;
	};

	struct FLiveWriteContext
	{
		UObject* Asset = nullptr;
		UPackage* Package = nullptr;
		FProperty* Property = nullptr;
		void* ValueAddress = nullptr;
		FString SessionId;
		FString TransactionTitle;
		FString AssetPath;
		FString PropertyPath;
		TSharedPtr<FJsonValue> Value;
	};

	struct FLiveWriteEvidence
	{
		bool bChanged = false;
		bool bTransactionRecorded = false;
		FString TransactionTitle;
		bool bPackageDirtyBefore = false;
		bool bPackageDirtyAfter = false;
		bool bSaved = false;
		TSharedPtr<FJsonValue> BeforeValue;
		TSharedPtr<FJsonValue> AfterValue;
	};

	// Per-value-kind IO contract. Each callback must set a stable error code and message on failure.
	class ILiveWriteValueIO
	{
	public:
		virtual ~ILiveWriteValueIO() = default;

		virtual bool ReadBefore(
			FProperty* Property,
			void* ValueAddress,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) = 0;

		virtual bool ApplyValue(
			FProperty* Property,
			void* ValueAddress,
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) = 0;

		virtual bool ReadAfter(
			FProperty* Property,
			void* ValueAddress,
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) = 0;

		virtual bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) = 0;
	};

	// Runs the unified live write transaction lifecycle.
	// Returns true on success (changed or semantic no-op); false on failure with a stable error.
	bool RunLiveWriteTransaction(
		const FLiveWriteContext& Context,
		ILiveWriteValueIO& IO,
		FLiveWriteEvidence& OutEvidence,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// Fills the evidence fields shared by the live write operations.
	// bIncludeContext adds packageName/classPath/transactionTitle/assetOpen/loadedByBridge.
	// bIncludeDirtyPair adds dirtyBefore/dirtyAfter.
	void FillLiveWriteEvidence(
		TSharedRef<FJsonObject>& Result,
		const FLiveWriteContext& Context,
		const FLiveWriteEvidence& Evidence,
		const FString& Operation,
		const FString& ValueKind,
		const FString& PropertyType,
		bool bIncludeContext,
		bool bIncludeDirtyPair);
}
