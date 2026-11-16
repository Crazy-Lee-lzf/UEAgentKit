#include "PieObjectProperties.h"

#include "EditorBridge.h"

#include "Components/ActorComponent.h"
#include "Dom/JsonValue.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Misc/DateTime.h"
#include "UObject/EnumProperty.h"
#include "UObject/FieldPathProperty.h"
#include "UObject/TextProperty.h"
#include "UObject/UnrealType.h"

namespace UEAgentKitPieRuntime
{
	namespace
	{
		/** JSON numbers are doubles; keep int64 exact above 2^53 by stringifying. */
		constexpr int64 MaxExactValue = 9007199254740991LL;
		constexpr int32 PerValueCharOverhead = 24;

		FString TruncateDeclaredType(const FString& TypeName)
		{
			return TypeName.Len() > MaxDeclaredTypeChars ? TypeName.Left(MaxDeclaredTypeChars) : TypeName;
		}

		/**
		 * Declared C++ type including container and pointer decoration.  A bare
		 * Property->GetCPPType() reports TArray for a container and hides the
		 * element type in the extended text, which is not useful to a caller.
		 */
		FString DeclaredTypeOf(const FProperty* Property)
		{
			if (Property == nullptr)
			{
				return FString();
			}
			FString ExtendedTypeText;
			const FString BaseTypeText = Property->GetCPPType(&ExtendedTypeText);
			return TruncateDeclaredType(BaseTypeText + ExtendedTypeText);
		}

		FString TruncateChars(
			const FString& Value,
			const int32 MaxChars,
			bool& bOutTruncated,
			int32& OutOriginalLength)
		{
			OutOriginalLength = Value.Len();
			if (OutOriginalLength <= MaxChars)
			{
				bOutTruncated = false;
				return Value;
			}
			bOutTruncated = true;
			int32 Cut = FMath::Max(0, MaxChars);
			if (Cut > 0)
			{
				const TCHAR Last = Value[Cut - 1];
				if (Last >= 0xD800 && Last <= 0xDBFF)
				{
					--Cut;
				}
			}
			return Value.Left(Cut);
		}

		FString EnumShortEntryName(const UEnum* Enum, const int64 Value)
		{
			if (Enum == nullptr)
			{
				return FString();
			}
			const FString FullName = Enum->GetNameStringByValue(Value);
			int32 SeparatorIndex = INDEX_NONE;
			if (FullName.FindLastChar(TEXT(':'), SeparatorIndex))
			{
				return FullName.Mid(SeparatorIndex + 1);
			}
			return FullName;
		}

		bool IsObjectRefKind(const EPropertyKind Kind)
		{
			return Kind == EPropertyKind::Object
				|| Kind == EPropertyKind::Class
				|| Kind == EPropertyKind::SoftObject
				|| Kind == EPropertyKind::SoftClass
				|| Kind == EPropertyKind::WeakObject;
		}

		bool IsContainerKind(const EPropertyKind Kind)
		{
			return Kind == EPropertyKind::Array
				|| Kind == EPropertyKind::Set
				|| Kind == EPropertyKind::Map;
		}

		bool IsScalarKind(const EPropertyKind Kind)
		{
			switch (Kind)
			{
			case EPropertyKind::Bool:
			case EPropertyKind::Int8:
			case EPropertyKind::Int16:
			case EPropertyKind::Int32:
			case EPropertyKind::Int64:
			case EPropertyKind::UInt8:
			case EPropertyKind::UInt16:
			case EPropertyKind::UInt32:
			case EPropertyKind::UInt64:
			case EPropertyKind::Float:
			case EPropertyKind::Double:
			case EPropertyKind::Name:
			case EPropertyKind::String:
			case EPropertyKind::Enum:
				return true;
			default:
				return false;
			}
		}

		/** One bounded response budget shared by every value in one response. */
		struct FWriteContext
		{
			int32 MaxStringChars = DefaultPropertyStringChars;
			int32 Offset = 0;
			int32 Limit = DefaultContainerPageLimit;
			const TArray<FWorldContextSnapshot>* AllContexts = nullptr;
			int32 RemainingItems = MaxReturnedContainerItems;
			int32 RemainingChars = MaxReturnedPropertyChars;

			bool TryReserveItem()
			{
				if (RemainingItems <= 0)
				{
					return false;
				}
				--RemainingItems;
				return true;
			}

			bool TryReserveChars(const int32 Count)
			{
				if (RemainingChars <= 0)
				{
					return false;
				}
				RemainingChars -= FMath::Max(0, Count);
				return true;
			}
		};

		FPropertySupport EvaluateSupportInternal(const FProperty* Property, const int32 Depth);

		/** The owning class path, truncated like every other reported path. */
		void AddDeclaringClass(const FProperty* Property, const TSharedRef<FJsonObject>& Item)
		{
			const UClass* DeclaringClass = Property != nullptr ? Property->GetOwnerClass() : nullptr;
			if (DeclaringClass == nullptr)
			{
				return;
			}
			bool bTruncated = false;
			int32 OriginalLength = 0;
			Item->SetStringField(
				TEXT("declaringClassPath"),
				TruncateChars(
					DeclaringClass->GetPathName(),
					MaxScalarClassPathChars,
					bTruncated,
					OriginalLength));
			Item->SetBoolField(TEXT("declaringClassPathTruncated"), bTruncated);
		}

		/**
		 * Identity of one referenced UObject: path, class and the PIE world it
		 * belongs to.  Nothing is traversed, nothing is loaded.
		 */
		TSharedRef<FJsonObject> BuildHardObjectRef(
			const UObject* Object,
			const UClass* DeclaredClass,
			const FWriteContext& Ctx)
		{
			TSharedRef<FJsonObject> ObjectRef = MakeShared<FJsonObject>();
			ObjectRef->SetStringField(TEXT("kind"), TEXT("object"));
			if (Object == nullptr)
			{
				ObjectRef->SetBoolField(TEXT("isNull"), true);
				ObjectRef->SetStringField(TEXT("path"), FString());
				ObjectRef->SetStringField(
					TEXT("class"),
					DeclaredClass != nullptr ? DeclaredClass->GetPathName() : FString());
				ObjectRef->SetStringField(TEXT("worldKey"), FString());
				ObjectRef->SetNumberField(TEXT("pieInstanceId"), -1);
				ObjectRef->SetBoolField(TEXT("inPieWorld"), false);
				ObjectRef->SetBoolField(TEXT("truncated"), false);
				return ObjectRef;
			}

			bool bTruncated = false;
			int32 OriginalLength = 0;
			ObjectRef->SetBoolField(TEXT("isNull"), false);
			ObjectRef->SetStringField(
				TEXT("path"),
				TruncateChars(
					Object->GetPathName(),
					MaxReferencedObjectPathChars,
					bTruncated,
					OriginalLength));
			ObjectRef->SetBoolField(TEXT("truncated"), bTruncated);
			ObjectRef->SetNumberField(TEXT("originalPathLength"), OriginalLength);
			ObjectRef->SetStringField(
				TEXT("class"),
				Object->GetClass() != nullptr ? Object->GetClass()->GetPathName() : FString());
			ObjectRef->SetStringField(TEXT("name"), Object->GetName());

			const UWorld* World = nullptr;
			if (const UWorld* AsWorld = Cast<UWorld>(Object))
			{
				World = AsWorld;
			}
			else if (const AActor* AsActor = Cast<AActor>(Object))
			{
				World = AsActor->GetWorld();
			}
			else if (const UActorComponent* AsComponent = Cast<UActorComponent>(Object))
			{
				World = AsComponent->GetWorld();
			}

			FString WorldKey;
			int32 PieInstanceId = INDEX_NONE;
			if (World != nullptr && Ctx.AllContexts != nullptr)
			{
				for (const FWorldContextSnapshot& Candidate : *Ctx.AllContexts)
				{
					if (Candidate.World == World)
					{
						WorldKey = Candidate.ContextId;
						PieInstanceId = Candidate.PieInstanceId;
						break;
					}
				}
			}
			ObjectRef->SetStringField(TEXT("worldKey"), WorldKey);
			ObjectRef->SetNumberField(TEXT("pieInstanceId"), PieInstanceId);
			ObjectRef->SetBoolField(TEXT("inPieWorld"), !WorldKey.IsEmpty());
			return ObjectRef;
		}

