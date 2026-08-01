#include "LiveWriteOperationRegistry.h"
#include "LiveWriteOperationCommon.h"
#include "StructuredPropertyJson.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonValue.h"
#include "Engine/DataTable.h"
#include "UObject/StructOnScope.h"
#include "UObject/UnrealType.h"

using namespace UEAgentKitLiveWrite;

namespace
{
	enum class ELiveDataTableOperationKind : uint8
	{
		Cell,
		RowFields,
		AddRow,
		RemoveRow,
		RenameRow
	};

	FString LiveDataTableOperationKindName(const ELiveDataTableOperationKind Kind)
	{
		switch (Kind)
		{
		case ELiveDataTableOperationKind::Cell:
			return TEXT("cell");
		case ELiveDataTableOperationKind::RowFields:
			return TEXT("row-fields");
		case ELiveDataTableOperationKind::AddRow:
			return TEXT("row-add");
		case ELiveDataTableOperationKind::RemoveRow:
			return TEXT("row-remove");
		default:
			return TEXT("row-rename");
		}
	}

	// Mirrors the offline AssetPatchCommandlet reference-impact gate so the Live
	// Editor Apply refuses to remove or rename a row that Searchable Name referencers
	// still point at.
	void FindLiveDataTableRowReferencers(
		UDataTable* DataTable,
		const FName RowName,
		TArray<FAssetIdentifier>& OutReferencers)
	{
		OutReferencers.Reset();
		if (!DataTable || !DataTable->GetOutermost())
		{
			return;
		}
		IAssetRegistry& AssetRegistry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(
			TEXT("AssetRegistry")).Get();
		AssetRegistry.SearchAllAssets(true);
		AssetRegistry.GetReferencers(
			FAssetIdentifier(DataTable->GetOutermost()->GetFName(), DataTable->GetFName(), RowName),
			OutReferencers,
			UE::AssetRegistry::EDependencyCategory::SearchableName);
		OutReferencers.Sort([](const FAssetIdentifier& Left, const FAssetIdentifier& Right)
		{
			return Left.ToString() < Right.ToString();
		});
	}

	bool IsLiveScalarFieldSupported(FProperty* Field)
	{
		return CastField<FBoolProperty>(Field) != nullptr
			|| CastField<FEnumProperty>(Field) != nullptr
			|| CastField<FNumericProperty>(Field) != nullptr
			|| CastField<FStrProperty>(Field) != nullptr
			|| CastField<FNameProperty>(Field) != nullptr
			|| CastField<FTextProperty>(Field) != nullptr;
	}

	bool FindLiveDataTableField(
		UDataTable* DataTable,
		const FString& FieldName,
		FProperty*& OutField,
		FString& OutError)
	{
		OutField = nullptr;
		UScriptStruct* RowStruct = const_cast<UScriptStruct*>(DataTable->GetRowStruct());
		if (FieldName.IsEmpty() || FieldName.Len() > 256 || FieldName.Contains(TEXT(".")))
		{
			OutError = TEXT("DataTable field names must be non-empty strings without dots.");
			return false;
		}
		FProperty* Field = RowStruct ? FindFProperty<FProperty>(RowStruct, FName(*FieldName)) : nullptr;
		if (!Field)
		{
			OutError = FString::Printf(TEXT("DataTable field was not found: %s"), *FieldName);
			return false;
		}
		if (Field->ArrayDim != 1
			|| Field->HasAnyPropertyFlags(CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient))
		{
			OutError = FString::Printf(TEXT("DataTable field is a fixed array or transient: %s"), *FieldName);
			return false;
		}
		if (!IsLiveScalarFieldSupported(Field))
		{
			OutError = FString::Printf(TEXT("DataTable field is not a supported scalar: %s"), *FieldName);
			return false;
		}
		OutField = Field;
		return true;
	}

