#include "WriteFixturePlanCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintContextSha256.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Engine/Texture2D.h"
#include "EdGraphSchema_K2.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "MaterialEditingLibrary.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionStaticSwitchParameter.h"
#include "Materials/MaterialExpressionTextureSampleParameter2D.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Misc/App.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "ReferenceWriteFixtureAsset.h"
#include "ScalarWriteFixtureAsset.h"
#include "StructuredWriteFixtureAsset.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/EditorAssetSubsystem.h"
#include "UObject/GarbageCollection.h"
#include "UObject/Interface.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "UObject/SoftObjectPtr.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogWriteFixturePlan, Log, All);

namespace WriteFixturePlanCommandletPrivate
{
	constexpr const TCHAR* SchemaVersion = TEXT("1.0");
	constexpr const TCHAR* ToolVersion = TEXT("0.6.0");
	constexpr int32 MaxFixtures = 64;

	struct FFixtureDefinition
	{
		FString Id;
		FString Kind;
		FString SourceAsset;
		FString TargetAsset;
		FString ExpectedClass;
		FString ParentClassPath;
		EBlueprintType BlueprintType = BPTYPE_Normal;
		TMap<FString, TSharedPtr<FJsonValue>> ReferenceValues;
		TMap<FString, TArray<FString>> MaterialParameters;
		TMap<FString, TSharedPtr<FJsonValue>> MaterialValues;
		FString ParentAsset;
		TObjectPtr<UObject> SourceObject = nullptr;
		TObjectPtr<UClass> ParentClass = nullptr;
	};

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

