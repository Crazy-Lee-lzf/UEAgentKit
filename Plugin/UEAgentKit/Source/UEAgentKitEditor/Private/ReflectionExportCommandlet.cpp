#include "ReflectionExportCommandlet.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Interfaces/IProjectManager.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "ModuleDescriptor.h"
#include "ProjectDescriptor.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Class.h"
#include "UObject/FieldIterator.h"
#include "UObject/MetaData.h"
#include "UObject/ObjectMacros.h"
#include "UObject/UObjectIterator.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogReflectionExport, Log, All);

namespace ReflectionExportPrivate
{
	constexpr const TCHAR* SchemaVersion = TEXT("reflection-1.0");
	constexpr const TCHAR* ExporterVersion = TEXT("0.8.0");
	constexpr const TCHAR* ProfileName = TEXT("code-reflection");

	FString MakeTypeStableId(const FString& CppName)
	{
		return TEXT("cpp:type:") + CppName;
	}

	FString MakeFunctionStableId(const FString& OwnerCppName, const FString& FunctionName)
	{
		return FString::Printf(TEXT("cpp:function:%s::%s"), *OwnerCppName, *FunctionName);
	}

	FString MakePropertyStableId(const FString& OwnerCppName, const FString& PropertyName)
	{
		return FString::Printf(TEXT("cpp:property:%s::%s"), *OwnerCppName, *PropertyName);
	}

	FString MakeParameterStableId(
		const FString& OwnerCppName,
		const FString& FunctionName,
		const FString& ParameterName)
	{
		return FString::Printf(
			TEXT("cpp:parameter:%s::%s::%s"),
			*OwnerCppName,
			*FunctionName,
			*ParameterName);
	}

	FString GetModuleName(const UObject* Object)
	{
		if (Object == nullptr || Object->GetOutermost() == nullptr)
		{
			return FString();
		}
		FString PackageName = Object->GetOutermost()->GetName();
		if (!PackageName.RemoveFromStart(TEXT("/Script/")))
		{
			return FString();
		}
		return PackageName;
	}

	TSharedRef<FJsonObject> BuildObjectMetadata(const UObject* Object)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
#if WITH_METADATA
		if (Object != nullptr)
		{
			if (const TMap<FName, FString>* Metadata = FMetaData::GetMapForObject(Object))
			{
				TArray<FName> Keys;
				Metadata->GetKeys(Keys);
				Keys.Sort(FNameLexicalLess());
				for (const FName& Key : Keys)
				{
					Result->SetStringField(Key.ToString(), Metadata->FindChecked(Key));
				}
			}
		}
#endif
		return Result;
	}

	TSharedRef<FJsonObject> BuildFieldMetadata(const FField* Field)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
#if WITH_METADATA
		if (Field != nullptr)
		{
			if (const TMap<FName, FString>* Metadata = Field->GetMetaDataMap())
			{
				TArray<FName> Keys;
				Metadata->GetKeys(Keys);
				Keys.Sort(FNameLexicalLess());
				for (const FName& Key : Keys)
				{
					Result->SetStringField(Key.ToString(), Metadata->FindChecked(Key));
				}
			}
		}
