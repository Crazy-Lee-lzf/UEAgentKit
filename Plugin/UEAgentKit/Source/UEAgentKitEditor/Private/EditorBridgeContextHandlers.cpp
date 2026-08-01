#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/PlatformTime.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace UEAgentKitEditorContextPrivate
{
	constexpr int32 ContextMaxSelectionItems = 50;
	constexpr int32 ContextMaxOpenAssets = 50;
	constexpr int32 ContextMaxDirtyPackages = 50;
	constexpr int32 ContextMaxCompileDiagnostics = 20;
	constexpr int32 ContextMaxGraphNodes = 20;
	constexpr int32 ContextMaxOutputLogEntries = 1;

	class FStageTimer
	{
	public:
		FStageTimer()
			: StartSeconds(FPlatformTime::Seconds())
		{
		}

		int64 Stop()
		{
			StageSeconds = FPlatformTime::Seconds() - StartSeconds;
			return FMath::RoundToInt32(StageSeconds * 1000.0);
		}

	private:
		double StartSeconds = 0.0;
		double StageSeconds = 0.0;
	};

	void TrimItems(const TSharedRef<FJsonObject>& Section, const FString& ItemsField, const int32 MaxItems)
	{
		const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
		if (!Section->TryGetArrayField(ItemsField, Items) || Items == nullptr)
		{
			return;
		}
		TArray<TSharedPtr<FJsonValue>> Trimmed;
		for (int32 Index = 0; Index < FMath::Min(Items->Num(), MaxItems); ++Index)
		{
			Trimmed.Add((*Items)[Index]);
		}
		Section->SetArrayField(ItemsField, Trimmed);
		if (Items->Num() > MaxItems)
		{
			Section->SetBoolField(TEXT("truncated"), true);
		}
	}

	TSharedRef<FJsonObject> BuildNextAction(const FString& Tool, const FString& Reason)
	{
		TSharedRef<FJsonObject> Action = MakeShared<FJsonObject>();
		Action->SetStringField(TEXT("tool"), Tool);
		Action->SetStringField(TEXT("reason"), Reason);
		return Action;
	}
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildEditorContextResult() const
{
	using namespace UEAgentKitEditorContextPrivate;

	const double StartSeconds = FPlatformTime::Seconds();
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	TSharedRef<FJsonObject> StageDurations = MakeShared<FJsonObject>();
	TArray<TSharedPtr<FJsonValue>> NextActions;

	FStageTimer EditorTimer;
	const TSharedRef<FJsonObject> EditorSection = BuildStatusResult();
	EditorSection->RemoveField(TEXT("capabilities"));
	StageDurations->SetNumberField(TEXT("editor"), EditorTimer.Stop());

	FStageTimer WorldTimer;
	const TSharedRef<FJsonObject> WorldSection = BuildCurrentLevelResult();
	StageDurations->SetNumberField(TEXT("world"), WorldTimer.Stop());

	FStageTimer SelectionTimer;
	const TSharedRef<FJsonObject> SelectionSection = BuildSelectionResult();
	TrimItems(SelectionSection, TEXT("items"), ContextMaxSelectionItems);
	StageDurations->SetNumberField(TEXT("selection"), SelectionTimer.Stop());

	FStageTimer OpenAssetsTimer;
	const TSharedRef<FJsonObject> OpenAssetsSection = BuildOpenAssetsResult();
	TrimItems(OpenAssetsSection, TEXT("items"), ContextMaxOpenAssets);
	StageDurations->SetNumberField(TEXT("openAssets"), OpenAssetsTimer.Stop());

	FStageTimer DirtyPackagesTimer;
	const TSharedRef<FJsonObject> DirtyPackagesSection = BuildDirtyAssetsResult();
	TrimItems(DirtyPackagesSection, TEXT("items"), ContextMaxDirtyPackages);
	StageDurations->SetNumberField(TEXT("dirtyPackages"), DirtyPackagesTimer.Stop());

	FStageTimer GraphTimer;
	const TSharedRef<FJsonObject> GraphSection = BuildBlueprintGraphSelectionResult();
	TrimItems(GraphSection, TEXT("selectedNodes"), ContextMaxGraphNodes);
	StageDurations->SetNumberField(TEXT("blueprintGraphSelection"), GraphTimer.Stop());

	FStageTimer CompileTimer;
	TSharedPtr<FJsonObject> CompileParams = MakeShared<FJsonObject>();
	CompileParams->SetNumberField(TEXT("limit"), static_cast<double>(ContextMaxCompileDiagnostics));
	const TSharedRef<FJsonObject> CompileSection = BuildCompileErrorsResult(CompileParams);
	CompileSection->RemoveField(TEXT("loadedBlueprints"));
	StageDurations->SetNumberField(TEXT("compileErrors"), CompileTimer.Stop());

	FStageTimer LogTimer;
	TSharedPtr<FJsonObject> LogParams = MakeShared<FJsonObject>();
	LogParams->SetNumberField(TEXT("limit"), static_cast<double>(ContextMaxOutputLogEntries));
	const TSharedRef<FJsonObject> LogResult = BuildOutputLogResult(LogParams);
	TSharedRef<FJsonObject> LogCursor = MakeShared<FJsonObject>();
	bool bLogAvailable = false;
	LogResult->TryGetBoolField(TEXT("available"), bLogAvailable);
	LogCursor->SetBoolField(TEXT("available"), bLogAvailable);
	FString CaptureStartedUtc;
	LogResult->TryGetStringField(TEXT("captureStartedUtc"), CaptureStartedUtc);
	LogCursor->SetStringField(TEXT("captureStartedUtc"), CaptureStartedUtc);
	double NumberValue = 0.0;
	LogResult->TryGetNumberField(TEXT("oldestSequence"), NumberValue);
	LogCursor->SetNumberField(TEXT("oldestSequence"), NumberValue);
	LogResult->TryGetNumberField(TEXT("newestSequence"), NumberValue);
	LogCursor->SetNumberField(TEXT("newestSequence"), NumberValue);
	LogResult->TryGetNumberField(TEXT("nextSequence"), NumberValue);
	LogCursor->SetNumberField(TEXT("nextSequence"), NumberValue);
	LogResult->TryGetNumberField(TEXT("droppedCount"), NumberValue);
	LogCursor->SetNumberField(TEXT("droppedCount"), NumberValue);
	bool bLogTruncated = false;
	LogResult->TryGetBoolField(TEXT("truncated"), bLogTruncated);
	LogCursor->SetBoolField(TEXT("truncated"), bLogTruncated);
	StageDurations->SetNumberField(TEXT("outputLogCursor"), LogTimer.Stop());

	if (CompileSection->TryGetNumberField(TEXT("diagnosticCount"), NumberValue) && NumberValue > 0.0)
	{
		NextActions.Add(MakeShared<FJsonValueObject>(BuildNextAction(TEXT("ue_get_compile_errors"), TEXT("compile-errors-present"))));
	}
	if (DirtyPackagesSection->TryGetNumberField(TEXT("count"), NumberValue) && NumberValue > 0.0)
	{
		NextActions.Add(MakeShared<FJsonValueObject>(BuildNextAction(TEXT("ue_get_dirty_assets"), TEXT("dirty-packages-present"))));
	}
	bool bTruncated = false;
	if (SelectionSection->TryGetBoolField(TEXT("truncated"), bTruncated) && bTruncated)
	{
		NextActions.Add(MakeShared<FJsonValueObject>(BuildNextAction(TEXT("ue_get_selection"), TEXT("selection-truncated"))));
	}
	if (OpenAssetsSection->TryGetBoolField(TEXT("truncated"), bTruncated) && bTruncated)
	{
		NextActions.Add(MakeShared<FJsonValueObject>(BuildNextAction(TEXT("ue_get_open_assets"), TEXT("open-assets-truncated"))));
	}
	bool bGraphAvailable = false;
	if (GraphSection->TryGetBoolField(TEXT("available"), bGraphAvailable) && bGraphAvailable)
	{
		NextActions.Add(MakeShared<FJsonValueObject>(BuildNextAction(TEXT("ue_get_blueprint_graph_selection"), TEXT("blueprint-graph-focused"))));
	}
	FString PieState;
	EditorSection->TryGetStringField(TEXT("pieState"), PieState);
	if (PieState == TEXT("playing") || PieState == TEXT("simulating"))
	{
		NextActions.Add(MakeShared<FJsonValueObject>(BuildNextAction(TEXT("ue_get_pie_state"), TEXT("pie-active"))));
	}
	if (LogCursor->TryGetNumberField(TEXT("newestSequence"), NumberValue) && NumberValue > 0.0)
	{
		NextActions.Add(MakeShared<FJsonValueObject>(BuildNextAction(TEXT("ue_get_output_log"), TEXT("incremental-log-available"))));
	}

	FString EditorState;
	EditorSection->TryGetStringField(TEXT("state"), EditorState);
	Result->SetStringField(TEXT("source"), TEXT("live-editor-memory"));
	Result->SetStringField(TEXT("state"), EditorState.IsEmpty() ? TEXT("available") : EditorState);
	Result->SetObjectField(TEXT("editor"), EditorSection);
	Result->SetObjectField(TEXT("world"), WorldSection);
	Result->SetObjectField(TEXT("selection"), SelectionSection);
	Result->SetObjectField(TEXT("openAssets"), OpenAssetsSection);
	Result->SetObjectField(TEXT("dirtyPackages"), DirtyPackagesSection);
	Result->SetObjectField(TEXT("blueprintGraphSelection"), GraphSection);
	Result->SetObjectField(TEXT("compileErrors"), CompileSection);
	Result->SetObjectField(TEXT("outputLogCursor"), LogCursor);
	Result->SetNumberField(TEXT("durationMs"), FMath::RoundToInt32((FPlatformTime::Seconds() - StartSeconds) * 1000.0));
	Result->SetObjectField(TEXT("stageDurationsMs"), StageDurations);
	Result->SetArrayField(TEXT("nextActions"), NextActions);
	return Result;
}
