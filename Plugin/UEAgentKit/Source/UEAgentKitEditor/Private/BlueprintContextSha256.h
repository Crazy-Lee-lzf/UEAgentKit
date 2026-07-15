#pragma once

#include "CoreMinimal.h"

class FBlueprintContextSha256
{
public:
	static bool HashFile(const FString& Filename, FString& OutHexDigest);
};