	bool ExportLiveDataTableRowAsJson(
		UScriptStruct* RowStruct,
		const uint8* RowData,
		TSharedRef<FJsonObject>& OutRow)
	{
		for (TFieldIterator<FProperty> It(RowStruct); It; ++It)
		{
			FProperty* Field = *It;
			if (Field->ArrayDim != 1
				|| Field->HasAnyPropertyFlags(CPF_Transient | CPF_DuplicateTransient | CPF_NonPIEDuplicateTransient))
			{
				continue;
			}
			const void* FieldAddress = Field->ContainerPtrToValuePtr<void>(RowData);
			TSharedPtr<FJsonValue> FieldValue;
			if (!ReadScalarValue(Field, FieldAddress, FieldValue))
			{
				return false;
			}
			OutRow->SetField(Field->GetName(), FieldValue);
		}
		return true;
	}

	bool ExportLiveFullDataTableAsJson(
		UDataTable* DataTable,
		UScriptStruct* RowStruct,
		TSharedRef<FJsonObject>& OutTable)
	{
		TArray<FName> RowNames = DataTable->GetRowNames();
		RowNames.Sort(FNameLexicalLess());
		for (const FName& RowName : RowNames)
		{
			const uint8* RowData = DataTable->FindRowUnchecked(RowName);
			if (!RowData)
			{
				return false;
			}
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			if (!ExportLiveDataTableRowAsJson(RowStruct, RowData, Row))
			{
				return false;
			}
			OutTable->SetObjectField(RowName.ToString(), Row);
		}
		return true;
	}

	struct FLiveDataTableRowSnapshot
	{
		TArray<FName> RowNames;
		TMap<FName, TUniquePtr<FStructOnScope>> Rows;
	};

	class FLiveWriteDataTableIO final : public UEAgentKitLiveWrite::ILiveWriteValueIO
	{
	public:
		FLiveWriteDataTableIO(
			UDataTable* InDataTable,
			UScriptStruct* InRowStruct,
			ELiveDataTableOperationKind InKind,
			FName InRowName,
			FName InNewRowName)
			: DataTable(InDataTable)
			, RowStruct(InRowStruct)
			, Kind(InKind)
			, RowName(InRowName)
			, NewRowName(InNewRowName)
		{
		}

		bool CaptureSnapshot() override
		{
			Snapshot.RowNames = DataTable->GetRowNames();
			Snapshot.RowNames.Sort(FNameLexicalLess());
			Snapshot.Rows.Reset();
			for (const FName& Name : Snapshot.RowNames)
			{
				const uint8* RowData = DataTable->FindRowUnchecked(Name);
				TUniquePtr<FStructOnScope> Row = MakeUnique<FStructOnScope>(RowStruct);
				if (!RowData || !Row->IsValid())
				{
					Snapshot.Rows.Reset();
					return false;
				}
				RowStruct->CopyScriptStruct(Row->GetStructMemory(), RowData);
				Snapshot.Rows.Add(Name, MoveTemp(Row));
			}
			bSnapshotValid = true;
			return true;
		}

		bool IsSnapshotValid() const override
		{
			return bSnapshotValid && RowStruct != nullptr;
		}

		void RestoreSnapshot() override
		{
			if (!bSnapshotValid)
			{
				return;
			}
			DataTable->EmptyTable();
			for (const FName& Name : Snapshot.RowNames)
			{
				const TUniquePtr<FStructOnScope>* Row = Snapshot.Rows.Find(Name);
				if (!Row || !Row->IsValid())
				{
					continue;
				}
				DataTable->AddRow(Name, Row->Get()->GetStructMemory(), RowStruct);
			}
		}

		void ReleaseSnapshot() override
		{
			bSnapshotValid = false;
			Snapshot.Rows.Reset();
		}

		bool ReadBefore(
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			TSharedRef<FJsonObject> Table = MakeShared<FJsonObject>();
			if (!ExportLiveFullDataTableAsJson(DataTable, RowStruct, Table))
			{
				OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
				OutErrorMessage = TEXT("The DataTable could not be exported before the write.");
				return false;
			}
			FullTableBeforeJson = UEAgentKit::StructuredPropertyJson::CanonicalJson(
				MakeShared<FJsonValueObject>(Table));
			if (Kind == ELiveDataTableOperationKind::AddRow)
			{
				OutValue = MakeShared<FJsonValueNull>();
				return true;
			}
			const uint8* RowData = DataTable->FindRowUnchecked(RowName);
			if (!RowData)
			{
				OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
				OutErrorMessage = TEXT("The DataTable row could not be read before the write.");
				return false;
			}
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			if (!ExportLiveDataTableRowAsJson(RowStruct, RowData, Row))
			{
				OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
				OutErrorMessage = TEXT("The DataTable row could not be exported before the write.");
				return false;
			}
			OutValue = MakeShared<FJsonValueObject>(Row);
			return true;
		}

