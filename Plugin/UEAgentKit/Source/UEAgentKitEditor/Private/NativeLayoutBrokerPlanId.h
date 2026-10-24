#pragma once

#include "CoreMinimal.h"

class FJsonObject;

/** Canonical LayoutPlan id recomputation shared by the M2 native client/service. */
struct FUEAgentKitLayoutBrokerPlanId
{
	/**
	 * Recompute layout:<16 hex> from a broker result using the original request
	 * options.  The projection matches graph_contracts.layout exactly:
	 * baseGraphHash, mode, positions[nodeId,x,y], options[spacingX,spacingY,seed]
	 * and packing[targetWidth,spacing,skyline-v1].
	 */
	static bool Compute(
		const TSharedPtr<FJsonObject>& Result,
		int32 SpacingX,
		int32 SpacingY,
		int32 TargetWidth,
		int32 Seed,
		FString& OutPlanId,
		FString& OutError);

	/** Compare the response planId with the canonical recomputation. */
	static bool Validate(
		const TSharedPtr<FJsonObject>& Result,
		int32 SpacingX,
		int32 SpacingY,
		int32 TargetWidth,
		int32 Seed,
		FString& OutError);
};
