#include "EditorBridge.h"

#include "Niagara/NiagaraModuleStackReader.h"

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
