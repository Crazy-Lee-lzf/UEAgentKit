#include "LiveWriteOperationRegistry.h"
#include "LiveWriteOperationCommon.h"
#include "StructuredPropertyJson.h"

#include "Dom/JsonValue.h"
#include "Engine/Texture.h"
#include "MaterialEditingLibrary.h"
#include "Materials/MaterialExpressionScalarParameter.h"
#include "Materials/MaterialExpressionStaticSwitchParameter.h"
#include "Materials/MaterialExpressionTextureSampleParameter.h"
#include "Materials/MaterialExpressionVectorParameter.h"
#include "Materials/MaterialInstanceConstant.h"
#include "StaticParameterSet.h"

using namespace UEAgentKitLiveWrite;

namespace
{
	enum class ELiveMaterialParameterKind : uint8
	{
		Scalar,
		Vector,
		Texture,
		StaticSwitch
	};

	FString LiveMaterialParameterTypeName(const ELiveMaterialParameterKind Kind)
	{
		switch (Kind)
		{
		case ELiveMaterialParameterKind::Scalar:
			return TEXT("Scalar");
		case ELiveMaterialParameterKind::Vector:
			return TEXT("Vector");
		case ELiveMaterialParameterKind::Texture:
			return TEXT("Texture");
		default:
			return TEXT("StaticSwitch");
		}
	}

