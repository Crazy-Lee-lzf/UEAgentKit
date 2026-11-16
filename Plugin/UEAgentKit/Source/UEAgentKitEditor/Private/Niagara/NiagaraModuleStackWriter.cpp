#include "Niagara/NiagaraModuleStackWriter.h"

#include "Niagara/NiagaraInputValue.h"
#include "Niagara/NiagaraModuleStackReader.h"

#include "NiagaraCommon.h"
#include "NiagaraScript.h"
#include "NiagaraSystem.h"
#include "NiagaraTypes.h"

#include "NiagaraNodeFunctionCall.h"
#include "NiagaraNodeOutput.h"

#include "ViewModels/NiagaraEmitterHandleViewModel.h"
#include "ViewModels/NiagaraSystemViewModel.h"
#include "ViewModels/Stack/NiagaraParameterHandle.h"
#include "ViewModels/Stack/NiagaraStackEntry.h"
#include "ViewModels/Stack/NiagaraStackFunctionInput.h"
#include "ViewModels/Stack/NiagaraStackModuleItem.h"
#include "ViewModels/Stack/NiagaraStackScriptItemGroup.h"
#include "ViewModels/Stack/NiagaraStackViewModel.h"

#include "Editor.h"
#include "Editor/EditorEngine.h"
#include "Editor/Transactor.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "ScopedTransaction.h"
#include "UObject/Package.h"
#include "UObject/StructOnScope.h"

namespace UEAgentKitNiagaraStack
{
	namespace
	{
		const TCHAR* const StageOtherName = TEXT("Other");

		const TCHAR* ValueModeName(const UNiagaraStackFunctionInput::EValueMode Mode)
		{
			switch (Mode)
			{
			case UNiagaraStackFunctionInput::EValueMode::Local: return TEXT("Local");
			case UNiagaraStackFunctionInput::EValueMode::Linked: return TEXT("Linked");
			case UNiagaraStackFunctionInput::EValueMode::Dynamic: return TEXT("Dynamic");
			case UNiagaraStackFunctionInput::EValueMode::Data: return TEXT("Data");
			case UNiagaraStackFunctionInput::EValueMode::ObjectAsset: return TEXT("ObjectAsset");
			case UNiagaraStackFunctionInput::EValueMode::Expression: return TEXT("Expression");
			case UNiagaraStackFunctionInput::EValueMode::DefaultFunction: return TEXT("DefaultFunction");
			case UNiagaraStackFunctionInput::EValueMode::InvalidOverride: return TEXT("InvalidOverride");
			case UNiagaraStackFunctionInput::EValueMode::UnsupportedDefault: return TEXT("UnsupportedDefault");
			case UNiagaraStackFunctionInput::EValueMode::None: break;
			}
			return TEXT("None");
		}

		const TCHAR* StageNameForUsage(const ENiagaraScriptUsage Usage)
		{
			switch (Usage)
			{
			case ENiagaraScriptUsage::SystemSpawnScript: return TEXT("SystemSpawn");
			case ENiagaraScriptUsage::SystemUpdateScript: return TEXT("SystemUpdate");
			case ENiagaraScriptUsage::EmitterSpawnScript: return TEXT("EmitterSpawn");
			case ENiagaraScriptUsage::EmitterUpdateScript: return TEXT("EmitterUpdate");
			case ENiagaraScriptUsage::ParticleSpawnScript:
			case ENiagaraScriptUsage::ParticleSpawnScriptInterpolated: return TEXT("ParticleSpawn");
			case ENiagaraScriptUsage::ParticleUpdateScript: return TEXT("ParticleUpdate");
			default: return StageOtherName;
			}
		}

		struct FLocatedInput
		{
			UNiagaraStackModuleItem* ModuleItem = nullptr;
			UNiagaraStackFunctionInput* Input = nullptr;
			FString StageName;
			FString ModuleName;
			FString EmitterName;
			FString EmitterHandleId;
		};

