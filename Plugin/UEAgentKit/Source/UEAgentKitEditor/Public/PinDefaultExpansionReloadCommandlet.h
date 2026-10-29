#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"

#include "PinDefaultExpansionReloadCommandlet.generated.h"

/**
 * Independent read-only verifier for the P1 pin-default edit evidence.
 *
 * Run in a fresh Editor process with:
 *   UnrealEditor-Cmd.exe HostProject.uproject -run=PinDefaultExpansionReload
 *
 * It loads the saved Blueprint from disk, resolves the exact graph and pin
 * identities, and compares each persisted DefaultValue with the evidence. It
 * never opens a Blueprint editor, mutates the graph, or saves a package.
 */
UCLASS()
class UPinDefaultExpansionReloadCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UPinDefaultExpansionReloadCommandlet();

	virtual int32 Main(const FString& Params) override;
};
