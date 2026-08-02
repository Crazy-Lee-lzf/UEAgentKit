#include "EditorBridge.h"
#include "EditorBridgeBatchTaskManager.h"

#include "Dom/JsonObject.h"

using namespace UEAgentKitBatchTaskPrivate;

namespace
{
	bool IsSafeTaskId(const FString& TaskId)
	{
		if (TaskId.IsEmpty() || TaskId.Len() > 64)
		{
			return false;
		}
		for (const TCHAR Character : TaskId)
		{
			if (!(FChar::IsHexDigit(Character) || Character == TEXT('-')))
			{
				return false;
			}
		}
		return true;
	}
}

bool FUEAgentKitEditorBridge::TryStartBatchTask(
	const FString& Operation,
	const int32 MaxActors,
	const int32 MaxComponentsPerActor,
	const int32 TimeoutSeconds,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (Operation != TEXT("scanCurrentWorld"))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("operation must be one of the registered Batch Task operations.");
		return false;
	}
	if (BatchTaskManager == nullptr)
	{
		OutErrorCode = TEXT("live-editor-unavailable");
		OutErrorMessage = TEXT("The Batch Task manager is unavailable.");
		return false;
	}
	FString TaskId;
	if (!BatchTaskManager->StartScanCurrentWorld(
		SessionId,
		MaxActors,
		MaxComponentsPerActor,
		TimeoutSeconds,
		TaskId,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	OutResult = BatchTaskManager->Status(TaskId);
	return OutResult.IsValid();
}

bool FUEAgentKitEditorBridge::BuildBatchTaskStatusResult(
	const FString& TaskId,
	const bool bIncludeDetails,
	const int32 DetailOffset,
	const int32 DetailLimit,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!IsSafeTaskId(TaskId))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("taskId is outside the bounded contract.");
		return false;
	}
	if (DetailOffset < 0 || DetailLimit < 1 || DetailLimit > MaxDetailPageItems)
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("detailOffset/detailLimit are outside the bounded paging contract.");
		return false;
	}
	if (BatchTaskManager == nullptr)
	{
		OutErrorCode = TEXT("live-editor-batch-task-not-found");
		OutErrorMessage = TEXT("No Batch Task is registered for this Editor session.");
		return false;
	}
	OutResult = BatchTaskManager->Status(TaskId, bIncludeDetails, DetailOffset, DetailLimit);
	if (!OutResult.IsValid())
	{
		OutErrorCode = TEXT("live-editor-batch-task-not-found");
		OutErrorMessage = TEXT("The requested Batch Task is not registered.");
		return false;
	}
	return true;
}

bool FUEAgentKitEditorBridge::BuildBatchTaskCancelResult(
	const FString& TaskId,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!IsSafeTaskId(TaskId))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("taskId is outside the bounded contract.");
		return false;
	}
	if (BatchTaskManager == nullptr)
	{
		OutErrorCode = TEXT("live-editor-batch-task-not-found");
		OutErrorMessage = TEXT("No Batch Task is registered for this Editor session.");
		return false;
	}
	OutResult = BatchTaskManager->Cancel(TaskId);
	if (!OutResult.IsValid())
	{
		OutErrorCode = TEXT("live-editor-batch-task-not-found");
		OutErrorMessage = TEXT("The requested Batch Task is not registered.");
		return false;
	}
	return true;
}
