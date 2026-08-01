#include "EditorBridgeBatchTaskManager.h"
#include "EditorBridgeHandlerUtils.h"

#include "Components/ActorComponent.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "EngineUtils.h"
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

		for (TActorIterator<AActor> It(World); It; ++It)
		{
			AActor* Actor = *It;
			if (IsValid(Actor) && !Actor->IsActorBeingDestroyed())
			{
				NewTask.Actors.Add(Actor);
			}
		}
		if (NewTask.Actors.Num() > NewTask.MaxActors)
		{
			NewTask.bActorLimitReached = true;
			NewTask.Actors.SetNum(NewTask.MaxActors);
		}

		Task = MakeUnique<FScanTask>(MoveTemp(NewTask));
		OutTaskId = Task->TaskId;
		return true;
	}

	TSharedPtr<FJsonObject> FBatchTaskManager::Status(const FString& TaskId) const
	{
		if (!Task.IsValid() || Task->TaskId != TaskId)
		{
			return nullptr;
		}
		return BuildSnapshot(*Task, Task->State != ETaskState::Running);
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
		return BuildSnapshot(*Task, true);
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

		int32 ProcessedThisTick = 0;
		while (Task->Cursor < Task->Actors.Num() && ProcessedThisTick < MaxActorsPerTick)
		{
			AActor* Actor = Task->Actors[Task->Cursor];
			++Task->Cursor;
			++ProcessedThisTick;
			if (!IsValid(Actor))
			{
				continue;
			}
			ProcessActor(*Task, Actor);
		}
		if (Task->Cursor >= Task->Actors.Num())
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

		int32 ComponentCount = 0;
		bool bComponentsTruncated = false;
		TArray<TSharedPtr<FJsonValue>> ComponentItems;
		const TSet<UActorComponent*>& Components = Actor->GetComponents();
		for (UActorComponent* Component : Components)
		{
			if (!IsValid(Component))
			{
				continue;
			}
			++ComponentCount;
			++ScanTask.TotalComponentCount;
			if (ComponentItems.Num() >= ScanTask.MaxComponentsPerActor)
			{
				bComponentsTruncated = true;
				continue;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("name"), Component->GetName().Left(128));
			Item->SetStringField(
				TEXT("classPath"),
				Component->GetClass() != nullptr ? Component->GetClass()->GetPathName() : FString());
			Item->SetBoolField(TEXT("nativeClass"), Component->IsNative());
			ComponentItems.Add(MakeShared<FJsonValueObject>(Item));
		}
		if (bComponentsTruncated)
		{
			++ScanTask.ComponentLimitActorCount;
		}
		if (ScanTask.DetailItems.Num() < MaxDetailedActors)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("actorGuid"), Actor->GetActorGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetStringField(TEXT("label"), Actor->GetActorLabel().Left(256));
			Item->SetStringField(TEXT("classPath"), ClassPath.Left(512));
			Item->SetStringField(TEXT("actorPath"), Actor->GetPathName().Left(512));
			Item->SetStringField(
				TEXT("levelPath"),
				Actor->GetLevel() != nullptr ? Actor->GetLevel()->GetPathName().Left(512) : FString());
			Item->SetNumberField(TEXT("componentCount"), ComponentCount);
			Item->SetBoolField(TEXT("componentsTruncated"), bComponentsTruncated);
			Item->SetArrayField(TEXT("components"), ComponentItems);
			ScanTask.DetailItems.Add(Item);
		}
		else
		{
			ScanTask.bDetailsTruncated = true;
		}
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

	TSharedRef<FJsonObject> FBatchTaskManager::BuildSnapshot(const FScanTask& ScanTask, const bool bIncludeDetails) const
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
		const int32 TotalActors = ScanTask.Actors.Num();
		const int32 CompletedPercent = TotalActors <= 0
			? 100
			: FMath::Clamp(FMath::RoundToInt32(ScanTask.Cursor * 100.0 / TotalActors), 0, 100);
		double EstimatedRemainingSeconds = 0.0;
		if (ScanTask.Cursor > 0 && TotalActors > ScanTask.Cursor)
		{
			EstimatedRemainingSeconds = ElapsedSeconds
				* static_cast<double>(TotalActors - ScanTask.Cursor)
				/ static_cast<double>(ScanTask.Cursor);
		}
		Progress->SetNumberField(TEXT("processedActors"), ScanTask.Cursor);
		Progress->SetNumberField(TEXT("totalActors"), TotalActors);
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
			Item->SetStringField(TEXT("classPath"), Pair.Key.Left(512));
			Item->SetNumberField(TEXT("count"), Pair.Value);
			ClassCounts.Add(MakeShared<FJsonValueObject>(Item));
			++Reported;
		}
		Summary->SetArrayField(TEXT("actorClassCounts"), ClassCounts);
		Summary->SetBoolField(TEXT("actorClassCountsTruncated"), ScanTask.ActorClassCounts.Num() > MaxActorClassesReported);
		TSharedRef<FJsonObject> Limits = MakeShared<FJsonObject>();
		Limits->SetNumberField(TEXT("maxActors"), ScanTask.MaxActors);
		Limits->SetNumberField(TEXT("maxComponentsPerActor"), ScanTask.MaxComponentsPerActor);
		Limits->SetBoolField(TEXT("actorLimitReached"), ScanTask.bActorLimitReached);
		Limits->SetNumberField(TEXT("componentLimitActorCount"), ScanTask.ComponentLimitActorCount);
		Summary->SetObjectField(TEXT("limits"), Limits);
		Result->SetObjectField(TEXT("summary"), Summary);

		if (bIncludeDetails)
		{
			TSharedRef<FJsonObject> Details = MakeShared<FJsonObject>();
			Details->SetNumberField(TEXT("actorCount"), ScanTask.DetailItems.Num());
			TArray<TSharedPtr<FJsonValue>> Items;
			for (const TSharedRef<FJsonObject>& Item : ScanTask.DetailItems)
			{
				Items.Add(MakeShared<FJsonValueObject>(Item));
			}
			Details->SetArrayField(TEXT("items"), Items);
			Details->SetBoolField(TEXT("truncated"), ScanTask.bDetailsTruncated);
			Result->SetObjectField(TEXT("details"), Details);
		}

		Result->SetNumberField(TEXT("durationMs"), FMath::RoundToInt32(ElapsedSeconds * 1000.0));
		return Result;
	}
}