		bool EqualsLoose(const FString& Left, const FString& Right)
		{
			return Left.Equals(Right, ESearchCase::IgnoreCase);
		}

		/** Walk one root and locate the requested module, then the requested input. */
		void FindModuleAndInput(
			UNiagaraStackEntry& Entry,
			const FString& CurrentStage,
			const FWriteRequest& Request,
			int32& RemainingOccurrence,
			FLocatedInput& OutLocated,
			bool& bOutFound,
			bool& bOutAmbiguous)
		{
			if (bOutFound)
			{
				return;
			}

			FString Stage = CurrentStage;
			if (const UNiagaraStackScriptItemGroup* ScriptGroup = Cast<UNiagaraStackScriptItemGroup>(&Entry))
			{
				if (const UNiagaraNodeOutput* OutputNode = ScriptGroup->GetScriptOutputNode())
				{
					Stage = StageNameForUsage(OutputNode->GetUsage());
				}
			}

			if (UNiagaraStackModuleItem* ModuleItem = Cast<UNiagaraStackModuleItem>(&Entry))
			{
				const FString ModuleName = ModuleItem->GetDisplayName().ToString();
				const bool bStageMatches = Request.Stage.IsEmpty() || EqualsLoose(Request.Stage, Stage);
				if (bStageMatches && EqualsLoose(Request.ModuleName, ModuleName))
				{
					if (RemainingOccurrence == 0)
					{
						// Found the module; resolve the input inside it.
						TArray<UNiagaraStackFunctionInput*> Inputs;
						ModuleItem->GetParameterInputs(Inputs);

						TArray<UNiagaraStackFunctionInput*> HandleMatches;
						TArray<UNiagaraStackFunctionInput*> NameMatches;
						for (UNiagaraStackFunctionInput* Input : Inputs)
						{
							if (Input == nullptr)
							{
								continue;
							}
							const FString HandleString =
								Input->GetInputParameterHandle().GetParameterHandleString().ToString();
							if (HandleString.Equals(Request.InputName, ESearchCase::CaseSensitive)
								|| EqualsLoose(HandleString, Request.InputName))
							{
								HandleMatches.Add(Input);
								continue;
							}
							const FString DisplayName = Input->GetDisplayName().ToString();
							if (DisplayName.Equals(Request.InputName, ESearchCase::CaseSensitive)
								|| EqualsLoose(DisplayName, Request.InputName))
							{
								NameMatches.Add(Input);
							}
						}

						const TArray<UNiagaraStackFunctionInput*>& Matches =
							HandleMatches.Num() > 0 ? HandleMatches : NameMatches;
						if (Matches.Num() > 1)
						{
							bOutAmbiguous = true;
							return;
						}
						if (Matches.Num() == 1)
						{
							OutLocated.ModuleItem = ModuleItem;
							OutLocated.Input = Matches[0];
							OutLocated.StageName = Stage;
							OutLocated.ModuleName = ModuleName;
							bOutFound = true;
							return;
						}
						// The module matched but the input did not: remember it so the
						// caller reports "input not found" rather than "module missing".
						OutLocated.ModuleItem = ModuleItem;
						OutLocated.ModuleName = ModuleName;
						OutLocated.StageName = Stage;
					}
					else
					{
						--RemainingOccurrence;
					}
				}
				return;
			}

			TArray<UNiagaraStackEntry*> Children;
			Entry.GetFilteredChildren(Children);
			for (UNiagaraStackEntry* Child : Children)
			{
				if (Child != nullptr)
				{
					FindModuleAndInput(*Child, Stage, Request, RemainingOccurrence, OutLocated, bOutFound, bOutAmbiguous);
					if (bOutFound || bOutAmbiguous)
					{
						return;
					}
				}
			}
		}

