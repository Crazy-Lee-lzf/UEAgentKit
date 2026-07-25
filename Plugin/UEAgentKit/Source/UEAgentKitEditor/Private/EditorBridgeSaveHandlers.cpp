#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "HAL/FileManager.h"
#include "Misc/CommandLine.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "ScalarWriteFixtureAsset.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/UObjectGlobals.h"

using namespace UEAgentKitEditorBridgePrivate;

bool FUEAgentKitEditorBridge::TrySaveAuthorizedAssetResult(
	const FString& AssetPath,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
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
		OutErrorMessage = TEXT("Authorized save is unavailable while PIE or SIE is active.");
		return false;
	}
	if (!IsSafeGameAssetPath(AssetPath))
	{
		OutErrorCode = TEXT("live-editor-invalid-parameters");
		OutErrorMessage = TEXT("assetPath must be one exact /Game Object Path.");
		return false;
	}

	UObject* Asset = StaticFindObject(UObject::StaticClass(), nullptr, *AssetPath, false);
	if (Asset == nullptr || !Asset->IsAsset())
	{
		OutErrorCode = TEXT("live-editor-save-asset-not-loaded");
		OutErrorMessage = TEXT("Authorized save only accepts an already loaded exact asset.");
		return false;
	}
	UPackage* Package = Asset->GetOutermost();
	if (Package == nullptr || !Package->GetName().StartsWith(TEXT("/Game/")))
	{
		OutErrorCode = TEXT("live-editor-save-package-invalid");
		OutErrorMessage = TEXT("The loaded asset does not belong to one project Content package.");
		return false;
	}
	if (Package->ContainsMap())
	{
		OutErrorCode = TEXT("live-editor-save-map-unsupported");
		OutErrorMessage = TEXT("Authorized save does not save maps or external-actor packages.");
		return false;
	}
	if (!Package->IsDirty())
	{
		OutErrorCode = TEXT("live-editor-save-not-dirty");
		OutErrorMessage = TEXT("The exact loaded package is not Dirty.");
		return false;
	}

	const FString Filename = FPackageName::LongPackageNameToFilename(
		Package->GetName(),
		FPackageName::GetAssetPackageExtension());
	if (Filename.IsEmpty() || !IFileManager::Get().FileExists(*Filename))
	{
		OutErrorCode = TEXT("live-editor-save-package-file-missing");
		OutErrorMessage = TEXT("The exact package file does not already exist on disk.");
		return false;
	}

	const int64 FileSizeBefore = IFileManager::Get().FileSize(*Filename);
	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	SaveArgs.SaveFlags = SAVE_NoError;
	SaveArgs.Error = GError;
	const bool bSaved = UPackage::SavePackage(Package, Asset, *Filename, SaveArgs);
	const int64 FileSizeAfter = IFileManager::Get().FileSize(*Filename);
	if (!bSaved || Package->IsDirty() || FileSizeAfter < 0)
	{
		OutErrorCode = TEXT("live-editor-save-failed");
		OutErrorMessage = TEXT("Unreal did not confirm a clean saved state for the exact package.");
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("save-authorized-asset"));
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetStringField(TEXT("pieState"), GetPieStateName());
	Result->SetStringField(TEXT("assetPath"), AssetPath);
	Result->SetStringField(TEXT("packageName"), Package->GetName());
	Result->SetStringField(TEXT("classPath"), Asset->GetClass() != nullptr ? Asset->GetClass()->GetPathName() : FString());
	Result->SetBoolField(TEXT("loadedByBridge"), false);
	Result->SetBoolField(TEXT("dirtyBefore"), true);
	Result->SetBoolField(TEXT("dirtyAfter"), Package->IsDirty());
	Result->SetBoolField(TEXT("saved"), true);
	Result->SetBoolField(TEXT("mapPackage"), false);
	Result->SetNumberField(TEXT("fileSizeBefore"), static_cast<double>(FileSizeBefore));
	Result->SetNumberField(TEXT("fileSizeAfter"), static_cast<double>(FileSizeAfter));
	OutResult = Result;
	return true;
}


bool FUEAgentKitEditorBridge::TryPrepareAuthorizedSaveFixtureResult(
	const FString& AssetPath,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	static const FString FixturePath = TEXT("/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget");
	if (!FParse::Param(FCommandLine::Get(), TEXT("UEAgentKitEnableTestHooks")))
	{
		OutErrorCode = TEXT("live-editor-test-hook-disabled");
		OutErrorMessage = TEXT("The authorized-save fixture hook is disabled.");
		return false;
	}
	if (GEditor == nullptr || GEditor->PlayWorld != nullptr)
	{
		OutErrorCode = TEXT("live-editor-pie-active");
		OutErrorMessage = TEXT("The authorized-save fixture hook requires a stopped Editor.");
		return false;
	}
	if (AssetPath != FixturePath)
	{
		OutErrorCode = TEXT("live-editor-test-hook-scope-rejected");
		OutErrorMessage = TEXT("The fixture hook accepts only the hard-coded scalar write fixture.");
		return false;
	}
	UUEAgentKitScalarWriteFixtureAsset* Fixture = Cast<UUEAgentKitScalarWriteFixtureAsset>(
		StaticFindObject(UObject::StaticClass(), nullptr, *FixturePath, false));
	if (Fixture == nullptr)
	{
		OutErrorCode = TEXT("live-editor-save-asset-not-loaded");
		OutErrorMessage = TEXT("Open the hard-coded scalar write fixture before using the test hook.");
		return false;
	}
	UPackage* Package = Fixture->GetOutermost();
	if (Package == nullptr)
	{
		OutErrorCode = TEXT("live-editor-save-package-invalid");
		OutErrorMessage = TEXT("The hard-coded scalar write fixture has no Package.");
		return false;
	}
	const bool bBefore = Fixture->BoolValue;
	Fixture->Modify();
	Fixture->BoolValue = !Fixture->BoolValue;
	Fixture->MarkPackageDirty();

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("prepare-authorized-save-fixture"));
	Result->SetStringField(TEXT("assetPath"), FixturePath);
	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	Result->SetBoolField(TEXT("boolValueBefore"), bBefore);
	Result->SetBoolField(TEXT("boolValueAfter"), Fixture->BoolValue);
	Result->SetBoolField(TEXT("packageDirty"), Package->IsDirty());
	Result->SetBoolField(TEXT("saved"), false);
	OutResult = Result;
	return Package->IsDirty();
}
