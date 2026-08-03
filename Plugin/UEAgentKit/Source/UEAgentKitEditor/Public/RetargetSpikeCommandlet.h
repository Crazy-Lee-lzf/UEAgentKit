#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "RetargetSpikeCommandlet.generated.h"

// Phase 0 API Spike: verifies the UE5.6 IKRig / IKRetargeter Editor API in an
// isolated throw-away fixture. Creates temporary assets under
// /Game/UEAgentKitRetargetTests/Spike, configures them through the official
// Controllers, runs one batch retarget, saves, reloads, verifies and deletes.
// This spike is a test tool only; it does not expose any generic invocation API.
UCLASS()
class UEAGENTKITEDITOR_API URetargetSpikeCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	URetargetSpikeCommandlet();

	virtual int32 Main(const FString& Params) override;
};
