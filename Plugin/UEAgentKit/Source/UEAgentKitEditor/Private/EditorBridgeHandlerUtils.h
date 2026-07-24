#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "EditorBridgeLogCapture.h"
#include "Engine/Blueprint.h"
#include "Engine/World.h"

class UObject;
class UWorld;

namespace UEAgentKitEditorBridgePrivate
{
	extern const TCHAR* const PluginVersion;

	inline constexpr int32 MaxSelectionItems = 200;
	inline constexpr int32 MaxOpenAssets = 200;
	inline constexpr int32 MaxDirtyPackages = 200;

	FString GetWorldTypeName(EWorldType::Type WorldType);
	FString GetPieStateName();
	UWorld* GetEditorWorld();
	TSharedRef<FJsonObject> DescribeObject(UObject* Object, const FString& Kind);
	int32 CountDirtyGamePackages();
	FString GetBlueprintStatusName(EBlueprintStatus Status);
	FString GetBlueprintTypeName(EBlueprintType BlueprintType);
	FUEAgentKitLogQuery BuildLogQuery(const TSharedPtr<FJsonObject>& Params, bool bCompileOnly);
	TSharedRef<FJsonObject> DescribeCapturedLogEntry(const FUEAgentKitCapturedLogEntry& Entry);
	TSharedRef<FJsonObject> DescribeBlueprintState(UBlueprint* Blueprint);
	bool IsObjectSelected(UObject* Object);
	bool IsAssetOpenInEditor(UObject* Asset);
}
