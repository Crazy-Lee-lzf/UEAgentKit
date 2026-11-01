#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "LiveWriteOperationRegistry.h"
#include "LiveWriteTransaction.h"

class UObject;

namespace UEAgentKitEditorBridgePrivate
{
	/**
	 * Read the current value of one retained live write through its own IO.
	 *
	 * Material Instance and DataTable writes keep exact target state in the
	 * retained transaction record; reading through that record re-resolves the
	 * target and uses the same read path Undo/Discard already trust, so fast
	 * verify does not need a second per-asset read implementation.  Every
	 * identity check fails closed.
	 */
	bool ReadRetainedLiveWriteFastValue(
		const FString& SessionId,
		const FString& Operation,
		const FString& AssetPath,
		const FString& TransactionId,
		UObject* Asset,
		const TMap<FString, TMap<FGuid, TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>>>& Records,
		const TSharedRef<FJsonObject>& OutResult,
		TSharedPtr<FJsonValue>& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage);
}
