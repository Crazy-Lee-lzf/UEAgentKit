#include "AssetReaders/AssetReaderCommon.h"
#include "AssetReaders/AssetReaderImplementations.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Modules/ModuleManager.h"

namespace AssetReaderRegistryPrivate
{
	TSharedRef<FJsonObject> WorldActorToJson(
		const AActor* Actor,
		TMap<FString, int32>& InOutComponentClassCounts)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("objectPath"), Actor->GetPathName());
		Json->SetStringField(TEXT("name"), Actor->GetName());
#if WITH_EDITOR
		Json->SetStringField(TEXT("label"), Actor->GetActorLabel(false));
		Json->SetStringField(TEXT("guid"), Actor->GetActorGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
		Json->SetStringField(TEXT("instanceGuid"), Actor->GetActorInstanceGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
		Json->SetStringField(TEXT("folderPath"), Actor->GetFolderPath().ToString());
		Json->SetArrayField(TEXT("dataLayers"), NamesToJson(Actor->GetDataLayerInstanceNames()));
#else
		Json->SetStringField(TEXT("label"), Actor->GetName());
		Json->SetStringField(TEXT("guid"), FString());
		Json->SetStringField(TEXT("instanceGuid"), FString());
		Json->SetStringField(TEXT("folderPath"), FString());
		Json->SetArrayField(TEXT("dataLayers"), TArray<TSharedPtr<FJsonValue>>());
#endif
		Json->SetStringField(TEXT("classPath"), Actor->GetClass()->GetPathName());
		Json->SetObjectField(TEXT("transform"), TransformToJson(Actor->GetActorTransform()));
		Json->SetArrayField(TEXT("tags"), NamesToJson(Actor->Tags));
		Json->SetStringField(TEXT("runtimeGrid"), Actor->GetRuntimeGrid().ToString());
		Json->SetBoolField(TEXT("spatiallyLoaded"), Actor->GetIsSpatiallyLoaded());
		Json->SetBoolField(TEXT("editorOnly"), Actor->IsEditorOnly());
		Json->SetBoolField(TEXT("replicated"), Actor->GetIsReplicated());
		Json->SetBoolField(TEXT("hidden"), Actor->IsHidden());
		Json->SetBoolField(TEXT("collisionEnabled"), Actor->GetActorEnableCollision());
		Json->SetStringField(TEXT("attachParentPath"), ObjectPathOrEmpty(Actor->GetAttachParentActor()));

		const USceneComponent* RootComponent = Actor->GetRootComponent();
		Json->SetStringField(TEXT("rootComponentPath"), ObjectPathOrEmpty(RootComponent));
		Json->SetStringField(TEXT("rootComponentClassPath"), RootComponent != nullptr ? RootComponent->GetClass()->GetPathName() : FString());
		Json->SetStringField(TEXT("rootMobility"), RootComponent != nullptr ? EnumNameOrValue<EComponentMobility::Type>(RootComponent->GetMobility()) : FString());
		Json->SetNumberField(TEXT("rootMobilityValue"), RootComponent != nullptr ? static_cast<int32>(RootComponent->GetMobility()) : -1);

		TArray<UActorComponent*> Components;
		Actor->GetComponents(Components);
		TMap<FString, int32> ActorComponentClassCounts;
		int32 ValidComponentCount = 0;
		for (const UActorComponent* Component : Components)
		{
			if (Component == nullptr)
			{
				continue;
			}
			++ValidComponentCount;
			const FString ClassPath = Component->GetClass()->GetPathName();
			++ActorComponentClassCounts.FindOrAdd(ClassPath);
			++InOutComponentClassCounts.FindOrAdd(ClassPath);
		}
		Json->SetNumberField(TEXT("componentCount"), ValidComponentCount);
		Json->SetArrayField(TEXT("componentClasses"), StringCountsToJson(ActorComponentClassCounts));
		return Json;
	}

	TSharedRef<FJsonObject> WorldPartitionActorDescToJson(const FWorldPartitionActorDescInstance* ActorDesc)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("guid"), ActorDesc->GetGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
		Json->SetStringField(TEXT("actorPath"), ActorDesc->GetActorSoftPath().ToString());
		Json->SetStringField(TEXT("actorPackage"), ActorDesc->GetActorPackage().ToString());
		Json->SetStringField(TEXT("name"), ActorDesc->GetActorName().ToString());
		Json->SetStringField(TEXT("label"), ActorDesc->GetActorLabel().ToString());
		const FString NativeClassPath = ActorDesc->GetNativeClass().ToString();
		FString BaseClassPath = ActorDesc->GetBaseClass().ToString();
		if (BaseClassPath.IsEmpty())
		{
			// Native actor descriptors do not always carry a resolved base class
			// asset path.  Fall back to the native class instead of returning an
			// unusable empty class for filtering and diagnostics.
			BaseClassPath = NativeClassPath;
		}
		Json->SetStringField(TEXT("baseClassPath"), BaseClassPath);
		Json->SetStringField(TEXT("nativeClassPath"), NativeClassPath);
		Json->SetStringField(TEXT("folderPath"), ActorDesc->GetFolderPath().ToString());
		Json->SetObjectField(TEXT("transform"), TransformToJson(ActorDesc->GetActorTransform()));
		Json->SetObjectField(TEXT("editorBounds"), BoxToJson(ActorDesc->GetEditorBounds()));
		Json->SetObjectField(TEXT("runtimeBounds"), BoxToJson(ActorDesc->GetRuntimeBounds()));
		Json->SetStringField(TEXT("runtimeGrid"), ActorDesc->GetRuntimeGrid().ToString());
		Json->SetBoolField(TEXT("spatiallyLoaded"), ActorDesc->GetIsSpatiallyLoaded());
		Json->SetBoolField(TEXT("editorOnly"), ActorDesc->GetActorIsEditorOnly());
		Json->SetBoolField(TEXT("runtimeOnly"), ActorDesc->GetActorIsRuntimeOnly());
		Json->SetBoolField(TEXT("runtimeRelevant"), ActorDesc->IsRuntimeRelevant());
		Json->SetBoolField(TEXT("editorRelevant"), ActorDesc->IsEditorRelevant());
		Json->SetBoolField(TEXT("loaded"), ActorDesc->IsLoaded(false));
		Json->SetArrayField(TEXT("tags"), NamesToJson(ActorDesc->GetTags()));
		const bool bDataLayersResolved = ActorDesc->HasResolvedDataLayerInstanceNames();
		Json->SetBoolField(TEXT("dataLayersResolved"), bDataLayersResolved);
		TArray<FName> DataLayers;
		if (bDataLayersResolved)
		{
			DataLayers = ActorDesc->GetDataLayerInstanceNames().ToArray();
		}
		else if (const FWorldPartitionActorDesc* RawActorDesc = ActorDesc->GetActorDesc())
		{
			// Resolved names need a UDataLayerManager.  When one is not available,
			// expose the descriptor's own data-layer references instead of
			// silently dropping them.
			DataLayers = RawActorDesc->GetDataLayers();
		}
		Json->SetArrayField(TEXT("dataLayers"), NamesToJson(DataLayers));
		Json->SetStringField(TEXT("dataLayersSource"), bDataLayersResolved ? TEXT("resolved") : TEXT("descriptor"));
		return Json;
	}

