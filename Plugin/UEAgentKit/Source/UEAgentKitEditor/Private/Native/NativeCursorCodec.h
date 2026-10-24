// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

/**
 * R1.4 opaque pagination cursor.
 *
 * Token shape: v1.<base64url(canonical payload)>.<base64url(HMAC-SHA256[0..15])>
 * The HMAC key is per editor session and never leaves the registry.  The payload
 * binds the cursor to the query identity, the ordered action set fingerprint,
 * the registry epoch, the page size and an expiry.
 */
struct FNativeCursorPayload
{
	int32 Version = 1;
	FString Kind;              // catalog | context
	FString QueryHash;         // sha256:16 of the normalised query identity
	FString SetFingerprint;    // sha256:16 of the ordered action-id set
	FString Epoch;             // sha256:16 of session+manifest+set
	int32 Offset = 0;
	int32 Total = 0;
	int32 PageSize = 0;
	int64 IssuedAt = 0;        // unix seconds
	int64 ExpiresAt = 0;       // unix seconds
};

namespace UEAgentKitNativeCursor
{
	constexpr int32 CursorVersion = 1;
	constexpr int32 MaxCursorLength = 4096;
	constexpr int32 MaxPayloadBytes = 2048;
	constexpr int32 MaxPageSize = 256;
	constexpr int32 CursorTtlSeconds = 900;
	constexpr int32 MacBytes = 16;

	bool Encode(
		const FNativeCursorPayload& Payload,
		const TArray<uint8>& Key,
		FString& OutToken,
		FString& OutErrorCode);

	bool Decode(
		const FString& Token,
		const TArray<uint8>& Key,
		int64 NowUnix,
		FNativeCursorPayload& OutPayload,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	FString Base64UrlEncode(const uint8* Data, int64 Length);

	bool Base64UrlDecode(const FString& Text, TArray<uint8>& OutBytes, FString& OutErrorCode);
}
