#include "Niagara/NiagaraInputValue.h"

#include "NiagaraTypes.h"

namespace UEAgentKitNiagaraValue
{
	namespace
	{
		bool MatchesStruct(const UStruct* Struct, const UScriptStruct* Known)
		{
			return Struct != nullptr && Known != nullptr && Struct == Known;
		}

		const TCHAR* const Vec2ComponentNames[] = { TEXT("x"), TEXT("y") };
		const TCHAR* const Vec3ComponentNames[] = { TEXT("x"), TEXT("y"), TEXT("z") };
		const TCHAR* const Vec4ComponentNames[] = { TEXT("x"), TEXT("y"), TEXT("z"), TEXT("w") };
		const TCHAR* const ColorComponentNames[] = { TEXT("r"), TEXT("g"), TEXT("b"), TEXT("a") };

		const TCHAR* const* ComponentNamesFor(const EShape Shape)
		{
			switch (Shape)
			{
			case EShape::Vec2: return Vec2ComponentNames;
			case EShape::Vec3: return Vec3ComponentNames;
			case EShape::Vec4: return Vec4ComponentNames;
			case EShape::Color: return ColorComponentNames;
			default: return nullptr;
			}
		}

		int32 ComponentCountFor(const EShape Shape)
		{
			switch (Shape)
			{
			case EShape::Vec2: return 2;
			case EShape::Vec3: return 3;
			case EShape::Vec4: return 4;
			case EShape::Color: return 4;
			default: return 0;
			}
		}

		/** Read ComponentCount numbers out of a payload whose width is decided by its size. */
		bool ReadComponents(
			const uint8* Data,
			const int32 Size,
			const int32 Count,
			const bool bDouble,
			TArray<double>& Out)
		{
			Out.Reset();
			if (Data == nullptr || Count <= 0)
			{
				return false;
			}

			if (bDouble)
			{
				if (Size != Count * static_cast<int32>(sizeof(double)))
				{
					return false;
				}
				Out.Reserve(Count);
				for (int32 Index = 0; Index < Count; ++Index)
				{
					double Value = 0.0;
					FMemory::Memcpy(&Value, Data + Index * static_cast<int32>(sizeof(double)), sizeof(double));
					Out.Add(Value);
				}
				return true;
			}

			if (Size != Count * static_cast<int32>(sizeof(float)))
			{
				return false;
			}
			Out.Reserve(Count);
			for (int32 Index = 0; Index < Count; ++Index)
			{
				float Value = 0.0f;
				FMemory::Memcpy(&Value, Data + Index * static_cast<int32>(sizeof(float)), sizeof(float));
				Out.Add(static_cast<double>(Value));
			}
			return true;
		}

		void WriteComponents(uint8* Data, const int32 Count, const bool bDouble, const TArray<double>& Components)
		{
			for (int32 Index = 0; Index < Count; ++Index)
			{
				if (bDouble)
				{
					const double Value = Components[Index];
					FMemory::Memcpy(Data + Index * static_cast<int32>(sizeof(double)), &Value, sizeof(double));
				}
				else
				{
					const float Value = static_cast<float>(Components[Index]);
					FMemory::Memcpy(Data + Index * static_cast<int32>(sizeof(float)), &Value, sizeof(float));
				}
			}
		}

		bool ReadFiniteNumber(const TSharedPtr<FJsonValue>& Value, const TCHAR* FieldName, double& OutValue, FString& OutError)
		{
			if (!Value.IsValid())
			{
				OutError = FString::Printf(TEXT("%s must be a finite number."), FieldName);
				return false;
			}

			double Number = 0.0;
			if (!Value->TryGetNumber(Number))
			{
				OutError = FString::Printf(TEXT("%s must be a finite number."), FieldName);
				return false;
			}
			if (!FMath::IsFinite(Number))
			{
				OutError = FString::Printf(TEXT("%s must be a finite number."), FieldName);
				return false;
			}
			OutValue = Number;
			return true;
		}