#endif
		return Result;
	}

	FString GetPropertyCppType(const FProperty* Property)
	{
		if (Property == nullptr)
		{
			return FString();
		}
		FString ExtendedType;
		const FString BaseType = Property->GetCPPType(&ExtendedType);
		return BaseType + ExtendedType;
	}

	TSharedRef<FJsonObject> BuildProperty(
		const FProperty* Property,
		const FString& OwnerCppName,
		const bool bFunctionParameter)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		const FString PropertyName = Property != nullptr ? Property->GetName() : FString();
		Result->SetStringField(TEXT("stableId"), MakePropertyStableId(OwnerCppName, PropertyName));
		Result->SetStringField(TEXT("name"), PropertyName);
		Result->SetStringField(TEXT("ownerCppName"), OwnerCppName);
		Result->SetStringField(TEXT("ownerStableId"), MakeTypeStableId(OwnerCppName));
		Result->SetStringField(TEXT("cppType"), GetPropertyCppType(Property));
		Result->SetStringField(
			TEXT("flagsValue"),
			Property != nullptr ? LexToString(static_cast<uint64>(Property->GetPropertyFlags())) : TEXT("0"));
		Result->SetBoolField(TEXT("functionParameter"), bFunctionParameter);
		Result->SetBoolField(
			TEXT("outParameter"),
			Property != nullptr && Property->HasAnyPropertyFlags(CPF_OutParm));
		Result->SetBoolField(
			TEXT("referenceParameter"),
			Property != nullptr && Property->HasAnyPropertyFlags(CPF_ReferenceParm));
		Result->SetBoolField(
			TEXT("constParameter"),
			Property != nullptr && Property->HasAnyPropertyFlags(CPF_ConstParm));
		Result->SetObjectField(TEXT("metadata"), BuildFieldMetadata(Property));
		return Result;
	}

	TSharedRef<FJsonObject> BuildFunction(const UFunction* Function, const FString& OwnerCppName)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		const FString FunctionName = Function != nullptr ? Function->GetName() : FString();
		const FString FunctionStableId = MakeFunctionStableId(OwnerCppName, FunctionName);
		Result->SetStringField(TEXT("stableId"), FunctionStableId);
		Result->SetStringField(TEXT("name"), FunctionName);
		Result->SetStringField(TEXT("ownerCppName"), OwnerCppName);
		Result->SetStringField(TEXT("ownerStableId"), MakeTypeStableId(OwnerCppName));
		Result->SetStringField(
			TEXT("flagsValue"),
			Function != nullptr ? LexToString(static_cast<uint64>(Function->FunctionFlags)) : TEXT("0"));
		Result->SetObjectField(TEXT("metadata"), BuildObjectMetadata(Function));

		TArray<TSharedPtr<FJsonValue>> Parameters;
		FString ReturnType;
		if (Function != nullptr)
		{
			for (TFieldIterator<FProperty> It(Function, EFieldIteratorFlags::ExcludeSuper); It; ++It)
			{
				const FProperty* Property = *It;
				if (Property == nullptr || !Property->HasAnyPropertyFlags(CPF_Parm))
				{
					continue;
				}
				if (Property->HasAnyPropertyFlags(CPF_ReturnParm))
				{
					ReturnType = GetPropertyCppType(Property);
					continue;
				}
				TSharedRef<FJsonObject> Parameter = BuildProperty(Property, OwnerCppName, true);
				Parameter->SetStringField(
					TEXT("stableId"),
					MakeParameterStableId(OwnerCppName, FunctionName, Property->GetName()));
				Parameter->SetStringField(TEXT("ownerCppName"), OwnerCppName + TEXT("::") + FunctionName);
				Parameter->SetStringField(TEXT("ownerStableId"), FunctionStableId);
				Parameters.Add(MakeShared<FJsonValueObject>(Parameter));
			}
		}
		Result->SetStringField(TEXT("returnType"), ReturnType);
		Result->SetArrayField(TEXT("parameters"), Parameters);
		return Result;
	}

	void AddDirectProperties(
		UStruct* Struct,
		const FString& OwnerCppName,
		TArray<TSharedPtr<FJsonValue>>& OutProperties)
	{
		if (Struct == nullptr)
		{
			return;
		}
		for (TFieldIterator<FProperty> It(Struct, EFieldIteratorFlags::ExcludeSuper); It; ++It)
		{
			const FProperty* Property = *It;
			if (Property == nullptr || Property->GetOwnerStruct() != Struct)
			{
				continue;
			}
			OutProperties.Add(MakeShared<FJsonValueObject>(BuildProperty(Property, OwnerCppName, false)));
		}
		OutProperties.Sort([](const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right)
		{
			const TSharedPtr<FJsonObject>* LeftObject = nullptr;
			const TSharedPtr<FJsonObject>* RightObject = nullptr;
			if (!Left.IsValid() || !Right.IsValid()
				|| !Left->TryGetObject(LeftObject) || !Right->TryGetObject(RightObject)
				|| LeftObject == nullptr || RightObject == nullptr)
			{
				return Left.IsValid() && !Right.IsValid();
			}
			return (*LeftObject)->GetStringField(TEXT("stableId")) < (*RightObject)->GetStringField(TEXT("stableId"));
		});
	}

	TSharedRef<FJsonObject> BuildClass(UClass* Class)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		const FString CppName = Class != nullptr
			? FString(Class->GetPrefixCPP()) + Class->GetName()
			: FString();
		Result->SetStringField(TEXT("stableId"), MakeTypeStableId(CppName));
		Result->SetStringField(TEXT("kind"), TEXT("class"));
		Result->SetStringField(TEXT("cppName"), CppName);
		Result->SetStringField(TEXT("reflectionName"), Class != nullptr ? Class->GetName() : FString());
		Result->SetStringField(TEXT("module"), GetModuleName(Class));
		Result->SetStringField(TEXT("objectPath"), Class != nullptr ? Class->GetPathName() : FString());
		Result->SetStringField(
			TEXT("flagsValue"),
			Class != nullptr ? LexToString(static_cast<uint64>(Class->GetClassFlags())) : TEXT("0"));
		Result->SetObjectField(TEXT("metadata"), BuildObjectMetadata(Class));

		UClass* SuperClass = Class != nullptr ? Class->GetSuperClass() : nullptr;
		const FString SuperCppName = SuperClass != nullptr
			? FString(SuperClass->GetPrefixCPP()) + SuperClass->GetName()
			: FString();
		Result->SetStringField(TEXT("superCppName"), SuperCppName);
		Result->SetStringField(TEXT("superObjectPath"), SuperClass != nullptr ? SuperClass->GetPathName() : FString());

		TArray<TSharedPtr<FJsonValue>> Interfaces;
		if (Class != nullptr)
		{
			for (const FImplementedInterface& Interface : Class->Interfaces)
			{
				if (Interface.Class == nullptr)
				{
					continue;
				}
				TSharedRef<FJsonObject> InterfaceObject = MakeShared<FJsonObject>();
				const FString InterfaceCppName = Interface.Class->HasAnyClassFlags(CLASS_Interface)
					? TEXT("I") + Interface.Class->GetName()
					: FString(Interface.Class->GetPrefixCPP()) + Interface.Class->GetName();
				InterfaceObject->SetStringField(TEXT("cppName"), InterfaceCppName);
				InterfaceObject->SetStringField(TEXT("stableId"), MakeTypeStableId(InterfaceCppName));
				InterfaceObject->SetStringField(TEXT("objectPath"), Interface.Class->GetPathName());
				Interfaces.Add(MakeShared<FJsonValueObject>(InterfaceObject));
			}
		}
		Result->SetArrayField(TEXT("interfaces"), Interfaces);

		TArray<TSharedPtr<FJsonValue>> Properties;
		AddDirectProperties(Class, CppName, Properties);
		Result->SetArrayField(TEXT("properties"), Properties);

		TArray<TSharedPtr<FJsonValue>> Functions;
		if (Class != nullptr)
		{
			for (TFieldIterator<UFunction> It(Class, EFieldIteratorFlags::ExcludeSuper); It; ++It)
			{
				UFunction* Function = *It;
				if (Function == nullptr || Function->GetOwnerClass() != Class)
				{
					continue;
				}
				Functions.Add(MakeShared<FJsonValueObject>(BuildFunction(Function, CppName)));
			}
		}
		Functions.Sort([](const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right)
		{
			const TSharedPtr<FJsonObject>* LeftObject = nullptr;
			const TSharedPtr<FJsonObject>* RightObject = nullptr;
			if (!Left.IsValid() || !Right.IsValid()
				|| !Left->TryGetObject(LeftObject) || !Right->TryGetObject(RightObject)
				|| LeftObject == nullptr || RightObject == nullptr)
			{
				return Left.IsValid() && !Right.IsValid();
			}
			return (*LeftObject)->GetStringField(TEXT("stableId")) < (*RightObject)->GetStringField(TEXT("stableId"));
		});
		Result->SetArrayField(TEXT("functions"), Functions);
		return Result;
	}

	TSharedRef<FJsonObject> BuildStruct(UScriptStruct* Struct)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		FString CppName = Struct != nullptr ? Struct->GetStructCPPName() : FString();
		if (CppName.IsEmpty() && Struct != nullptr)
		{
			CppName = FString(Struct->GetPrefixCPP()) + Struct->GetName();
		}
		Result->SetStringField(TEXT("stableId"), MakeTypeStableId(CppName));
		Result->SetStringField(TEXT("kind"), TEXT("struct"));
		Result->SetStringField(TEXT("cppName"), CppName);
		Result->SetStringField(TEXT("reflectionName"), Struct != nullptr ? Struct->GetName() : FString());
		Result->SetStringField(TEXT("module"), GetModuleName(Struct));
		Result->SetStringField(TEXT("objectPath"), Struct != nullptr ? Struct->GetPathName() : FString());
		Result->SetStringField(
			TEXT("flagsValue"),
			Struct != nullptr ? LexToString(static_cast<uint64>(Struct->StructFlags)) : TEXT("0"));
		Result->SetObjectField(TEXT("metadata"), BuildObjectMetadata(Struct));

		UStruct* SuperStruct = Struct != nullptr ? Struct->GetSuperStruct() : nullptr;
		FString SuperCppName;
		if (const UScriptStruct* ScriptSuper = Cast<UScriptStruct>(SuperStruct))
		{
			SuperCppName = ScriptSuper->GetStructCPPName();
			if (SuperCppName.IsEmpty())
			{
				SuperCppName = FString(ScriptSuper->GetPrefixCPP()) + ScriptSuper->GetName();
			}
		}
		Result->SetStringField(TEXT("superCppName"), SuperCppName);
		Result->SetStringField(TEXT("superObjectPath"), SuperStruct != nullptr ? SuperStruct->GetPathName() : FString());
		Result->SetArrayField(TEXT("interfaces"), {});
		Result->SetArrayField(TEXT("functions"), {});

		TArray<TSharedPtr<FJsonValue>> Properties;
		AddDirectProperties(Struct, CppName, Properties);
		Result->SetArrayField(TEXT("properties"), Properties);
		return Result;
	}

	TSharedRef<FJsonObject> BuildEnum(UEnum* Enum)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		FString CppName = Enum != nullptr ? Enum->CppType : FString();
		if (CppName.IsEmpty() && Enum != nullptr)
		{
			CppName = Enum->GetName();
		}
		Result->SetStringField(TEXT("stableId"), MakeTypeStableId(CppName));
		Result->SetStringField(TEXT("kind"), TEXT("enum"));
		Result->SetStringField(TEXT("cppName"), CppName);
		Result->SetStringField(TEXT("reflectionName"), Enum != nullptr ? Enum->GetName() : FString());
		Result->SetStringField(TEXT("module"), GetModuleName(Enum));
		Result->SetStringField(TEXT("objectPath"), Enum != nullptr ? Enum->GetPathName() : FString());
		Result->SetStringField(TEXT("flagsValue"), FString());
		Result->SetObjectField(TEXT("metadata"), BuildObjectMetadata(Enum));
		Result->SetStringField(TEXT("superCppName"), FString());
		Result->SetStringField(TEXT("superObjectPath"), FString());
		Result->SetArrayField(TEXT("interfaces"), {});
		Result->SetArrayField(TEXT("properties"), {});
		Result->SetArrayField(TEXT("functions"), {});

		TArray<TSharedPtr<FJsonValue>> FlagNames;
		if (Enum != nullptr && Enum->HasAnyEnumFlags(EEnumFlags::Flags))
		{
			FlagNames.Add(MakeShared<FJsonValueString>(TEXT("Flags")));
		}
		if (Enum != nullptr && Enum->HasAnyEnumFlags(EEnumFlags::NewerVersionExists))
		{
			FlagNames.Add(MakeShared<FJsonValueString>(TEXT("NewerVersionExists")));
		}
		Result->SetArrayField(TEXT("flagNames"), FlagNames);

		TArray<TSharedPtr<FJsonValue>> Values;
		if (Enum != nullptr)
		{
			for (int32 Index = 0; Index < Enum->NumEnums(); ++Index)
			{
				TSharedRef<FJsonObject> Value = MakeShared<FJsonObject>();
				Value->SetStringField(TEXT("name"), Enum->GetAuthoredNameStringByIndex(Index));
				Value->SetStringField(TEXT("value"), LexToString(Enum->GetValueByIndex(Index)));
				Values.Add(MakeShared<FJsonValueObject>(Value));
			}
		}
		Result->SetArrayField(TEXT("values"), Values);
		return Result;
	}

	bool SaveJson(const TSharedRef<FJsonObject>& Root, const FString& Filename, const bool bPretty)
	{
		FString Text;
		bool bSerialized = false;
		if (bPretty)
		{
			const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
				TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&Text);
			bSerialized = FJsonSerializer::Serialize(Root, Writer);
		}
		else
		{
			const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
				TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text);
			bSerialized = FJsonSerializer::Serialize(Root, Writer);
		}
		if (!bSerialized)
		{
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		return FFileHelper::SaveStringToFile(
			Text,
			*Filename,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	bool IsProjectModuleObject(const UObject* Object, const TSet<FString>& ProjectModules)
	{
		return ProjectModules.Contains(GetModuleName(Object));
	}
}

UReflectionExportCommandlet::UReflectionExportCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UReflectionExportCommandlet::Main(const FString& Params)
{
	using namespace ReflectionExportPrivate;

	FString OutputDirectory;
	FParse::Value(*Params, TEXT("Output="), OutputDirectory);
	const bool bPretty = !FParse::Param(*Params, TEXT("CompactJson"));

	OutputDirectory = OutputDirectory.IsEmpty()
		? FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKitReflection"))
		: FPaths::ConvertRelativePathToFull(OutputDirectory);
	const FString OutputFile = FPaths::Combine(OutputDirectory, TEXT("reflection.json"));

	const FProjectDescriptor* Project = IProjectManager::Get().GetCurrentProject();
	if (Project == nullptr)
	{
		UE_LOG(LogReflectionExport, Error, TEXT("No current project descriptor is available."));
		return 1;
	}

	TSet<FString> ProjectModules;
	TArray<TSharedPtr<FJsonValue>> ModuleValues;
	for (const FModuleDescriptor& Module : Project->Modules)
	{
		const FString ModuleName = Module.Name.ToString();
		if (ModuleName.IsEmpty())
		{
			continue;
		}
		ProjectModules.Add(ModuleName);
		ModuleValues.Add(MakeShared<FJsonValueString>(ModuleName));
	}
	if (ProjectModules.IsEmpty())
	{
		UE_LOG(LogReflectionExport, Error, TEXT("Current project declares no modules."));
		return 2;
	}

	TArray<TSharedRef<FJsonObject>> TypeObjects;
	for (TObjectIterator<UClass> It; It; ++It)
	{
		UClass* Class = *It;
		if (Class == nullptr || !IsProjectModuleObject(Class, ProjectModules))
		{
			continue;
		}
		TypeObjects.Add(BuildClass(Class));
	}
	for (TObjectIterator<UScriptStruct> It; It; ++It)
	{
		UScriptStruct* Struct = *It;
		if (Struct == nullptr || !IsProjectModuleObject(Struct, ProjectModules))
		{
			continue;
		}
		TypeObjects.Add(BuildStruct(Struct));
	}
	for (TObjectIterator<UEnum> It; It; ++It)
	{
		UEnum* Enum = *It;
		if (Enum == nullptr || !IsProjectModuleObject(Enum, ProjectModules))
		{
			continue;
		}
		TypeObjects.Add(BuildEnum(Enum));
	}

	TypeObjects.Sort([](const TSharedRef<FJsonObject>& Left, const TSharedRef<FJsonObject>& Right)
	{
		return Left->GetStringField(TEXT("stableId")) < Right->GetStringField(TEXT("stableId"));
	});

	TArray<TSharedPtr<FJsonValue>> Types;
	int32 FunctionCount = 0;
	int32 PropertyCount = 0;
	for (const TSharedRef<FJsonObject>& Type : TypeObjects)
	{
		const TArray<TSharedPtr<FJsonValue>>* Functions = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* Properties = nullptr;
		if (Type->TryGetArrayField(TEXT("functions"), Functions) && Functions != nullptr)
		{
			FunctionCount += Functions->Num();
		}
		if (Type->TryGetArrayField(TEXT("properties"), Properties) && Properties != nullptr)
		{
			PropertyCount += Properties->Num();
		}
		Types.Add(MakeShared<FJsonValueObject>(Type));
	}

	TSharedRef<FJsonObject> Summary = MakeShared<FJsonObject>();
	Summary->SetNumberField(TEXT("modules"), ProjectModules.Num());
	Summary->SetNumberField(TEXT("types"), TypeObjects.Num());
	Summary->SetNumberField(TEXT("functions"), FunctionCount);
	Summary->SetNumberField(TEXT("properties"), PropertyCount);

	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetStringField(TEXT("schemaVersion"), SchemaVersion);
	Root->SetStringField(TEXT("exporterVersion"), ExporterVersion);
	Root->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
	Root->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	Root->SetStringField(TEXT("createdUtc"), FDateTime::UtcNow().ToIso8601());
	Root->SetStringField(TEXT("profile"), ProfileName);
	Root->SetArrayField(TEXT("modules"), ModuleValues);
	Root->SetArrayField(TEXT("types"), Types);
	Root->SetObjectField(TEXT("summary"), Summary);

	if (!SaveJson(Root, OutputFile, bPretty))
	{
		UE_LOG(LogReflectionExport, Error, TEXT("Failed to write reflection export: %s"), *OutputFile);
		return 3;
	}

	UE_LOG(
		LogReflectionExport,
		Display,
		TEXT("Reflection export finished. Types=%d Functions=%d Properties=%d Output=%s"),
		TypeObjects.Num(),
		FunctionCount,
		PropertyCount,
		*OutputFile);
	return 0;
}