		/** Identity of one soft reference; the asset is never resolved or loaded. */
		TSharedRef<FJsonObject> BuildSoftObjectRef(
			const FSoftObjectPath& SoftPath,
			const UClass* DeclaredClass)
		{
			TSharedRef<FJsonObject> ObjectRef = MakeShared<FJsonObject>();
			ObjectRef->SetStringField(TEXT("kind"), TEXT("softObject"));
			bool bTruncated = false;
			int32 OriginalLength = 0;
			const FString Path = SoftPath.ToString();
			ObjectRef->SetBoolField(TEXT("isNull"), Path.IsEmpty());
			ObjectRef->SetStringField(
				TEXT("path"),
				TruncateChars(Path, MaxReferencedObjectPathChars, bTruncated, OriginalLength));
			ObjectRef->SetBoolField(TEXT("truncated"), bTruncated);
			ObjectRef->SetNumberField(TEXT("originalPathLength"), OriginalLength);
			ObjectRef->SetStringField(
				TEXT("class"),
				DeclaredClass != nullptr ? DeclaredClass->GetPathName() : FString());
			ObjectRef->SetStringField(TEXT("worldKey"), FString());
			ObjectRef->SetNumberField(TEXT("pieInstanceId"), -1);
			ObjectRef->SetBoolField(TEXT("inPieWorld"), false);
			ObjectRef->SetBoolField(TEXT("resolved"), false);
			return ObjectRef;
		}

