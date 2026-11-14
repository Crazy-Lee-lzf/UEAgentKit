#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class AActor;
class UWorld;

/**
 * Bounded, read-only Play-In-Editor runtime inspection.
 *
 * Every entry point here is a pure read of Editor memory on the game thread:
 * nothing starts or stops PIE, loads a package, mutates an Actor or Component,
 * writes an asset, or opens the SQLite index.  Enumerations are hard capped and
 * every cap is reported back so a caller can tell a complete answer from a
 * bounded one.
 *
 * Identity rules:
 *  - A PIE world is addressed by its exact PIE instance id (FWorldContext
 *    PIEInstance, with UPackage PIEInstanceID as fallback) plus its NetMode.
 *  - An Actor is addressed by exactly one of actorPath, actorGuid, actorName or
 *    actorLabel inside that exact world.  A bare name/label that matches more
 *    than one Actor is rejected with bounded candidates instead of guessing.
 */
namespace UEAgentKitPieRuntime
{
	/** Hard caps. Requests are clamped into these boundaries. */
	inline constexpr int32 MaxPieWorldContexts = 32;
	inline constexpr int32 MaxWorldContextScan = 64;
	inline constexpr int32 MaxActorComponents = 128;
	inline constexpr int32 MaxComponentAssets = 32;
	inline constexpr int32 MaxClassHierarchy = 16;
	inline constexpr int32 MaxScannedActors = 65536;
	inline constexpr int32 MaxAmbiguousCandidates = 8;
	inline constexpr int32 DefaultMaxContexts = 16;
	inline constexpr int32 DefaultActorComponents = 64;
	inline constexpr int32 DefaultComponentAssets = 8;
	inline constexpr int32 RetiredPieInstanceHistory = 64;
	/** Scalar snapshot caps: one exact Actor/Component, named immediate fields. */
	inline constexpr int32 MaxScalarProperties = 8;
	inline constexpr int32 MaxScalarPropertyNameChars = 64;
	inline constexpr int32 MinScalarStringChars = 64;
	inline constexpr int32 DefaultScalarStringChars = 256;
	inline constexpr int32 MaxScalarStringChars = 512;
	/** Reflected declaring-class anchors are bounded like other scalar metadata. */
	inline constexpr int32 MaxScalarClassPathChars = 512;

	/** Instance lookup class. Codes stay distinct on the wire. */
	enum class ELookupStatus : uint8
	{
		Matched,
		NotRunning,
		NotFound,
		Stale,
	};

	/** Exact failure class of one inspection request. */
	enum class EFailure : uint8
	{
		None,
		InvalidRequest,
		InstanceNotRunning,
		InstanceNotFound,
		InstanceStale,
		InstanceAmbiguous,
		WorldStale,
		ActorNotFound,
		ActorAmbiguous,
		ActorStale,
		ComponentNotFound,
		ComponentAmbiguous,
		ComponentStale,
		ContextScanTruncated,
		ScanTruncated,
	};

	/** Stable wire code for one lookup status; Matched has no code. */
	const TCHAR* LookupStatusCode(ELookupStatus Status);

	/** Stable wire code for one failure class; None has no code. */
	const TCHAR* FailureCode(EFailure Failure);

	/** True when the failure belongs to the STALE family. */
	bool IsStaleFailure(EFailure Failure);

	/**
	 * Deterministic instance classification.
	 *  - A live id matches.
	 *  - A retired id is stale even while other PIE instances still run.
	 *  - With nothing live and no history, PIE is not running.
	 *  - Otherwise the requested instance is simply not found.
	 */
	ELookupStatus ClassifyInstanceLookup(
		int32 RequestedInstanceId,
		const TArray<int32>& LiveInstanceIds,
		const TArray<int32>& RetiredInstanceIds);

