#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "EditorBridgeLogCapture.h"
#include "BlueprintContextSha256.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "EditorValidatorSubsystem.h"
#include "Engine/Blueprint.h"
#include "HAL/PlatformTime.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/DateTime.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

namespace
{
	bool RequireStoppedEditor(FString& OutErrorCode, FString& OutErrorMessage)
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
			OutErrorMessage = TEXT("Compile and validation actions are unavailable while PIE or SIE is active.");
			return false;
		}
		return true;
	}

	bool ResolveAssetData(
		const FString& AssetPath,
		FAssetData& OutAssetData,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!IsSafeGameAssetPath(AssetPath))
		{
			OutErrorCode = TEXT("live-editor-invalid-parameters");
			OutErrorMessage = TEXT("assetPath must be an exact /Game Object Path.");
			return false;
		}
		FAssetRegistryModule& AssetRegistryModule =
			FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
		OutAssetData = AssetRegistryModule.Get().GetAssetByObjectPath(FSoftObjectPath(AssetPath));
		if (!OutAssetData.IsValid())
		{
			OutErrorCode = TEXT("live-editor-asset-not-found");
			OutErrorMessage = TEXT("The exact asset was not found in the current Asset Registry.");
			return false;
		}
		return true;
	}

	struct FValidationRevisionSnapshot
	{
		FString AssetPath;
		FString PackageName;
		FString BeforeRevision;
		bool bPackageDirtyBefore = false;
	};

	FString HashAssetPackage(const FAssetData& AssetData)
	{
		FString PackageFilename;
		FString Digest;
		if (FPackageName::DoesPackageExist(AssetData.PackageName.ToString(), &PackageFilename)
			&& FBlueprintContextSha256::HashFile(PackageFilename, Digest))
		{
			return TEXT("sha256:") + Digest;
		}
		return FString();
	}

	TArray<FValidationRevisionSnapshot> CaptureValidationRevisions(const TArray<FAssetData>& Assets)
	{
		TArray<FValidationRevisionSnapshot> Snapshots;
		Snapshots.Reserve(Assets.Num());
		for (const FAssetData& AssetData : Assets)
		{
			FValidationRevisionSnapshot Snapshot;
			Snapshot.AssetPath = AssetData.GetObjectPathString();
			Snapshot.PackageName = AssetData.PackageName.ToString();
			Snapshot.BeforeRevision = HashAssetPackage(AssetData);
			const UPackage* LoadedPackage = FindPackage(nullptr, *Snapshot.PackageName);
			Snapshot.bPackageDirtyBefore = LoadedPackage != nullptr && LoadedPackage->IsDirty();
			Snapshots.Add(MoveTemp(Snapshot));
		}
		Snapshots.Sort([](const FValidationRevisionSnapshot& Left, const FValidationRevisionSnapshot& Right)
		{
			return Left.AssetPath < Right.AssetPath;
		});
		return Snapshots;
	}

	void CompleteValidationRevisionEvidence(
		const TSharedRef<FJsonObject>& Evidence,
		const TArray<FAssetData>& Assets,
		const TArray<FValidationRevisionSnapshot>& BeforeSnapshots)
	{
		TMap<FString, FAssetData> AssetsByPath;
		for (const FAssetData& AssetData : Assets)
		{
			AssetsByPath.Add(AssetData.GetObjectPathString(), AssetData);
		}

		TArray<TSharedPtr<FJsonValue>> RevisionSet;
		int32 MissingRevisionCount = 0;
		int32 DirtyAssetCount = 0;
		int32 ChangedDuringActionCount = 0;
		for (const FValidationRevisionSnapshot& Before : BeforeSnapshots)
		{
			const FAssetData* AssetData = AssetsByPath.Find(Before.AssetPath);
			const FString AfterRevision = AssetData != nullptr ? HashAssetPackage(*AssetData) : FString();
			const UPackage* LoadedPackage = FindPackage(nullptr, *Before.PackageName);
			const bool bPackageDirtyAfter = LoadedPackage != nullptr && LoadedPackage->IsDirty();
			const bool bRevisionAvailable = !Before.BeforeRevision.IsEmpty() && !AfterRevision.IsEmpty();
			const bool bRevisionStable = bRevisionAvailable && Before.BeforeRevision == AfterRevision;
			if (!bRevisionAvailable)
			{
				++MissingRevisionCount;
			}
			if (Before.bPackageDirtyBefore || bPackageDirtyAfter)
			{
				++DirtyAssetCount;
			}
			if (bRevisionAvailable && !bRevisionStable)
			{
				++ChangedDuringActionCount;
			}

			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("assetPath"), Before.AssetPath);
			Item->SetStringField(TEXT("packageName"), Before.PackageName);
			Item->SetStringField(TEXT("source"), TEXT("disk-package"));
			Item->SetStringField(TEXT("revision"), Before.BeforeRevision);
			Item->SetStringField(TEXT("revisionAfter"), AfterRevision);
			Item->SetBoolField(TEXT("revisionAvailable"), bRevisionAvailable);
			Item->SetBoolField(TEXT("revisionStable"), bRevisionStable);
			Item->SetBoolField(TEXT("packageDirtyBefore"), Before.bPackageDirtyBefore);
			Item->SetBoolField(TEXT("packageDirtyAfter"), bPackageDirtyAfter);
			RevisionSet.Add(MakeShared<FJsonValueObject>(Item));
		}

		const bool bComplete = MissingRevisionCount == 0
			&& DirtyAssetCount == 0
			&& ChangedDuringActionCount == 0
			&& RevisionSet.Num() == Assets.Num();
		Evidence->SetStringField(TEXT("revisionCoverage"), bComplete ? TEXT("complete") : TEXT("partial"));
		Evidence->SetArrayField(TEXT("revisionSet"), RevisionSet);
		Evidence->SetNumberField(TEXT("revisionAssetCount"), RevisionSet.Num());
		Evidence->SetNumberField(TEXT("missingRevisionCount"), MissingRevisionCount);
		Evidence->SetNumberField(TEXT("dirtyAssetCount"), DirtyAssetCount);
		Evidence->SetNumberField(TEXT("changedDuringActionCount"), ChangedDuringActionCount);
		Evidence->SetBoolField(TEXT("revisionStable"), ChangedDuringActionCount == 0);
	}

	FString ValidationResultName(const EDataValidationResult Result)
	{
		switch (Result)
		{
		case EDataValidationResult::Valid:
			return TEXT("valid");
		case EDataValidationResult::Invalid:
			return TEXT("invalid");
		case EDataValidationResult::NotValidated:
		default:
			return TEXT("not-validated");
		}
	}

	TSharedRef<FJsonObject> DescribeValidationDetails(
		const FString& ObjectPath,
		const FValidateAssetsDetails& Details,
		int32& InOutRemainingIssues,
		bool& bOutIssuesTruncated)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("assetPath"), ObjectPath);
		Item->SetStringField(TEXT("packageName"), Details.PackageName.ToString());
		Item->SetStringField(TEXT("assetName"), Details.AssetName.ToString());
		Item->SetStringField(TEXT("result"), ValidationResultName(Details.Result));
		Item->SetNumberField(TEXT("errorCount"), Details.ValidationErrors.Num());
		Item->SetNumberField(TEXT("warningCount"), Details.ValidationWarnings.Num());

		TArray<TSharedPtr<FJsonValue>> Errors;
		for (const FText& Error : Details.ValidationErrors)
		{
			if (InOutRemainingIssues <= 0)
			{
				bOutIssuesTruncated = true;
				break;
			}
			Errors.Add(MakeShared<FJsonValueString>(Error.ToString().Left(1024)));
			--InOutRemainingIssues;
		}
		TArray<TSharedPtr<FJsonValue>> Warnings;
		for (const FText& Warning : Details.ValidationWarnings)
		{
			if (InOutRemainingIssues <= 0)
			{
				bOutIssuesTruncated = true;
				break;
			}
			Warnings.Add(MakeShared<FJsonValueString>(Warning.ToString().Left(1024)));
			--InOutRemainingIssues;
		}
		Item->SetArrayField(TEXT("errors"), Errors);
		Item->SetArrayField(TEXT("warnings"), Warnings);
		return Item;
	}

	TSharedRef<FJsonObject> DescribeValidationResults(
		const FString& Scope,
		const FString& SessionId,
		const FValidateAssetsResults& Results,
		const int32 FailureCount,
		const int32 MaxIssues,
		const double DurationMs,
		const int32 DirtyBefore,
		const int32 DirtyAfter)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("action"), TEXT("validate-assets"));
		Result->SetStringField(TEXT("scope"), Scope);
		Result->SetStringField(TEXT("editorSessionId"), SessionId);
		Result->SetStringField(TEXT("pieState"), GetPieStateName());
		Result->SetNumberField(TEXT("durationMs"), DurationMs);
		Result->SetNumberField(TEXT("failureOrWarningCount"), FailureCount);
		Result->SetNumberField(TEXT("numRequested"), Results.NumRequested);
		Result->SetNumberField(TEXT("numChecked"), Results.NumChecked);
		Result->SetNumberField(TEXT("numValid"), Results.NumValid);
		Result->SetNumberField(TEXT("numInvalid"), Results.NumInvalid);
		Result->SetNumberField(TEXT("numWarnings"), Results.NumWarnings);
		Result->SetNumberField(TEXT("numSkipped"), Results.NumSkipped);
		Result->SetNumberField(TEXT("numUnableToValidate"), Results.NumUnableToValidate);
		Result->SetBoolField(TEXT("assetLimitReached"), Results.bAssetLimitReached);
		Result->SetBoolField(TEXT("saved"), false);
		Result->SetNumberField(TEXT("dirtyPackageCountBefore"), DirtyBefore);
		Result->SetNumberField(TEXT("dirtyPackageCountAfter"), DirtyAfter);
		Result->SetBoolField(TEXT("dirtyPackageCountChanged"), DirtyBefore != DirtyAfter);
		const FString Overall = Results.NumInvalid > 0
			? TEXT("invalid")
			: (Results.NumWarnings > 0 ? TEXT("valid-with-warnings")
				: (Results.NumChecked > 0 ? TEXT("valid") : TEXT("not-validated")));
		Result->SetStringField(TEXT("result"), Overall);

		TArray<FString> ObjectPaths;
		Results.AssetsDetails.GetKeys(ObjectPaths);
		ObjectPaths.Sort();
		TArray<TSharedPtr<FJsonValue>> Assets;
		int32 RemainingIssues = MaxIssues;
		bool bIssuesTruncated = false;
		for (const FString& ObjectPath : ObjectPaths)
		{
			if (const FValidateAssetsDetails* Details = Results.AssetsDetails.Find(ObjectPath))
			{
				Assets.Add(MakeShared<FJsonValueObject>(
					DescribeValidationDetails(ObjectPath, *Details, RemainingIssues, bIssuesTruncated)));
			}
		}
		Result->SetArrayField(TEXT("assets"), Assets);
		Result->SetNumberField(TEXT("returnedAssetCount"), Assets.Num());
		Result->SetNumberField(TEXT("returnedIssueCount"), MaxIssues - RemainingIssues);
		Result->SetBoolField(TEXT("issuesTruncated"), bIssuesTruncated);
		return Result;
	}

	FValidateAssetsSettings MakeValidationSettings(const int32 MaxAssets)
	{
		FValidateAssetsSettings Settings;
		Settings.bSkipExcludedDirectories = true;
		Settings.bShowIfNoFailures = false;
		Settings.bCollectPerAssetDetails = true;
		Settings.ValidationUsecase = EDataValidationUsecase::Manual;
		Settings.bLoadAssetsForValidation = true;
		Settings.bUnloadAssetsLoadedForValidation = true;
		Settings.bLoadExternalObjectsForValidation = false;
		Settings.bCaptureAssetLoadLogs = true;
		Settings.bCaptureLogsDuringValidation = true;
		Settings.bCaptureWarningsDuringValidationAsErrors = false;
		Settings.MaxAssetsToValidate = MaxAssets;
		Settings.ShowMessageLogSeverity.Reset();
		Settings.bSilent = true;
		return Settings;
	}
}