#if WITH_EDITOR
	/**
	 * A commandlet can load a UWorld as a plain asset, leaving its World
	 * Partition uninitialized (WorldType == Inactive).  In that one controlled
	 * context, initialize the partition just long enough to read the official
	 * actor-descriptor container, then uninitialize it and restore the package
	 * dirty flag.  Normal editor/game worlds and already-initialized partitions
	 * are never initialized here.
	 */
	class FScopedWorldPartitionDescriptorInitialization
	{
	public:
		bool Initialize(UWorld* World, UWorldPartition* WorldPartition)
		{
			if (World == nullptr || WorldPartition == nullptr || WorldPartition->IsInitialized())
			{
				return false;
			}
			if (!IsRunningCommandlet() || World->WorldType != EWorldType::Inactive)
			{
				return false;
			}
			if (!WorldPartition->CanInitialize(World))
			{
				return false;
			}

			WorldPackage = World->GetPackage();
			bPackageWasDirty = WorldPackage != nullptr && WorldPackage->IsDirty();
			WorldPartition->Initialize(World, FTransform::Identity);
			if (!WorldPartition->IsInitialized())
			{
				// Initialization can dirty the package before failing.  Restore
				// the pre-initialization flag even on this failure path.
				if (WorldPackage != nullptr && !bPackageWasDirty)
				{
					WorldPackage->SetDirtyFlag(false);
				}
				return false;
			}

			WorldPartitionToUninitialize = WorldPartition;
			return true;
		}

		~FScopedWorldPartitionDescriptorInitialization()
		{
			if (WorldPartitionToUninitialize != nullptr && WorldPartitionToUninitialize->IsInitialized())
			{
				WorldPartitionToUninitialize->Uninitialize();
				if (WorldPackage != nullptr && !bPackageWasDirty)
				{
					WorldPackage->SetDirtyFlag(false);
				}
			}
		}

	private:
		UWorldPartition* WorldPartitionToUninitialize = nullptr;
		UPackage* WorldPackage = nullptr;
		bool bPackageWasDirty = false;
	};