		bool ApplyValue(
			const TSharedPtr<FJsonValue>& Value,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			DataTable->Modify();
			if (Kind == ELiveDataTableOperationKind::Cell)
			{
				FProperty* Field = FindFProperty<FProperty>(RowStruct, FName(*CellFieldName));
				if (!Field)
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("The DataTable cell field could not be resolved during the write.");
					return false;
				}
				uint8* RowData = DataTable->FindRowUnchecked(RowName);
				if (!RowData)
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("The DataTable row is missing during the write.");
					return false;
				}
				void* FieldAddress = Field->ContainerPtrToValuePtr<void>(RowData);
				FString SetError;
				if (!SetScalarValue(Field, FieldAddress, Value, SetError))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-value-invalid");
					OutErrorMessage = SetError;
					return false;
				}
				return true;
			}
			if (Kind == ELiveDataTableOperationKind::RowFields)
			{
				uint8* RowData = DataTable->FindRowUnchecked(RowName);
				if (!RowData)
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("The DataTable row is missing during the write.");
					return false;
				}
				for (const TPair<FString, FProperty*>& FieldEntry : FieldProperties)
				{
					void* FieldAddress = FieldEntry.Value->ContainerPtrToValuePtr<void>(RowData);
					FString SetError;
					if (!SetScalarValue(FieldEntry.Value, FieldAddress, Value->AsObject()->Values.FindRef(FieldEntry.Key), SetError))
					{
						OutErrorCode = TEXT("live-editor-write-data-table-value-invalid");
						OutErrorMessage = FString::Printf(TEXT("Field %s: %s"), *FieldEntry.Key, *SetError);
						return false;
					}
				}
				return true;
			}
			if (Kind == ELiveDataTableOperationKind::AddRow)
			{
				FStructOnScope NewRow(RowStruct);
				if (!NewRow.IsValid())
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("Could not allocate the new DataTable row.");
					return false;
				}
				for (const TPair<FString, FProperty*>& FieldEntry : FieldProperties)
				{
					void* FieldAddress = FieldEntry.Value->ContainerPtrToValuePtr<void>(NewRow.GetStructMemory());
					FString SetError;
					if (!SetScalarValue(FieldEntry.Value, FieldAddress, Value->AsObject()->Values.FindRef(FieldEntry.Key), SetError))
					{
						OutErrorCode = TEXT("live-editor-write-data-table-value-invalid");
						OutErrorMessage = FString::Printf(TEXT("Field %s: %s"), *FieldEntry.Key, *SetError);
						return false;
					}
				}
				DataTable->AddRow(RowName, NewRow.GetStructMemory(), RowStruct);
				return true;
			}
			if (Kind == ELiveDataTableOperationKind::RemoveRow)
			{
				TMap<FName, uint8*>& MutableRowMap =
					const_cast<TMap<FName, uint8*>&>(DataTable->GetRowMap());
				uint8* RemovedRowData = nullptr;
				if (!MutableRowMap.RemoveAndCopyValue(RowName, RemovedRowData) || !RemovedRowData)
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("Could not detach the DataTable row for removal.");
					return false;
				}
				RowStruct->DestroyStruct(RemovedRowData);
				FMemory::Free(RemovedRowData);
				DataTable->HandleDataTableChanged(NAME_None);
				return true;
			}
			TMap<FName, uint8*>& MutableRowMap =
				const_cast<TMap<FName, uint8*>&>(DataTable->GetRowMap());
			uint8* MovedRowData = nullptr;
			if (!MutableRowMap.RemoveAndCopyValue(RowName, MovedRowData) || !MovedRowData)
			{
				OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
				OutErrorMessage = TEXT("Could not detach the DataTable source row for rename.");
				return false;
			}
			MutableRowMap.Add(NewRowName, MovedRowData);
			DataTable->HandleDataTableChanged(NewRowName);
			return true;
		}

		bool ReadAfter(
			const TSharedPtr<FJsonValue>& Requested,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutErrorCode,
			FString& OutErrorMessage) override
		{
			if (Kind == ELiveDataTableOperationKind::Cell)
			{
				const uint8* RowData = DataTable->FindRowUnchecked(RowName);
				if (!RowData)
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable cell read-back verification failed.");
					return false;
				}
				FProperty* Field = FindFProperty<FProperty>(RowStruct, FName(*CellFieldName));
				TSharedPtr<FJsonValue> AfterValue;
				if (!Field
					|| !ReadScalarValue(Field, Field->ContainerPtrToValuePtr<void>(RowData), AfterValue)
					|| !UEAgentKit::StructuredPropertyJson::JsonEqual(AfterValue, Requested))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable cell read-back verification failed.");
					return false;
				}
				TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
				if (!ExportLiveDataTableRowAsJson(RowStruct, RowData, Row))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable cell read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueObject>(Row);
				return true;
			}
			if (Kind == ELiveDataTableOperationKind::RowFields)
			{
				const uint8* RowData = DataTable->FindRowUnchecked(RowName);
				if (!RowData || !RequestedRowFieldsMatch(Requested, RowData))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable row-fields read-back verification failed.");
					return false;
				}
				TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
				if (!ExportLiveDataTableRowAsJson(RowStruct, RowData, Row))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable row-fields read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueObject>(Row);
				return true;
			}
			if (Kind == ELiveDataTableOperationKind::AddRow)
			{
				const uint8* RowData = DataTable->FindRowUnchecked(RowName);
				if (!RowData || !AppliedRowMatchesRequested(Requested))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable row-add read-back verification failed.");
					return false;
				}
				TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
				if (!ExportLiveDataTableRowAsJson(RowStruct, RowData, Row))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable row-add read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueObject>(Row);
				return true;
			}
			if (Kind == ELiveDataTableOperationKind::RemoveRow)
			{
				if (DataTable->FindRowUnchecked(RowName) != nullptr)
				{
					OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
					OutErrorMessage = TEXT("DataTable row-remove read-back verification failed.");
					return false;
				}
				OutValue = MakeShared<FJsonValueNull>();
				return true;
			}
			if (DataTable->FindRowUnchecked(NewRowName) == nullptr
				|| DataTable->FindRowUnchecked(RowName) != nullptr)
			{
				OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
				OutErrorMessage = TEXT("DataTable row-rename read-back verification failed.");
				return false;
			}
			TSharedRef<FJsonObject> Row = MakeShared<FJsonObject>();
			if (!ExportLiveDataTableRowAsJson(RowStruct, DataTable->FindRowUnchecked(NewRowName), Row))
			{
				OutErrorCode = TEXT("live-editor-write-data-table-apply-failed");
				OutErrorMessage = TEXT("DataTable row-rename read-back verification failed.");
				return false;
			}
			OutValue = MakeShared<FJsonValueObject>(Row);
			return true;
		}

		bool SemanticEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right) override
		{
			TSharedRef<FJsonObject> Table = MakeShared<FJsonObject>();
			if (!ExportLiveFullDataTableAsJson(DataTable, RowStruct, Table))
			{
				return false;
			}
			const FString FullTableAfterJson = UEAgentKit::StructuredPropertyJson::CanonicalJson(
				MakeShared<FJsonValueObject>(Table));
			return FullTableBeforeJson == FullTableAfterJson;
		}

		void NotifyChanged() override
		{
			if (Kind == ELiveDataTableOperationKind::Cell || Kind == ELiveDataTableOperationKind::RowFields)
			{
				DataTable->HandleDataTableChanged(RowName);
			}
		}

		void NotifyRestored() override
		{
			DataTable->HandleDataTableChanged(NAME_None);
		}

		FString CellFieldName;
		TMap<FString, FProperty*> FieldProperties;

	private:
		bool RequestedRowFieldsMatch(const TSharedPtr<FJsonValue>& Requested, const uint8* RowData) const
		{
			if (!Requested.IsValid() || Requested->Type != EJson::Object || RowData == nullptr)
			{
				return false;
			}
			const TSharedPtr<FJsonObject> RequestedObject = Requested->AsObject();
			for (const TPair<FString, FProperty*>& FieldEntry : FieldProperties)
			{
				TSharedPtr<FJsonValue> ActualValue;
				if (!ReadScalarValue(
						FieldEntry.Value,
						FieldEntry.Value->ContainerPtrToValuePtr<void>(RowData),
						ActualValue)
					|| !UEAgentKit::StructuredPropertyJson::JsonEqual(
						ActualValue,
						RequestedObject->Values.FindRef(FieldEntry.Key)))
				{
					return false;
				}
			}
			return true;
		}

		bool AppliedRowMatchesRequested(const TSharedPtr<FJsonValue>& Requested) const
		{
			FStructOnScope Probe(RowStruct);
			if (!Probe.IsValid())
			{
				return false;
			}
			for (const TPair<FString, FProperty*>& FieldEntry : FieldProperties)
			{
				const TSharedPtr<FJsonValue> FieldValue = Requested->AsObject()->Values.FindRef(FieldEntry.Key);
				void* FieldAddress = FieldEntry.Value->ContainerPtrToValuePtr<void>(Probe.GetStructMemory());
				FString SetError;
				if (!SetScalarValue(FieldEntry.Value, FieldAddress, FieldValue, SetError))
				{
					return false;
				}
			}
			const uint8* RowData = DataTable->FindRowUnchecked(RowName);
			return RowData != nullptr
				&& RowStruct->CompareScriptStruct(RowData, Probe.GetStructMemory(), PPF_None);
		}

		UDataTable* DataTable = nullptr;
		UScriptStruct* RowStruct = nullptr;
		ELiveDataTableOperationKind Kind = ELiveDataTableOperationKind::Cell;
		FName RowName;
		FName NewRowName;
		FLiveDataTableRowSnapshot Snapshot;
		FString FullTableBeforeJson;
		bool bSnapshotValid = false;
	};

	bool TryApplyDataTableLive(
		UObject* Asset,
		UPackage* Package,
		const FString& AssetPath,
		const FString& RowNameText,
		const FString& NewRowNameText,
		const FString& FieldName,
		const TSharedPtr<FJsonValue>& Value,
		const FString& SessionId,
		const ELiveDataTableOperationKind Kind,
		const FString& Operation,
		TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		UDataTable* DataTable = Cast<UDataTable>(Asset);
		if (DataTable == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-data-table-required");
			OutErrorMessage = TEXT("DataTable writes require a loaded DataTable asset.");
			return false;
		}
		UScriptStruct* RowStruct = const_cast<UScriptStruct*>(DataTable->GetRowStruct());
		if (RowStruct == nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-data-table-required");
			OutErrorMessage = TEXT("The loaded DataTable has no valid row struct.");
			return false;
		}
		if (RowNameText.IsEmpty() || RowNameText.Len() > 256)
		{
			OutErrorCode = TEXT("live-editor-write-data-table-row-invalid");
			OutErrorMessage = TEXT("rowName must be a non-empty string of at most 256 characters.");
			return false;
		}
		const FName RowName(*RowNameText);
		FName NewRowName = NAME_None;
		if (Kind == ELiveDataTableOperationKind::RenameRow)
		{
			if (NewRowNameText.IsEmpty() || NewRowNameText.Len() > 256
				|| NewRowNameText.Equals(RowNameText, ESearchCase::CaseSensitive))
			{
				OutErrorCode = TEXT("live-editor-write-data-table-row-invalid");
				OutErrorMessage = TEXT("renameDataTableRow requires a distinct non-empty newRowName.");
				return false;
			}
			NewRowName = FName(*NewRowNameText);
		}

		const bool bRowExists = DataTable->FindRowUnchecked(RowName) != nullptr;
		if (Kind == ELiveDataTableOperationKind::AddRow && bRowExists)
		{
			OutErrorCode = TEXT("live-editor-write-data-table-row-exists");
			OutErrorMessage = TEXT("The DataTable row already exists.");
			return false;
		}
		if (Kind != ELiveDataTableOperationKind::AddRow && !bRowExists)
		{
			OutErrorCode = TEXT("live-editor-write-data-table-row-not-found");
			OutErrorMessage = TEXT("The DataTable source row was not found.");
			return false;
		}
		if (Kind == ELiveDataTableOperationKind::RenameRow && DataTable->FindRowUnchecked(NewRowName) != nullptr)
		{
			OutErrorCode = TEXT("live-editor-write-data-table-row-exists");
			OutErrorMessage = TEXT("The DataTable destination row already exists.");
			return false;
		}

		TUniquePtr<UEAgentKitLiveWrite::ILiveWriteValueIO> IO = MakeUnique<FLiveWriteDataTableIO>(DataTable, RowStruct, Kind, RowName, NewRowName);
		FLiveWriteDataTableIO* DataTableIO = static_cast<FLiveWriteDataTableIO*>(IO.Get());
		if (Kind == ELiveDataTableOperationKind::Cell)
		{
			FString FieldError;
			FProperty* Field = nullptr;
			if (!FindLiveDataTableField(DataTable, FieldName, Field, FieldError))
			{
				OutErrorCode = TEXT("live-editor-write-data-table-field-unsupported");
				OutErrorMessage = FieldError;
				return false;
			}
			DataTableIO->CellFieldName = FieldName;
		}
		else if (Kind == ELiveDataTableOperationKind::RowFields || Kind == ELiveDataTableOperationKind::AddRow)
		{
			const TSharedPtr<FJsonObject> ValuesObject = Value.IsValid() ? Value->AsObject() : nullptr;
			if (!ValuesObject.IsValid() || ValuesObject->Values.IsEmpty() || ValuesObject->Values.Num() > 32)
			{
				OutErrorCode = TEXT("live-editor-write-data-table-value-invalid");
				OutErrorMessage = TEXT("Row fields require an object containing 1-32 fields.");
				return false;
			}
			TArray<FString> FieldNames;
			ValuesObject->Values.GetKeys(FieldNames);
			FieldNames.Sort();
			for (const FString& FieldNameEntry : FieldNames)
			{
				FString FieldError;
				FProperty* Field = nullptr;
				if (!FindLiveDataTableField(DataTable, FieldNameEntry, Field, FieldError))
				{
					OutErrorCode = TEXT("live-editor-write-data-table-field-unsupported");
					OutErrorMessage = FieldError;
					return false;
				}
				DataTableIO->FieldProperties.Add(FieldNameEntry, Field);
			}
		}
		else if ((Kind == ELiveDataTableOperationKind::RemoveRow || Kind == ELiveDataTableOperationKind::RenameRow)
			&& (!Value.IsValid() || Value->Type != EJson::Boolean || !Value->AsBool()))
		{
			OutErrorCode = TEXT("live-editor-write-data-table-value-invalid");
			OutErrorMessage = TEXT("Structural DataTable row operations require value=true.");
			return false;
		}

		if (Kind == ELiveDataTableOperationKind::RemoveRow || Kind == ELiveDataTableOperationKind::RenameRow)
		{
			TArray<FAssetIdentifier> RowReferencers;
			FindLiveDataTableRowReferencers(DataTable, RowName, RowReferencers);
			if (!RowReferencers.IsEmpty())
			{
				OutErrorCode = TEXT("live-editor-write-data-table-row-referenced");
				OutErrorMessage = TEXT("The DataTable row is referenced by Searchable Name referencers and cannot be removed or renamed.");
				return false;
			}
		}

		UEAgentKitLiveWrite::FLiveWriteContext Context;
		Context.Asset = Asset;
		Context.Package = Package;
		Context.SessionId = SessionId;
		Context.TransactionTitle = TEXT("UE Agent Kit: Set DataTable Value");
		Context.AssetPath = AssetPath;
		Context.PropertyPath = RowNameText;
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
			FString(TEXT("data-table-")) + LiveDataTableOperationKindName(Kind),
			SessionId,
			Evidence,
			IO);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		UEAgentKitLiveWrite::FillLiveWriteEvidence(
			Result,
			Context,
			Evidence,
			Operation,
			FString(TEXT("data-table-")) + LiveDataTableOperationKindName(Kind),
			RowStruct->GetPathName(),
			true,
			true);
		Result->RemoveField(TEXT("propertyPath"));
		Result->SetStringField(TEXT("rowName"), RowNameText);
		Result->SetStringField(TEXT("dataTableKind"), LiveDataTableOperationKindName(Kind));
		Result->SetStringField(TEXT("rowStructPath"), RowStruct->GetPathName());
		if (Kind == ELiveDataTableOperationKind::Cell)
		{
			Result->SetStringField(TEXT("fieldName"), FieldName);
		}
		else if (Kind == ELiveDataTableOperationKind::RenameRow)
		{
			Result->SetStringField(TEXT("newRowName"), NewRowNameText);
		}
		OutResult = Result;
		return true;
	}

	bool ApplyDataTableOperation(
		const FLiveWriteOperationContext& Context,
		const FLiveWriteOperationRequest& Request,
		const ELiveDataTableOperationKind Kind,
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		return TryApplyDataTableLive(
			Context.Asset, Context.Package, Request.AssetPath, Request.RowName,
			Request.NewRowName, Request.FieldName, Request.Value, Request.SessionId,
			Kind, Request.Operation, OutRecord, OutResult, OutErrorCode, OutErrorMessage);
	}

