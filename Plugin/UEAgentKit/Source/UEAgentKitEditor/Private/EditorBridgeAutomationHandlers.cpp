#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "HAL/PlatformFileManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/AutomationTest.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

using namespace UEAgentKitEditorBridgePrivate;

#if WITH_DEV_AUTOMATION_TESTS

DEFINE_LATENT_AUTOMATION_COMMAND(FUEAgentKitBridgeSmokeLatentCommand);

bool FUEAgentKitBridgeSmokeLatentCommand::Update()
{
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitBridgeSmokeAutomationTest,
	"UEAgentKit.EditorBridge.LiveActionSmoke",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitBridgeSmokeAutomationTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	AddInfo(TEXT("UE Agent Kit bounded Live Editor Automation action smoke test."));
	ADD_LATENT_AUTOMATION_COMMAND(FUEAgentKitBridgeSmokeLatentCommand());
	return true;
}

#endif

namespace
{
	bool IsSafeAutomationTestName(const FString& TestName)
	{
		if (TestName.IsEmpty() || TestName.Len() > 512 || TestName.TrimStartAndEnd() != TestName)
		{
			return false;
		}
		for (const TCHAR Character : TestName)
		{
			if (!(FChar::IsAlnum(Character) || Character == TEXT('.') || Character == TEXT('_') || Character == TEXT('-')))
			{
				return false;
			}
		}
		return true;
	}

	FString ResolveEditorCommandExecutable()
	{
		const FString CurrentExecutable = FPlatformProcess::ExecutablePath();
		return FPaths::Combine(FPaths::GetPath(CurrentExecutable), TEXT("UnrealEditor-Cmd.exe"));
	}

	FString AutomationStateName(const FString& ReportState)
	{
		if (ReportState.Equals(TEXT("Success"), ESearchCase::IgnoreCase))
		{
			return TEXT("success");
		}
		if (ReportState.Equals(TEXT("NotRun"), ESearchCase::IgnoreCase))
		{
			return TEXT("not-run");
		}
		if (ReportState.Equals(TEXT("InProcess"), ESearchCase::IgnoreCase))
		{
			return TEXT("in-process");
		}
		return TEXT("failed");
	}

	FString AutomationEventTypeName(const FString& ReportType)
	{
		if (ReportType.Equals(TEXT("Error"), ESearchCase::IgnoreCase))
		{
			return TEXT("error");
		}
		if (ReportType.Equals(TEXT("Warning"), ESearchCase::IgnoreCase))
		{
			return TEXT("warning");
		}
		return TEXT("info");
	}

}

