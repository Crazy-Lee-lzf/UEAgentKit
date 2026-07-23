#pragma once

#include "CoreMinimal.h"
#include "Misc/OutputDevice.h"

struct FUEAgentKitCapturedLogEntry
{
	uint64 Sequence = 0;
	FDateTime TimestampUtc;
	FString Category;
	ELogVerbosity::Type Verbosity = ELogVerbosity::Log;
	FString Message;
	uint32 ThreadId = 0;
	int32 PieSessionId = 0;
	FString PieState = TEXT("editor");
	bool bFromBacklog = false;
	bool bMessageTruncated = false;
};

struct FUEAgentKitLogQuery
{
	FString Category;
	FString Keyword;
	ELogVerbosity::Type MinimumVerbosity = ELogVerbosity::Log;
	uint64 SinceSequence = 0;
	TOptional<FDateTime> SinceUtc;
	TOptional<FDateTime> UntilUtc;
	int32 PieSessionId = -1;
	int32 Limit = 100;
	bool bCompileOnly = false;
	FString AssetFilter;
};

struct FUEAgentKitLogQueryResult
{
	TArray<FUEAgentKitCapturedLogEntry> Entries;
	uint64 OldestSequence = 0;
	uint64 NewestSequence = 0;
	uint64 NextSequence = 0;
	uint64 DroppedCount = 0;
	int32 MatchedCount = 0;
	bool bTruncated = false;
	FDateTime CaptureStartedUtc;
};

class FUEAgentKitEditorBridgeLogCapture final : public FOutputDevice
{
public:
	FUEAgentKitEditorBridgeLogCapture();
	virtual ~FUEAgentKitEditorBridgeLogCapture() override;

	void Start();
	void Stop();
	FUEAgentKitLogQueryResult Query(const FUEAgentKitLogQuery& Query) const;
	int32 GetCurrentPieSessionId() const;
	FString GetCurrentPieState() const;

	virtual void Serialize(const TCHAR* Message, ELogVerbosity::Type Verbosity, const FName& Category) override;

private:
	void HandleBeginPie(bool bIsSimulating);
	void HandleEndPie(bool bIsSimulating);
	static bool MatchesCompileFilter(const FUEAgentKitCapturedLogEntry& Entry);
	static bool MatchesAssetFilter(const FUEAgentKitCapturedLogEntry& Entry, const FString& AssetFilter);

	mutable FCriticalSection CriticalSection;
	TArray<FUEAgentKitCapturedLogEntry> Entries;
	FDateTime CaptureStartedUtc;
	uint64 NextSequence = 1;
	uint64 DroppedCount = 0;
	int32 LastPieSessionId = 0;
	int32 CurrentPieSessionId = 0;
	FString CurrentPieState = TEXT("editor");
	bool bCapturingBacklog = false;
	bool bStarted = false;
};