		/** Merge meta fields (truncation, int64 exactness, enum entry) into an entry. */
		void MergeMeta(const TSharedRef<FJsonObject>& Target, const TSharedRef<FJsonObject>& Meta)
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Field : Meta->Values)
			{
				Target->SetField(Field.Key, Field.Value);
			}
		}

		/**
		 * Format one scalar value.  Returns false with a stable reason for any
		 * shape that cannot be represented exactly.
		 */
		bool FormatScalarJson(
			const FProperty* Property,
			const void* ValuePtr,
			FWriteContext& Ctx,
			TSharedPtr<FJsonValue>& OutValue,
			const TSharedRef<FJsonObject>& OutMeta,
			FString& OutReason)
		{
			OutValue.Reset();
			OutReason.Reset();
			if (Property == nullptr || ValuePtr == nullptr)
			{
				OutReason = TEXT("unreadable-value");
				return false;
			}

			const EPropertyKind Kind = ClassifyProperty(Property);
			switch (Kind)
			{
			case EPropertyKind::Bool:
			{
				const bool bValue = static_cast<const FBoolProperty*>(Property)->GetPropertyValue(ValuePtr);
				OutValue = MakeShared<FJsonValueBoolean>(bValue);
				return true;
			}
			case EPropertyKind::Int8:
			case EPropertyKind::Int16:
			case EPropertyKind::Int32:
			case EPropertyKind::Int64:
			{
				const FNumericProperty* Numeric = CastField<FNumericProperty>(Property);
				const int64 Value = Numeric != nullptr ? Numeric->GetSignedIntPropertyValue(ValuePtr) : 0;
				if (Value >= -MaxExactValue && Value <= MaxExactValue)
				{
					OutValue = MakeShared<FJsonValueNumber>(static_cast<double>(Value));
				}
				else
				{
					OutValue = MakeShared<FJsonValueString>(FString::Printf(TEXT("%lld"), Value));
					OutMeta->SetBoolField(TEXT("valueAsString"), true);
				}
				return true;
			}
			case EPropertyKind::UInt8:
			case EPropertyKind::UInt16:
			case EPropertyKind::UInt32:
			case EPropertyKind::UInt64:
			{
				const FNumericProperty* Numeric = CastField<FNumericProperty>(Property);
				const uint64 Value = Numeric != nullptr ? Numeric->GetUnsignedIntPropertyValue(ValuePtr) : 0;
				if (Value <= static_cast<uint64>(MaxExactValue))
				{
					OutValue = MakeShared<FJsonValueNumber>(static_cast<double>(Value));
				}
				else
				{
					OutValue = MakeShared<FJsonValueString>(FString::Printf(TEXT("%llu"), Value));
					OutMeta->SetBoolField(TEXT("valueAsString"), true);
				}
				return true;
			}
			case EPropertyKind::Float:
			case EPropertyKind::Double:
			{
				const FNumericProperty* Numeric = CastField<FNumericProperty>(Property);
				const double Value = Numeric != nullptr ? Numeric->GetFloatingPointPropertyValue(ValuePtr) : 0.0;
				if (!FMath::IsFinite(Value))
				{
					OutReason = TEXT("non-finite-number");
					return false;
				}
				OutValue = MakeShared<FJsonValueNumber>(Value);
				return true;
			}
			case EPropertyKind::Name:
			{
				const FName Value = static_cast<const FNameProperty*>(Property)->GetPropertyValue(ValuePtr);
				const FString Text = Value.ToString();
				bool bTruncated = false;
				int32 OriginalLength = 0;
				const FString Written = TruncateChars(Text, Ctx.MaxStringChars, bTruncated, OriginalLength);
				if (!Ctx.TryReserveChars(Written.Len() + PerValueCharOverhead))
				{
					OutReason = TEXT("response-budget-exhausted");
					return false;
				}
				OutValue = MakeShared<FJsonValueString>(Written);
				OutMeta->SetBoolField(TEXT("truncated"), bTruncated);
				OutMeta->SetNumberField(TEXT("originalLength"), OriginalLength);
				return true;
			}
			case EPropertyKind::String:
			{
				const FString& Text = static_cast<const FStrProperty*>(Property)->GetPropertyValue(ValuePtr);
				bool bTruncated = false;
				int32 OriginalLength = 0;
				const FString Written = TruncateChars(Text, Ctx.MaxStringChars, bTruncated, OriginalLength);
				if (!Ctx.TryReserveChars(Written.Len() + PerValueCharOverhead))
				{
					OutReason = TEXT("response-budget-exhausted");
					return false;
				}
				OutValue = MakeShared<FJsonValueString>(Written);
				OutMeta->SetBoolField(TEXT("truncated"), bTruncated);
				OutMeta->SetNumberField(TEXT("originalLength"), OriginalLength);
				return true;
			}
			case EPropertyKind::Enum:
			{
				const UEnum* Enum = nullptr;
				int64 RawValue = 0;
				if (const FEnumProperty* EnumProperty = CastField<FEnumProperty>(Property))
				{
					Enum = EnumProperty->GetEnum();
					const FNumericProperty* Underlying = EnumProperty->GetUnderlyingProperty();
					RawValue = Underlying != nullptr ? Underlying->GetSignedIntPropertyValue(ValuePtr) : 0;
				}
				else if (const FByteProperty* ByteProperty = CastField<FByteProperty>(Property))
				{
					Enum = ByteProperty->GetIntPropertyEnum();
					RawValue = static_cast<int64>(ByteProperty->GetPropertyValue(ValuePtr));
				}
				const FString ShortName = EnumShortEntryName(Enum, RawValue);
				OutMeta->SetStringField(TEXT("enumName"), Enum != nullptr ? Enum->GetName() : FString());
				OutMeta->SetNumberField(TEXT("numericValue"), static_cast<double>(RawValue));
				if (ShortName.IsEmpty())
				{
					OutValue = MakeShared<FJsonValueString>(FString::Printf(TEXT("%lld"), RawValue));
					OutMeta->SetBoolField(TEXT("enumEntryKnown"), false);
					return true;
				}
				OutValue = MakeShared<FJsonValueString>(ShortName);
				OutMeta->SetStringField(TEXT("enumEntry"), Enum->GetNameStringByValue(RawValue));
				OutMeta->SetBoolField(TEXT("enumEntryKnown"), true);
				return true;
			}
			default:
				OutReason = TEXT("type-not-supported");
				return false;
			}
		}

		bool WriteValueInto(
			const FProperty* Property,
			const void* ValuePtr,
			const int32 Depth,
			FWriteContext& Ctx,
			const TSharedRef<FJsonObject>& Entry,
			FString& OutReason);

		bool WriteStructMembers(
			const FStructProperty* StructProperty,
			const void* StructMemory,
			const int32 Depth,
			FWriteContext& Ctx,
			const TSharedRef<FJsonObject>& Entry,
			FString& OutReason)
		{
			if (Depth > MaxStructDepth)
			{
				OutReason = TEXT("struct-depth-exceeded");
				return false;
			}
			if (StructProperty == nullptr || StructProperty->Struct == nullptr || StructMemory == nullptr)
			{
				OutReason = TEXT("unreadable-value");
				return false;
			}

			TArray<const FProperty*> MemberProperties;
			int32 TotalMembers = 0;
			for (TFieldIterator<FProperty> It(StructProperty->Struct); It; ++It)
			{
				++TotalMembers;
				if (MemberProperties.Num() < MaxStructMembersPerStruct)
				{
					MemberProperties.Add(*It);
				}
			}

			Entry->SetStringField(TEXT("structName"), StructProperty->Struct->GetName());
			Entry->SetNumberField(TEXT("memberCount"), TotalMembers);
			Entry->SetBoolField(TEXT("membersTruncated"), TotalMembers > MemberProperties.Num());
			Entry->SetNumberField(TEXT("membersOmitted"), TotalMembers - MemberProperties.Num());

			TArray<TSharedPtr<FJsonValue>> Members;
			for (const FProperty* Member : MemberProperties)
			{
				if (Member == nullptr)
				{
					continue;
				}
				const void* MemberValue = static_cast<const uint8*>(StructMemory) + Member->GetOffset_ForInternal();
				const EPropertyKind MemberKind = ClassifyProperty(Member);
				TSharedRef<FJsonObject> MemberEntry = MakeShared<FJsonObject>();
				MemberEntry->SetStringField(TEXT("name"), Member->GetName());
				MemberEntry->SetStringField(TEXT("type"), DeclaredTypeOf(Member));
				MemberEntry->SetStringField(TEXT("typeKind"), PropertyKindName(MemberKind));

				const FPropertySupport MemberSupport = EvaluatePropertySupport(Member);
				MemberEntry->SetBoolField(TEXT("supported"), MemberSupport.bSupported);
				if (!MemberSupport.bSupported)
				{
					MemberEntry->SetStringField(TEXT("reason"), MemberSupport.Reason);
					MemberEntry->SetBoolField(TEXT("valueReturned"), false);
					Members.Add(MakeShared<FJsonValueObject>(MemberEntry));
					continue;
				}

				FString MemberReason;
				if (!WriteValueInto(Member, MemberValue, Depth + 1, Ctx, MemberEntry, MemberReason))
				{
					MemberEntry->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
					MemberEntry->SetStringField(TEXT("reason"), MemberReason);
					MemberEntry->SetBoolField(TEXT("valueReturned"), false);
				}
				Members.Add(MakeShared<FJsonValueObject>(MemberEntry));
			}

			Entry->SetArrayField(TEXT("members"), Members);
			Entry->SetNumberField(TEXT("membersReturned"), Members.Num());
			return true;
		}

		/** Shared frame for one paged container: counts first, then the page. */
		void WriteContainerEnvelope(
			const TSharedRef<FJsonObject>& Entry,
			const TCHAR* ContainerKind,
			const int32 TotalCount,
			const int32 Begin,
			const int32 ReturnedCount,
			const int32 Limit,
			bool bBudgetExceeded)
		{
			Entry->SetStringField(TEXT("containerKind"), ContainerKind);
			Entry->SetNumberField(TEXT("totalCount"), TotalCount);
			Entry->SetNumberField(TEXT("offset"), Begin);
			Entry->SetNumberField(TEXT("limit"), Limit);
			Entry->SetNumberField(TEXT("returnedCount"), ReturnedCount);
			Entry->SetBoolField(TEXT("hasMore"), (Begin + ReturnedCount) < TotalCount);
			if (bBudgetExceeded)
			{
				Entry->SetBoolField(TEXT("budgetExceeded"), true);
			}
		}

		bool WriteArrayItems(
			const FArrayProperty* ArrayProperty,
			const void* ValuePtr,
			const int32 Depth,
			FWriteContext& Ctx,
			const TSharedRef<FJsonObject>& Entry,
			FString& OutReason)
		{
			const FProperty* Inner = ArrayProperty != nullptr ? ArrayProperty->Inner : nullptr;
			if (Inner == nullptr || ValuePtr == nullptr)
			{
				OutReason = TEXT("unreadable-value");
				return false;
			}

			FScriptArrayHelper Helper(ArrayProperty, const_cast<void*>(ValuePtr));
			const int32 TotalCount = Helper.Num();
			const int32 Begin = FMath::Clamp(Ctx.Offset, 0, TotalCount);
			const int32 End = FMath::Clamp(Ctx.Offset + Ctx.Limit, Begin, TotalCount);

			Entry->SetStringField(TEXT("elementType"), DeclaredTypeOf(Inner));
			Entry->SetStringField(TEXT("elementTypeKind"), PropertyKindName(ClassifyProperty(Inner)));

			TArray<TSharedPtr<FJsonValue>> Items;
			bool bBudgetExceeded = false;
			for (int32 Index = Begin; Index < End; ++Index)
			{
				if (!Ctx.TryReserveItem())
				{
					bBudgetExceeded = true;
					break;
				}
				TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
				Item->SetNumberField(TEXT("index"), Index);
				Item->SetStringField(TEXT("type"), DeclaredTypeOf(Inner));
				Item->SetStringField(TEXT("typeKind"), PropertyKindName(ClassifyProperty(Inner)));
				FString ItemReason;
				if (!WriteValueInto(Inner, Helper.GetElementPtr(Index), Depth + 1, Ctx, Item, ItemReason))
				{
					Item->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
					Item->SetStringField(TEXT("reason"), ItemReason);
					Item->SetBoolField(TEXT("valueReturned"), false);
				}
				Items.Add(MakeShared<FJsonValueObject>(Item));
			}

			WriteContainerEnvelope(Entry, TEXT("array"), TotalCount, Begin, Items.Num(), Ctx.Limit, bBudgetExceeded);
			Entry->SetArrayField(TEXT("items"), Items);
			return true;
		}

		bool WriteSetItems(
			const FSetProperty* SetProperty,
			const void* ValuePtr,
			const int32 Depth,
			FWriteContext& Ctx,
			const TSharedRef<FJsonObject>& Entry,
			FString& OutReason)
		{
			const FProperty* Element = SetProperty != nullptr ? SetProperty->ElementProp : nullptr;
			if (Element == nullptr || ValuePtr == nullptr)
			{
				OutReason = TEXT("unreadable-value");
				return false;
			}

			FScriptSetHelper Helper(SetProperty, const_cast<void*>(ValuePtr));
			const int32 TotalCount = Helper.Num();
			const int32 MaxIndex = Helper.GetMaxIndex();

			Entry->SetStringField(TEXT("elementType"), DeclaredTypeOf(Element));
			Entry->SetStringField(TEXT("elementTypeKind"), PropertyKindName(ClassifyProperty(Element)));
			Entry->SetBoolField(TEXT("orderStable"), false);

			TArray<TSharedPtr<FJsonValue>> Items;
			bool bBudgetExceeded = false;
			int32 Position = 0;
			int32 FirstIndex = INDEX_NONE;
			for (int32 InternalIndex = 0; InternalIndex < MaxIndex; ++InternalIndex)
			{
				if (!Helper.IsValidIndex(InternalIndex))
				{
					continue;
				}
				const int32 CurrentPosition = Position++;
				if (CurrentPosition < Ctx.Offset)
				{
					continue;
				}
				if (Items.Num() >= Ctx.Limit)
				{
					break;
				}
				if (!Ctx.TryReserveItem())
				{
					bBudgetExceeded = true;
					break;
				}
				if (FirstIndex == INDEX_NONE)
				{
					FirstIndex = CurrentPosition;
				}
				TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
				Item->SetNumberField(TEXT("index"), CurrentPosition);
				Item->SetStringField(TEXT("type"), DeclaredTypeOf(Element));
				Item->SetStringField(TEXT("typeKind"), PropertyKindName(ClassifyProperty(Element)));
				FString ItemReason;
				if (!WriteValueInto(Element, Helper.GetElementPtr(InternalIndex), Depth + 1, Ctx, Item, ItemReason))
				{
					Item->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
					Item->SetStringField(TEXT("reason"), ItemReason);
					Item->SetBoolField(TEXT("valueReturned"), false);
				}
				Items.Add(MakeShared<FJsonValueObject>(Item));
			}

			const int32 Begin = FirstIndex == INDEX_NONE ? FMath::Min(Ctx.Offset, TotalCount) : FirstIndex;
			WriteContainerEnvelope(Entry, TEXT("set"), TotalCount, Begin, Items.Num(), Ctx.Limit, bBudgetExceeded);
			Entry->SetArrayField(TEXT("items"), Items);
			return true;
		}

		/** Compact JSON value used for map keys and values. */
		bool WritePlainValue(
			const FProperty* Property,
			const void* ValuePtr,
			const int32 Depth,
			FWriteContext& Ctx,
			TSharedPtr<FJsonValue>& OutValue,
			FString& OutReason)
		{
			if (Property == nullptr || ValuePtr == nullptr)
			{
				OutReason = TEXT("unreadable-value");
				return false;
			}
			if (Depth > MaxStructDepth)
			{
				OutReason = TEXT("struct-depth-exceeded");
				return false;
			}

			const EPropertyKind Kind = ClassifyProperty(Property);
			if (Kind == EPropertyKind::Struct)
			{
				const FStructProperty* StructProperty = CastField<FStructProperty>(Property);
				if (StructProperty == nullptr || StructProperty->Struct == nullptr)
				{
					OutReason = TEXT("unreadable-value");
					return false;
				}
				TSharedRef<FJsonObject> Plain = MakeShared<FJsonObject>();
				int32 Omitted = 0;
				for (TFieldIterator<FProperty> It(StructProperty->Struct); It; ++It)
				{
					const FProperty* Member = *It;
					if (Member == nullptr)
					{
						continue;
					}
					const void* MemberValue = static_cast<const uint8*>(ValuePtr) + Member->GetOffset_ForInternal();
					TSharedPtr<FJsonValue> MemberJson;
					FString MemberReason;
					if (WritePlainValue(Member, MemberValue, Depth + 1, Ctx, MemberJson, MemberReason))
					{
						Plain->SetField(Member->GetName(), MemberJson);
					}
					else
					{
						++Omitted;
					}
				}
				if (Omitted > 0)
				{
					Plain->SetNumberField(TEXT("unsupportedMemberCount"), Omitted);
				}
				OutValue = MakeShared<FJsonValueObject>(Plain);
				return true;
			}
			if (IsObjectRefKind(Kind))
			{
				const UClass* DeclaredClass = nullptr;
				if (const FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
				{
					DeclaredClass = ObjectProperty->PropertyClass;
				}
				if (Kind == EPropertyKind::SoftObject || Kind == EPropertyKind::SoftClass)
				{
					const FSoftObjectPtr& SoftPtr = *static_cast<const FSoftObjectPtr*>(ValuePtr);
					OutValue = MakeShared<FJsonValueObject>(
						BuildSoftObjectRef(SoftPtr.ToSoftObjectPath(), DeclaredClass));
					return true;
				}
				const UObject* Referenced = static_cast<const FObjectPropertyBase*>(Property)->GetObjectPropertyValue(ValuePtr);
				OutValue = MakeShared<FJsonValueObject>(BuildHardObjectRef(Referenced, DeclaredClass, Ctx));
				return true;
			}

			TSharedPtr<FJsonValue> ScalarValue;
			const TSharedRef<FJsonObject> Meta = MakeShared<FJsonObject>();
			if (!FormatScalarJson(Property, ValuePtr, Ctx, ScalarValue, Meta, OutReason))
			{
				return false;
			}
			OutValue = ScalarValue;
			return true;
		}

		bool WriteMapItems(
			const FMapProperty* MapProperty,
			const void* ValuePtr,
			const int32 Depth,
			FWriteContext& Ctx,
			const TSharedRef<FJsonObject>& Entry,
			FString& OutReason)
		{
			const FProperty* KeyProperty = MapProperty != nullptr ? MapProperty->KeyProp : nullptr;
			const FProperty* ValueProperty = MapProperty != nullptr ? MapProperty->ValueProp : nullptr;
			if (KeyProperty == nullptr || ValueProperty == nullptr || ValuePtr == nullptr)
			{
				OutReason = TEXT("unreadable-value");
				return false;
			}

			FScriptMapHelper Helper(MapProperty, const_cast<void*>(ValuePtr));
			const int32 TotalCount = Helper.Num();
			const int32 MaxIndex = Helper.GetMaxIndex();

			Entry->SetStringField(TEXT("keyType"), DeclaredTypeOf(KeyProperty));
			Entry->SetStringField(TEXT("keyTypeKind"), PropertyKindName(ClassifyProperty(KeyProperty)));
			Entry->SetStringField(TEXT("valueType"), DeclaredTypeOf(ValueProperty));
			Entry->SetStringField(TEXT("valueTypeKind"), PropertyKindName(ClassifyProperty(ValueProperty)));
			Entry->SetBoolField(TEXT("orderStable"), false);

			TArray<TSharedPtr<FJsonValue>> Items;
			bool bBudgetExceeded = false;
			int32 Position = 0;
			int32 FirstIndex = INDEX_NONE;
			for (int32 InternalIndex = 0; InternalIndex < MaxIndex; ++InternalIndex)
			{
				if (!Helper.IsValidIndex(InternalIndex))
				{
					continue;
				}
				const int32 CurrentPosition = Position++;
				if (CurrentPosition < Ctx.Offset)
				{
					continue;
				}
				if (Items.Num() >= Ctx.Limit)
				{
					break;
				}
				if (!Ctx.TryReserveItem())
				{
					bBudgetExceeded = true;
					break;
				}
				if (FirstIndex == INDEX_NONE)
				{
					FirstIndex = CurrentPosition;
				}

				TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
				Item->SetNumberField(TEXT("index"), CurrentPosition);
				TSharedPtr<FJsonValue> KeyJson;
				FString KeyReason;
				if (WritePlainValue(KeyProperty, Helper.GetKeyPtr(InternalIndex), Depth + 1, Ctx, KeyJson, KeyReason))
				{
					Item->SetField(TEXT("key"), KeyJson);
				}
				else
				{
					Item->SetStringField(TEXT("keyStatus"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
					Item->SetStringField(TEXT("keyReason"), KeyReason);
				}
				TSharedPtr<FJsonValue> ValueJson;
				FString ItemReason;
				if (WritePlainValue(ValueProperty, Helper.GetValuePtr(InternalIndex), Depth + 1, Ctx, ValueJson, ItemReason))
				{
					Item->SetField(TEXT("value"), ValueJson);
				}
				else
				{
					Item->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
					Item->SetStringField(TEXT("reason"), ItemReason);
					Item->SetBoolField(TEXT("valueReturned"), false);
				}
				Items.Add(MakeShared<FJsonValueObject>(Item));
			}

			const int32 Begin = FirstIndex == INDEX_NONE ? FMath::Min(Ctx.Offset, TotalCount) : FirstIndex;
			WriteContainerEnvelope(Entry, TEXT("map"), TotalCount, Begin, Items.Num(), Ctx.Limit, bBudgetExceeded);
			Entry->SetArrayField(TEXT("items"), Items);
			return true;
		}

		bool WriteValueInto(
			const FProperty* Property,
			const void* ValuePtr,
			const int32 Depth,
			FWriteContext& Ctx,
			const TSharedRef<FJsonObject>& Entry,
			FString& OutReason)
		{
			OutReason.Reset();
			if (Property == nullptr || ValuePtr == nullptr)
			{
				OutReason = TEXT("unreadable-value");
				return false;
			}
			if (Depth > MaxStructDepth)
			{
				OutReason = TEXT("struct-depth-exceeded");
				return false;
			}

			const EPropertyKind Kind = ClassifyProperty(Property);
			switch (Kind)
			{
			case EPropertyKind::Struct:
				return WriteStructMembers(
					CastField<FStructProperty>(Property),
					ValuePtr,
					Depth,
					Ctx,
					Entry,
					OutReason);
			case EPropertyKind::Array:
				return WriteArrayItems(
					CastField<FArrayProperty>(Property),
					ValuePtr,
					Depth,
					Ctx,
					Entry,
					OutReason);
			case EPropertyKind::Set:
				return WriteSetItems(
					CastField<FSetProperty>(Property),
					ValuePtr,
					Depth,
					Ctx,
					Entry,
					OutReason);
			case EPropertyKind::Map:
				return WriteMapItems(
					CastField<FMapProperty>(Property),
					ValuePtr,
					Depth,
					Ctx,
					Entry,
					OutReason);
			case EPropertyKind::Object:
			case EPropertyKind::Class:
			case EPropertyKind::WeakObject:
			{
				const FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property);
				const UObject* Referenced = ObjectProperty != nullptr
					? ObjectProperty->GetObjectPropertyValue(ValuePtr)
					: nullptr;
				Entry->SetObjectField(
					TEXT("objectRef"),
					BuildHardObjectRef(
						Referenced,
						ObjectProperty != nullptr ? ObjectProperty->PropertyClass : nullptr,
						Ctx));
				return true;
			}
			case EPropertyKind::SoftObject:
			case EPropertyKind::SoftClass:
			{
				const FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property);
				const FSoftObjectPtr& SoftPtr = *static_cast<const FSoftObjectPtr*>(ValuePtr);
				Entry->SetObjectField(
					TEXT("objectRef"),
					BuildSoftObjectRef(
						SoftPtr.ToSoftObjectPath(),
						ObjectProperty != nullptr ? ObjectProperty->PropertyClass : nullptr));
				return true;
			}
			default:
				break;
			}

			if (!IsScalarKind(Kind))
			{
				OutReason = EvaluateSupportInternal(Property, Depth).Reason;
				if (OutReason.IsEmpty())
				{
					OutReason = TEXT("type-not-supported");
				}
				return false;
			}

			TSharedPtr<FJsonValue> ScalarValue;
			const TSharedRef<FJsonObject> Meta = MakeShared<FJsonObject>();
			if (!FormatScalarJson(Property, ValuePtr, Ctx, ScalarValue, Meta, OutReason))
			{
				return false;
			}
			Entry->SetField(TEXT("value"), ScalarValue);
			MergeMeta(Entry, Meta);
			return true;
		}

		FPropertySupport EvaluateSupportInternal(const FProperty* Property, const int32 Depth)
		{
			FPropertySupport Result;
			if (Property == nullptr)
			{
				Result.Reason = TEXT("property-not-found");
				return Result;
			}
			if (Depth > MaxStructDepth)
			{
				Result.Reason = TEXT("struct-depth-exceeded");
				return Result;
			}

			const EPropertyKind Kind = ClassifyProperty(Property);
			switch (Kind)
			{
			case EPropertyKind::Bitfield:
				Result.Reason = TEXT("bitfield-not-readable");
				return Result;
			case EPropertyKind::Text:
				Result.Reason = TEXT("text-not-readable");
				return Result;
			case EPropertyKind::Delegate:
			case EPropertyKind::MulticastDelegate:
				Result.Reason = TEXT("delegate-not-readable");
				return Result;
			case EPropertyKind::Interface:
				Result.Reason = TEXT("interface-not-readable");
				return Result;
			case EPropertyKind::FieldPath:
				Result.Reason = TEXT("field-path-not-readable");
				return Result;
			case EPropertyKind::LazyObject:
				// Resolving a lazy object would force a package load.
				Result.Reason = TEXT("lazy-object-not-readable");
				return Result;
			case EPropertyKind::Unknown:
				Result.Reason = TEXT("type-not-supported");
				return Result;
			case EPropertyKind::Struct:
			{
				const FStructProperty* StructProperty = CastField<FStructProperty>(Property);
				if (StructProperty == nullptr || StructProperty->Struct == nullptr)
				{
					Result.Reason = TEXT("unreadable-value");
					return Result;
				}
				int32 ReadableMembers = 0;
				int32 InspectedMembers = 0;
				for (TFieldIterator<FProperty> It(StructProperty->Struct); It; ++It)
				{
					if (InspectedMembers >= MaxStructMembersPerStruct)
					{
						break;
					}
					++InspectedMembers;
					if (EvaluateSupportInternal(*It, Depth + 1).bSupported)
					{
						++ReadableMembers;
					}
				}
				if (ReadableMembers == 0)
				{
					Result.Reason = TEXT("struct-has-no-readable-members");
					return Result;
				}
				Result.bSupported = true;
				return Result;
			}
			case EPropertyKind::Array:
			case EPropertyKind::Set:
			{
				const FProperty* Element = nullptr;
				if (Kind == EPropertyKind::Array)
				{
					const FArrayProperty* ArrayProperty = CastField<FArrayProperty>(Property);
					Element = ArrayProperty != nullptr ? ArrayProperty->Inner : nullptr;
				}
				else
				{
					const FSetProperty* SetProperty = CastField<FSetProperty>(Property);
					Element = SetProperty != nullptr ? SetProperty->ElementProp : nullptr;
				}
				if (Element == nullptr)
				{
					Result.Reason = TEXT("unreadable-value");
					return Result;
				}
				if (IsContainerKind(ClassifyProperty(Element)))
				{
					// Defensive: UnrealHeaderTool rejects a nested UPROPERTY
					// container at compile time, so a real reflected property
					// cannot reach this branch today.  Keeping the refusal means a
					// future engine change degrades into an explicit reason
					// instead of an unbounded recursive dump.
					Result.Reason = TEXT("nested-container-not-supported");
					return Result;
				}
				const FPropertySupport ElementSupport = EvaluateSupportInternal(Element, Depth + 1);
				if (!ElementSupport.bSupported)
				{
					Result.Reason = FString::Printf(
						TEXT("unsupported-element-type:%s"),
						*ElementSupport.Reason);
					return Result;
				}
				Result.bSupported = true;
				return Result;
			}
			case EPropertyKind::Map:
			{
				const FMapProperty* MapProperty = CastField<FMapProperty>(Property);
				if (MapProperty == nullptr || MapProperty->KeyProp == nullptr || MapProperty->ValueProp == nullptr)
				{
					Result.Reason = TEXT("unreadable-value");
					return Result;
				}
				// Defensive, for the same reason as the Array/Set branch above.
				if (IsContainerKind(ClassifyProperty(MapProperty->KeyProp))
					|| IsContainerKind(ClassifyProperty(MapProperty->ValueProp)))
				{
					Result.Reason = TEXT("nested-container-not-supported");
					return Result;
				}
				const FPropertySupport KeySupport = EvaluateSupportInternal(MapProperty->KeyProp, Depth + 1);
				if (!KeySupport.bSupported)
				{
					Result.Reason = FString::Printf(TEXT("unsupported-key-type:%s"), *KeySupport.Reason);
					return Result;
				}
				const FPropertySupport ValueSupport = EvaluateSupportInternal(MapProperty->ValueProp, Depth + 1);
				if (!ValueSupport.bSupported)
				{
					Result.Reason = FString::Printf(TEXT("unsupported-value-type:%s"), *ValueSupport.Reason);
					return Result;
				}
				Result.bSupported = true;
				return Result;
			}
			default:
				Result.bSupported = true;
				return Result;
			}
		}

		bool IsPlainPropertyIdentifier(const FString& Name)
		{
			if (Name.IsEmpty() || Name.Len() > MaxReadPropertyNameChars)
			{
				return false;
			}
			const TCHAR First = Name[0];
			if (!(FChar::IsAlpha(First) || First == TEXT('_')))
			{
				return false;
			}
			for (int32 Index = 1; Index < Name.Len(); ++Index)
			{
				const TCHAR Character = Name[Index];
				if (!(FChar::IsAlnum(Character) || Character == TEXT('_')))
				{
					return false;
				}
			}
			return true;
		}

		/** Shared envelope so both methods describe the exact same target. */
		void WriteTargetEnvelope(
			const FResolvedPropertyTarget& Resolved,
			const TSharedRef<FJsonObject>& Result)
		{
			const FWorldContextSnapshot& Snapshot = Resolved.Snapshot;
			Result->SetBoolField(TEXT("readOnly"), true);
			Result->SetBoolField(TEXT("requiresDatabase"), false);
			Result->SetStringField(TEXT("source"), TEXT("pie-live"));
			Result->SetStringField(TEXT("sampledAtUtc"), FDateTime::UtcNow().ToIso8601());
			Result->SetStringField(TEXT("worldKey"), Snapshot.ContextId);
			Result->SetStringField(TEXT("contextId"), Snapshot.ContextId);
			Result->SetNumberField(TEXT("pieInstanceId"), Snapshot.PieInstanceId);
			Result->SetStringField(TEXT("worldPath"), Snapshot.WorldPath);
			Result->SetStringField(TEXT("worldName"), Snapshot.WorldName);
			Result->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
			Result->SetNumberField(TEXT("netMode"), Snapshot.NetMode);
			Result->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);
			Result->SetBoolField(TEXT("isServer"), Snapshot.bIsServer);
			Result->SetBoolField(TEXT("isClient"), Snapshot.bIsClient);
			Result->SetStringField(TEXT("targetKind"), Resolved.bIsComponent ? TEXT("component") : TEXT("actor"));
			Result->SetStringField(
				TEXT("actorPath"),
				Resolved.Actor != nullptr ? Resolved.Actor->GetPathName() : FString());
			Result->SetStringField(
				TEXT("objectPath"),
				Resolved.Target != nullptr ? Resolved.Target->GetPathName() : FString());
			Result->SetStringField(
				TEXT("objectName"),
				Resolved.Target != nullptr ? Resolved.Target->GetName() : FString());
			Result->SetStringField(
				TEXT("classPath"),
				Resolved.Target != nullptr && Resolved.Target->GetClass() != nullptr
					? Resolved.Target->GetClass()->GetPathName()
					: FString());
		}
	}

	const TCHAR* PropertyKindName(const EPropertyKind Kind)
	{
		switch (Kind)
		{
		case EPropertyKind::Bool:
			return TEXT("bool");
		case EPropertyKind::Int8:
			return TEXT("int8");
		case EPropertyKind::Int16:
			return TEXT("int16");
		case EPropertyKind::Int32:
			return TEXT("int32");
		case EPropertyKind::Int64:
			return TEXT("int64");
		case EPropertyKind::UInt8:
			return TEXT("uint8");
		case EPropertyKind::UInt16:
			return TEXT("uint16");
		case EPropertyKind::UInt32:
			return TEXT("uint32");
		case EPropertyKind::UInt64:
			return TEXT("uint64");
		case EPropertyKind::Float:
			return TEXT("float");
		case EPropertyKind::Double:
			return TEXT("double");
		case EPropertyKind::Name:
			return TEXT("name");
		case EPropertyKind::String:
			return TEXT("string");
		case EPropertyKind::Enum:
			return TEXT("enum");
		case EPropertyKind::Struct:
			return TEXT("struct");
		case EPropertyKind::Array:
			return TEXT("array");
		case EPropertyKind::Set:
			return TEXT("set");
		case EPropertyKind::Map:
			return TEXT("map");
		case EPropertyKind::Object:
			return TEXT("object");
		case EPropertyKind::Class:
			return TEXT("class");
		case EPropertyKind::SoftObject:
			return TEXT("softObject");
		case EPropertyKind::SoftClass:
			return TEXT("softClass");
		case EPropertyKind::WeakObject:
			return TEXT("weakObject");
		case EPropertyKind::LazyObject:
			return TEXT("lazyObject");
		case EPropertyKind::Text:
			return TEXT("text");
		case EPropertyKind::Delegate:
			return TEXT("delegate");
		case EPropertyKind::MulticastDelegate:
			return TEXT("multicastDelegate");
		case EPropertyKind::Interface:
			return TEXT("interface");
		case EPropertyKind::FieldPath:
			return TEXT("fieldPath");
		case EPropertyKind::Bitfield:
			return TEXT("bitfield");
		case EPropertyKind::Unknown:
		default:
			return TEXT("unknown");
		}
	}

	const TCHAR* PropertyValueStatusCode(const EPropertyValueStatus Status)
	{
		switch (Status)
		{
		case EPropertyValueStatus::Ok:
			return TEXT("ok");
		case EPropertyValueStatus::UnsupportedType:
			return TEXT("unsupported-type");
		case EPropertyValueStatus::Unknown:
			return TEXT("unknown");
		case EPropertyValueStatus::NotExposed:
			return TEXT("not-exposed");
		default:
			return TEXT("unknown");
		}
	}

	EPropertyKind ClassifyProperty(const FProperty* Property)
	{
		if (Property == nullptr)
		{
			return EPropertyKind::Unknown;
		}
		if (const FBoolProperty* BoolProperty = CastField<FBoolProperty>(Property))
		{
			return BoolProperty->IsNativeBool() ? EPropertyKind::Bool : EPropertyKind::Bitfield;
		}
		if (const FByteProperty* ByteProperty = CastField<FByteProperty>(Property))
		{
			return ByteProperty->GetIntPropertyEnum() != nullptr ? EPropertyKind::Enum : EPropertyKind::UInt8;
		}
		if (Property->IsA<FInt8Property>())
		{
			return EPropertyKind::Int8;
		}
		if (Property->IsA<FInt16Property>())
		{
			return EPropertyKind::Int16;
		}
		if (Property->IsA<FIntProperty>())
		{
			return EPropertyKind::Int32;
		}
		if (Property->IsA<FInt64Property>())
		{
			return EPropertyKind::Int64;
		}
		if (Property->IsA<FUInt16Property>())
		{
			return EPropertyKind::UInt16;
		}
		if (Property->IsA<FUInt32Property>())
		{
			return EPropertyKind::UInt32;
		}
		if (Property->IsA<FUInt64Property>())
		{
			return EPropertyKind::UInt64;
		}
		if (Property->IsA<FFloatProperty>())
		{
			return EPropertyKind::Float;
		}
		if (Property->IsA<FDoubleProperty>() || Property->IsA<FLargeWorldCoordinatesRealProperty>())
		{
			return EPropertyKind::Double;
		}
		if (Property->IsA<FNameProperty>())
		{
			return EPropertyKind::Name;
		}
		if (Property->IsA<FStrProperty>())
		{
			return EPropertyKind::String;
		}
		if (Property->IsA<FTextProperty>())
		{
			return EPropertyKind::Text;
		}
		if (Property->IsA<FEnumProperty>())
		{
			return EPropertyKind::Enum;
		}
		if (Property->IsA<FStructProperty>())
		{
			return EPropertyKind::Struct;
		}
		if (Property->IsA<FArrayProperty>())
		{
			return EPropertyKind::Array;
		}
		if (Property->IsA<FSetProperty>())
		{
			return EPropertyKind::Set;
		}
		if (Property->IsA<FMapProperty>())
		{
			return EPropertyKind::Map;
		}
		if (Property->IsA<FSoftClassProperty>())
		{
			return EPropertyKind::SoftClass;
		}
		if (Property->IsA<FSoftObjectProperty>())
		{
			return EPropertyKind::SoftObject;
		}
		if (Property->IsA<FClassProperty>())
		{
			return EPropertyKind::Class;
		}
		if (Property->IsA<FWeakObjectProperty>())
		{
			return EPropertyKind::WeakObject;
		}
		if (Property->IsA<FLazyObjectProperty>())
		{
			return EPropertyKind::LazyObject;
		}
		if (Property->IsA<FObjectProperty>())
		{
			return EPropertyKind::Object;
		}
		if (Property->IsA<FInterfaceProperty>())
		{
			return EPropertyKind::Interface;
		}
		if (Property->IsA<FMulticastDelegateProperty>())
		{
			return EPropertyKind::MulticastDelegate;
		}
		if (Property->IsA<FDelegateProperty>())
		{
			return EPropertyKind::Delegate;
		}
		if (Property->IsA<FFieldPathProperty>())
		{
			return EPropertyKind::FieldPath;
		}
		return EPropertyKind::Unknown;
	}

	FPropertySupport EvaluatePropertySupport(const FProperty* Property)
	{
		return EvaluateSupportInternal(Property, 0);
	}

	TSharedRef<FJsonObject> DescribePropertyDescriptor(const FProperty* Property)
	{
		TSharedRef<FJsonObject> Descriptor = MakeShared<FJsonObject>();
		if (Property == nullptr)
		{
			Descriptor->SetStringField(TEXT("name"), FString());
			Descriptor->SetStringField(TEXT("type"), FString());
			Descriptor->SetStringField(TEXT("typeKind"), PropertyKindName(EPropertyKind::Unknown));
			Descriptor->SetBoolField(TEXT("supported"), false);
			Descriptor->SetStringField(TEXT("unsupportedReason"), TEXT("property-not-found"));
			return Descriptor;
		}

		const EPropertyKind Kind = ClassifyProperty(Property);
		Descriptor->SetStringField(TEXT("name"), Property->GetName());
		Descriptor->SetStringField(TEXT("type"), DeclaredTypeOf(Property));
		Descriptor->SetStringField(TEXT("typeKind"), PropertyKindName(Kind));
		Descriptor->SetBoolField(TEXT("isArray"), Kind == EPropertyKind::Array);
		Descriptor->SetBoolField(TEXT("isMap"), Kind == EPropertyKind::Map);
		Descriptor->SetBoolField(TEXT("isSet"), Kind == EPropertyKind::Set);
		Descriptor->SetBoolField(TEXT("isStruct"), Kind == EPropertyKind::Struct);
		Descriptor->SetBoolField(TEXT("isObjectRef"), IsObjectRefKind(Kind));
		Descriptor->SetBoolField(TEXT("isBitfield"), Kind == EPropertyKind::Bitfield);

		const FPropertySupport Support = EvaluateSupportInternal(Property, 0);
		Descriptor->SetBoolField(TEXT("supported"), Support.bSupported);
		if (!Support.bSupported)
		{
			Descriptor->SetStringField(TEXT("unsupportedReason"), Support.Reason);
		}
		AddDeclaringClass(Property, Descriptor);
		return Descriptor;
	}

	EFailure ResolvePieComponentExact(
		AActor* Actor,
		const int32 PieInstanceId,
		const FString& ComponentPath,
		const FString& ComponentName,
		const FString& ComponentClassPath,
		UActorComponent*& OutComponent,
		FString& OutErrorMessage)
	{
		OutComponent = nullptr;
		OutErrorMessage.Reset();
		if (Actor == nullptr)
		{
			OutErrorMessage = TEXT("The Actor could not be resolved.");
			return EFailure::ActorNotFound;
		}
		if (ComponentPath.IsEmpty() && ComponentName.IsEmpty())
		{
			return EFailure::None;
		}

		TArray<UActorComponent*> Components;
		Actor->GetComponents(Components);
		UActorComponent* ComponentMatch = nullptr;
		int32 ComponentMatchCount = 0;
		TArray<FString> ComponentCandidates;
		for (UActorComponent* Candidate : Components)
		{
			if (Candidate == nullptr)
			{
				continue;
			}
			const bool bMatches = !ComponentPath.IsEmpty()
				? Candidate->GetPathName().Equals(ComponentPath, ESearchCase::CaseSensitive)
				: Candidate->GetName().Equals(ComponentName, ESearchCase::CaseSensitive);
			if (!bMatches)
			{
				continue;
			}
			++ComponentMatchCount;
			if (ComponentMatch == nullptr)
			{
				ComponentMatch = Candidate;
			}
			if (ComponentCandidates.Num() < MaxAmbiguousCandidates)
			{
				ComponentCandidates.Add(Candidate->GetPathName());
			}
		}
		if (ComponentMatchCount == 0 || ComponentMatch == nullptr)
		{
			OutErrorMessage = FString::Printf(
				TEXT("No owned Component of Actor '%s' matches the requested identity in PIE instance %d."),
				*Actor->GetPathName(),
				PieInstanceId);
			return EFailure::ComponentNotFound;
		}
		if (ComponentMatchCount > 1)
		{
			FString Joined;
			for (const FString& CandidatePath : ComponentCandidates)
			{
				if (!Joined.IsEmpty())
				{
					Joined += TEXT(", ");
				}
				Joined += CandidatePath;
			}
			OutErrorMessage = FString::Printf(
				TEXT("The Component identity matches %d owned Components in PIE instance %d; use componentPath. Bounded candidates: %s"),
				ComponentMatchCount,
				PieInstanceId,
				*Joined);
			return EFailure::ComponentAmbiguous;
		}
		if (ComponentMatch->IsBeingDestroyed())
		{
			OutErrorMessage = FString::Printf(
				TEXT("The matched Component '%s' is being destroyed."),
				*ComponentMatch->GetPathName());
			return EFailure::ComponentNotFound;
		}
		if (!ComponentClassPath.IsEmpty()
			&& (ComponentMatch->GetClass() == nullptr
				|| !ComponentMatch->GetClass()->GetPathName().Equals(ComponentClassPath, ESearchCase::CaseSensitive)))
		{
			OutErrorMessage = FString::Printf(
				TEXT("Component '%s' is class '%s', not the expected '%s'."),
				*ComponentMatch->GetPathName(),
				ComponentMatch->GetClass() != nullptr ? *ComponentMatch->GetClass()->GetPathName() : TEXT(""),
				*ComponentClassPath);
			return EFailure::ComponentStale;
		}
		OutComponent = ComponentMatch;
		return EFailure::None;
	}

	EFailure ResolvePiePropertyTarget(
		const FPropertyTargetRequest& Request,
		FResolvedPropertyTarget& OutResolved,
		FString& OutErrorMessage)
	{
		OutResolved = FResolvedPropertyTarget();
		OutErrorMessage.Reset();

		if (!Request.ComponentPath.IsEmpty() && !Request.ComponentName.IsEmpty())
		{
			OutErrorMessage = TEXT("Provide at most one Component identity: componentPath or componentName.");
			return EFailure::InvalidRequest;
		}
		if (!Request.ComponentClassPath.IsEmpty()
			&& Request.ComponentPath.IsEmpty()
			&& Request.ComponentName.IsEmpty())
		{
			OutErrorMessage = TEXT("componentClassPath requires componentPath or componentName.");
			return EFailure::InvalidRequest;
		}

		FResolvedPieActor ResolvedActor;
		const EFailure ActorFailure = ResolvePieActorExact(Request.Actor, ResolvedActor, OutErrorMessage);
		if (ActorFailure != EFailure::None)
		{
			return ActorFailure;
		}

		OutResolved.Snapshot = ResolvedActor.Snapshot;
		OutResolved.Actor = ResolvedActor.Actor;
		OutResolved.Target = ResolvedActor.Actor;
		OutResolved.bIsComponent = false;
		// The full live context list is kept so a referenced UObject can be
		// placed in its own PIE world without a second scan.
		bool bContextScanTruncated = false;
		CollectPieWorldContexts(OutResolved.AllContexts, bContextScanTruncated);

		if (Request.ComponentPath.IsEmpty() && Request.ComponentName.IsEmpty())
		{
			return EFailure::None;
		}

		UActorComponent* Component = nullptr;
		const EFailure ComponentFailure = ResolvePieComponentExact(
			ResolvedActor.Actor,
			Request.Actor.PieInstanceId,
			Request.ComponentPath,
			Request.ComponentName,
			Request.ComponentClassPath,
			Component,
			OutErrorMessage);
		if (ComponentFailure != EFailure::None)
		{
			return ComponentFailure;
		}
		OutResolved.Target = Component;
		OutResolved.bIsComponent = true;
		return EFailure::None;
	}

	bool TryListPieObjectProperties(
		const FObjectPropertyListRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutResult.Reset();
		OutErrorCode.Reset();
		OutErrorMessage.Reset();

		const int32 MaxProperties = FMath::Clamp(Request.MaxProperties, 1, MaxListedObjectProperties);

		FResolvedPropertyTarget Resolved;
		const EFailure Failure = ResolvePiePropertyTarget(Request.Target, Resolved, OutErrorMessage);
		if (Failure != EFailure::None)
		{
			OutErrorCode = FailureCode(Failure);
			return false;
		}
		if (Resolved.Target == nullptr || Resolved.Target->GetClass() == nullptr)
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("The resolved PIE object has no reflected class.");
			return false;
		}

		TArray<const FProperty*> Matched;
		int32 TotalCount = 0;
		for (TFieldIterator<FProperty> It(Resolved.Target->GetClass()); It; ++It)
		{
			const FProperty* Property = *It;
			if (Property == nullptr)
			{
				continue;
			}
			++TotalCount;
			if (!Request.NameContains.IsEmpty()
				&& !Property->GetName().Contains(Request.NameContains, ESearchCase::IgnoreCase))
			{
				continue;
			}
			Matched.Add(Property);
		}

		// Deterministic order: declared name, ordinal.  Reflection iteration
		// order alone is an engine implementation detail.
		Matched.Sort([](const FProperty& Left, const FProperty& Right)
		{
			const FString LeftName = Left.GetName();
			const FString RightName = Right.GetName();
			const int32 NameOrder = LeftName.Compare(RightName, ESearchCase::CaseSensitive);
			if (NameOrder != 0)
			{
				return NameOrder < 0;
			}
			return false;
		});

		const int32 MatchedCount = Matched.Num();
		const int32 ReturnedCount = FMath::Min(MatchedCount, MaxProperties);
		TArray<TSharedPtr<FJsonValue>> Properties;
		for (int32 Index = 0; Index < ReturnedCount; ++Index)
		{
			Properties.Add(MakeShared<FJsonValueObject>(DescribePropertyDescriptor(Matched[Index])));
		}

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		WriteTargetEnvelope(Resolved, Result);
		Result->SetStringField(TEXT("nameContains"), Request.NameContains);
		Result->SetNumberField(TEXT("totalCount"), TotalCount);
		Result->SetNumberField(TEXT("matchedCount"), MatchedCount);
		Result->SetNumberField(TEXT("returnedCount"), Properties.Num());
		Result->SetNumberField(TEXT("maxProperties"), MaxProperties);
		Result->SetBoolField(TEXT("truncated"), ReturnedCount < MatchedCount);
		Result->SetArrayField(TEXT("properties"), Properties);

		OutResult = Result;
		return true;
	}

	bool TryReadPieObjectProperties(
		const FObjectPropertyReadRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutResult.Reset();
		OutErrorCode.Reset();
		OutErrorMessage.Reset();

		auto Fail = [&OutErrorCode, &OutErrorMessage](const EFailure Failure, const FString& Message)
		{
			OutErrorCode = FailureCode(Failure);
			OutErrorMessage = Message;
			return false;
		};

		if (Request.PropertyNames.Num() <= 0)
		{
			return Fail(
				EFailure::InvalidRequest,
				FString::Printf(
					TEXT("propertyNames must name between 1 and %d reflected properties."),
					MaxReadObjectProperties));
		}
		TArray<FString> UniqueNames;
		TArray<FString> DuplicateNames;
		TSet<FString> SeenNames;
		for (const FString& Name : Request.PropertyNames)
		{
			if (!IsPlainPropertyIdentifier(Name))
			{
				return Fail(
					EFailure::InvalidRequest,
					FString::Printf(
						TEXT("Property name '%s' is not a plain identifier; dotted paths, indices and non-reflected shapes are rejected."),
						*Name.Left(MaxReadPropertyNameChars)));
			}
			if (SeenNames.Contains(Name))
			{
				DuplicateNames.AddUnique(Name);
				continue;
			}
			SeenNames.Add(Name);
			UniqueNames.Add(Name);
		}
		if (UniqueNames.Num() > MaxReadObjectProperties)
		{
			return Fail(
				EFailure::InvalidRequest,
				FString::Printf(
					TEXT("propertyNames must contain at most %d distinct names after de-duplication; the request has %d."),
					MaxReadObjectProperties,
					UniqueNames.Num()));
		}
		if (Request.Offset < 0)
		{
			return Fail(EFailure::InvalidRequest, TEXT("offset must be zero or a positive integer."));
		}
		if (Request.Limit < 1 || Request.Limit > MaxContainerPageLimit)
		{
			return Fail(
				EFailure::InvalidRequest,
				FString::Printf(
					TEXT("limit must be from 1 through %d container elements."),
					MaxContainerPageLimit));
		}
		const int32 MaxStringChars = FMath::Clamp(
			Request.MaxStringChars,
			MinPropertyStringChars,
			MaxPropertyStringChars);
		const int32 Limit = FMath::Clamp(Request.Limit, 1, MaxContainerPageLimit);

		FResolvedPropertyTarget Resolved;
		const EFailure Failure = ResolvePiePropertyTarget(Request.Target, Resolved, OutErrorMessage);
		if (Failure != EFailure::None)
		{
			OutErrorCode = FailureCode(Failure);
			return false;
		}
		if (Resolved.Target == nullptr || Resolved.Target->GetClass() == nullptr)
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("The resolved PIE object has no reflected class.");
			return false;
		}

		FWriteContext Ctx;
		Ctx.MaxStringChars = MaxStringChars;
		Ctx.Offset = Request.Offset;
		Ctx.Limit = Limit;
		Ctx.AllContexts = &Resolved.AllContexts;

		TArray<TSharedPtr<FJsonValue>> Items;
		for (const FString& Name : UniqueNames)
		{
			const FProperty* Property =
				Resolved.Target->GetClass()->FindPropertyByName(FName(*Name));
			TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
			Entry->SetStringField(TEXT("name"), Name);

			if (Property == nullptr)
			{
				Entry->SetStringField(TEXT("type"), FString());
				Entry->SetStringField(TEXT("typeKind"), PropertyKindName(EPropertyKind::Unknown));
				Entry->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
				Entry->SetStringField(TEXT("reason"), TEXT("property-not-found"));
				Entry->SetBoolField(TEXT("valueReturned"), false);
				Items.Add(MakeShared<FJsonValueObject>(Entry));
				continue;
			}

			const EPropertyKind Kind = ClassifyProperty(Property);
			Entry->SetStringField(TEXT("type"), DeclaredTypeOf(Property));
			Entry->SetStringField(TEXT("typeKind"), PropertyKindName(Kind));
			Entry->SetBoolField(TEXT("isArray"), Kind == EPropertyKind::Array);
			Entry->SetBoolField(TEXT("isMap"), Kind == EPropertyKind::Map);
			Entry->SetBoolField(TEXT("isSet"), Kind == EPropertyKind::Set);
			Entry->SetBoolField(TEXT("isStruct"), Kind == EPropertyKind::Struct);
			Entry->SetBoolField(TEXT("isObjectRef"), IsObjectRefKind(Kind));
			AddDeclaringClass(Property, Entry);

			const FPropertySupport Support = EvaluateSupportInternal(Property, 0);
			Entry->SetBoolField(TEXT("supported"), Support.bSupported);
			if (!Support.bSupported)
			{
				Entry->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::UnsupportedType));
				Entry->SetStringField(TEXT("reason"), Support.Reason);
				Entry->SetBoolField(TEXT("valueReturned"), false);
				Items.Add(MakeShared<FJsonValueObject>(Entry));
				continue;
			}

			const void* ValuePtr = Property->ContainerPtrToValuePtr<void>(Resolved.Target);
			if (ValuePtr == nullptr)
			{
				Entry->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
				Entry->SetStringField(TEXT("reason"), TEXT("unreadable-value"));
				Entry->SetBoolField(TEXT("valueReturned"), false);
				Items.Add(MakeShared<FJsonValueObject>(Entry));
				continue;
			}

			FString ValueReason;
			if (!WriteValueInto(Property, ValuePtr, 0, Ctx, Entry, ValueReason))
			{
				Entry->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Unknown));
				Entry->SetStringField(TEXT("reason"), ValueReason);
				Entry->SetBoolField(TEXT("valueReturned"), false);
			}
			else
			{
				Entry->SetStringField(TEXT("status"), PropertyValueStatusCode(EPropertyValueStatus::Ok));
				Entry->SetBoolField(TEXT("valueReturned"), true);
			}
			Items.Add(MakeShared<FJsonValueObject>(Entry));
		}

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		WriteTargetEnvelope(Resolved, Result);
		Result->SetNumberField(TEXT("offset"), Request.Offset);
		Result->SetNumberField(TEXT("limit"), Limit);
		Result->SetNumberField(TEXT("maxStringChars"), MaxStringChars);
		Result->SetNumberField(TEXT("requestedCount"), Request.PropertyNames.Num());
		Result->SetNumberField(TEXT("distinctRequestedCount"), UniqueNames.Num());
		Result->SetNumberField(TEXT("returnedCount"), Items.Num());
		TArray<TSharedPtr<FJsonValue>> DuplicateValues;
		for (const FString& DuplicateName : DuplicateNames)
		{
			DuplicateValues.Add(MakeShared<FJsonValueString>(DuplicateName));
		}
		Result->SetArrayField(TEXT("duplicateNames"), DuplicateValues);
		Result->SetNumberField(TEXT("remainingItemBudget"), Ctx.RemainingItems);
		Result->SetNumberField(TEXT("remainingCharBudget"), Ctx.RemainingChars);
		Result->SetArrayField(TEXT("properties"), Items);

		OutResult = Result;
		return true;
	}
}

