#include "EditorBridgeLogCapture.h"

#include "Editor.h"
#include "HAL/PlatformTLS.h"
#include "Logging/LogVerbosity.h"
#include "Misc/OutputDeviceRedirector.h"
#include "Misc/Paths.h"
#include "Misc/ScopeLock.h"

namespace UEAgentKitEditorBridgeLogCapturePrivate
{
	constexpr int32 MaxEntries = 4096;
	constexpr int32 TrimEntries = 512;
	constexpr int32 MaxMessageCharacters = 1024;

	ELogVerbosity::Type NormalizeVerbosity(const ELogVerbosity::Type Verbosity)
	{
		return static_cast<ELogVerbosity::Type>(Verbosity & ELogVerbosity::VerbosityMask);
	}
}

FUEAgentKitEditorBridgeLogCapture::FUEAgentKitEditorBridgeLogCapture() = default;

FUEAgentKitEditorBridgeLogCapture::~FUEAgentKitEditorBridgeLogCapture()
{
	Stop();
}

void FUEAgentKitEditorBridgeLogCapture::Start()
{
	if (bStarted)
	{
		return;
	}
	CaptureStartedUtc = FDateTime::UtcNow();
	FEditorDelegates::BeginPIE.AddRaw(this, &FUEAgentKitEditorBridgeLogCapture::HandleBeginPie);
	FEditorDelegates::EndPIE.AddRaw(this, &FUEAgentKitEditorBridgeLogCapture::HandleEndPie);
	if (GLog != nullptr)
	{
		GLog->AddOutputDevice(this);
		{
			FScopeLock Lock(&CriticalSection);
			bCapturingBacklog = true;
		}
		GLog->SerializeBacklog(this);
		{
			FScopeLock Lock(&CriticalSection);
			bCapturingBacklog = false;
		}
	}
	bStarted = true;
}

void FUEAgentKitEditorBridgeLogCapture::Stop()
{
	if (!bStarted)
	{
		return;
	}
	FEditorDelegates::BeginPIE.RemoveAll(this);
	FEditorDelegates::EndPIE.RemoveAll(this);
	if (GLog != nullptr)
	{
		GLog->RemoveOutputDevice(this);
	}
	bStarted = false;
}

FUEAgentKitLogQueryResult FUEAgentKitEditorBridgeLogCapture::Query(const FUEAgentKitLogQuery& Query) const
{
	FUEAgentKitLogQueryResult Result;
	FScopeLock Lock(&CriticalSection);
	Result.CaptureStartedUtc = CaptureStartedUtc;
	Result.DroppedCount = DroppedCount;
	if (!Entries.IsEmpty())
	{
		Result.OldestSequence = Entries[0].Sequence;
		Result.NewestSequence = Entries.Last().Sequence;
	}
	for (const FUEAgentKitCapturedLogEntry& Entry : Entries)
	{
		if (Entry.Sequence < Query.SinceSequence)
		{
			continue;
		}
		if (!Query.Category.IsEmpty() && !Entry.Category.Equals(Query.Category, ESearchCase::IgnoreCase))
		{
			continue;
		}
		if (Entry.Verbosity > Query.MinimumVerbosity)
		{
			continue;
		}
		if (!Query.Keyword.IsEmpty() && !Entry.Message.Contains(Query.Keyword, ESearchCase::IgnoreCase))
		{
			continue;
		}
		if (Query.SinceUtc.IsSet() && Entry.TimestampUtc < Query.SinceUtc.GetValue())
		{
			continue;
		}
		if (Query.UntilUtc.IsSet() && Entry.TimestampUtc > Query.UntilUtc.GetValue())
		{
			continue;
		}
		if (Query.PieSessionId >= 0 && Entry.PieSessionId != Query.PieSessionId)
		{
			continue;
		}
		if (Query.bCompileOnly && !MatchesCompileFilter(Entry))
		{
			continue;
		}
		if (!MatchesAssetFilter(Entry, Query.AssetFilter))
		{
			continue;
		}
		++Result.MatchedCount;
		if (Result.Entries.Num() < Query.Limit)
		{
			Result.Entries.Add(Entry);
		}
	}
	Result.bTruncated = Result.MatchedCount > Result.Entries.Num();
	Result.NextSequence = !Result.Entries.IsEmpty()
		? Result.Entries.Last().Sequence + 1
		: Query.SinceSequence;
	return Result;
}

int32 FUEAgentKitEditorBridgeLogCapture::GetCurrentPieSessionId() const
{
	FScopeLock Lock(&CriticalSection);
	return CurrentPieSessionId;
}

