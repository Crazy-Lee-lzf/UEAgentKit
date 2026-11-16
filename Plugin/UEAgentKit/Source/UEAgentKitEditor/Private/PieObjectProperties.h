#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "PieRuntimeInspection.h"

class FProperty;
class UActorComponent;
class UObject;

/**
 * P2: generic reflective UObject property reading inside one exact PIE world.
 *
 * Two questions are answered separately and honestly:
 *
 *  1. listPieObjectProperties  - which reflected properties does this object
 *     have, and can this tool read each of them at all (type level)?
 *  2. readPieObjectProperties  - what are the actual values of the properties
 *     the caller named?
 *
 * Contract rules that shape this code:
 *
 *  - Reflection driven.  `CPF_Edit` / `CPF_BlueprintVisible` are NOT a gate:
 *    any property that can be read safely through UE reflection is readable.
 *  - Refusal is explicit.  A type the tool cannot decode is reported as
 *    `unsupported-type` with a stable reason and `valueReturned=false`.
 *    No value is ever synthesized, approximated or default-initialised.
 *  - Object references stop at identity.  A referenced UObject contributes a
 *    path, a class and its PIE world identity; its properties are never
 *    traversed (no recursion, no lazy load, no package load).
 *  - Containers are paged.  `offset` / `limit` select one window of elements
 *    and `totalCount` / `hasMore` make completeness explicit.
 *  - Read only.  Nothing here opens a transaction, mutates an object, compiles
 *    a Blueprint, saves a package or opens the SQLite index.
 */
namespace UEAgentKitPieRuntime
{
	inline constexpr int32 MaxReadObjectProperties = 16;
	inline constexpr int32 MaxReadPropertyNameChars = 128;
	inline constexpr int32 DefaultListedObjectProperties = 128;
	inline constexpr int32 MaxListedObjectProperties = 512;
	inline constexpr int32 DefaultContainerPageLimit = 50;
	inline constexpr int32 MaxContainerPageLimit = 200;
	inline constexpr int32 MinPropertyStringChars = 32;
	inline constexpr int32 DefaultPropertyStringChars = 256;
	inline constexpr int32 MaxPropertyStringChars = 512;
	inline constexpr int32 MaxStructDepth = 4;
	inline constexpr int32 MaxStructMembersPerStruct = 64;
	inline constexpr int32 MaxReferencedObjectPathChars = 512;
	/** Bounded outer-chain walk when deriving the world of an arbitrary object. */
	inline constexpr int32 MaxOuterChainSteps = 32;
	inline constexpr int32 MaxDeclaredTypeChars = 128;
	/** Whole-response budgets; exceeding them downgrades, never truncates silently. */
	inline constexpr int32 MaxReturnedContainerItems = 512;
	inline constexpr int32 MaxReturnedPropertyChars = 256 * 1024;

	/**
	 * Normalized reflection kind.  This is the closed vocabulary reported as
	 * `typeKind`; it is what a caller should branch on instead of parsing the
	 * declared C++ type text.
	 */
	enum class EPropertyKind : uint8
	{
		Bool,
		Int8,
		Int16,
		Int32,
		Int64,
		UInt8,
		UInt16,
		UInt32,
		UInt64,
		Float,
		Double,
		Name,
		String,
		Enum,
		Struct,
		Array,
		Set,
		Map,
		Object,
		Class,
		SoftObject,
		SoftClass,
		WeakObject,
		/** Resolving a lazy object would force a package load, so it is refused. */
		LazyObject,
		Text,
		Delegate,
		MulticastDelegate,
		Interface,
		FieldPath,
		/** A `uint8 foo : 1` style bitfield member. */
		Bitfield,
		Unknown,
	};

	/** Stable `typeKind` wire name; never localized, never renamed. */
	const TCHAR* PropertyKindName(EPropertyKind Kind);

	/** Pure reflection classification of one property; no object instance needed. */
	EPropertyKind ClassifyProperty(const FProperty* Property);

	/** Per-property value status. `not-exposed` is part of the family vocabulary. */
	enum class EPropertyValueStatus : uint8
	{
		Ok,
		UnsupportedType,
		Unknown,
		NotExposed,
	};