#define UEAK_DEFINE_DATATABLE_APPLY(Name, KindValue) \
	bool Name( \
		const FLiveWriteOperationContext& Context, \
		const FLiveWriteOperationRequest& Request, \
		TSharedPtr<FLiveWriteTransactionRecord>& OutRecord, \
		TSharedPtr<FJsonObject>& OutResult, \
		FString& OutErrorCode, \
		FString& OutErrorMessage) \
	{ \
		return ApplyDataTableOperation(Context, Request, KindValue, OutRecord, OutResult, OutErrorCode, OutErrorMessage); \
	}

	UEAK_DEFINE_DATATABLE_APPLY(ApplyDataTableCellOperation, ELiveDataTableOperationKind::Cell)
	UEAK_DEFINE_DATATABLE_APPLY(ApplyDataTableRowFieldsOperation, ELiveDataTableOperationKind::RowFields)
	UEAK_DEFINE_DATATABLE_APPLY(ApplyDataTableAddRowOperation, ELiveDataTableOperationKind::AddRow)
	UEAK_DEFINE_DATATABLE_APPLY(ApplyDataTableRemoveRowOperation, ELiveDataTableOperationKind::RemoveRow)
	UEAK_DEFINE_DATATABLE_APPLY(ApplyDataTableRenameRowOperation, ELiveDataTableOperationKind::RenameRow)