#endif

	EAssetReaderStatus ReadWorld(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UWorld* World = Cast<UWorld>(AssetData.GetAsset());
		if (World == nullptr)
		{
			OutError = TEXT("Failed to load World asset.");
			return EAssetReaderStatus::Failed;
		}

		constexpr int32 MaxExportedActors = 20000;
		constexpr int32 MaxExportedActorDescs = 20000;
		ULevel* PersistentLevel = World->PersistentLevel;
		OutDetails->SetStringField(TEXT("type"), TEXT("world"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("worldType"), LexToString(World->WorldType));
		OutDetails->SetStringField(TEXT("persistentLevelPath"), ObjectPathOrEmpty(PersistentLevel));
		OutDetails->SetStringField(TEXT("persistentLevelPackage"), PersistentLevel != nullptr ? PersistentLevel->GetPackage()->GetName() : FString());
		OutDetails->SetBoolField(TEXT("usingExternalActors"), PersistentLevel != nullptr && PersistentLevel->IsUsingExternalActors());
		TArray<AActor*> LoadedActors;
		if (PersistentLevel != nullptr)
		{
			for (const TObjectPtr<AActor>& Actor : PersistentLevel->Actors)
			{
				if (Actor != nullptr)
				{
					LoadedActors.Add(Actor.Get());
				}
			}
		}
		LoadedActors.Sort([](const AActor& Left, const AActor& Right)
		{
			return Left.GetPathName() < Right.GetPathName();
		});

		TMap<FString, int32> ActorClassCounts;
		TMap<FString, int32> ComponentClassCounts;
		int32 TotalComponentCount = 0;
		for (const AActor* Actor : LoadedActors)
		{
			++ActorClassCounts.FindOrAdd(Actor->GetClass()->GetPathName());
			for (const UActorComponent* Component : Actor->GetComponents())
			{
				if (Component != nullptr)
				{
					++TotalComponentCount;
				}
			}
		}
		TArray<TSharedPtr<FJsonValue>> Actors;
		const int32 ExportedActorCount = FMath::Min(LoadedActors.Num(), MaxExportedActors);
		Actors.Reserve(ExportedActorCount);
		for (int32 ActorIndex = 0; ActorIndex < ExportedActorCount; ++ActorIndex)
		{
			Actors.Add(MakeShared<FJsonValueObject>(WorldActorToJson(LoadedActors[ActorIndex], ComponentClassCounts)));
		}
		if (ExportedActorCount < LoadedActors.Num())
		{
			ComponentClassCounts.Reset();
			for (const AActor* Actor : LoadedActors)
			{
				for (const UActorComponent* Component : Actor->GetComponents())
				{
					if (Component != nullptr)
					{
						++ComponentClassCounts.FindOrAdd(Component->GetClass()->GetPathName());
					}
				}
			}
		}
		OutDetails->SetNumberField(TEXT("loadedActorCount"), LoadedActors.Num());
		OutDetails->SetNumberField(TEXT("exportedActorCount"), ExportedActorCount);
		OutDetails->SetBoolField(TEXT("actorListTruncated"), ExportedActorCount < LoadedActors.Num());
		OutDetails->SetNumberField(TEXT("componentCount"), TotalComponentCount);
		OutDetails->SetArrayField(TEXT("actorClassCounts"), StringCountsToJson(ActorClassCounts));
		OutDetails->SetArrayField(TEXT("componentClassCounts"), StringCountsToJson(ComponentClassCounts));
		OutDetails->SetArrayField(TEXT("actors"), Actors);

		TArray<ULevelStreaming*> StreamingLevels = World->GetStreamingLevels();
		StreamingLevels.Sort([](const ULevelStreaming& Left, const ULevelStreaming& Right)
		{
			const FString LeftKey = Left.GetWorldAssetPackageName() + TEXT("|") + Left.GetPathName();
			const FString RightKey = Right.GetWorldAssetPackageName() + TEXT("|") + Right.GetPathName();
			return LeftKey < RightKey;
		});
		TArray<TSharedPtr<FJsonValue>> StreamingLevelValues;
		for (const ULevelStreaming* StreamingLevel : StreamingLevels)
		{
			if (StreamingLevel == nullptr)
			{
				continue;
			}
			TSharedRef<FJsonObject> StreamingJson = MakeShared<FJsonObject>();
			StreamingJson->SetStringField(TEXT("objectPath"), StreamingLevel->GetPathName());
			StreamingJson->SetStringField(TEXT("classPath"), StreamingLevel->GetClass()->GetPathName());
			StreamingJson->SetStringField(TEXT("worldAssetPackage"), StreamingLevel->GetWorldAssetPackageName());
			StreamingJson->SetObjectField(TEXT("levelTransform"), TransformToJson(StreamingLevel->LevelTransform));
			StreamingJson->SetBoolField(TEXT("shouldBeLoaded"), StreamingLevel->ShouldBeLoaded());
			StreamingJson->SetBoolField(TEXT("shouldBeVisible"), StreamingLevel->GetShouldBeVisibleFlag());
#if WITH_EDITOR
			StreamingJson->SetBoolField(TEXT("visibleInEditor"), StreamingLevel->GetShouldBeVisibleInEditor());
#else
			StreamingJson->SetBoolField(TEXT("visibleInEditor"), false);
#endif
			StreamingJson->SetBoolField(TEXT("alwaysLoaded"), StreamingLevel->ShouldBeAlwaysLoaded());
			StreamingJson->SetBoolField(TEXT("loaded"), StreamingLevel->IsLevelLoaded());
			StreamingJson->SetBoolField(TEXT("visible"), StreamingLevel->IsLevelVisible());
			StreamingJson->SetStringField(TEXT("loadedLevelPath"), ObjectPathOrEmpty(StreamingLevel->GetLoadedLevel()));
			StreamingJson->SetNumberField(TEXT("lodIndex"), StreamingLevel->GetLevelLODIndex());
			StreamingLevelValues.Add(MakeShared<FJsonValueObject>(StreamingJson));
		}
		OutDetails->SetNumberField(TEXT("streamingLevelCount"), StreamingLevelValues.Num());
		OutDetails->SetArrayField(TEXT("streamingLevels"), StreamingLevelValues);

		AWorldSettings* WorldSettings = World->GetWorldSettings(false, false);
		TSharedRef<FJsonObject> WorldSettingsJson = MakeShared<FJsonObject>();
		WorldSettingsJson->SetBoolField(TEXT("available"), WorldSettings != nullptr);
		WorldSettingsJson->SetStringField(TEXT("objectPath"), ObjectPathOrEmpty(WorldSettings));
		WorldSettingsJson->SetStringField(TEXT("classPath"), WorldSettings != nullptr ? WorldSettings->GetClass()->GetPathName() : FString());
		WorldSettingsJson->SetStringField(TEXT("defaultGameModeClassPath"), WorldSettings != nullptr ? ObjectPathOrEmpty(WorldSettings->DefaultGameMode.Get()) : FString());
		WorldSettingsJson->SetNumberField(TEXT("killZ"), WorldSettings != nullptr ? WorldSettings->KillZ : 0.0f);
		WorldSettingsJson->SetStringField(TEXT("killZDamageTypeClassPath"), WorldSettings != nullptr ? ObjectPathOrEmpty(WorldSettings->KillZDamageType.Get()) : FString());
		WorldSettingsJson->SetNumberField(TEXT("gravityZ"), WorldSettings != nullptr ? WorldSettings->GetGravityZ() : 0.0f);
		WorldSettingsJson->SetNumberField(TEXT("worldToMeters"), WorldSettings != nullptr ? WorldSettings->WorldToMeters : 0.0f);
		WorldSettingsJson->SetBoolField(TEXT("worldBoundsChecks"), WorldSettings != nullptr && WorldSettings->AreWorldBoundsChecksEnabled());
		WorldSettingsJson->SetBoolField(TEXT("navigationSystemEnabled"), WorldSettings != nullptr && WorldSettings->IsNavigationSystemEnabled());
		WorldSettingsJson->SetBoolField(TEXT("aiSystemEnabled"), WorldSettings != nullptr && WorldSettings->IsAISystemEnabled());
		OutDetails->SetObjectField(TEXT("worldSettings"), WorldSettingsJson);

		UWorldPartition* WorldPartition = World->GetWorldPartition();
		TSharedRef<FJsonObject> WorldPartitionJson = MakeShared<FJsonObject>();
		WorldPartitionJson->SetBoolField(TEXT("available"), WorldPartition != nullptr);
		WorldPartitionJson->SetStringField(TEXT("objectPath"), ObjectPathOrEmpty(WorldPartition));
		WorldPartitionJson->SetStringField(TEXT("classPath"), WorldPartition != nullptr ? WorldPartition->GetClass()->GetPathName() : FString());
		WorldPartitionJson->SetBoolField(TEXT("supportsStreaming"), WorldPartition != nullptr && WorldPartition->SupportsStreaming());
		WorldPartitionJson->SetBoolField(TEXT("streamingEnabled"), WorldPartition != nullptr && WorldPartition->IsStreamingEnabled());
		WorldPartitionJson->SetBoolField(TEXT("canStream"), WorldPartition != nullptr && WorldPartition->CanStream());
		UObject* RuntimeHash = GetReflectedObject(WorldPartition, TEXT("RuntimeHash"));
		WorldPartitionJson->SetStringField(TEXT("runtimeHashClassPath"), RuntimeHash != nullptr ? RuntimeHash->GetClass()->GetPathName() : FString());
		WorldPartitionJson->SetStringField(TEXT("worldDataLayersPath"), ObjectPathOrEmpty(World->GetWorldDataLayers()));

		TArray<const FWorldPartitionActorDescInstance*> ActorDescs;
		TMap<FString, int32> ActorDescClassCounts;
		TArray<TSharedPtr<FJsonValue>> ActorDescValues;
		bool bActorDescMetadataAvailable = false;
		FString ActorDescInitialization = TEXT("not-performed");
		const bool bWorldPackageDirtyBefore = World->GetPackage() != nullptr && World->GetPackage()->IsDirty();
		const UActorDescContainerInstance* ActorDescContainer = nullptr;
		FString ExternalActorPathForCoverage;
		TArray<FString> UnmatchedExternalActorPackages;
		int32 ExternalActorPackageCount = 0;
		int32 ExternalActorDescriptorPackageCount = 0;
		bool bUnmatchedExternalActorPackagesTruncated = false;
		FString ExternalActorPackageCoverage = TEXT("not-applicable");
		constexpr int32 MaxLoggedUnmatchedExternalActorPackages = 64;
#if WITH_EDITOR
		if (WorldPartition != nullptr)
		{
			FScopedWorldPartitionDescriptorInitialization InitializationScope;
			const bool bTemporaryInitialization = InitializationScope.Initialize(World, WorldPartition);
			ActorDescContainer = WorldPartition->GetActorDescContainerInstance();
			bActorDescMetadataAvailable = ActorDescContainer != nullptr;
			if (bActorDescMetadataAvailable)
			{
				ExternalActorPathForCoverage = ActorDescContainer->GetExternalActorPath();
				ActorDescInitialization = bTemporaryInitialization ? TEXT("commandlet-temporary") : TEXT("reused");
				FWorldPartitionHelpers::ForEachActorDescInstance<AActor>(WorldPartition, [&ActorDescs, &ActorDescClassCounts](const FWorldPartitionActorDescInstance* ActorDesc)
				{
					if (ActorDesc != nullptr)
					{
						ActorDescs.Add(ActorDesc);
						FString ClassPath = ActorDesc->GetBaseClass().ToString();
						if (ClassPath.IsEmpty())
						{
							ClassPath = ActorDesc->GetNativeClass().ToString();
						}
						++ActorDescClassCounts.FindOrAdd(ClassPath);
					}
					return true;
				});
			}
			else
			{
				ActorDescInitialization = bTemporaryInitialization ? TEXT("commandlet-temporary") : TEXT("not-performed");
			}

			ActorDescs.Sort([](const FWorldPartitionActorDescInstance& Left, const FWorldPartitionActorDescInstance& Right)
			{
				const FString LeftKey = Left.GetActorSoftPath().ToString() + TEXT("|") + Left.GetGuid().ToString();
				const FString RightKey = Right.GetActorSoftPath().ToString() + TEXT("|") + Right.GetGuid().ToString();
				return LeftKey < RightKey;
			});
			const int32 ExportedActorDescCount = FMath::Min(ActorDescs.Num(), MaxExportedActorDescs);
			ActorDescValues.Reserve(ExportedActorDescCount);
			for (int32 ActorDescIndex = 0; ActorDescIndex < ExportedActorDescCount; ++ActorDescIndex)
			{
				ActorDescValues.Add(MakeShared<FJsonValueObject>(WorldPartitionActorDescToJson(ActorDescs[ActorDescIndex])));
			}

#if WITH_EDITOR
			// A descriptor list is not a proof that every external actor package
			// was represented.  Use the native AssetRegistry inventory for the
			// exact World Partition external-actor folder as a diagnostic;
			// unmatched packages can be unresolved classes or genuinely stale
			// packages, so they are recorded for fail-closed coverage rather
			// than synthesized.  This runs before the scoped initialization is
			// released, because ActorDescs is owned by the container.
			if (PersistentLevel != nullptr && PersistentLevel->IsUsingExternalActors()
				&& !ExternalActorPathForCoverage.IsEmpty())
			{
				ExternalActorPackageCoverage = TEXT("unknown");
				const FString& ExternalActorPath = ExternalActorPathForCoverage;
				if (!ExternalActorPath.IsEmpty())
				{
					if (FAssetRegistryModule* AssetRegistryModule = FModuleManager::GetModulePtr<FAssetRegistryModule>(TEXT("AssetRegistry")))
					{
						IAssetRegistry& AssetRegistry = AssetRegistryModule->Get();
						TArray<FAssetData> ExternalAssets;
						const bool bInventoryAvailable = AssetRegistry.GetAssetsByPath(
							FName(*ExternalActorPath), ExternalAssets, true, true);
						if (bInventoryAvailable)
						{
							FString ExternalActorPrefix = ExternalActorPath;
							if (!ExternalActorPrefix.EndsWith(TEXT("/")))
							{
								ExternalActorPrefix += TEXT("/");
							}

							TSet<FName> ExternalPackages;
							for (const FAssetData& ExternalAsset : ExternalAssets)
							{
								ExternalPackages.Add(ExternalAsset.PackageName);
							}
							TSet<FName> DescriptorExternalPackages;
							for (const FWorldPartitionActorDescInstance* ActorDesc : ActorDescs)
							{
								if (ActorDesc != nullptr
									&& ActorDesc->GetActorPackage().ToString().StartsWith(ExternalActorPrefix))
								{
									DescriptorExternalPackages.Add(ActorDesc->GetActorPackage());
								}
							}

							ExternalActorPackageCount = ExternalPackages.Num();
							ExternalActorDescriptorPackageCount = DescriptorExternalPackages.Num();
							for (const FName& ExternalPackage : ExternalPackages)
							{
								if (!DescriptorExternalPackages.Contains(ExternalPackage))
								{
									UnmatchedExternalActorPackages.Add(ExternalPackage.ToString());
								}
							}
							UnmatchedExternalActorPackages.Sort();
							bUnmatchedExternalActorPackagesTruncated =
								UnmatchedExternalActorPackages.Num() > MaxLoggedUnmatchedExternalActorPackages;
							ExternalActorPackageCoverage =
								UnmatchedExternalActorPackages.Num() == 0 ? TEXT("complete") : TEXT("unmatched");
							if (ExternalActorPackageCount == 0 && ExternalActorDescriptorPackageCount > 0)
							{
								// An empty inventory cannot prove that external
								// descriptors are complete; keep the fail-closed
								// unknown state.
								ExternalActorPackageCoverage = TEXT("unknown");
							}
						}
					}
				}
			}
#endif
		}
		else
		{
			ActorDescInitialization = TEXT("not-performed");
		}
#endif
		WorldPartitionJson->SetBoolField(TEXT("actorDescMetadataAvailable"), bActorDescMetadataAvailable);
		WorldPartitionJson->SetStringField(TEXT("actorDescInitialization"), ActorDescInitialization);
		WorldPartitionJson->SetBoolField(TEXT("worldPackageDirtyBefore"), bWorldPackageDirtyBefore);
		WorldPartitionJson->SetBoolField(TEXT("worldPackageDirtyAfter"), World->GetPackage() != nullptr && World->GetPackage()->IsDirty());
		WorldPartitionJson->SetNumberField(TEXT("actorDescCount"), ActorDescs.Num());
		WorldPartitionJson->SetNumberField(TEXT("exportedActorDescCount"), ActorDescValues.Num());
		WorldPartitionJson->SetBoolField(TEXT("actorDescListTruncated"), ActorDescValues.Num() < ActorDescs.Num());
		WorldPartitionJson->SetNumberField(TEXT("externalActorPackageCount"), ExternalActorPackageCount);
		WorldPartitionJson->SetNumberField(TEXT("externalActorDescriptorPackageCount"), ExternalActorDescriptorPackageCount);
		WorldPartitionJson->SetNumberField(TEXT("unmatchedExternalActorPackageCount"), UnmatchedExternalActorPackages.Num());
		WorldPartitionJson->SetBoolField(TEXT("unmatchedExternalActorPackagesTruncated"), bUnmatchedExternalActorPackagesTruncated);
		TArray<TSharedPtr<FJsonValue>> UnmatchedExternalActorPackageValues;
		const int32 ExportedUnmatchedExternalActorPackageCount = FMath::Min(
			UnmatchedExternalActorPackages.Num(), MaxLoggedUnmatchedExternalActorPackages);
		UnmatchedExternalActorPackageValues.Reserve(ExportedUnmatchedExternalActorPackageCount);
		for (int32 ExternalPackageIndex = 0; ExternalPackageIndex < ExportedUnmatchedExternalActorPackageCount; ++ExternalPackageIndex)
		{
			UnmatchedExternalActorPackageValues.Add(
				MakeShared<FJsonValueString>(UnmatchedExternalActorPackages[ExternalPackageIndex]));
		}
		WorldPartitionJson->SetArrayField(TEXT("unmatchedExternalActorPackages"), UnmatchedExternalActorPackageValues);
		WorldPartitionJson->SetStringField(TEXT("externalActorPackageCoverage"), ExternalActorPackageCoverage);
		WorldPartitionJson->SetArrayField(TEXT("actorDescClassCounts"), StringCountsToJson(ActorDescClassCounts));
		WorldPartitionJson->SetArrayField(TEXT("actorDescs"), ActorDescValues);
		OutDetails->SetObjectField(TEXT("worldPartition"), WorldPartitionJson);
		return EAssetReaderStatus::Success;
	}
}