bool FUEAgentKitEditorBridge::TryStartAutomationTest(
	const FString& TestName,
	const int32 TimeoutSeconds,
	const int32 MaxEntries,
	FSocket* Socket,
	const FString& RequestId,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (GEditor == nullptr)
	{
		OutErrorCode = TEXT("live-editor-unavailable");
		OutErrorMessage = TEXT("The Unreal Editor is unavailable.");
		return false;
	}
	if (GEditor->PlayWorld != nullptr)
	{
		OutErrorCode = TEXT("live-editor-pie-active");
		OutErrorMessage = TEXT("Automation actions are unavailable while PIE or SIE is active.");
		return false;
	}
	if (!IsSafeAutomationTestName(TestName) || TimeoutSeconds < 1 || TimeoutSeconds > 300 || MaxEntries < 1 || MaxEntries > 200)
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("Automation parameters are outside the bounded contract.");
		return false;
	}
	if (PendingAutomation.bActive)
	{
		OutErrorCode = TEXT("live-editor-automation-busy");
		OutErrorMessage = TEXT("Another isolated Unreal Automation Test is already active.");
		return false;
	}

	const FString EditorCommand = ResolveEditorCommandExecutable();
	const FString ProjectPath = FPaths::ConvertRelativePathToFull(FPaths::GetProjectFilePath());
	if (!FPaths::FileExists(EditorCommand) || ProjectPath.IsEmpty() || !FPaths::FileExists(ProjectPath))
	{
		OutErrorCode = TEXT("live-editor-automation-unavailable");
		OutErrorMessage = TEXT("The fixed UnrealEditor-Cmd executable or current project file is unavailable.");
		return false;
	}

	const FString RunId = FGuid::NewGuid().ToString(EGuidFormats::Digits);
	const FString ReportDirectory = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("Automation"), RunId);
	IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
	if (!PlatformFile.CreateDirectoryTree(*ReportDirectory))
	{
		OutErrorCode = TEXT("live-editor-automation-report-directory-failed");
		OutErrorMessage = TEXT("The isolated Automation report directory could not be created.");
		return false;
	}

	const FString ExecCommands = FString::Printf(TEXT("Automation RunTests %s;Quit"), *TestName);
	const FString Arguments = FString::Printf(
		TEXT("\"%s\" -UEAgentKitAutomationChild -unattended -nop4 -nosplash -NoSound -NullRHI -stdout -FullStdOutLogOutput -ReportExportPath=\"%s\" -ExecCmds=\"%s\" -TestExit=\"Automation Test Queue Empty\""),
		*ProjectPath,
		*ReportDirectory,
		*ExecCommands);

	uint32 ProcessId = 0;
	FProcHandle ProcessHandle = FPlatformProcess::CreateProc(
		*EditorCommand,
		*Arguments,
		true,
		true,
		true,
		&ProcessId,
		0,
		nullptr,
		nullptr,
		nullptr);
	if (!ProcessHandle.IsValid())
	{
		PlatformFile.DeleteDirectoryRecursively(*ReportDirectory);
		OutErrorCode = TEXT("live-editor-automation-start-failed");
		OutErrorMessage = TEXT("The isolated UnrealEditor-Cmd process could not be started.");
		return false;
	}

	PendingAutomation.Socket = Socket;
	PendingAutomation.RequestId = RequestId;
	PendingAutomation.TestName = TestName;
	PendingAutomation.StartedAtUtc = FDateTime::UtcNow().ToIso8601();
	PendingAutomation.ReportDirectory = ReportDirectory;
	PendingAutomation.ReportPath = FPaths::Combine(ReportDirectory, TEXT("index.json"));
	PendingAutomation.ProcessHandle = ProcessHandle;
	PendingAutomation.ProcessId = ProcessId;
	PendingAutomation.DeadlineSeconds = FPlatformTime::Seconds() + static_cast<double>(TimeoutSeconds);
	PendingAutomation.MaxEntries = MaxEntries;
	PendingAutomation.bActive = true;
	return true;
}

void FUEAgentKitEditorBridge::TickAutomationTest()
{
	if (!PendingAutomation.bActive)
	{
		return;
	}

	if (FPlatformTime::Seconds() >= PendingAutomation.DeadlineSeconds)
	{
		if (PendingAutomation.ProcessHandle.IsValid() && FPlatformProcess::IsProcRunning(PendingAutomation.ProcessHandle))
		{
			FPlatformProcess::TerminateProc(PendingAutomation.ProcessHandle, true);
			FPlatformProcess::WaitForProc(PendingAutomation.ProcessHandle);
		}
		CompleteAutomationTest(true);
		return;
	}

	if (PendingAutomation.ProcessHandle.IsValid() && FPlatformProcess::IsProcRunning(PendingAutomation.ProcessHandle))
	{
		return;
	}

	int32 ReturnCode = INDEX_NONE;
	if (PendingAutomation.ProcessHandle.IsValid())
	{
		FPlatformProcess::GetProcReturnCode(PendingAutomation.ProcessHandle, &ReturnCode);
	}
	PendingAutomation.ExitCode = ReturnCode;
	CompleteAutomationTest(false);
}

