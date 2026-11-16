#pragma once

#include "Commandlets/Commandlet.h"
#include "NiagaraStackFixtureCommandlet.generated.h"

/**
 * Test asset generator for the Niagara Module Stack line.
 *
 * Creates a deterministic fixture Niagara System with two emitters so the N1
 * reader and the native automation test have something stable to read:
 *
 *   /Game/Dev/NS_DevStack   (system)
 *   /Game/Dev/NE_DevStack_A (emitter)
 *   /Game/Dev/NE_DevStack_B (emitter)
 *
 * Each emitter is built through UNiagaraEmitterFactoryNew::InitializeEmitter with
 * default modules/renderers, which yields modules in EmitterUpdate, ParticleSpawn
 * and ParticleUpdate.  The commandlet then makes sure at least one module input is
 * a Linked value so the reader's Linked branch is covered by a real asset.
 */
UCLASS()
class UEAGENTKITEDITOR_API UNiagaraStackFixtureCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UNiagaraStackFixtureCommandlet();

	virtual int32 Main(const FString& Params) override;
};
