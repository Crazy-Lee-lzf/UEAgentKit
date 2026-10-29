#pragma once

#include "Commandlets/Commandlet.h"
#include "GraphEditExpansionReloadCommandlet.generated.h"

/**
 * Cross-process read-only verifier for the GraphEdit expansion acceptance
 * evidence.  It loads the saved UAsset in a fresh Editor process, resolves the
 * exact graph GUID and verifies every created node GUID and edge string written
 * by the prior process.  It never mutates or saves the asset.
 */
UCLASS()
class UEAGENTKITEDITOR_API UGraphEditExpansionReloadCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UGraphEditExpansionReloadCommandlet();

	virtual int32 Main(const FString& Params) override;
};