namespace
{
	using namespace UEAgentKitPieRuntime;

	/** Shared parameter parsing for the two P2 property methods. */
	bool ParsePropertyTarget(
		const TSharedPtr<FJsonObject>& Params,
		const TCHAR* MethodName,
		FPropertyTargetRequest& OutRequest,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		if (!Params.IsValid())
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = FString::Printf(
				TEXT("%s requires an exact pieInstanceId, one Actor identity and optional Component identity."),
				MethodName);
			return false;
		}

		double PieInstanceId = 0.0;
		if (!Params->TryGetNumberField(TEXT("pieInstanceId"), PieInstanceId))
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("pieInstanceId must be the exact PIE instance id reported by editor.getPieWorldContexts (0 is the primary instance).");
			return false;
		}
		OutRequest.Actor.PieInstanceId = static_cast<int32>(PieInstanceId);
		Params->TryGetStringField(TEXT("worldPath"), OutRequest.Actor.WorldPath);
		Params->TryGetStringField(TEXT("actorPath"), OutRequest.Actor.ActorPath);
		Params->TryGetStringField(TEXT("actorGuid"), OutRequest.Actor.ActorGuid);
		Params->TryGetStringField(TEXT("actorName"), OutRequest.Actor.ActorName);
		Params->TryGetStringField(TEXT("actorLabel"), OutRequest.Actor.ActorLabel);
		Params->TryGetStringField(TEXT("actorClassPath"), OutRequest.Actor.ActorClassPath);
		Params->TryGetStringField(TEXT("componentPath"), OutRequest.ComponentPath);
		Params->TryGetStringField(TEXT("componentName"), OutRequest.ComponentName);
		Params->TryGetStringField(TEXT("componentClassPath"), OutRequest.ComponentClassPath);
		return true;
	}
}