		bool FindInput(
			FNiagaraSystemViewModel& ViewModel,
			const FWriteRequest& Request,
			FLocatedInput& OutLocated,
			EWriteFailure& OutFailure,
			FString& OutMessage)
		{
			const TArray<TSharedRef<FNiagaraEmitterHandleViewModel>>& EmitterHandles =
				ViewModel.GetEmitterHandleViewModels();

			FNiagaraEmitterHandleViewModel* ChosenEmitter = nullptr;
			for (const TSharedRef<FNiagaraEmitterHandleViewModel>& Handle : EmitterHandles)
			{
				const bool bIdMatch = !Request.EmitterHandleId.IsEmpty()
					&& Handle->GetId().ToString(EGuidFormats::DigitsWithHyphensLower)
						.Equals(Request.EmitterHandleId, ESearchCase::IgnoreCase);
				const bool bNameMatch = !Request.EmitterName.IsEmpty()
					&& EqualsLoose(Handle->GetName().ToString(), Request.EmitterName);
				if (bIdMatch || bNameMatch)
				{
					ChosenEmitter = &Handle.Get();
					break;
				}
			}

			if (ChosenEmitter == nullptr)
			{
				OutFailure = EWriteFailure::EmitterNotFound;
				OutMessage = Request.EmitterHandleId.IsEmpty()
					? FString::Printf(TEXT("No emitter named '%s' in '%s'."), *Request.EmitterName, *Request.SystemPath)
					: FString::Printf(TEXT("No emitter with handle id '%s' in '%s'."), *Request.EmitterHandleId, *Request.SystemPath);
				return false;
			}

			UNiagaraStackViewModel* StackViewModel = ChosenEmitter->GetEmitterStackViewModel();
			UNiagaraStackEntry* RootEntry = StackViewModel != nullptr ? StackViewModel->GetRootEntry() : nullptr;
			if (RootEntry == nullptr)
			{
				OutFailure = EWriteFailure::StackUnavailable;
				OutMessage = FString::Printf(
					TEXT("Emitter '%s' has no stack view model."), *ChosenEmitter->GetName().ToString());
				return false;
			}

			RootEntry->RefreshChildren();

			int32 RemainingOccurrence = Request.ModuleOccurrence;
			bool bFound = false;
			bool bAmbiguous = false;
			FindModuleAndInput(*RootEntry, FString(StageOtherName), Request, RemainingOccurrence, OutLocated, bFound, bAmbiguous);

			if (bAmbiguous)
			{
				OutFailure = EWriteFailure::InputAmbiguous;
				OutMessage = FString::Printf(
					TEXT("Module '%s' has more than one input matching '%s'; use the parameter handle."),
					*Request.ModuleName, *Request.InputName);
				return false;
			}

			if (!bFound)
			{
				if (OutLocated.ModuleItem == nullptr)
				{
					OutFailure = EWriteFailure::ModuleNotFound;
					OutMessage = FString::Printf(
						TEXT("No module '%s' (occurrence %d%s) in emitter '%s'."),
						*Request.ModuleName,
						Request.ModuleOccurrence,
						Request.Stage.IsEmpty() ? TEXT("") : *FString::Printf(TEXT(", stage %s"), *Request.Stage),
						*ChosenEmitter->GetName().ToString());
				}
				else
				{
					OutFailure = EWriteFailure::InputNotFound;
					OutMessage = FString::Printf(
						TEXT("Module '%s' has no input named '%s'."), *Request.ModuleName, *Request.InputName);
				}
				return false;
			}

			OutLocated.EmitterName = ChosenEmitter->GetName().ToString();
			OutLocated.EmitterHandleId = ChosenEmitter->GetId().ToString(EGuidFormats::DigitsWithHyphensLower);
			return true;
		}

