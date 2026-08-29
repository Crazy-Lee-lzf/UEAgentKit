#include "PerformanceFixtureCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Dom/JsonObject.h"
#include "Engine/Blueprint.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "HAL/PlatformMisc.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Misc/DateTime.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/App.h"
#include "Misc/FileHelper.h"
#include "Misc/MessageDialog.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/ConstructorHelpers.h"
#include "UObject/GarbageCollection.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UObjectGlobals.h"

DEFINE_LOG_CATEGORY_STATIC(LogPerformanceFixture, Log, All);

namespace PerformanceFixtureCommandletPrivate
{
	constexpr const TCHAR* ManifestFilename = TEXT("manifest.json");
	constexpr const TCHAR* CheckpointDir = TEXT("checkpoints");
	constexpr int64 Target50GB = 50LL * 1024 * 1024 * 1024;
	constexpr int64 HardProjectCap = 200LL * 1024 * 1024 * 1024;
	constexpr int64 MinFreeDisk = 50LL * 1024 * 1024 * 1024;
	constexpr int64 SmallBatchSize = 100;
	constexpr int32 MaxSmallAssets = 20000;
	constexpr int32 MaxSimpleBlueprints = 3000;

	FString GetProjectPath(const FString& Params)
	{
		FString ProjectPath;
		FParse::Value(*Params, TEXT("ProjectPath="), ProjectPath);
		if (ProjectPath.IsEmpty())
		{
			ProjectPath = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
		}
		return ProjectPath;
	}