bool FUEAgentKitEditorBridge::TryListPieObjectPropertiesResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	using namespace UEAgentKitPieRuntime;

	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();

	FObjectPropertyListRequest Request;
	if (!ParsePropertyTarget(
		Params,
		TEXT("editor.listPieObjectProperties"),
		Request.Target,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}
	Params->TryGetStringField(TEXT("nameContains"), Request.NameContains);
	double MaxProperties = static_cast<double>(DefaultListedObjectProperties);
	if (Params->TryGetNumberField(TEXT("maxProperties"), MaxProperties))
	{
		if (FMath::FloorToDouble(MaxProperties) != MaxProperties)
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("maxProperties must be an integer.");
			return false;
		}
		Request.MaxProperties = static_cast<int32>(MaxProperties);
	}
	return TryListPieObjectProperties(Request, OutResult, OutErrorCode, OutErrorMessage);
}

bool FUEAgentKitEditorBridge::TryReadPieObjectPropertiesResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	using namespace UEAgentKitPieRuntime;

	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();

	FObjectPropertyReadRequest Request;
	if (!ParsePropertyTarget(
		Params,
		TEXT("editor.readPieObjectProperties"),
		Request.Target,
		OutErrorCode,
		OutErrorMessage))
	{
		return false;
	}

	const TArray<TSharedPtr<FJsonValue>>* NameValues = nullptr;
	if (!Params->TryGetArrayField(TEXT("propertyNames"), NameValues) || NameValues == nullptr)
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = FString::Printf(
			TEXT("propertyNames must be an explicit array of 1 to %d reflected property identifiers."),
			MaxReadObjectProperties);
		return false;
	}
	for (const TSharedPtr<FJsonValue>& NameValue : *NameValues)
	{
		FString Name;
		if (!NameValue.IsValid() || !NameValue->TryGetString(Name))
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("Every propertyNames entry must be a string.");
			return false;
		}
		Request.PropertyNames.Add(Name);
	}

	double NumberValue = 0.0;
	if (Params->TryGetNumberField(TEXT("offset"), NumberValue))
	{
		if (FMath::FloorToDouble(NumberValue) != NumberValue)
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("offset must be an integer.");
			return false;
		}
		Request.Offset = static_cast<int32>(NumberValue);
	}
	NumberValue = static_cast<double>(DefaultContainerPageLimit);
	if (Params->TryGetNumberField(TEXT("limit"), NumberValue))
	{
		if (FMath::FloorToDouble(NumberValue) != NumberValue)
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("limit must be an integer.");
			return false;
		}
		Request.Limit = static_cast<int32>(NumberValue);
	}
	NumberValue = static_cast<double>(DefaultPropertyStringChars);
	if (Params->TryGetNumberField(TEXT("maxStringChars"), NumberValue))
	{
		Request.MaxStringChars = static_cast<int32>(NumberValue);
	}
	return TryReadPieObjectProperties(Request, OutResult, OutErrorCode, OutErrorMessage);
}
