#include "AssetReaders/AssetReaderRegistry.h"

#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshSocket.h"
#include "Materials/MaterialInterface.h"
#include "PhysicsEngine/AggregateGeom.h"
#include "PhysicsEngine/BodySetup.h"

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

	FString ObjectPathOrEmpty(const UObject* Object)
	{
		return Object != nullptr ? Object->GetPathName() : FString();
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

		const FBoxSphereBounds Bounds = StaticMesh->GetBounds();
		TSharedRef<FJsonObject> BoundsObject = MakeShared<FJsonObject>();
		BoundsObject->SetObjectField(TEXT("origin"), VectorToJson(Bounds.Origin));
		BoundsObject->SetObjectField(TEXT("boxExtent"), VectorToJson(Bounds.BoxExtent));
		BoundsObject->SetNumberField(TEXT("sphereRadius"), Bounds.SphereRadius);
		OutDetails->SetObjectField(TEXT("bounds"), BoundsObject);

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
			Collision->SetStringField(
				TEXT("traceFlag"),
				CollisionTraceFlagToString(BodySetup->CollisionTraceFlag));
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
