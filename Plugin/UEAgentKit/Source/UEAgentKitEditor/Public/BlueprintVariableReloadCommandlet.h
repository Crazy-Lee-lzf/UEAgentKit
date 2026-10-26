#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"

#include "BlueprintVariableReloadCommandlet.generated.h"

/**
 * Read-only fresh-process verification for the P2 Blueprint variable slice.
 *
 * It loads the package written by the Editor acceptance test from disk and
 * checks the persisted variable names, GUIDs, declared types and default
 * values, the exact variable set, the exact node/link sets and the saved asset
 * SHA-256.  It never saves, compiles or mutates anything.
 */
UCLASS()
class UBlueprintVariableReloadCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UBlueprintVariableReloadCommandlet();

	virtual int32 Main(const FString& Params) override;
};
