#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "LiveWriteTransaction.h"

class UObject;
class UPackage;

namespace UEAgentKitLiveWrite
{
	enum class ELiveWriteTargetKind : uint8
	{
		Property,
		MaterialParameter,
		DataTableRow
	};

	enum class ELiveWriteAssetRequirement : uint8
	{
		None = 0,
		LoadedAsset = 1 << 0,
		OpenInEditor = 1 << 1,
		NonBlueprint = 1 << 2,
		ProjectContent = 1 << 3,
		NonMap = 1 << 4,
		CleanPackage = 1 << 5
	};
	ENUM_CLASS_FLAGS(ELiveWriteAssetRequirement);

	constexpr ELiveWriteAssetRequirement StandardAssetRequirements =
		ELiveWriteAssetRequirement::LoadedAsset
		| ELiveWriteAssetRequirement::OpenInEditor
		| ELiveWriteAssetRequirement::NonBlueprint
		| ELiveWriteAssetRequirement::ProjectContent
		| ELiveWriteAssetRequirement::NonMap
		| ELiveWriteAssetRequirement::CleanPackage;

	struct FLiveWriteOperationRequest
	{
		FString Operation;
		FString AssetPath;
		FString PropertyPath;
		FString ParameterName;
		FString RowName;
		FString NewRowName;
		FString FieldName;
		TSharedPtr<FJsonObject> Target;
		FString SessionId;
		TSharedPtr<FJsonValue> Value;
	};

	struct FLiveWriteOperationContext
	{
		UObject* Asset = nullptr;
		UPackage* Package = nullptr;
	};

	using FLiveWriteApplyFunction = bool (*)(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	struct FLiveWriteOperationDescriptor
	{
		FString Name;
		ELiveWriteTargetKind TargetKind = ELiveWriteTargetKind::Property;
		TArray<FString> RequiredTargetFields;
		ELiveWriteAssetRequirement AssetRequirements = StandardAssetRequirements;
		FLiveWriteApplyFunction Apply = nullptr;
	};

	class FLiveWriteOperationRegistry
	{
	public:
		static const FLiveWriteOperationRegistry& Get();

		const FLiveWriteOperationDescriptor* Find(const FString& Operation) const;
		const TArray<FString>& GetSupportedOperationNames() const;
		FString GetSupportedOperationSummary() const;

		void Register(const FLiveWriteOperationDescriptor& Descriptor);

	private:
		FLiveWriteOperationRegistry();

		TMap<FString, FLiveWriteOperationDescriptor> Operations;
		TArray<FString> SupportedOperationNames;
	};

	bool ValidateLiveWriteOperationRequest(
		const FLiveWriteOperationDescriptor& Descriptor,
		const FLiveWriteOperationRequest& Request,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	void RegisterPropertyLiveWriteOperations(FLiveWriteOperationRegistry& Registry);
	void RegisterMaterialLiveWriteOperations(FLiveWriteOperationRegistry& Registry);
	void RegisterDataTableLiveWriteOperations(FLiveWriteOperationRegistry& Registry);
}