	FString GetReportPath(const FString& Params)
	{
		FString Report;
		FParse::Value(*Params, TEXT("Report="), Report);
		if (Report.IsEmpty())
		{
			Report = FPaths::ConvertRelativePathToFull(FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("PerformanceFixture-report.json")));
		}
		return Report;
	}

	FString GetAction(const FString& Params)
	{
		FString Action;
		FParse::Value(*Params, TEXT("Action="), Action);
		if (Action.IsEmpty())
		{
			Action = TEXT("ValidateFixture");
		}
		return Action;
	}

	bool SaveJsonObject(const FString& Filename, const TSharedRef<FJsonObject>& Object, FString& OutError)
	{
		FString JsonText;
		const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(Object, Writer))
		{
			OutError = TEXT("Could not serialize JSON.");
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		if (!FFileHelper::SaveStringToFile(JsonText, *Filename, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write JSON file: %s"), *Filename);
			return false;
		}
		return true;
	}

	bool LoadJsonObject(const FString& Filename, TSharedPtr<FJsonObject>& OutObject, FString& OutError)
	{
		FString JsonText;
		if (!FFileHelper::LoadFileToString(JsonText, *Filename))
		{
			OutError = FString::Printf(TEXT("Could not read JSON file: %s"), *Filename);
			return false;
		}
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
		if (!FJsonSerializer::Deserialize(Reader, OutObject) || !OutObject.IsValid())
		{
			OutError = FString::Printf(TEXT("Could not parse JSON file: %s"), *Filename);
			return false;
		}
		return true;
	}

	int64 GetDirectorySize(const FString& Path)
	{
		int64 Total = 0;
		IFileManager& FileManager = IFileManager::Get();
		TArray<FString> Files;
		FileManager.FindFilesRecursive(Files, *Path, TEXT("*"), true, false, false);
		for (const FString& File : Files)
		{
			Total += FileManager.FileSize(*File);
		}
		return Total;
	}

	int64 GetFreeDiskSpace(const FString& Path)
	{
		uint64 TotalBytes = 0;
		uint64 FreeBytes = 0;
		FPlatformMisc::GetDiskTotalAndFreeSpace(Path, TotalBytes, FreeBytes);
		return static_cast<int64>(FreeBytes);
	}

	FString GetAssetPackageName(const FString& Root, const FString& Name)
	{
		return Root / Name;
	}

	bool CreateSimpleBlueprint(const FString& Root, const FString& Name, FString& OutError)
	{
		const FString PackageName = GetAssetPackageName(Root, Name);
		const FString ObjectPath = PackageName + TEXT(".") + Name;
		if (LoadObject<UBlueprint>(nullptr, *ObjectPath))
		{
			return true;
		}

		UPackage* Package = CreatePackage(*PackageName);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *PackageName);
			return false;
		}

		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(*Name),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitPerfFixture")));
		if (!Blueprint)
		{
			OutError = FString::Printf(TEXT("Could not create Blueprint: %s"), *PackageName);
			return false;
		}

		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = FString::Printf(TEXT("Blueprint compilation failed: %s"), *PackageName);
			return false;
		}

		const FString Filename = FPaths::ConvertRelativePathToFull(FPackageName::LongPackageNameToFilename(
			PackageName,
			FPackageName::GetAssetPackageExtension()));
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save Blueprint: %s"), *PackageName);
			return false;
		}
		return true;
	}

	bool DuplicateSmallAsset(const FString& SourceAsset, const FString& TargetPackage, FString& OutError)
	{
		UObject* SourceObject = LoadObject<UObject>(nullptr, *SourceAsset);
		if (!SourceObject)
		{
			OutError = FString::Printf(TEXT("Could not load source asset: %s"), *SourceAsset);
			return false;
		}

		const FString TargetName = FPackageName::GetLongPackageAssetName(TargetPackage);
		UObject* Duplicate = IAssetTools::Get().DuplicateAsset(TargetName, TargetPackage, SourceObject);
		if (!Duplicate)
		{
			OutError = FString::Printf(TEXT("Could not duplicate asset: %s -> %s"), *SourceAsset, *TargetPackage);
			return false;
		}

		UPackage* Package = Duplicate->GetOutermost();
		Package->MarkPackageDirty();
		const FString Filename = FPaths::ConvertRelativePathToFull(FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension()));
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Duplicate, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save duplicated asset: %s"), *TargetPackage);
			return false;
		}
		return true;
	}

	bool CopySeedContent(const FString& SeedRoot, const FString& ProjectRoot, FString& OutError)
	{
		const FString SeedContent = FPaths::Combine(SeedRoot, TEXT("Content"));
		const FString TargetContent = FPaths::Combine(ProjectRoot, TEXT("Content"));
		if (!IFileManager::Get().DirectoryExists(*SeedContent))
		{
			OutError = FString::Printf(TEXT("Seed Content does not exist: %s"), *SeedContent);
			return false;
		}
		IFileManager::Get().MakeDirectory(*TargetContent, true);

		TArray<FString> Files;
		IFileManager::Get().FindFilesRecursive(Files, *SeedContent, TEXT("*"), true, false, false);
		for (const FString& SourceFile : Files)
		{
			FString Relative = SourceFile;
			FPaths::MakePathRelativeTo(Relative, *SeedContent);
			const FString Destination = FPaths::Combine(TargetContent, Relative);
			IFileManager::Get().MakeDirectory(*FPaths::GetPath(Destination), true);
			if (!IFileManager::Get().Copy(*Destination, *SourceFile, true, true))
			{
				OutError = FString::Printf(TEXT("Could not copy seed file: %s"), *SourceFile);
				return false;
			}
		}
		return true;
	}

	TSharedPtr<FJsonObject> LoadOrCreateManifest(const FString& ProjectRoot, bool& bCreated, FString& OutError)
	{
		const FString ManifestPath = FPaths::Combine(ProjectRoot, TEXT(".ueak-fixture"), ManifestFilename);
		TSharedPtr<FJsonObject> Manifest;
		if (IFileManager::Get().FileExists(*ManifestPath))
		{
			if (!LoadJsonObject(ManifestPath, Manifest, OutError))
			{
				return nullptr;
			}
			bCreated = false;
			return Manifest;
		}

		Manifest = MakeShared<FJsonObject>();
		Manifest->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
		Manifest->SetStringField(TEXT("projectPath"), ProjectRoot);
		Manifest->SetStringField(TEXT("seed"), TEXT("UEAK-PERF-50GB-20260829"));
		Manifest->SetStringField(TEXT("seedSource"), TEXT("F:/UELecture/DarkRuinsMegascansSample"));
		Manifest->SetNumberField(TEXT("targetSizeBytes"), Target50GB);
		Manifest->SetNumberField(TEXT("hardProjectCapBytes"), HardProjectCap);
		Manifest->SetNumberField(TEXT("minFreeDiskBytes"), MinFreeDisk);
		Manifest->SetNumberField(TEXT("checkpointCount"), 0);
		Manifest->SetStringField(TEXT("status"), TEXT("initialized"));
		bCreated = true;
		return Manifest;
	}

	bool SaveManifest(const FString& ProjectRoot, const TSharedPtr<FJsonObject>& Manifest, FString& OutError)
	{
		const FString ManifestPath = FPaths::Combine(ProjectRoot, TEXT(".ueak-fixture"), ManifestFilename);
		return SaveJsonObject(ManifestPath, Manifest.ToSharedRef(), OutError);
	}

	bool AppendCheckpoint(const FString& ProjectRoot, const FString& Action, const TSharedRef<FJsonObject>& Entry, FString& OutError)
	{
		const FString CheckpointFile = FPaths::Combine(
			ProjectRoot,
			TEXT(".ueak-fixture"),
			CheckpointDir,
			Action + TEXT(".jsonl"));
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(CheckpointFile), true);

		FString JsonText;
		const TSharedRef<TJsonWriter<TCHAR, TCompactJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCompactJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(Entry, Writer))
		{
			OutError = TEXT("Could not serialize checkpoint entry.");
			return false;
		}
		FString Line = JsonText + TEXT("\n");
		return FFileHelper::SaveStringToFile(Line, *CheckpointFile, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM, &IFileManager::Get(), FILEWRITE_Append);
	}

	int32 CountCheckpointLines(const FString& ProjectRoot, const FString& Action)
	{
		const FString CheckpointFile = FPaths::Combine(
			ProjectRoot,
			TEXT(".ueak-fixture"),
			CheckpointDir,
			Action + TEXT(".jsonl"));
		if (!IFileManager::Get().FileExists(*CheckpointFile))
		{
			return 0;
		}
		TArray<FString> Lines;
		FFileHelper::LoadFileToStringArray(Lines, *CheckpointFile);
		return Lines.Num();
	}

	bool GenerateSmallAssets(const FString& ProjectRoot, const TSharedPtr<FJsonObject>& Manifest, FString& OutError)
	{
		const FString Root = TEXT("/Game/PerfSmall");
		const int32 Existing = CountCheckpointLines(ProjectRoot, TEXT("GenerateSmallAssets"));
		const int32 Target = MaxSmallAssets;
		int32 Generated = Existing;
		while (Generated < Target)
		{
			const FString PackageName = FString::Printf(TEXT("%s/SA_%05d"), *Root, Generated + 1);
			if (!DuplicateSmallAsset(
				TEXT("/Game/LevelPrototyping/Materials/MI_DefaultColorway.MI_DefaultColorway"),
				PackageName,
				OutError))
			{
				// If the seed asset is unavailable, create a simple Blueprint instead.
				if (!CreateSimpleBlueprint(Root, FString::Printf(TEXT("BP_SA_%05d"), Generated + 1), OutError))
				{
					return false;
				}
			}

			TSharedPtr<FJsonObject> Entry = MakeShared<FJsonObject>();
			Entry->SetNumberField(TEXT("batch"), (Generated + SmallBatchSize) / SmallBatchSize);
			Entry->SetStringField(TEXT("action"), TEXT("GenerateSmallAssets"));
			Entry->SetNumberField(TEXT("count"), Generated + 1);
			Entry->SetStringField(TEXT("completedUtc"), *FDateTime::UtcNow().ToString());
			if (!AppendCheckpoint(ProjectRoot, TEXT("GenerateSmallAssets"), Entry.ToSharedRef(), OutError))
			{
				return false;
			}
			++Generated;

			const int64 ProjectSize = GetDirectorySize(ProjectRoot);
			if (ProjectSize >= Target50GB)
			{
				Manifest->SetStringField(TEXT("status"), TEXT("target_reached"));
				UE_LOG(LogPerformanceFixture, Display, TEXT("Small-asset generation reached target size."));
				break;
			}
			if (GetFreeDiskSpace(ProjectRoot) < MinFreeDisk)
			{
				OutError = TEXT("Free disk space below minimum while generating small assets.");
				return false;
			}
		}
		Manifest->SetNumberField(TEXT("smallAssetsGenerated"), Generated);
		return true;
	}

	bool GenerateBlueprintSuite(const FString& ProjectRoot, const TSharedPtr<FJsonObject>& Manifest, FString& OutError)
	{
		const FString Root = TEXT("/Game/PerfBlueprints");
		const int32 Existing = CountCheckpointLines(ProjectRoot, TEXT("GenerateBlueprintSuite"));
		const int32 Target = MaxSimpleBlueprints;
		int32 Generated = Existing;
		while (Generated < Target)
		{
			const FString Name = FString::Printf(TEXT("BP_Perf_%05d"), Generated + 1);
			if (!CreateSimpleBlueprint(Root, Name, OutError))
			{
				return false;
			}

			TSharedPtr<FJsonObject> Entry = MakeShared<FJsonObject>();
			Entry->SetNumberField(TEXT("batch"), (Generated + SmallBatchSize) / SmallBatchSize);
			Entry->SetStringField(TEXT("action"), TEXT("GenerateBlueprintSuite"));
			Entry->SetNumberField(TEXT("count"), Generated + 1);
			Entry->SetStringField(TEXT("completedUtc"), *FDateTime::UtcNow().ToString());
			if (!AppendCheckpoint(ProjectRoot, TEXT("GenerateBlueprintSuite"), Entry.ToSharedRef(), OutError))
			{
				return false;
			}
			++Generated;

			const int64 ProjectSize = GetDirectorySize(ProjectRoot);
			if (ProjectSize >= Target50GB)
			{
				Manifest->SetStringField(TEXT("status"), TEXT("target_reached"));
				UE_LOG(LogPerformanceFixture, Display, TEXT("Blueprint generation reached target size."));
				break;
			}
			if (GetFreeDiskSpace(ProjectRoot) < MinFreeDisk)
			{
				OutError = TEXT("Free disk space below minimum while generating Blueprints.");
				return false;
			}
		}
		Manifest->SetNumberField(TEXT("blueprintsGenerated"), Generated);
		return true;
	}

	bool ValidateFixture(const FString& ProjectRoot, const TSharedPtr<FJsonObject>& Manifest, FString& OutError)
	{
		const FString ContentRoot = FPaths::Combine(ProjectRoot, TEXT("Content"));
		TArray<FString> UassetFiles;
		IFileManager::Get().FindFilesRecursive(UassetFiles, *ContentRoot, TEXT("*.uasset"), true, false, false);
		TArray<FString> UmapFiles;
		IFileManager::Get().FindFilesRecursive(UmapFiles, *ContentRoot, TEXT("*.umap"), true, false, false);

		const int64 ProjectSize = GetDirectorySize(ProjectRoot);
		const int64 FreeDisk = GetFreeDiskSpace(ProjectRoot);

		Manifest->SetNumberField(TEXT("uassetCount"), UassetFiles.Num());
		Manifest->SetNumberField(TEXT("umapCount"), UmapFiles.Num());
		Manifest->SetNumberField(TEXT("projectSizeBytes"), ProjectSize);
		Manifest->SetNumberField(TEXT("freeDiskBytes"), FreeDisk);

		if (ProjectSize < Target50GB)
		{
			Manifest->SetStringField(TEXT("status"), TEXT("in_progress"));
			UE_LOG(LogPerformanceFixture, Display, TEXT("Project size %lld bytes; target %lld bytes."), ProjectSize, Target50GB);
			return true;
		}
		if (ProjectSize > HardProjectCap)
		{
			OutError = TEXT("Project exceeded hard cap.");
			return false;
		}
		if (FreeDisk < MinFreeDisk)
		{
			OutError = TEXT("Free disk below minimum.");
			return false;
		}
		Manifest->SetStringField(TEXT("status"), TEXT("validated"));
		return true;
	}
}

