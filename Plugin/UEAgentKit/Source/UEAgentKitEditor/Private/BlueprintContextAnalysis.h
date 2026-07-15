#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class FJsonValue;
class UBlueprint;
struct FBlueprintContextExportOptions;

class FBlueprintContextAnalysis
{
public:
	static TSharedRef<FJsonObject> BuildAssetRevision(UBlueprint* Blueprint);

	static void BuildSymbolsAndReferences(
		UBlueprint* Blueprint,
		const FBlueprintContextExportOptions& Options,
		TArray<TSharedPtr<FJsonValue>>& OutSymbols,
		TArray<TSharedPtr<FJsonValue>>& OutReferences,
		int32& OutSymbolCount,
		int32& OutReferenceCount);
};
