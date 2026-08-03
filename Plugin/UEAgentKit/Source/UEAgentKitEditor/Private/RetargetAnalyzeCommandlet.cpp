#include "RetargetAnalyzeCommandlet.h"

#include "Dom/JsonObject.h"
#include "Engine/SkeletalMesh.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/Paths.h"
#include "Retarget/RetargetAnalysis.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/UObjectGlobals.h"

DEFINE_LOG_CATEGORY_STATIC(LogRetargetAnalyze, Log, All);

namespace
{
	FString LoadParam(const FString& Params, const FString& Key)
	{
		FString Value;
		if (FParse::Value(*Params, *Key, Value, false))
		{
			return Value;
		}
		return FString();
	}

	USkeletalMesh* LoadSkeletalMesh(const FString& ObjectPath, FString& OutError)
	{
		USkeletalMesh* Mesh = LoadObject<USkeletalMesh>(nullptr, *ObjectPath);
		if (Mesh == nullptr)
		{
			OutError = FString::Printf(TEXT("Could not load SkeletalMesh: %s"), *ObjectPath);
			return nullptr;
		}
		return Mesh;
	}

	bool SaveJsonObject(const FString& Filename, const TSharedRef<FJsonObject>& Object, FString& OutError)
	{
		FString JsonText;
		const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(Object, Writer))
		{
			OutError = TEXT("Could not serialize the analysis report.");
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);
		if (!FFileHelper::SaveStringToFile(JsonText, *Filename, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write the analysis report: %s"), *Filename);
			return false;
		}
		return true;
	}
}

URetargetAnalyzeCommandlet::URetargetAnalyzeCommandlet()
{
	IsClient = false;
	IsEditor = true;
}

int32 URetargetAnalyzeCommandlet::Main(const FString& Params)
{
	const FString ReportPath = LoadParam(Params, TEXT("-Report="));
	const FString SourceMeshParam = LoadParam(Params, TEXT("-SourceMesh="));
	const FString TargetMeshParam = LoadParam(Params, TEXT("-TargetMesh="));
	const FString IncludeOptionalParam = LoadParam(Params, TEXT("-IncludeOptionalChains="));
	const bool bIncludeOptional = IncludeOptionalParam.IsEmpty() || IncludeOptionalParam.ToBool();
	const int32 MaxBoneDetails = 512;

	TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
	Report->SetStringField(TEXT("schemaVersion"), TEXT("1.0"));
	Report->SetStringField(TEXT("tool"), TEXT("retarget-analyze"));

	FString Error;
	USkeletalMesh* SourceMesh = SourceMeshParam.IsEmpty() ? nullptr : LoadSkeletalMesh(SourceMeshParam, Error);
	if (SourceMesh == nullptr)
	{
		Report->SetStringField(TEXT("error"), Error.IsEmpty() ? TEXT("-SourceMesh is required.") : Error);
	}
	USkeletalMesh* TargetMesh = TargetMeshParam.IsEmpty() ? nullptr : LoadSkeletalMesh(TargetMeshParam, Error);
	if (TargetMesh == nullptr)
	{
		Report->SetStringField(TEXT("error"), Error.IsEmpty() ? TEXT("-TargetMesh is required.") : Error);
	}

	if (SourceMesh != nullptr && TargetMesh != nullptr)
	{
		UEAgentKitRetarget::FRetargetCompatibilityReport Compatibility;
		if (UEAgentKitRetarget::AnalyzeRetargetCompatibility(
				SourceMesh,
				TargetMesh,
				bIncludeOptional,
				MaxBoneDetails,
				Compatibility,
				Error))
		{
			Report->SetField(TEXT("analysis"), MakeShared<FJsonValueObject>(UEAgentKitRetarget::CompatibilityReportToJson(Compatibility)));
		}
		else
		{
			Report->SetStringField(TEXT("error"), Error);
		}
	}

	FString SaveError;
	if (!SaveJsonObject(ReportPath, Report, SaveError))
	{
		UE_LOG(LogRetargetAnalyze, Error, TEXT("Could not write the analysis report: %s"), *SaveError);
		return 1;
	}
	UE_LOG(LogRetargetAnalyze, Display, TEXT("Retarget analysis report written to %s."), *ReportPath);
	return 0;
}