#undef UEAK_DEFINE_DATATABLE_APPLY
}

namespace UEAgentKitLiveWrite
{
	void RegisterDataTableLiveWriteOperations(FLiveWriteOperationRegistry& Registry)
	{
		Registry.Register({TEXT("setDataTableCell"), ELiveWriteTargetKind::DataTableRow,
			{TEXT("rowName"), TEXT("fieldName")}, StandardAssetRequirements, &ApplyDataTableCellOperation});
		Registry.Register({TEXT("setDataTableRowFields"), ELiveWriteTargetKind::DataTableRow,
			{TEXT("rowName")}, StandardAssetRequirements, &ApplyDataTableRowFieldsOperation});
		Registry.Register({TEXT("addDataTableRow"), ELiveWriteTargetKind::DataTableRow,
			{TEXT("rowName")}, StandardAssetRequirements, &ApplyDataTableAddRowOperation});
		Registry.Register({TEXT("removeDataTableRow"), ELiveWriteTargetKind::DataTableRow,
			{TEXT("rowName")}, StandardAssetRequirements, &ApplyDataTableRemoveRowOperation});
		Registry.Register({TEXT("renameDataTableRow"), ELiveWriteTargetKind::DataTableRow,
			{TEXT("rowName"), TEXT("newRowName")}, StandardAssetRequirements, &ApplyDataTableRenameRowOperation});
	}
}
