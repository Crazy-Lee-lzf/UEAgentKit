#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "Modules/ModuleManager.h"
#include "Subsystems/AssetEditorSubsystem.h"
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
			OutErrorMessage = TEXT("Editor navigation actions are unavailable while PIE or SIE is active.");
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

	TSharedRef<FJsonObject> MakeActionBase(
		const FString& Action,
		const FString& SessionId,
		const FString& PieState)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("action"), Action);
		Result->SetStringField(TEXT("editorSessionId"), SessionId);
		Result->SetStringField(TEXT("pieState"), PieState);
		Result->SetBoolField(TEXT("saved"), false);
		return Result;
	}
}

bool FUEAgentKitEditorBridge::TryOpenAssetResult(
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
	UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
	if (AssetEditorSubsystem == nullptr)
	{
		OutErrorCode = TEXT("live-editor-asset-editor-unavailable");
		OutErrorMessage = TEXT("The Asset Editor Subsystem is unavailable.");
		return false;
	}

	UObject* ExistingObject = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	const bool bLoadedBefore = ExistingObject != nullptr && ExistingObject->IsAsset();
	const bool bOpenBefore = bLoadedBefore && IsAssetOpenInEditor(ExistingObject);
	UObject* Asset = AssetData.GetAsset();
	if (Asset == nullptr || !Asset->IsAsset())
	{
		OutErrorCode = TEXT("live-editor-asset-load-failed");
		OutErrorMessage = TEXT("The registered asset could not be loaded by Unreal Editor.");
		return false;
	}
	UPackage* Package = Asset->GetOutermost();
	const bool bDirtyBefore = Package != nullptr && Package->IsDirty();
	const bool bOpenRequested = AssetEditorSubsystem->OpenEditorForAsset(Asset);
	const bool bOpenAfter = IsAssetOpenInEditor(Asset);
	if (!bOpenRequested || !bOpenAfter)
	{
		OutErrorCode = TEXT("live-editor-asset-editor-unavailable");
		OutErrorMessage = TEXT("Unreal Editor did not open a registered editor for the asset.");
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeActionBase(TEXT("open-asset"), SessionId, GetPieStateName());
	Result->SetStringField(TEXT("assetPath"), AssetData.GetObjectPathString());
	Result->SetStringField(TEXT("classPath"), AssetData.AssetClassPath.ToString());
	Result->SetBoolField(TEXT("loadedBefore"), bLoadedBefore);
	Result->SetBoolField(TEXT("loadedAfter"), true);
	Result->SetBoolField(TEXT("openBefore"), bOpenBefore);
	Result->SetBoolField(TEXT("openAfter"), bOpenAfter);
	Result->SetBoolField(TEXT("openedNewEditor"), !bOpenBefore);
	Result->SetBoolField(TEXT("packageDirtyBefore"), bDirtyBefore);
	Result->SetBoolField(TEXT("packageDirtyAfter"), Package != nullptr && Package->IsDirty());
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryFocusAssetResult(
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
	UObject* Asset = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	if (Asset == nullptr || !Asset->IsAsset() || !IsAssetOpenInEditor(Asset))
	{
		OutErrorCode = TEXT("live-editor-asset-not-open");
		OutErrorMessage = TEXT("The exact asset is not currently open in an asset editor; focus does not load assets.");
		return false;
	}
	UAssetEditorSubsystem* AssetEditorSubsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
	if (AssetEditorSubsystem == nullptr || AssetEditorSubsystem->FindEditorForAsset(Asset, true) == nullptr)
	{
		OutErrorCode = TEXT("live-editor-asset-editor-unavailable");
		OutErrorMessage = TEXT("The open asset editor could not be focused.");
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeActionBase(TEXT("focus-asset"), SessionId, GetPieStateName());
	Result->SetStringField(TEXT("assetPath"), AssetData.GetObjectPathString());
	Result->SetStringField(TEXT("classPath"), AssetData.AssetClassPath.ToString());
	Result->SetBoolField(TEXT("loadedByBridge"), false);
	Result->SetBoolField(TEXT("open"), true);
	Result->SetBoolField(TEXT("focused"), true);
	Result->SetBoolField(TEXT("packageDirty"), Asset->GetOutermost() != nullptr && Asset->GetOutermost()->IsDirty());
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TrySyncContentBrowserResult(
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
	TArray<FAssetData> AssetsToSync;
	AssetsToSync.Add(AssetData);
	GEditor->SyncBrowserToObjects(AssetsToSync, true);
	const bool bLoadedAfter = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false) != nullptr;

	TSharedRef<FJsonObject> Result = MakeActionBase(TEXT("sync-content-browser"), SessionId, GetPieStateName());
	Result->SetStringField(TEXT("assetPath"), AssetData.GetObjectPathString());
	Result->SetStringField(TEXT("classPath"), AssetData.AssetClassPath.ToString());
	Result->SetBoolField(TEXT("synchronized"), true);
	Result->SetBoolField(TEXT("contentBrowserFocused"), true);
	Result->SetBoolField(TEXT("loadedBefore"), bLoadedBefore);
	Result->SetBoolField(TEXT("loadedAfter"), bLoadedAfter);
	Result->SetBoolField(TEXT("loadedByBridge"), !bLoadedBefore && bLoadedAfter);
	OutResult = Result;
	return true;
}

bool FUEAgentKitEditorBridge::TryFocusActorResult(
	const FString& ActorGuidText,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (!RequireStoppedEditor(OutErrorCode, OutErrorMessage))
	{
		return false;
	}
	FGuid ActorGuid;
	if (!FGuid::Parse(ActorGuidText, ActorGuid) || !ActorGuid.IsValid())
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("actorGuid must be a valid non-zero GUID.");
		return false;
	}
	UWorld* EditorWorld = GetEditorWorld();
	if (EditorWorld == nullptr || EditorWorld->WorldType != EWorldType::Editor)
	{
		OutErrorCode = TEXT("live-editor-world-unavailable");
		OutErrorMessage = TEXT("The current Editor World is unavailable.");
		return false;
	}

	AActor* Match = nullptr;
	int32 MatchCount = 0;
	for (TActorIterator<AActor> It(EditorWorld); It; ++It)
	{
		AActor* Actor = *It;
		if (Actor != nullptr && !Actor->IsTemplate() && Actor->GetActorGuid() == ActorGuid)
		{
			Match = Actor;
			++MatchCount;
		}
	}
	if (MatchCount == 0 || Match == nullptr)
	{
		OutErrorCode = TEXT("live-editor-actor-not-found");
		OutErrorMessage = TEXT("No Actor with the exact ActorGuid exists in the current Editor World.");
		return false;
	}
	if (MatchCount != 1)
	{
		OutErrorCode = TEXT("live-editor-actor-guid-ambiguous");
		OutErrorMessage = TEXT("More than one current-world Actor has the supplied ActorGuid.");
		return false;
	}
	if (!GEditor->CanSelectActor(Match, true, true, false))
	{
		OutErrorCode = TEXT("live-editor-actor-not-selectable");
		OutErrorMessage = TEXT("The target Actor cannot be selected in the current Editor state.");
		return false;
	}

	const int32 DirtyBefore = CountDirtyGamePackages();
	GEditor->SelectNone(false, true, false);
	GEditor->SelectActor(Match, true, true, true, true);
	GEditor->MoveViewportCamerasToActor(*Match, false);
	const int32 DirtyAfter = CountDirtyGamePackages();

	TSharedRef<FJsonObject> Result = MakeActionBase(TEXT("focus-actor"), SessionId, GetPieStateName());
	Result->SetStringField(TEXT("actorGuid"), Match->GetActorGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
	Result->SetStringField(TEXT("actorInstanceGuid"), Match->GetActorInstanceGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
	Result->SetStringField(TEXT("actorPath"), Match->GetPathName());
	Result->SetStringField(TEXT("actorLabel"), Match->GetActorLabel());
	Result->SetStringField(TEXT("levelPath"), Match->GetLevel() != nullptr ? Match->GetLevel()->GetPathName() : FString());
	Result->SetBoolField(TEXT("selected"), Match->IsSelected());
	Result->SetBoolField(TEXT("viewportFocused"), true);
	Result->SetNumberField(TEXT("dirtyPackageCountBefore"), DirtyBefore);
	Result->SetNumberField(TEXT("dirtyPackageCountAfter"), DirtyAfter);
	Result->SetBoolField(TEXT("dirtyPackageCountChanged"), DirtyBefore != DirtyAfter);
	OutResult = Result;
	return true;
}
