#include "BlueprintWriteFixtureCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "UObject/Interface.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintWriteFixture, Log, All);

namespace BlueprintWriteFixtureCommandletPrivate
{
	bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		if (!Blueprint)
		{
			OutError = TEXT("Blueprint is null.");
			return false;
		}

		UPackage* Package = Blueprint->GetOutermost();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension());
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);

		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = FString::Printf(TEXT("Blueprint compilation failed: %s"), *Blueprint->GetPathName());
			return false;
		}

		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Failed to save fixture: %s"), *Filename);
			return false;
		}
		return true;
	}

	bool CreateFixture(
		const FString& Root,
		const FString& AssetName,
		UClass* ParentClass,
		EBlueprintType BlueprintType,
		FString& OutPath,
		FString& OutError)
	{
		const FString PackageName = Root / AssetName;
		OutPath = PackageName + TEXT(".") + AssetName;
		if (LoadObject<UBlueprint>(nullptr, *OutPath))
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
			ParentClass,
			Package,
			FName(*AssetName),
			BlueprintType,
			FName(TEXT("UEAgentKitWriteFixture")));
		if (!Blueprint)
		{
			OutError = FString::Printf(TEXT("Could not create Blueprint: %s"), *PackageName);
			return false;
		}

		Blueprint->BlueprintDescription = FString::Printf(
			TEXT("UEAgentKit generated %s fixture."),
			*AssetName);
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		return SaveBlueprint(Blueprint, OutError);
	}
}

UBlueprintWriteFixtureCommandlet::UBlueprintWriteFixtureCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UBlueprintWriteFixtureCommandlet::Main(const FString& Params)
{
	using namespace BlueprintWriteFixtureCommandletPrivate;

	FString Root = TEXT("/Game/UEAgentKitWriteTests");
	FParse::Value(*Params, TEXT("Root="), Root);
	Root.RemoveFromEnd(TEXT("/"));
	if (!Root.StartsWith(TEXT("/Game/"), ESearchCase::CaseSensitive))
	{
		UE_LOG(LogBlueprintWriteFixture, Error, TEXT("Root must be a specific directory below /Game."));
		return 1;
	}

	struct FFixtureDefinition
	{
		const TCHAR* Name;
		UClass* ParentClass;
		EBlueprintType BlueprintType;
	};

	const TArray<FFixtureDefinition> Fixtures = {
		{TEXT("BFL_PatchTarget"), UBlueprintFunctionLibrary::StaticClass(), BPTYPE_FunctionLibrary},
		{TEXT("BML_PatchTarget"), AActor::StaticClass(), BPTYPE_MacroLibrary},
		{TEXT("BPI_PatchTarget"), UInterface::StaticClass(), BPTYPE_Interface},
	};

	for (const FFixtureDefinition& Fixture : Fixtures)
	{
		FString AssetPath;
		FString Error;
		if (!CreateFixture(Root, Fixture.Name, Fixture.ParentClass, Fixture.BlueprintType, AssetPath, Error))
		{
			UE_LOG(LogBlueprintWriteFixture, Error, TEXT("%s"), *Error);
			return 2;
		}
		UE_LOG(LogBlueprintWriteFixture, Display, TEXT("Fixture ready: %s"), *AssetPath);
	}

	return 0;
}
