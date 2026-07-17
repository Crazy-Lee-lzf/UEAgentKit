#include "AssetReaders/AssetReaderCommon.h"
#include "AssetReaders/AssetReaderImplementations.h"

namespace AssetReaderRegistryPrivate
{
	EAssetReaderStatus ReadMaterialFunction(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UMaterialFunction* Function = Cast<UMaterialFunction>(AssetData.GetAsset());
		if (Function == nullptr)
		{
			OutError = TEXT("Failed to load Material Function asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("material-function"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("description"), Function->Description);
		OutDetails->SetStringField(TEXT("caption"), Function->UserExposedCaption);
		OutDetails->SetBoolField(TEXT("exposeToLibrary"), Function->bExposeToLibrary);

		TArray<const UMaterialExpressionFunctionInput*> Inputs;
		TArray<const UMaterialExpressionFunctionOutput*> Outputs;
		TMap<FString, int32> ClassCounts;
		TArray<FString> CalledFunctions;
		for (const TObjectPtr<UMaterialExpression>& ExpressionPtr : Function->GetExpressions())
		{
			const UMaterialExpression* Expression = ExpressionPtr.Get();
			if (Expression == nullptr)
			{
				continue;
			}
			ClassCounts.FindOrAdd(Expression->GetClass()->GetPathName()) += 1;
			if (const UMaterialExpressionFunctionInput* Input = Cast<UMaterialExpressionFunctionInput>(Expression))
			{
				Inputs.Add(Input);
			}
			else if (const UMaterialExpressionFunctionOutput* Output = Cast<UMaterialExpressionFunctionOutput>(Expression))
			{
				Outputs.Add(Output);
			}
		}

		Inputs.Sort([](const UMaterialExpressionFunctionInput& Left, const UMaterialExpressionFunctionInput& Right)
		{
			if (Left.SortPriority != Right.SortPriority)
			{
				return Left.SortPriority < Right.SortPriority;
			}
			return Left.InputName.LexicalLess(Right.InputName);
		});
		Outputs.Sort([](const UMaterialExpressionFunctionOutput& Left, const UMaterialExpressionFunctionOutput& Right)
		{
			if (Left.SortPriority != Right.SortPriority)
			{
				return Left.SortPriority < Right.SortPriority;
			}
			return Left.OutputName.LexicalLess(Right.OutputName);
		});

		TArray<TSharedPtr<FJsonValue>> InputValues;
		for (const UMaterialExpressionFunctionInput* Input : Inputs)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Input->InputName.ToString());
			Json->SetStringField(TEXT("description"), Input->Description);
			Json->SetStringField(TEXT("id"), Input->Id.ToString(EGuidFormats::DigitsWithHyphensLower));
			Json->SetStringField(TEXT("inputType"), FunctionInputTypeToString(Input->InputType));
			Json->SetNumberField(TEXT("inputTypeValue"), static_cast<int32>(Input->InputType.GetValue()));
			Json->SetNumberField(TEXT("sortPriority"), Input->SortPriority);
			Json->SetBoolField(TEXT("usePreviewAsDefault"), Input->bUsePreviewValueAsDefault);
			Json->SetObjectField(
				TEXT("previewValue"),
				Vector4dToJson(FVector4d(Input->PreviewValue.X, Input->PreviewValue.Y, Input->PreviewValue.Z, Input->PreviewValue.W)));
			InputValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("inputCount"), InputValues.Num());
		OutDetails->SetArrayField(TEXT("inputs"), InputValues);

		TArray<TSharedPtr<FJsonValue>> OutputValues;
		for (const UMaterialExpressionFunctionOutput* Output : Outputs)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Output->OutputName.ToString());
			Json->SetStringField(TEXT("description"), Output->Description);
			Json->SetStringField(TEXT("id"), Output->Id.ToString(EGuidFormats::DigitsWithHyphensLower));
			Json->SetNumberField(TEXT("sortPriority"), Output->SortPriority);
			Json->SetBoolField(TEXT("lastPreviewed"), Output->bLastPreviewed);
			OutputValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("outputCount"), OutputValues.Num());
		OutDetails->SetArrayField(TEXT("outputs"), OutputValues);

		TArray<FString> ClassNames;
		ClassCounts.GetKeys(ClassNames);
		ClassNames.Sort();
		TArray<TSharedPtr<FJsonValue>> ExpressionClasses;
		for (const FString& ClassName : ClassNames)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("class"), ClassName);
			Json->SetNumberField(TEXT("count"), ClassCounts[ClassName]);
			ExpressionClasses.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("expressionCount"), Function->GetExpressions().Num());
		OutDetails->SetArrayField(TEXT("expressionClasses"), ExpressionClasses);
		OutDetails->SetNumberField(TEXT("commentCount"), Function->GetEditorComments().Num());
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadMaterial(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UMaterial* Material = Cast<UMaterial>(AssetData.GetAsset());
		if (Material == nullptr)
		{
			OutError = TEXT("Failed to load Material asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("material"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("domain"), MaterialDomainString(Material->MaterialDomain));
		OutDetails->SetNumberField(TEXT("domainValue"), static_cast<int32>(Material->MaterialDomain));
		OutDetails->SetStringField(TEXT("blendMode"), BlendModeToString(Material->GetBlendMode()));
		OutDetails->SetNumberField(TEXT("blendModeValue"), static_cast<int32>(Material->GetBlendMode()));
		OutDetails->SetBoolField(TEXT("twoSided"), Material->IsTwoSided());
		OutDetails->SetBoolField(TEXT("thinSurface"), Material->IsThinSurface());
		OutDetails->SetBoolField(TEXT("shadingModelFromExpression"), Material->IsShadingModelFromMaterialExpression());
		OutDetails->SetNumberField(TEXT("opacityMaskClipValue"), Material->GetOpacityMaskClipValue());
		OutDetails->SetStringField(TEXT("shadingModels"), GetShadingModelFieldString(Material->GetShadingModels()));

#if WITH_EDITOR
		TArray<UMaterialExpression*> Expressions;
		Material->GetAllReferencedExpressions(Expressions, nullptr);
		TMap<FString, int32> ClassCounts;
		for (const UMaterialExpression* Expression : Expressions)
		{
			if (Expression != nullptr)
			{
				ClassCounts.FindOrAdd(Expression->GetClass()->GetPathName()) += 1;
			}
		}
		TArray<FString> ClassNames;
		ClassCounts.GetKeys(ClassNames);
		ClassNames.Sort();
		TArray<TSharedPtr<FJsonValue>> ExpressionClasses;
		for (const FString& ClassName : ClassNames)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("class"), ClassName);
			Json->SetNumberField(TEXT("count"), ClassCounts[ClassName]);
			ExpressionClasses.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("expressionCount"), Expressions.Num());
		OutDetails->SetArrayField(TEXT("expressionClasses"), ExpressionClasses);
#else
		OutDetails->SetNumberField(TEXT("expressionCount"), 0);
		OutDetails->SetArrayField(TEXT("expressionClasses"), {});
#endif
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadMaterialInstance(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UMaterialInstance* Instance = Cast<UMaterialInstance>(AssetData.GetAsset());
		if (Instance == nullptr)
		{
			OutError = TEXT("Failed to load Material Instance asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("material-instance"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("parentPath"), ObjectPathOrEmpty(Instance->Parent));
		OutDetails->SetStringField(TEXT("blendMode"), BlendModeToString(Instance->GetBlendMode()));
		OutDetails->SetNumberField(TEXT("blendModeValue"), static_cast<int32>(Instance->GetBlendMode()));
		OutDetails->SetBoolField(TEXT("twoSided"), Instance->IsTwoSided());
		OutDetails->SetBoolField(TEXT("thinSurface"), Instance->IsThinSurface());
		OutDetails->SetNumberField(TEXT("opacityMaskClipValue"), Instance->GetOpacityMaskClipValue());
		OutDetails->SetStringField(TEXT("shadingModels"), GetShadingModelFieldString(Instance->GetShadingModels()));
		OutDetails->SetBoolField(TEXT("hasBasePropertyOverrides"), Instance->HasOverridenBaseProperties());

		TArray<const FScalarParameterValue*> Scalars;
		for (const FScalarParameterValue& Parameter : Instance->ScalarParameterValues) Scalars.Add(&Parameter);
		Scalars.Sort([](const FScalarParameterValue& Left, const FScalarParameterValue& Right)
		{
			return MaterialParameterSortKey(Left.ParameterInfo) < MaterialParameterSortKey(Right.ParameterInfo);
		});
		TArray<TSharedPtr<FJsonValue>> ScalarValues;
		for (const FScalarParameterValue* Parameter : Scalars)
		{
			TSharedRef<FJsonObject> Json = MaterialParameterInfoToJson(Parameter->ParameterInfo);
			Json->SetNumberField(TEXT("value"), Parameter->ParameterValue);
			ScalarValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("scalarParameterCount"), ScalarValues.Num());
		OutDetails->SetArrayField(TEXT("scalarParameters"), ScalarValues);

		TArray<const FVectorParameterValue*> Vectors;
		for (const FVectorParameterValue& Parameter : Instance->VectorParameterValues) Vectors.Add(&Parameter);
		Vectors.Sort([](const FVectorParameterValue& Left, const FVectorParameterValue& Right)
		{
			return MaterialParameterSortKey(Left.ParameterInfo) < MaterialParameterSortKey(Right.ParameterInfo);
		});
		TArray<TSharedPtr<FJsonValue>> VectorValues;
		for (const FVectorParameterValue* Parameter : Vectors)
		{
			TSharedRef<FJsonObject> Json = MaterialParameterInfoToJson(Parameter->ParameterInfo);
			Json->SetObjectField(TEXT("value"), LinearColorToJson(Parameter->ParameterValue));
			VectorValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("vectorParameterCount"), VectorValues.Num());
		OutDetails->SetArrayField(TEXT("vectorParameters"), VectorValues);

		TArray<const FDoubleVectorParameterValue*> DoubleVectors;
		for (const FDoubleVectorParameterValue& Parameter : Instance->DoubleVectorParameterValues) DoubleVectors.Add(&Parameter);
		DoubleVectors.Sort([](const FDoubleVectorParameterValue& Left, const FDoubleVectorParameterValue& Right)
		{
			return MaterialParameterSortKey(Left.ParameterInfo) < MaterialParameterSortKey(Right.ParameterInfo);
		});
		TArray<TSharedPtr<FJsonValue>> DoubleVectorValues;
		for (const FDoubleVectorParameterValue* Parameter : DoubleVectors)
		{
			TSharedRef<FJsonObject> Json = MaterialParameterInfoToJson(Parameter->ParameterInfo);
			Json->SetObjectField(TEXT("value"), Vector4dToJson(Parameter->ParameterValue));
			DoubleVectorValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("doubleVectorParameterCount"), DoubleVectorValues.Num());
		OutDetails->SetArrayField(TEXT("doubleVectorParameters"), DoubleVectorValues);

		TArray<const FTextureParameterValue*> Textures;
		for (const FTextureParameterValue& Parameter : Instance->TextureParameterValues) Textures.Add(&Parameter);
		Textures.Sort([](const FTextureParameterValue& Left, const FTextureParameterValue& Right)
		{
			return MaterialParameterSortKey(Left.ParameterInfo) < MaterialParameterSortKey(Right.ParameterInfo);
		});
		TArray<TSharedPtr<FJsonValue>> TextureValues;
		for (const FTextureParameterValue* Parameter : Textures)
		{
			TSharedRef<FJsonObject> Json = MaterialParameterInfoToJson(Parameter->ParameterInfo);
			Json->SetStringField(TEXT("valuePath"), ObjectPathOrEmpty(Parameter->ParameterValue));
			TextureValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("textureParameterCount"), TextureValues.Num());
		OutDetails->SetArrayField(TEXT("textureParameters"), TextureValues);

		TArray<const FFontParameterValue*> Fonts;
		for (const FFontParameterValue& Parameter : Instance->FontParameterValues) Fonts.Add(&Parameter);
		Fonts.Sort([](const FFontParameterValue& Left, const FFontParameterValue& Right)
		{
			return MaterialParameterSortKey(Left.ParameterInfo) < MaterialParameterSortKey(Right.ParameterInfo);
		});
		TArray<TSharedPtr<FJsonValue>> FontValues;
		for (const FFontParameterValue* Parameter : Fonts)
		{
			TSharedRef<FJsonObject> Json = MaterialParameterInfoToJson(Parameter->ParameterInfo);
			Json->SetStringField(TEXT("fontPath"), ObjectPathOrEmpty(Parameter->FontValue.Get()));
			Json->SetNumberField(TEXT("fontPage"), Parameter->FontPage);
			FontValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("fontParameterCount"), FontValues.Num());
		OutDetails->SetArrayField(TEXT("fontParameters"), FontValues);

		const FStaticParameterSet StaticParameters = Instance->GetStaticParameters();
		TArray<const FStaticSwitchParameter*> StaticSwitches;
		for (const FStaticSwitchParameter& Parameter : StaticParameters.StaticSwitchParameters) StaticSwitches.Add(&Parameter);
		StaticSwitches.Sort([](const FStaticSwitchParameter& Left, const FStaticSwitchParameter& Right)
		{
			return MaterialParameterSortKey(Left.ParameterInfo) < MaterialParameterSortKey(Right.ParameterInfo);
		});
		TArray<TSharedPtr<FJsonValue>> StaticSwitchValues;
		for (const FStaticSwitchParameter* Parameter : StaticSwitches)
		{
			TSharedRef<FJsonObject> Json = MaterialParameterInfoToJson(Parameter->ParameterInfo);
			Json->SetBoolField(TEXT("value"), Parameter->Value);
			Json->SetBoolField(TEXT("override"), Parameter->bOverride);
			StaticSwitchValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("staticSwitchParameterCount"), StaticSwitchValues.Num());
		OutDetails->SetArrayField(TEXT("staticSwitchParameters"), StaticSwitchValues);
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadTexture2D(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UTexture2D* Texture = Cast<UTexture2D>(AssetData.GetAsset());
		if (Texture == nullptr)
		{
			OutError = TEXT("Failed to load Texture2D asset.");
			return EAssetReaderStatus::Failed;
		}

		const int32 PlatformSizeX = Texture->GetSizeX();
		const int32 PlatformSizeY = Texture->GetSizeY();
		const int32 PlatformMipCount = Texture->GetNumMips();
		const EPixelFormat PixelFormat = Texture->GetPixelFormat();
		const bool bPlatformDataAvailable = PlatformSizeX > 0 && PlatformSizeY > 0 && PixelFormat != PF_Unknown;

		int64 SourceSizeX = 0;
		int64 SourceSizeY = 0;
		int32 SourceMipCount = 0;
		bool bSourceAvailable = false;
		TSharedRef<FJsonObject> Source = MakeShared<FJsonObject>();
#if WITH_EDITORONLY_DATA
		bSourceAvailable = Texture->Source.IsValid();
		SourceSizeX = Texture->Source.GetSizeX();
		SourceSizeY = Texture->Source.GetSizeY();
		SourceMipCount = Texture->Source.GetNumMips();
		const ETextureSourceFormat SourceFormat = Texture->Source.GetFormat();
		Source->SetBoolField(TEXT("available"), bSourceAvailable);
		Source->SetNumberField(TEXT("sizeX"), static_cast<double>(SourceSizeX));
		Source->SetNumberField(TEXT("sizeY"), static_cast<double>(SourceSizeY));
		Source->SetNumberField(TEXT("sliceCount"), Texture->Source.GetNumSlices());
		Source->SetNumberField(TEXT("mipCount"), SourceMipCount);
		Source->SetNumberField(TEXT("layerCount"), Texture->Source.GetNumLayers());
		Source->SetNumberField(TEXT("blockCount"), Texture->Source.GetNumBlocks());
		Source->SetStringField(TEXT("format"), EnumNameOrValue<ETextureSourceFormat>(SourceFormat));
		Source->SetNumberField(TEXT("formatValue"), static_cast<int32>(SourceFormat));
		Source->SetBoolField(TEXT("hdr"), FTextureSource::IsHDR(SourceFormat));
#else
		Source->SetBoolField(TEXT("available"), false);
#endif

		TSharedRef<FJsonObject> Platform = MakeShared<FJsonObject>();
		Platform->SetBoolField(TEXT("available"), bPlatformDataAvailable);
		Platform->SetNumberField(TEXT("sizeX"), PlatformSizeX);
		Platform->SetNumberField(TEXT("sizeY"), PlatformSizeY);
		Platform->SetNumberField(TEXT("mipCount"), PlatformMipCount);
		Platform->SetStringField(TEXT("pixelFormat"), GetPixelFormatString(PixelFormat));
		Platform->SetNumberField(TEXT("pixelFormatValue"), static_cast<int32>(PixelFormat));

		OutDetails->SetStringField(TEXT("type"), TEXT("texture-2d"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetObjectField(TEXT("source"), Source);
		OutDetails->SetObjectField(TEXT("platform"), Platform);
		OutDetails->SetNumberField(TEXT("sizeX"), static_cast<double>(bSourceAvailable ? SourceSizeX : PlatformSizeX));
		OutDetails->SetNumberField(TEXT("sizeY"), static_cast<double>(bSourceAvailable ? SourceSizeY : PlatformSizeY));
		OutDetails->SetNumberField(TEXT("mipCount"), bSourceAvailable ? SourceMipCount : PlatformMipCount);
		OutDetails->SetStringField(TEXT("pixelFormat"), GetPixelFormatString(PixelFormat));
		OutDetails->SetNumberField(TEXT("pixelFormatValue"), static_cast<int32>(PixelFormat));
		OutDetails->SetStringField(TEXT("compressionSettings"), EnumNameOrValue<TextureCompressionSettings>(Texture->CompressionSettings));
		OutDetails->SetNumberField(TEXT("compressionSettingsValue"), static_cast<int32>(Texture->CompressionSettings));
		OutDetails->SetBoolField(TEXT("srgb"), Texture->SRGB);
		OutDetails->SetStringField(TEXT("lodGroup"), EnumNameOrValue<TextureGroup>(Texture->LODGroup));
		OutDetails->SetNumberField(TEXT("lodGroupValue"), static_cast<int32>(Texture->LODGroup));
		OutDetails->SetStringField(TEXT("mipGenSettings"), UTexture::GetMipGenSettingsString(Texture->MipGenSettings));
		OutDetails->SetNumberField(TEXT("mipGenSettingsValue"), static_cast<int32>(Texture->MipGenSettings));
		OutDetails->SetStringField(TEXT("filter"), EnumNameOrValue<TextureFilter>(Texture->Filter));
		OutDetails->SetNumberField(TEXT("filterValue"), static_cast<int32>(Texture->Filter));
		OutDetails->SetStringField(TEXT("addressX"), EnumNameOrValue<TextureAddress>(Texture->AddressX));
		OutDetails->SetStringField(TEXT("addressY"), EnumNameOrValue<TextureAddress>(Texture->AddressY));
		OutDetails->SetBoolField(TEXT("neverStream"), Texture->NeverStream);
		OutDetails->SetBoolField(TEXT("globalForceMipLevelsResident"), Texture->bGlobalForceMipLevelsToBeResident);
		OutDetails->SetNumberField(TEXT("cinematicMipLevels"), Texture->NumCinematicMipLevels);
		OutDetails->SetBoolField(TEXT("virtualTextureStreaming"), Texture->VirtualTextureStreaming);
		OutDetails->SetBoolField(TEXT("requiresVirtualTexturing"), Texture->RequiresVirtualTexturing());
		OutDetails->SetNumberField(TEXT("virtualTexturePrefetchMips"), Texture->VirtualTexturePrefetchMips);
		return EAssetReaderStatus::Success;
	}
}