	const TCHAR* PropertyValueStatusCode(EPropertyValueStatus Status);

	/** Type-level verdict for one property: can this tool decode it at all? */
	struct FPropertySupport
	{
		bool bSupported = false;
		FString Reason;
	};

	/**
	 * Structural support verdict, depth aware.
	 *
	 * A struct is supported when at least one member is readable; a container
	 * is supported when its element (or key/value) type is readable and is not
	 * itself a container.  Nothing is guessed: unsupported shapes come back
	 * with a stable reason code.
	 */
	FPropertySupport EvaluatePropertySupport(const FProperty* Property);

	/** One descriptor row: name, declared type, kind flags and support verdict. */
	TSharedRef<FJsonObject> DescribePropertyDescriptor(const FProperty* Property);

	/** Target of a property request: one exact PIE Actor plus an optional owned Component. */
	struct FPropertyTargetRequest
	{
		FActorInspectionRequest Actor;
		/**
		 * P3: any UObject inside the exact PIE world, addressed by its full object
		 * path.  Mutually exclusive with every Actor and Component identity, so a
		 * caller always states exactly one target.
		 */
		FString ObjectPath;
		/** Optional owned Component identity: at most one of path or name. */
		FString ComponentPath;
		FString ComponentName;
		/** Optional expected Component class path; a mismatch is stale. */
		FString ComponentClassPath;
	};

	/**
	 * Fully resolved read target.  `AllContexts` is kept so a referenced object
	 * can be placed in its own PIE world (or explicitly reported as not being
	 * part of one) without re-scanning.
	 */
	struct FResolvedPropertyTarget
	{
		FWorldContextSnapshot Snapshot;
		AActor* Actor = nullptr;
		UObject* Target = nullptr;
		bool bIsComponent = false;
		TArray<FWorldContextSnapshot> AllContexts;
	};

	/**
	 * Resolve the exact object behind a property request: one Actor (optionally
	 * narrowed to one owned Component), or - in P3 - any UObject inside the exact
	 * PIE world addressed by its full object path.
	 *
	 * Everything shares the P1 world/Actor resolver and the shared Component
	 * resolver, so identity, ambiguity, cross-world and stale handling stay one
	 * implementation instead of one per hop.
	 */
	EFailure ResolvePiePropertyTarget(
		const FPropertyTargetRequest& Request,
		FResolvedPropertyTarget& OutResolved,
		FString& OutErrorMessage);

	struct FObjectPropertyListRequest
	{
		FPropertyTargetRequest Target;
		/** Case-insensitive substring filter over the reflected property name. */
		FString NameContains;
		int32 MaxProperties = DefaultListedObjectProperties;
	};

	/**
	 * Bounded property discovery for one exact PIE object.
	 *
	 * Every reflected property on the object's class is considered, in a
	 * deterministic order (declared name, ordinal), regardless of whether it is
	 * Edit visible, Blueprint visible or native-private.
	 */
	bool TryListPieObjectProperties(
		const FObjectPropertyListRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	struct FObjectPropertyReadRequest
	{
		FPropertyTargetRequest Target;
		/** Explicit property names; duplicates are collapsed before validation. */
		TArray<FString> PropertyNames;
		int32 Offset = 0;
		int32 Limit = DefaultContainerPageLimit;
		int32 MaxStringChars = DefaultPropertyStringChars;
	};

	/**
	 * Bounded, read-only value read for one exact PIE object.
	 *
	 * Returned values are real values or explicit refusals.  Containers are
	 * paged by `offset` / `limit`; object references return identity only.
	 */
	bool TryReadPieObjectProperties(
		const FObjectPropertyReadRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage);

	/**
	 * Resolve one owned Component of `Actor` by exact path or exact name.
	 * Shared by `editor.getPieScalars` (P1) and the P2 property readers so
	 * Component identity, ambiguity and stale reporting stay one behaviour.
	 */
	EFailure ResolvePieComponentExact(
		AActor* Actor,
		int32 PieInstanceId,
		const FString& ComponentPath,
		const FString& ComponentName,
		const FString& ComponentClassPath,
		UActorComponent*& OutComponent,
		FString& OutErrorMessage);
}
