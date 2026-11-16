#include "EditorBridge.h"

#include "Niagara/NiagaraModuleStackReader.h"
#include "Niagara/NiagaraModuleStackWriter.h"

#include "Dom/JsonObject.h"

bool FUEAgentKitEditorBridge::TryBuildNiagaraModuleStackResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();

	if (!Params.IsValid())
	{
		OutErrorCode = UEAgentKitNiagaraStack::FailureCode(UEAgentKitNiagaraStack::EReadFailure::InvalidRequest);
		OutErrorMessage = TEXT("editor.getNiagaraModuleStack requires a systemPath parameter.");
		return false;
	}

	UEAgentKitNiagaraStack::FReadRequest Request;
	if (!Params->TryGetStringField(TEXT("systemPath"), Request.SystemPath) || Request.SystemPath.IsEmpty())
	{
		OutErrorCode = UEAgentKitNiagaraStack::FailureCode(UEAgentKitNiagaraStack::EReadFailure::InvalidRequest);
		OutErrorMessage = TEXT("systemPath must be the exact /Game package path of a Niagara System.");
		return false;
	}

	Params->TryGetBoolField(TEXT("includeInputs"), Request.bIncludeInputs);

	double NumberValue = 0.0;
	if (Params->TryGetNumberField(TEXT("maxModules"), NumberValue))
	{
		Request.MaxModules = static_cast<int32>(NumberValue);
	}
	if (Params->TryGetNumberField(TEXT("maxInputsPerModule"), NumberValue))
	{
		Request.MaxInputsPerModule = static_cast<int32>(NumberValue);
	}

	if (Request.MaxModules <= 0)
	{
		OutErrorCode = UEAgentKitNiagaraStack::FailureCode(UEAgentKitNiagaraStack::EReadFailure::InvalidRequest);
		OutErrorMessage = TEXT("maxModules must be greater than zero.");
		return false;
	}
	if (Request.MaxInputsPerModule < 0)
	{
		OutErrorCode = UEAgentKitNiagaraStack::FailureCode(UEAgentKitNiagaraStack::EReadFailure::InvalidRequest);
		OutErrorMessage = TEXT("maxInputsPerModule must not be negative.");
		return false;
	}

	UEAgentKitNiagaraStack::EReadFailure Failure = UEAgentKitNiagaraStack::EReadFailure::None;
	FString FailureMessage;
	if (!UEAgentKitNiagaraStack::ReadModuleStack(Request, OutResult, Failure, FailureMessage))
	{
		OutErrorCode = UEAgentKitNiagaraStack::FailureCode(Failure);
		OutErrorMessage = FailureMessage.IsEmpty()
			? TEXT("The Niagara module stack could not be read.")
			: FailureMessage;
		return false;
	}

	return true;
}

bool FUEAgentKitEditorBridge::TrySetNiagaraModuleInputResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();

	if (!Params.IsValid())
	{
		OutErrorCode = UEAgentKitNiagaraStack::WriteFailureCode(UEAgentKitNiagaraStack::EWriteFailure::InvalidRequest);
		OutErrorMessage = TEXT("editor.setNiagaraModuleInput requires systemPath, emitter identity, moduleName, inputName and value.");
		return false;
	}

	UEAgentKitNiagaraStack::FWriteRequest Request;
	if (!Params->TryGetStringField(TEXT("systemPath"), Request.SystemPath) || Request.SystemPath.IsEmpty())
	{
		OutErrorCode = UEAgentKitNiagaraStack::WriteFailureCode(UEAgentKitNiagaraStack::EWriteFailure::InvalidRequest);
		OutErrorMessage = TEXT("systemPath must be the exact /Game package path of a Niagara System.");
		return false;
	}

	Params->TryGetStringField(TEXT("emitterName"), Request.EmitterName);
	Params->TryGetStringField(TEXT("emitterHandleId"), Request.EmitterHandleId);
	Params->TryGetStringField(TEXT("stage"), Request.Stage);
	Params->TryGetStringField(TEXT("moduleName"), Request.ModuleName);
	Params->TryGetStringField(TEXT("inputName"), Request.InputName);
	Params->TryGetStringField(TEXT("expectedValueMode"), Request.ExpectedValueMode);
	Params->TryGetBoolField(TEXT("verifyUndo"), Request.bVerifyUndo);

	double NumberValue = 0.0;
	if (Params->TryGetNumberField(TEXT("moduleOccurrence"), NumberValue))
	{
		Request.ModuleOccurrence = static_cast<int32>(NumberValue);
	}
	if (Params->TryGetNumberField(TEXT("compileTimeoutSeconds"), NumberValue))
	{
		Request.CompileTimeoutSeconds = NumberValue;
	}

	const TSharedPtr<FJsonValue> ValueField = Params->TryGetField(TEXT("value"));
	if (ValueField.IsValid())
	{
		Request.Value = ValueField;
	}

	const TSharedPtr<FJsonValue> ExpectedOldField = Params->TryGetField(TEXT("expectedOldValue"));
	if (ExpectedOldField.IsValid() && !ExpectedOldField->IsNull())
	{
		Request.ExpectedOldValue = ExpectedOldField;
	}

	UEAgentKitNiagaraStack::EWriteFailure Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
	FString FailureMessage;
	if (!UEAgentKitNiagaraStack::SetModuleInput(Request, OutResult, Failure, FailureMessage))
	{
		OutErrorCode = UEAgentKitNiagaraStack::WriteFailureCode(Failure);
		OutErrorMessage = FailureMessage.IsEmpty()
			? TEXT("The Niagara module input could not be modified.")
			: FailureMessage;
		return false;
	}

	return true;
}