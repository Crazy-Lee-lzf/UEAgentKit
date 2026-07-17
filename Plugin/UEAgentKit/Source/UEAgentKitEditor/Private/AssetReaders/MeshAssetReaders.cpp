#include "AssetReaders/AssetReaderCommon.h"
#include "AssetReaders/AssetReaderImplementations.h"

namespace AssetReaderRegistryPrivate
{
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
}