		/** Object {x,y,z...} or array form of a vector/color value. */
		bool ReadComponentArray(
			const TSharedPtr<FJsonValue>& Value,
			const EShape Shape,
			TArray<double>& OutComponents,
			FString& OutError)
		{
			const TCHAR* const* Names = ComponentNamesFor(Shape);
			const int32 Count = ComponentCountFor(Shape);
			OutComponents.Reset();
			OutComponents.SetNumZeroed(Count);

			if (!Value.IsValid())
			{
				OutError = FString::Printf(TEXT("%s value is missing."), ShapeName(Shape));
				return false;
			}

			const TArray<TSharedPtr<FJsonValue>>* ArrayField = nullptr;
			const TSharedPtr<FJsonObject>* ObjectField = nullptr;

			if (Value->TryGetArray(ArrayField) && ArrayField != nullptr)
			{
				const bool bColorAllowsAlpha = Shape == EShape::Color;
				const int32 MinCount = bColorAllowsAlpha ? 3 : Count;
				if (ArrayField->Num() != Count && !(bColorAllowsAlpha && ArrayField->Num() == MinCount))
				{
					OutError = FString::Printf(
						TEXT("%s must have exactly %d components."), ShapeName(Shape), Count);
					return false;
				}
				for (int32 Index = 0; Index < ArrayField->Num(); ++Index)
				{
					if (!ReadFiniteNumber((*ArrayField)[Index], Names[Index], OutComponents[Index], OutError))
					{
						return false;
					}
				}
				if (ArrayField->Num() < Count)
				{
					OutComponents[Count - 1] = 1.0; // default alpha
				}
				return true;
			}

			if (Value->TryGetObject(ObjectField) && ObjectField != nullptr && ObjectField->IsValid())
			{
				const TSharedPtr<FJsonObject>& Object = *ObjectField;
				const bool bColorAllowsAlpha = Shape == EShape::Color;
				const int32 Required = bColorAllowsAlpha ? 3 : Count;

				for (int32 Index = 0; Index < Required; ++Index)
				{
					const TSharedPtr<FJsonValue> Field = Object->TryGetField(Names[Index]);
					if (!Field.IsValid())
					{
						OutError = FString::Printf(
							TEXT("%s must contain the '%s' component."), ShapeName(Shape), Names[Index]);
						return false;
					}
					if (!ReadFiniteNumber(Field, Names[Index], OutComponents[Index], OutError))
					{
						return false;
					}
				}

				if (bColorAllowsAlpha && Count > Required)
				{
					const TSharedPtr<FJsonValue> AlphaField = Object->TryGetField(Names[Count - 1]);
					OutComponents[Count - 1] = 1.0;
					if (AlphaField.IsValid()
						&& !ReadFiniteNumber(AlphaField, Names[Count - 1], OutComponents[Count - 1], OutError))
					{
						return false;
					}
				}
				return true;
			}

			OutError = FString::Printf(
				TEXT("%s must be an object with its named components, or an array of %d numbers."),
				ShapeName(Shape),
				Count);
			return false;
		}
	}

