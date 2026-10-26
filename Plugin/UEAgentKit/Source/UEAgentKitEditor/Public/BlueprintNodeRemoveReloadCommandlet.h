#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"

#include "BlueprintNodeRemoveReloadCommandlet.generated.h"

/**
 * Read-only fresh-process verification of a saved Blueprint node-removal result.
 *
 * Run as: UnrealEditor-Cmd.exe <project> -run=BlueprintNodeRemoveReload
 *
 * It loads the package named by Saved/BlueprintNodeRemove/evidence.json, verifies
 * the saved asset SHA-256, that every recorded deleted node GUID is absent, the
 * exact remaining node GUID/class set, every remaining node's exact pin
 * name/GUID/direction/category inventory, the exact final edge set, and that the
 * recorded removed edges are absent while the recorded untouched edges are
 * present.  It never saves, compiles or mutates anything; it writes
 * Saved/BlueprintNodeRemove/reload.json and returns 0 only when every check
 * passed.
 */
UCLASS()
class UBlueprintNodeRemoveReloadCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UBlueprintNodeRemoveReloadCommandlet();

	virtual int32 Main(const FString& Params) override;
};
