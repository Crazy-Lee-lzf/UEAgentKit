#include "AssetCatalogExportCommandlet.h"

#include "AssetReaders/AssetReaderRegistry.h"

#include "AssetRegistry/AssetData.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "BlueprintContextSha256.h"
#include "Dom/JsonObject.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

DEFINE_LOG_CATEGORY_STATIC(LogAssetCatalogExport, Log, All);

namespace AssetCatalogExportPrivate
{
	constexpr const TCHAR* SchemaVersion = TEXT("1.1");
	constexpr const TCHAR* ExporterVersion = TEXT("0.3.6");
	constexpr const TCHAR* ProfileName = TEXT("asset-index");

	struct FAssetCatalogExportResult
	{
		FString AssetPath;
		FString JsonPath;
		FString Error;
		bool bSuccess = false;
		int32 TagCount = 0;
		int32 ReferenceCount = 0;
		FString ReaderName;
		FString ReaderStatus;
		FString ReaderError;
	};

	FString NormalizeObjectPath(FString Path)
	{
		Path.TrimStartAndEndInline();
		Path.TrimQuotesInline();
		if (Path.IsEmpty())
		{
			return Path;
		}

		const int32 LastSlash = Path.Find(TEXT("/"), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		const int32 LastDot = Path.Find(TEXT("."), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
		if (LastDot <= LastSlash)
		{
			Path += TEXT(".") + FPackageName::GetShortName(Path);
		}
		return Path;
	}

	FString NameOrEmpty(const FName Name)
	{
		return Name.IsNone() ? FString() : Name.ToString();
	}

	FString MakeSymbolId(const FString& Kind, const FString& OwnerPath, const FString& StableKey = FString())
	{
		return StableKey.IsEmpty()
			? FString::Printf(TEXT("%s|%s"), *Kind, *OwnerPath)
			: FString::Printf(TEXT("%s|%s|%s"), *Kind, *OwnerPath, *StableKey);
	}

	FString DependencyDomainName(const FString& PackageName)
	{
		if (PackageName.StartsWith(TEXT("/Game/")))
		{
			return TEXT("project");
		}
		if (PackageName.StartsWith(TEXT("/Engine/")))
		{
			return TEXT("engine-content");
		}
		if (PackageName.StartsWith(TEXT("/Script/")))
		{
			return TEXT("script");
		}
		return PackageName.StartsWith(TEXT("/")) ? TEXT("plugin-or-mounted") : TEXT("external");
	}

	FString DependencyCategoryName(const UE::AssetRegistry::EDependencyCategory Category)
	{
		using namespace UE::AssetRegistry;
		if (EnumHasAnyFlags(Category, EDependencyCategory::Package))
		{
			return TEXT("package");
		}
		if (EnumHasAnyFlags(Category, EDependencyCategory::Manage))
		{
			return TEXT("manage");
		}
		if (EnumHasAnyFlags(Category, EDependencyCategory::SearchableName))
		{
			return TEXT("searchable-name");
		}
		return TEXT("unknown");
	}

	FString DependencyPropertiesName(
		const UE::AssetRegistry::EDependencyCategory Category,
		const UE::AssetRegistry::EDependencyProperty Properties)
	{
		using namespace UE::AssetRegistry;
		TArray<FString> Names;
		if (EnumHasAnyFlags(Category, EDependencyCategory::Package))
		{
			Names.Add(EnumHasAnyFlags(Properties, EDependencyProperty::Hard) ? TEXT("hard") : TEXT("soft"));
			Names.Add(EnumHasAnyFlags(Properties, EDependencyProperty::Game) ? TEXT("game") : TEXT("editor-only"));
			if (EnumHasAnyFlags(Properties, EDependencyProperty::Build))
			{
				Names.Add(TEXT("build"));
			}
		}
		if (EnumHasAnyFlags(Category, EDependencyCategory::Manage))
		{
			Names.Add(EnumHasAnyFlags(Properties, EDependencyProperty::Direct) ? TEXT("direct") : TEXT("indirect"));
		}
		return FString::Join(Names, TEXT(","));
	}

	FString AssetDependencyReferenceKind(const FAssetDependency& Dependency)
	{
		using namespace UE::AssetRegistry;
		if (EnumHasAnyFlags(Dependency.Category, EDependencyCategory::Package))
		{
			return EnumHasAnyFlags(Dependency.Properties, EDependencyProperty::Hard)
				? TEXT("depends-hard-package")
				: TEXT("depends-soft-package");
		}
		if (EnumHasAnyFlags(Dependency.Category, EDependencyCategory::Manage))
		{
			return EnumHasAnyFlags(Dependency.Properties, EDependencyProperty::Direct)
				? TEXT("manages-direct")
				: TEXT("manages-indirect");
		}
		if (EnumHasAnyFlags(Dependency.Category, EDependencyCategory::SearchableName))
		{
			return TEXT("depends-searchable-name");
		}
		return TEXT("depends-asset-registry");
	}

	FString ResolveAssetPathForPackage(IAssetRegistry& AssetRegistry, const FName PackageName)
	{
		if (PackageName.IsNone())
		{
			return FString();
		}

		TArray<FAssetData> Assets;
		if (!AssetRegistry.GetAssetsByPackageName(PackageName, Assets, true) || Assets.IsEmpty())
		{
			return FString();
		}

		Assets.Sort([](const FAssetData& Left, const FAssetData& Right)
		{
			return Left.GetSoftObjectPath().ToString() < Right.GetSoftObjectPath().ToString();
		});
		return Assets[0].GetSoftObjectPath().ToString();
	}

	TSharedRef<FJsonObject> BuildRevision(const FAssetData& AssetData)
	{
		TSharedRef<FJsonObject> Revision = MakeShared<FJsonObject>();
		Revision->SetStringField(TEXT("strategy"), TEXT("package-sha256-v1"));
		Revision->SetBoolField(TEXT("available"), false);
		Revision->SetBoolField(TEXT("packageDirty"), false);
		Revision->SetStringField(TEXT("value"), FString());
		Revision->SetStringField(TEXT("packageGuid"), FString());
		Revision->SetNumberField(TEXT("fileSize"), 0.0);
		Revision->SetStringField(TEXT("modifiedUtc"), FString());
		Revision->SetStringField(TEXT("contentSha256"), FString());

		FString PackageFilename;
		if (!FPackageName::DoesPackageExist(AssetData.PackageName.ToString(), &PackageFilename)
			|| !IFileManager::Get().FileExists(*PackageFilename))
		{
			return Revision;
		}

		const int64 FileSize = IFileManager::Get().FileSize(*PackageFilename);
		const FDateTime ModifiedUtc = IFileManager::Get().GetTimeStamp(*PackageFilename);
		Revision->SetNumberField(TEXT("fileSize"), static_cast<double>(FMath::Max<int64>(FileSize, 0)));
		Revision->SetStringField(TEXT("modifiedUtc"), ModifiedUtc.ToIso8601());

		FString ContentSha256;
		if (FileSize >= 0 && FBlueprintContextSha256::HashFile(PackageFilename, ContentSha256))
		{
			Revision->SetBoolField(TEXT("available"), true);
			Revision->SetStringField(TEXT("contentSha256"), ContentSha256);
			Revision->SetStringField(TEXT("value"), TEXT("sha256:") + ContentSha256);
		}
		return Revision;
	}

	TSharedRef<FJsonObject> BuildRegistryTags(const FAssetData& AssetData, int32& OutTagCount)
	{
		TArray<TPair<FString, FString>> Tags;
		AssetData.EnumerateTags([&Tags](const auto& Pair)
		{
			Tags.Emplace(Pair.Key.ToString(), Pair.Value.AsString());
		});
		Tags.Sort([](const TPair<FString, FString>& Left, const TPair<FString, FString>& Right)
		{
			return Left.Key < Right.Key;
		});

		TSharedRef<FJsonObject> TagObject = MakeShared<FJsonObject>();
		for (const TPair<FString, FString>& Tag : Tags)
		{
			TagObject->SetStringField(Tag.Key, Tag.Value);
		}
		OutTagCount = Tags.Num();
		return TagObject;
	}

	TArray<TSharedPtr<FJsonValue>> BuildDependencies(
		IAssetRegistry& AssetRegistry,
		const FAssetData& AssetData,
		const FString& AssetSymbolId)
	{
		TArray<TSharedPtr<FJsonValue>> References;
		TSet<FString> ReferenceIds;
		TArray<FAssetDependency> Dependencies;
		if (!AssetRegistry.GetDependencies(
			FAssetIdentifier(AssetData.PackageName),
			Dependencies,
			UE::AssetRegistry::EDependencyCategory::All))
		{
			return References;
		}

		Dependencies.Sort([](const FAssetDependency& Left, const FAssetDependency& Right)
		{
			return Left.LexicalLess(Right);
		});

		for (const FAssetDependency& Dependency : Dependencies)
		{
			if (!Dependency.AssetId.IsValid() || Dependency.AssetId.PackageName == AssetData.PackageName)
			{
				continue;
			}

			const FString Identifier = Dependency.AssetId.ToString();
			const FString TargetPackageName = Dependency.AssetId.PackageName.ToString();
			const FString TargetAssetPath = ResolveAssetPathForPackage(AssetRegistry, Dependency.AssetId.PackageName);
			const FString TargetKind = !TargetAssetPath.IsEmpty()
				? TEXT("asset")
				: (Dependency.AssetId.GetPrimaryAssetId().IsValid()
					? TEXT("primary-asset")
					: (Dependency.AssetId.IsValue() ? TEXT("searchable-name") : TEXT("package")));
			const FString TargetSymbolId = MakeSymbolId(
				TargetKind,
				TargetAssetPath.IsEmpty() ? Identifier : TargetAssetPath);
			const FString ReferenceKind = AssetDependencyReferenceKind(Dependency);
			const FString DependencyProperties = DependencyPropertiesName(Dependency.Category, Dependency.Properties);
			const FString ReferenceId = MakeSymbolId(
				TEXT("reference"),
				ReferenceKind,
				AssetSymbolId + TEXT("|") + TargetSymbolId + TEXT("|") + DependencyProperties);
			if (ReferenceIds.Contains(ReferenceId))
			{
				continue;
			}
			ReferenceIds.Add(ReferenceId);

			const FString TargetName = !Dependency.AssetId.ValueName.IsNone()
				? Dependency.AssetId.ValueName.ToString()
				: (!Dependency.AssetId.ObjectName.IsNone()
					? Dependency.AssetId.ObjectName.ToString()
					: FPackageName::GetShortName(TargetPackageName));
			TSharedRef<FJsonObject> Reference = MakeShared<FJsonObject>();
			Reference->SetStringField(TEXT("id"), ReferenceId);
			Reference->SetStringField(TEXT("kind"), ReferenceKind);
			Reference->SetStringField(TEXT("sourceSymbolId"), AssetSymbolId);
			Reference->SetStringField(TEXT("targetSymbolId"), TargetSymbolId);
			Reference->SetStringField(TEXT("targetKind"), TargetKind);
			Reference->SetStringField(TEXT("targetName"), TargetName);
			Reference->SetStringField(TEXT("targetAssetPath"), TargetAssetPath);
			Reference->SetStringField(TEXT("targetPath"), Identifier);
			Reference->SetStringField(TEXT("targetPackageName"), TargetPackageName);
			Reference->SetStringField(TEXT("targetObjectName"), NameOrEmpty(Dependency.AssetId.ObjectName));
			Reference->SetStringField(TEXT("targetValueName"), NameOrEmpty(Dependency.AssetId.ValueName));
			Reference->SetStringField(
				TEXT("targetPrimaryAssetType"),
				NameOrEmpty(Dependency.AssetId.PrimaryAssetType.GetName()));
			Reference->SetStringField(TEXT("dependencyCategory"), DependencyCategoryName(Dependency.Category));
			Reference->SetStringField(TEXT("dependencyProperties"), DependencyProperties);
			Reference->SetStringField(TEXT("dependencyDomain"), DependencyDomainName(TargetPackageName));
			Reference->SetBoolField(
				TEXT("hard"),
				EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Hard));
			Reference->SetBoolField(
				TEXT("game"),
				EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Game));
			Reference->SetBoolField(
				TEXT("build"),
				EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Build));
			Reference->SetBoolField(
				TEXT("direct"),
				EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Direct));
			References.Add(MakeShared<FJsonValueObject>(Reference));
		}
		return References;
	}

	FString MakeCanonicalPath(const FString& OutputDirectory, const FAssetData& AssetData)
	{
		FString RelativePackage = AssetData.PackageName.ToString();
		RelativePackage.RemoveFromStart(TEXT("/"));
		const FString Directory = FPaths::GetPath(RelativePackage);
		const FString Filename = FPaths::MakeValidFileName(
			FPaths::GetCleanFilename(RelativePackage) + TEXT("_") + AssetData.AssetName.ToString() + TEXT(".json"));
		return FPaths::Combine(OutputDirectory, TEXT("canonical"), Directory, Filename);
	}

	bool SaveJsonObject(const TSharedRef<FJsonObject>& Object, const FString& Filename, const bool bPrettyJson)
	{
		FString JsonText;
		bool bSerialized = false;
		if (bPrettyJson)
		{
			const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
				TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
			bSerialized = FJsonSerializer::Serialize(Object, Writer);
		}
		else
		{
			const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
				TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&JsonText);
			bSerialized = FJsonSerializer::Serialize(Object, Writer);
		}
		if (!bSerialized)
		{
			return false;
		}

		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		return FFileHelper::SaveStringToFile(
			JsonText,
			*Filename,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	bool ExportAsset(
		IAssetRegistry& AssetRegistry,
		const FAssetData& AssetData,
		const FString& OutputDirectory,
		const bool bIncludeTags,
		const bool bPrettyJson,
		FAssetCatalogExportResult& OutResult)
	{
		OutResult.AssetPath = AssetData.GetSoftObjectPath().ToString();
		OutResult.JsonPath = MakeCanonicalPath(OutputDirectory, AssetData);

		int32 TagCount = 0;
		TSharedRef<FJsonObject> RegistryTags = bIncludeTags
			? BuildRegistryTags(AssetData, TagCount)
			: MakeShared<FJsonObject>();
		const FString AssetSymbolId = MakeSymbolId(TEXT("asset"), OutResult.AssetPath);
		TArray<TSharedPtr<FJsonValue>> References = BuildDependencies(AssetRegistry, AssetData, AssetSymbolId);

		TSharedRef<FJsonObject> AssetDetails = MakeShared<FJsonObject>();
		FString ReaderName;
		FString ReaderError;
		const EAssetReaderStatus ReaderStatus = FAssetReaderRegistry::ReadAssetDetails(
			AssetData,
			AssetDetails,
			ReaderName,
			ReaderError);

		TArray<TSharedPtr<FJsonValue>> ChunkIds;
		for (const int32 ChunkId : AssetData.GetChunkIDs())
		{
			ChunkIds.Add(MakeShared<FJsonValueNumber>(ChunkId));
		}

		TSharedRef<FJsonObject> RegistryObject = MakeShared<FJsonObject>();
		RegistryObject->SetStringField(TEXT("assetName"), AssetData.AssetName.ToString());
		RegistryObject->SetStringField(TEXT("packageName"), AssetData.PackageName.ToString());
		RegistryObject->SetStringField(TEXT("packagePath"), AssetData.PackagePath.ToString());
		RegistryObject->SetStringField(TEXT("assetClassPath"), AssetData.AssetClassPath.ToString());
		RegistryObject->SetNumberField(TEXT("packageFlags"), static_cast<double>(AssetData.PackageFlags));
		RegistryObject->SetBoolField(TEXT("redirector"), AssetData.IsRedirector());
		RegistryObject->SetArrayField(TEXT("chunkIds"), ChunkIds);
		RegistryObject->SetObjectField(TEXT("tags"), RegistryTags);

		TSharedRef<FJsonObject> AssetSymbol = MakeShared<FJsonObject>();
		AssetSymbol->SetStringField(TEXT("id"), AssetSymbolId);
		AssetSymbol->SetStringField(TEXT("kind"), TEXT("asset"));
		AssetSymbol->SetStringField(TEXT("name"), AssetData.AssetName.ToString());
		AssetSymbol->SetStringField(TEXT("assetPath"), OutResult.AssetPath);
		AssetSymbol->SetStringField(TEXT("path"), OutResult.AssetPath);
		AssetSymbol->SetStringField(TEXT("class"), AssetData.AssetClassPath.ToString());
		AssetSymbol->SetObjectField(TEXT("assetRegistry"), RegistryObject);
		AssetSymbol->SetStringField(TEXT("assetReader"), ReaderName);
		AssetSymbol->SetStringField(TEXT("assetReaderStatus"), FAssetReaderRegistry::StatusToString(ReaderStatus));
		AssetSymbol->SetObjectField(TEXT("assetDetails"), AssetDetails);
		TArray<TSharedPtr<FJsonValue>> Symbols;
		Symbols.Add(MakeShared<FJsonValueObject>(AssetSymbol));

		TSharedRef<FJsonObject> Summary = MakeShared<FJsonObject>();
		Summary->SetNumberField(TEXT("variables"), 0);
		Summary->SetNumberField(TEXT("components"), 0);
		Summary->SetNumberField(TEXT("graphs"), 0);
		Summary->SetNumberField(TEXT("nodes"), 0);
		Summary->SetNumberField(TEXT("pins"), 0);
		Summary->SetNumberField(TEXT("links"), 0);
		Summary->SetNumberField(TEXT("symbols"), Symbols.Num());
		Summary->SetNumberField(TEXT("references"), References.Num());
		Summary->SetNumberField(TEXT("registryTags"), TagCount);
		Summary->SetNumberField(TEXT("specializedDetails"), ReaderStatus == EAssetReaderStatus::Success ? 1 : 0);

		TSharedRef<FJsonObject> RootObject = MakeShared<FJsonObject>();
		RootObject->SetStringField(TEXT("schemaVersion"), SchemaVersion);
		RootObject->SetStringField(TEXT("exporterVersion"), ExporterVersion);
		RootObject->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		RootObject->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		RootObject->SetStringField(TEXT("profile"), ProfileName);
		RootObject->SetStringField(TEXT("assetPath"), OutResult.AssetPath);
		RootObject->SetStringField(TEXT("packageName"), AssetData.PackageName.ToString());
		RootObject->SetStringField(TEXT("assetName"), AssetData.AssetName.ToString());
		RootObject->SetStringField(TEXT("assetClass"), AssetData.AssetClassPath.ToString());
		RootObject->SetStringField(TEXT("blueprintType"), FString());
		RootObject->SetStringField(TEXT("parentClass"), FString());
		RootObject->SetStringField(TEXT("generatedClass"), FString());
		RootObject->SetStringField(TEXT("skeletonGeneratedClass"), FString());
		RootObject->SetNumberField(TEXT("status"), 0);
		RootObject->SetObjectField(TEXT("revision"), BuildRevision(AssetData));
		RootObject->SetObjectField(TEXT("assetRegistry"), RegistryObject);
		RootObject->SetStringField(TEXT("assetReader"), ReaderName);
		RootObject->SetStringField(TEXT("assetReaderStatus"), FAssetReaderRegistry::StatusToString(ReaderStatus));
		RootObject->SetStringField(TEXT("assetReaderError"), ReaderError);
		RootObject->SetObjectField(TEXT("assetDetails"), AssetDetails);
		RootObject->SetArrayField(TEXT("interfaces"), {});
		RootObject->SetArrayField(TEXT("variables"), {});
		RootObject->SetArrayField(TEXT("components"), {});
		RootObject->SetArrayField(TEXT("functions"), {});
		RootObject->SetArrayField(TEXT("graphs"), {});
		RootObject->SetArrayField(TEXT("symbols"), Symbols);
		RootObject->SetArrayField(TEXT("references"), References);
		RootObject->SetObjectField(TEXT("summary"), Summary);

		if (!SaveJsonObject(RootObject, OutResult.JsonPath, bPrettyJson))
		{
			OutResult.Error = TEXT("Failed to write Canonical JSON.");
			return false;
		}

		OutResult.bSuccess = true;
		OutResult.TagCount = TagCount;
		OutResult.ReferenceCount = References.Num();
		OutResult.ReaderName = ReaderName;
		OutResult.ReaderStatus = FAssetReaderRegistry::StatusToString(ReaderStatus);
		OutResult.ReaderError = ReaderError;
		return true;
	}

	bool SaveManifest(
		const FString& OutputDirectory,
		const FString& RootPath,
		const bool bIncludeBlueprints,
		const bool bIncludeGenerated,
		const bool bIncludeTags,
		const int32 SkippedBlueprintCount,
		const int32 SkippedGeneratedCount,
		const TArray<FAssetCatalogExportResult>& Results,
		const bool bPrettyJson)
	{
		TSharedRef<FJsonObject> RootObject = MakeShared<FJsonObject>();
		RootObject->SetStringField(TEXT("schemaVersion"), SchemaVersion);
		RootObject->SetStringField(TEXT("exporterVersion"), ExporterVersion);
		RootObject->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		RootObject->SetStringField(TEXT("projectName"), FApp::GetProjectName());
		RootObject->SetStringField(TEXT("createdUtc"), FDateTime::UtcNow().ToIso8601());
		RootObject->SetStringField(TEXT("profile"), ProfileName);
		RootObject->SetStringField(TEXT("root"), RootPath);
		RootObject->SetBoolField(TEXT("includeBlueprints"), bIncludeBlueprints);
		RootObject->SetBoolField(TEXT("includeGenerated"), bIncludeGenerated);
		RootObject->SetBoolField(TEXT("includeTags"), bIncludeTags);
		RootObject->SetNumberField(TEXT("skippedBlueprintCount"), SkippedBlueprintCount);
		RootObject->SetNumberField(TEXT("skippedGeneratedCount"), SkippedGeneratedCount);

		int32 SuccessCount = 0;
		int32 ReaderSuccessCount = 0;
		int32 ReaderFailureCount = 0;
		TArray<TSharedPtr<FJsonValue>> Assets;
		for (const FAssetCatalogExportResult& Result : Results)
		{
			SuccessCount += Result.bSuccess ? 1 : 0;
			ReaderSuccessCount += Result.ReaderStatus == TEXT("success") ? 1 : 0;
			ReaderFailureCount += Result.ReaderStatus == TEXT("failed") ? 1 : 0;
			TSharedRef<FJsonObject> AssetObject = MakeShared<FJsonObject>();
			AssetObject->SetStringField(TEXT("assetPath"), Result.AssetPath);
			AssetObject->SetBoolField(TEXT("success"), Result.bSuccess);
			AssetObject->SetStringField(TEXT("jsonPath"), Result.JsonPath);
			AssetObject->SetStringField(TEXT("bpctxPath"), FString());
			AssetObject->SetStringField(TEXT("error"), Result.Error);
			AssetObject->SetNumberField(TEXT("variables"), 0);
			AssetObject->SetNumberField(TEXT("components"), 0);
			AssetObject->SetNumberField(TEXT("graphs"), 0);
			AssetObject->SetNumberField(TEXT("nodes"), 0);
			AssetObject->SetNumberField(TEXT("pins"), 0);
			AssetObject->SetNumberField(TEXT("links"), 0);
			AssetObject->SetNumberField(TEXT("symbols"), Result.bSuccess ? 1 : 0);
			AssetObject->SetNumberField(TEXT("references"), Result.ReferenceCount);
			AssetObject->SetNumberField(TEXT("registryTags"), Result.TagCount);
			AssetObject->SetStringField(TEXT("assetReader"), Result.ReaderName);
			AssetObject->SetStringField(TEXT("assetReaderStatus"), Result.ReaderStatus);
			AssetObject->SetStringField(TEXT("assetReaderError"), Result.ReaderError);
			Assets.Add(MakeShared<FJsonValueObject>(AssetObject));
		}

		RootObject->SetNumberField(TEXT("assetCount"), Results.Num());
		RootObject->SetNumberField(TEXT("successCount"), SuccessCount);
		RootObject->SetNumberField(TEXT("failureCount"), Results.Num() - SuccessCount);
		RootObject->SetNumberField(TEXT("readerSuccessCount"), ReaderSuccessCount);
		RootObject->SetNumberField(TEXT("readerFailureCount"), ReaderFailureCount);
		RootObject->SetArrayField(TEXT("assets"), Assets);
		return SaveJsonObject(
			RootObject,
			FPaths::Combine(OutputDirectory, TEXT("manifest.json")),
			bPrettyJson);
	}

	bool IsGeneratedPackage(const FAssetData& AssetData)
	{
		const FString PackageName = AssetData.PackageName.ToString();
		return PackageName.Contains(TEXT("/__ExternalActors__/"))
			|| PackageName.Contains(TEXT("/__ExternalObjects__/"));
	}
}

