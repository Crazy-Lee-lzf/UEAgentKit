#include "EditorBridgeBatchTaskManager.h"
#include "EditorBridgeHandlerUtils.h"

#include "Components/ActorComponent.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Level.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/PlatformTime.h"
#include "Misc/DateTime.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace UEAgentKitBatchTaskPrivate
{
	namespace
	{
		int32 ClampLimit(const int32 Value, const int32 Minimum, const int32 Maximum)
		{
			return FMath::Clamp(Value, Minimum, Maximum);
		}

		FString StateName(const ETaskState State)
		{
			switch (State)
			{
			case ETaskState::Completed:
				return TEXT("completed");
			case ETaskState::Cancelled:
				return TEXT("cancelled");
			case ETaskState::Failed:
				return TEXT("failed");
			case ETaskState::TimedOut:
				return TEXT("timed-out");
			case ETaskState::Invalidated:
				return TEXT("invalidated");
			case ETaskState::Running:
			default:
				return TEXT("running");
			}
		}
	}

	void FBatchTaskManager::Reset()
	{
		Task.Reset();
	}

	bool FBatchTaskManager::StartScanCurrentWorld(
		const FString& EditorSessionId,
		const int32 MaxActors,
		const int32 MaxComponentsPerActor,
		const int32 TimeoutSeconds,
		FString& OutTaskId,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (Task.IsValid() && Task->State == ETaskState::Running)
		{
			OutErrorCode = TEXT("live-editor-batch-task-busy");
			OutErrorMessage = TEXT("Another Batch Task is already active.");
			return false;
		}
		if (GEditor == nullptr)
		{
			OutErrorCode = TEXT("live-editor-unavailable");
			OutErrorMessage = TEXT("The Unreal Editor is unavailable.");
			return false;
		}
		if (GEditor->PlayWorld != nullptr)
		{
			OutErrorCode = TEXT("live-editor-pie-active");
			OutErrorMessage = TEXT("Batch Tasks are unavailable while PIE or SIE is active.");
			return false;
		}
		UWorld* World = GetEditorWorld();
		if (World == nullptr)
		{
			OutErrorCode = TEXT("live-editor-unavailable");
			OutErrorMessage = TEXT("The Editor World is unavailable.");
			return false;
		}

		FScanTask NewTask;
		NewTask.TaskId = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphensLower);
		NewTask.Operation = TEXT("scanCurrentWorld");
		NewTask.EditorSessionId = EditorSessionId;
		NewTask.StartedAtUtc = FDateTime::UtcNow().ToIso8601();
		NewTask.StartSeconds = FPlatformTime::Seconds();
		NewTask.MaxActors = ClampLimit(MaxActors, 1, MaxActorsPerScan);
		NewTask.MaxComponentsPerActor = ClampLimit(MaxComponentsPerActor, 1, MaxComponentsPerActorLimit);
		NewTask.DeadlineSeconds = NewTask.StartSeconds + static_cast<double>(ClampLimit(TimeoutSeconds, 1, MaxTimeoutSeconds));
		NewTask.CapturedWorldId = World->GetUniqueID();
		NewTask.WorldName = World->GetName();
		NewTask.WorldPath = World->GetPathName();
		NewTask.WorldType = GetWorldTypeName(World->WorldType);

		for (ULevel* Level : World->GetLevels())
		{
			if (!IsValid(Level))
			{
				continue;
			}
			NewTask.Levels.Add(Level);
			NewTask.TotalActorSlots += Level->Actors.Num();
		}

		Task = MakeUnique<FScanTask>(MoveTemp(NewTask));
		OutTaskId = Task->TaskId;
		return true;
	}

	TSharedPtr<FJsonObject> FBatchTaskManager::Status(
		const FString& TaskId,
		const bool bIncludeDetails,
		const int32 DetailOffset,
		const int32 DetailLimit) const
	{
		if (!Task.IsValid() || Task->TaskId != TaskId)
		{
			return nullptr;
		}
		return BuildSnapshot(*Task, bIncludeDetails, DetailOffset, DetailLimit);
	}

	TSharedPtr<FJsonObject> FBatchTaskManager::Cancel(const FString& TaskId)
	{
		if (!Task.IsValid() || Task->TaskId != TaskId)
		{
			return nullptr;
		}
		if (Task->State == ETaskState::Running)
		{
			CompleteTask(ETaskState::Cancelled, FString(), FString());
		}
		return BuildSnapshot(*Task, false, 0, MaxDetailPageItems);
	}

	void FBatchTaskManager::Tick()
	{
		if (!Task.IsValid() || Task->State != ETaskState::Running)
		{
			return;
		}
		const double Now = FPlatformTime::Seconds();
		if (GEditor == nullptr || GEditor->PlayWorld != nullptr)
		{
			CompleteTask(
				ETaskState::Invalidated,
				TEXT("live-editor-batch-task-world-invalidated"),
				TEXT("The Batch Task was invalidated because PIE or SIE became active."));
			return;
		}
		UWorld* World = GetEditorWorld();
		if (World == nullptr || World->GetUniqueID() != Task->CapturedWorldId)
		{
			CompleteTask(
				ETaskState::Invalidated,
				TEXT("live-editor-batch-task-world-invalidated"),
				TEXT("The Batch Task was invalidated because the World changed."));
			return;
		}
		if (Now >= Task->DeadlineSeconds)
		{
			CompleteTask(
				ETaskState::TimedOut,
				TEXT("live-editor-batch-task-timeout"),
				TEXT("The Batch Task exceeded its bounded runtime."));
			return;
		}

		const double TickStarted = FPlatformTime::Seconds();
		int32 ScannedThisTick = 0;
		while (Task->LevelCursor < Task->Levels.Num() && ScannedThisTick < MaxActorSlotsPerTick)
		{
			if (ScannedThisTick > 0 && FPlatformTime::Seconds() - TickStarted >= MaxTickBudgetSeconds)
			{
				break;
			}

			ULevel* Level = Task->Levels[Task->LevelCursor].Get();
			if (!IsValid(Level))
			{
				++Task->LevelCursor;
				Task->ActorCursor = 0;
				continue;
			}
			if (Task->ActorCursor >= Level->Actors.Num())
			{
				++Task->LevelCursor;
				Task->ActorCursor = 0;
				continue;
			}

			AActor* Actor = Level->Actors[Task->ActorCursor].Get();
			++Task->ActorCursor;
			++Task->ScannedActorSlots;
			++ScannedThisTick;
			if (!IsValid(Actor) || Actor->IsActorBeingDestroyed())
			{
				continue;
			}

			ProcessActor(*Task, Actor);
			if (Task->ValidActorCount >= Task->MaxActors)
			{
				Task->bActorLimitReached = Task->ScannedActorSlots < Task->TotalActorSlots;
				CompleteTask(ETaskState::Completed, FString(), FString());
				return;
			}
		}
		if (Task->LevelCursor >= Task->Levels.Num())
		{
			CompleteTask(ETaskState::Completed, FString(), FString());
		}
	}

	void FBatchTaskManager::ProcessActor(FScanTask& ScanTask, AActor* Actor)
	{
		if (!IsValid(Actor))
		{
			return;
		}
		++ScanTask.ValidActorCount;
		const FString ClassPath = Actor->GetClass() != nullptr ? Actor->GetClass()->GetPathName() : FString();
		ScanTask.ActorClassCounts.FindOrAdd(ClassPath) += 1;

		const TSet<UActorComponent*>& Components = Actor->GetComponents();
		const int32 ComponentCount = Components.Num();
		ScanTask.TotalComponentCount += ComponentCount;
		if (ScanTask.DetailItems.Num() >= MaxDetailedActors)
		{
			ScanTask.bDetailsTruncated = true;
			return;
		}

		TArray<TSharedPtr<FJsonValue>> ComponentItems;
		int32 SerializedComponentCount = 0;
		for (UActorComponent* Component : Components)
		{
			if (!IsValid(Component))
			{
				continue;
			}
			if (SerializedComponentCount >= ScanTask.MaxComponentsPerActor)
			{
				break;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("name"), Component->GetName().Left(128));
			Item->SetStringField(
				TEXT("classPath"),
				Component->GetClass() != nullptr ? Component->GetClass()->GetPathName().Left(256) : FString());
			Item->SetBoolField(TEXT("nativeClass"), Component->IsNative());
			ComponentItems.Add(MakeShared<FJsonValueObject>(Item));
			++SerializedComponentCount;
		}
		const bool bComponentsTruncated = ComponentCount > SerializedComponentCount;
		if (bComponentsTruncated)
		{
			++ScanTask.ComponentLimitActorCount;
		}

		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("actorGuid"), Actor->GetActorGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
		Item->SetStringField(TEXT("label"), Actor->GetActorLabel().Left(256));
		Item->SetStringField(TEXT("classPath"), ClassPath.Left(256));
		Item->SetStringField(TEXT("actorPath"), Actor->GetPathName().Left(384));
		Item->SetStringField(
			TEXT("levelPath"),
			Actor->GetLevel() != nullptr ? Actor->GetLevel()->GetPathName().Left(384) : FString());
		Item->SetNumberField(TEXT("componentCount"), ComponentCount);
		Item->SetBoolField(TEXT("componentsTruncated"), bComponentsTruncated);
		Item->SetArrayField(TEXT("components"), ComponentItems);
		ScanTask.DetailItems.Add(Item);
	}

	void FBatchTaskManager::CompleteTask(const ETaskState State, const FString& FailureCode, const FString& FailureMessage)
	{
		if (!Task.IsValid())
		{
			return;
		}
		Task->State = State;
		Task->FailureCode = FailureCode;
		Task->FailureMessage = FailureMessage;
		Task->CompletedSeconds = FPlatformTime::Seconds();
		Task->CompletedAtUtc = FDateTime::UtcNow().ToIso8601();
	}

	TSharedRef<FJsonObject> FBatchTaskManager::BuildSnapshot(
		const FScanTask& ScanTask,
		const bool bIncludeDetails,
		const int32 DetailOffset,
		const int32 DetailLimit) const
	{
		const double Now = FPlatformTime::Seconds();
		const double ElapsedSeconds = ScanTask.State == ETaskState::Running
			? FMath::Max(0.0, Now - ScanTask.StartSeconds)
			: FMath::Max(0.0, ScanTask.CompletedSeconds - ScanTask.StartSeconds);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("taskId"), ScanTask.TaskId);
		Result->SetStringField(TEXT("operation"), ScanTask.Operation);
		Result->SetStringField(TEXT("state"), StateName(ScanTask.State));
		Result->SetStringField(TEXT("editorSessionId"), ScanTask.EditorSessionId);
		Result->SetStringField(TEXT("startedAtUtc"), ScanTask.StartedAtUtc);
		Result->SetBoolField(TEXT("partialResultAvailable"), ScanTask.DetailItems.Num() > 0);
		if (ScanTask.State != ETaskState::Running)
		{
			Result->SetStringField(TEXT("completedAtUtc"), ScanTask.CompletedAtUtc);
		}
		if (!ScanTask.FailureCode.IsEmpty())
		{
			Result->SetStringField(TEXT("errorCode"), ScanTask.FailureCode);
		}
		if (!ScanTask.FailureMessage.IsEmpty())
		{
			Result->SetStringField(TEXT("errorMessage"), ScanTask.FailureMessage);
		}

		TSharedRef<FJsonObject> Progress = MakeShared<FJsonObject>();
		const int32 MaximumProgress = ScanTask.State == ETaskState::Running ? 99 : 100;
		const int32 CompletedPercent = ScanTask.State == ETaskState::Completed
			? 100
			: ScanTask.TotalActorSlots <= 0
				? 0
				: FMath::Clamp(
					FMath::RoundToInt32(ScanTask.ScannedActorSlots * 100.0 / ScanTask.TotalActorSlots), 0, MaximumProgress);
		double EstimatedRemainingSeconds = 0.0;
		if (ScanTask.ScannedActorSlots > 0 && ScanTask.TotalActorSlots > ScanTask.ScannedActorSlots)
		{
			EstimatedRemainingSeconds = ElapsedSeconds
				* static_cast<double>(ScanTask.TotalActorSlots - ScanTask.ScannedActorSlots)
				/ static_cast<double>(ScanTask.ScannedActorSlots);
		}
		Progress->SetStringField(TEXT("phase"), ScanTask.State == ETaskState::Running ? TEXT("scanning") : TEXT("terminal"));
		Progress->SetNumberField(TEXT("processedActors"), ScanTask.ValidActorCount);
		Progress->SetNumberField(TEXT("scannedActorSlots"), ScanTask.ScannedActorSlots);
		Progress->SetNumberField(TEXT("totalActorSlots"), ScanTask.TotalActorSlots);
		Progress->SetNumberField(TEXT("completedPercent"), CompletedPercent);
		Progress->SetNumberField(TEXT("elapsedSeconds"), FMath::RoundToFloat(static_cast<float>(ElapsedSeconds * 1000.0)) / 1000.0f);
		Progress->SetNumberField(TEXT("estimatedRemainingSeconds"), FMath::RoundToFloat(static_cast<float>(EstimatedRemainingSeconds * 1000.0)) / 1000.0f);
		Result->SetObjectField(TEXT("progress"), Progress);

		TSharedRef<FJsonObject> World = MakeShared<FJsonObject>();
		World->SetStringField(TEXT("name"), ScanTask.WorldName);
		World->SetStringField(TEXT("path"), ScanTask.WorldPath);
		World->SetNumberField(TEXT("worldId"), ScanTask.CapturedWorldId);
		World->SetStringField(TEXT("worldType"), ScanTask.WorldType);
		Result->SetObjectField(TEXT("world"), World);

		TSharedRef<FJsonObject> Summary = MakeShared<FJsonObject>();
		Summary->SetNumberField(TEXT("actorCount"), ScanTask.ValidActorCount);
		Summary->SetNumberField(TEXT("totalComponentCount"), ScanTask.TotalComponentCount);
		Summary->SetNumberField(TEXT("availableDetailCount"), ScanTask.DetailItems.Num());
		TArray<TSharedPtr<FJsonValue>> ClassCounts;
		TArray<TPair<FString, int32>> ClassCountList;
		for (const TPair<FString, int32>& Pair : ScanTask.ActorClassCounts)
		{
			ClassCountList.Add(Pair);
		}
		ClassCountList.Sort(
			[](const TPair<FString, int32>& Left, const TPair<FString, int32>& Right)
			{
				return Left.Value > Right.Value;
			});
		int32 Reported = 0;
		for (const TPair<FString, int32>& Pair : ClassCountList)
		{
			if (Reported >= MaxActorClassesReported)
			{
				break;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("classPath"), Pair.Key.Left(256));
			Item->SetNumberField(TEXT("count"), Pair.Value);
			ClassCounts.Add(MakeShared<FJsonValueObject>(Item));
			++Reported;
		}
		Summary->SetArrayField(TEXT("actorClassCounts"), ClassCounts);
		Summary->SetBoolField(TEXT("actorClassCountsTruncated"), ScanTask.ActorClassCounts.Num() > MaxActorClassesReported);
		TSharedRef<FJsonObject> Limits = MakeShared<FJsonObject>();
		Limits->SetNumberField(TEXT("maxActors"), ScanTask.MaxActors);
		Limits->SetNumberField(TEXT("maxComponentsPerActor"), ScanTask.MaxComponentsPerActor);
		Limits->SetNumberField(TEXT("maxDetailedActors"), MaxDetailedActors);
		Limits->SetNumberField(TEXT("maxDetailPageItems"), MaxDetailPageItems);
		Limits->SetNumberField(TEXT("maxTickBudgetMs"), MaxTickBudgetSeconds * 1000.0);
		Limits->SetBoolField(TEXT("actorLimitReached"), ScanTask.bActorLimitReached);
		Limits->SetNumberField(TEXT("componentLimitActorCount"), ScanTask.ComponentLimitActorCount);
		Summary->SetObjectField(TEXT("limits"), Limits);
		Result->SetObjectField(TEXT("summary"), Summary);

		if (bIncludeDetails)
		{
			const int32 Offset = FMath::Clamp(DetailOffset, 0, ScanTask.DetailItems.Num());
			const int32 Limit = FMath::Clamp(DetailLimit, 1, MaxDetailPageItems);
			const int32 End = FMath::Min(Offset + Limit, ScanTask.DetailItems.Num());
			TSharedRef<FJsonObject> Details = MakeShared<FJsonObject>();
			TArray<TSharedPtr<FJsonValue>> Items;
			for (int32 Index = Offset; Index < End; ++Index)
			{
				Items.Add(MakeShared<FJsonValueObject>(ScanTask.DetailItems[Index]));
			}
			const bool bHasMore = End < ScanTask.DetailItems.Num();
			Details->SetNumberField(TEXT("offset"), Offset);
			Details->SetNumberField(TEXT("limit"), Limit);
			Details->SetNumberField(TEXT("returnedCount"), Items.Num());
			Details->SetNumberField(TEXT("totalAvailable"), ScanTask.DetailItems.Num());
			Details->SetBoolField(TEXT("hasMore"), bHasMore);
			if (bHasMore)
			{
				Details->SetNumberField(TEXT("nextOffset"), End);
			}
			Details->SetArrayField(TEXT("items"), Items);
			Details->SetBoolField(TEXT("truncated"), ScanTask.bDetailsTruncated || bHasMore);
			Result->SetObjectField(TEXT("details"), Details);
		}

		Result->SetNumberField(TEXT("durationMs"), FMath::RoundToInt32(ElapsedSeconds * 1000.0));
		return Result;
	}
}
