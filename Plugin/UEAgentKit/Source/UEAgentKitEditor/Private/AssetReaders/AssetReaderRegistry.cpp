#include "AssetReaders/AssetReaderRegistry.h"

#include "Animation/MorphTarget.h"
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
#include "Engine/Font.h"
#include "Engine/Texture2D.h"
#include "PhysicsEngine/AggregateGeom.h"
#include "PhysicsEngine/BodySetup.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/PhysicsConstraintTemplate.h"
#include "PhysicsEngine/SkeletalBodySetup.h"
#include "ReferenceSkeleton.h"

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
	}}

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
