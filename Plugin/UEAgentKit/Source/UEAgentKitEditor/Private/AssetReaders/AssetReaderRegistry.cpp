#include "AssetReaders/AssetReaderRegistry.h"

#include "Animation/MorphTarget.h"
#include "Animation/AnimCompositeBase.h"
#include "Animation/AnimCurveTypes.h"
#include "Animation/AnimMontage.h"
#include "Animation/AnimNotifies/AnimNotify.h"
#include "Animation/AnimNotifies/AnimNotifyState.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSequenceBase.h"
#include "Animation/AnimTypes.h"
#include "Animation/BlendSpace.h"
#include "Animation/Skeleton.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/SkeletalMeshSocket.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshSocket.h"
#include "Materials/MaterialInterface.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"
#include "Materials/MaterialInstance.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Materials/MaterialFunction.h"
#include "Materials/MaterialExpressionFunctionInput.h"
#include "Materials/MaterialExpressionFunctionOutput.h"
#include "MaterialDomain.h"
#include "MaterialShaderType.h"
#include "PixelFormat.h"
#include "StaticParameterSet.h"
#include "Engine/DataAsset.h"
#include "Engine/DataTable.h"
#include "Engine/Font.h"
#include "Engine/Texture2D.h"
#include "PhysicsEngine/AggregateGeom.h"
#include "PhysicsEngine/BodySetup.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/PhysicsConstraintTemplate.h"
#include "PhysicsEngine/SkeletalBodySetup.h"
#include "ReferenceSkeleton.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "JsonObjectConverter.h"
#include "NiagaraDataInterface.h"
#include "NiagaraEmitter.h"
#include "NiagaraEmitterHandle.h"
#include "NiagaraParameterStore.h"
#include "NiagaraRendererProperties.h"
#include "NiagaraScript.h"
#include "NiagaraSystem.h"
#include "UObject/UnrealType.h"

