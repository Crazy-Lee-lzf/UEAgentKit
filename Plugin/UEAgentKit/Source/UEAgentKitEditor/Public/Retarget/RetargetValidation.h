#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class UIKRetargeter;

namespace UEAgentKitRetarget
{
	// One validation finding.
	struct FRetargetValidationIssue
	{
		FString Level; // error | warning
		FString Code;
		FString Message;
		FString Scope; // structure | metadata | motion
		FString AssetPath;
		FString Bone;
		float TimeSeconds = 0.0f;
	};

	// Validates the IK Retargeter structure, the animation metadata of the given
	// output animations, and samples major-bone motion for NaN/Inf and extreme
	// jumps. Produces passed / passed_with_warnings / failed.
	bool ValidateAnimationRetarget(
		UIKRetargeter* Retargeter,
		const TArray<FString>& AnimationPaths,
		TArray<FRetargetValidationIssue>& OutIssues,
		FString& OutVerdict,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	// Serializes a validation issue for the JSON report.
	TSharedRef<FJsonObject> ValidationIssueToJson(const FRetargetValidationIssue& Issue);
}
