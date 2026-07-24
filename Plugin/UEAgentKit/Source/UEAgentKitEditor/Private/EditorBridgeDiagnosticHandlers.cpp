#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "EditorBridgeLogCapture.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/Blueprint.h"
#include "UObject/UObjectIterator.h"

using namespace UEAgentKitEditorBridgePrivate;

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildOutputLogResult(const TSharedPtr<FJsonObject>& Params) const
{
	const FUEAgentKitLogQuery Query = UEAgentKitEditorBridgePrivate::BuildLogQuery(Params, false);
	const FUEAgentKitLogQueryResult QueryResult = LogCapture.IsValid()
		? LogCapture->Query(Query)
		: FUEAgentKitLogQueryResult();
	TArray<TSharedPtr<FJsonValue>> Items;
	for (const FUEAgentKitCapturedLogEntry& Entry : QueryResult.Entries)
	{
		Items.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeCapturedLogEntry(Entry)));
	}
	TSharedRef<FJsonObject> Filters = MakeShared<FJsonObject>();
	Filters->SetStringField(TEXT("category"), Query.Category);
	Filters->SetStringField(TEXT("minimumVerbosity"), ToString(Query.MinimumVerbosity));
	Filters->SetStringField(TEXT("keyword"), Query.Keyword);
	Filters->SetNumberField(TEXT("sinceSequence"), static_cast<double>(Query.SinceSequence));
	Filters->SetNumberField(TEXT("pieSessionId"), Query.PieSessionId);
	Filters->SetStringField(TEXT("sinceUtc"), Query.SinceUtc.IsSet() ? Query.SinceUtc.GetValue().ToIso8601() : FString());
	Filters->SetStringField(TEXT("untilUtc"), Query.UntilUtc.IsSet() ? Query.UntilUtc.GetValue().ToIso8601() : FString());
	Filters->SetNumberField(TEXT("limit"), Query.Limit);

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetBoolField(TEXT("available"), LogCapture.IsValid());
	Result->SetStringField(TEXT("captureStartedUtc"), QueryResult.CaptureStartedUtc.ToIso8601());
	Result->SetNumberField(TEXT("oldestSequence"), static_cast<double>(QueryResult.OldestSequence));
	Result->SetNumberField(TEXT("newestSequence"), static_cast<double>(QueryResult.NewestSequence));
	Result->SetNumberField(TEXT("nextSequence"), static_cast<double>(QueryResult.NextSequence));
	Result->SetNumberField(TEXT("droppedCount"), static_cast<double>(QueryResult.DroppedCount));
	Result->SetNumberField(TEXT("matchedCount"), QueryResult.MatchedCount);
	Result->SetNumberField(TEXT("resultCount"), Items.Num());
	Result->SetBoolField(TEXT("truncated"), QueryResult.bTruncated);
	Result->SetObjectField(TEXT("filters"), Filters);
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildCompileErrorsResult(const TSharedPtr<FJsonObject>& Params) const
{
	const FUEAgentKitLogQuery Query = UEAgentKitEditorBridgePrivate::BuildLogQuery(Params, true);
	const FUEAgentKitLogQueryResult QueryResult = LogCapture.IsValid()
		? LogCapture->Query(Query)
		: FUEAgentKitLogQueryResult();
	TArray<TSharedPtr<FJsonValue>> Diagnostics;
	for (const FUEAgentKitCapturedLogEntry& Entry : QueryResult.Entries)
	{
		Diagnostics.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeCapturedLogEntry(Entry)));
	}

	TArray<UBlueprint*> Blueprints;
	for (TObjectIterator<UBlueprint> It; It; ++It)
	{
		UBlueprint* Blueprint = *It;
		if (Blueprint == nullptr || !Blueprint->GetPathName().StartsWith(TEXT("/Game/")))
		{
			continue;
		}
		if (!Query.AssetFilter.IsEmpty() && !Blueprint->GetPathName().Equals(Query.AssetFilter, ESearchCase::IgnoreCase))
		{
			continue;
		}
		if (
			Query.AssetFilter.IsEmpty() &&
			Blueprint->Status != BS_Error &&
			Blueprint->Status != BS_UpToDateWithWarnings)
		{
			continue;
		}
		Blueprints.Add(Blueprint);
	}
	Blueprints.Sort([](const UBlueprint& Left, const UBlueprint& Right)
	{
		return Left.GetPathName() < Right.GetPathName();
	});
	const int32 MatchedBlueprintCount = Blueprints.Num();
	constexpr int32 MaxBlueprintStates = 100;
	TArray<TSharedPtr<FJsonValue>> BlueprintStates;
	for (int32 Index = 0; Index < FMath::Min(MatchedBlueprintCount, MaxBlueprintStates); ++Index)
	{
		BlueprintStates.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeBlueprintState(Blueprints[Index])));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("diagnosticSource"), TEXT("captured-output-log"));
	Result->SetBoolField(TEXT("historyComplete"), false);
	Result->SetStringField(TEXT("historyScope"), TEXT("Bridge backlog plus messages received during the current Editor Bridge session."));
	Result->SetStringField(TEXT("assetPath"), Query.AssetFilter);
	Result->SetNumberField(TEXT("diagnosticCount"), Diagnostics.Num());
	Result->SetNumberField(TEXT("matchedDiagnosticCount"), QueryResult.MatchedCount);
	Result->SetBoolField(TEXT("diagnosticsTruncated"), QueryResult.bTruncated);
	Result->SetNumberField(TEXT("nextSequence"), static_cast<double>(QueryResult.NextSequence));
	Result->SetNumberField(TEXT("matchedLoadedBlueprintCount"), MatchedBlueprintCount);
	Result->SetNumberField(TEXT("loadedBlueprintCount"), BlueprintStates.Num());
	Result->SetBoolField(TEXT("loadedBlueprintsTruncated"), MatchedBlueprintCount > BlueprintStates.Num());
	Result->SetArrayField(TEXT("diagnostics"), Diagnostics);
	Result->SetArrayField(TEXT("loadedBlueprints"), BlueprintStates);
	return Result;
}
