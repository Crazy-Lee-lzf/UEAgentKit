// N0 compile probe: proves the NiagaraEditor stack API is reachable from this
// Editor module.  Nothing here runs at startup; the function exists so the
// compiler and linker must resolve every symbol it touches.

#include "NiagaraProbe/NiagaraStackApiProbe.h"

#if WITH_EDITOR

#include "NiagaraSystem.h"

#include "ViewModels/Stack/NiagaraStackFunctionInput.h"
#include "ViewModels/Stack/NiagaraStackModuleItem.h"
#include "ViewModels/Stack/NiagaraStackViewModel.h"
#include "ViewModels/NiagaraEmitterHandleViewModel.h"
#include "ViewModels/NiagaraSystemViewModel.h"

namespace UEAgentKitNiagaraProbe
{
	bool TouchStackApi(UNiagaraSystem* System, UNiagaraStackFunctionInput* Input, FString& OutDescription)
	{
		bool bReached = false;

		if (Input != nullptr)
		{
			const UNiagaraStackFunctionInput::EValueMode Mode = Input->GetValueMode();
			const FNiagaraTypeDefinition& InputType = Input->GetInputType();
			const FName HandleName = Input->GetInputParameterHandle().GetParameterHandleString();

			// GetLocalValueStruct() is declared non-const in 5.6.1 even though the
			// call only reads, so a writable view is needed.
			TSharedPtr<const FStructOnScope> LocalValue =
				const_cast<UNiagaraStackFunctionInput&>(*Input).GetLocalValueStruct();

			OutDescription = FString::Printf(
				TEXT("%s/%s/mode=%d/local=%d"),
				*HandleName.ToString(),
				*InputType.GetName(),
				static_cast<int32>(Mode),
				LocalValue.IsValid() ? 1 : 0);
			bReached = true;
		}

		if (System != nullptr)
		{
			TSharedRef<FNiagaraSystemViewModel> SystemViewModel = MakeShared<FNiagaraSystemViewModel>();
			const TArray<TSharedRef<FNiagaraEmitterHandleViewModel>> Emitters =
				SystemViewModel->GetEmitterHandleViewModels();
			bReached = bReached || Emitters.Num() >= 0;
		}

		return bReached;
	}
}

#endif // WITH_EDITOR