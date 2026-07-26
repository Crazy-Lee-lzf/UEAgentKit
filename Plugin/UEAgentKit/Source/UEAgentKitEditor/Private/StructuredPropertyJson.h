#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonValue.h"
#include "UObject/UnrealType.h"

namespace UEAgentKit::StructuredPropertyJson
{
	constexpr int32 MaxDepth = 8;
	constexpr int32 MaxContainerEntries = 4096;
	constexpr int32 MaxDiffEntries = 1024;

	enum class EKind : uint8
	{
		Invalid,
		Struct,
		Array,
		Set,
		Map
	};

	EKind GetKind(const FProperty* Property);
	FString KindName(EKind Kind);

	bool BuildSchema(
		const FProperty* Property,
		TSharedPtr<FJsonValue>& OutSchema,
		FString& OutError);

	bool ExportValue(
		const FProperty* Property,
		const void* ValueAddress,
		TSharedPtr<FJsonValue>& OutValue,
		FString& OutError);

	bool ImportValue(
		const FProperty* Property,
		void* ValueAddress,
		const TSharedPtr<FJsonValue>& JsonValue,
		FString& OutError);

	FString CanonicalJson(const TSharedPtr<FJsonValue>& Value);
	bool JsonEqual(const TSharedPtr<FJsonValue>& Left, const TSharedPtr<FJsonValue>& Right);

	void BuildDiff(
		const TSharedPtr<FJsonValue>& Before,
		const TSharedPtr<FJsonValue>& After,
		TArray<TSharedPtr<FJsonValue>>& OutDiff,
		bool& bOutTruncated);
}
