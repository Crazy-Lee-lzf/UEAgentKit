#pragma once

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
#include "Components/ActorComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/Level.h"
#include "Engine/LevelStreaming.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "GameFramework/DamageType.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/WorldSettings.h"
#include "WorldPartition/DataLayer/DataLayerInstanceNames.h"
#include "WorldPartition/DataLayer/WorldDataLayers.h"
#include "WorldPartition/WorldPartition.h"
#include "WorldPartition/WorldPartitionActorDescInstance.h"
#include "WorldPartition/WorldPartitionHelpers.h"
#include "UObject/UnrealType.h"

namespace AssetReaderRegistryPrivate
{
	TSharedRef<FJsonObject> VectorToJson(const FVector& Value);

	TSharedRef<FJsonObject> RotatorToJson(const FRotator& Value);

	TSharedRef<FJsonObject> QuatToJson(const FQuat& Value);

	TSharedRef<FJsonObject> TransformToJson(const FTransform& Value);

	TSharedRef<FJsonObject> BoundsToJson(const FBoxSphereBounds& Bounds);

	TSharedRef<FJsonObject> BoxToJson(const FBox& Value);

	TArray<TSharedPtr<FJsonValue>> NamesToJson(TArray<FName> Values);

	TArray<TSharedPtr<FJsonValue>> StringCountsToJson(const TMap<FString, int32>& Counts);

	bool GetReflectedBool(const UObject* Object, const FName PropertyName, const bool DefaultValue = false);

	UObject* GetReflectedObject(const UObject* Object, const FName PropertyName);

	int32 GetReflectedInt(const UObject* Object, const FName PropertyName, const int32 DefaultValue = 0);

	int32 GetReflectedArrayCount(const UObject* Object, const FName PropertyName);

	TArray<UObject*> GetReflectedObjectArray(const UObject* Object, const FName PropertyName);

	TSharedRef<FJsonObject> GetReflectedBox(const UObject* Object, const FName PropertyName);

	FString ObjectPathOrEmpty(const UObject* Object);

	TSharedRef<FJsonObject> LinearColorToJson(const FLinearColor& Value);

	TSharedRef<FJsonObject> Vector4dToJson(const FVector4d& Value);

	const TCHAR* BlendModeToString(const EBlendMode Value);

	const TCHAR* FunctionInputTypeToString(const EFunctionInputType Value);

	const TCHAR* ParameterAssociationToString(const EMaterialParameterAssociation Association);

	TSharedRef<FJsonObject> MaterialParameterInfoToJson(const FMaterialParameterInfo& Info);

	FString MaterialParameterSortKey(const FMaterialParameterInfo& Info);

	template <typename EnumType>
	FString EnumNameOrValue(const EnumType Value)
	{
		if (const UEnum* Enum = StaticEnum<EnumType>())
		{
			return Enum->GetNameStringByValue(static_cast<int64>(Value));
		}
		return FString::FromInt(static_cast<int32>(Value));
	}

	TSharedRef<FJsonObject> FrameRateToJson(const FFrameRate& Value);

	const TCHAR* AdditiveAnimationTypeToString(const EAdditiveAnimationType Value);

	const TCHAR* AdditiveBasePoseTypeToString(const EAdditiveBasePoseType Value);

	const TCHAR* RootMotionRootLockToString(const ERootMotionRootLock::Type Value);

	const TCHAR* NotifyTriggerModeToString(const ENotifyTriggerMode::Type Value);

	TArray<TSharedPtr<FJsonValue>> BuildNotifyArray(UAnimSequenceBase* Asset, FString& OutError);

	int32 GetReflectedArrayCount(UObject* Object, const FName PropertyName);

	TArray<TSharedPtr<FJsonValue>> BuildCurveArray(const UAnimSequenceBase* Asset);

	const TCHAR* CollisionTraceFlagToString(const ECollisionTraceFlag Value);

	TArray<TSharedPtr<FJsonValue>> BuildSkeletalSocketArray(const TArray<USkeletalMeshSocket*>& SourceSockets);

	TArray<TSharedPtr<FJsonValue>> BuildSkeletalMaterials(const TArray<FSkeletalMaterial>& SourceMaterials);

}