	/**
	 * Game-thread-only STALE history.  The retired list is a bounded FIFO of
	 * PIE instance ids that were observed running earlier in this process and
	 * have since disappeared, so "ended" and "never existed" stay distinct.
	 */
	void ObserveLivePieInstances(const TArray<int32>& LiveInstanceIds);
	void GetRetiredPieInstances(TArray<int32>& OutRetiredInstanceIds);
	void ResetPieInstanceHistoryForTests();

	struct FWorldContextSnapshot
	{
		int32 PieInstanceId = INDEX_NONE;
		int32 ContextIndex = INDEX_NONE;
		FString ContextId;
		FString WorldPath;
		FString WorldName;
		FString PackagePath;
		FString WorldType;
		FString NetModeName;
		FString ServerOrClient;
		int32 NetMode = -1;
		bool bIsServer = false;
		bool bIsClient = false;
		bool bIsDedicatedServer = false;
		bool bIsListenServer = false;
		bool bIsStandalone = false;
		bool bIsPrimaryPieInstance = false;
		bool bRunAsDedicated = false;
		int32 LocalPlayerCount = 0;
		bool bHasBegunPlay = false;
		bool bIsPaused = false;
		double TimeSeconds = 0.0;
		UWorld* World = nullptr;
	};

	/** One bounded pass over GEditor's live PIE world contexts. */
	void CollectPieWorldContexts(TArray<FWorldContextSnapshot>& OutContexts, bool& bOutTruncated);

	TSharedRef<FJsonObject> DescribeWorldContext(const FWorldContextSnapshot& Snapshot);

	/** Bounded listing; MaxContexts is clamped to [1, MaxPieWorldContexts]. */
	TSharedRef<FJsonObject> BuildWorldContextsJson(int32 MaxContexts = DefaultMaxContexts);

	struct FActorInspectionRequest
	{
		int32 PieInstanceId = INDEX_NONE;
		/** Optional expected world path; a mismatch is reported as stale. */
		FString WorldPath;
		FString ActorPath;
		FString ActorGuid;
		FString ActorName;
		FString ActorLabel;
		/** Optional expected Actor class path; a mismatch is reported as stale. */
		FString ActorClassPath;
		int32 MaxComponents = DefaultActorComponents;
		int32 MaxComponentAssets = DefaultComponentAssets;
	};

	/**
	 * Exact-world Actor inspection.  Returns false with OutErrorCode set to one
	 * of the stable EFailure codes; nothing is guessed and nothing is mutated.
	 */
	bool TryDescribePieActor(
		const FActorInspectionRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Resolved exact world + Actor pair; the snapshot is owned by value. */
	struct FResolvedPieActor
	{
		FWorldContextSnapshot Snapshot;
		AActor* Actor = nullptr;
	};

	/**
	 * Shared exact-identity resolver used by Actor description and scalar reads.
	 * Validates the instance id, duplicate live contexts, optional world path,
	 * exactly one Actor identity, ambiguity, destruction and optional class path.
	 */
	EFailure ResolvePieActorExact(
		const FActorInspectionRequest& Request,
		FResolvedPieActor& OutResolved,
		FString& OutErrorMessage);

	/** One bounded read of explicitly named immediate scalar FProperties. */
	struct FScalarInspectionRequest
	{
		FActorInspectionRequest Actor;
		/** Optional owned Component identity: at most one of path or name. */
		FString ComponentPath;
		FString ComponentName;
		/** Optional expected Component class path; a mismatch is stale. */
		FString ComponentClassPath;
		/** Explicit property names only (no dotted paths, no collections). */
		TArray<FString> PropertyNames;
		int32 MaxStringChars = DefaultScalarStringChars;
	};

	/**
	 * Bounded, read-only scalar read for one exact PIE Actor (or one exact owned
	 * Component).  Missing / non-exposed / unsupported properties are reported
	 * per property; nothing is fabricated and no UObject graph is traversed.
	 */
	bool TryReadPieScalars(
		const FScalarInspectionRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);
}
