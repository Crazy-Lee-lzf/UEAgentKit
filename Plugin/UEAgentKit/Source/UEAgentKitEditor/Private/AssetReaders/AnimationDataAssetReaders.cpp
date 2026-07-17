#include "AssetReaders/AssetReaderCommon.h"
#include "AssetReaders/AssetReaderImplementations.h"

namespace AssetReaderRegistryPrivate
{
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
}