		/** Read the current Local payload of one input, or why it has none. */
		bool ReadPayload(
			UNiagaraStackFunctionInput& Input,
			TArray<uint8>& OutBytes,
			FString& OutError)
		{
			OutBytes.Reset();
			const FNiagaraTypeDefinition& Type = Input.GetInputType();
			const int32 Size = Type.GetSize();
			if (Size <= 0)
			{
				OutError = FString::Printf(TEXT("Input type '%s' has no payload."), *Type.GetName());
				return false;
			}

			// GetLocalValueStruct() is non-const in 5.6.1 even though it only reads.
			const TSharedPtr<const FStructOnScope> LocalValue = Input.GetLocalValueStruct();
			if (!LocalValue.IsValid() || LocalValue->GetStructMemory() == nullptr)
			{
				OutError = TEXT("Input has no local value.");
				return false;
			}
			OutBytes.Append(LocalValue->GetStructMemory(), Size);
			return true;
		}

		/** Rebuild the stack from scratch and read the value back (used after undo/redo). */
		bool ReadPayloadFresh(
			UNiagaraSystem& System,
			const FWriteRequest& Request,
			TArray<uint8>& OutBytes,
			FString& OutError)
		{
			TSharedPtr<FNiagaraSystemViewModel> ViewModel = CreateStackViewModel(System);
			if (!ViewModel.IsValid())
			{
				OutError = TEXT("The Niagara System view model could not be created.");
				return false;
			}

			FLocatedInput Located;
			EWriteFailure Failure = EWriteFailure::None;
			FString Message;
			const bool bFound = FindInput(*ViewModel, Request, Located, Failure, Message);
			const bool bRead = bFound && ReadPayload(*Located.Input, OutBytes, OutError);
			if (!bFound)
			{
				OutError = Message;
			}
			ReleaseStackViewModel(ViewModel);
			return bRead;
		}

		void WaitForCompilation(UNiagaraSystem& System, const double TimeoutSeconds, bool& bOutTimedOut)
		{
			bOutTimedOut = false;
			const double Deadline = FPlatformTime::Seconds() + TimeoutSeconds;
			while (System.HasOutstandingCompilationRequests())
			{
				System.PollForCompilationComplete(/*bFlushRequestCompile*/ true);
				if (!System.HasOutstandingCompilationRequests())
				{
					return;
				}
				if (FPlatformTime::Seconds() >= Deadline)
				{
					bOutTimedOut = true;
					return;
				}
				FPlatformProcess::Sleep(0.005f);
			}
		}
	}

	const TCHAR* WriteFailureCode(const EWriteFailure Failure)
	{
		switch (Failure)
		{
		case EWriteFailure::InvalidRequest: return TEXT("invalid-request");
		case EWriteFailure::SystemNotFound: return TEXT("niagara-system-not-found");
		case EWriteFailure::SystemCompiling: return TEXT("niagara-system-compiling");
		case EWriteFailure::StackUnavailable: return TEXT("niagara-stack-unavailable");
		case EWriteFailure::EmitterNotFound: return TEXT("niagara-emitter-not-found");
		case EWriteFailure::ModuleNotFound: return TEXT("niagara-module-not-found");
		case EWriteFailure::InputNotFound: return TEXT("niagara-input-not-found");
		case EWriteFailure::InputAmbiguous: return TEXT("niagara-input-ambiguous");
		case EWriteFailure::InputNotLocal: return TEXT("niagara-input-not-local");
		case EWriteFailure::TypeUnsupported: return TEXT("niagara-input-type-unsupported");
		case EWriteFailure::ValueInvalid: return TEXT("niagara-input-value-invalid");
		case EWriteFailure::Stale: return TEXT("niagara-input-stale");
		case EWriteFailure::WriteFailed: return TEXT("niagara-input-write-failed");
		case EWriteFailure::None: break;
		}
		return TEXT("live-editor-action-failed");
	}

