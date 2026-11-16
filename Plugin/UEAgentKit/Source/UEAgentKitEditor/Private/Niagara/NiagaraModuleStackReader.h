#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UNiagaraSystem;
class FNiagaraSystemViewModel;

/**
 * Niagara Module Stack reader plus the two stack helpers the N2 writer shares with
 * it, so both slices load the asset and build the view model the same way.
 *
 * Nothing in the reader writes: no transactions, no SetLocalValue, no Save.
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
	 * Resolve a /Game path to a loaded UNiagaraSystem.
	 *
	 * Accepts both a package path and an exact object path.  Loading queues the
	 * engine's post-load compile, so this waits for it (a readiness wait, not an
	 * edit) and fails with SystemCompiling when it still has work outstanding.
	 */
	UNiagaraSystem* ResolveSystem(const FString& SystemPath, EReadFailure& OutFailure, FString& OutMessage);

	/** Build the data-processing only view model used by both the reader and the writer. */
	TSharedPtr<FNiagaraSystemViewModel> CreateStackViewModel(UNiagaraSystem& System);

	/** Destroy a view model built by CreateStackViewModel. */
	void ReleaseStackViewModel(TSharedPtr<FNiagaraSystemViewModel>& ViewModel);

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
