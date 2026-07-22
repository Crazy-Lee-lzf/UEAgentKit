#include "BlueprintContextExportCommandlet.h"

#include "AssetRegistry/AssetData.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintContextExporter.h"
#include "Dom/JsonObject.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintContextExport, Log, All);

namespace BlueprintContextCommandletPrivate
{
	FString NormalizeObjectPath(FString Path)
	{
		Path.TrimStartAndEndInline();
		Path.TrimQuotesInline();
		if (Path.IsEmpty())
		{
			return Path;
		}

		if (Path.EndsWith(TEXT("_C")))
		{
			Path.LeftChopInline(2, EAllowShrinking::No);
		}

		const int32 LastSlash = Path.Find(TEXT("/"), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		const int32 LastDot = Path.Find(TEXT("."), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		if (LastDot <= LastSlash)
		{
			Path += TEXT(".") + FPackageName::GetShortName(Path);
		}
		return Path;
	}

	bool SaveManifest(
		const FString& OutputDirectory,
		const FString& Profile,
		const TArray<FBlueprintContextExportResult>& Results)
	{
		TSharedRef<FJsonObject> RootObject = MakeShared<FJsonObject>();
		RootObject->SetStringField(TEXT("schemaVersion"), TEXT("1.1"));
		RootObject->SetStringField(TEXT("exporterVersion"), TEXT("0.4.4"));
		RootObject->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		RootObject->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		RootObject->SetStringField(TEXT("createdUtc"), FDateTime::UtcNow().ToIso8601());
		RootObject->SetStringField(TEXT("profile"), Profile);

		int32 SuccessCount = 0;
		TArray<TSharedPtr<FJsonValue>> Assets;
		for (const FBlueprintContextExportResult& Result : Results)
		{
			SuccessCount += Result.bSuccess ? 1 : 0;
			TSharedRef<FJsonObject> AssetObject = MakeShared<FJsonObject>();
			AssetObject->SetStringField(TEXT("assetPath"), Result.AssetPath);
			AssetObject->SetBoolField(TEXT("success"), Result.bSuccess);
			AssetObject->SetStringField(TEXT("jsonPath"), Result.JsonPath);
			AssetObject->SetStringField(TEXT("bpctxPath"), Result.BpctxPath);
			AssetObject->SetStringField(TEXT("error"), Result.Error);
			AssetObject->SetNumberField(TEXT("variables"), Result.VariableCount);
			AssetObject->SetNumberField(TEXT("components"), Result.ComponentCount);
			AssetObject->SetNumberField(TEXT("graphs"), Result.GraphCount);
			AssetObject->SetNumberField(TEXT("nodes"), Result.NodeCount);
			AssetObject->SetNumberField(TEXT("pins"), Result.PinCount);
			AssetObject->SetNumberField(TEXT("links"), Result.LinkCount);
			AssetObject->SetNumberField(TEXT("symbols"), Result.SymbolCount);
			AssetObject->SetNumberField(TEXT("references"), Result.ReferenceCount);
			Assets.Add(MakeShared<FJsonValueObject>(AssetObject));
		}

		RootObject->SetNumberField(TEXT("assetCount"), Results.Num());
		RootObject->SetNumberField(TEXT("successCount"), SuccessCount);
		RootObject->SetNumberField(TEXT("failureCount"), Results.Num() - SuccessCount);
		RootObject->SetArrayField(TEXT("assets"), Assets);

		FString JsonText;
		const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(RootObject, Writer))
		{
			return false;
		}

		IFileManager::Get().MakeDirectory(*OutputDirectory, true);
		return FFileHelper::SaveStringToFile(
			JsonText,
			*FPaths::Combine(OutputDirectory, TEXT("manifest.json")),
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}
}

UBlueprintContextExportCommandlet::UBlueprintContextExportCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UBlueprintContextExportCommandlet::Main(const FString& Params)
{
	using namespace BlueprintContextCommandletPrivate;

	FString AssetPath;
	FString RootPath;
	FString OutputDirectory;
	FString ProfileValue = TEXT("logic");
	FString FormatValue = TEXT("both");
	FString GraphFilter;

	FParse::Value(*Params, TEXT("Asset="), AssetPath);
	FParse::Value(*Params, TEXT("Root="), RootPath);
	FParse::Value(*Params, TEXT("Output="), OutputDirectory);
	FParse::Value(*Params, TEXT("Profile="), ProfileValue);
	FParse::Value(*Params, TEXT("Format="), FormatValue);
	FParse::Value(*Params, TEXT("Graph="), GraphFilter);

	if (AssetPath.IsEmpty() && RootPath.IsEmpty())
	{
		UE_LOG(
			LogBlueprintContextExport,
			Error,
			TEXT("Specify -Asset=/Game/Path/BP_Name or -Root=/Game/Folder."));
		return 1;
	}

	if (!AssetPath.IsEmpty() && !RootPath.IsEmpty())
	{
		UE_LOG(LogBlueprintContextExport, Error, TEXT("Use either -Asset or -Root, not both."));
		return 1;
	}

	FBlueprintContextExportOptions Options;
	if (!FBlueprintContextExporter::ParseProfile(ProfileValue, Options.Profile))
	{
		UE_LOG(LogBlueprintContextExport, Error, TEXT("Unknown profile: %s"), *ProfileValue);
		return 1;
	}

	Options.OutputDirectory = OutputDirectory.IsEmpty()
		? FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("BlueprintContextExport"))
		: FPaths::ConvertRelativePathToFull(OutputDirectory);
	Options.GraphFilter = GraphFilter;
	FBlueprintContextExporter::ApplyProfileDefaults(Options);

	if (FormatValue.Equals(TEXT("json"), ESearchCase::IgnoreCase))
	{
		Options.bWriteJson = true;
		Options.bWriteBpctx = false;
	}
	else if (FormatValue.Equals(TEXT("bpctx"), ESearchCase::IgnoreCase))
	{
		Options.bWriteJson = false;
		Options.bWriteBpctx = true;
	}
	else if (FormatValue.Equals(TEXT("both"), ESearchCase::IgnoreCase))
	{
		Options.bWriteJson = true;
		Options.bWriteBpctx = true;
	}
	else
	{
		UE_LOG(LogBlueprintContextExport, Error, TEXT("Unknown format: %s"), *FormatValue);
		return 1;
	}

	if (FParse::Param(*Params, TEXT("CompactJson")))
	{
		Options.bPrettyJson = false;
	}
	if (FParse::Param(*Params, TEXT("IncludeLayout")))
	{
		Options.bIncludeLayout = true;
	}
	if (FParse::Param(*Params, TEXT("NoNodeProperties")))
	{
		Options.bIncludeReflectedNodeProperties = false;
	}
	if (FParse::Param(*Params, TEXT("IncludeUnchangedDefaults")))
	{
		Options.bIncludeUnchangedDefaults = true;
	}

	IAssetRegistry& AssetRegistry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	TArray<FAssetData> AssetDataList;

	if (!AssetPath.IsEmpty())
	{
		const FString ObjectPath = NormalizeObjectPath(AssetPath);
		const FAssetData AssetData = AssetRegistry.GetAssetByObjectPath(FSoftObjectPath(ObjectPath));
		if (AssetData.IsValid())
		{
			AssetDataList.Add(AssetData);
		}
		else if (UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *ObjectPath))
		{
			AssetDataList.Add(FAssetData(Blueprint));
		}
	}
	else
	{
		RootPath.TrimStartAndEndInline();
		RootPath.TrimQuotesInline();
		if (!RootPath.StartsWith(TEXT("/")))
		{
			UE_LOG(LogBlueprintContextExport, Error, TEXT("Root must be a package path such as /Game/Folder."));
			return 1;
		}

		AssetRegistry.ScanPathsSynchronous({ RootPath }, true);
		FARFilter Filter;
		Filter.PackagePaths.Add(FName(*RootPath));
		Filter.ClassPaths.Add(UBlueprint::StaticClass()->GetClassPathName());
		Filter.bRecursivePaths = true;
		Filter.bRecursiveClasses = true;
		AssetRegistry.GetAssets(Filter, AssetDataList);
	}

