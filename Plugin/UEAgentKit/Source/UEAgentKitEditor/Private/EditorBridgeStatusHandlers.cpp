#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "EditorBridgeLogCapture.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Selection.h"
#include "Engine/World.h"
#include "Modules/ModuleManager.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"
#include "UObject/UObjectIterator.h"

using namespace UEAgentKitEditorBridgePrivate;

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildStatusResult() const
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("state"), TEXT("available"));
	Result->SetStringField(TEXT("pluginVersion"), PluginVersion);
	Result->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Result->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
	Result->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Result->SetStringField(TEXT("sessionId"), SessionId);
	Result->SetArrayField(TEXT("capabilities"), BuildCapabilityValues());
	Result->SetStringField(TEXT("pieState"), UEAgentKitEditorBridgePrivate::GetPieStateName());
	UWorld* World = UEAgentKitEditorBridgePrivate::GetEditorWorld();
	Result->SetStringField(TEXT("currentLevel"), World != nullptr && World->GetCurrentLevel() != nullptr ? World->GetCurrentLevel()->GetPathName() : FString());
	Result->SetNumberField(TEXT("dirtyPackageCount"), UEAgentKitEditorBridgePrivate::CountDirtyGamePackages());
	Result->SetNumberField(TEXT("currentPieSessionId"), LogCapture.IsValid() ? LogCapture->GetCurrentPieSessionId() : 0);
	Result->SetStringField(TEXT("capturedPieState"), LogCapture.IsValid() ? LogCapture->GetCurrentPieState() : TEXT("unavailable"));
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildSelectionResult() const
{
	TArray<TSharedPtr<FJsonValue>> Items;
	TSet<FString> SeenPaths;
	auto AddSelection = [&Items, &SeenPaths](UObject* Object, const FString& Kind)
	{
		if (Object == nullptr || Items.Num() >= UEAgentKitEditorBridgePrivate::MaxSelectionItems)
		{
			return;
		}
		const FString ObjectPath = Object->GetPathName();
		if (SeenPaths.Contains(ObjectPath))
		{
			return;
		}
		SeenPaths.Add(ObjectPath);
		Items.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeObject(Object, Kind)));
	};

	if (GEditor != nullptr)
	{
		if (USelection* SelectedActors = GEditor->GetSelectedActors())
		{
			for (FSelectionIterator It(*SelectedActors); It; ++It)
			{
				AddSelection(*It, TEXT("Actor"));
			}
		}
		if (USelection* SelectedComponents = GEditor->GetSelectedComponents())
		{
			for (FSelectionIterator It(*SelectedComponents); It; ++It)
			{
				AddSelection(*It, TEXT("Component"));
			}
		}
		if (USelection* SelectedObjects = GEditor->GetSelectedObjects())
		{
			for (FSelectionIterator It(*SelectedObjects); It; ++It)
			{
				UObject* Object = *It;
				AddSelection(Object, Object != nullptr && Object->IsAsset() ? TEXT("Asset") : TEXT("Object"));
			}
		}
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("count"), Items.Num());
	Result->SetBoolField(TEXT("truncated"), Items.Num() >= UEAgentKitEditorBridgePrivate::MaxSelectionItems);
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildOpenAssetsResult() const
{
	TArray<UObject*> EditedAssets;
	if (GEditor != nullptr)
	{
		if (UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>())
		{
			EditedAssets = AssetEditorSubsystem->GetAllEditedAssets();
		}
	}
	EditedAssets.Sort([](const UObject& Left, const UObject& Right)
	{
		return Left.GetPathName() < Right.GetPathName();
	});

	TArray<TSharedPtr<FJsonValue>> Items;
	const int32 TotalCount = EditedAssets.Num();
	for (UObject* Asset : EditedAssets)
	{
		if (Asset == nullptr || Items.Num() >= UEAgentKitEditorBridgePrivate::MaxOpenAssets)
		{
			continue;
		}
		Items.Add(MakeShared<FJsonValueObject>(UEAgentKitEditorBridgePrivate::DescribeObject(Asset, TEXT("Asset"))));
	}
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("count"), TotalCount);
	Result->SetBoolField(TEXT("truncated"), TotalCount > Items.Num());
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildDirtyAssetsResult() const
{
	TArray<UPackage*> DirtyPackages;
	for (TObjectIterator<UPackage> It; It; ++It)
	{
		const FString PackageName = It->GetName();
		if (It->IsDirty() && PackageName.StartsWith(TEXT("/Game/")))
		{
			DirtyPackages.Add(*It);
		}
	}
	DirtyPackages.Sort([](const UPackage& Left, const UPackage& Right)
	{
		return Left.GetName() < Right.GetName();
	});

	FAssetRegistryModule& AssetRegistryModule = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	TArray<TSharedPtr<FJsonValue>> Items;
	const int32 TotalCount = DirtyPackages.Num();
	for (UPackage* Package : DirtyPackages)
	{
		if (Package == nullptr || Items.Num() >= UEAgentKitEditorBridgePrivate::MaxDirtyPackages)
		{
			continue;
		}
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		const FString PackageName = Package->GetName();
		Item->SetStringField(TEXT("packageName"), PackageName);
		TArray<FAssetData> Assets;
		AssetRegistryModule.Get().GetAssetsByPackageName(FName(*PackageName), Assets);
		Assets.Sort([](const FAssetData& Left, const FAssetData& Right)
		{
			return Left.GetObjectPathString() < Right.GetObjectPathString();
		});
		TArray<TSharedPtr<FJsonValue>> AssetPaths;
		for (const FAssetData& Asset : Assets)
		{
			AssetPaths.Add(MakeShared<FJsonValueString>(Asset.GetObjectPathString()));
		}
		Item->SetArrayField(TEXT("assetPaths"), AssetPaths);
		Items.Add(MakeShared<FJsonValueObject>(Item));
	}
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("count"), TotalCount);
	Result->SetBoolField(TEXT("truncated"), TotalCount > Items.Num());
	Result->SetArrayField(TEXT("items"), Items);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildCurrentLevelResult() const
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	UWorld* World = UEAgentKitEditorBridgePrivate::GetEditorWorld();
	Result->SetBoolField(TEXT("available"), World != nullptr);
	if (World == nullptr)
	{
		return Result;
	}
	Result->SetStringField(TEXT("worldPath"), World->GetPathName());
	Result->SetStringField(TEXT("worldType"), UEAgentKitEditorBridgePrivate::GetWorldTypeName(World->WorldType));
	Result->SetStringField(TEXT("persistentLevelPath"), World->PersistentLevel != nullptr ? World->PersistentLevel->GetPathName() : FString());
	Result->SetStringField(TEXT("currentLevelPath"), World->GetCurrentLevel() != nullptr ? World->GetCurrentLevel()->GetPathName() : FString());
	Result->SetBoolField(TEXT("packageDirty"), World->GetOutermost() != nullptr && World->GetOutermost()->IsDirty());
	Result->SetBoolField(TEXT("worldPartitioned"), World->GetWorldPartition() != nullptr);
	return Result;
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildPieStateResult() const
{
	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	const FString State = UEAgentKitEditorBridgePrivate::GetPieStateName();
	Result->SetStringField(TEXT("state"), State);
	Result->SetBoolField(TEXT("playing"), State == TEXT("playing"));
	Result->SetBoolField(TEXT("simulating"), State == TEXT("simulating"));
	UWorld* PlayWorld = GEditor != nullptr ? GEditor->PlayWorld : nullptr;
	Result->SetStringField(TEXT("worldPath"), PlayWorld != nullptr ? PlayWorld->GetPathName() : FString());
	Result->SetStringField(TEXT("worldType"), PlayWorld != nullptr ? UEAgentKitEditorBridgePrivate::GetWorldTypeName(PlayWorld->WorldType) : FString());
	Result->SetNumberField(TEXT("netMode"), PlayWorld != nullptr ? static_cast<int32>(PlayWorld->GetNetMode()) : -1);
	return Result;
}
