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

	/** HMAC-SHA256 over raw bytes (RFC 2104); used by R1.4 opaque cursors. */
	static bool HmacSha256(
		const uint8* Key,
		int64 KeyLength,
		const uint8* Data,
		int64 DataLength,
		uint8 OutDigest[32]);
};