	AssetDataList.Sort([](const FAssetData& A, const FAssetData& B)
	{
		return A.GetObjectPathString() < B.GetObjectPathString();
	});

	if (AssetDataList.IsEmpty())
	{
		UE_LOG(LogBlueprintContextExport, Error, TEXT("No Blueprint assets found."));
		return 2;
	}

	UE_LOG(
		LogBlueprintContextExport,
		Display,
		TEXT("Exporting %d Blueprint asset(s) to %s with profile %s."),
		AssetDataList.Num(),
		*Options.OutputDirectory,
		*FBlueprintContextExporter::ProfileToString(Options.Profile));

	TArray<FBlueprintContextExportResult> Results;
	Results.Reserve(AssetDataList.Num());
	int32 FailureCount = 0;

	for (const FAssetData& AssetData : AssetDataList)
	{
		FBlueprintContextExportResult Result;
		Result.AssetPath = AssetData.GetObjectPathString();

		UBlueprint* Blueprint = Cast<UBlueprint>(AssetData.GetAsset());
		if (!Blueprint)
		{
			Result.Error = TEXT("Asset could not be loaded as UBlueprint.");
			++FailureCount;
			Results.Add(MoveTemp(Result));
			UE_LOG(LogBlueprintContextExport, Error, TEXT("Failed: %s"), *AssetData.GetObjectPathString());
			continue;
		}

		if (!FBlueprintContextExporter::ExportBlueprint(Blueprint, Options, Result))
		{
			++FailureCount;
			UE_LOG(
				LogBlueprintContextExport,
				Error,
				TEXT("Failed: %s - %s"),
				*Blueprint->GetPathName(),
				*Result.Error);
		}
		else
		{
			UE_LOG(
				LogBlueprintContextExport,
				Display,
				TEXT("Exported: %s (graphs=%d nodes=%d pins=%d links=%d symbols=%d references=%d)"),
				*Blueprint->GetPathName(),
				Result.GraphCount,
				Result.NodeCount,
				Result.PinCount,
				Result.LinkCount,
				Result.SymbolCount,
				Result.ReferenceCount);
		}
		Results.Add(MoveTemp(Result));
	}

	if (!SaveManifest(Options.OutputDirectory, FBlueprintContextExporter::ProfileToString(Options.Profile), Results))
	{
		UE_LOG(LogBlueprintContextExport, Warning, TEXT("Failed to write manifest.json."));
	}

	UE_LOG(
		LogBlueprintContextExport,
		Display,
		TEXT("Blueprint export finished. Success=%d Failure=%d"),
		Results.Num() - FailureCount,
		FailureCount);
	return FailureCount == 0 ? 0 : 3;
}
