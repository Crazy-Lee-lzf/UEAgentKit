#pragma once

#include "CoreMinimal.h"

/**
 * M2.2 opt-in descriptor for the external Python layout broker.
 *
 * The descriptor is read only when a caller explicitly starts a native preview.
 * Nothing here starts a listener, spawns a process, or connects at module load.
 * A descriptor that cannot prove a current-user-only DACL is rejected.
 */
struct FUEAgentKitLayoutBrokerDescriptor
{
	FString Address;
	int32 Port = 0;
	FString Token;
	FString ProjectName;
	FString ProjectPathHash;
	FString BrokerVersion;
	TArray<FString> SupportedContractVersions;
	FString AlgorithmId;
	FString AlgorithmVersion;
	FDateTime StartedAtUtc;
	int32 ProcessId = 0;
	int32 MaxConcurrentJobs = 0;

	/** Default production path: <Project>/Saved/UEAgentKit/LayoutBroker.json. */
	static FString DefaultPath();

	/** Strict parse, identity, freshness and local DACL validation. */
	static bool Load(
		FUEAgentKitLayoutBrokerDescriptor& OutDescriptor,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	static bool LoadFromPath(
		const FString& Path,
		FUEAgentKitLayoutBrokerDescriptor& OutDescriptor,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/** Same normalization/SHA-1 identity as the existing Editor Bridge. */
	static FString ComputeProjectPathHash();

private:
	static bool ValidateFileShape(const FString& Path, FString& OutReason);
	static bool ValidateCurrentUserDacl(const FString& Path, FString& OutReason);
	static bool IsHexToken(const FString& Value);
};
