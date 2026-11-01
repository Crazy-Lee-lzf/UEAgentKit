#include "EditorBridgeRetainedLiveWrite.h"

#include "CoreMinimal.h"
#include "UObject/Object.h"

namespace UEAgentKitEditorBridgePrivate
{
	namespace
	{
		FString DescribeTargetKind(const FString& Operation)
		{
			const UEAgentKitLiveWrite::FLiveWriteOperationDescriptor* Descriptor =
				UEAgentKitLiveWrite::FLiveWriteOperationRegistry::Get().Find(Operation);
			if (Descriptor == nullptr)
			{
				return FString(TEXT("retained-live-write"));
			}
			switch (Descriptor->TargetKind)
			{
			case UEAgentKitLiveWrite::ELiveWriteTargetKind::Property:
				return FString(TEXT("property"));
			case UEAgentKitLiveWrite::ELiveWriteTargetKind::MaterialParameter:
				return FString(TEXT("materialParameter"));
			case UEAgentKitLiveWrite::ELiveWriteTargetKind::DataTableRow:
				return FString(TEXT("dataTableRow"));
			}
			return FString(TEXT("retained-live-write"));
		}

		bool Fail(
			FString& OutErrorCode,
			FString& OutErrorMessage,
			const TCHAR* Code,
			const FString& Message)
		{
			OutErrorCode = Code;
			OutErrorMessage = Message;
			return false;
		}
	}

	bool ReadRetainedLiveWriteFastValue(
		const FString& SessionId,
		const FString& Operation,
		const FString& AssetPath,
		const FString& TransactionId,
		UObject* Asset,
		const TMap<FString, TMap<FGuid, TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>>>& Records,
		const TSharedRef<FJsonObject>& OutResult,
		TSharedPtr<FJsonValue>& OutValue,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutValue.Reset();
		OutErrorCode.Reset();
		OutErrorMessage.Reset();

		FGuid RequestedTransactionId;
		if (!FGuid::Parse(TransactionId, RequestedTransactionId) || !RequestedTransactionId.IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("live-fast-verify-target-invalid"),
				TEXT("Fast Resident Verify requires one canonical transactionId."));
		}
		const TMap<FGuid, TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>>* AssetRecords =
			Records.Find(AssetPath);
		const TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>* Found =
			AssetRecords != nullptr ? AssetRecords->Find(RequestedTransactionId) : nullptr;
		if (Found == nullptr || !Found->IsValid())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("live-fast-verify-transaction-not-found"),
				TEXT("No retained live-write transaction matches this exact asset and transactionId."));
		}
		const TSharedPtr<UEAgentKitLiveWrite::FLiveWriteTransactionRecord>& Record = *Found;
		if (Record->SessionId != SessionId
			|| Record->Operation != Operation
			|| Asset == nullptr
			|| !Record->Asset.IsValid()
			|| Record->Asset.Get() != Asset
			|| Record->ClassPath != Asset->GetClass()->GetPathName())
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("live-fast-verify-target-changed"),
				TEXT("The retained live-write transaction does not match this Editor session, operation or asset."));
		}
		if (Record->IO == nullptr)
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("live-fast-verify-target-not-found"),
				TEXT("The retained live-write transaction has no target IO to read."));
		}
		FString RefreshError;
		if (!Record->IO->RefreshTarget(RefreshError))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				TEXT("live-fast-verify-target-not-found"),
				TEXT("The retained live-write target could not be re-resolved: ") + RefreshError);
		}
		FString ReadErrorCode;
		FString ReadErrorMessage;
		if (!Record->IO->ReadWrittenValueForRevert(OutValue, ReadErrorCode, ReadErrorMessage))
		{
			return Fail(
				OutErrorCode,
				OutErrorMessage,
				ReadErrorCode.IsEmpty() ? TEXT("live-fast-verify-read-failed") : *ReadErrorCode,
				ReadErrorMessage.IsEmpty()
					? FString(TEXT("The retained live-write target could not be read back."))
					: ReadErrorMessage);
		}
		const FString TargetKind = DescribeTargetKind(Operation);
		OutResult->SetStringField(TEXT("targetKind"), TargetKind);
		OutResult->SetBoolField(TEXT("targetResolved"), true);
		OutResult->SetStringField(TEXT("transactionId"), TransactionId);
		return true;
	}
}