void FUEAgentKitEditorBridge::CompleteAutomationTest(const bool bTimedOut)
{
	if (!PendingAutomation.bActive)
	{
		return;
	}

	FPendingAutomationRun Completed = MoveTemp(PendingAutomation);
	PendingAutomation = FPendingAutomationRun();
	if (Completed.ProcessHandle.IsValid())
	{
		FPlatformProcess::CloseProc(Completed.ProcessHandle);
	}

	auto FinishClient = [this, &Completed]()
	{
		for (FClientConnection& Client : Clients)
		{
			if (Client.Socket == Completed.Socket)
			{
				Client.bDeferredResponse = false;
				Client.bCloseAfterResponse = true;
				break;
			}
		}
	};

	IPlatformFile& PlatformFile = FPlatformFileManager::Get().GetPlatformFile();
	const FString CompletedAtUtc = FDateTime::UtcNow().ToIso8601();
	auto AddAutomationEvidence = [this, &Completed, &CompletedAtUtc](const TSharedRef<FJsonObject>& Result)
	{
		TSharedRef<FJsonObject> Evidence = BuildValidationEvidence(
			TEXT("automation"),
			Completed.StartedAtUtc,
			CompletedAtUtc,
			TEXT("not-applicable"));
		Evidence->SetStringField(
			TEXT("revisionRationale"),
			TEXT("The exact Automation Test did not declare asset inputs."));
		Evidence->SetStringField(TEXT("executionIsolation"), TEXT("isolated-unreal-editor-cmd"));
		Evidence->SetNumberField(TEXT("executionProcessId"), Completed.ProcessId);
		Result->SetStringField(TEXT("startedAtUtc"), Completed.StartedAtUtc);
		Result->SetStringField(TEXT("completedAtUtc"), CompletedAtUtc);
		Result->SetObjectField(TEXT("validationEvidence"), Evidence);
	};
	if (bTimedOut)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("action"), TEXT("run-automation-test"));
		Result->SetStringField(TEXT("editorSessionId"), SessionId);
		Result->SetStringField(TEXT("pieState"), GetPieStateName());
		Result->SetStringField(TEXT("testName"), Completed.TestName);
		Result->SetStringField(TEXT("state"), TEXT("timed-out"));
		Result->SetBoolField(TEXT("successful"), false);
		Result->SetBoolField(TEXT("timedOut"), true);
		Result->SetBoolField(TEXT("isolatedProcess"), true);
		Result->SetNumberField(TEXT("processId"), Completed.ProcessId);
		Result->SetNumberField(TEXT("entryCount"), 0);
		Result->SetNumberField(TEXT("returnedEntryCount"), 0);
		Result->SetBoolField(TEXT("entriesTruncated"), false);
		Result->SetArrayField(TEXT("entries"), {});
		Result->SetBoolField(TEXT("saved"), false);
		AddAutomationEvidence(Result);
		SendResult(Completed.Socket, Completed.RequestId, Result);
		FinishClient();
		PlatformFile.DeleteDirectoryRecursively(*Completed.ReportDirectory);
		return;
	}

	FString ReportText;
	if (!FFileHelper::LoadFileToString(ReportText, *Completed.ReportPath))
	{
		SendError(
			Completed.Socket,
			Completed.RequestId,
			Completed.ExitCode == 0 ? TEXT("live-editor-automation-report-missing") : TEXT("live-editor-automation-process-failed"),
			Completed.ExitCode == 0 ? TEXT("The isolated Automation process exited without its fixed report.") : TEXT("The isolated Automation process failed before producing a valid report."));
		FinishClient();
		PlatformFile.DeleteDirectoryRecursively(*Completed.ReportDirectory);
		return;
	}

	TSharedPtr<FJsonObject> Report;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ReportText);
	if (!FJsonSerializer::Deserialize(Reader, Report) || !Report.IsValid())
	{
		SendError(Completed.Socket, Completed.RequestId, TEXT("live-editor-automation-report-invalid"), TEXT("The isolated Automation report is not valid JSON."));
		FinishClient();
		PlatformFile.DeleteDirectoryRecursively(*Completed.ReportDirectory);
		return;
	}

	const TArray<TSharedPtr<FJsonValue>>* Tests = nullptr;
	if (!Report->TryGetArrayField(TEXT("tests"), Tests) || Tests == nullptr || Tests->Num() != 1)
	{
		SendError(Completed.Socket, Completed.RequestId, TEXT("live-editor-automation-report-invalid"), TEXT("The isolated Automation report did not contain exactly one test result."));
		FinishClient();
		PlatformFile.DeleteDirectoryRecursively(*Completed.ReportDirectory);
		return;
	}

	const TSharedPtr<FJsonObject> Test = (*Tests)[0].IsValid() ? (*Tests)[0]->AsObject() : nullptr;
	FString FullTestPath;
	FString DisplayName;
	FString ReportState;
	if (!Test.IsValid() ||
		!Test->TryGetStringField(TEXT("fullTestPath"), FullTestPath) ||
		FullTestPath != Completed.TestName ||
		!Test->TryGetStringField(TEXT("testDisplayName"), DisplayName) ||
		!Test->TryGetStringField(TEXT("state"), ReportState))
	{
		SendError(Completed.Socket, Completed.RequestId, TEXT("live-editor-automation-report-invalid"), TEXT("The isolated Automation report did not exactly match the requested test."));
		FinishClient();
		PlatformFile.DeleteDirectoryRecursively(*Completed.ReportDirectory);
		return;
	}

	double Duration = 0.0;
	double WarningCount = 0.0;
	double ErrorCount = 0.0;
	Test->TryGetNumberField(TEXT("duration"), Duration);
	Test->TryGetNumberField(TEXT("warnings"), WarningCount);
	Test->TryGetNumberField(TEXT("errors"), ErrorCount);
	const FString State = AutomationStateName(ReportState);
	const bool bSuccessful = Completed.ExitCode == 0 && State == TEXT("success");

	const TArray<TSharedPtr<FJsonValue>>* ReportEntries = nullptr;
	Test->TryGetArrayField(TEXT("entries"), ReportEntries);
	const int32 EntryCount = ReportEntries == nullptr ? 0 : ReportEntries->Num();
	const int32 ReturnedCount = FMath::Min(EntryCount, Completed.MaxEntries);
	TArray<TSharedPtr<FJsonValue>> Entries;
	for (int32 Index = 0; Index < ReturnedCount; ++Index)
	{
		const TSharedPtr<FJsonObject> ReportEntry = (*ReportEntries)[Index].IsValid() ? (*ReportEntries)[Index]->AsObject() : nullptr;
		if (!ReportEntry.IsValid())
		{
			continue;
		}
		const TSharedPtr<FJsonObject>* Event = nullptr;
		if (!ReportEntry->TryGetObjectField(TEXT("event"), Event) || Event == nullptr || !Event->IsValid())
		{
			continue;
		}
		FString Type;
		FString Message;
		FString Context;
		FString Filename;
		FString Timestamp;
		double LineNumber = -1.0;
		(*Event)->TryGetStringField(TEXT("type"), Type);
		(*Event)->TryGetStringField(TEXT("message"), Message);
		(*Event)->TryGetStringField(TEXT("context"), Context);
		ReportEntry->TryGetStringField(TEXT("filename"), Filename);
		ReportEntry->TryGetStringField(TEXT("timestamp"), Timestamp);
		ReportEntry->TryGetNumberField(TEXT("lineNumber"), LineNumber);

		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("type"), AutomationEventTypeName(Type));
		Item->SetStringField(TEXT("message"), Message.Left(2048));
		Item->SetStringField(TEXT("context"), Context.Left(256));
		Item->SetStringField(TEXT("sourceFileName"), FPaths::GetCleanFilename(Filename));
		Item->SetNumberField(TEXT("sourceLine"), LineNumber);
		Item->SetStringField(TEXT("timestampUtc"), Timestamp);
		Entries.Add(MakeShared<FJsonValueObject>(Item));
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("run-automation-test"));
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetStringField(TEXT("pieState"), GetPieStateName());
	Result->SetStringField(TEXT("testName"), Completed.TestName);
	Result->SetStringField(TEXT("fullTestPath"), FullTestPath);
	Result->SetStringField(TEXT("displayName"), DisplayName);
	Result->SetStringField(TEXT("state"), State);
	Result->SetBoolField(TEXT("successful"), bSuccessful);
	Result->SetBoolField(TEXT("timedOut"), false);
	Result->SetBoolField(TEXT("isolatedProcess"), true);
	Result->SetNumberField(TEXT("processId"), Completed.ProcessId);
	Result->SetNumberField(TEXT("exitCode"), Completed.ExitCode);
	Result->SetNumberField(TEXT("durationSeconds"), Duration);
	Result->SetNumberField(TEXT("warningCount"), WarningCount);
	Result->SetNumberField(TEXT("errorCount"), ErrorCount);
	Result->SetNumberField(TEXT("entryCount"), EntryCount);
	Result->SetNumberField(TEXT("returnedEntryCount"), Entries.Num());
	Result->SetBoolField(TEXT("entriesTruncated"), EntryCount > ReturnedCount);
	Result->SetArrayField(TEXT("entries"), Entries);
	Result->SetBoolField(TEXT("saved"), false);
	AddAutomationEvidence(Result);

	SendResult(Completed.Socket, Completed.RequestId, Result);
	FinishClient();
	PlatformFile.DeleteDirectoryRecursively(*Completed.ReportDirectory);
}

void FUEAgentKitEditorBridge::CancelAutomationTest()
{
	if (!PendingAutomation.bActive)
	{
		return;
	}
	if (PendingAutomation.ProcessHandle.IsValid())
	{
		if (FPlatformProcess::IsProcRunning(PendingAutomation.ProcessHandle))
		{
			FPlatformProcess::TerminateProc(PendingAutomation.ProcessHandle, true);
			FPlatformProcess::WaitForProc(PendingAutomation.ProcessHandle);
		}
		FPlatformProcess::CloseProc(PendingAutomation.ProcessHandle);
	}
	FPlatformFileManager::Get().GetPlatformFile().DeleteDirectoryRecursively(*PendingAutomation.ReportDirectory);
	PendingAutomation = FPendingAutomationRun();
}