	bool SetModuleInput(
		const FWriteRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		EWriteFailure& OutFailure,
		FString& OutMessage)
	{
		OutResult.Reset();
		OutFailure = EWriteFailure::None;
		OutMessage.Reset();

		// ---- 1. request shape (no side effects) --------------------------------
		if (Request.SystemPath.IsEmpty() || !Request.SystemPath.StartsWith(TEXT("/")))
		{
			OutFailure = EWriteFailure::InvalidRequest;
			OutMessage = TEXT("systemPath must be an exact /Game package path of a Niagara System.");
			return false;
		}
		if (Request.EmitterName.IsEmpty() && Request.EmitterHandleId.IsEmpty())
		{
			OutFailure = EWriteFailure::InvalidRequest;
			OutMessage = TEXT("Either emitterName or emitterHandleId is required.");
			return false;
		}
		if (Request.ModuleName.IsEmpty())
		{
			OutFailure = EWriteFailure::InvalidRequest;
			OutMessage = TEXT("moduleName is required.");
			return false;
		}
		if (Request.InputName.IsEmpty())
		{
			OutFailure = EWriteFailure::InvalidRequest;
			OutMessage = TEXT("inputName is required.");
			return false;
		}
		if (!Request.Value.IsValid())
		{
			OutFailure = EWriteFailure::InvalidRequest;
			OutMessage = TEXT("value is required.");
			return false;
		}
		if (Request.ModuleOccurrence < 0)
		{
			OutFailure = EWriteFailure::InvalidRequest;
			OutMessage = TEXT("moduleOccurrence must not be negative.");
			return false;
		}
		if (Request.CompileTimeoutSeconds <= 0.0 || Request.CompileTimeoutSeconds > 300.0)
		{
			OutFailure = EWriteFailure::InvalidRequest;
			OutMessage = TEXT("compileTimeoutSeconds must be within (0, 300].");
			return false;
		}

		// ---- 2. asset + view model --------------------------------------------
		EReadFailure ResolveFailure = EReadFailure::None;
		FString ResolveMessage;
		UNiagaraSystem* System = ResolveSystem(Request.SystemPath, ResolveFailure, ResolveMessage);
		if (System == nullptr)
		{
			switch (ResolveFailure)
			{
			case EReadFailure::SystemNotFound: OutFailure = EWriteFailure::SystemNotFound; break;
			case EReadFailure::SystemCompiling: OutFailure = EWriteFailure::SystemCompiling; break;
			case EReadFailure::InvalidRequest: OutFailure = EWriteFailure::InvalidRequest; break;
			default: OutFailure = EWriteFailure::StackUnavailable; break;
			}
			OutMessage = ResolveMessage;
			return false;
		}

		UPackage* Package = System->GetOutermost();
		const bool bWasDirty = Package != nullptr && Package->IsDirty();

		TSharedPtr<FNiagaraSystemViewModel> ViewModel = CreateStackViewModel(*System);
		if (!ViewModel.IsValid())
		{
			OutFailure = EWriteFailure::StackUnavailable;
			OutMessage = TEXT("The Niagara System view model could not be created.");
			return false;
		}

		FLocatedInput Located;
		if (!FindInput(*ViewModel, Request, Located, OutFailure, OutMessage))
		{
			ReleaseStackViewModel(ViewModel);
			return false;
		}

		const FString EmitterName = Located.EmitterName;
		const FString EmitterHandleId = Located.EmitterHandleId;

		// ---- 3. value source gate ---------------------------------------------
		const UNiagaraStackFunctionInput::EValueMode Mode = Located.Input->GetValueMode();
		const FString ModeName = ValueModeName(Mode);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("systemPath"), Request.SystemPath);
		Result->SetStringField(TEXT("systemAssetPath"), System->GetPathName());
		Result->SetStringField(TEXT("emitterName"), EmitterName);
		Result->SetStringField(TEXT("emitterHandleId"), EmitterHandleId);
		Result->SetStringField(TEXT("stage"), Located.StageName);
		Result->SetStringField(TEXT("moduleName"), Located.ModuleName);
		Result->SetNumberField(TEXT("moduleOccurrence"), Request.ModuleOccurrence);
		Result->SetStringField(
			TEXT("moduleInstanceIdentity"),
			Located.ModuleItem->GetModuleNode().NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Result->SetStringField(TEXT("inputName"), Located.Input->GetDisplayName().ToString());
		Result->SetStringField(
			TEXT("inputHandle"),
			Located.Input->GetInputParameterHandle().GetParameterHandleString().ToString());
		Result->SetStringField(TEXT("inputType"), Located.Input->GetInputType().GetName());
		Result->SetStringField(TEXT("valueMode"), ModeName);

		// Report the real source up front: both the expected-mode refusal and the
		// not-local refusal must tell the caller where the value actually comes from.
		if (Mode == UNiagaraStackFunctionInput::EValueMode::Linked)
		{
			const FNiagaraVariableBase& LinkedParameter = Located.Input->GetLinkedParameterValue();
			Result->SetStringField(TEXT("linkedTarget"), LinkedParameter.GetName().ToString());
			Result->SetStringField(TEXT("linkedTargetType"), LinkedParameter.GetType().GetName());
		}
		if (!Request.ExpectedValueMode.IsEmpty() && !EqualsLoose(ModeName, Request.ExpectedValueMode))
		{
			OutFailure = EWriteFailure::InputNotLocal;
			OutMessage = FString::Printf(
				TEXT("Input '%s.%s' is %s, not %s; it was not modified."),
				*Located.ModuleName, *Request.InputName, *ModeName, *Request.ExpectedValueMode);
			Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
			Result->SetStringField(TEXT("errorMessage"), OutMessage);
			Result->SetBoolField(TEXT("wrote"), false);
			OutResult = Result;
			ReleaseStackViewModel(ViewModel);
			return false;
		}

		if (Mode != UNiagaraStackFunctionInput::EValueMode::Local)
		{
			OutFailure = EWriteFailure::InputNotLocal;
			OutMessage = FString::Printf(
				TEXT("Input '%s.%s' is provided by %s, not a local constant; it was not modified."),
				*Located.ModuleName, *Request.InputName, *ModeName);
			Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
			Result->SetStringField(TEXT("errorMessage"), OutMessage);
			Result->SetBoolField(TEXT("wrote"), false);
			OutResult = Result;
			ReleaseStackViewModel(ViewModel);
			return false;
		}

		// ---- 4. type + new value ----------------------------------------------
		const FNiagaraTypeDefinition& InputType = Located.Input->GetInputType();
		if (!UEAgentKitNiagaraValue::IsWritable(InputType))
		{
			OutFailure = EWriteFailure::TypeUnsupported;
			OutMessage = FString::Printf(
				TEXT("Input '%s.%s' has type '%s'; supported types are float, int32, bool, vec2, vec3, vec4 and color."),
				*Located.ModuleName, *Request.InputName, *InputType.GetName());
			Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
			Result->SetStringField(TEXT("errorMessage"), OutMessage);
			OutResult = Result;
			ReleaseStackViewModel(ViewModel);
			return false;
		}

		TArray<uint8> OldBytes;
		FString ReadError;
		if (!ReadPayload(*Located.Input, OldBytes, ReadError))
		{
			OutFailure = EWriteFailure::WriteFailed;
			OutMessage = ReadError;
			Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
			Result->SetStringField(TEXT("errorMessage"), OutMessage);
			OutResult = Result;
			ReleaseStackViewModel(ViewModel);
			return false;
		}

		TArray<uint8> NewBytes;
		FString ValueError;
		if (!UEAgentKitNiagaraValue::FromJson(InputType, Request.Value, NewBytes, ValueError))
		{
			OutFailure = EWriteFailure::ValueInvalid;
			OutMessage = ValueError;
			Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
			Result->SetStringField(TEXT("errorMessage"), OutMessage);
			OutResult = Result;
			ReleaseStackViewModel(ViewModel);
			return false;
		}

		// ---- 5. drift guard ---------------------------------------------------
		if (Request.ExpectedOldValue.IsValid())
		{
			TArray<uint8> ExpectedBytes;
			if (!UEAgentKitNiagaraValue::FromJson(InputType, Request.ExpectedOldValue, ExpectedBytes, ValueError))
			{
				OutFailure = EWriteFailure::ValueInvalid;
				OutMessage = FString::Printf(TEXT("expectedOldValue is invalid: %s"), *ValueError);
				Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
				Result->SetStringField(TEXT("errorMessage"), OutMessage);
				OutResult = Result;
				ReleaseStackViewModel(ViewModel);
				return false;
			}
			if (!UEAgentKitNiagaraValue::EqualBytes(InputType, OldBytes.GetData(), ExpectedBytes.GetData()))
			{
				TSharedPtr<FJsonValue> CurrentJson;
				FString Reason;
				UEAgentKitNiagaraValue::ToJson(InputType, OldBytes.GetData(), OldBytes.Num(), CurrentJson, Reason);

				OutFailure = EWriteFailure::Stale;
				OutMessage = FString::Printf(
					TEXT("Input '%s.%s' no longer holds expectedOldValue."),
					*Located.ModuleName, *Request.InputName);
				Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
				Result->SetStringField(TEXT("errorMessage"), OutMessage);
				Result->SetField(TEXT("currentValue"), CurrentJson);
				Result->SetBoolField(TEXT("wrote"), false);
				OutResult = Result;
				ReleaseStackViewModel(ViewModel);
				return false;
			}
		}

		const bool bSameValue = UEAgentKitNiagaraValue::EqualBytes(
			InputType, OldBytes.GetData(), NewBytes.GetData());

		TSharedPtr<FJsonValue> OldJson;
		FString OldReason;
		UEAgentKitNiagaraValue::ToJson(InputType, OldBytes.GetData(), OldBytes.Num(), OldJson, OldReason);
		TSharedPtr<FJsonValue> NewJson;
		FString NewReason;
		UEAgentKitNiagaraValue::ToJson(InputType, NewBytes.GetData(), NewBytes.Num(), NewJson, NewReason);

		Result->SetField(TEXT("oldValue"), OldJson);
		Result->SetField(TEXT("value"), Request.Value);
		Result->SetField(TEXT("newValue"), NewJson);

		// ---- 6. write (single undo step) --------------------------------------
		bool bWrote = false;
		int32 TransactionCount = 0;
		if (!bSameValue)
		{
			// GetQueueLength() is the length of the transaction queue; GetUndoCount()
			// counts undone-but-redoable entries, which stays 0 after a write.
			const int32 UndoCountBefore = (GEditor != nullptr && GEditor->Trans != nullptr)
				? GEditor->Trans->GetQueueLength()
				: 0;

			{
				// The outer transaction merges with the one SetLocalValue opens
				// internally, so one call is exactly one Undo step.
				FScopedTransaction Transaction(NSLOCTEXT(
					"UEAgentKitNiagaraStack", "SetNiagaraModuleInput", "Set Niagara module input"));

				TSharedRef<FStructOnScope> NewValue = MakeShared<FStructOnScope>(InputType.GetStruct());
				FMemory::Memcpy(NewValue->GetStructMemory(), NewBytes.GetData(), NewBytes.Num());
				Located.Input->SetLocalValue(NewValue);
			}

			const int32 UndoCountAfter = (GEditor != nullptr && GEditor->Trans != nullptr)
				? GEditor->Trans->GetQueueLength()
				: 0;
			TransactionCount = FMath::Max(0, UndoCountAfter - UndoCountBefore);

			TArray<uint8> WrittenBytes;
			FString WriteReadError;
			if (!ReadPayload(*Located.Input, WrittenBytes, WriteReadError)
				|| !UEAgentKitNiagaraValue::EqualBytes(InputType, WrittenBytes.GetData(), NewBytes.GetData()))
			{
				// The write did not stick: roll the transaction back so a failed call
				// reports wrote=false truthfully and leaves no applied value behind.
				if (GEditor != nullptr && TransactionCount > 0)
				{
					GEditor->UndoTransaction(/*bCanRedo*/ false);
					TransactionCount = 0;
				}

				OutFailure = EWriteFailure::WriteFailed;
				OutMessage = FString::Printf(
					TEXT("SetLocalValue did not read back as the requested value: %s"), *WriteReadError);
				Result->SetStringField(TEXT("errorCode"), WriteFailureCode(OutFailure));
				Result->SetStringField(TEXT("errorMessage"), OutMessage);
				Result->SetBoolField(TEXT("wrote"), false);
				OutResult = Result;
				ReleaseStackViewModel(ViewModel);
				return false;
			}

			bWrote = true;
		}

		// ---- 7. compile (bounded) ---------------------------------------------
		bool bCompileTimedOut = false;
		if (bWrote)
		{
			System->RequestCompile(/*bForce*/ false);
		}
		WaitForCompilation(*System, Request.CompileTimeoutSeconds, bCompileTimedOut);

		ReleaseStackViewModel(ViewModel);

		// ---- 8. undo/redo proof ------------------------------------------------
		bool bUndoVerified = false;
		bool bRedoVerified = false;
		bool bUndoVerificationAttempted = false;
		if (bWrote && Request.bVerifyUndo)
		{
			bUndoVerificationAttempted = true;

			if (GEditor != nullptr)
			{
				TArray<uint8> UndoneBytes;
				FString UndoError;
				if (GEditor->UndoTransaction(/*bCanRedo*/ true)
					&& ReadPayloadFresh(*System, Request, UndoneBytes, UndoError))
				{
					bUndoVerified = UEAgentKitNiagaraValue::EqualBytes(
						InputType, UndoneBytes.GetData(), OldBytes.GetData());
				}

				TArray<uint8> RedoneBytes;
				FString RedoError;
				if (GEditor->RedoTransaction()
					&& ReadPayloadFresh(*System, Request, RedoneBytes, RedoError))
				{
					bRedoVerified = UEAgentKitNiagaraValue::EqualBytes(
						InputType, RedoneBytes.GetData(), NewBytes.GetData());
				}
			}
		}

		// ---- 9. result ----------------------------------------------------------
		const bool bIsDirtyNow = Package != nullptr && Package->IsDirty();

		Result->SetBoolField(TEXT("wrote"), bWrote);
		Result->SetBoolField(TEXT("sameValue"), bSameValue);
		Result->SetNumberField(TEXT("transactionCount"), TransactionCount);
		Result->SetBoolField(TEXT("undoVerificationAttempted"), bUndoVerificationAttempted);
		Result->SetBoolField(TEXT("undoVerified"), bUndoVerified);
		Result->SetBoolField(TEXT("redoVerified"), bRedoVerified);
		Result->SetBoolField(TEXT("saved"), false);
		Result->SetBoolField(TEXT("requiresSave"), bWrote);
		Result->SetBoolField(TEXT("compileRequested"), bWrote);
		Result->SetBoolField(TEXT("compileTimedOut"), bCompileTimedOut);
		Result->SetBoolField(TEXT("compilePending"), System->HasOutstandingCompilationRequests());
		Result->SetBoolField(TEXT("packageDirtyBefore"), bWasDirty);
		Result->SetBoolField(TEXT("packageDirty"), bIsDirtyNow);
		Result->SetBoolField(TEXT("dirtied"), !bWasDirty && bIsDirtyNow);
		Result->SetStringField(TEXT("packagePath"), Package != nullptr ? Package->GetName() : FString());

		OutResult = Result;
		return true;
	}
}
