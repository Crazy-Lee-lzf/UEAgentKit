#include "AssetReaders/AssetReaderCommon.h"

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

	TArray<TSharedPtr<FJsonValue>> NamesToJson(TArray<FName> Values)
	{
		Values.Sort(FNameLexicalLess());
		TArray<TSharedPtr<FJsonValue>> Result;
		Result.Reserve(Values.Num());
		for (const FName Value : Values)
		{
			Result.Add(MakeShared<FJsonValueString>(Value.ToString()));
		}
		return Result;
	}

	TArray<TSharedPtr<FJsonValue>> StringCountsToJson(const TMap<FString, int32>& Counts)
	{
		TArray<FString> Keys;
		Counts.GetKeys(Keys);
		Keys.Sort();
		TArray<TSharedPtr<FJsonValue>> Result;
		Result.Reserve(Keys.Num());
		for (const FString& Key : Keys)
		{
			TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
			Entry->SetStringField(TEXT("classPath"), Key);
			Entry->SetNumberField(TEXT("count"), Counts.FindRef(Key));
			Result.Add(MakeShared<FJsonValueObject>(Entry));
		}
		return Result;
	}

	bool GetReflectedBool(const UObject* Object, const FName PropertyName, const bool DefaultValue)
	{
		if (Object == nullptr)
		{
			return DefaultValue;
		}
		const FBoolProperty* Property = FindFProperty<FBoolProperty>(Object->GetClass(), PropertyName);
		return Property != nullptr ? Property->GetPropertyValue_InContainer(Object) : DefaultValue;
	}

	UObject* GetReflectedObject(const UObject* Object, const FName PropertyName)
	{
		if (Object == nullptr)
		{
			return nullptr;
		}
		const FObjectPropertyBase* Property = FindFProperty<FObjectPropertyBase>(Object->GetClass(), PropertyName);
		return Property != nullptr ? Property->GetObjectPropertyValue_InContainer(Object) : nullptr;
	}

	int32 GetReflectedInt(const UObject* Object, const FName PropertyName, const int32 DefaultValue)
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
}
