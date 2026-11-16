#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

/**
 * Read-only Niagara Module Stack reader.
 *
 * Walks the Niagara Editor stack view models so that the reported module order is
 * the real stack order the engine builds, not an order derived from asset data.
 *
 * Nothing in this file writes: no transactions, no SetLocalValue, no Save.
 */
namespace UEAgentKitNiagaraStack
{
	enum class EReadFailure : uint8
	{
		None,
		InvalidRequest,
		SystemNotFound,
		SystemCompiling,
		StackUnavailable,
	};

	struct FReadRequest
	{
		/** Exact /Game package path of the Niagara System. */
		FString SystemPath;

		/** When false only module identity/enabled/order is reported. */
		bool bIncludeInputs = true;

		/** Bound per emitter (not global) so one huge emitter cannot starve the rest. */
		int32 MaxModules = 128;

		/** Bound per module. */
		int32 MaxInputsPerModule = 32;
	};

	/** Wire error code for a failure kind. */
	const TCHAR* FailureCode(EReadFailure Failure);

	/**
	 * Read the module stack of one Niagara System.
	 *
	 * On success OutResult holds the full payload and true is returned.
	 * On failure OutFailure/OutMessage describe the reason and false is returned.
	 */
	bool ReadModuleStack(
		const FReadRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		EReadFailure& OutFailure,
		FString& OutMessage);
}
