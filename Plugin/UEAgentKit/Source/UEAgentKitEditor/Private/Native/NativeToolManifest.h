// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

/** Single validated native tool manifest entry (R1.5 subset of ueak.native-registry/1.0). */
struct FNativeToolManifestEntry
{
	FString Name;
	FString Version;
	FString Domain;
	FString TitleKey;
	FString Risk;
	FString Maturity;
	FString GateProfile;
	FString Replacement;
	FString BridgeMethod;
	FString ThreadingExecution;
	TArray<FString> BridgeAliases;
	TArray<FString> Capabilities;
	bool bReadOnly = false;
	bool bRequiresTransaction = false;
	bool bRequiresSave = false;
	bool bDeprecated = false;
	int32 MaxRequestBytes = 0;
	int32 MaxResponseBytes = 0;
};

/** Validated manifest plus its contract hash. */
struct FNativeToolManifest
{
	bool bValid = false;
	FString ErrorCode;
	FString ErrorMessage;
	FString SchemaVersion;
	FString RegistryVersion;
	FString Hash;
	TArray<FNativeToolManifestEntry> Tools;

	bool HasTool(const FString& ToolId) const;
	const FNativeToolManifestEntry* FindTool(const FString& ToolId) const;
};

/**
 * Loads Plugin/UEAgentKit/Resources/NativeTools/native-tools.v1.json.
 *
 * Validation is fail-closed but never fatal to the editor: an absent or invalid
 * manifest only marks the native registry unavailable (N0-D01 opt-in rule).
 */
class FNativeToolManifestLoader
{
public:
	static FString ResolveManifestPath();

	/** Fills OutManifest; returns false with a stable code (never a local path). */
	static bool Load(FNativeToolManifest& OutManifest);

	/** Canonical hash of a parsed manifest document (``ueak.native.registry/1``). */
	static bool ComputeManifestHash(const TSharedPtr<class FJsonObject>& Document, FString& OutHash, FString& OutError);

private:
	static bool ValidateTool(const TSharedPtr<class FJsonObject>& Tool, FNativeToolManifestEntry& OutEntry, FString& OutError);
};