	// Mirrors the offline AssetPatchCommandlet helpers so the Live Editor Apply reads,
	// applies, and verifies Material Instance parameters exactly like the patch path.
	bool FindLiveGlobalScalarParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FGuid& OutExpressionGuid,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllScalarParameterInfo(ParameterInfos, ParameterIds);
		if (ParameterInfos.Num() != ParameterIds.Num())
		{
			OutError = TEXT("Scalar parameter metadata is inconsistent.");
			return false;
		}
		int32 MatchCount = 0;
		for (int32 Index = 0; Index < ParameterInfos.Num(); ++Index)
		{
			const FMaterialParameterInfo& Info = ParameterInfos[Index];
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				OutExpressionGuid = ParameterIds[Index];
				++MatchCount;
			}
		}
		if (MatchCount != 1)
		{
			OutError = FString::Printf(
				TEXT("Expected exactly one global scalar parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool FindLiveGlobalVectorParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FGuid& OutExpressionGuid,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllVectorParameterInfo(ParameterInfos, ParameterIds);
		if (ParameterInfos.Num() != ParameterIds.Num())
		{
			OutError = TEXT("Vector parameter metadata is inconsistent.");
			return false;
		}
		int32 MatchCount = 0;
		for (int32 Index = 0; Index < ParameterInfos.Num(); ++Index)
		{
			const FMaterialParameterInfo& Info = ParameterInfos[Index];
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				OutExpressionGuid = ParameterIds[Index];
				++MatchCount;
			}
		}
		if (MatchCount != 1)
		{
			OutError = FString::Printf(
				TEXT("Expected exactly one global vector parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool FindLiveGlobalTextureParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FGuid& OutExpressionGuid,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllTextureParameterInfo(ParameterInfos, ParameterIds);
		if (ParameterInfos.Num() != ParameterIds.Num())
		{
			OutError = TEXT("Texture parameter metadata is inconsistent.");
			return false;
		}
		int32 MatchCount = 0;
		for (int32 Index = 0; Index < ParameterInfos.Num(); ++Index)
		{
			const FMaterialParameterInfo& Info = ParameterInfos[Index];
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				OutExpressionGuid = ParameterIds[Index];
				++MatchCount;
			}
		}
		if (MatchCount != 1)
		{
			OutError = FString::Printf(
				TEXT("Expected exactly one global texture parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool FindLiveGlobalStaticSwitchParameter(
		UMaterialInstanceConstant* Instance,
		const FName ParameterName,
		FMaterialParameterInfo& OutInfo,
		FString& OutError)
	{
		TArray<FMaterialParameterInfo> ParameterInfos;
		TArray<FGuid> ParameterIds;
		Instance->GetAllStaticSwitchParameterInfo(ParameterInfos, ParameterIds);
		int32 MatchCount = 0;
		for (const FMaterialParameterInfo& Info : ParameterInfos)
		{
			if (Info.Name == ParameterName
				&& Info.Association == EMaterialParameterAssociation::GlobalParameter)
			{
				OutInfo = Info;
				++MatchCount;
			}
		}
		if (MatchCount != 1)
		{
			OutError = FString::Printf(
				TEXT("Expected exactly one global static switch parameter named %s; found %d."),
				*ParameterName.ToString(),
				MatchCount);
			return false;
		}
		return true;
	}

	bool ReadLiveStaticSwitchParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		bool& OutValue,
		FGuid& OutExpressionGuid,
		bool& OutOverride)
	{
		if (!Instance->GetStaticSwitchParameterValue(
			FHashedMaterialParameterInfo(ParameterInfo),
			OutValue,
			OutExpressionGuid,
			false))
		{
			return false;
		}

		const TArray<FStaticSwitchParameter> StaticParameters = Instance->GetStaticParameters().StaticSwitchParameters;
		int32 MatchCount = 0;
		for (const FStaticSwitchParameter& Parameter : StaticParameters)
		{
			if (Parameter.ParameterInfo == ParameterInfo)
			{
				OutOverride = Parameter.bOverride;
				++MatchCount;
			}
		}
		return MatchCount == 1;
	}

	template<typename TParameterValue>
	bool ReadLiveMaterialParameterMetadata(
		const TArray<TParameterValue>& Parameters,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		OutOverride = false;
		OutExpressionGuid = DefaultExpressionGuid;
		int32 MatchCount = 0;
		for (const TParameterValue& Parameter : Parameters)
		{
			if (Parameter.ParameterInfo == ParameterInfo)
			{
				OutOverride = true;
				OutExpressionGuid = Parameter.ExpressionGUID;
				++MatchCount;
			}
		}
		return MatchCount <= 1;
	}

	bool ReadLiveScalarParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		float& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetScalarParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadLiveMaterialParameterMetadata(
				Instance->ScalarParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	bool ReadLiveVectorParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		FLinearColor& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetVectorParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadLiveMaterialParameterMetadata(
				Instance->VectorParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	bool ReadLiveTextureParameter(
		UMaterialInstanceConstant* Instance,
		const FMaterialParameterInfo& ParameterInfo,
		const FGuid& DefaultExpressionGuid,
		UTexture*& OutValue,
		bool& OutOverride,
		FGuid& OutExpressionGuid)
	{
		return Instance->GetTextureParameterValue(FHashedMaterialParameterInfo(ParameterInfo), OutValue)
			&& ReadLiveMaterialParameterMetadata(
				Instance->TextureParameterValues,
				ParameterInfo,
				DefaultExpressionGuid,
				OutOverride,
				OutExpressionGuid);
	}

	TSharedRef<FJsonObject> MakeLiveMaterialVectorValue(const FLinearColor& Value)
	{
		const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetNumberField(TEXT("r"), Value.R);
		Result->SetNumberField(TEXT("g"), Value.G);
		Result->SetNumberField(TEXT("b"), Value.B);
		Result->SetNumberField(TEXT("a"), Value.A);
		return Result;
	}

	TSharedPtr<FJsonValue> MakeLiveMaterialTextureValue(const UTexture* Value)
	{
		if (Value != nullptr)
		{
			return MakeShared<FJsonValueString>(Value->GetPathName());
		}
		return MakeShared<FJsonValueNull>();
	}

	struct FLiveMaterialSnapshotState
	{
		bool bEntryPresent = false;
		float ScalarValue = 0.0f;
		FLinearColor VectorValue;
		TObjectPtr<UTexture> TextureValue = nullptr;
		bool bSwitchValue = false;
		bool bSwitchOverride = false;
		FGuid SwitchExpressionGuid;
	};

	class FLiveWriteMaterialIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		FLiveWriteMaterialIO(
			UMaterialInstanceConstant* InInstance,
			FName InParameterName,
			ELiveMaterialParameterKind InKind,
			FMaterialParameterInfo InParameterInfo,
			FGuid InExpressionGuid)
			: Instance(InInstance)
			, ParameterName(InParameterName)
			, Kind(InKind)
			, ParameterInfo(InParameterInfo)
			, ExpressionGuid(InExpressionGuid)
		{
		}

		bool CaptureSnapshot() override
		{
			FLiveMaterialSnapshotState State;
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				const int32 Index = Instance->ScalarParameterValues.IndexOfByPredicate(
					[&](const FScalarParameterValue& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.ScalarValue = Instance->ScalarParameterValues[Index].ParameterValue;
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Vector)
			{
				const int32 Index = Instance->VectorParameterValues.IndexOfByPredicate(
					[&](const FVectorParameterValue& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.VectorValue = Instance->VectorParameterValues[Index].ParameterValue;
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Texture)
			{
				const int32 Index = Instance->TextureParameterValues.IndexOfByPredicate(
					[&](const FTextureParameterValue& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.TextureValue = Instance->TextureParameterValues[Index].ParameterValue;
				}
			}
			else
			{
				const TArray<FStaticSwitchParameter> SwitchParameters = Instance->GetStaticParameters().StaticSwitchParameters;
				const int32 Index = SwitchParameters.IndexOfByPredicate(
					[&](const FStaticSwitchParameter& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				State.bEntryPresent = Index != INDEX_NONE;
				if (Index != INDEX_NONE)
				{
					State.bSwitchValue = SwitchParameters[Index].Value;
					State.bSwitchOverride = SwitchParameters[Index].bOverride;
					State.SwitchExpressionGuid = SwitchParameters[Index].ExpressionGUID;
				}
			}
			SnapshotState = State;
			bSnapshotValid = true;
			return true;
		}

		bool IsSnapshotValid() const override
		{
			return bSnapshotValid;
		}

		void RestoreSnapshot() override
		{
			if (!bSnapshotValid)
			{
				return;
			}
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				if (SnapshotState.bEntryPresent)
				{
					UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
						Instance,
						ParameterName,
						SnapshotState.ScalarValue,
						EMaterialParameterAssociation::GlobalParameter);
				}
				else
				{
					Instance->ScalarParameterValues.RemoveAll(
						[&](const FScalarParameterValue& Parameter)
						{
							return Parameter.ParameterInfo == ParameterInfo;
						});
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Vector)
			{
				if (SnapshotState.bEntryPresent)
				{
					UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
						Instance,
						ParameterName,
						SnapshotState.VectorValue,
						EMaterialParameterAssociation::GlobalParameter);
				}
				else
				{
					Instance->VectorParameterValues.RemoveAll(
						[&](const FVectorParameterValue& Parameter)
						{
							return Parameter.ParameterInfo == ParameterInfo;
						});
				}
			}
			else if (Kind == ELiveMaterialParameterKind::Texture)
			{
				if (SnapshotState.bEntryPresent)
				{
					UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
						Instance,
						ParameterName,
						SnapshotState.TextureValue,
						EMaterialParameterAssociation::GlobalParameter);
				}
				else
				{
					Instance->TextureParameterValues.RemoveAll(
						[&](const FTextureParameterValue& Parameter)
						{
							return Parameter.ParameterInfo == ParameterInfo;
						});
				}
			}
			else
			{
				FMaterialInstanceParameterUpdateContext UpdateContext(Instance);
				FStaticParameterSet& StaticParameters = UpdateContext.GetStaticParameters();
				const int32 Index = StaticParameters.StaticSwitchParameters.IndexOfByPredicate(
					[&](const FStaticSwitchParameter& Parameter)
					{
						return Parameter.ParameterInfo == ParameterInfo;
					});
				if (SnapshotState.bEntryPresent)
				{
					if (Index != INDEX_NONE)
					{
						FStaticSwitchParameter& Entry = StaticParameters.StaticSwitchParameters[Index];
						Entry.Value = SnapshotState.bSwitchValue;
						Entry.bOverride = SnapshotState.bSwitchOverride;
						Entry.ExpressionGUID = SnapshotState.SwitchExpressionGuid;
					}
				}
				else if (Index != INDEX_NONE)
				{
					StaticParameters.StaticSwitchParameters.RemoveAt(Index);
				}
			}
		}

		void ReleaseSnapshot() override
		{
			bSnapshotValid = false;
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				float Value = 0.0f;
				bool bOverride = false;
				FGuid ValueExpressionGuid;
				if (!ReadLiveScalarParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						Value,
						bOverride,
						ValueExpressionGuid))
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("The material scalar parameter could not be read before the write.");
					return false;
				}
				OutValue = MakeShared<FJsonValueNumber>(Value);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Vector)
			{
				FLinearColor Value;
				bool bOverride = false;
				FGuid ValueExpressionGuid;
				if (!ReadLiveVectorParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						Value,
						bOverride,
						ValueExpressionGuid))
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("The material vector parameter could not be read before the write.");
					return false;
				}
				OutValue = MakeShared<FJsonValueObject>(MakeLiveMaterialVectorValue(Value));
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Texture)
			{
				UTexture* Value = nullptr;
				bool bOverride = false;
				FGuid ValueExpressionGuid;
				if (!ReadLiveTextureParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						Value,
						bOverride,
						ValueExpressionGuid))
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("The material texture parameter could not be read before the write.");
					return false;
				}
				OutValue = MakeLiveMaterialTextureValue(Value);
				return true;
			}
			bool bSwitchValue = false;
			FGuid SwitchExpressionGuid;
			bool bOverride = false;
			if (!ReadLiveStaticSwitchParameter(
					Instance,
					ParameterInfo,
					bSwitchValue,
					SwitchExpressionGuid,
					bOverride))
			{
				OutErrorCode = TEXT("live-editor-write-material-apply-failed");
				OutErrorMessage = TEXT("The material static switch parameter could not be read before the write.");
				return false;
			}
			OutValue = MakeShared<FJsonValueBoolean>(bSwitchValue);
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				double Number = 0.0;
				if (!Value.IsValid()
					|| !Value->TryGetNumber(Number)
					|| !FMath::IsFinite(Number)
					|| FMath::Abs(Number) > static_cast<double>(FLT_MAX))
				{
					OutErrorCode = TEXT("live-editor-write-material-value-invalid");
					OutErrorMessage = TEXT("Material scalar parameters require a finite JSON number.");
					return false;
				}
				UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
					Instance,
					ParameterName,
					static_cast<float>(Number),
					EMaterialParameterAssociation::GlobalParameter);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Vector)
			{
				double R = 0.0;
				double G = 0.0;
				double B = 0.0;
				double A = 0.0;
				const TSharedPtr<FJsonObject> Color = Value.IsValid() ? Value->AsObject() : nullptr;
				if (!Color.IsValid()
					|| Color->Values.Num() != 4
					|| !Color->TryGetNumberField(TEXT("r"), R)
					|| !Color->TryGetNumberField(TEXT("g"), G)
					|| !Color->TryGetNumberField(TEXT("b"), B)
					|| !Color->TryGetNumberField(TEXT("a"), A)
					|| !FMath::IsFinite(R) || !FMath::IsFinite(G)
					|| !FMath::IsFinite(B) || !FMath::IsFinite(A)
					|| FMath::Abs(R) > static_cast<double>(FLT_MAX)
					|| FMath::Abs(G) > static_cast<double>(FLT_MAX)
					|| FMath::Abs(B) > static_cast<double>(FLT_MAX)
					|| FMath::Abs(A) > static_cast<double>(FLT_MAX))
				{
					OutErrorCode = TEXT("live-editor-write-material-value-invalid");
					OutErrorMessage = TEXT("Material vector parameters require finite r, g, b, and a floats.");
					return false;
				}
				UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
					Instance,
					ParameterName,
					FLinearColor(
						static_cast<float>(R),
						static_cast<float>(G),
						static_cast<float>(B),
						static_cast<float>(A)),
					EMaterialParameterAssociation::GlobalParameter);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Texture)
			{
				FString TexturePath;
				if (!Value.IsValid() || !Value->TryGetString(TexturePath) || TexturePath.IsEmpty())
				{
					OutErrorCode = TEXT("live-editor-write-material-value-invalid");
					OutErrorMessage = TEXT("Material texture parameters require an object path string.");
					return false;
				}
				UTexture* Texture = LoadObject<UTexture>(nullptr, *TexturePath);
				if (Texture == nullptr)
				{
					OutErrorCode = TEXT("live-editor-write-material-texture-invalid");
					OutErrorMessage = FString::Printf(
						TEXT("The material texture asset could not be loaded: %s"),
						*TexturePath);
					return false;
				}
				UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
					Instance,
					ParameterName,
					Texture,
					EMaterialParameterAssociation::GlobalParameter);
				return true;
			}
			bool bSwitchValue = false;
			if (!Value.IsValid() || !Value->TryGetBool(bSwitchValue))
			{
				OutErrorCode = TEXT("live-editor-write-material-value-invalid");
				OutErrorMessage = TEXT("Material static switch parameters require a JSON boolean.");
				return false;
			}
			UMaterialEditingLibrary::SetMaterialInstanceStaticSwitchParameterValue(
				Instance,
				ParameterName,
				bSwitchValue,
				EMaterialParameterAssociation::GlobalParameter,
				true);
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Kind == ELiveMaterialParameterKind::Scalar)
			{
				double RequestedNumber = 0.0;
				float AfterValue = 0.0f;
				bool bAfterOverride = false;
				FGuid AfterExpressionGuid;
				if (!Requested.IsValid()
					|| !Requested->TryGetNumber(RequestedNumber)
					|| !ReadLiveScalarParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						AfterValue,
						bAfterOverride,
						AfterExpressionGuid)
					|| !FMath::IsNearlyEqual(AfterValue, static_cast<float>(RequestedNumber), UE_SMALL_NUMBER)
					|| !bAfterOverride
					|| AfterExpressionGuid != ExpressionGuid)
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("Material scalar parameter read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueNumber>(AfterValue);
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Vector)
			{
				const TSharedPtr<FJsonObject> RequestedColor = Requested.IsValid() ? Requested->AsObject() : nullptr;
				FLinearColor AfterValue;
				bool bAfterOverride = false;
				FGuid AfterExpressionGuid;
				if (!RequestedColor.IsValid()
					|| !ReadLiveVectorParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						AfterValue,
						bAfterOverride,
						AfterExpressionGuid)
					|| !AfterValue.Equals(
						FLinearColor(
							static_cast<float>(RequestedColor->GetNumberField(TEXT("r"))),
							static_cast<float>(RequestedColor->GetNumberField(TEXT("g"))),
							static_cast<float>(RequestedColor->GetNumberField(TEXT("b"))),
							static_cast<float>(RequestedColor->GetNumberField(TEXT("a")))),
						UE_SMALL_NUMBER)
					|| !bAfterOverride
					|| AfterExpressionGuid != ExpressionGuid)
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("Material vector parameter read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueObject>(MakeLiveMaterialVectorValue(AfterValue));
				return true;
			}
			if (Kind == ELiveMaterialParameterKind::Texture)
			{
				FString RequestedPath;
				UTexture* AfterValue = nullptr;
				bool bAfterOverride = false;
				FGuid AfterExpressionGuid;
				UTexture* RequestedTexture = Requested.IsValid()
					? LoadObject<UTexture>(nullptr, *Requested->AsString())
					: nullptr;
				if (!Requested.IsValid()
					|| !Requested->TryGetString(RequestedPath)
					|| RequestedTexture == nullptr
					|| !ReadLiveTextureParameter(
						Instance,
						ParameterInfo,
						ExpressionGuid,
						AfterValue,
						bAfterOverride,
						AfterExpressionGuid)
					|| AfterValue != RequestedTexture
					|| !bAfterOverride
					|| AfterExpressionGuid != ExpressionGuid)
				{
					OutErrorCode = TEXT("live-editor-write-material-apply-failed");
					OutErrorMessage = TEXT("Material texture parameter read-back verification failed.");
					return false;
				}
				OutValue = MakeLiveMaterialTextureValue(AfterValue);
				return true;
			}
			bool bRequestedSwitch = false;
			bool bAfterSwitch = false;
			FGuid AfterExpressionGuid;
			bool bAfterOverride = false;
			FGuid BeforeSwitchGuid = SnapshotState.bEntryPresent
				? SnapshotState.SwitchExpressionGuid
				: ExpressionGuid;
			if (!Requested.IsValid()
				|| !Requested->TryGetBool(bRequestedSwitch)
				|| !ReadLiveStaticSwitchParameter(
					Instance,
					ParameterInfo,
					bAfterSwitch,
					AfterExpressionGuid,
					bAfterOverride)
				|| bAfterSwitch != bRequestedSwitch
				|| AfterExpressionGuid != BeforeSwitchGuid
				|| !bAfterOverride)
			{
				OutErrorCode = TEXT("live-editor-write-material-apply-failed");
				OutErrorMessage = TEXT("Material static switch parameter read-back verification failed.");
				return false;
			}
			OutValue = MakeShared<FJsonValueBoolean>(bAfterSwitch);
			return true;
		}

		bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) override
		{
			return UEAgentKit::StructuredPropertyJson::JsonEqual(Left, Right);
		}

		void NotifyChanged() override
		{
		}

		void NotifyRestored() override
		{
			UMaterialEditingLibrary::UpdateMaterialInstance(Instance);
		}

	private:
		UMaterialInstanceConstant* Instance = nullptr;
		FName ParameterName;
		ELiveMaterialParameterKind Kind = ELiveMaterialParameterKind::Scalar;
		FMaterialParameterInfo ParameterInfo;
		FGuid ExpressionGuid;
		FLiveMaterialSnapshotState SnapshotState;
		bool bSnapshotValid = false;
	};

	bool TryApplyMaterialParameterLive(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& ParameterName,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		const ELiveMaterialParameterKind Kind,
		const FString& Operation,
		TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UMaterialInstanceConstant* MaterialInstance = Cast<UMaterialInstanceConstant>(Asset);
		if (MaterialInstance == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-material-instance-required");
			OutErrorMessage = TEXT("Material parameter writes require a loaded MaterialInstanceConstant asset.");
			return false;
		}
		if (ParameterName.IsEmpty() || ParameterName.Len() > 256 || ParameterName.Contains(TEXT(".")))
		{
			OutErrorCode = TEXT("live-editor-write-material-parameter-invalid");
			OutErrorMessage = TEXT("parameterName must be a non-empty name without dots.");
			return false;
		}
		if (!Value.IsValid())
		{
			OutErrorCode = TEXT("live-editor-write-material-value-invalid");
			OutErrorMessage = TEXT("Material parameter value is required.");
			return false;
		}

		const FName ParameterFName = FName(*ParameterName);
		FMaterialParameterInfo ParameterInfo;
		FGuid ParameterExpressionGuid;
		FString ResolveError;
		bool bResolved = false;
		switch (Kind)
		{
		case ELiveMaterialParameterKind::Scalar:
			bResolved = FindLiveGlobalScalarParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ParameterExpressionGuid,
				ResolveError);
			break;
		case ELiveMaterialParameterKind::Vector:
			bResolved = FindLiveGlobalVectorParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ParameterExpressionGuid,
				ResolveError);
			break;
		case ELiveMaterialParameterKind::Texture:
			bResolved = FindLiveGlobalTextureParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ParameterExpressionGuid,
				ResolveError);
			break;
		default:
			bResolved = FindLiveGlobalStaticSwitchParameter(
				MaterialInstance,
				ParameterFName,
				ParameterInfo,
				ResolveError);
			break;
		}
		if (!bResolved)
		{
			OutErrorCode = TEXT("live-editor-write-material-parameter-not-found");
			OutErrorMessage = TEXT("Material parameter was not found on the loaded asset: ") + ResolveError;
			return false;
		}

		const FString ParameterType = LiveMaterialParameterTypeName(Kind);
		FString ValueKind;
		switch (Kind)
		{
		case ELiveMaterialParameterKind::Scalar:
			ValueKind = TEXT("material-scalar");
			break;
		case ELiveMaterialParameterKind::Vector:
			ValueKind = TEXT("material-vector");
			break;
		case ELiveMaterialParameterKind::Texture:
			ValueKind = TEXT("material-texture");
			break;
		default:
			ValueKind = TEXT("material-static-switch");
			break;
		}

		TUniquePtr<UEAgentKitLiveWrite::ILiveWriteValueIO> IO = MakeUnique<FLiveWriteMaterialIO>(MaterialInstance, ParameterFName, Kind, ParameterInfo, ParameterExpressionGuid);
		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.SessionId = SessionId;
		Context.TransactionTitle = TEXT("UE Agent Kit: Set Material Instance Parameter");
		Context.AssetPath = AssetPath;
		Context.PropertyPath = ParameterName;
		Context.Value = Value;

		UEAgentKitLiveWrite::FLiveWriteEvidence Evidence;
		if (!UEAgentKitLiveWrite::RunLiveWriteTransaction(Context, IO, Evidence, OutErrorCode, OutErrorMessage))
		{
			return false;
		}
		OutRecord = BuildLiveWriteTransactionRecord(
			Asset,
			Package,
			AssetPath,
			Operation,
			ValueKind,
			SessionId,
			Evidence,
			IO);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		UEAgentKitLiveWrite::FillLiveWriteEvidence(
			Result,
			Context,
			Evidence,
			Operation,
			ValueKind,
			TEXT("MaterialInstanceParameter"),
			true,
			true);
		Result->RemoveField(TEXT("propertyPath"));
		Result->SetStringField(TEXT("parameterName"), ParameterName);
		Result->SetStringField(TEXT("parameterType"), ParameterType);
		Result->SetStringField(TEXT("parameterAssociation"), TEXT("Global"));
		OutResult = Result;
		return true;
	}

	bool ApplyMaterialOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		const ELiveMaterialParameterKind Kind,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		return TryApplyMaterialParameterLive(
			Context.Asset, Context.Package, Request.AssetPath, Request.ParameterName,
			Request.Value, Request.SessionId, Kind, Request.Operation,
			OutRecord, OutResult, OutErrorCode, OutErrorMessage);
	}