	bool SaveJsonObject(const FString& Filename, const TSharedRef<FJsonObject>& Object, FString& OutError)
	{
		FString JsonText;
		const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(Object, Writer))
		{
			OutError = TEXT("Could not serialize fixture report.");
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		if (!FFileHelper::SaveStringToFile(JsonText, *Filename, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write fixture report: %s"), *Filename);
			return false;
		}
		return true;
	}

	FString NormalizePackagePath(const FString& Input)
	{
		FString Result = Input;
		int32 DotIndex = INDEX_NONE;
		if (Result.FindChar(TEXT('.'), DotIndex))
		{
			Result = Result.Left(DotIndex);
		}
		Result.RemoveFromEnd(TEXT("/"));
		return Result;
	}

	bool IsSpecificGameRoot(const FString& Root)
	{
		return Root.StartsWith(TEXT("/Game/"), ESearchCase::CaseSensitive)
			&& !Root.Contains(TEXT(".."))
			&& FPackageName::IsValidLongPackageName(Root);
	}

	bool IsTargetUnderRoot(const FString& Target, const FString& Root)
	{
		return Target.StartsWith(Root + TEXT("/"), ESearchCase::CaseSensitive)
			&& !Target.Contains(TEXT(".."))
			&& !Target.Contains(TEXT("."))
			&& FPackageName::IsValidLongPackageName(Target);
	}

	bool IsReadableSourcePackage(const FString& Source)
	{
		return Source.StartsWith(TEXT("/"), ESearchCase::CaseSensitive)
			&& !Source.Contains(TEXT(".."))
			&& FPackageName::IsValidLongPackageName(Source);
	}

	FString ToObjectPath(const FString& PackagePath)
	{
		return PackagePath + TEXT(".") + FPackageName::GetLongPackageAssetName(PackagePath);
	}

	FString GetAssetClassPath(const UObject* Asset)
	{
		return Asset && Asset->GetClass()
			? Asset->GetClass()->GetClassPathName().ToString()
			: FString();
	}

	FString GetPackageFilename(const FString& PackageName)
	{
		return FPaths::ConvertRelativePathToFull(FPackageName::LongPackageNameToFilename(
			PackageName,
			FPackageName::GetAssetPackageExtension()));
	}

	TArray<FString> FindPackageSidecars(const FString& PackageFilename)
	{
		static const TCHAR* SidecarExtensions[] = {
			TEXT(".uexp"),
			TEXT(".ubulk"),
			TEXT(".uptnl"),
			TEXT(".m.ubulk"),
			TEXT(".upayload")};
		TArray<FString> Results;
		const FString BaseFilename = FPaths::ChangeExtension(PackageFilename, TEXT(""));
		for (const TCHAR* Extension : SidecarExtensions)
		{
			const FString Candidate = BaseFilename + Extension;
			if (IFileManager::Get().FileExists(*Candidate))
			{
				Results.Add(Candidate);
			}
		}
		return Results;
	}

	bool ParseBlueprintType(const FString& Value, EBlueprintType& OutType)
	{
		if (Value.Equals(TEXT("Normal"), ESearchCase::CaseSensitive))
		{
			OutType = BPTYPE_Normal;
			return true;
		}
		if (Value.Equals(TEXT("FunctionLibrary"), ESearchCase::CaseSensitive))
		{
			OutType = BPTYPE_FunctionLibrary;
			return true;
		}
		if (Value.Equals(TEXT("MacroLibrary"), ESearchCase::CaseSensitive))
		{
			OutType = BPTYPE_MacroLibrary;
			return true;
		}
		if (Value.Equals(TEXT("Interface"), ESearchCase::CaseSensitive))
		{
			OutType = BPTYPE_Interface;
			return true;
		}
		return false;
	}

	bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		if (!Blueprint)
		{
			OutError = TEXT("Blueprint is null.");
			return false;
		}
		UPackage* Package = Blueprint->GetOutermost();
		const FString Filename = GetPackageFilename(Package->GetName());
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
			OutError = FString::Printf(TEXT("Could not save Blueprint fixture: %s"), *Filename);
			return false;
		}
		return true;
	}

	UUEAgentKitScalarWriteFixtureAsset* CreateScalarAssetFixture(
		const FFixtureDefinition& Definition,
		FString& OutError)
	{
		UPackage* Package = CreatePackage(*Definition.TargetAsset);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		const FString AssetName = FPackageName::GetLongPackageAssetName(Definition.TargetAsset);
		UUEAgentKitScalarWriteFixtureAsset* Asset = NewObject<UUEAgentKitScalarWriteFixtureAsset>(
			Package,
			FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional);
		if (!Asset)
		{
			OutError = FString::Printf(TEXT("Could not create scalar fixture: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Asset);
		Package->MarkPackageDirty();
		const FString Filename = GetPackageFilename(Definition.TargetAsset);
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Asset, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save scalar fixture: %s"), *Filename);
			return nullptr;
		}
		return Asset;
	}

	UMaterial* CreateMaterialParentAssetFixture(
		const FFixtureDefinition& Definition,
		FString& OutError)
	{
		UPackage* Package = CreatePackage(*Definition.TargetAsset);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		const FString AssetName = FPackageName::GetLongPackageAssetName(Definition.TargetAsset);
		UMaterial* Material = NewObject<UMaterial>(
			Package,
			FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional);
		if (!Material)
		{
			OutError = FString::Printf(TEXT("Could not create material fixture: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		for (const TPair<FString, TArray<FString>>& TypeEntry : Definition.MaterialParameters)
		{
			for (const FString& ParameterName : TypeEntry.Value)
			{
				if (TypeEntry.Key.Equals(TEXT("Scalar"), ESearchCase::CaseSensitive))
				{
					UMaterialExpressionScalarParameter* Expression = NewObject<UMaterialExpressionScalarParameter>(
						Material,
						NAME_None,
						RF_Transactional);
					Expression->ParameterName = FName(*ParameterName);
					Expression->DefaultValue = 0.5f;
					Material->GetExpressionCollection().AddExpression(Expression);
				}
				else if (TypeEntry.Key.Equals(TEXT("Vector"), ESearchCase::CaseSensitive))
				{
					UMaterialExpressionVectorParameter* Expression = NewObject<UMaterialExpressionVectorParameter>(
						Material,
						NAME_None,
						RF_Transactional);
					Expression->ParameterName = FName(*ParameterName);
					Expression->DefaultValue = FLinearColor(0.2f, 0.2f, 0.2f, 1.0f);
					Material->GetExpressionCollection().AddExpression(Expression);
				}
				else if (TypeEntry.Key.Equals(TEXT("Texture"), ESearchCase::CaseSensitive))
				{
					UMaterialExpressionTextureSampleParameter2D* Expression = NewObject<UMaterialExpressionTextureSampleParameter2D>(
						Material,
						NAME_None,
						RF_Transactional);
					Expression->ParameterName = FName(*ParameterName);
					Material->GetExpressionCollection().AddExpression(Expression);
				}
				else
				{
					UMaterialExpressionStaticSwitchParameter* Expression = NewObject<UMaterialExpressionStaticSwitchParameter>(
						Material,
						NAME_None,
						RF_Transactional);
					Expression->ParameterName = FName(*ParameterName);
					Expression->DefaultValue = false;
					Material->GetExpressionCollection().AddExpression(Expression);
				}
			}
		}
		const TArray<TObjectPtr<UMaterialExpression>>& Expressions = Material->GetExpressionCollection().Expressions;
		if (!Expressions.IsEmpty())
		{
			Material->GetEditorOnlyData()->BaseColor.Expression = Expressions[0];
			Material->GetEditorOnlyData()->BaseColor.Mask = 0;
		}
		FAssetRegistryModule::AssetCreated(Material);
		Material->PreEditChange(nullptr);
		Material->PostEditChange();
		Package->MarkPackageDirty();
		const FString Filename = GetPackageFilename(Definition.TargetAsset);
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Material, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save material fixture: %s"), *Filename);
			return nullptr;
		}
		return Material;
	}

	UMaterialInstanceConstant* CreateMaterialAssetFixture(
		const FFixtureDefinition& Definition,
		FString& OutError)
	{
		UMaterialInterface* ParentMaterial = LoadObject<UMaterialInterface>(nullptr, *Definition.ParentAsset);
		if (!ParentMaterial)
		{
			OutError = FString::Printf(TEXT("Parent material could not be loaded: %s"), *Definition.ParentAsset);
			return nullptr;
		}
		UPackage* Package = CreatePackage(*Definition.TargetAsset);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		const FString AssetName = FPackageName::GetLongPackageAssetName(Definition.TargetAsset);
		UMaterialInstanceConstant* Instance = NewObject<UMaterialInstanceConstant>(
			Package,
			FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional);
		if (!Instance)
		{
			OutError = FString::Printf(TEXT("Could not create material instance fixture: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		Instance->Parent = ParentMaterial;
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Definition.MaterialValues)
		{
			FString Type;
			FString ParameterName;
			if (!Entry.Key.Split(TEXT(":"), &Type, &ParameterName)
				|| ParameterName.IsEmpty() || !Entry.Value.IsValid())
			{
				OutError = TEXT("Material fixture value key must use Type:ParameterName form.");
				return nullptr;
			}
			if (Type.Equals(TEXT("Scalar"), ESearchCase::CaseSensitive))
			{
				double Number = 0.0;
				if (!Entry.Value->TryGetNumber(Number) || !FMath::IsFinite(Number))
				{
					OutError = FString::Printf(TEXT("Material scalar fixture value is invalid: %s"), *ParameterName);
					return nullptr;
				}
				UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
					Instance,
					FName(*ParameterName),
					static_cast<float>(Number),
					EMaterialParameterAssociation::GlobalParameter);
			}
			else if (Type.Equals(TEXT("Vector"), ESearchCase::CaseSensitive))
			{
				const TSharedPtr<FJsonObject> Color = Entry.Value->AsObject();
				double R = 0.0;
				double G = 0.0;
				double B = 0.0;
				double A = 0.0;
				if (!Color.IsValid()
					|| !Color->TryGetNumberField(TEXT("r"), R)
					|| !Color->TryGetNumberField(TEXT("g"), G)
					|| !Color->TryGetNumberField(TEXT("b"), B)
					|| !Color->TryGetNumberField(TEXT("a"), A))
				{
					OutError = FString::Printf(TEXT("Material vector fixture value is invalid: %s"), *ParameterName);
					return nullptr;
				}
				UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
					Instance,
					FName(*ParameterName),
					FLinearColor(
						static_cast<float>(R),
						static_cast<float>(G),
						static_cast<float>(B),
						static_cast<float>(A)),
					EMaterialParameterAssociation::GlobalParameter);
			}
			else if (Type.Equals(TEXT("Texture"), ESearchCase::CaseSensitive))
			{
				FString TexturePath;
				if (!Entry.Value->TryGetString(TexturePath))
				{
					OutError = FString::Printf(TEXT("Material texture fixture value is invalid: %s"), *ParameterName);
					return nullptr;
				}
				UTexture* Texture = LoadObject<UTexture>(nullptr, *TexturePath);
				if (!Texture)
				{
					OutError = FString::Printf(TEXT("Material texture fixture asset could not be loaded: %s"), *TexturePath);
					return nullptr;
				}
				UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
					Instance,
					FName(*ParameterName),
					Texture,
					EMaterialParameterAssociation::GlobalParameter);
			}
			else
			{
				bool bValue = false;
				if (!Entry.Value->TryGetBool(bValue))
				{
					OutError = FString::Printf(TEXT("Material static switch fixture value is invalid: %s"), *ParameterName);
					return nullptr;
				}
				UMaterialEditingLibrary::SetMaterialInstanceStaticSwitchParameterValue(
					Instance,
					FName(*ParameterName),
					bValue,
					EMaterialParameterAssociation::GlobalParameter,
					true);
			}
		}
		FAssetRegistryModule::AssetCreated(Instance);
		Instance->PreEditChange(nullptr);
		Instance->PostEditChange();
		Package->MarkPackageDirty();
		const FString Filename = GetPackageFilename(Definition.TargetAsset);
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Instance, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save material instance fixture: %s"), *Filename);
			return nullptr;
		}
		return Instance;
	}

	bool ApplyReferenceFixtureValues(
		UUEAgentKitReferenceWriteFixtureAsset* Asset,
		const TMap<FString, TSharedPtr<FJsonValue>>& Values,
		FString& OutError)
	{
		if (!Asset)
		{
			OutError = TEXT("Reference fixture asset is null.");
			return false;
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : Values)
		{
			FProperty* Property = FindFProperty<FProperty>(Asset->GetClass(), FName(*Entry.Key));
			if (Property == nullptr)
			{
				OutError = FString::Printf(TEXT("Reference fixture property was not found: %s"), *Entry.Key);
				return false;
			}
			void* ValueAddress = Property->ContainerPtrToValuePtr<void>(Asset);
			const TSharedPtr<FJsonValue>& JsonValue = Entry.Value;
			if (!JsonValue.IsValid() || JsonValue->Type == EJson::Null)
			{
				if (FSoftObjectProperty* SoftObjectProperty = CastField<FSoftObjectProperty>(Property))
				{
					SoftObjectProperty->SetPropertyValue(ValueAddress, FSoftObjectPtr());
				}
				else if (FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
				{
					ObjectProperty->SetObjectPropertyValue(ValueAddress, nullptr);
				}
				else
				{
					OutError = FString::Printf(TEXT("Reference fixture property is not a reference: %s"), *Entry.Key);
					return false;
				}
				continue;
			}
			if (JsonValue->Type != EJson::Object)
			{
				OutError = FString::Printf(TEXT("Reference fixture value must be null or an object: %s"), *Entry.Key);
				return false;
			}
			const TSharedPtr<FJsonObject> ReferenceObject = JsonValue->AsObject();
			FString RequestedType;
			FString RequestedPath;
			if (!ReferenceObject.IsValid()
				|| !ReferenceObject->TryGetStringField(TEXT("referenceType"), RequestedType)
				|| !ReferenceObject->TryGetStringField(TEXT("path"), RequestedPath)
				|| RequestedPath.IsEmpty())
			{
				OutError = FString::Printf(TEXT("Reference fixture value requires non-empty referenceType and path: %s"), *Entry.Key);
				return false;
			}
			const FString ExpectedType =
				Entry.Key == TEXT("ObjectValue") ? TEXT("Object")
				: Entry.Key == TEXT("ClassValue") ? TEXT("Class")
				: Entry.Key == TEXT("SoftObjectValue") ? TEXT("SoftObject")
				: TEXT("SoftClass");
			if (!RequestedType.Equals(ExpectedType, ESearchCase::CaseSensitive))
			{
				OutError = FString::Printf(
					TEXT("Reference fixture type %s does not match %s."),
					*RequestedType,
					*Entry.Key);
				return false;
			}
			const FSoftObjectPath SoftPath(RequestedPath);
			if (!SoftPath.IsValid() || !SoftPath.GetSubPathString().IsEmpty())
			{
				OutError = FString::Printf(TEXT("Reference fixture path is invalid or contains a subobject: %s"), *RequestedPath);
				return false;
			}
			if (ExpectedType == TEXT("Class") || ExpectedType == TEXT("SoftClass"))
			{
				UClass* ReferencedClass = LoadObject<UClass>(nullptr, *RequestedPath);
				if (ReferencedClass == nullptr || !ReferencedClass->IsChildOf(AActor::StaticClass()))
				{
					OutError = FString::Printf(
						TEXT("Reference fixture class is missing or not an Actor class: %s"),
						*RequestedPath);
					return false;
				}
				if (ExpectedType == TEXT("Class"))
				{
					CastFieldChecked<FClassProperty>(Property)->SetObjectPropertyValue(ValueAddress, ReferencedClass);
				}
				else
				{
					CastFieldChecked<FSoftClassProperty>(Property)->SetPropertyValue(ValueAddress, FSoftObjectPtr(SoftPath));
				}
			}
			else
			{
				UObject* ReferencedObject = StaticLoadObject(UTexture2D::StaticClass(), nullptr, *RequestedPath);
				if (ReferencedObject == nullptr)
				{
					OutError = FString::Printf(
						TEXT("Reference fixture object is missing or is not a Texture2D: %s"),
						*RequestedPath);
					return false;
				}
				if (ExpectedType == TEXT("Object"))
				{
					CastFieldChecked<FObjectPropertyBase>(Property)->SetObjectPropertyValue(ValueAddress, ReferencedObject);
				}
				else
				{
					CastFieldChecked<FSoftObjectProperty>(Property)->SetPropertyValue(ValueAddress, FSoftObjectPtr(SoftPath));
				}
			}
		}
		return true;
	}

	UUEAgentKitReferenceWriteFixtureAsset* CreateReferenceAssetFixture(
		const FFixtureDefinition& Definition,
		FString& OutError)
	{
		UPackage* Package = CreatePackage(*Definition.TargetAsset);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		const FString AssetName = FPackageName::GetLongPackageAssetName(Definition.TargetAsset);
		UUEAgentKitReferenceWriteFixtureAsset* Asset = NewObject<UUEAgentKitReferenceWriteFixtureAsset>(
			Package,
			FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional);
		if (!Asset)
		{
			OutError = FString::Printf(TEXT("Could not create reference fixture: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		FString ValuesError;
		if (!ApplyReferenceFixtureValues(Asset, Definition.ReferenceValues, ValuesError))
		{
			OutError = FString::Printf(TEXT("Could not set reference fixture values: %s"), *ValuesError);
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Asset);
		Package->MarkPackageDirty();
		const FString Filename = GetPackageFilename(Definition.TargetAsset);
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Asset, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save reference fixture: %s"), *Filename);
			return nullptr;
		}
		return Asset;
	}

	UUEAgentKitStructuredWriteFixtureAsset* CreateStructuredAssetFixture(
		const FFixtureDefinition& Definition,
		FString& OutError)
	{
		UPackage* Package = CreatePackage(*Definition.TargetAsset);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		const FString AssetName = FPackageName::GetLongPackageAssetName(Definition.TargetAsset);
		UUEAgentKitStructuredWriteFixtureAsset* Asset = NewObject<UUEAgentKitStructuredWriteFixtureAsset>(
			Package,
			FName(*AssetName),
			RF_Public | RF_Standalone | RF_Transactional);
		if (!Asset)
		{
			OutError = FString::Printf(TEXT("Could not create structured fixture: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Asset);
		Package->MarkPackageDirty();
		const FString Filename = GetPackageFilename(Definition.TargetAsset);
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Asset, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Could not save structured fixture: %s"), *Filename);
			return nullptr;
		}
		return Asset;
	}

	UBlueprint* CreateBlueprintFixture(const FFixtureDefinition& Definition, FString& OutError)
	{
		UPackage* Package = CreatePackage(*Definition.TargetAsset);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		const FString AssetName = FPackageName::GetLongPackageAssetName(Definition.TargetAsset);
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			Definition.ParentClass,
			Package,
			FName(*AssetName),
			Definition.BlueprintType,
			FName(TEXT("UEAgentKitWriteFixturePlan")));
		if (!Blueprint)
		{
			OutError = FString::Printf(TEXT("Could not create Blueprint fixture: %s"), *Definition.TargetAsset);
			return nullptr;
		}
		Blueprint->BlueprintDescription = FString::Printf(
			TEXT("UEAgentKit generated fixture %s."),
			*Definition.Id);
		if (Definition.Id.Equals(TEXT("transaction-blueprint"), ESearchCase::CaseSensitive))
		{
			FEdGraphPinType IntType;
			IntType.PinCategory = UEdGraphSchema_K2::PC_Int;
			FEdGraphPinType BoolType;
			BoolType.PinCategory = UEdGraphSchema_K2::PC_Boolean;
			if (!FBlueprintEditorUtils::AddMemberVariable(
					Blueprint,
					FName(TEXT("TransactionInt")),
					IntType,
					TEXT("0"))
				|| !FBlueprintEditorUtils::AddMemberVariable(
					Blueprint,
					FName(TEXT("TransactionFlag")),
					BoolType,
					TEXT("false")))
			{
				OutError = TEXT("Could not create transaction Blueprint fixture variables.");
				return nullptr;
			}
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		if (!SaveBlueprint(Blueprint, OutError))
		{
			return nullptr;
		}
		return Blueprint;
	}

	void AddError(TArray<TSharedPtr<FJsonValue>>& Errors, const FString& Code, const FString& Message, const FString& Path)
	{
		const TSharedRef<FJsonObject> Error = MakeShared<FJsonObject>();
		Error->SetStringField(TEXT("code"), Code);
		Error->SetStringField(TEXT("message"), Message);
		Error->SetStringField(TEXT("path"), Path);
		Errors.Add(MakeShared<FJsonValueObject>(Error));
	}
}

UWriteFixturePlanCommandlet::UWriteFixturePlanCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UWriteFixturePlanCommandlet::Main(const FString& Params)
{
	using namespace WriteFixturePlanCommandletPrivate;

	FString PlanFilename;
	FString ExpectedPlanRevision;
	FString ReportFilename;
	FString Mode = TEXT("Create");
	FParse::Value(*Params, TEXT("Plan="), PlanFilename);
	FParse::Value(*Params, TEXT("ExpectedPlanRevision="), ExpectedPlanRevision);
	FParse::Value(*Params, TEXT("Report="), ReportFilename);
	FParse::Value(*Params, TEXT("Mode="), Mode);
	PlanFilename = FPaths::ConvertRelativePathToFull(PlanFilename);
	ReportFilename = FPaths::ConvertRelativePathToFull(ReportFilename);

	const TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
	Report->SetStringField(TEXT("schemaVersion"), SchemaVersion);
	Report->SetStringField(TEXT("toolVersion"), ToolVersion);
	Report->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
	Report->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Report->SetStringField(TEXT("mode"), Mode);
	Report->SetStringField(TEXT("planPath"), PlanFilename);
	TArray<TSharedPtr<FJsonValue>> Errors;
	TArray<TSharedPtr<FJsonValue>> FixtureResults;
	int32 DeletedCount = 0;
	int32 CreatedCount = 0;

	auto Finish = [&](const int32 ExitCode, const bool bValid, const FString& Status) -> int32
	{
		Report->SetNumberField(TEXT("deletedCount"), DeletedCount);
		Report->SetNumberField(TEXT("createdCount"), CreatedCount);
		Report->SetBoolField(TEXT("valid"), bValid);
		Report->SetStringField(TEXT("status"), Status);
		Report->SetArrayField(TEXT("fixtures"), FixtureResults);
		Report->SetArrayField(TEXT("errors"), Errors);
		Report->SetNumberField(TEXT("fixtureCount"), FixtureResults.Num());
		Report->SetNumberField(TEXT("errorCount"), Errors.Num());
		FString SaveError;
		if (ReportFilename.IsEmpty() || !SaveJsonObject(ReportFilename, Report, SaveError))
		{
			UE_LOG(LogWriteFixturePlan, Error, TEXT("%s"), *SaveError);
			return ExitCode == 0 ? 9 : ExitCode;
		}
		return ExitCode;
	};

	if (PlanFilename.IsEmpty() || ExpectedPlanRevision.IsEmpty() || ReportFilename.IsEmpty())
	{
		AddError(Errors, TEXT("arguments"), TEXT("Plan, ExpectedPlanRevision, and Report are required."), TEXT("commandLine"));
		return Finish(1, false, TEXT("invalid-arguments"));
	}
	if (!Mode.Equals(TEXT("Create"), ESearchCase::CaseSensitive)
		&& !Mode.Equals(TEXT("Reset"), ESearchCase::CaseSensitive))
	{
		AddError(Errors, TEXT("mode"), TEXT("Mode must be Create or Reset."), TEXT("commandLine.Mode"));
		return Finish(1, false, TEXT("invalid-mode"));
	}

	TSharedPtr<FJsonObject> Plan;
	FString Error;
	if (!LoadJsonObject(PlanFilename, Plan, Error))
	{
		AddError(Errors, TEXT("plan-json"), Error, TEXT("plan"));
		return Finish(1, false, TEXT("invalid-plan"));
	}
	FString PlanHash;
	if (!FBlueprintContextSha256::HashFile(PlanFilename, PlanHash))
	{
		AddError(Errors, TEXT("plan-hash"), TEXT("Fixture plan could not be hashed."), TEXT("plan"));
		return Finish(1, false, TEXT("invalid-plan"));
	}
	const FString ActualPlanRevision = TEXT("sha256:") + PlanHash.ToLower();
	Report->SetStringField(TEXT("planRevision"), ActualPlanRevision);
	if (!ActualPlanRevision.Equals(ExpectedPlanRevision, ESearchCase::CaseSensitive))
	{
		AddError(
			Errors,
			TEXT("plan-revision-conflict"),
			FString::Printf(
				TEXT("Expected Plan Revision %s, found %s."),
				*ExpectedPlanRevision,
				*ActualPlanRevision),
			TEXT("commandLine.ExpectedPlanRevision"));
		return Finish(2, false, TEXT("validation-failed"));
	}
	FString PlanSchemaVersion;
	Plan->TryGetStringField(TEXT("schemaVersion"), PlanSchemaVersion);
	if (!PlanSchemaVersion.Equals(SchemaVersion, ESearchCase::CaseSensitive))
	{
		AddError(Errors, TEXT("plan-schema"), TEXT("Unsupported fixture plan schemaVersion."), TEXT("plan.schemaVersion"));
	}
	FString Root;
	Plan->TryGetStringField(TEXT("root"), Root);
	Root.RemoveFromEnd(TEXT("/"));
	if (!IsSpecificGameRoot(Root))
	{
		AddError(Errors, TEXT("root"), TEXT("root must be a specific valid directory below /Game."), TEXT("plan.root"));
	}
	Report->SetStringField(TEXT("root"), Root);

	const TArray<TSharedPtr<FJsonValue>>* FixtureValues = nullptr;
	if (!Plan->TryGetArrayField(TEXT("fixtures"), FixtureValues)
		|| !FixtureValues
		|| FixtureValues->IsEmpty()
		|| FixtureValues->Num() > MaxFixtures)
	{
		AddError(
			Errors,
			TEXT("fixtures"),
			FString::Printf(TEXT("fixtures must contain 1-%d entries."), MaxFixtures),
			TEXT("plan.fixtures"));
	}

	UEditorAssetSubsystem* AssetSubsystem = GEditor
		? GEditor->GetEditorSubsystem<UEditorAssetSubsystem>()
		: nullptr;
	if (!AssetSubsystem)
	{
		AddError(Errors, TEXT("editor-subsystem"), TEXT("EditorAssetSubsystem is unavailable."), TEXT("engine"));
	}
	if (!Errors.IsEmpty())
	{
		return Finish(2, false, TEXT("validation-failed"));
	}

	TArray<FFixtureDefinition> Definitions;
	TSet<FString> FixtureIds;
	TSet<FString> TargetPackages;
	for (int32 Index = 0; Index < FixtureValues->Num(); ++Index)
	{
		const TSharedPtr<FJsonObject> Object = (*FixtureValues)[Index].IsValid()
			? (*FixtureValues)[Index]->AsObject()
			: nullptr;
		const FString BasePath = FString::Printf(TEXT("plan.fixtures[%d]"), Index);
		if (!Object.IsValid())
		{
			AddError(Errors, TEXT("fixture-object"), TEXT("Fixture entry must be an object."), BasePath);
			continue;
		}
		FFixtureDefinition Definition;
		Object->TryGetStringField(TEXT("id"), Definition.Id);
		Object->TryGetStringField(TEXT("kind"), Definition.Kind);
		Object->TryGetStringField(TEXT("targetAsset"), Definition.TargetAsset);
		Object->TryGetStringField(TEXT("expectedClass"), Definition.ExpectedClass);
		const bool bTargetHasObjectSuffix = Definition.TargetAsset.Contains(TEXT("."));
		Definition.TargetAsset = NormalizePackagePath(Definition.TargetAsset);
		if (Definition.Id.IsEmpty() || FixtureIds.Contains(Definition.Id))
		{
			AddError(Errors, TEXT("fixture-id"), TEXT("Fixture id must be non-empty and unique."), BasePath + TEXT(".id"));
		}
		else
		{
			FixtureIds.Add(Definition.Id);
		}
		if (bTargetHasObjectSuffix
			|| !IsTargetUnderRoot(Definition.TargetAsset, Root)
			|| TargetPackages.Contains(Definition.TargetAsset))
		{
			AddError(
				Errors,
				TEXT("target"),
				TEXT("targetAsset must be a unique package directly below the declared root."),
				BasePath + TEXT(".targetAsset"));
		}
		else
		{
			TargetPackages.Add(Definition.TargetAsset);
		}
		if (!Definition.ExpectedClass.StartsWith(TEXT("/Script/"), ESearchCase::CaseSensitive))
		{
			AddError(Errors, TEXT("expected-class"), TEXT("expectedClass must use /Script/Module.Class form."), BasePath + TEXT(".expectedClass"));
		}

		if (Definition.Kind.Equals(TEXT("duplicateAsset"), ESearchCase::CaseSensitive))
		{
			Object->TryGetStringField(TEXT("sourceAsset"), Definition.SourceAsset);
			Definition.SourceAsset = NormalizePackagePath(Definition.SourceAsset);
			if (!IsReadableSourcePackage(Definition.SourceAsset)
				|| Definition.SourceAsset.Equals(Definition.TargetAsset, ESearchCase::CaseSensitive))
			{
				AddError(Errors, TEXT("source"), TEXT("sourceAsset must be a different valid long package name."), BasePath + TEXT(".sourceAsset"));
			}
			else
			{
				Definition.SourceObject = AssetSubsystem->LoadAsset(Definition.SourceAsset);
				if (!Definition.SourceObject)
				{
					AddError(Errors, TEXT("source-missing"), TEXT("sourceAsset could not be loaded."), BasePath + TEXT(".sourceAsset"));
				}
				else if (!GetAssetClassPath(Definition.SourceObject).Equals(Definition.ExpectedClass, ESearchCase::CaseSensitive))
				{
					AddError(
						Errors,
						TEXT("source-class"),
						FString::Printf(
							TEXT("sourceAsset class %s does not match expectedClass %s."),
							*GetAssetClassPath(Definition.SourceObject),
							*Definition.ExpectedClass),
						BasePath + TEXT(".expectedClass"));
				}
			}
		}
		else if (Definition.Kind.Equals(TEXT("scalarAsset"), ESearchCase::CaseSensitive))
		{
			if (!Definition.ExpectedClass.Equals(
					TEXT("/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"),
					ESearchCase::CaseSensitive))
			{
				AddError(
					Errors,
					TEXT("scalar-asset-class"),
					TEXT("scalarAsset fixtures require the UEAgentKit scalar fixture class."),
					BasePath + TEXT(".expectedClass"));
			}
		}
		else if (Definition.Kind.Equals(TEXT("referenceAsset"), ESearchCase::CaseSensitive))
		{
			if (!Definition.ExpectedClass.Equals(
					TEXT("/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset"),
					ESearchCase::CaseSensitive))
			{
				AddError(
					Errors,
					TEXT("reference-asset-class"),
					TEXT("referenceAsset fixtures require the UEAgentKit reference fixture class."),
					BasePath + TEXT(".expectedClass"));
			}
			const TSharedPtr<FJsonValue> ValuesField = Object->Values.FindRef(TEXT("values"));
			if (ValuesField.IsValid())
			{
				const TSharedPtr<FJsonObject> ValuesObject = ValuesField->AsObject();
				if (!ValuesObject.IsValid())
				{
					AddError(
						Errors,
						TEXT("reference-values"),
						TEXT("referenceAsset values must be an object."),
						BasePath + TEXT(".values"));
				}
				else
				{
					for (const TPair<FString, TSharedPtr<FJsonValue>>& Entry : ValuesObject->Values)
					{
						const FString PropertyPath = BasePath + TEXT(".values.") + Entry.Key;
						const FString ExpectedType =
							Entry.Key == TEXT("ObjectValue") ? TEXT("Object")
							: Entry.Key == TEXT("ClassValue") ? TEXT("Class")
							: Entry.Key == TEXT("SoftObjectValue") ? TEXT("SoftObject")
							: Entry.Key == TEXT("SoftClassValue") ? TEXT("SoftClass")
							: FString();
						if (ExpectedType.IsEmpty())
						{
							AddError(
								Errors,
								TEXT("reference-values-property"),
								TEXT("Unknown reference fixture property."),
								PropertyPath);
							continue;
						}
						if (!Entry.Value.IsValid() || Entry.Value->Type == EJson::Null)
						{
							Definition.ReferenceValues.Add(Entry.Key, Entry.Value);
							continue;
						}
						if (Entry.Value->Type != EJson::Object)
						{
							AddError(
								Errors,
								TEXT("reference-values-value"),
								TEXT("Reference fixture value must be null or an object."),
								PropertyPath);
							continue;
						}
						const TSharedPtr<FJsonObject> ReferenceObject = Entry.Value->AsObject();
						FString RequestedType;
						FString RequestedPath;
						if (!ReferenceObject.IsValid()
							|| !ReferenceObject->TryGetStringField(TEXT("referenceType"), RequestedType)
							|| !ReferenceObject->TryGetStringField(TEXT("path"), RequestedPath)
							|| RequestedPath.IsEmpty())
						{
							AddError(
								Errors,
								TEXT("reference-values-value"),
								TEXT("Reference fixture value requires non-empty referenceType and path."),
								PropertyPath);
							continue;
						}
						if (!RequestedType.Equals(ExpectedType, ESearchCase::CaseSensitive))
						{
							AddError(
								Errors,
								TEXT("reference-values-type"),
								FString::Printf(
									TEXT("Reference fixture type %s does not match property type %s."),
									*RequestedType,
									*ExpectedType),
								PropertyPath + TEXT(".referenceType"));
							continue;
						}
						const FSoftObjectPath SoftPath(RequestedPath);
						if (!SoftPath.IsValid() || !SoftPath.GetSubPathString().IsEmpty())
						{
							AddError(
								Errors,
								TEXT("reference-values-path"),
								FString::Printf(
									TEXT("Reference fixture path is invalid or contains a subobject: %s"),
									*RequestedPath),
								PropertyPath + TEXT(".path"));
							continue;
						}
						Definition.ReferenceValues.Add(Entry.Key, Entry.Value);
					}
				}
			}
		}
		else if (Definition.Kind.Equals(TEXT("structuredAsset"), ESearchCase::CaseSensitive))
		{
			if (!Definition.ExpectedClass.Equals(
					TEXT("/Script/UEAgentKitEditor.UEAgentKitStructuredWriteFixtureAsset"),
					ESearchCase::CaseSensitive))
			{
				AddError(
					Errors,
					TEXT("structured-asset-class"),
					TEXT("structuredAsset fixtures require the UEAgentKit structured fixture class."),
					BasePath + TEXT(".expectedClass"));
			}
		}
		else if (Definition.Kind.Equals(TEXT("materialParentAsset"), ESearchCase::CaseSensitive))
		{
			if (!Definition.ExpectedClass.Equals(TEXT("/Script/Engine.Material"), ESearchCase::CaseSensitive))
			{
				AddError(
					Errors,
					TEXT("material-parent-class"),
					TEXT("materialParentAsset fixtures require expectedClass /Script/Engine.Material."),
					BasePath + TEXT(".expectedClass"));
			}
			const TSharedPtr<FJsonValue> ParametersField = Object->Values.FindRef(TEXT("parameters"));
			const TSharedPtr<FJsonObject> ParametersObject = ParametersField.IsValid()
				? ParametersField->AsObject()
				: nullptr;
			if (!ParametersObject.IsValid())
			{
				AddError(
					Errors,
					TEXT("material-parent-parameters"),
					TEXT("materialParentAsset parameters must be an object."),
					BasePath + TEXT(".parameters"));
			}
			else
			{
				for (const TPair<FString, TSharedPtr<FJsonValue>>& TypeEntry : ParametersObject->Values)
				{
					const FString ParameterPath = BasePath + TEXT(".parameters.") + TypeEntry.Key;
					if (!TypeEntry.Key.Equals(TEXT("Scalar"), ESearchCase::CaseSensitive)
						&& !TypeEntry.Key.Equals(TEXT("Vector"), ESearchCase::CaseSensitive)
						&& !TypeEntry.Key.Equals(TEXT("Texture"), ESearchCase::CaseSensitive)
						&& !TypeEntry.Key.Equals(TEXT("StaticSwitch"), ESearchCase::CaseSensitive))
					{
						AddError(
							Errors,
							TEXT("material-parent-parameter-type"),
							TEXT("materialParentAsset parameter types must be Scalar, Vector, Texture, or StaticSwitch."),
							ParameterPath);
						continue;
					}
					const TArray<TSharedPtr<FJsonValue>>* NamesArray = nullptr;
					if (!TypeEntry.Value.IsValid() || !TypeEntry.Value->TryGetArray(NamesArray) || NamesArray->IsEmpty())
					{
						AddError(
							Errors,
							TEXT("material-parent-parameter-type"),
							TEXT("materialParentAsset parameter type must contain at least one parameter name."),
							ParameterPath);
						continue;
					}
					TArray<FString> ValidNames;
					TSet<FString> SeenNames;
					for (const TSharedPtr<FJsonValue>& NameValue : *NamesArray)
					{
						FString Name;
						if (!NameValue.IsValid() || !NameValue->TryGetString(Name) || Name.IsEmpty() || Name.Len() > 128 || Name.Contains(TEXT(".")) || Name.Contains(TEXT("/")))
						{
							AddError(
								Errors,
								TEXT("material-parent-parameter-name"),
								TEXT("Material parameter names must be non-empty strings of at most 128 characters without dots or slashes."),
								ParameterPath);
							continue;
						}
						if (SeenNames.Contains(Name))
						{
							AddError(
								Errors,
								TEXT("material-parent-parameter-name"),
								TEXT("Material parameter names must be unique within a type."),
								ParameterPath);
							continue;
						}
						SeenNames.Add(Name);
						ValidNames.Add(Name);
					}
					Definition.MaterialParameters.Add(TypeEntry.Key, MoveTemp(ValidNames));
				}
			}
		}
		else if (Definition.Kind.Equals(TEXT("materialAsset"), ESearchCase::CaseSensitive))
		{
			if (!Definition.ExpectedClass.Equals(
					TEXT("/Script/Engine.MaterialInstanceConstant"),
					ESearchCase::CaseSensitive))
			{
				AddError(
					Errors,
					TEXT("material-asset-class"),
					TEXT("materialAsset fixtures require expectedClass /Script/Engine.MaterialInstanceConstant."),
					BasePath + TEXT(".expectedClass"));
			}
			Object->TryGetStringField(TEXT("parentAsset"), Definition.ParentAsset);
			Definition.ParentAsset = NormalizePackagePath(Definition.ParentAsset);
			if (!IsReadableSourcePackage(Definition.ParentAsset)
				|| Definition.ParentAsset.Equals(Definition.TargetAsset, ESearchCase::CaseSensitive))
			{
				AddError(
					Errors,
					TEXT("material-parent-invalid"),
					TEXT("materialAsset parentAsset must be a different valid long package name."),
					BasePath + TEXT(".parentAsset"));
			}
			const TSharedPtr<FJsonValue> ValuesField = Object->Values.FindRef(TEXT("values"));
			if (ValuesField.IsValid())
			{
				const TSharedPtr<FJsonObject> ValuesObject = ValuesField->AsObject();
				if (!ValuesObject.IsValid())
				{
					AddError(
						Errors,
						TEXT("material-values"),
						TEXT("materialAsset values must be an object."),
						BasePath + TEXT(".values"));
				}
				else
				{
					for (const TPair<FString, TSharedPtr<FJsonValue>>& TypeEntry : ValuesObject->Values)
					{
						const FString ValuesPath = BasePath + TEXT(".values.") + TypeEntry.Key;
						if (!TypeEntry.Key.Equals(TEXT("Scalar"), ESearchCase::CaseSensitive)
							&& !TypeEntry.Key.Equals(TEXT("Vector"), ESearchCase::CaseSensitive)
							&& !TypeEntry.Key.Equals(TEXT("Texture"), ESearchCase::CaseSensitive)
							&& !TypeEntry.Key.Equals(TEXT("StaticSwitch"), ESearchCase::CaseSensitive))
						{
							AddError(
								Errors,
								TEXT("material-values-type"),
								TEXT("materialAsset value types must be Scalar, Vector, Texture, or StaticSwitch."),
								ValuesPath);
							continue;
						}
						const TSharedPtr<FJsonObject> TypeValues = TypeEntry.Value.IsValid()
							? TypeEntry.Value->AsObject()
							: nullptr;
						if (!TypeValues.IsValid() || TypeValues->Values.IsEmpty())
						{
							AddError(
								Errors,
								TEXT("material-values-type"),
								TEXT("materialAsset value type must contain at least one parameter value."),
								ValuesPath);
							continue;
						}
						for (const TPair<FString, TSharedPtr<FJsonValue>>& ParameterEntry : TypeValues->Values)
						{
							if (ParameterEntry.Key.IsEmpty() || ParameterEntry.Key.Len() > 128
								|| ParameterEntry.Key.Contains(TEXT(".")) || ParameterEntry.Key.Contains(TEXT("/"))
								|| !ParameterEntry.Value.IsValid())
							{
								AddError(
									Errors,
									TEXT("material-values-name"),
									TEXT("Material value parameter names must be non-empty strings without dots or slashes."),
									ValuesPath);
								continue;
							}
							Definition.MaterialValues.Add(
								TypeEntry.Key + TEXT(":") + ParameterEntry.Key,
								ParameterEntry.Value);
						}
					}
				}
			}
		}
		else if (Definition.Kind.Equals(TEXT("blueprint"), ESearchCase::CaseSensitive))
		{
			FString BlueprintTypeText;
			Object->TryGetStringField(TEXT("parentClass"), Definition.ParentClassPath);
			Object->TryGetStringField(TEXT("blueprintType"), BlueprintTypeText);
			if (!Definition.ExpectedClass.Equals(TEXT("/Script/Engine.Blueprint"), ESearchCase::CaseSensitive))
			{
				AddError(Errors, TEXT("blueprint-class"), TEXT("blueprint fixtures require expectedClass /Script/Engine.Blueprint."), BasePath + TEXT(".expectedClass"));
			}
			if (!ParseBlueprintType(BlueprintTypeText, Definition.BlueprintType))
			{
				AddError(Errors, TEXT("blueprint-type"), TEXT("blueprintType is invalid."), BasePath + TEXT(".blueprintType"));
			}
			Definition.ParentClass = LoadObject<UClass>(nullptr, *Definition.ParentClassPath);
			if (!Definition.ParentClass)
			{
				AddError(Errors, TEXT("parent-class"), TEXT("parentClass could not be loaded."), BasePath + TEXT(".parentClass"));
			}
		}
		else
		{
			AddError(Errors, TEXT("fixture-kind"), TEXT("kind must be duplicateAsset, scalarAsset, referenceAsset, structuredAsset, materialParentAsset, materialAsset, or blueprint."), BasePath + TEXT(".kind"));
		}
		Definitions.Add(MoveTemp(Definition));
	}

	for (const FFixtureDefinition& Definition : Definitions)
	{
		if (!Definition.SourceAsset.IsEmpty() && TargetPackages.Contains(Definition.SourceAsset))
		{
			AddError(
				Errors,
				TEXT("source-target-overlap"),
				TEXT("A sourceAsset cannot also be a targetAsset in the same plan."),
				Definition.Id);
		}
		if (Definition.Kind.Equals(TEXT("materialAsset"), ESearchCase::CaseSensitive)
			&& !TargetPackages.Contains(Definition.ParentAsset))
		{
			AddError(
				Errors,
				TEXT("material-parent-kind"),
				TEXT("materialAsset parentAsset must reference a materialParentAsset fixture in the same plan."),
				Definition.Id);
		}
		const bool bExists = AssetSubsystem->DoesAssetExist(Definition.TargetAsset);
		if (Mode.Equals(TEXT("Create"), ESearchCase::CaseSensitive) && bExists)
		{
			AddError(
				Errors,
				TEXT("target-exists"),
				TEXT("Create mode refuses existing targets; use Reset for deterministic recreation."),
				Definition.TargetAsset);
		}
		if (bExists)
		{
			const TArray<FString> Sidecars = FindPackageSidecars(GetPackageFilename(Definition.TargetAsset));
			if (!Sidecars.IsEmpty())
			{
				AddError(
					Errors,
					TEXT("target-sidecars"),
					TEXT("Fixture reset currently supports only single-file .uasset packages."),
					Definition.TargetAsset);
			}
		}
	}
	if (!Errors.IsEmpty())
	{
		return Finish(2, false, TEXT("validation-failed"));
	}

	if (Mode.Equals(TEXT("Reset"), ESearchCase::CaseSensitive))
	{
		for (const FFixtureDefinition& Definition : Definitions)
		{
			if (AssetSubsystem->DoesAssetExist(Definition.TargetAsset))
			{
				if (!AssetSubsystem->DeleteAsset(Definition.TargetAsset))
				{
					AddError(Errors, TEXT("delete-failed"), TEXT("Could not delete existing fixture target."), Definition.TargetAsset);
					return Finish(3, false, TEXT("reset-failed"));
				}
				++DeletedCount;
			}
		}
		if (DeletedCount > 0)
		{
			CollectGarbage(RF_NoFlags);
		}
	}

	for (const FFixtureDefinition& Definition : Definitions)
	{
		UObject* CreatedAsset = nullptr;
		FString CreateError;
		if (Definition.Kind.Equals(TEXT("duplicateAsset"), ESearchCase::CaseSensitive))
		{
			CreatedAsset = AssetSubsystem->DuplicateAsset(Definition.SourceAsset, Definition.TargetAsset);
			if (CreatedAsset && !AssetSubsystem->SaveLoadedAsset(CreatedAsset, false))
			{
				CreateError = TEXT("Duplicated fixture could not be saved.");
				CreatedAsset = nullptr;
			}
		}
		else if (Definition.Kind.Equals(TEXT("scalarAsset"), ESearchCase::CaseSensitive))
		{
			CreatedAsset = CreateScalarAssetFixture(Definition, CreateError);
		}
		else if (Definition.Kind.Equals(TEXT("referenceAsset"), ESearchCase::CaseSensitive))
		{
			CreatedAsset = CreateReferenceAssetFixture(Definition, CreateError);
		}
		else if (Definition.Kind.Equals(TEXT("structuredAsset"), ESearchCase::CaseSensitive))
		{
			CreatedAsset = CreateStructuredAssetFixture(Definition, CreateError);
		}
		else if (Definition.Kind.Equals(TEXT("materialParentAsset"), ESearchCase::CaseSensitive))
		{
			CreatedAsset = CreateMaterialParentAssetFixture(Definition, CreateError);
		}
		else if (Definition.Kind.Equals(TEXT("materialAsset"), ESearchCase::CaseSensitive))
		{
			CreatedAsset = CreateMaterialAssetFixture(Definition, CreateError);
		}
		else
		{
			CreatedAsset = CreateBlueprintFixture(Definition, CreateError);
		}

		if (!CreatedAsset)
		{
			AddError(
				Errors,
				TEXT("create-failed"),
				CreateError.IsEmpty() ? TEXT("Fixture creation failed.") : CreateError,
				Definition.TargetAsset);
			return Finish(4, false, TEXT("creation-failed"));
		}
		const FString ActualClass = GetAssetClassPath(CreatedAsset);
		if (!ActualClass.Equals(Definition.ExpectedClass, ESearchCase::CaseSensitive))
		{
			AddError(
				Errors,
				TEXT("created-class"),
				FString::Printf(TEXT("Created class %s does not match %s."), *ActualClass, *Definition.ExpectedClass),
				Definition.TargetAsset);
			return Finish(5, false, TEXT("verification-failed"));
		}
		const FString PackageFilename = GetPackageFilename(Definition.TargetAsset);
		FString RevisionHex;
		if (!IFileManager::Get().FileExists(*PackageFilename)
			|| !FBlueprintContextSha256::HashFile(PackageFilename, RevisionHex))
		{
			AddError(Errors, TEXT("revision"), TEXT("Created fixture package could not be hashed."), Definition.TargetAsset);
			return Finish(5, false, TEXT("verification-failed"));
		}
		const TArray<FString> Sidecars = FindPackageSidecars(PackageFilename);
		if (!Sidecars.IsEmpty())
		{
			AddError(Errors, TEXT("created-sidecars"), TEXT("Created fixture has unsupported package sidecars."), Definition.TargetAsset);
			return Finish(5, false, TEXT("verification-failed"));
		}

		const TSharedRef<FJsonObject> FixtureResult = MakeShared<FJsonObject>();
		FixtureResult->SetStringField(TEXT("id"), Definition.Id);
		FixtureResult->SetStringField(TEXT("kind"), Definition.Kind);
		FixtureResult->SetStringField(TEXT("assetPath"), ToObjectPath(Definition.TargetAsset));
		FixtureResult->SetStringField(TEXT("packageName"), Definition.TargetAsset);
		FixtureResult->SetStringField(TEXT("assetClass"), ActualClass);
		FixtureResult->SetStringField(TEXT("packageFilename"), PackageFilename);
		FixtureResult->SetStringField(TEXT("revision"), TEXT("sha256:") + RevisionHex.ToLower());
		FixtureResult->SetNumberField(TEXT("fileSize"), IFileManager::Get().FileSize(*PackageFilename));
		FixtureResult->SetBoolField(TEXT("singleFilePackage"), true);
		FixtureResult->SetStringField(TEXT("status"), TEXT("created"));
		FixtureResults.Add(MakeShared<FJsonValueObject>(FixtureResult));
		++CreatedCount;
	}

	UE_LOG(
		LogWriteFixturePlan,
		Display,
		TEXT("Fixture plan completed. Mode=%s Created=%d Deleted=%d Root=%s"),
		*Mode,
		CreatedCount,
		DeletedCount,
		*Root);
	return Finish(0, true, TEXT("completed"));
}
