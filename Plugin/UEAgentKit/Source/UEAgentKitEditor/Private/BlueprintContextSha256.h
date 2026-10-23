#pragma once

#include "CoreMinimal.h"

class FBlueprintContextSha256
{
public:
	static bool HashFile(const FString& Filename, FString& OutHexDigest);

	/** Compact in-memory SHA-256 used by Editor automation and contract probes. */
	static bool HashText(const FString& Text, FString& OutHexDigest);

	/** SHA-256 over already UTF-8 encoded bytes (byte-exact canonical hashing). */
	static bool HashUtf8Bytes(const uint8* Data, int64 Length, FString& OutHexDigest);
};