#define UEAK_DEFINE_MATERIAL_APPLY(Name, KindValue) \
	bool Name( \
		const FLiveWriteOperationContext& Context, \
		const FLiveWriteOperationRequest& Request, \
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord, \
		TSharedPtr<FJsonObject>& OutResult, \
		FString& OutErrorCode, \
		FString& OutErrorMessage) \
	{ \
		return ApplyMaterialOperation(Context, Request, KindValue, OutRecord, OutResult, OutErrorCode, OutErrorMessage); \
	}

	UEAK_DEFINE_MATERIAL_APPLY(ApplyMaterialScalarOperation, ELiveMaterialParameterKind::Scalar)
	UEAK_DEFINE_MATERIAL_APPLY(ApplyMaterialVectorOperation, ELiveMaterialParameterKind::Vector)
	UEAK_DEFINE_MATERIAL_APPLY(ApplyMaterialTextureOperation, ELiveMaterialParameterKind::Texture)
	UEAK_DEFINE_MATERIAL_APPLY(ApplyMaterialStaticSwitchOperation, ELiveMaterialParameterKind::StaticSwitch)

#undef UEAK_DEFINE_MATERIAL_APPLY
}

namespace UEAgentKitLiveWrite
{
	void RegisterMaterialLiveWriteOperations(FLiveWriteOperationRegistry& Registry)
	{
		Registry.Register({TEXT("setMaterialInstanceScalarParameter"), ELiveWriteTargetKind::MaterialParameter,
			{TEXT("parameterName")}, StandardAssetRequirements, &ApplyMaterialScalarOperation});
		Registry.Register({TEXT("setMaterialInstanceVectorParameter"), ELiveWriteTargetKind::MaterialParameter,
			{TEXT("parameterName")}, StandardAssetRequirements, &ApplyMaterialVectorOperation});
		Registry.Register({TEXT("setMaterialInstanceTextureParameter"), ELiveWriteTargetKind::MaterialParameter,
			{TEXT("parameterName")}, StandardAssetRequirements, &ApplyMaterialTextureOperation});
		Registry.Register({TEXT("setMaterialInstanceStaticSwitchParameter"), ELiveWriteTargetKind::MaterialParameter,
			{TEXT("parameterName")}, StandardAssetRequirements, &ApplyMaterialStaticSwitchOperation});
	}
}
