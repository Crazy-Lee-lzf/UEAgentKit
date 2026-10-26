#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"

#include "BlueprintRewireReloadCommandlet.generated.h"

/**
 * Read-only fresh-process verification of a saved Blueprint rewire result.
 *
 * Run as: UnrealEditor-Cmd.exe <project> -run=BlueprintRewireReload
 *
 * It loads the package named by Saved/BlueprintRewire/evidence.json, verifies
 * the saved asset SHA-256, the exact node GUID/class set, every node's exact
 * pin name/GUID/direction/category inventory, the exact final edge set, and
 * that the recorded removed edges are absent while the recorded added and
 * untouched edges are present.  It never saves, compiles or mutates anything;
 * it writes Saved/BlueprintRewire/reload.json and returns 0 only when every
 * check passed.
 */
UCLASS()
class UBlueprintRewireReloadCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UBlueprintRewireReloadCommandlet();

	virtual int32 Main(const FString& Params) override;
};
