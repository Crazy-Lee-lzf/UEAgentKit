#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"

#include "BlueprintAgentE2EReloadCommandlet.generated.h"

/**
 * Read-only fresh-process verification of the P5 Agent end-to-end Blueprint state.
 *
 * Reads Saved/UEAgentKitAgentE2E/expected.json (written by the MCP driver after
 * the last explicit Save), loads the package from disk, and verifies:
 *   - saved asset SHA-256 and graph GUID;
 *   - every recorded deleted node GUID is absent;
 *   - the exact remaining node GUID/class set;
 *   - every remaining pin name/GUID/direction/type/default value;
 *   - the exact final edge set;
 *   - every created user variable name/type/effective default.
 *
 * The commandlet never saves, compiles or mutates anything.  It writes
 * Saved/UEAgentKitAgentE2E/reload.json and returns 0 only when every check
 * passed.
 */
UCLASS()
class UBlueprintAgentE2EReloadCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UBlueprintAgentE2EReloadCommandlet();

	virtual int32 Main(const FString& Params) override;
};
