#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"
#include "EditorBridgeLogCapture.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "Engine/Blueprint.h"
#include "Modules/ModuleManager.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildInspectAssetLiveResult(const FString& AssetPath) const
{
	FAssetRegistryModule& AssetRegistryModule = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	const FAssetData AssetData = AssetRegistryModule.Get().GetAssetByObjectPath(FSoftObjectPath(AssetPath));
	UObject* LoadedObject = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	if (LoadedObject != nullptr && !LoadedObject->IsAsset())
	{
		LoadedObject = nullptr;
	}

	TSharedRef<FJsonObject> RegistryState = MakeShared<FJsonObject>();
	RegistryState->SetBoolField(TEXT("found"), AssetData.IsValid());
	RegistryState->SetStringField(TEXT("assetPath"), AssetData.IsValid() ? AssetData.GetObjectPathString() : AssetPath);
	RegistryState->SetStringField(TEXT("packageName"), AssetData.IsValid() ? AssetData.PackageName.ToString() : FString());
	RegistryState->SetStringField(TEXT("assetName"), AssetData.IsValid() ? AssetData.AssetName.ToString() : FString());
	RegistryState->SetStringField(TEXT("classPath"), AssetData.IsValid() ? AssetData.AssetClassPath.ToString() : FString());

	TSharedRef<FJsonObject> MemoryState = MakeShared<FJsonObject>();
	MemoryState->SetBoolField(TEXT("loaded"), LoadedObject != nullptr);
	MemoryState->SetBoolField(TEXT("loadedByBridge"), false);
	MemoryState->SetBoolField(TEXT("packageDirty"), LoadedObject != nullptr && LoadedObject->GetOutermost() != nullptr && LoadedObject->GetOutermost()->IsDirty());
	MemoryState->SetBoolField(TEXT("openInAssetEditor"), UEAgentKitEditorBridgePrivate::IsAssetOpenInEditor(LoadedObject));
	MemoryState->SetBoolField(TEXT("selected"), UEAgentKitEditorBridgePrivate::IsObjectSelected(LoadedObject));
	MemoryState->SetBoolField(TEXT("rooted"), LoadedObject != nullptr && LoadedObject->IsRooted());
	MemoryState->SetStringField(TEXT("objectPath"), LoadedObject != nullptr ? LoadedObject->GetPathName() : FString());
	MemoryState->SetStringField(TEXT("classPath"), LoadedObject != nullptr && LoadedObject->GetClass() != nullptr ? LoadedObject->GetClass()->GetPathName() : FString());
	MemoryState->SetStringField(
		TEXT("state"),
		LoadedObject == nullptr
			? TEXT("not-loaded")
			: (LoadedObject->GetOutermost() != nullptr && LoadedObject->GetOutermost()->IsDirty() ? TEXT("loaded-unsaved") : TEXT("loaded-saved")));

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetNumberField(TEXT("pieSessionId"), LogCapture.IsValid() ? LogCapture->GetCurrentPieSessionId() : 0);
	Result->SetStringField(TEXT("pieState"), LogCapture.IsValid() ? LogCapture->GetCurrentPieState() : TEXT("unavailable"));
	Result->SetObjectField(TEXT("assetRegistry"), RegistryState);
	Result->SetObjectField(TEXT("memory"), MemoryState);
	Result->SetBoolField(TEXT("hasBlueprintState"), Cast<UBlueprint>(LoadedObject) != nullptr);
	if (UBlueprint* Blueprint = Cast<UBlueprint>(LoadedObject))
	{
		Result->SetObjectField(TEXT("blueprint"), UEAgentKitEditorBridgePrivate::DescribeBlueprintState(Blueprint));
	}
	return Result;
}
