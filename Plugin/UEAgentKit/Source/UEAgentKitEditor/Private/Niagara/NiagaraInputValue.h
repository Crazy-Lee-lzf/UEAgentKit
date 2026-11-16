#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "NiagaraTypes.h"

/**
 * Shared JSON <-> Niagara input payload conversion.
 *
 * Both the N1 reader and the N2 writer use this so a value that is written by
 * SetLocalValue is exactly the value that is read back, and so there is only one
 * place that decides which input types are expressible.
 *
 * Supported shapes: float, int32 (including enums, which FNiagaraTypeDefinition
 * maps onto the int struct), bool (FNiagaraBool), vec2, vec3/position, vec4/quat
 * and color (FLinearColor).
 */
namespace UEAgentKitNiagaraValue
{
	enum class EShape : uint8
	{
		Unsupported,
		Float,
		Int32,
		Bool,
		Vec2,
		Vec3,
		Vec4,
		Color,
	};

	struct FShape
	{
		EShape Kind = EShape::Unsupported;
		int32 ComponentCount = 0;
		/** Byte size of the payload for this shape; 0 when unsupported. */
		int32 PayloadSize = 0;
		/** LWC vectors/colors can be double based; the payload decides, not the type name. */
		bool bDoubleComponents = false;
	};

	/** Largest magnitude accepted for a non-color component. */
	inline constexpr double MaxComponentMagnitude = 1.0e6;

	FShape Classify(const FNiagaraTypeDefinition& Type);

	/** True when a Local input of this type can be written by the N2 tool. */
	bool IsWritable(const FNiagaraTypeDefinition& Type);

	/**
	 * Project a raw payload into JSON. Returns false and fills OutReason when the
	 * shape is unsupported or the payload size does not match the shape.
	 */
	bool ToJson(
		const FNiagaraTypeDefinition& Type,
		const uint8* Data,
		int32 Size,
		TSharedPtr<FJsonValue>& OutValue,
		FString& OutReason);

	/**
	 * Parse a JSON value into the exact raw bytes SetLocalValue expects.
	 * Missing vector/color components are rejected rather than guessed; the alpha
	 * channel is the only optional component (defaults to 1.0).
	 */
	bool FromJson(
		const FNiagaraTypeDefinition& Type,
		const TSharedPtr<FJsonValue>& Value,
		TArray<uint8>& OutBytes,
		FString& OutError);

	/** Byte equality for one typed payload. */
	bool EqualBytes(const FNiagaraTypeDefinition& Type, const uint8* A, const uint8* B);

	const TCHAR* ShapeName(EShape Shape);
}
