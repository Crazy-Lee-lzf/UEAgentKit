#pragma once

#include "CoreMinimal.h"

class UNiagaraStackFunctionInput;
class UNiagaraSystem;

namespace UEAgentKitNiagaraProbe
{
	bool TouchStackApi(UNiagaraSystem* System, UNiagaraStackFunctionInput* Input, FString& OutDescription);
}