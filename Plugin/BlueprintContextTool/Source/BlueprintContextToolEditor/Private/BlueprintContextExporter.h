#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class UBlueprint;

enum class EBlueprintContextProfile : uint8
{
	Index,
	Structure,
	Logic,
	Defaults,
	Full,
	AI
};

struct FBlueprintContextExportOptions
{
	FString OutputDirectory;
	EBlueprintContextProfile Profile = EBlueprintContextProfile::Logic;
	FString GraphFilter;
	bool bWriteJson = true;
	bool bWriteBpctx = true;
	bool bPrettyJson = true;
	bool bIncludeLayout = false;
	bool bIncludeReflectedNodeProperties = true;
	bool bIncludeUnchangedDefaults = false;
};

struct FBlueprintContextExportResult
{
	bool bSuccess = false;
	FString AssetPath;
	FString JsonPath;
	FString BpctxPath;
	FString Error;
	int32 VariableCount = 0;
	int32 ComponentCount = 0;
	int32 GraphCount = 0;
	int32 NodeCount = 0;
	int32 PinCount = 0;
	int32 LinkCount = 0;
	int32 SymbolCount = 0;
	int32 ReferenceCount = 0;
};

class FBlueprintContextExporter
{
public:
	static bool ParseProfile(const FString& Value, EBlueprintContextProfile& OutProfile);
	static FString ProfileToString(EBlueprintContextProfile Profile);
	static void ApplyProfileDefaults(FBlueprintContextExportOptions& Options);

	static bool ExportBlueprint(
		UBlueprint* Blueprint,
		const FBlueprintContextExportOptions& Options,
		FBlueprintContextExportResult& OutResult);

private:
	static TSharedRef<FJsonObject> BuildCanonicalJson(
		UBlueprint* Blueprint,
		const FBlueprintContextExportOptions& Options,
		FBlueprintContextExportResult& InOutResult);

	static FString BuildBpctx(const TSharedRef<FJsonObject>& RootObject);
	static bool SaveJson(const TSharedRef<FJsonObject>& RootObject, const FString& Path, bool bPretty);
	static bool SaveText(const FString& Text, const FString& Path);
};
