// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

/**
 * R1.0 read-only Blueprint node registry (N0 contract, 2026-10-24).
 *
 * Scope: this module only *reads* FBlueprintActionDatabase, derives
 * ueak.node-descriptor/1.0 documents and computes the contract hashes.  It
 * never modifies an asset, opens a transaction, marks a package dirty or saves.
 *
 * The feature is OFF by default and is opt-in through
 * ``[UEAgentKit] NativeToolRegistry=off|shadow|active`` (GConfig, startup only).
 * A missing or invalid manifest only disables the native surface; legacy P12
 * behaviour is untouched.
 */

enum class ENativeRegistryMode : uint8
{
	Off = 0,
	Shadow = 1,
	Active = 2,
};

class UBlueprintNodeSpawner;

enum class ENativeRegistryState : uint8
{
	Uninitialized = 0,
	Off = 1,
	Ready = 2,
	Unavailable = 3,
};

/** Bounded catalog options.  Defaults are the R1 freeze; callers may only shrink. */
struct FNativeCatalogOptions
{
	/** Maximum number of committed descriptors kept for this call. */
	int32 MaxItems = 512;

	/** Official engine spawners only.  Non-official spawners are never described. */
	bool bOfficialSpawnersOnly = true;

	/** Create missing template nodes for official spawners (bounded by the two limits below). */
	bool bAllowOfficialTemplateSpawn = true;

	int32 MaxTemplateSpawns = 512;

	double TemplateBudgetMs = 5000.0;

	/**
	 * Allocate default pins on the (transient) template node when the engine did
	 * not do it.  Pin templates are required by the descriptor contract; the
	 * allocation touches only transient template objects, never an asset.
	 */
	bool bAllowTransientPinAllocation = true;

	int32 MaxPinsPerDescriptor = 256;

	/** Optional fixed "discoveredAtUtc"; empty means capture the wall clock once per catalog. */
	FString DiscoveredAtUtcOverride;
};

struct FNativeCatalogIdentity
{
	FString EngineVersion;
	int32 EngineChangelist = 0;
	FString PluginVersion;
	FString PluginBuildHash;
	FString PluginSetHash;
	FString EditorSessionId;
	FString ProjectName;
	FString BuildConfiguration;
};

struct FNativeUnsupportedEntry
{
	FString Reason;
	FString SpawnerClass;
	FString NodeClass;
	FString TrustLevel;
};

struct FNativeNodeDescriptorRecord
{
	FString ActionId;
	FString DescriptorDigest;
	FString CanonicalDiscoveryKey;
	TSharedPtr<FJsonObject> Descriptor;

	/** Canonical descriptor projection hash (no descriptorDigest / discoveredAtUtc). */
	FString DescriptorProjectionHash;
};

struct FNativeCatalogResult
{
	bool bOk = false;
	FString ErrorCode;
	FString ErrorMessage;

	ENativeRegistryMode Mode = ENativeRegistryMode::Off;
	FNativeCatalogIdentity Identity;

	TArray<FNativeNodeDescriptorRecord> Descriptors;
	TArray<FNativeUnsupportedEntry> Unsupported;

	/** Full reason histogram (not bounded); Unsupported[] itself is bounded. */
	TMap<FString, int32> UnsupportedByReason;

	int32 TotalSpawners = 0;
	int32 OwnerCount = 0;
	int32 NotNodeActionCount = 0;
	int32 TotalUnsupported = 0;
	int32 DuplicateActionIdCount = 0;
	int32 TotalDescriptorsAvailable = 0;
	int32 TemplateNodesCached = 0;
	int32 TemplateNodesCreated = 0;
	int32 TemplateBudgetSkips = 0;
	int32 PinsAllocated = 0;
	int32 PinAllocationFailures = 0;

	bool bComplete = false;
	bool bTruncated = false;
	FString ActionDatabaseFingerprint;
	double DurationMs = 0.0;

	/** Human-readable identity summary used by the shadow tools. */
	TSharedRef<FJsonObject> MakeIdentityJson() const;

