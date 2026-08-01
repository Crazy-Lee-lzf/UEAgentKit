#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class AActor;

namespace UEAgentKitBatchTaskPrivate
{
	enum class ETaskState : uint8
	{
		Running,
		Completed,
		Cancelled,
		Failed,
		TimedOut,
		Invalidated
	};

	constexpr int32 MaxConcurrentTasks = 1;
	constexpr int32 MaxActorsPerScan = 10000;
	constexpr int32 MaxComponentsPerActorLimit = 500;
	constexpr int32 MaxDetailedActors = 200;
	constexpr int32 MaxActorClassesReported = 50;
	constexpr int32 MaxActorsPerTick = 250;
	constexpr int32 MaxTimeoutSeconds = 300;

	struct FScanTask
	{
		FString TaskId;
		FString Operation;
		FString EditorSessionId;
		FString StartedAtUtc;
		FString CompletedAtUtc;
		double StartSeconds = 0.0;
		double CompletedSeconds = 0.0;
		double DeadlineSeconds = 0.0;
		int32 MaxActors = 2000;
		int32 MaxComponentsPerActor = 200;
		int32 CapturedWorldId = 0;
		FString WorldName;
		FString WorldPath;
		FString WorldType;
		ETaskState State = ETaskState::Running;
		FString FailureCode;
		FString FailureMessage;
		bool bActorLimitReached = false;
		bool bDetailsTruncated = false;
		int32 ComponentLimitActorCount = 0;
		int32 TotalComponentCount = 0;
		int32 Cursor = 0;
		int32 ValidActorCount = 0;
		TArray<AActor*> Actors;
		TMap<FString, int32> ActorClassCounts;
		TArray<TSharedRef<FJsonObject>> DetailItems;
	};

	class FBatchTaskManager final
	{
	public:
		void Reset();
		bool StartScanCurrentWorld(
			const FString& EditorSessionId,
			int32 MaxActors,
			int32 MaxComponentsPerActor,
			int32 TimeoutSeconds,
			FString& OutTaskId,
			FString& OutErrorCode,
			FString& OutErrorMessage);
		TSharedPtr<FJsonObject> Status(const FString& TaskId) const;
		TSharedPtr<FJsonObject> Cancel(const FString& TaskId);
		void Tick();

	private:
		void ProcessActor(FScanTask& ScanTask, AActor* Actor);
		void CompleteTask(ETaskState State, const FString& FailureCode, const FString& FailureMessage);
		TSharedRef<FJsonObject> BuildSnapshot(const FScanTask& ScanTask, bool bIncludeDetails) const;
		TUniquePtr<FScanTask> Task;
	};
}