FString FUEAgentKitEditorBridgeLogCapture::GetCurrentPieState() const
{
	FScopeLock Lock(&CriticalSection);
	return CurrentPieState;
}

void FUEAgentKitEditorBridgeLogCapture::Serialize(
	const TCHAR* Message,
	const ELogVerbosity::Type Verbosity,
	const FName& Category)
{
	const ELogVerbosity::Type NormalizedVerbosity =
		UEAgentKitEditorBridgeLogCapturePrivate::NormalizeVerbosity(Verbosity);
	if (Message == nullptr || NormalizedVerbosity == ELogVerbosity::NoLogging)
	{
		return;
	}
	FUEAgentKitCapturedLogEntry Entry;
	Entry.TimestampUtc = FDateTime::UtcNow();
	Entry.Category = Category.ToString();
	Entry.Verbosity = NormalizedVerbosity;
	Entry.Message = Message;
	Entry.ThreadId = FPlatformTLS::GetCurrentThreadId();
	if (Entry.Message.Len() > UEAgentKitEditorBridgeLogCapturePrivate::MaxMessageCharacters)
	{
		Entry.Message.LeftInline(UEAgentKitEditorBridgeLogCapturePrivate::MaxMessageCharacters, EAllowShrinking::No);
		Entry.bMessageTruncated = true;
	}

	FScopeLock Lock(&CriticalSection);
	Entry.Sequence = NextSequence++;
	Entry.PieSessionId = CurrentPieSessionId;
	Entry.PieState = CurrentPieState;
	Entry.bFromBacklog = bCapturingBacklog;
	Entries.Add(MoveTemp(Entry));
	if (Entries.Num() > UEAgentKitEditorBridgeLogCapturePrivate::MaxEntries)
	{
		const int32 RemoveCount = FMath::Min(
			UEAgentKitEditorBridgeLogCapturePrivate::TrimEntries,
			Entries.Num());
		Entries.RemoveAt(0, RemoveCount, EAllowShrinking::No);
		DroppedCount += static_cast<uint64>(RemoveCount);
	}
}

void FUEAgentKitEditorBridgeLogCapture::HandleBeginPie(const bool bIsSimulating)
{
	FScopeLock Lock(&CriticalSection);
	CurrentPieSessionId = ++LastPieSessionId;
	CurrentPieState = bIsSimulating ? TEXT("simulating") : TEXT("playing");
}

void FUEAgentKitEditorBridgeLogCapture::HandleEndPie(const bool bIsSimulating)
{
	(void)bIsSimulating;
	FScopeLock Lock(&CriticalSection);
	CurrentPieSessionId = 0;
	CurrentPieState = TEXT("editor");
}

bool FUEAgentKitEditorBridgeLogCapture::MatchesCompileFilter(const FUEAgentKitCapturedLogEntry& Entry)
{
	if (Entry.Verbosity > ELogVerbosity::Warning)
	{
		return false;
	}
	const FString Category = Entry.Category.ToLower();
	if (
		Category.Contains(TEXT("compiler")) ||
		Category.Contains(TEXT("compile")) ||
		Category.Contains(TEXT("k2")) ||
		Category.Contains(TEXT("blueprint")) ||
		Category.Contains(TEXT("script")))
	{
		return true;
	}
	const FString Message = Entry.Message.ToLower();
	return
		Message.Contains(TEXT("compile of")) ||
		Message.Contains(TEXT("compiler error")) ||
		Message.Contains(TEXT("compiler warning")) ||
		Message.Contains(TEXT("[compiler"));
}

bool FUEAgentKitEditorBridgeLogCapture::MatchesAssetFilter(
	const FUEAgentKitCapturedLogEntry& Entry,
	const FString& AssetFilter)
{
	if (AssetFilter.IsEmpty())
	{
		return true;
	}
	FString PackageName = AssetFilter;
	FString AssetName = FPaths::GetBaseFilename(AssetFilter);
	int32 DotIndex = INDEX_NONE;
	if (PackageName.FindLastChar(TEXT('.'), DotIndex))
	{
		AssetName = PackageName.Mid(DotIndex + 1);
		PackageName.LeftInline(DotIndex, EAllowShrinking::No);
	}
	return
		Entry.Message.Contains(AssetFilter, ESearchCase::IgnoreCase) ||
		Entry.Message.Contains(PackageName, ESearchCase::IgnoreCase) ||
		(!AssetName.IsEmpty() && Entry.Message.Contains(AssetName, ESearchCase::IgnoreCase));
}
