#include "AssetReaders/AssetReaderCommon.h"
#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Animation/AnimBlueprint.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimSequence.h"
#include "Animation/Skeleton.h"
#include "ClothingAssetBase.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Editor.h"
#include "Engine/SkeletalMesh.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Rendering/SkinWeightVertexBuffer.h"
#include "UObject/UObjectGlobals.h"

bool FUEAgentKitEditorBridge::TryInspectSkeletalSecondaryMotionResult(
	const FString& SkeletalMeshPath,
	const FString& AnimationPath,
	const FString& AnimationBlueprintPath,
	bool bLoadIfNeeded,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	if (GEditor == nullptr)
	{
		OutErrorCode = TEXT("live-editor-unavailable");
		OutErrorMessage = TEXT("The Unreal Editor is unavailable.");
		return false;
	}
	if (GEditor->PlayWorld != nullptr)
	{
		OutErrorCode = TEXT("retarget_editor_state_invalid");
		OutErrorMessage = TEXT("Skeletal secondary-motion inspection is unavailable while PIE or SIE is active.");
		return false;
	}
	if (SkeletalMeshPath.IsEmpty() || !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(SkeletalMeshPath))
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("skeletalMeshPath must be an exact /Game Object Path.");
		return false;
	}
	if (!AnimationPath.IsEmpty() && !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AnimationPath))
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("animationPath must be an exact /Game Object Path.");
		return false;
	}
	if (!AnimationBlueprintPath.IsEmpty() && !UEAgentKitEditorBridgePrivate::IsSafeGameAssetPath(AnimationBlueprintPath))
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("animationBlueprintPath must be an exact /Game Object Path.");
		return false;
	}

	UObject* ExistingMesh = StaticFindObject(UObject::StaticClass(), nullptr, *SkeletalMeshPath, false);
	const bool bMeshLoadedBefore = ExistingMesh != nullptr;
	USkeletalMesh* SkeletalMesh = Cast<USkeletalMesh>(ExistingMesh);
	if (SkeletalMesh == nullptr && bLoadIfNeeded)
	{
		SkeletalMesh = LoadObject<USkeletalMesh>(nullptr, *SkeletalMeshPath);
	}
	if (SkeletalMesh == nullptr)
	{
		OutErrorCode = TEXT("retarget_asset_not_found");
		OutErrorMessage = TEXT("skeletalMeshPath must resolve to a Skeletal Mesh asset.");
		return false;
	}

	TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("action"), TEXT("inspect-skeletal-secondary-motion"));
	Result->SetBoolField(TEXT("loadIfNeeded"), bLoadIfNeeded);

	// --- Skeletal Mesh summary -------------------------------------------------
	USkeleton* Skeleton = SkeletalMesh->GetSkeleton();
	const int32 LodCount = SkeletalMesh->GetLODNum();

	TSharedRef<FJsonObject> MeshJson = MakeShared<FJsonObject>();
	MeshJson->SetStringField(TEXT("path"), SkeletalMeshPath);
	MeshJson->SetBoolField(TEXT("loadedBefore"), bMeshLoadedBefore);
	MeshJson->SetBoolField(TEXT("loadedByBridge"), !bMeshLoadedBefore && SkeletalMesh != nullptr);
	MeshJson->SetStringField(
		TEXT("skeletonPath"),
		Skeleton != nullptr ? Skeleton->GetPathName() : FString());
	MeshJson->SetStringField(
		TEXT("physicsAssetPath"),
		AssetReaderRegistryPrivate::ObjectPathOrEmpty(SkeletalMesh->GetPhysicsAsset()));
	MeshJson->SetNumberField(TEXT("lodCount"), LodCount);

	// Skin weight presence summary (LOD 0). Exact per-bone influence enumeration is
	// intentionally omitted: it is render-thread data whose layout is engine-version
	// sensitive, and presence + influence count is sufficient for classification.
	int32 VertexCount = 0;
	int32 MaxBoneInfluences = 0;
	const FSkeletalMeshRenderData* RenderData = SkeletalMesh->GetResourceForRendering();
	if (RenderData != nullptr && RenderData->LODRenderData.Num() > 0)
	{
		const FSkeletalMeshLODRenderData& LOD0 = RenderData->LODRenderData[0];
		VertexCount = static_cast<int32>(LOD0.GetNumVertices());
		const FSkinWeightVertexBuffer* SkinWeightBuffer = LOD0.GetSkinWeightVertexBuffer();
		if (SkinWeightBuffer != nullptr)
		{
			MaxBoneInfluences = static_cast<int32>(SkinWeightBuffer->GetMaxBoneInfluences());
		}
	}
	MeshJson->SetNumberField(TEXT("vertexCount"), VertexCount);
	MeshJson->SetNumberField(TEXT("maxBoneInfluences"), MaxBoneInfluences);
	MeshJson->SetBoolField(TEXT("hasSkinWeights"), VertexCount > 0 && MaxBoneInfluences > 0);
	Result->SetObjectField(TEXT("skeletalMesh"), MeshJson);

	// --- Skeleton hierarchy ------------------------------------------------------
	TSharedRef<FJsonObject> SkeletonJson = MakeShared<FJsonObject>();
	Result->SetObjectField(TEXT("skeleton"), SkeletonJson);
	if (Skeleton == nullptr)
	{
		SkeletonJson->SetNumberField(TEXT("boneCount"), 0);
		SkeletonJson->SetStringField(TEXT("rootBoneName"), FString());
		SkeletonJson->SetArrayField(TEXT("bones"), TArray<TSharedPtr<FJsonValue>>());
	}
	else
	{
		const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 BoneCount = RefSkeleton.GetNum();
		SkeletonJson->SetNumberField(TEXT("boneCount"), BoneCount);
		SkeletonJson->SetStringField(
			TEXT("rootBoneName"),
			BoneCount > 0 ? RefSkeleton.GetBoneName(0).ToString() : FString());

		const int32 MaxBones = FMath::Min(BoneCount, 512);
		TArray<TSharedPtr<FJsonValue>> Bones;
		for (int32 BoneIndex = 0; BoneIndex < MaxBones; ++BoneIndex)
		{
			const int32 ParentIndex = RefSkeleton.GetParentIndex(BoneIndex);
			TSharedRef<FJsonObject> Bone = MakeShared<FJsonObject>();
			Bone->SetNumberField(TEXT("index"), BoneIndex);
			Bone->SetStringField(TEXT("name"), RefSkeleton.GetBoneName(BoneIndex).ToString());
			Bone->SetNumberField(TEXT("parentIndex"), ParentIndex);
			Bone->SetStringField(
				TEXT("parentName"),
				ParentIndex != INDEX_NONE ? RefSkeleton.GetBoneName(ParentIndex).ToString() : FString());
			Bones.Add(MakeShared<FJsonValueObject>(Bone));
		}
		SkeletonJson->SetArrayField(TEXT("bones"), Bones);
	}

	// --- Physics Asset ------------------------------------------------------------
	TSharedRef<FJsonObject> PhysicsJson = MakeShared<FJsonObject>();
	Result->SetObjectField(TEXT("physics"), PhysicsJson);
	UPhysicsAsset* PhysicsAsset = SkeletalMesh->GetPhysicsAsset();
	PhysicsJson->SetBoolField(TEXT("present"), PhysicsAsset != nullptr);
	PhysicsJson->SetStringField(
		TEXT("path"),
		AssetReaderRegistryPrivate::ObjectPathOrEmpty(PhysicsAsset));
	PhysicsJson->SetNumberField(
		TEXT("bodyCount"),
		PhysicsAsset != nullptr ? PhysicsAsset->SkeletalBodySetups.Num() : 0);
	PhysicsJson->SetNumberField(
		TEXT("constraintCount"),
		PhysicsAsset != nullptr ? PhysicsAsset->ConstraintSetup.Num() : 0);

	// --- Clothing Assets ----------------------------------------------------------
	TSharedRef<FJsonObject> ClothJson = MakeShared<FJsonObject>();
	Result->SetObjectField(TEXT("cloth"), ClothJson);
	const TArray<UClothingAssetBase*>& ClothingAssets = SkeletalMesh->GetMeshClothingAssets();
	ClothJson->SetNumberField(TEXT("assetCount"), ClothingAssets.Num());
	TArray<TSharedPtr<FJsonValue>> ClothAssets;
	for (const UClothingAssetBase* Asset : ClothingAssets)
	{
		if (Asset == nullptr)
		{
			continue;
		}
		TSharedRef<FJsonObject> AssetJson = MakeShared<FJsonObject>();
		AssetJson->SetStringField(TEXT("name"), Asset->GetName());
		AssetJson->SetStringField(TEXT("className"), Asset->GetClass()->GetName());
		AssetJson->SetNumberField(TEXT("numLods"), Asset->GetNumLods());
		ClothAssets.Add(MakeShared<FJsonValueObject>(AssetJson));
	}
	ClothJson->SetArrayField(TEXT("assets"), ClothAssets);

	// --- Animation track coverage -------------------------------------------------
	TSharedRef<FJsonObject> AnimationJson = MakeShared<FJsonObject>();
	Result->SetObjectField(TEXT("animation"), AnimationJson);
	AnimationJson->SetStringField(TEXT("path"), AnimationPath);

	UAnimSequence* Sequence = nullptr;
	bool bAnimationLoadedBefore = false;
	if (!AnimationPath.IsEmpty())
	{
		UObject* ExistingAnimation = StaticFindObject(UObject::StaticClass(), nullptr, *AnimationPath, false);
		bAnimationLoadedBefore = ExistingAnimation != nullptr;
		Sequence = Cast<UAnimSequence>(ExistingAnimation);
		if (Sequence == nullptr && bLoadIfNeeded)
		{
			Sequence = LoadObject<UAnimSequence>(nullptr, *AnimationPath);
		}
	}
	AnimationJson->SetBoolField(TEXT("loadedBefore"), bAnimationLoadedBefore);

	USkeleton* AnimationSkeleton = Sequence != nullptr ? Sequence->GetSkeleton() : nullptr;
	const bool bSkeletonCompatible =
		AnimationSkeleton != nullptr && Skeleton != nullptr && AnimationSkeleton == Skeleton;

	FString AnimationStatus;
	if (Sequence == nullptr)
	{
		AnimationStatus = AnimationPath.IsEmpty()
			? TEXT("skipped-no-animation")
			: (bLoadIfNeeded ? TEXT("not-an-animation-sequence") : TEXT("not-loaded"));
	}
	else if (AnimationSkeleton == nullptr)
	{
		AnimationStatus = TEXT("missing-skeleton");
	}
	else if (Skeleton == nullptr)
	{
		AnimationStatus = TEXT("mesh-skeleton-unavailable");
	}
	else if (!bSkeletonCompatible)
	{
		AnimationStatus = TEXT("skeleton-mismatch");
	}
	else
	{
		AnimationStatus = TEXT("evaluated");
	}
	AnimationJson->SetStringField(TEXT("status"), AnimationStatus);
	AnimationJson->SetBoolField(TEXT("skeletonCompatible"), bSkeletonCompatible);

	int32 AnimatedBoneCount = 0;
	int32 TotalBoneCount = Skeleton != nullptr ? Skeleton->GetReferenceSkeleton().GetNum() : 0;
	if (AnimationStatus == TEXT("evaluated"))
	{
		const IAnimationDataModel* Model = Sequence->GetDataModel();
		const FReferenceSkeleton& RefSkeleton = Skeleton->GetReferenceSkeleton();
		const int32 MaxBones = FMath::Min(RefSkeleton.GetNum(), 512);
		for (int32 BoneIndex = 0; BoneIndex < MaxBones; ++BoneIndex)
		{
			if (Model != nullptr && Model->IsValidBoneTrackName(RefSkeleton.GetBoneName(BoneIndex)))
			{
				++AnimatedBoneCount;
			}
		}
	}
	AnimationJson->SetNumberField(TEXT("animatedBoneCount"), AnimatedBoneCount);
	AnimationJson->SetNumberField(TEXT("totalBoneCount"), TotalBoneCount);

	// --- Animation Blueprint secondary-motion nodes ---------------------------------
	TSharedRef<FJsonObject> AnimBlueprintJson = MakeShared<FJsonObject>();
	Result->SetObjectField(TEXT("animationBlueprint"), AnimBlueprintJson);
	AnimBlueprintJson->SetStringField(TEXT("path"), AnimationBlueprintPath);

	UAnimBlueprint* AnimBlueprint = nullptr;
	if (!AnimationBlueprintPath.IsEmpty())
	{
		UObject* ExistingBlueprint = StaticFindObject(UObject::StaticClass(), nullptr, *AnimationBlueprintPath, false);
		AnimBlueprint = Cast<UAnimBlueprint>(ExistingBlueprint);
		if (AnimBlueprint == nullptr && bLoadIfNeeded)
		{
			AnimBlueprint = LoadObject<UAnimBlueprint>(nullptr, *AnimationBlueprintPath);
		}
	}

	int32 SpringBoneCount = 0;
	int32 RigidBodyCount = 0;
	int32 AnimDynamicsCount = 0;
	TArray<TSharedPtr<FJsonValue>> SecondaryMotionNodes;

	FString AnimBlueprintStatus;
	if (AnimBlueprint == nullptr)
	{
		AnimBlueprintStatus = AnimationBlueprintPath.IsEmpty()
			? TEXT("skipped-no-animation-blueprint")
			: (bLoadIfNeeded ? TEXT("not-an-anim-blueprint") : TEXT("not-loaded"));
	}
	else
	{
		TArray<UEdGraph*> Graphs;
		AnimBlueprint->GetAllGraphs(Graphs);
		for (const UEdGraph* Graph : Graphs)
		{
			if (Graph == nullptr)
			{
				continue;
			}
			for (const TObjectPtr<UEdGraphNode>& Node : Graph->Nodes)
			{
				if (Node == nullptr)
				{
					continue;
				}
				const FString ClassName = Node->GetClass()->GetName();
				if (ClassName != TEXT("AnimGraphNode_SpringBone") &&
					ClassName != TEXT("AnimGraphNode_RigidBody") &&
					ClassName != TEXT("AnimGraphNode_AnimDynamics"))
				{
					continue;
				}
				TSharedRef<FJsonObject> NodeJson = MakeShared<FJsonObject>();
				NodeJson->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString());
				NodeJson->SetStringField(TEXT("className"), ClassName);
				SecondaryMotionNodes.Add(MakeShared<FJsonValueObject>(NodeJson));

				if (ClassName == TEXT("AnimGraphNode_SpringBone"))
				{
					++SpringBoneCount;
				}
				else if (ClassName == TEXT("AnimGraphNode_RigidBody"))
				{
					++RigidBodyCount;
				}
				else if (ClassName == TEXT("AnimGraphNode_AnimDynamics"))
				{
					++AnimDynamicsCount;
				}
			}
		}
		AnimBlueprintStatus = TEXT("evaluated");
	}
	AnimBlueprintJson->SetStringField(TEXT("status"), AnimBlueprintStatus);
	AnimBlueprintJson->SetNumberField(TEXT("secondaryMotionNodeCount"), SecondaryMotionNodes.Num());
	AnimBlueprintJson->SetNumberField(TEXT("springBoneCount"), SpringBoneCount);
	AnimBlueprintJson->SetNumberField(TEXT("rigidBodyCount"), RigidBodyCount);
	AnimBlueprintJson->SetNumberField(TEXT("animDynamicsCount"), AnimDynamicsCount);
	AnimBlueprintJson->SetArrayField(TEXT("nodes"), SecondaryMotionNodes);

	Result->SetStringField(TEXT("editorSessionId"), SessionId);
	OutResult = Result;
	return true;
}