	FShape Classify(const FNiagaraTypeDefinition& Type)
	{
		FShape Info;
		const UStruct* Struct = Type.GetStruct();
		const int32 Size = Type.GetSize();

		if (MatchesStruct(Struct, FNiagaraTypeDefinition::GetFloatStruct()) && Size == static_cast<int32>(sizeof(float)))
		{
			Info.Kind = EShape::Float;
			Info.ComponentCount = 1;
			Info.PayloadSize = static_cast<int32>(sizeof(float));
			return Info;
		}

		if (MatchesStruct(Struct, FNiagaraTypeDefinition::GetIntStruct()) && Size == static_cast<int32>(sizeof(int32)))
		{
			Info.Kind = EShape::Int32;
			Info.ComponentCount = 1;
			Info.PayloadSize = static_cast<int32>(sizeof(int32));
			return Info;
		}

		if (MatchesStruct(Struct, FNiagaraTypeDefinition::GetBoolStruct()) && Size == static_cast<int32>(sizeof(int32)))
		{
			Info.Kind = EShape::Bool;
			Info.ComponentCount = 1;
			Info.PayloadSize = static_cast<int32>(sizeof(int32));
			return Info;
		}

		struct FShapeCandidate
		{
			bool bMatches = false;
			EShape Kind = EShape::Unsupported;
			int32 Count = 0;
		};

		FShapeCandidate Candidate;
		if (MatchesStruct(Struct, FNiagaraTypeDefinition::GetVec2Struct()))
		{
			Candidate = { true, EShape::Vec2, 2 };
		}
		else if (MatchesStruct(Struct, FNiagaraTypeDefinition::GetVec3Struct())
			|| MatchesStruct(Struct, FNiagaraTypeDefinition::GetPositionStruct()))
		{
			Candidate = { true, EShape::Vec3, 3 };
		}
		else if (MatchesStruct(Struct, FNiagaraTypeDefinition::GetVec4Struct())
			|| MatchesStruct(Struct, FNiagaraTypeDefinition::GetQuatStruct()))
		{
			Candidate = { true, EShape::Vec4, 4 };
		}
		else if (MatchesStruct(Struct, FNiagaraTypeDefinition::GetColorStruct()))
		{
			Candidate = { true, EShape::Color, 4 };
		}

		if (!Candidate.bMatches)
		{
			return Info;
		}

		const bool bDouble = Size == Candidate.Count * static_cast<int32>(sizeof(double));
		const bool bSingle = Size == Candidate.Count * static_cast<int32>(sizeof(float));
		if (!bDouble && !bSingle)
		{
			return Info;
		}

		Info.Kind = Candidate.Kind;
		Info.ComponentCount = Candidate.Count;
		Info.PayloadSize = Size;
		Info.bDoubleComponents = bDouble;
		return Info;
	}

	bool IsWritable(const FNiagaraTypeDefinition& Type)
	{
		return Classify(Type).Kind != EShape::Unsupported;
	}

	const TCHAR* ShapeName(const EShape Shape)
	{
		switch (Shape)
		{
		case EShape::Float: return TEXT("float");
		case EShape::Int32: return TEXT("int32");
		case EShape::Bool: return TEXT("bool");
		case EShape::Vec2: return TEXT("vec2");
		case EShape::Vec3: return TEXT("vec3");
		case EShape::Vec4: return TEXT("vec4");
		case EShape::Color: return TEXT("color");
		case EShape::Unsupported: break;
		}
		return TEXT("unsupported");
	}

	bool ToJson(
		const FNiagaraTypeDefinition& Type,
		const uint8* Data,
		const int32 Size,
		TSharedPtr<FJsonValue>& OutValue,
		FString& OutReason)
	{
		OutValue.Reset();
		OutReason.Reset();

		const FShape Info = Classify(Type);
		if (Info.Kind == EShape::Unsupported)
		{
			OutReason = FString::Printf(TEXT("Type '%s' is not representable."), *Type.GetName());
			return false;
		}
		if (Data == nullptr || Size != Info.PayloadSize)
		{
			OutReason = FString::Printf(TEXT("Payload size %d does not match type '%s'."), Size, *Type.GetName());
			return false;
		}

		if (Info.Kind == EShape::Bool)
		{
			int32 Raw = 0;
			FMemory::Memcpy(&Raw, Data, sizeof(int32));
			OutValue = MakeShared<FJsonValueBoolean>(Raw != 0);
			return true;
		}

		if (Info.Kind == EShape::Int32)
		{
			int32 Raw = 0;
			FMemory::Memcpy(&Raw, Data, sizeof(int32));
			OutValue = MakeShared<FJsonValueNumber>(static_cast<double>(Raw));
			return true;
		}

		TArray<double> Components;
		if (!ReadComponents(Data, Size, Info.ComponentCount, Info.bDoubleComponents, Components))
		{
			OutReason = FString::Printf(TEXT("Payload for '%s' is malformed."), *Type.GetName());
			return false;
		}

		if (Info.Kind == EShape::Float)
		{
			OutValue = MakeShared<FJsonValueNumber>(Components[0]);
			return true;
		}

		const TCHAR* const* Names = ComponentNamesFor(Info.Kind);
		const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
		for (int32 Index = 0; Index < Info.ComponentCount; ++Index)
		{
			Object->SetNumberField(Names[Index], Components[Index]);
		}
		OutValue = MakeShared<FJsonValueObject>(Object);
		return true;
	}

