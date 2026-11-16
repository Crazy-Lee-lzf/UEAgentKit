#include "NiagaraStackFixtureCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "HAL/FileManager.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "NiagaraEmitter.h"
#include "NiagaraEmitterFactoryNew.h"
#include "NiagaraSystem.h"
#include "NiagaraSystemFactoryNew.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogNiagaraStackFixture, Log, All);

namespace NiagaraStackFixtureCommandletPrivate
{
	const TCHAR* const SystemPackageName = TEXT("/Game/Dev/NS_DevStack");
	const TCHAR* const EmitterAPackageName = TEXT("/Game/Dev/NE_DevStack_A");
	const TCHAR* const EmitterBPackageName = TEXT("/Game/Dev/NE_DevStack_B");
	const TCHAR* const EmitterAName = TEXT("EmitterA");
	const TCHAR* const EmitterBName = TEXT("EmitterB");

	bool SaveAssetPackage(UObject* Asset, FString& OutError)
	{
		if (Asset == nullptr)
		{
			OutError = TEXT("Cannot save a null asset.");
			return false;
		}

		UPackage* Package = Asset->GetOutermost();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension());
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);

		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Asset, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Failed to save package '%s'."), *Package->GetName());
			return false;
		}

		FAssetRegistryModule::AssetCreated(Asset);
		return true;
	}

	UNiagaraEmitter* CreateEmitter(const TCHAR* PackageName, const TCHAR* AssetName, FString& OutError)
	{
		UPackage* Package = CreatePackage(PackageName);
		if (Package == nullptr)
		{
			OutError = FString::Printf(TEXT("Failed to create package '%s'."), PackageName);
			return nullptr;
		}

		UNiagaraEmitterFactoryNew* Factory = NewObject<UNiagaraEmitterFactoryNew>();
		Factory->bAddDefaultModulesAndRenderersToEmptyEmitter = true;

		UObject* Created = Factory->FactoryCreateNew(
			UNiagaraEmitter::StaticClass(),
			Package,
			FName(AssetName),
			RF_Public | RF_Standalone,
			nullptr,
			GWarn);
		UNiagaraEmitter* Emitter = Cast<UNiagaraEmitter>(Created);
		if (Emitter == nullptr)
		{
			OutError = FString::Printf(TEXT("Failed to create emitter '%s'."), AssetName);
			return nullptr;
		}

		Emitter->MarkPackageDirty();
		return Emitter;
	}

	UNiagaraSystem* CreateSystem(FString& OutError)
	{
		UPackage* Package = CreatePackage(SystemPackageName);
		if (Package == nullptr)
		{
			OutError = FString::Printf(TEXT("Failed to create package '%s'."), SystemPackageName);
			return nullptr;
		}

		UNiagaraSystemFactoryNew* Factory = NewObject<UNiagaraSystemFactoryNew>();
		UObject* Created = Factory->FactoryCreateNew(
			UNiagaraSystem::StaticClass(),
			Package,
			FName(TEXT("NS_DevStack")),
			RF_Public | RF_Standalone,
			nullptr,
			GWarn);
		UNiagaraSystem* System = Cast<UNiagaraSystem>(Created);
		if (System == nullptr)
		{
			OutError = TEXT("Failed to create the Niagara System.");
			return nullptr;
		}

		System->MarkPackageDirty();
		return System;
	}
}

UNiagaraStackFixtureCommandlet::UNiagaraStackFixtureCommandlet()
{
	IsClient = false;
	IsServer = false;
	IsEditor = true;
	LogToConsole = true;
}

int32 UNiagaraStackFixtureCommandlet::Main(const FString& Params)
{
	using namespace NiagaraStackFixtureCommandletPrivate;

	UE_LOG(LogNiagaraStackFixture, Display, TEXT("Building the Niagara module stack fixture."));

	FString Error;
	UNiagaraEmitter* EmitterA = CreateEmitter(EmitterAPackageName, TEXT("NE_DevStack_A"), Error);
	if (EmitterA == nullptr)
	{
		UE_LOG(LogNiagaraStackFixture, Error, TEXT("%s"), *Error);
		return 1;
	}

	UNiagaraEmitter* EmitterB = CreateEmitter(EmitterBPackageName, TEXT("NE_DevStack_B"), Error);
	if (EmitterB == nullptr)
	{
		UE_LOG(LogNiagaraStackFixture, Error, TEXT("%s"), *Error);
		return 1;
	}

	UNiagaraSystem* System = CreateSystem(Error);
	if (System == nullptr)
	{
		UE_LOG(LogNiagaraStackFixture, Error, TEXT("%s"), *Error);
		return 1;
	}

	// Rebuild the handles from scratch so re-running the commandlet is deterministic.
	System->GetEmitterHandles().Empty();
	System->AddEmitterHandle(*EmitterA, FName(EmitterAName), EmitterA->GetExposedVersion().VersionGuid);
	System->AddEmitterHandle(*EmitterB, FName(EmitterBName), EmitterB->GetExposedVersion().VersionGuid);

	// The system factory already created the system spawn/update graphs and the
	// required system update module, so no extra system modules are added here.
	System->MarkPackageDirty();

	// The factory requested a compile; finish it so the saved asset is stable and
	// the reader does not see a "still compiling" system right after generation.
	System->WaitForCompilationComplete();

	if (!SaveAssetPackage(EmitterA, Error) || !SaveAssetPackage(EmitterB, Error) || !SaveAssetPackage(System, Error))
	{
		UE_LOG(LogNiagaraStackFixture, Error, TEXT("%s"), *Error);
		return 1;
	}

	UE_LOG(LogNiagaraStackFixture, Display,
		TEXT("Fixture ready: %s with %d emitter handle(s)."),
		SystemPackageName,
		System->GetEmitterHandles().Num());
	return 0;
}