bool FUEAgentKitEditorBridge::TryCompileBlueprintResult(
	const FString& AssetPath,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (!RequireStoppedEditor(OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	FAssetData AssetData;
	if (!ResolveAssetData(AssetPath, AssetData, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	const bool bLoadedBefore = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false) != nullptr;
	UObject* Asset = AssetData.GetAsset();
	UBlueprint* Blueprint = Cast<UBlueprint>(Asset);
	if (Asset == nullptr)
	{
		OutErrorCode = TEXT("live-editor-asset-load-failed");
		OutErrorMessage = TEXT("The registered asset could not be loaded by Unreal Editor.");
		return false;
	}
	if (Blueprint == nullptr)
	{
		OutErrorCode = TEXT("live-editor-blueprint-required");
		OutErrorMessage = TEXT("ue_compile_blueprint requires a Blueprint asset.");
		return false;
	}
	UPackage* Package = Blueprint->GetOutermost();
	const bool bDirtyBefore = Package != nullptr && Package->IsDirty();
	const TSharedRef<FJsonObject> BeforeState = DescribeBlueprintState(Blueprint);

	uint64 SinceSequence = 0;
	if (LogCapture.IsValid())
	{
		FUEAgentKitLogQuery SnapshotQuery;
		SnapshotQuery.Limit = 1;
		SinceSequence = LogCapture->Query(SnapshotQuery).NextSequence;
	}
	const double Started = FPlatformTime::Seconds();
	FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
	const double DurationMs = (FPlatformTime::Seconds() - Started) * 1000.0;

	TArray<TSharedPtr<FJsonValue>> Diagnostics;
	bool bDiagnosticsTruncated = false;
	uint64 NextSequence = SinceSequence;
	if (LogCapture.IsValid())
	{
		FUEAgentKitLogQuery Query;
		Query.bCompileOnly = true;
		Query.SinceSequence = SinceSequence;
		Query.MinimumVerbosity = ELogVerbosity::Warning;
		Query.Limit = 100;
		const FUEAgentKitLogQueryResult Logs = LogCapture->Query(Query);
		for (const FUEAgentKitCapturedLogEntry& Entry : Logs.Entries)
		{
			Diagnostics.Add(MakeShared<FJsonValueObject>(DescribeCapturedLogEntry(Entry)));
		}
		bDiagnosticsTruncated = Logs.bTruncated;
		NextSequence = Logs.NextSequence;
	}

	const bool bDirtyAfter = Package != nullptr && Package->IsDirty();
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("compile-blueprint"));
	Result->SetStringField(TEXT("assetPath"), AssetData.GetObjectPathString());
	Result->SetStringField(TEXT("classPath"), AssetData.AssetClassPath.ToString());
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetStringField(TEXT("pieState"), GetPieStateName());
	Result->SetBoolField(TEXT("loadedBefore"), bLoadedBefore);
	Result->SetBoolField(TEXT("loadedAfter"), true);
	Result->SetBoolField(TEXT("loadedByBridge"), !bLoadedBefore);
	Result->SetObjectField(TEXT("before"), BeforeState);
	Result->SetObjectField(TEXT("after"), DescribeBlueprintState(Blueprint));
	Result->SetStringField(TEXT("result"), Blueprint->Status == BS_Error
		? TEXT("error")
		: (Blueprint->Status == BS_UpToDateWithWarnings ? TEXT("success-with-warnings") : TEXT("success")));
	Result->SetBoolField(TEXT("compiled"), true);
	Result->SetBoolField(TEXT("succeeded"), Blueprint->Status != BS_Error);
	Result->SetBoolField(TEXT("saved"), false);
	Result->SetBoolField(TEXT("packageDirtyBefore"), bDirtyBefore);
	Result->SetBoolField(TEXT("packageDirtyAfter"), bDirtyAfter);
	Result->SetBoolField(TEXT("packageDirtyChanged"), bDirtyBefore != bDirtyAfter);
	Result->SetNumberField(TEXT("durationMs"), DurationMs);
	Result->SetNumberField(TEXT("diagnosticStartSequence"), static_cast<double>(SinceSequence));
	Result->SetNumberField(TEXT("diagnosticNextSequence"), static_cast<double>(NextSequence));
	Result->SetArrayField(TEXT("diagnostics"), Diagnostics);
	Result->SetBoolField(TEXT("diagnosticsTruncated"), bDiagnosticsTruncated);
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryValidateAssetResult(
	const FString& AssetPath,
	const int32 MaxIssues,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (!RequireStoppedEditor(OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	FAssetData AssetData;
	if (!ResolveAssetData(AssetPath, AssetData, OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	UEditorValidatorSubsystem* ValidatorSubsystem = GEditor->GetEditorSubsystem<UEditorValidatorSubsystem>();
	if (ValidatorSubsystem == nullptr)
	{
		OutErrorCode = TEXT("live-editor-data-validation-unavailable");
		OutErrorMessage = TEXT("The Unreal Data Validation subsystem is unavailable.");
		return false;
	}
	TArray<FAssetData> Assets;
	Assets.Add(AssetData);
	const TArray<FValidationRevisionSnapshot> RevisionSnapshots = CaptureValidationRevisions(Assets);
	const FString StartedAtUtc = FDateTime::UtcNow().ToIso8601();
	const int32 DirtyBefore = CountDirtyGamePackages();
	const double Started = FPlatformTime::Seconds();
	FValidateAssetsResults Results;
	const int32 FailureCount = ValidatorSubsystem->ValidateAssetsWithSettings(
		Assets,
		MakeValidationSettings(1),
		Results);
	const double DurationMs = (FPlatformTime::Seconds() - Started) * 1000.0;
	const FString CompletedAtUtc = FDateTime::UtcNow().ToIso8601();
	const int32 DirtyAfter = CountDirtyGamePackages();
	TSharedRef<FJsonObject> Result = DescribeValidationResults(
		AssetData.GetObjectPathString(),
		SessionId,
		Results,
		FailureCount,
		MaxIssues,
		DurationMs,
		DirtyBefore,
		DirtyAfter);
	TSharedRef<FJsonObject> Evidence = BuildValidationEvidence(
		TEXT("asset"),
		StartedAtUtc,
		CompletedAtUtc,
		TEXT("pending"));
	CompleteValidationRevisionEvidence(Evidence, Assets, RevisionSnapshots);
	Result->SetObjectField(TEXT("validationEvidence"), Evidence);
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryValidateFolderResult(
	const FString& PackagePath,
	const bool bRecursive,
	const int32 MaxAssets,
	const int32 MaxIssues,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (!RequireStoppedEditor(OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	if (!IsSafeGamePackagePath(PackagePath))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("packagePath must be a non-root /Game package path.");
		return false;
	}
	FAssetRegistryModule& AssetRegistryModule =
		FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	TArray<FAssetData> Assets;
	AssetRegistryModule.Get().GetAssetsByPath(FName(*PackagePath), Assets, bRecursive);
	Assets.RemoveAll([](const FAssetData& AssetData)
	{
		return !AssetData.IsValid() || AssetData.IsRedirector();
	});
	Assets.Sort([](const FAssetData& Left, const FAssetData& Right)
	{
		return Left.GetObjectPathString() < Right.GetObjectPathString();
	});
	if (Assets.IsEmpty())
	{
		OutErrorCode = TEXT("live-editor-folder-empty");
		OutErrorMessage = TEXT("No non-redirector assets were found under the requested package path.");
		return false;
	}
	if (Assets.Num() > MaxAssets)
	{
		OutErrorCode = TEXT("live-editor-asset-limit-exceeded");
		OutErrorMessage = FString::Printf(
			TEXT("Folder validation matched %d assets, exceeding maxAssets=%d."),
			Assets.Num(),
			MaxAssets);
		return false;
	}
	UEditorValidatorSubsystem* ValidatorSubsystem = GEditor->GetEditorSubsystem<UEditorValidatorSubsystem>();
	if (ValidatorSubsystem == nullptr)
	{
		OutErrorCode = TEXT("live-editor-data-validation-unavailable");
		OutErrorMessage = TEXT("The Unreal Data Validation subsystem is unavailable.");
		return false;
	}
	const TArray<FValidationRevisionSnapshot> RevisionSnapshots = CaptureValidationRevisions(Assets);
	const FString StartedAtUtc = FDateTime::UtcNow().ToIso8601();
	const int32 DirtyBefore = CountDirtyGamePackages();
	const double Started = FPlatformTime::Seconds();
	FValidateAssetsResults Results;
	const int32 FailureCount = ValidatorSubsystem->ValidateAssetsWithSettings(
		Assets,
		MakeValidationSettings(MaxAssets),
		Results);
	const double DurationMs = (FPlatformTime::Seconds() - Started) * 1000.0;
	const FString CompletedAtUtc = FDateTime::UtcNow().ToIso8601();
	const int32 DirtyAfter = CountDirtyGamePackages();
	TSharedRef<FJsonObject> Result = DescribeValidationResults(
		PackagePath,
		SessionId,
		Results,
		FailureCount,
		MaxIssues,
		DurationMs,
		DirtyBefore,
		DirtyAfter);
	Result->SetBoolField(TEXT("recursive"), bRecursive);
	Result->SetNumberField(TEXT("matchedAssetCount"), Assets.Num());
	Result->SetNumberField(TEXT("maxAssets"), MaxAssets);
	TSharedRef<FJsonObject> Evidence = BuildValidationEvidence(
		TEXT("folder"),
		StartedAtUtc,
		CompletedAtUtc,
		TEXT("pending"));
	CompleteValidationRevisionEvidence(Evidence, Assets, RevisionSnapshots);
	Result->SetObjectField(TEXT("validationEvidence"), Evidence);
	OutResult = Result;
	return true;
}