namespace AssetReaderRegistryPrivate
{
	TSharedRef<FJsonObject> VectorToJson(const FVector& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("x"), Value.X);
		Json->SetNumberField(TEXT("y"), Value.Y);
		Json->SetNumberField(TEXT("z"), Value.Z);
		return Json;
	}

	TSharedRef<FJsonObject> RotatorToJson(const FRotator& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("pitch"), Value.Pitch);
		Json->SetNumberField(TEXT("yaw"), Value.Yaw);
		Json->SetNumberField(TEXT("roll"), Value.Roll);
		return Json;
	}

	TSharedRef<FJsonObject> QuatToJson(const FQuat& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("x"), Value.X);
		Json->SetNumberField(TEXT("y"), Value.Y);
		Json->SetNumberField(TEXT("z"), Value.Z);
		Json->SetNumberField(TEXT("w"), Value.W);
		return Json;
	}

	TSharedRef<FJsonObject> TransformToJson(const FTransform& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetObjectField(TEXT("translation"), VectorToJson(Value.GetTranslation()));
		Json->SetObjectField(TEXT("rotation"), QuatToJson(Value.GetRotation()));
		Json->SetObjectField(TEXT("scale"), VectorToJson(Value.GetScale3D()));
		return Json;
	}

	TSharedRef<FJsonObject> BoundsToJson(const FBoxSphereBounds& Bounds)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetObjectField(TEXT("origin"), VectorToJson(Bounds.Origin));
		Json->SetObjectField(TEXT("boxExtent"), VectorToJson(Bounds.BoxExtent));
		Json->SetNumberField(TEXT("sphereRadius"), Bounds.SphereRadius);
		return Json;
	}


	TSharedRef<FJsonObject> BoxToJson(const FBox& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetBoolField(TEXT("valid"), Value.IsValid != 0);
		Json->SetObjectField(TEXT("min"), VectorToJson(Value.Min));
		Json->SetObjectField(TEXT("max"), VectorToJson(Value.Max));
		Json->SetObjectField(TEXT("center"), VectorToJson(Value.GetCenter()));
		Json->SetObjectField(TEXT("size"), VectorToJson(Value.GetSize()));
		return Json;
	}

	bool GetReflectedBool(const UObject* Object, const FName PropertyName, const bool DefaultValue = false)
	{
		if (Object == nullptr)
		{
			return DefaultValue;
		}
		const FBoolProperty* Property = FindFProperty<FBoolProperty>(Object->GetClass(), PropertyName);
		return Property != nullptr ? Property->GetPropertyValue_InContainer(Object) : DefaultValue;
	}

	int32 GetReflectedInt(const UObject* Object, const FName PropertyName, const int32 DefaultValue = 0)
	{
		if (Object == nullptr)
		{
			return DefaultValue;
		}
		const FNumericProperty* Property = FindFProperty<FNumericProperty>(Object->GetClass(), PropertyName);
		return Property != nullptr && Property->IsInteger()
			? static_cast<int32>(Property->GetSignedIntPropertyValue(Property->ContainerPtrToValuePtr<void>(Object)))
			: DefaultValue;
	}


	int32 GetReflectedArrayCount(const UObject* Object, const FName PropertyName)
	{
		if (Object == nullptr)
		{
			return 0;
		}
		const FArrayProperty* Property = FindFProperty<FArrayProperty>(Object->GetClass(), PropertyName);
		if (Property == nullptr)
		{
			return 0;
		}
		const void* ArrayAddress = Property->ContainerPtrToValuePtr<void>(Object);
		return FScriptArrayHelper(Property, ArrayAddress).Num();
	}

	TArray<UObject*> GetReflectedObjectArray(const UObject* Object, const FName PropertyName)
	{
		TArray<UObject*> Result;
		if (Object == nullptr)
		{
			return Result;
		}
		const FArrayProperty* Property = FindFProperty<FArrayProperty>(Object->GetClass(), PropertyName);
		const FObjectPropertyBase* InnerProperty = Property != nullptr ? CastField<FObjectPropertyBase>(Property->Inner) : nullptr;
		if (Property == nullptr || InnerProperty == nullptr)
		{
			return Result;
		}
		const void* ArrayAddress = Property->ContainerPtrToValuePtr<void>(Object);
		FScriptArrayHelper Helper(Property, ArrayAddress);
		for (int32 Index = 0; Index < Helper.Num(); ++Index)
		{
			if (UObject* Value = InnerProperty->GetObjectPropertyValue(Helper.GetRawPtr(Index)))
			{
				Result.Add(Value);
			}
		}
		return Result;
	}

	TSharedRef<FJsonObject> GetReflectedBox(const UObject* Object, const FName PropertyName)
	{
		if (Object != nullptr)
		{
			const FStructProperty* Property = FindFProperty<FStructProperty>(Object->GetClass(), PropertyName);
			if (Property != nullptr && Property->Struct != nullptr && Property->Struct->GetName() == TEXT("Box"))
			{
				return BoxToJson(*Property->ContainerPtrToValuePtr<FBox>(Object));
			}
		}
		return BoxToJson(FBox());
	}

	FString ObjectPathOrEmpty(const UObject* Object)
	{
		return Object != nullptr ? Object->GetPathName() : FString();
	}

	TSharedRef<FJsonObject> LinearColorToJson(const FLinearColor& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("r"), Value.R);
		Json->SetNumberField(TEXT("g"), Value.G);
		Json->SetNumberField(TEXT("b"), Value.B);
		Json->SetNumberField(TEXT("a"), Value.A);
		return Json;
	}

	TSharedRef<FJsonObject> Vector4dToJson(const FVector4d& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("x"), Value.X);
		Json->SetNumberField(TEXT("y"), Value.Y);
		Json->SetNumberField(TEXT("z"), Value.Z);
		Json->SetNumberField(TEXT("w"), Value.W);
		return Json;
	}

	const TCHAR* BlendModeToString(const EBlendMode Value)
	{
		switch (Value)
		{
		case BLEND_Opaque:
			return TEXT("Opaque");
		case BLEND_Masked:
			return TEXT("Masked");
		case BLEND_Translucent:
			return TEXT("Translucent");
		case BLEND_Additive:
			return TEXT("Additive");
		case BLEND_Modulate:
			return TEXT("Modulate");
		case BLEND_AlphaComposite:
			return TEXT("AlphaComposite");
		case BLEND_AlphaHoldout:
			return TEXT("AlphaHoldout");
		case BLEND_TranslucentColoredTransmittance:
			return TEXT("TranslucentColoredTransmittance");
		default:
			return TEXT("Unknown");
		}
	}

	const TCHAR* FunctionInputTypeToString(const EFunctionInputType Value)
	{
		switch (Value)
		{
		case FunctionInput_Scalar:
			return TEXT("Scalar");
		case FunctionInput_Vector2:
			return TEXT("Vector2");
		case FunctionInput_Vector3:
			return TEXT("Vector3");
		case FunctionInput_Vector4:
			return TEXT("Vector4");
		case FunctionInput_Texture2D:
			return TEXT("Texture2D");
		case FunctionInput_TextureCube:
			return TEXT("TextureCube");
		case FunctionInput_Texture2DArray:
			return TEXT("Texture2DArray");
		case FunctionInput_VolumeTexture:
			return TEXT("VolumeTexture");
		case FunctionInput_StaticBool:
			return TEXT("StaticBool");
		case FunctionInput_MaterialAttributes:
			return TEXT("MaterialAttributes");
		case FunctionInput_TextureExternal:
			return TEXT("TextureExternal");
		case FunctionInput_Bool:
			return TEXT("Bool");
		case FunctionInput_Substrate:
			return TEXT("Substrate");
		default:
			return TEXT("Unknown");
		}
	}

	const TCHAR* ParameterAssociationToString(const EMaterialParameterAssociation Association)
	{
		switch (Association)
		{
		case EMaterialParameterAssociation::GlobalParameter:
			return TEXT("global");
		case EMaterialParameterAssociation::LayerParameter:
			return TEXT("layer");
		case EMaterialParameterAssociation::BlendParameter:
			return TEXT("blend");
		default:
			return TEXT("unknown");
		}
	}

	TSharedRef<FJsonObject> MaterialParameterInfoToJson(const FMaterialParameterInfo& Info)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetStringField(TEXT("name"), Info.Name.ToString());
		Json->SetStringField(TEXT("association"), ParameterAssociationToString(Info.Association));
		Json->SetNumberField(TEXT("associationValue"), static_cast<int32>(Info.Association));
		Json->SetNumberField(TEXT("index"), Info.Index);
		return Json;
	}

	FString MaterialParameterSortKey(const FMaterialParameterInfo& Info)
	{
		return FString::Printf(
			TEXT("%s|%d|%08d"),
			*Info.Name.ToString(),
			static_cast<int32>(Info.Association),
			Info.Index);
	}

	template <typename EnumType>
	FString EnumNameOrValue(const EnumType Value)
	{
		if (const UEnum* Enum = StaticEnum<EnumType>())
		{
			return Enum->GetNameStringByValue(static_cast<int64>(Value));
		}
		return FString::FromInt(static_cast<int32>(Value));
	}

	TSharedRef<FJsonObject> FrameRateToJson(const FFrameRate& Value)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetNumberField(TEXT("numerator"), Value.Numerator);
		Json->SetNumberField(TEXT("denominator"), Value.Denominator);
		Json->SetNumberField(TEXT("decimal"), Value.IsValid() ? Value.AsDecimal() : 0.0);
		return Json;
	}

	const TCHAR* AdditiveAnimationTypeToString(const EAdditiveAnimationType Value)
	{
		switch (Value)
		{
		case AAT_None: return TEXT("None");
		case AAT_LocalSpaceBase: return TEXT("LocalSpaceBase");
		case AAT_RotationOffsetMeshSpace: return TEXT("RotationOffsetMeshSpace");
		default: return TEXT("Unknown");
		}
	}

	const TCHAR* AdditiveBasePoseTypeToString(const EAdditiveBasePoseType Value)
	{
		switch (Value)
		{
		case ABPT_None: return TEXT("None");
		case ABPT_RefPose: return TEXT("ReferencePose");
		case ABPT_AnimScaled: return TEXT("AnimationScaled");
		case ABPT_AnimFrame: return TEXT("AnimationFrame");
		case ABPT_LocalAnimFrame: return TEXT("LocalAnimationFrame");
		default: return TEXT("Unknown");
		}
	}

	const TCHAR* RootMotionRootLockToString(const ERootMotionRootLock::Type Value)
	{
		switch (Value)
		{
		case ERootMotionRootLock::RefPose: return TEXT("ReferencePose");
		case ERootMotionRootLock::AnimFirstFrame: return TEXT("AnimationFirstFrame");
		case ERootMotionRootLock::Zero: return TEXT("Zero");
		default: return TEXT("Unknown");
		}
	}

	const TCHAR* NotifyTriggerModeToString(const ENotifyTriggerMode::Type Value)
	{
		switch (Value)
		{
		case ENotifyTriggerMode::AllAnimations: return TEXT("AllAnimations");
		case ENotifyTriggerMode::HighestWeightedAnimation: return TEXT("HighestWeightedAnimation");
		case ENotifyTriggerMode::None: return TEXT("None");
		default: return TEXT("Unknown");
		}
	}

	struct FSortedNotify
	{
		const FAnimNotifyEvent* Event = nullptr;
		FString SortKey;
	};

	TArray<TSharedPtr<FJsonValue>> BuildNotifyArray(UAnimSequenceBase* Asset, FString& OutError)
	{
		TArray<TSharedPtr<FJsonValue>> Result;
		const FArrayProperty* ArrayProperty = FindFProperty<FArrayProperty>(Asset->GetClass(), TEXT("Notifies"));
		const FStructProperty* StructProperty = ArrayProperty != nullptr ? CastField<FStructProperty>(ArrayProperty->Inner) : nullptr;
		if (ArrayProperty == nullptr || StructProperty == nullptr || StructProperty->Struct != FAnimNotifyEvent::StaticStruct())
		{
			OutError = TEXT("Notifies property is unavailable or has an unexpected type.");
			return Result;
		}

		FScriptArrayHelper Helper(ArrayProperty, ArrayProperty->ContainerPtrToValuePtr<void>(Asset));
		TArray<FSortedNotify> Sorted;
		Sorted.Reserve(Helper.Num());
		for (int32 Index = 0; Index < Helper.Num(); ++Index)
		{
			const FAnimNotifyEvent* Event = reinterpret_cast<const FAnimNotifyEvent*>(Helper.GetRawPtr(Index));
			const FString Name = !Event->NotifyName.IsNone() ? Event->NotifyName.ToString()
				: (Event->Notify != nullptr ? Event->Notify->GetName()
					: (Event->NotifyStateClass != nullptr ? Event->NotifyStateClass->GetName() : FString()));
			FSortedNotify Record;
			Record.Event = Event;
			Record.SortKey = FString::Printf(TEXT("%020.9f|%08d|%s"), Event->GetTriggerTime(), Event->TrackIndex, *Name);
			Sorted.Add(MoveTemp(Record));
		}
		Sorted.Sort([](const FSortedNotify& Left, const FSortedNotify& Right) { return Left.SortKey < Right.SortKey; });

		for (const FSortedNotify& Record : Sorted)
		{
			const FAnimNotifyEvent& Event = *Record.Event;
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Event.NotifyName.ToString());
			Json->SetStringField(TEXT("notifyPath"), ObjectPathOrEmpty(Event.Notify.Get()));
			Json->SetStringField(TEXT("notifyStatePath"), ObjectPathOrEmpty(Event.NotifyStateClass.Get()));
			Json->SetNumberField(TEXT("triggerTime"), Event.GetTriggerTime());
			Json->SetNumberField(TEXT("endTriggerTime"), Event.GetEndTriggerTime());
			Json->SetNumberField(TEXT("duration"), Event.GetDuration());
			Json->SetNumberField(TEXT("trackIndex"), Event.TrackIndex);
			Json->SetNumberField(TEXT("triggerWeightThreshold"), Event.TriggerWeightThreshold);
			Json->SetNumberField(TEXT("triggerChance"), Event.NotifyTriggerChance);
			Json->SetNumberField(TEXT("filterLod"), Event.NotifyFilterLOD);
			Json->SetBoolField(TEXT("branchingPoint"), Event.IsBranchingPoint());
			Json->SetBoolField(TEXT("triggerOnDedicatedServer"), Event.bTriggerOnDedicatedServer);
			Json->SetBoolField(TEXT("triggerOnFollower"), Event.bTriggerOnFollower);
#if WITH_EDITORONLY_DATA
			Json->SetStringField(TEXT("guid"), Event.Guid.ToString(EGuidFormats::DigitsWithHyphensLower));
#endif
			Result.Add(MakeShared<FJsonValueObject>(Json));
		}
		return Result;
	}

	int32 GetReflectedArrayCount(UObject* Object, const FName PropertyName)
	{
		const FArrayProperty* Property = FindFProperty<FArrayProperty>(Object->GetClass(), PropertyName);
		if (Property == nullptr)
		{
			return 0;
		}
		FScriptArrayHelper Helper(Property, Property->ContainerPtrToValuePtr<void>(Object));
		return Helper.Num();
	}

	TArray<TSharedPtr<FJsonValue>> BuildCurveArray(const UAnimSequenceBase* Asset)
	{
		TArray<const FFloatCurve*> Curves;
		for (const FFloatCurve& Curve : Asset->GetCurveData().FloatCurves) Curves.Add(&Curve);
		Curves.Sort([](const FFloatCurve& Left, const FFloatCurve& Right) { return Left.GetName().LexicalLess(Right.GetName()); });
		TArray<TSharedPtr<FJsonValue>> Result;
		for (const FFloatCurve* Curve : Curves)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Curve->GetName().ToString());
			Json->SetNumberField(TEXT("keyCount"), Curve->FloatCurve.GetNumKeys());
			Json->SetNumberField(TEXT("flags"), Curve->GetCurveTypeFlags());
			Result.Add(MakeShared<FJsonValueObject>(Json));
		}
		return Result;
	}

	const TCHAR* CollisionTraceFlagToString(const ECollisionTraceFlag Value)
	{
		switch (Value)
		{
		case CTF_UseDefault:
			return TEXT("UseDefault");
		case CTF_UseSimpleAndComplex:
			return TEXT("UseSimpleAndComplex");
		case CTF_UseSimpleAsComplex:
			return TEXT("UseSimpleAsComplex");
		case CTF_UseComplexAsSimple:
			return TEXT("UseComplexAsSimple");
		default:
			return TEXT("Unknown");
		}
	}

	TArray<TSharedPtr<FJsonValue>> BuildSkeletalSocketArray(const TArray<USkeletalMeshSocket*>& SourceSockets)
	{
		TArray<const USkeletalMeshSocket*> SortedSockets;
		for (const USkeletalMeshSocket* Socket : SourceSockets)
		{
			if (Socket != nullptr)
			{
				SortedSockets.Add(Socket);
			}
		}
		SortedSockets.Sort([](const USkeletalMeshSocket& Left, const USkeletalMeshSocket& Right)
		{
			const FString LeftKey = Left.SocketName.ToString() + TEXT("|") + Left.BoneName.ToString();
			const FString RightKey = Right.SocketName.ToString() + TEXT("|") + Right.BoneName.ToString();
			return LeftKey < RightKey;
		});

		TArray<TSharedPtr<FJsonValue>> Result;
		for (const USkeletalMeshSocket* Socket : SortedSockets)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Socket->SocketName.ToString());
			Json->SetStringField(TEXT("boneName"), Socket->BoneName.ToString());
			Json->SetObjectField(TEXT("location"), VectorToJson(Socket->RelativeLocation));
			Json->SetObjectField(TEXT("rotation"), RotatorToJson(Socket->RelativeRotation));
			Json->SetObjectField(TEXT("scale"), VectorToJson(Socket->RelativeScale));
			Result.Add(MakeShared<FJsonValueObject>(Json));
		}
		return Result;
	}

	TArray<TSharedPtr<FJsonValue>> BuildSkeletalMaterials(const TArray<FSkeletalMaterial>& SourceMaterials)
	{
		TArray<TSharedPtr<FJsonValue>> Result;
		for (int32 MaterialIndex = 0; MaterialIndex < SourceMaterials.Num(); ++MaterialIndex)
		{
			const FSkeletalMaterial& Source = SourceMaterials[MaterialIndex];
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetNumberField(TEXT("index"), MaterialIndex);
			Json->SetStringField(TEXT("slotName"), Source.MaterialSlotName.ToString());
#if WITH_EDITORONLY_DATA
			Json->SetStringField(TEXT("importedSlotName"), Source.ImportedMaterialSlotName.ToString());
#else
			Json->SetStringField(TEXT("importedSlotName"), FString());
#endif
			Json->SetStringField(TEXT("materialPath"), ObjectPathOrEmpty(Source.MaterialInterface));
			Json->SetStringField(TEXT("overlayMaterialPath"), ObjectPathOrEmpty(Source.OverlayMaterialInterface));
			Result.Add(MakeShared<FJsonValueObject>(Json));
		}
		return Result;
	}

	EAssetReaderStatus ReadStaticMesh(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UStaticMesh* StaticMesh = Cast<UStaticMesh>(AssetData.GetAsset());
		if (StaticMesh == nullptr)
		{
			OutError = TEXT("Failed to load Static Mesh asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("static-mesh"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);

		const int32 LodCount = StaticMesh->GetNumLODs();
		OutDetails->SetNumberField(TEXT("lodCount"), LodCount);
		TArray<TSharedPtr<FJsonValue>> Lods;
		for (int32 LodIndex = 0; LodIndex < LodCount; ++LodIndex)
		{
			TSharedRef<FJsonObject> Lod = MakeShared<FJsonObject>();
			Lod->SetNumberField(TEXT("index"), LodIndex);
			Lod->SetNumberField(TEXT("sectionCount"), StaticMesh->GetNumSections(LodIndex));
			Lods.Add(MakeShared<FJsonValueObject>(Lod));
		}
		OutDetails->SetArrayField(TEXT("lods"), Lods);

		TArray<TSharedPtr<FJsonValue>> Materials;
		const TArray<FStaticMaterial>& StaticMaterials = StaticMesh->GetStaticMaterials();
		for (int32 MaterialIndex = 0; MaterialIndex < StaticMaterials.Num(); ++MaterialIndex)
		{
			const FStaticMaterial& StaticMaterial = StaticMaterials[MaterialIndex];
			TSharedRef<FJsonObject> Material = MakeShared<FJsonObject>();
			Material->SetNumberField(TEXT("index"), MaterialIndex);
			Material->SetStringField(TEXT("slotName"), StaticMaterial.MaterialSlotName.ToString());
			Material->SetStringField(TEXT("importedSlotName"), StaticMaterial.ImportedMaterialSlotName.ToString());
			Material->SetStringField(TEXT("materialPath"), ObjectPathOrEmpty(StaticMaterial.MaterialInterface));
			Material->SetStringField(TEXT("overlayMaterialPath"), ObjectPathOrEmpty(StaticMaterial.OverlayMaterialInterface));
			Materials.Add(MakeShared<FJsonValueObject>(Material));
		}
		OutDetails->SetNumberField(TEXT("materialSlotCount"), StaticMaterials.Num());
		OutDetails->SetArrayField(TEXT("materials"), Materials);
		OutDetails->SetObjectField(TEXT("bounds"), BoundsToJson(StaticMesh->GetBounds()));

		TSharedRef<FJsonObject> Lightmap = MakeShared<FJsonObject>();
		Lightmap->SetNumberField(TEXT("resolution"), StaticMesh->GetLightMapResolution());
		Lightmap->SetNumberField(TEXT("coordinateIndex"), StaticMesh->GetLightMapCoordinateIndex());
		OutDetails->SetObjectField(TEXT("lightmap"), Lightmap);

		TSharedRef<FJsonObject> Nanite = MakeShared<FJsonObject>();
#if WITH_EDITORONLY_DATA
		Nanite->SetBoolField(TEXT("enabled"), StaticMesh->NaniteSettings.bEnabled);
		Nanite->SetBoolField(TEXT("preserveArea"), StaticMesh->NaniteSettings.bPreserveArea);
		Nanite->SetBoolField(TEXT("explicitTangents"), StaticMesh->NaniteSettings.bExplicitTangents);
		Nanite->SetNumberField(TEXT("keepPercentTriangles"), StaticMesh->NaniteSettings.KeepPercentTriangles);
		Nanite->SetNumberField(TEXT("trimRelativeError"), StaticMesh->NaniteSettings.TrimRelativeError);
#else
		Nanite->SetBoolField(TEXT("enabled"), false);
#endif
		OutDetails->SetObjectField(TEXT("nanite"), Nanite);

		const UBodySetup* BodySetup = StaticMesh->GetBodySetup();
		TSharedRef<FJsonObject> Collision = MakeShared<FJsonObject>();
		Collision->SetBoolField(TEXT("hasBodySetup"), BodySetup != nullptr);
		if (BodySetup != nullptr)
		{
			const FKAggregateGeom& Geometry = BodySetup->AggGeom;
			Collision->SetStringField(TEXT("traceFlag"), CollisionTraceFlagToString(BodySetup->CollisionTraceFlag));
			Collision->SetNumberField(TEXT("traceFlagValue"), static_cast<int32>(BodySetup->CollisionTraceFlag));
			Collision->SetNumberField(TEXT("sphereCount"), Geometry.SphereElems.Num());
			Collision->SetNumberField(TEXT("boxCount"), Geometry.BoxElems.Num());
			Collision->SetNumberField(TEXT("capsuleCount"), Geometry.SphylElems.Num());
			Collision->SetNumberField(TEXT("convexCount"), Geometry.ConvexElems.Num());
			Collision->SetNumberField(TEXT("taperedCapsuleCount"), Geometry.TaperedCapsuleElems.Num());
			Collision->SetNumberField(TEXT("levelSetCount"), Geometry.LevelSetElems.Num());
			Collision->SetNumberField(TEXT("simpleShapeCount"), Geometry.GetElementCount());
		}
		OutDetails->SetObjectField(TEXT("collision"), Collision);

		TArray<TPair<FString, const UStaticMeshSocket*>> SortedSockets;
		for (const TObjectPtr<UStaticMeshSocket>& Socket : StaticMesh->Sockets)
		{
			if (Socket != nullptr)
			{
				SortedSockets.Emplace(Socket->SocketName.ToString(), Socket.Get());
			}
		}
		SortedSockets.Sort([](
			const TPair<FString, const UStaticMeshSocket*>& Left,
			const TPair<FString, const UStaticMeshSocket*>& Right)
		{
			return Left.Key < Right.Key;
		});

		TArray<TSharedPtr<FJsonValue>> Sockets;
		for (const TPair<FString, const UStaticMeshSocket*>& Pair : SortedSockets)
		{
			const UStaticMeshSocket* Socket = Pair.Value;
			TSharedRef<FJsonObject> SocketObject = MakeShared<FJsonObject>();
			SocketObject->SetStringField(TEXT("name"), Socket->SocketName.ToString());
			SocketObject->SetObjectField(TEXT("location"), VectorToJson(Socket->RelativeLocation));
			SocketObject->SetObjectField(TEXT("rotation"), RotatorToJson(Socket->RelativeRotation));
			SocketObject->SetObjectField(TEXT("scale"), VectorToJson(Socket->RelativeScale));
			Sockets.Add(MakeShared<FJsonValueObject>(SocketObject));
		}
		OutDetails->SetNumberField(TEXT("socketCount"), Sockets.Num());
		OutDetails->SetArrayField(TEXT("sockets"), Sockets);
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadSkeletalMesh(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		USkeletalMesh* SkeletalMesh = Cast<USkeletalMesh>(AssetData.GetAsset());
		if (SkeletalMesh == nullptr)
		{
			OutError = TEXT("Failed to load Skeletal Mesh asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("skeletal-mesh"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("skeletonPath"), ObjectPathOrEmpty(SkeletalMesh->GetSkeleton()));
		OutDetails->SetStringField(TEXT("physicsAssetPath"), ObjectPathOrEmpty(SkeletalMesh->GetPhysicsAsset()));
		OutDetails->SetNumberField(TEXT("lodCount"), SkeletalMesh->GetLODNum());
		OutDetails->SetObjectField(TEXT("bounds"), BoundsToJson(SkeletalMesh->GetBounds()));

		const FReferenceSkeleton& RefSkeleton = SkeletalMesh->GetRefSkeleton();
		OutDetails->SetNumberField(TEXT("boneCount"), RefSkeleton.GetNum());
		OutDetails->SetNumberField(TEXT("rawBoneCount"), RefSkeleton.GetRawBoneNum());
		OutDetails->SetStringField(
			TEXT("rootBoneName"),
			RefSkeleton.GetNum() > 0 ? RefSkeleton.GetBoneName(0).ToString() : FString());

		const TArray<FSkeletalMaterial>& Materials = SkeletalMesh->GetMaterials();
		OutDetails->SetNumberField(TEXT("materialSlotCount"), Materials.Num());
		OutDetails->SetArrayField(TEXT("materials"), BuildSkeletalMaterials(Materials));

		TArray<TPair<FString, FString>> MorphTargets;
		for (const TObjectPtr<UMorphTarget>& MorphTarget : SkeletalMesh->GetMorphTargets())
		{
			if (MorphTarget != nullptr)
			{
				MorphTargets.Emplace(MorphTarget->GetName(), MorphTarget->GetPathName());
			}
		}
		MorphTargets.Sort([](const TPair<FString, FString>& Left, const TPair<FString, FString>& Right)
		{
			return Left.Key < Right.Key;
		});
		TArray<TSharedPtr<FJsonValue>> MorphTargetValues;
		for (const TPair<FString, FString>& MorphTarget : MorphTargets)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), MorphTarget.Key);
			Json->SetStringField(TEXT("path"), MorphTarget.Value);
			MorphTargetValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("morphTargetCount"), MorphTargetValues.Num());
		OutDetails->SetArrayField(TEXT("morphTargets"), MorphTargetValues);

		const USkeletalMesh* ConstMesh = SkeletalMesh;
		const TArray<USkeletalMeshSocket*>& MeshSocketList = ConstMesh->GetMeshOnlySocketList();
		const TArray<USkeletalMeshSocket*> ActiveSocketList = ConstMesh->GetActiveSocketList();
		OutDetails->SetNumberField(TEXT("meshSocketCount"), MeshSocketList.Num());
		OutDetails->SetArrayField(TEXT("meshSockets"), BuildSkeletalSocketArray(MeshSocketList));
		OutDetails->SetNumberField(TEXT("activeSocketCount"), ActiveSocketList.Num());
		OutDetails->SetArrayField(TEXT("activeSockets"), BuildSkeletalSocketArray(ActiveSocketList));
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadSkeleton(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		USkeleton* Skeleton = Cast<USkeleton>(AssetData.GetAsset());
		if (Skeleton == nullptr)
		{
			OutError = TEXT("Failed to load Skeleton asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("skeleton"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
		OutDetails->SetNumberField(TEXT("boneCount"), RefSkeleton.GetNum());
		OutDetails->SetNumberField(TEXT("rawBoneCount"), RefSkeleton.GetRawBoneNum());
		OutDetails->SetStringField(
			TEXT("rootBoneName"),
			RefSkeleton.GetNum() > 0 ? RefSkeleton.GetBoneName(0).ToString() : FString());

		const TArray<FTransform>& BonePoses = RefSkeleton.GetRefBonePose();
		TArray<TSharedPtr<FJsonValue>> Bones;
		for (int32 BoneIndex = 0; BoneIndex < RefSkeleton.GetNum(); ++BoneIndex)
		{
			const int32 ParentIndex = RefSkeleton.GetParentIndex(BoneIndex);
			TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
			Bone->SetNumberField(TEXT("index"), BoneIndex);
			Bone->SetStringField(TEXT("name"), RefSkeleton.GetBoneName(BoneIndex).ToString());
			Bone->SetNumberField(TEXT("parentIndex"), ParentIndex);
			Bone->SetStringField(
				TEXT("parentName"),
				ParentIndex != INDEX_NONE ? RefSkeleton.GetBoneName(ParentIndex).ToString() : FString());
			Bone->SetObjectField(TEXT("localTransform"), TransformToJson(BonePoses[BoneIndex]));
			Bones.Add(MakeShared<FJsonValueObject>(Bone));
		}
		OutDetails->SetArrayField(TEXT("bones"), Bones);

		TArray<FVirtualBone> VirtualBones = Skeleton->GetVirtualBones();
		VirtualBones.Sort([](const FVirtualBone& Left, const FVirtualBone& Right)
		{
			return Left.VirtualBoneName.LexicalLess(Right.VirtualBoneName);
		});
		TArray<TSharedPtr<FJsonValue>> VirtualBoneValues;
		for (const FVirtualBone& VirtualBone : VirtualBones)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), VirtualBone.VirtualBoneName.ToString());
			Json->SetStringField(TEXT("sourceBoneName"), VirtualBone.SourceBoneName.ToString());
			Json->SetStringField(TEXT("targetBoneName"), VirtualBone.TargetBoneName.ToString());
			VirtualBoneValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("virtualBoneCount"), VirtualBoneValues.Num());
		OutDetails->SetArrayField(TEXT("virtualBones"), VirtualBoneValues);

		TArray<USkeletalMeshSocket*> SkeletonSocketList;
		for (const TObjectPtr<USkeletalMeshSocket>& Socket : Skeleton->Sockets)
		{
			if (Socket != nullptr)
			{
				SkeletonSocketList.Add(Socket.Get());
			}
		}
		OutDetails->SetNumberField(TEXT("socketCount"), SkeletonSocketList.Num());
		OutDetails->SetArrayField(TEXT("sockets"), BuildSkeletalSocketArray(SkeletonSocketList));

		const USkeleton* ConstSkeleton = Skeleton;
		OutDetails->SetStringField(TEXT("previewMeshPath"), ObjectPathOrEmpty(ConstSkeleton->GetPreviewMesh()));

		TArray<FString> CompatibleSkeletonPaths;
		for (const TSoftObjectPtr<USkeleton>& CompatibleSkeleton : Skeleton->GetCompatibleSkeletons())
		{
			const FString Path = CompatibleSkeleton.ToSoftObjectPath().ToString();
			if (!Path.IsEmpty())
			{
				CompatibleSkeletonPaths.Add(Path);
			}
		}
		CompatibleSkeletonPaths.Sort();
		TArray<TSharedPtr<FJsonValue>> CompatibleSkeletonValues;
		for (const FString& Path : CompatibleSkeletonPaths)
		{
			CompatibleSkeletonValues.Add(MakeShared<FJsonValueString>(Path));
		}
		OutDetails->SetNumberField(TEXT("compatibleSkeletonCount"), CompatibleSkeletonValues.Num());
		OutDetails->SetArrayField(TEXT("compatibleSkeletons"), CompatibleSkeletonValues);

		TArray<FName> CurveNames;
		Skeleton->GetCurveMetaDataNames(CurveNames);
		CurveNames.Sort(FNameLexicalLess());
		TArray<TSharedPtr<FJsonValue>> CurveNameValues;
		for (const FName CurveName : CurveNames)
		{
			CurveNameValues.Add(MakeShared<FJsonValueString>(CurveName.ToString()));
		}
		OutDetails->SetNumberField(TEXT("curveMetadataCount"), CurveNameValues.Num());
		OutDetails->SetArrayField(TEXT("curveMetadataNames"), CurveNameValues);
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadPhysicsAsset(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UPhysicsAsset* PhysicsAsset = Cast<UPhysicsAsset>(AssetData.GetAsset());
		if (PhysicsAsset == nullptr)
		{
			OutError = TEXT("Failed to load Physics Asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("physics-asset"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("previewSkeletalMeshPath"), ObjectPathOrEmpty(PhysicsAsset->GetPreviewMesh()));
		OutDetails->SetNumberField(TEXT("bodyCount"), PhysicsAsset->SkeletalBodySetups.Num());
		OutDetails->SetNumberField(TEXT("constraintCount"), PhysicsAsset->ConstraintSetup.Num());
		OutDetails->SetNumberField(TEXT("disabledCollisionPairCount"), PhysicsAsset->CollisionDisableTable.Num());

		TMap<int32, FName> BoneNameByBodyIndex;
		for (const TPair<FName, int32>& Pair : PhysicsAsset->BodySetupIndexMap)
		{
			if (Pair.Value >= 0)
			{
				BoneNameByBodyIndex.Add(Pair.Value, Pair.Key);
			}
		}

		TSet<int32> BoundsBodyIndices;
		for (const int32 BodyIndex : PhysicsAsset->BoundsBodies)
		{
			BoundsBodyIndices.Add(BodyIndex);
		}
		TArray<TSharedPtr<FJsonValue>> BoundsBodies;
		for (const int32 BodyIndex : PhysicsAsset->BoundsBodies)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetNumberField(TEXT("bodyIndex"), BodyIndex);
			Json->SetStringField(TEXT("boneName"), BoneNameByBodyIndex.FindRef(BodyIndex).ToString());
			BoundsBodies.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("boundsBodyCount"), BoundsBodies.Num());
		OutDetails->SetArrayField(TEXT("boundsBodies"), BoundsBodies);

		TArray<FName> PhysicalAnimationProfileNames = PhysicsAsset->GetPhysicalAnimationProfileNames();
		PhysicalAnimationProfileNames.Sort(FNameLexicalLess());
		TArray<TSharedPtr<FJsonValue>> PhysicalAnimationProfiles;
		for (const FName ProfileName : PhysicalAnimationProfileNames)
		{
			PhysicalAnimationProfiles.Add(MakeShared<FJsonValueString>(ProfileName.ToString()));
		}
		OutDetails->SetNumberField(TEXT("physicalAnimationProfileCount"), PhysicalAnimationProfiles.Num());
		OutDetails->SetArrayField(TEXT("physicalAnimationProfiles"), PhysicalAnimationProfiles);

		TArray<FName> ConstraintProfileNames = PhysicsAsset->GetConstraintProfileNames();
		ConstraintProfileNames.Sort(FNameLexicalLess());
		TArray<TSharedPtr<FJsonValue>> ConstraintProfiles;
		for (const FName ProfileName : ConstraintProfileNames)
		{
			ConstraintProfiles.Add(MakeShared<FJsonValueString>(ProfileName.ToString()));
		}
		OutDetails->SetNumberField(TEXT("constraintProfileCount"), ConstraintProfiles.Num());
		OutDetails->SetArrayField(TEXT("constraintProfiles"), ConstraintProfiles);

		TArray<TSharedPtr<FJsonValue>> Bodies;
		int32 TotalShapeCount = 0;
		for (int32 BodyIndex = 0; BodyIndex < PhysicsAsset->SkeletalBodySetups.Num(); ++BodyIndex)
		{
			const USkeletalBodySetup* BodySetup = PhysicsAsset->SkeletalBodySetups[BodyIndex];
			if (BodySetup == nullptr)
			{
				continue;
			}

			const FKAggregateGeom& Geometry = BodySetup->AggGeom;
			const int32 ShapeCount = Geometry.GetElementCount();
			TotalShapeCount += ShapeCount;
			TSharedRef<FJsonObject> Body = MakeShared<FJsonObject>();
			Body->SetNumberField(TEXT("index"), BodyIndex);
			Body->SetStringField(TEXT("boneName"), BoneNameByBodyIndex.FindRef(BodyIndex).ToString());
			Body->SetStringField(TEXT("objectName"), BodySetup->GetName());
			Body->SetBoolField(TEXT("considerForBounds"), BodySetup->bConsiderForBounds);
			Body->SetBoolField(TEXT("skipScaleFromAnimation"), BodySetup->bSkipScaleFromAnimation);
			Body->SetBoolField(TEXT("listedAsBoundsBody"), BoundsBodyIndices.Contains(BodyIndex));
			Body->SetNumberField(TEXT("shapeCount"), ShapeCount);
			TSharedRef<FJsonObject> Shapes = MakeShared<FJsonObject>();
			Shapes->SetNumberField(TEXT("sphere"), Geometry.SphereElems.Num());
			Shapes->SetNumberField(TEXT("box"), Geometry.BoxElems.Num());
			Shapes->SetNumberField(TEXT("capsule"), Geometry.SphylElems.Num());
			Shapes->SetNumberField(TEXT("convex"), Geometry.ConvexElems.Num());
			Shapes->SetNumberField(TEXT("taperedCapsule"), Geometry.TaperedCapsuleElems.Num());
			Shapes->SetNumberField(TEXT("levelSet"), Geometry.LevelSetElems.Num());
			Body->SetObjectField(TEXT("shapeCounts"), Shapes);

			TArray<FString> BodyProfileNames;
			for (const FPhysicalAnimationProfile& Profile : BodySetup->GetPhysicalAnimationProfiles())
			{
				if (!Profile.ProfileName.IsNone())
				{
					BodyProfileNames.Add(Profile.ProfileName.ToString());
				}
			}
			BodyProfileNames.Sort();
			TArray<TSharedPtr<FJsonValue>> BodyProfiles;
			for (const FString& ProfileName : BodyProfileNames)
			{
				BodyProfiles.Add(MakeShared<FJsonValueString>(ProfileName));
			}
			Body->SetNumberField(TEXT("physicalAnimationProfileCount"), BodyProfiles.Num());
			Body->SetArrayField(TEXT("physicalAnimationProfiles"), BodyProfiles);
			Bodies.Add(MakeShared<FJsonValueObject>(Body));
		}
		OutDetails->SetNumberField(TEXT("totalShapeCount"), TotalShapeCount);
		OutDetails->SetArrayField(TEXT("bodies"), Bodies);

		TArray<TSharedPtr<FJsonValue>> Constraints;
		for (int32 ConstraintIndex = 0; ConstraintIndex < PhysicsAsset->ConstraintSetup.Num(); ++ConstraintIndex)
		{
			const UPhysicsConstraintTemplate* ConstraintTemplate = PhysicsAsset->ConstraintSetup[ConstraintIndex];
			if (ConstraintTemplate == nullptr)
			{
				continue;
			}
			const FConstraintInstance& Instance = ConstraintTemplate->DefaultInstance;
			TSharedRef<FJsonObject> Constraint = MakeShared<FJsonObject>();
			Constraint->SetNumberField(TEXT("index"), ConstraintIndex);
			Constraint->SetStringField(TEXT("jointName"), Instance.JointName.ToString());
			Constraint->SetStringField(TEXT("bone1"), Instance.ConstraintBone1.ToString());
			Constraint->SetStringField(TEXT("bone2"), Instance.ConstraintBone2.ToString());
			Constraint->SetObjectField(TEXT("position1"), VectorToJson(Instance.Pos1));
			Constraint->SetObjectField(TEXT("primaryAxis1"), VectorToJson(Instance.PriAxis1));
			Constraint->SetObjectField(TEXT("secondaryAxis1"), VectorToJson(Instance.SecAxis1));
			Constraint->SetObjectField(TEXT("position2"), VectorToJson(Instance.Pos2));
			Constraint->SetObjectField(TEXT("primaryAxis2"), VectorToJson(Instance.PriAxis2));
			Constraint->SetObjectField(TEXT("secondaryAxis2"), VectorToJson(Instance.SecAxis2));
			Constraint->SetObjectField(TEXT("angularRotationOffset"), RotatorToJson(Instance.AngularRotationOffset));

			TArray<FString> ProfileNames;
			for (const FPhysicsConstraintProfileHandle& Profile : ConstraintTemplate->ProfileHandles)
			{
				if (!Profile.ProfileName.IsNone())
				{
					ProfileNames.Add(Profile.ProfileName.ToString());
				}
			}
			ProfileNames.Sort();
			TArray<TSharedPtr<FJsonValue>> Profiles;
			for (const FString& ProfileName : ProfileNames)
			{
				Profiles.Add(MakeShared<FJsonValueString>(ProfileName));
			}
			Constraint->SetNumberField(TEXT("profileCount"), Profiles.Num());
			Constraint->SetArrayField(TEXT("profiles"), Profiles);
			Constraints.Add(MakeShared<FJsonValueObject>(Constraint));
		}
		OutDetails->SetArrayField(TEXT("constraints"), Constraints);
		return EAssetReaderStatus::Success;
	}

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

	EAssetReaderStatus ReadAnimSequence(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UAnimSequence* Sequence = Cast<UAnimSequence>(AssetData.GetAsset());
		if (Sequence == nullptr)
		{
			OutError = TEXT("Failed to load Anim Sequence asset.");
			return EAssetReaderStatus::Failed;
		}

		const EAdditiveAnimationType AdditiveType = static_cast<EAdditiveAnimationType>(Sequence->AdditiveAnimType.GetValue());
		const EAdditiveBasePoseType BasePoseType = static_cast<EAdditiveBasePoseType>(Sequence->RefPoseType.GetValue());
		OutDetails->SetStringField(TEXT("type"), TEXT("anim-sequence"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("skeletonPath"), ObjectPathOrEmpty(Sequence->GetSkeleton()));
		OutDetails->SetNumberField(TEXT("playLength"), Sequence->GetPlayLength());
		OutDetails->SetNumberField(TEXT("rateScale"), Sequence->RateScale);
		OutDetails->SetNumberField(TEXT("sampledKeyCount"), Sequence->GetNumberOfSampledKeys());
		OutDetails->SetObjectField(TEXT("samplingFrameRate"), FrameRateToJson(Sequence->GetSamplingFrameRate()));
		OutDetails->SetStringField(TEXT("retargetSource"), Sequence->RetargetSource.ToString());
		OutDetails->SetStringField(TEXT("additiveType"), AdditiveAnimationTypeToString(AdditiveType));
		OutDetails->SetNumberField(TEXT("additiveTypeValue"), static_cast<int32>(AdditiveType));
		OutDetails->SetStringField(TEXT("basePoseType"), AdditiveBasePoseTypeToString(BasePoseType));
		OutDetails->SetNumberField(TEXT("basePoseTypeValue"), static_cast<int32>(BasePoseType));
		OutDetails->SetStringField(TEXT("basePoseSequencePath"), ObjectPathOrEmpty(Sequence->RefPoseSeq.Get()));
		OutDetails->SetNumberField(TEXT("basePoseFrameIndex"), Sequence->RefFrameIndex);

		TSharedRef<FJsonObject> RootMotion = MakeShared<FJsonObject>();
		RootMotion->SetBoolField(TEXT("enabled"), Sequence->bEnableRootMotion);
		RootMotion->SetStringField(TEXT("rootLock"), RootMotionRootLockToString(Sequence->RootMotionRootLock));
		RootMotion->SetNumberField(TEXT("rootLockValue"), static_cast<int32>(Sequence->RootMotionRootLock.GetValue()));
		RootMotion->SetBoolField(TEXT("forceRootLock"), Sequence->bForceRootLock);
		RootMotion->SetBoolField(TEXT("normalizedScale"), Sequence->bUseNormalizedRootMotionScale);
		OutDetails->SetObjectField(TEXT("rootMotion"), RootMotion);

		FString NotifyError;
		TArray<TSharedPtr<FJsonValue>> Notifies = BuildNotifyArray(Sequence, NotifyError);
		OutDetails->SetNumberField(TEXT("notifyCount"), Notifies.Num());
		OutDetails->SetArrayField(TEXT("notifies"), Notifies);
		OutDetails->SetStringField(TEXT("notifyReadError"), NotifyError);
		TArray<TSharedPtr<FJsonValue>> Curves = BuildCurveArray(Sequence);
		OutDetails->SetNumberField(TEXT("curveCount"), Curves.Num());
		OutDetails->SetArrayField(TEXT("curves"), Curves);

		TArray<FAnimSyncMarker> Markers = Sequence->AuthoredSyncMarkers;
		Markers.Sort([](const FAnimSyncMarker& Left, const FAnimSyncMarker& Right)
		{
			if (!FMath::IsNearlyEqual(Left.Time, Right.Time)) return Left.Time < Right.Time;
			return Left.MarkerName.LexicalLess(Right.MarkerName);
		});
		TArray<TSharedPtr<FJsonValue>> MarkerValues;
		for (const FAnimSyncMarker& Marker : Markers)
		{
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Marker.MarkerName.ToString());
			Json->SetNumberField(TEXT("time"), Marker.Time);
#if WITH_EDITORONLY_DATA
			Json->SetNumberField(TEXT("trackIndex"), Marker.TrackIndex);
			Json->SetStringField(TEXT("guid"), Marker.Guid.ToString(EGuidFormats::DigitsWithHyphensLower));
#endif
			MarkerValues.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("syncMarkerCount"), MarkerValues.Num());
		OutDetails->SetArrayField(TEXT("syncMarkers"), MarkerValues);

		TArray<FName> UniqueMarkerNames = Sequence->UniqueMarkerNames;
		UniqueMarkerNames.Sort(FNameLexicalLess());
		TArray<TSharedPtr<FJsonValue>> UniqueMarkers;
		for (const FName Name : UniqueMarkerNames) UniqueMarkers.Add(MakeShared<FJsonValueString>(Name.ToString()));
		OutDetails->SetArrayField(TEXT("uniqueMarkerNames"), UniqueMarkers);
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadAnimMontage(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UAnimMontage* Montage = Cast<UAnimMontage>(AssetData.GetAsset());
		if (Montage == nullptr)
		{
			OutError = TEXT("Failed to load Anim Montage asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("anim-montage"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("skeletonPath"), ObjectPathOrEmpty(Montage->GetSkeleton()));
		OutDetails->SetNumberField(TEXT("playLength"), Montage->GetPlayLength());
		OutDetails->SetNumberField(TEXT("rateScale"), Montage->RateScale);
		OutDetails->SetObjectField(TEXT("samplingFrameRate"), FrameRateToJson(Montage->GetSamplingFrameRate()));
		OutDetails->SetBoolField(TEXT("hasRootMotion"), Montage->HasRootMotion());
		OutDetails->SetBoolField(TEXT("autoBlendOut"), Montage->bEnableAutoBlendOut);
		OutDetails->SetNumberField(TEXT("defaultBlendInTime"), Montage->GetDefaultBlendInTime());
		OutDetails->SetNumberField(TEXT("defaultBlendOutTime"), Montage->GetDefaultBlendOutTime());
		OutDetails->SetNumberField(TEXT("blendOutTriggerTime"), Montage->BlendOutTriggerTime);
		OutDetails->SetStringField(TEXT("syncGroup"), Montage->SyncGroup.ToString());
		OutDetails->SetNumberField(TEXT("syncSlotIndex"), Montage->SyncSlotIndex);

		TArray<TSharedPtr<FJsonValue>> Sections;
		for (int32 SectionIndex = 0; SectionIndex < Montage->GetNumSections(); ++SectionIndex)
		{
			const FCompositeSection& Section = Montage->GetAnimCompositeSection(SectionIndex);
			float StartTime = 0.0f;
			float EndTime = 0.0f;
			Montage->GetSectionStartAndEndTime(SectionIndex, StartTime, EndTime);
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetNumberField(TEXT("index"), SectionIndex);
			Json->SetStringField(TEXT("name"), Section.SectionName.ToString());
			Json->SetStringField(TEXT("nextSectionName"), Section.NextSectionName.ToString());
			Json->SetNumberField(TEXT("startTime"), StartTime);
			Json->SetNumberField(TEXT("endTime"), EndTime);
			Json->SetNumberField(TEXT("length"), EndTime - StartTime);
			Json->SetNumberField(TEXT("metadataCount"), Section.MetaData.Num());
			Sections.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("sectionCount"), Sections.Num());
		OutDetails->SetArrayField(TEXT("sections"), Sections);

		TArray<TSharedPtr<FJsonValue>> Slots;
		for (int32 SlotIndex = 0; SlotIndex < Montage->SlotAnimTracks.Num(); ++SlotIndex)
		{
			const FSlotAnimationTrack& Slot = Montage->SlotAnimTracks[SlotIndex];
			TSharedRef<FJsonObject> SlotJson = MakeShared<FJsonObject>();
			SlotJson->SetNumberField(TEXT("index"), SlotIndex);
			SlotJson->SetStringField(TEXT("name"), Slot.SlotName.ToString());
			SlotJson->SetNumberField(TEXT("trackLength"), Slot.AnimTrack.GetLength());
			TArray<TSharedPtr<FJsonValue>> Segments;
			for (int32 SegmentIndex = 0; SegmentIndex < Slot.AnimTrack.AnimSegments.Num(); ++SegmentIndex)
			{
				const FAnimSegment& Segment = Slot.AnimTrack.AnimSegments[SegmentIndex];
				TSharedRef<FJsonObject> SegmentJson = MakeShared<FJsonObject>();
				SegmentJson->SetNumberField(TEXT("index"), SegmentIndex);
				SegmentJson->SetStringField(TEXT("animationPath"), ObjectPathOrEmpty(Segment.GetAnimReference().Get()));
				SegmentJson->SetNumberField(TEXT("startPosition"), Segment.StartPos);
				SegmentJson->SetNumberField(TEXT("endPosition"), Segment.GetEndPos());
				SegmentJson->SetNumberField(TEXT("animationStartTime"), Segment.AnimStartTime);
				SegmentJson->SetNumberField(TEXT("animationEndTime"), Segment.AnimEndTime);
				SegmentJson->SetNumberField(TEXT("playRate"), Segment.AnimPlayRate);
				SegmentJson->SetNumberField(TEXT("loopCount"), Segment.LoopingCount);
				SegmentJson->SetNumberField(TEXT("length"), Segment.GetLength());
				SegmentJson->SetBoolField(TEXT("valid"), Segment.IsValid());
				Segments.Add(MakeShared<FJsonValueObject>(SegmentJson));
			}
			SlotJson->SetNumberField(TEXT("segmentCount"), Segments.Num());
			SlotJson->SetArrayField(TEXT("segments"), Segments);
			Slots.Add(MakeShared<FJsonValueObject>(SlotJson));
		}
		OutDetails->SetNumberField(TEXT("slotCount"), Slots.Num());
		OutDetails->SetArrayField(TEXT("slots"), Slots);

		FString NotifyError;
		TArray<TSharedPtr<FJsonValue>> Notifies = BuildNotifyArray(Montage, NotifyError);
		OutDetails->SetNumberField(TEXT("notifyCount"), Notifies.Num());
		OutDetails->SetArrayField(TEXT("notifies"), Notifies);
		OutDetails->SetStringField(TEXT("notifyReadError"), NotifyError);
		OutDetails->SetNumberField(TEXT("branchingPointMarkerCount"), GetReflectedArrayCount(Montage, TEXT("BranchingPointMarkers")));
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadBlendSpace(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UBlendSpace* BlendSpace = Cast<UBlendSpace>(AssetData.GetAsset());
		if (BlendSpace == nullptr)
		{
			OutError = TEXT("Failed to load Blend Space asset.");
			return EAssetReaderStatus::Failed;
		}

		const bool bAimOffset = AssetData.AssetClassPath.ToString().Contains(TEXT("AimOffset"));
		OutDetails->SetStringField(TEXT("type"), TEXT("blend-space"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("blendSpaceType"), bAimOffset ? TEXT("aim-offset") : TEXT("blend-space"));
		OutDetails->SetStringField(TEXT("skeletonPath"), ObjectPathOrEmpty(BlendSpace->GetSkeleton()));
		OutDetails->SetStringField(TEXT("notifyTriggerMode"), NotifyTriggerModeToString(BlendSpace->NotifyTriggerMode));
		OutDetails->SetNumberField(TEXT("notifyTriggerModeValue"), static_cast<int32>(BlendSpace->NotifyTriggerMode.GetValue()));

		TArray<TSharedPtr<FJsonValue>> Axes;
		for (int32 AxisIndex = 0; AxisIndex < 3; ++AxisIndex)
		{
			const FBlendParameter& Axis = BlendSpace->GetBlendParameter(AxisIndex);
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetNumberField(TEXT("index"), AxisIndex);
			Json->SetStringField(TEXT("name"), Axis.DisplayName);
			Json->SetNumberField(TEXT("min"), Axis.Min);
			Json->SetNumberField(TEXT("max"), Axis.Max);
			Json->SetNumberField(TEXT("gridDivisions"), Axis.GridNum);
			Json->SetBoolField(TEXT("snapToGrid"), Axis.bSnapToGrid);
			Json->SetBoolField(TEXT("wrapInput"), Axis.bWrapInput);
			Axes.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetArrayField(TEXT("axes"), Axes);

		TArray<const FBlendSample*> SortedSamples;
		for (const FBlendSample& Sample : BlendSpace->GetBlendSamples()) SortedSamples.Add(&Sample);
		SortedSamples.Sort([](const FBlendSample& Left, const FBlendSample& Right)
		{
			if (Left.SampleValue.X != Right.SampleValue.X) return Left.SampleValue.X < Right.SampleValue.X;
			if (Left.SampleValue.Y != Right.SampleValue.Y) return Left.SampleValue.Y < Right.SampleValue.Y;
			if (Left.SampleValue.Z != Right.SampleValue.Z) return Left.SampleValue.Z < Right.SampleValue.Z;
			return ObjectPathOrEmpty(Left.Animation.Get()) < ObjectPathOrEmpty(Right.Animation.Get());
		});
		TArray<TSharedPtr<FJsonValue>> Samples;
		for (int32 SampleIndex = 0; SampleIndex < SortedSamples.Num(); ++SampleIndex)
		{
			const FBlendSample& Sample = *SortedSamples[SampleIndex];
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetNumberField(TEXT("index"), SampleIndex);
			Json->SetStringField(TEXT("animationPath"), ObjectPathOrEmpty(Sample.Animation.Get()));
			Json->SetObjectField(TEXT("value"), VectorToJson(Sample.SampleValue));
			Json->SetNumberField(TEXT("rateScale"), Sample.RateScale);
#if WITH_EDITORONLY_DATA
			Json->SetBoolField(TEXT("includeInAnalyzeAll"), Sample.bIncludeInAnalyseAll);
			Json->SetBoolField(TEXT("valid"), Sample.bIsValid);
#endif
			Samples.Add(MakeShared<FJsonValueObject>(Json));
		}
		OutDetails->SetNumberField(TEXT("sampleCount"), Samples.Num());
		OutDetails->SetArrayField(TEXT("samples"), Samples);
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadDataTable(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UDataTable* Table = Cast<UDataTable>(AssetData.GetAsset());
		if (Table == nullptr)
		{
			OutError = TEXT("Failed to load DataTable asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("data-table"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("rowStructPath"), ObjectPathOrEmpty(Table->GetRowStruct()));
		TArray<FName> RowNames = Table->GetRowNames();
		RowNames.Sort(FNameLexicalLess());
		TArray<TSharedPtr<FJsonValue>> RowNameValues;
		for (const FName RowName : RowNames) RowNameValues.Add(MakeShared<FJsonValueString>(RowName.ToString()));
		OutDetails->SetNumberField(TEXT("rowCount"), RowNames.Num());
		OutDetails->SetArrayField(TEXT("rowNames"), RowNameValues);

		const FString TableJson = Table->GetTableAsJSON(EDataTableExportFlags::None);
		TArray<TSharedPtr<FJsonValue>> ParsedRows;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(TableJson);
		if (!FJsonSerializer::Deserialize(Reader, ParsedRows))
		{
			OutError = TEXT("Failed to parse DataTable JSON export.");
			return EAssetReaderStatus::Failed;
		}
		ParsedRows.Sort([](const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right)
		{
			FString LeftName;
			FString RightName;
			const TSharedPtr<FJsonObject> LeftObject = Left.IsValid() ? Left->AsObject() : nullptr;
			const TSharedPtr<FJsonObject> RightObject = Right.IsValid() ? Right->AsObject() : nullptr;
			if (LeftObject.IsValid()) LeftObject->TryGetStringField(TEXT("Name"), LeftName);
			if (RightObject.IsValid()) RightObject->TryGetStringField(TEXT("Name"), RightName);
			return LeftName < RightName;
		});
		OutDetails->SetArrayField(TEXT("rows"), ParsedRows);
		return EAssetReaderStatus::Success;
	}

	EAssetReaderStatus ReadDataAsset(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UDataAsset* DataAsset = Cast<UDataAsset>(AssetData.GetAsset());
		if (DataAsset == nullptr)
		{
			OutError = TEXT("Failed to load Data Asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("data-asset"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("classPath"), DataAsset->GetClass()->GetPathName());
		const FPrimaryAssetId PrimaryAssetId = DataAsset->GetPrimaryAssetId();
		const bool bHasPrimaryAssetId = PrimaryAssetId.IsValid();
		OutDetails->SetBoolField(TEXT("hasPrimaryAssetId"), bHasPrimaryAssetId);
		OutDetails->SetStringField(TEXT("primaryAssetType"), bHasPrimaryAssetId ? PrimaryAssetId.PrimaryAssetType.ToString() : FString());
		OutDetails->SetStringField(TEXT("primaryAssetName"), bHasPrimaryAssetId ? PrimaryAssetId.PrimaryAssetName.ToString() : FString());
		OutDetails->SetStringField(TEXT("primaryAssetId"), bHasPrimaryAssetId ? PrimaryAssetId.ToString() : FString());

		const EPropertyFlags IncludeFlags = CPF_Edit | CPF_BlueprintVisible | CPF_Config | CPF_AssetRegistrySearchable;
		const EPropertyFlags SkipFlags = CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient | CPF_Deprecated;
		TArray<FProperty*> Properties;
		int32 SkippedPropertyCount = 0;
		for (TFieldIterator<FProperty> Iterator(DataAsset->GetClass(), EFieldIterationFlags::IncludeSuper); Iterator; ++Iterator)
		{
			FProperty* Property = *Iterator;
			if (!Property->HasAnyPropertyFlags(IncludeFlags) || Property->HasAnyPropertyFlags(SkipFlags))
			{
				++SkippedPropertyCount;
				continue;
			}
			Properties.Add(Property);
		}
		Properties.Sort([](const FProperty& Left, const FProperty& Right)
		{
			return Left.GetName() < Right.GetName();
		});

		FJsonObjectConverter::CustomExportCallback ExportCallback;
		ExportCallback.BindLambda([](FProperty* Property, const void* Value) -> TSharedPtr<FJsonValue>
		{
			if (const FSoftObjectProperty* SoftObjectProperty = CastField<FSoftObjectProperty>(Property))
			{
				const FSoftObjectPtr SoftObject = SoftObjectProperty->GetPropertyValue(Value);
				return MakeShared<FJsonValueString>(SoftObject.ToSoftObjectPath().ToString());
			}
			if (const FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
			{
				const UObject* Object = ObjectProperty->GetObjectPropertyValue(Value);
				return MakeShared<FJsonValueString>(ObjectPathOrEmpty(Object));
			}
			return nullptr;
		});

		TArray<TSharedPtr<FJsonValue>> PropertyValues;
		int32 ConversionFailureCount = 0;
		for (FProperty* Property : Properties)
		{
			const void* Value = Property->ContainerPtrToValuePtr<void>(DataAsset);
			TSharedPtr<FJsonValue> JsonValue = FJsonObjectConverter::UPropertyToJsonValue(
				Property,
				Value,
				0,
				static_cast<int64>(SkipFlags),
				&ExportCallback,
				nullptr,
				EJsonObjectConversionFlags::SuppressClassNameForPersistentObject);

			TSharedRef<FJsonObject> PropertyObject = MakeShared<FJsonObject>();
			PropertyObject->SetStringField(TEXT("name"), Property->GetName());
			PropertyObject->SetStringField(TEXT("displayName"), Property->GetDisplayNameText().ToString());
			PropertyObject->SetStringField(TEXT("cppType"), Property->GetCPPType());
			PropertyObject->SetStringField(TEXT("propertyClass"), Property->GetClass()->GetName());
			PropertyObject->SetStringField(TEXT("ownerClassPath"), Property->GetOwnerClass() != nullptr ? Property->GetOwnerClass()->GetPathName() : FString());
			PropertyObject->SetStringField(TEXT("flagsHex"), FString::Printf(TEXT("0x%016llx"), static_cast<uint64>(Property->GetPropertyFlags())));
			PropertyObject->SetBoolField(TEXT("conversionSucceeded"), JsonValue.IsValid());
			if (JsonValue.IsValid())
			{
				PropertyObject->SetField(TEXT("value"), JsonValue);
			}
			else
			{
				++ConversionFailureCount;
				FString ExportedText;
				Property->ExportTextItem_Direct(ExportedText, Value, nullptr, DataAsset, PPF_None);
				PropertyObject->SetStringField(TEXT("fallbackExportText"), ExportedText.Left(262144));
				PropertyObject->SetBoolField(TEXT("fallbackTruncated"), ExportedText.Len() > 262144);
			}
			PropertyValues.Add(MakeShared<FJsonValueObject>(PropertyObject));
		}
		OutDetails->SetNumberField(TEXT("propertyCount"), PropertyValues.Num());
		OutDetails->SetNumberField(TEXT("skippedPropertyCount"), SkippedPropertyCount);
		OutDetails->SetNumberField(TEXT("conversionFailureCount"), ConversionFailureCount);
		OutDetails->SetArrayField(TEXT("properties"), PropertyValues);
		return EAssetReaderStatus::Success;
	}


	void AppendNiagaraRendererJson(
		const UNiagaraRendererProperties* Renderer,
		TArray<TSharedPtr<FJsonValue>>& OutRenderers)
	{
		if (Renderer == nullptr)
		{
			return;
		}
		TSharedRef<FJsonObject> RendererJson = MakeShared<FJsonObject>();
		RendererJson->SetStringField(TEXT("classPath"), Renderer->GetClass()->GetPathName());
		RendererJson->SetStringField(TEXT("objectName"), Renderer->GetName());
		RendererJson->SetBoolField(TEXT("enabled"), Renderer->GetIsEnabled());
		RendererJson->SetNumberField(TEXT("sortOrderHint"), Renderer->SortOrderHint);
		RendererJson->SetBoolField(TEXT("allowInCullProxies"), Renderer->bAllowInCullProxies);
		OutRenderers.Add(MakeShared<FJsonValueObject>(RendererJson));
	}

	TSharedRef<FJsonObject> NiagaraScriptToJson(const UNiagaraScript* Script)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetBoolField(TEXT("available"), Script != nullptr);
		if (Script != nullptr)
		{
			Json->SetStringField(TEXT("path"), Script->GetPathName());
			Json->SetStringField(TEXT("usage"), EnumNameOrValue<ENiagaraScriptUsage>(Script->GetUsage()));
			Json->SetNumberField(TEXT("usageValue"), static_cast<int32>(Script->GetUsage()));
			Json->SetStringField(TEXT("usageId"), Script->GetUsageId().ToString(EGuidFormats::DigitsWithHyphensLower));
		}
		return Json;
	}

	TArray<TSharedPtr<FJsonValue>> NiagaraParametersToJson(const FNiagaraParameterStore& Store)
	{
		TArray<FNiagaraVariable> Parameters;
		Store.GetParameters(Parameters);
		Parameters.Sort([](const FNiagaraVariable& Left, const FNiagaraVariable& Right)
		{
			const FString LeftKey = Left.GetName().ToString() + TEXT("|") + Left.GetType().GetName();
			const FString RightKey = Right.GetName().ToString() + TEXT("|") + Right.GetType().GetName();
			return LeftKey < RightKey;
		});

		TArray<TSharedPtr<FJsonValue>> Result;
		for (const FNiagaraVariable& Parameter : Parameters)
		{
			const FNiagaraTypeDefinition& Type = Parameter.GetType();
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Parameter.GetName().ToString());
			Json->SetStringField(TEXT("type"), Type.GetName());
			Json->SetNumberField(TEXT("sizeBytes"), Parameter.GetSizeInBytes());
			Json->SetBoolField(TEXT("dataInterface"), Parameter.IsDataInterface());
			Json->SetBoolField(TEXT("uobject"), Parameter.IsUObject());
			Json->SetStringField(TEXT("objectPath"), FString());
			Json->SetStringField(TEXT("defaultValueHex"), FString());

			if (Parameter.IsDataInterface())
			{
				Json->SetStringField(TEXT("objectPath"), ObjectPathOrEmpty(Store.GetDataInterface(Parameter)));
			}
			else if (Parameter.IsUObject())
			{
				Json->SetStringField(TEXT("objectPath"), ObjectPathOrEmpty(Store.GetUObject(Parameter).Get()));
			}
			else if (Parameter.GetSizeInBytes() > 0)
			{
				const uint8* Data = Store.GetParameterData(Parameter);
				if (Data != nullptr)
				{
					Json->SetStringField(TEXT("defaultValueHex"), BytesToHex(Data, Parameter.GetSizeInBytes()));
				}
			}
			Result.Add(MakeShared<FJsonValueObject>(Json));
		}
		return Result;
	}

	EAssetReaderStatus ReadNiagaraSystem(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UNiagaraSystem* System = Cast<UNiagaraSystem>(AssetData.GetAsset());
		if (System == nullptr)
		{
			OutError = TEXT("Failed to load Niagara System asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("niagara-system"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("effectTypePath"), ObjectPathOrEmpty(System->GetEffectType()));
		OutDetails->SetBoolField(TEXT("deterministic"), GetReflectedBool(System, TEXT("bDeterminism")));
		OutDetails->SetNumberField(TEXT("randomSeed"), System->GetRandomSeed());

		TSharedRef<FJsonObject> Warmup = MakeShared<FJsonObject>();
		Warmup->SetBoolField(TEXT("needed"), System->NeedsWarmup());
		Warmup->SetNumberField(TEXT("time"), System->GetWarmupTime());
		Warmup->SetNumberField(TEXT("tickCount"), System->GetWarmupTickCount());
		Warmup->SetNumberField(TEXT("tickDelta"), System->GetWarmupTickDelta());
		OutDetails->SetObjectField(TEXT("warmup"), Warmup);

		TSharedRef<FJsonObject> FixedTick = MakeShared<FJsonObject>();
		FixedTick->SetBoolField(TEXT("enabled"), System->HasFixedTickDelta());
		FixedTick->SetNumberField(TEXT("deltaTime"), System->GetFixedTickDeltaTime());
		const TOptional<float> MaxDeltaTime = System->GetMaxDeltaTime();
		FixedTick->SetBoolField(TEXT("hasMaxDeltaTime"), MaxDeltaTime.IsSet());
		FixedTick->SetNumberField(TEXT("maxDeltaTime"), MaxDeltaTime.Get(0.0f));
		OutDetails->SetObjectField(TEXT("fixedTick"), FixedTick);

		TSharedRef<FJsonObject> FixedBounds = BoxToJson(System->GetFixedBounds());
		FixedBounds->SetBoolField(TEXT("enabled"), GetReflectedBool(System, TEXT("bFixedBounds")));
		OutDetails->SetObjectField(TEXT("fixedBounds"), FixedBounds);
		OutDetails->SetObjectField(TEXT("systemSpawnScript"), NiagaraScriptToJson(System->GetSystemSpawnScript()));
		OutDetails->SetObjectField(TEXT("systemUpdateScript"), NiagaraScriptToJson(System->GetSystemUpdateScript()));

		TArray<TSharedPtr<FJsonValue>> ExposedParameters = NiagaraParametersToJson(System->GetExposedParameters());
		OutDetails->SetNumberField(TEXT("exposedParameterCount"), ExposedParameters.Num());
		OutDetails->SetArrayField(TEXT("exposedParameters"), ExposedParameters);

		TArray<TSharedPtr<FJsonValue>> Emitters;
		const TArray<FNiagaraEmitterHandle>& Handles = System->GetEmitterHandles();
		for (int32 HandleIndex = 0; HandleIndex < Handles.Num(); ++HandleIndex)
		{
			const FNiagaraEmitterHandle& Handle = Handles[HandleIndex];
			TSharedRef<FJsonObject> EmitterJson = MakeShared<FJsonObject>();
			EmitterJson->SetNumberField(TEXT("index"), HandleIndex);
			EmitterJson->SetStringField(TEXT("id"), Handle.GetId().ToString(EGuidFormats::DigitsWithHyphensLower));
			EmitterJson->SetStringField(TEXT("name"), Handle.GetName().ToString());
			EmitterJson->SetBoolField(TEXT("enabled"), Handle.GetIsEnabled());
			EmitterJson->SetStringField(TEXT("mode"), EnumNameOrValue<ENiagaraEmitterMode>(Handle.GetEmitterMode()));
			EmitterJson->SetNumberField(TEXT("modeValue"), static_cast<int32>(Handle.GetEmitterMode()));

			const FVersionedNiagaraEmitter Instance = Handle.GetInstance();
			EmitterJson->SetStringField(TEXT("emitterAssetPath"), ObjectPathOrEmpty(Instance.Emitter.Get()));
			EmitterJson->SetStringField(TEXT("version"), Instance.Version.ToString(EGuidFormats::DigitsWithHyphensLower));
			FVersionedNiagaraEmitterData* EmitterData = Handle.GetEmitterData();
			EmitterJson->SetBoolField(TEXT("emitterDataAvailable"), EmitterData != nullptr);

			TArray<TSharedPtr<FJsonValue>> RendererValues;
			TArray<TSharedPtr<FJsonValue>> ScriptValues;
			if (EmitterData != nullptr)
			{
				EmitterJson->SetBoolField(TEXT("localSpace"), EmitterData->bLocalSpace);
				EmitterJson->SetBoolField(TEXT("deterministic"), EmitterData->bDeterminism);
				EmitterJson->SetNumberField(TEXT("randomSeed"), EmitterData->RandomSeed);
				EmitterJson->SetStringField(TEXT("simTarget"), EnumNameOrValue<ENiagaraSimTarget>(EmitterData->SimTarget));
				EmitterJson->SetNumberField(TEXT("simTargetValue"), static_cast<int32>(EmitterData->SimTarget));
				EmitterJson->SetStringField(TEXT("boundsMode"), EnumNameOrValue<ENiagaraEmitterCalculateBoundMode>(EmitterData->CalculateBoundsMode));
				EmitterJson->SetNumberField(TEXT("boundsModeValue"), static_cast<int32>(EmitterData->CalculateBoundsMode));
				EmitterJson->SetObjectField(TEXT("fixedBounds"), BoxToJson(EmitterData->FixedBounds));
				EmitterJson->SetBoolField(TEXT("requiresPersistentIds"), EmitterData->RequiresPersistentIDs());
				EmitterJson->SetNumberField(TEXT("eventHandlerCount"), EmitterData->GetEventHandlers().Num());
				EmitterJson->SetNumberField(TEXT("simulationStageCount"), EmitterData->GetSimulationStages().Num());

				TArray<UNiagaraRendererProperties*> Renderers = EmitterData->GetRenderers();
				Renderers.Sort([](const UNiagaraRendererProperties& Left, const UNiagaraRendererProperties& Right)
				{
					const FString LeftKey = Left.GetClass()->GetPathName() + TEXT("|") + Left.GetName();
					const FString RightKey = Right.GetClass()->GetPathName() + TEXT("|") + Right.GetName();
					return LeftKey < RightKey;
				});
				for (const UNiagaraRendererProperties* Renderer : Renderers)
				{
					AppendNiagaraRendererJson(Renderer, RendererValues);
				}

				TArray<UNiagaraScript*> Scripts;
				EmitterData->GetScripts(Scripts, false, false);
				Scripts.Sort([](const UNiagaraScript& Left, const UNiagaraScript& Right)
				{
					const FString LeftKey = EnumNameOrValue<ENiagaraScriptUsage>(Left.GetUsage()) + TEXT("|") + Left.GetPathName();
					const FString RightKey = EnumNameOrValue<ENiagaraScriptUsage>(Right.GetUsage()) + TEXT("|") + Right.GetPathName();
					return LeftKey < RightKey;
				});
				for (const UNiagaraScript* Script : Scripts)
				{
					if (Script != nullptr)
					{
						ScriptValues.Add(MakeShared<FJsonValueObject>(NiagaraScriptToJson(Script)));
					}
				}
			}
			else
			{
				EmitterJson->SetBoolField(TEXT("localSpace"), false);
				EmitterJson->SetBoolField(TEXT("deterministic"), false);
				EmitterJson->SetNumberField(TEXT("randomSeed"), 0);
				EmitterJson->SetStringField(TEXT("simTarget"), FString());
				EmitterJson->SetNumberField(TEXT("simTargetValue"), 0);
				EmitterJson->SetStringField(TEXT("boundsMode"), FString());
				EmitterJson->SetNumberField(TEXT("boundsModeValue"), 0);
				EmitterJson->SetObjectField(TEXT("fixedBounds"), BoxToJson(FBox()));
				EmitterJson->SetBoolField(TEXT("requiresPersistentIds"), false);
				EmitterJson->SetNumberField(TEXT("eventHandlerCount"), 0);
				EmitterJson->SetNumberField(TEXT("simulationStageCount"), 0);
			}

			const bool bStateless = Handle.GetEmitterMode() == ENiagaraEmitterMode::Stateless;
			UObject* StatelessEmitter = bStateless
				? reinterpret_cast<UObject*>(Handle.GetStatelessEmitter())
				: nullptr;
			if (StatelessEmitter != nullptr && !IsValid(StatelessEmitter))
			{
				StatelessEmitter = nullptr;
			}
			EmitterJson->SetBoolField(TEXT("statelessEmitterAvailable"), StatelessEmitter != nullptr);
			EmitterJson->SetStringField(TEXT("statelessEmitterPath"), ObjectPathOrEmpty(StatelessEmitter));
			EmitterJson->SetStringField(TEXT("statelessEmitterClassPath"), StatelessEmitter != nullptr ? StatelessEmitter->GetClass()->GetPathName() : FString());

			TArray<TSharedPtr<FJsonValue>> StatelessModules;
			if (StatelessEmitter != nullptr)
			{
				EmitterJson->SetNumberField(TEXT("statelessSpawnInfoCount"), GetReflectedArrayCount(StatelessEmitter, TEXT("SpawnInfos")));
				for (const UObject* Module : GetReflectedObjectArray(StatelessEmitter, TEXT("Modules")))
				{
					TSharedRef<FJsonObject> ModuleJson = MakeShared<FJsonObject>();
					ModuleJson->SetStringField(TEXT("classPath"), Module->GetClass()->GetPathName());
					ModuleJson->SetStringField(TEXT("objectName"), Module->GetName());
					ModuleJson->SetBoolField(TEXT("enabled"), GetReflectedBool(Module, TEXT("bModuleEnabled"), true));
					StatelessModules.Add(MakeShared<FJsonValueObject>(ModuleJson));
				}
				EmitterJson->SetBoolField(TEXT("deterministic"), GetReflectedBool(StatelessEmitter, TEXT("bDeterministic")));
				EmitterJson->SetNumberField(TEXT("randomSeed"), GetReflectedInt(StatelessEmitter, TEXT("RandomSeed")));
				EmitterJson->SetObjectField(TEXT("fixedBounds"), GetReflectedBox(StatelessEmitter, TEXT("FixedBounds")));
				RendererValues.Reset();
				TArray<UNiagaraRendererProperties*> StatelessRenderers;
				for (UObject* Object : GetReflectedObjectArray(StatelessEmitter, TEXT("RendererProperties")))
				{
					if (UNiagaraRendererProperties* Renderer = Cast<UNiagaraRendererProperties>(Object))
					{
						StatelessRenderers.Add(Renderer);
					}
				}
				StatelessRenderers.Sort([](const UNiagaraRendererProperties& Left, const UNiagaraRendererProperties& Right)
				{
					const FString LeftKey = Left.GetClass()->GetPathName() + TEXT("|") + Left.GetName();
					const FString RightKey = Right.GetClass()->GetPathName() + TEXT("|") + Right.GetName();
					return LeftKey < RightKey;
				});
				for (const UNiagaraRendererProperties* Renderer : StatelessRenderers)
				{
					AppendNiagaraRendererJson(Renderer, RendererValues);
				}
			}
			else
			{
				EmitterJson->SetNumberField(TEXT("statelessSpawnInfoCount"), 0);
			}
			EmitterJson->SetNumberField(TEXT("statelessModuleCount"), StatelessModules.Num());
			EmitterJson->SetArrayField(TEXT("statelessModules"), StatelessModules);

			EmitterJson->SetNumberField(TEXT("rendererCount"), RendererValues.Num());
			EmitterJson->SetArrayField(TEXT("renderers"), RendererValues);
			EmitterJson->SetNumberField(TEXT("scriptCount"), ScriptValues.Num());
			EmitterJson->SetArrayField(TEXT("scripts"), ScriptValues);
			Emitters.Add(MakeShared<FJsonValueObject>(EmitterJson));
		}
		OutDetails->SetNumberField(TEXT("emitterCount"), Emitters.Num());
		OutDetails->SetArrayField(TEXT("emitters"), Emitters);
		return EAssetReaderStatus::Success;
	}

}

EAssetReaderStatus FAssetReaderRegistry::ReadAssetDetails(
	const FAssetData& AssetData,
	TSharedRef<FJsonObject>& OutDetails,
	FString& OutReaderName,
	FString& OutError)
{
	OutDetails = MakeShared<FJsonObject>();
	OutReaderName = TEXT("generic");
	OutError.Reset();

	if (AssetData.AssetClassPath == UStaticMesh::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("static-mesh-v1");
		return AssetReaderRegistryPrivate::ReadStaticMesh(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == USkeletalMesh::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("skeletal-mesh-v1");
		return AssetReaderRegistryPrivate::ReadSkeletalMesh(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == USkeleton::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("skeleton-v1");
		return AssetReaderRegistryPrivate::ReadSkeleton(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UPhysicsAsset::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("physics-asset-v1");
		return AssetReaderRegistryPrivate::ReadPhysicsAsset(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UMaterialFunction::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("material-function-v1");
		return AssetReaderRegistryPrivate::ReadMaterialFunction(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UMaterial::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("material-v1");
		return AssetReaderRegistryPrivate::ReadMaterial(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UMaterialInstanceConstant::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("material-instance-v1");
		return AssetReaderRegistryPrivate::ReadMaterialInstance(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UTexture2D::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("texture-2d-v1");
		return AssetReaderRegistryPrivate::ReadTexture2D(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UAnimSequence::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("anim-sequence-v1");
		return AssetReaderRegistryPrivate::ReadAnimSequence(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UAnimMontage::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("anim-montage-v1");
		return AssetReaderRegistryPrivate::ReadAnimMontage(AssetData, OutDetails, OutError);
	}
	if (const UClass* AssetClass = AssetData.GetClass(); AssetClass != nullptr && AssetClass->IsChildOf(UBlendSpace::StaticClass()))
	{
		OutReaderName = TEXT("blend-space-v1");
		return AssetReaderRegistryPrivate::ReadBlendSpace(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UDataTable::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("data-table-v1");
		return AssetReaderRegistryPrivate::ReadDataTable(AssetData, OutDetails, OutError);
	}
	if (AssetData.AssetClassPath == UNiagaraSystem::StaticClass()->GetClassPathName())
	{
		OutReaderName = TEXT("niagara-system-v1");
		return AssetReaderRegistryPrivate::ReadNiagaraSystem(AssetData, OutDetails, OutError);
	}
	if (const UClass* AssetClass = AssetData.GetClass(); AssetClass != nullptr && AssetClass->IsChildOf(UDataAsset::StaticClass()))
	{
		OutReaderName = TEXT("data-asset-v1");
		return AssetReaderRegistryPrivate::ReadDataAsset(AssetData, OutDetails, OutError);
	}
	return EAssetReaderStatus::NotHandled;
}

const TCHAR* FAssetReaderRegistry::StatusToString(const EAssetReaderStatus Status)
{
	switch (Status)
	{
	case EAssetReaderStatus::Success:
		return TEXT("success");
	case EAssetReaderStatus::Failed:
		return TEXT("failed");
	default:
		return TEXT("not-handled");
	}
}