UAssetCatalogExportCommandlet::UAssetCatalogExportCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UAssetCatalogExportCommandlet::Main(const FString& Params)
{
	using namespace AssetCatalogExportPrivate;

	FString AssetPath;
	FString RootPath;
	FString OutputDirectory;
	FParse::Value(*Params, TEXT("Asset="), AssetPath);
	FParse::Value(*Params, TEXT("Root="), RootPath);
	FParse::Value(*Params, TEXT("Output="), OutputDirectory);
	const bool bIncludeBlueprints = FParse::Param(*Params, TEXT("IncludeBlueprints"));
	const bool bIncludeGenerated = FParse::Param(*Params, TEXT("IncludeGenerated"));
	const bool bIncludeTags = !FParse::Param(*Params, TEXT("NoTags"));
	const bool bPrettyJson = !FParse::Param(*Params, TEXT("CompactJson"));

	if (AssetPath.IsEmpty() && RootPath.IsEmpty())
	{
		UE_LOG(LogAssetCatalogExport, Error, TEXT("Specify -Asset=/Game/Path/Asset or -Root=/Game/Folder."));
		return 1;
	}
	if (!AssetPath.IsEmpty() && !RootPath.IsEmpty())
	{
		UE_LOG(LogAssetCatalogExport, Error, TEXT("Use either -Asset or -Root, not both."));
		return 1;
	}

	OutputDirectory = OutputDirectory.IsEmpty()
		? FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKitAssetCatalog"))
		: FPaths::ConvertRelativePathToFull(OutputDirectory);

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
	}
	else
	{
		RootPath.TrimStartAndEndInline();
		RootPath.TrimQuotesInline();
		if (!RootPath.StartsWith(TEXT("/")))
		{
			UE_LOG(LogAssetCatalogExport, Error, TEXT("Root must be a package path such as /Game/Folder."));
			return 1;
		}

		AssetRegistry.ScanPathsSynchronous({ RootPath }, true);
		FARFilter Filter;
		Filter.PackagePaths.Add(FName(*RootPath));
		Filter.bRecursivePaths = true;
		AssetRegistry.GetAssets(Filter, AssetDataList);
	}

	TSet<FTopLevelAssetPath> BlueprintClassPaths;
	TArray<FTopLevelAssetPath> BlueprintBaseClasses = { UBlueprint::StaticClass()->GetClassPathName() };
	AssetRegistry.GetDerivedClassNames(BlueprintBaseClasses, {}, BlueprintClassPaths);
	BlueprintClassPaths.Add(UBlueprint::StaticClass()->GetClassPathName());

	int32 SkippedBlueprintCount = 0;
	int32 SkippedGeneratedCount = 0;
	AssetDataList.RemoveAll([&](const FAssetData& AssetData)
	{
		if (!bIncludeBlueprints && BlueprintClassPaths.Contains(AssetData.AssetClassPath))
		{
			++SkippedBlueprintCount;
			return true;
		}
		if (!bIncludeGenerated && IsGeneratedPackage(AssetData))
		{
			++SkippedGeneratedCount;
			return true;
		}
		return false;
	});

	AssetDataList.Sort([](const FAssetData& Left, const FAssetData& Right)
	{
		return Left.GetSoftObjectPath().ToString() < Right.GetSoftObjectPath().ToString();
	});
	if (AssetDataList.IsEmpty())
	{
		UE_LOG(LogAssetCatalogExport, Error, TEXT("No matching assets found."));
		return 2;
	}

	UE_LOG(
		LogAssetCatalogExport,
		Display,
		TEXT("Exporting %d generic asset record(s) to %s."),
		AssetDataList.Num(),
		*OutputDirectory);

	TArray<FAssetCatalogExportResult> Results;
	Results.Reserve(AssetDataList.Num());
	int32 FailureCount = 0;
	for (const FAssetData& AssetData : AssetDataList)
	{
		FAssetCatalogExportResult Result;
		if (!ExportAsset(AssetRegistry, AssetData, OutputDirectory, bIncludeTags, bPrettyJson, Result))
		{
			++FailureCount;
			UE_LOG(LogAssetCatalogExport, Error, TEXT("Failed: %s - %s"), *Result.AssetPath, *Result.Error);
		}
		else
		{
			UE_LOG(
				LogAssetCatalogExport,
				Display,
				TEXT("Exported: %s (tags=%d references=%d)"),
				*Result.AssetPath,
				Result.TagCount,
				Result.ReferenceCount);
		}
		Results.Add(MoveTemp(Result));
	}

	if (!SaveManifest(
		OutputDirectory,
		RootPath,
		bIncludeBlueprints,
		bIncludeGenerated,
		bIncludeTags,
		SkippedBlueprintCount,
		SkippedGeneratedCount,
		Results,
		bPrettyJson))
	{
		UE_LOG(LogAssetCatalogExport, Error, TEXT("Failed to write manifest.json."));
		return 3;
	}

	UE_LOG(
		LogAssetCatalogExport,
		Display,
		TEXT("Asset catalog export finished. Success=%d Failure=%d SkippedBlueprints=%d SkippedGenerated=%d"),
		Results.Num() - FailureCount,
		FailureCount,
		SkippedBlueprintCount,
		SkippedGeneratedCount);
	return FailureCount == 0 ? 0 : 4;
}
