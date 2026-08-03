#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "RetargetAnalyzeCommandlet.generated.h"

// Phase 1 read-only analysis commandlet: loads a source and target Skeletal
// Mesh, builds the reference skeleton snapshots, runs the humanoid-v1 chain
// candidate scoring, and writes the compatibility report JSON. It never
// modifies assets or changes Dirty state.
UCLASS()
class UEAGENTKITEDITOR_API URetargetAnalyzeCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	URetargetAnalyzeCommandlet();

	virtual int32 Main(const FString& Params) override;
};
