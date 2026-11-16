#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

/**
 * Niagara Module Input writer (N2).
 *
 * Only inputs whose value source is Local are writable.  Every other source is
 * refused with the real source reported, so a Linked parameter is never silently
 * replaced by a constant.
 *
 * The writer never saves.  It reports requiresSave=true and leaves the package
 * dirty; an explicit Save is a separate authorised action.
 */
namespace UEAgentKitNiagaraStack
{
	enum class EWriteFailure : uint8
	{
		None,
		InvalidRequest,
		SystemNotFound,
		SystemCompiling,
		StackUnavailable,
		EmitterNotFound,
		ModuleNotFound,
		InputNotFound,
		InputAmbiguous,
		InputNotLocal,
		TypeUnsupported,
		ValueInvalid,
		Stale,
		WriteFailed,
	};

	struct FWriteRequest
	{
		FString SystemPath;

		/** Exactly one of these identifies the emitter. */
		FString EmitterName;
		FString EmitterHandleId;

		/** Optional execution stage used to disambiguate equal module names. */
		FString Stage;

		FString ModuleName;
		int32 ModuleOccurrence = 0;

		FString InputName;

		/** New value, interpreted against the input's own type. */
		TSharedPtr<FJsonValue> Value;

		/** Defaults to Local; any other expected mode is a refusal, not a conversion. */
		FString ExpectedValueMode = TEXT("Local");

		/** Optional drift guard; a mismatch is refused as stale with zero side effects. */
		TSharedPtr<FJsonValue> ExpectedOldValue;

		/** Prove the edit is a single Undo/Redo step by actually undoing and redoing it. */
		bool bVerifyUndo = true;

		/** Bounded wait for the follow-up compile. */
		double CompileTimeoutSeconds = 30.0;
	};

	const TCHAR* WriteFailureCode(EWriteFailure Failure);

	/**
	 * Set one Local module input value.
	 *
	 * On success OutResult holds the wire payload and true is returned.  On any
	 * failure nothing is written, the package is not dirtied and no transaction is
	 * left in the undo stack.
	 */
	bool SetModuleInput(
		const FWriteRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		EWriteFailure& OutFailure,
		FString& OutMessage);
}