	bool FromJson(
		const FNiagaraTypeDefinition& Type,
		const TSharedPtr<FJsonValue>& Value,
		TArray<uint8>& OutBytes,
		FString& OutError)
	{
		OutBytes.Reset();
		OutError.Reset();

		const FShape Info = Classify(Type);
		if (Info.Kind == EShape::Unsupported)
		{
			OutError = FString::Printf(
				TEXT("Type '%s' is not writable; supported types are float, int32, bool, vec2, vec3, vec4 and color."),
				*Type.GetName());
			return false;
		}
		if (!Value.IsValid())
		{
			OutError = TEXT("value is required.");
			return false;
		}

		OutBytes.SetNumZeroed(Info.PayloadSize);

		if (Info.Kind == EShape::Bool)
		{
			bool bValue = false;
			if (!Value->TryGetBool(bValue))
			{
				OutError = TEXT("value must be a boolean for a bool input.");
				return false;
			}
			// FNiagaraBool uses -1 for true.
			const int32 Raw = bValue ? -1 : 0;
			FMemory::Memcpy(OutBytes.GetData(), &Raw, sizeof(int32));
			return true;
		}

		if (Info.Kind == EShape::Int32)
		{
			double Number = 0.0;
			if (!ReadFiniteNumber(Value, TEXT("value"), Number, OutError))
			{
				return false;
			}
			if (!FMath::IsNearlyEqual(Number, FMath::RoundToDouble(Number), 0.0))
			{
				OutError = TEXT("value must be an integer for an int32 input.");
				return false;
			}
			if (Number < static_cast<double>(MIN_int32) || Number > static_cast<double>(MAX_int32))
			{
				OutError = TEXT("value is outside the int32 range.");
				return false;
			}
			const int32 Raw = static_cast<int32>(FMath::RoundToDouble(Number));
			FMemory::Memcpy(OutBytes.GetData(), &Raw, sizeof(int32));
			return true;
		}

		if (Info.Kind == EShape::Float)
		{
			double Number = 0.0;
			if (!ReadFiniteNumber(Value, TEXT("value"), Number, OutError))
			{
				return false;
			}
			if (FMath::Abs(Number) > MaxComponentMagnitude)
			{
				OutError = FString::Printf(
					TEXT("value must be within +/-%g."), MaxComponentMagnitude);
				return false;
			}
			TArray<double> Components;
			Components.Add(Number);
			WriteComponents(OutBytes.GetData(), 1, Info.bDoubleComponents, Components);
			return true;
		}

		TArray<double> Components;
		if (!ReadComponentArray(Value, Info.Kind, Components, OutError))
		{
			return false;
		}
		for (const double Component : Components)
		{
			if (Info.Kind == EShape::Color)
			{
				if (Component < 0.0 || Component > 1.0)
				{
					OutError = TEXT("color components must be within [0, 1].");
					return false;
				}
			}
			else if (FMath::Abs(Component) > MaxComponentMagnitude)
			{
				OutError = FString::Printf(TEXT("every component must be within +/-%g."), MaxComponentMagnitude);
				return false;
			}
		}
		WriteComponents(OutBytes.GetData(), Info.ComponentCount, Info.bDoubleComponents, Components);
		return true;
	}

	bool EqualBytes(const FNiagaraTypeDefinition& Type, const uint8* A, const uint8* B)
	{
		const FShape Info = Classify(Type);
		if (Info.Kind == EShape::Unsupported || Info.PayloadSize <= 0 || A == nullptr || B == nullptr)
		{
			return false;
		}
		return FMemory::Memcmp(A, B, Info.PayloadSize) == 0;
	}
}