UPerformanceFixtureCommandlet::UPerformanceFixtureCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UPerformanceFixtureCommandlet::Main(const FString& Params)
{
	using namespace PerformanceFixtureCommandletPrivate;

	const FString Action = GetAction(Params);
	const FString ProjectRoot = GetProjectPath(Params);
	const FString ReportPath = GetReportPath(Params);

	IFileManager::Get().MakeDirectory(*FPaths::Combine(ProjectRoot, TEXT(".ueak-fixture"), CheckpointDir), true);

	bool bCreated = false;
	FString Error;
	TSharedPtr<FJsonObject> Manifest = LoadOrCreateManifest(ProjectRoot, bCreated, Error);
	if (!Manifest)
	{
		UE_LOG(LogPerformanceFixture, Error, TEXT("%s"), *Error);
		return 1;
	}

	if (Action == TEXT("CreateProjectProfile"))
	{
		Manifest->SetStringField(TEXT("status"), TEXT("project_created"));
		Manifest->SetStringField(TEXT("lastAction"), TEXT("CreateProjectProfile"));
	}
	else if (Action == TEXT("CopySeedContent"))
	{
		const FString SeedRoot = TEXT("F:/UELecture/DarkRuinsMegascansSample");
		if (!CopySeedContent(SeedRoot, ProjectRoot, Error))
		{
			UE_LOG(LogPerformanceFixture, Error, TEXT("%s"), *Error);
			return 2;
		}
		Manifest->SetStringField(TEXT("status"), TEXT("seed_copied"));
		Manifest->SetStringField(TEXT("lastAction"), TEXT("CopySeedContent"));
		Manifest->SetNumberField(TEXT("seedContentBytes"), GetDirectorySize(FPaths::Combine(ProjectRoot, TEXT("Content"))));
	}
	else if (Action == TEXT("GenerateSmallAssets"))
	{
		if (!GenerateSmallAssets(ProjectRoot, Manifest, Error))
		{
			UE_LOG(LogPerformanceFixture, Error, TEXT("%s"), *Error);
			return 3;
		}
		Manifest->SetStringField(TEXT("lastAction"), TEXT("GenerateSmallAssets"));
	}
	else if (Action == TEXT("GenerateBlueprintSuite"))
	{
		if (!GenerateBlueprintSuite(ProjectRoot, Manifest, Error))
		{
			UE_LOG(LogPerformanceFixture, Error, TEXT("%s"), *Error);
			return 4;
		}
		Manifest->SetStringField(TEXT("lastAction"), TEXT("GenerateBlueprintSuite"));
	}
	else if (Action == TEXT("ValidateFixture"))
	{
		if (!ValidateFixture(ProjectRoot, Manifest, Error))
		{
			UE_LOG(LogPerformanceFixture, Error, TEXT("%s"), *Error);
			return 5;
		}
		Manifest->SetStringField(TEXT("lastAction"), TEXT("ValidateFixture"));
	}
	else if (Action == TEXT("CleanupFixture"))
	{
		IFileManager::Get().DeleteDirectory(*FPaths::Combine(ProjectRoot, TEXT("Content"), TEXT("PerfSmall")), false, true);
		IFileManager::Get().DeleteDirectory(*FPaths::Combine(ProjectRoot, TEXT("Content"), TEXT("PerfBlueprints")), false, true);
		Manifest->SetStringField(TEXT("status"), TEXT("cleaned"));
		Manifest->SetStringField(TEXT("lastAction"), TEXT("CleanupFixture"));
	}
	else
	{
		UE_LOG(LogPerformanceFixture, Error, TEXT("Unknown action: %s"), *Action);
		return 6;
	}

	Manifest->SetNumberField(TEXT("checkpointCount"), CountCheckpointLines(ProjectRoot, TEXT("GenerateSmallAssets")) + CountCheckpointLines(ProjectRoot, TEXT("GenerateBlueprintSuite")));
	Manifest->SetStringField(TEXT("updatedUtc"), *FDateTime::UtcNow().ToString());

	if (!SaveManifest(ProjectRoot, Manifest, Error))
	{
		UE_LOG(LogPerformanceFixture, Error, TEXT("%s"), *Error);
		return 7;
	}

	TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
	Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
	Report->SetStringField(TEXT("action"), Action);
	Report->SetStringField(TEXT("projectPath"), ProjectRoot);
	Report->SetObjectField(TEXT("manifest"), Manifest);
	if (!SaveJsonObject(ReportPath, Report, Error))
	{
		UE_LOG(LogPerformanceFixture, Error, TEXT("%s"), *Error);
		return 8;
	}

	UE_LOG(LogPerformanceFixture, Display, TEXT("PerformanceFixture %s completed: %s"), *Action, *ProjectRoot);
	return 0;
}
