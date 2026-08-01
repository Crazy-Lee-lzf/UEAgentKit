#include "LiveWriteOperationRegistry.h"
#include "LiveWriteOperationCommon.h"

namespace UEAgentKitLiveWrite
{
	FLiveWriteOperationRegistry::FLiveWriteOperationRegistry()
	{
		RegisterPropertyLiveWriteOperations(*this);
		RegisterMaterialLiveWriteOperations(*this);
		RegisterDataTableLiveWriteOperations(*this);
		SupportedOperationNames.Sort();
	}

	const FLiveWriteOperationRegistry& FLiveWriteOperationRegistry::Get()
	{
		static const FLiveWriteOperationRegistry Registry;
		return Registry;
	}

	void FLiveWriteOperationRegistry::Register(const FLiveWriteOperationDescriptor& Descriptor)
	{
		checkf(!Descriptor.Name.IsEmpty(), TEXT("Live Write operation names cannot be empty."));
		checkf(Descriptor.Apply != nullptr, TEXT("Live Write operation %s has no apply function."), *Descriptor.Name);
		checkf(!Operations.Contains(Descriptor.Name), TEXT("Duplicate Live Write operation registration: %s"), *Descriptor.Name);
		Operations.Add(Descriptor.Name, Descriptor);
		SupportedOperationNames.Add(Descriptor.Name);
	}

	const FLiveWriteOperationDescriptor* FLiveWriteOperationRegistry::Find(const FString& Operation) const
	{
		return Operations.Find(Operation);
	}

	const TArray<FString>& FLiveWriteOperationRegistry::GetSupportedOperationNames() const
	{
		return SupportedOperationNames;
	}

	FString FLiveWriteOperationRegistry::GetSupportedOperationSummary() const
	{
		return FString::Join(SupportedOperationNames, TEXT(", "));
	}

	bool ValidateLiveWriteOperationRequest(
		const FLiveWriteOperationDescriptor& Descriptor,
		const FLiveWriteOperationRequest& Request,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Request.Target.IsValid() || Request.Target->Values.Num() > 32)
		{
			OutErrorCode = TEXT("live-editor-invalid-parameters");
			OutErrorMessage = TEXT("target must be one JSON object with at most 32 fields.");
			return false;
		}
		for (const FString& Field : Descriptor.RequiredTargetFields)
		{
			FString Value;
			if (!Request.Target->TryGetStringField(Field, Value) || !IsSafeLiveWriteSelector(Value))
			{
				OutErrorCode = TEXT("live-editor-invalid-parameters");
				OutErrorMessage = FString::Printf(TEXT("target.%s must be one exact non-empty string."), *Field);
				return false;
			}
		}
		return true;
	}
}