	/** Compact catalog summary (counts + fingerprint + bounded unsupported list). */
	TSharedRef<FJsonObject> MakeSummaryJson() const;
};

struct FNativeManifestSummary
{
	bool bLoaded = false;
	FString SchemaVersion;
	FString RegistryVersion;
	FString Hash;
	int32 ToolCount = 0;
	TArray<FString> ToolIds;
	TArray<FString> ToolVersions;
};

/** Abstract read-only registry surface (lifecycle + query). */
class UEAGENTKITEDITOR_API INativeNodeRegistry
{
public:
	virtual ~INativeNodeRegistry() = default;

	virtual ENativeRegistryMode GetMode() const = 0;
	virtual ENativeRegistryState GetState() const = 0;
	virtual bool IsAvailable() const = 0;
	virtual const FString& GetUnavailableCode() const = 0;
	virtual const FString& GetUnavailableMessage() const = 0;
};

/**
 * Process-wide read-only node registry.
 *
 * Lifecycle: Initialize() at module startup, Shutdown() at module shutdown.
 * All queries are game-thread only and never cache UObject pointers.
 */
class UEAGENTKITEDITOR_API FNativeNodeRegistry final : public INativeNodeRegistry
{
public:
	static FNativeNodeRegistry& Get();

	// INativeNodeRegistry
	virtual ENativeRegistryMode GetMode() const override;
	virtual ENativeRegistryState GetState() const override;
	virtual bool IsAvailable() const override;
	virtual const FString& GetUnavailableCode() const override;
	virtual const FString& GetUnavailableMessage() const override;

	/** Idempotent.  Off mode performs no manifest read and no engine access. */
	void Initialize(ENativeRegistryMode InMode);

	/** Idempotent.  Clears the manifest and any derived catalog state. */
	void Shutdown();

	bool IsInitialized() const;

	/** Raw config value (``[UEAgentKit] NativeToolRegistry``); empty when unset. */
	static FString ReadConfiguredModeText();

	static ENativeRegistryMode ParseMode(const FString& ModeText);

	/** Test-only seam: requires GIsAutomationTesting; enables shadow + manifest. */
	bool InitializeForAutomation();

	/** Builds the full bounded catalog.  Requires Ready state and the game thread. */
	FNativeCatalogResult BuildCatalog(const FNativeCatalogOptions& Options) const;

	/** Stable database fingerprint from an already sorted action id array. */
	static FString ComputeActionDatabaseFingerprint(const TArray<FString>& SortedActionIds, FString& OutErrorCode);

	/** Stable plugin-set hash (enabled plugins only, sorted, engine+project). */
	static FString ComputePluginSetHash(FString& OutErrorCode);

	const FNativeManifestSummary& GetManifestSummary() const;

	/**
	 * R1.5 partial shadow dispatch.  Only the manifest-declared native tools are
	 * reachable; every argument is validated and no write path exists.
	 */
	bool TryInvokeShadowTool(
		const FString& ToolId,
		const FString& ToolVersion,
		const TSharedPtr<FJsonObject>& Arguments,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage) const;

	/** Ability to resolve a tool at all (used by the bridge capability list). */
	bool HasTool(const FString& ToolId) const;

private:
	FNativeNodeRegistry() = default;

	ENativeRegistryMode Mode = ENativeRegistryMode::Off;
	ENativeRegistryState State = ENativeRegistryState::Uninitialized;
	FString UnavailableCode;
	FString UnavailableMessage;
	FNativeManifestSummary Manifest;
	FString EditorSessionId;

	/**
	 * Session-frozen coverage.  A spawner that could not be described because the
	 * bounded template budget was exhausted (or its template could not be
	 * created) stays unsupported for the rest of the editor session.  Without
	 * this, the engine's asynchronous template priming would silently change the
	 * catalog between two calls and break the fingerprint invariant.
	 */
	mutable TMap<const UBlueprintNodeSpawner*, FString> DeferredSpawnerReasons;
	mutable int32 SessionTemplateCreations = 0;
};
