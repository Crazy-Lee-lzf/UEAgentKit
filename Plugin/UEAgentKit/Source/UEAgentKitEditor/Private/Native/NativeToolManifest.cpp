// Copyright Epic Games, Inc. All Rights Reserved.

#include "Native/NativeToolManifest.h"

#include "Native/NativeCanonicalJson.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Internationalization/Regex.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	const TCHAR* const ManifestSchemaVersion = TEXT("ueak.native-registry/1.0");
	const TCHAR* const ManifestRelativePath = TEXT("Resources/NativeTools/native-tools.v1.json");
	const TCHAR* const BridgeMethodName = TEXT("native.invoke");

	bool IsSemver(const FString& Value)
	{
		TArray<FString> Parts;
		Value.ParseIntoArray(Parts, TEXT("."), false);
		if (Parts.Num() != 3)
		{
			return false;
		}
		for (const FString& Part : Parts)
		{
			if (Part.IsEmpty())
			{
				return false;
			}
			for (const TCHAR Character : Part)
			{
				if (Character < TEXT('0') || Character > TEXT('9'))
				{
					return false;
				}
			}
		}
		return true;
	}

	bool IsToolName(const FString& Value)
	{
		static const FRegexPattern Pattern(TEXT("^[a-z][a-z0-9]*(\\.[a-z][a-zA-Z0-9]*){2,4}$"));
		FRegexMatcher Matcher(Pattern, Value);
		return Matcher.FindNext() && Matcher.GetMatchBeginning() == 0 && Matcher.GetMatchEnding() == Value.Len();
	}

	bool IsKnownHandler(const FString& ToolId)
	{
		return ToolId == TEXT("graph.nodeRegistry.catalog")
			|| ToolId == TEXT("graph.nodeRegistry.fingerprint")
			|| ToolId == TEXT("graph.nodeRegistry.filterContext");
	}

	bool ReadRequiredString(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Field,
		FString& OutValue,
		FString& OutError)
	{
		if (!Object.IsValid() || !Object->TryGetStringField(Field, OutValue) || OutValue.IsEmpty())
		{
			OutError = FString::Printf(TEXT("The native manifest field '%s' is missing."), Field);
			return false;
		}
		return true;
	}

	bool ReadRequiredBool(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Field,
		bool& bOutValue,
		FString& OutError)
	{
		if (!Object.IsValid() || !Object->TryGetBoolField(Field, bOutValue))
		{
			OutError = FString::Printf(TEXT("The native manifest field '%s' is missing."), Field);
			return false;
		}
		return true;
	}

	bool ReadRequiredObject(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* Field,
		TSharedPtr<FJsonObject>& OutValue,
		FString& OutError)
	{
		const TSharedPtr<FJsonObject>* Found = nullptr;
		if (!Object.IsValid() || !Object->TryGetObjectField(Field, Found) || Found == nullptr || !Found->IsValid())
		{
			OutError = FString::Printf(TEXT("The native manifest field '%s' is missing."), Field);
			return false;
		}
		OutValue = *Found;
		return true;
	}
}

bool FNativeToolManifest::HasTool(const FString& ToolId) const
{
	return FindTool(ToolId) != nullptr;
}

const FNativeToolManifestEntry* FNativeToolManifest::FindTool(const FString& ToolId) const
{
	for (const FNativeToolManifestEntry& Entry : Tools)
	{
		if (Entry.Name == ToolId)
		{
			return &Entry;
		}
	}
	return nullptr;
}

FString FNativeToolManifestLoader::ResolveManifestPath()
{
	const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAgentKit"));
	if (!Plugin.IsValid())
	{
		return FString();
	}
	return FPaths::Combine(Plugin->GetBaseDir(), ManifestRelativePath);
}

bool FNativeToolManifestLoader::ComputeManifestHash(
	const TSharedPtr<FJsonObject>& Document,
	FString& OutHash,
	FString& OutError)
{
	FNativeCanonicalValueRef Canonical = FNativeCanonicalValue::MakeNull();
	if (!UEAgentKitCanonical::FromJsonObject(Document, Canonical, OutError))
	{
		return false;
	}
	return UEAgentKitCanonical::Fingerprint(Canonical, TEXT("ueak.native.registry/1"), 16, OutHash, OutError);
}

bool FNativeToolManifestLoader::ValidateTool(
	const TSharedPtr<FJsonObject>& Tool,
	FNativeToolManifestEntry& OutEntry,
	FString& OutError)
{
	if (!Tool.IsValid())
	{
		OutError = TEXT("A native manifest tool entry is not an object.");
		return false;
	}
	if (!ReadRequiredString(Tool, TEXT("name"), OutEntry.Name, OutError)
		|| !ReadRequiredString(Tool, TEXT("version"), OutEntry.Version, OutError)
		|| !ReadRequiredString(Tool, TEXT("domain"), OutEntry.Domain, OutError)
		|| !ReadRequiredString(Tool, TEXT("titleKey"), OutEntry.TitleKey, OutError)
		|| !ReadRequiredString(Tool, TEXT("risk"), OutEntry.Risk, OutError)
		|| !ReadRequiredBool(Tool, TEXT("readOnly"), OutEntry.bReadOnly, OutError))
	{
		return false;
	}
	if (!IsToolName(OutEntry.Name))
	{
		OutError = FString::Printf(TEXT("The native tool name '%s' is not contract-shaped."), *OutEntry.Name);
		return false;
	}
	if (!IsSemver(OutEntry.Version))
	{
		OutError = FString::Printf(TEXT("The native tool '%s' has a non-semver version."), *OutEntry.Name);
		return false;
	}
	if (!IsKnownHandler(OutEntry.Name))
	{
		OutError = FString::Printf(TEXT("The native tool '%s' has no registered handler."), *OutEntry.Name);
		return false;
	}

	TSharedPtr<FJsonObject> Requires;
	if (!ReadRequiredObject(Tool, TEXT("requires"), Requires, OutError)
		|| !ReadRequiredBool(Requires, TEXT("transaction"), OutEntry.bRequiresTransaction, OutError)
		|| !ReadRequiredBool(Requires, TEXT("save"), OutEntry.bRequiresSave, OutError))
	{
		return false;
	}
	FString RevisionScope;
	if (!ReadRequiredString(Requires, TEXT("revisionScope"), RevisionScope, OutError))
	{
		return false;
	}

	TSharedPtr<FJsonObject> Transaction;
	if (!ReadRequiredObject(Tool, TEXT("transaction"), Transaction, OutError))
	{
		return false;
	}
	FString TransactionKind;
	if (!ReadRequiredString(Transaction, TEXT("kind"), TransactionKind, OutError))
	{
		return false;
	}
	if (OutEntry.bReadOnly)
	{
		if (OutEntry.bRequiresTransaction || OutEntry.bRequiresSave || TransactionKind != TEXT("none"))
		{
			OutError = FString::Printf(TEXT("The read-only native tool '%s' declares a write path."), *OutEntry.Name);
			return false;
		}
	}

	TSharedPtr<FJsonObject> Threading;
	if (!ReadRequiredObject(Tool, TEXT("threading"), Threading, OutError)
		|| !ReadRequiredString(Threading, TEXT("execution"), OutEntry.ThreadingExecution, OutError))
	{
		return false;
	}
	if (OutEntry.ThreadingExecution != TEXT("game"))
	{
		OutError = FString::Printf(TEXT("The native tool '%s' is not game-thread bound."), *OutEntry.Name);
		return false;
	}

	TSharedPtr<FJsonObject> Dto;
	if (!ReadRequiredObject(Tool, TEXT("dto"), Dto, OutError))
	{
		return false;
	}
	int32 MaxRequestBytes = 0;
	int32 MaxResponseBytes = 0;
	if (!Dto->TryGetNumberField(TEXT("maxRequestBytes"), MaxRequestBytes)
		|| !Dto->TryGetNumberField(TEXT("maxResponseBytes"), MaxResponseBytes))
	{
		OutError = FString::Printf(TEXT("The native tool '%s' has no DTO size limits."), *OutEntry.Name);
		return false;
	}
	if (MaxRequestBytes <= 0 || MaxRequestBytes > 65536 || MaxResponseBytes <= 0 || MaxResponseBytes > 1048576)
	{
		OutError = FString::Printf(TEXT("The native tool '%s' exceeds the bridge DTO limits."), *OutEntry.Name);
		return false;
	}
	OutEntry.MaxRequestBytes = MaxRequestBytes;
	OutEntry.MaxResponseBytes = MaxResponseBytes;
	// R1.5 subset: DTO schema ids are declared but resolved by N1, not by this slice.

	TSharedPtr<FJsonObject> Gating;
	if (!ReadRequiredObject(Tool, TEXT("gating"), Gating, OutError)
		|| !ReadRequiredString(Gating, TEXT("maturity"), OutEntry.Maturity, OutError))
	{
		return false;
	}
	if (OutEntry.Maturity != TEXT("stable") && OutEntry.Maturity != TEXT("experimental")
		&& OutEntry.Maturity != TEXT("test-only") && OutEntry.Maturity != TEXT("deprecated"))
	{
		OutError = FString::Printf(TEXT("The native tool '%s' has an unknown maturity."), *OutEntry.Name);
		return false;
	}
	if (OutEntry.Maturity == TEXT("test-only"))
	{
		if (!Gating->TryGetStringField(TEXT("gateProfile"), OutEntry.GateProfile) || OutEntry.GateProfile.IsEmpty())
		{
			OutError = FString::Printf(TEXT("The test-only native tool '%s' has no gate profile."), *OutEntry.Name);
			return false;
		}
	}

	TSharedPtr<FJsonObject> Exposure;
	if (!ReadRequiredObject(Tool, TEXT("exposure"), Exposure, OutError))
	{
		return false;
	}
	const TSharedPtr<FJsonObject>* Bridge = nullptr;
	if (Exposure->TryGetObjectField(TEXT("bridge"), Bridge) && Bridge != nullptr && Bridge->IsValid())
	{
		if (!ReadRequiredString(*Bridge, TEXT("method"), OutEntry.BridgeMethod, OutError))
		{
			return false;
		}
		if (OutEntry.BridgeMethod != BridgeMethodName)
		{
			OutError = FString::Printf(TEXT("The native tool '%s' uses an unsupported bridge method."), *OutEntry.Name);
			return false;
		}
		const TArray<TSharedPtr<FJsonValue>>* Aliases = nullptr;
		if ((*Bridge)->TryGetArrayField(TEXT("aliases"), Aliases) && Aliases != nullptr)
		{
			for (const TSharedPtr<FJsonValue>& Alias : *Aliases)
			{
				if (!Alias.IsValid() || Alias->Type != EJson::String || Alias->AsString().IsEmpty())
				{
					OutError = FString::Printf(TEXT("The native tool '%s' has an invalid bridge alias."), *OutEntry.Name);
					return false;
				}
				OutEntry.BridgeAliases.Add(Alias->AsString());
			}
		}
	}
	bool bHasSurface = OutEntry.BridgeMethod == BridgeMethodName;
	for (const TCHAR* Surface : { TEXT("mcp"), TEXT("toolMenu"), TEXT("cli") })
	{
		const TSharedPtr<FJsonObject>* SurfaceObject = nullptr;
		if (Exposure->TryGetObjectField(Surface, SurfaceObject) && SurfaceObject != nullptr && SurfaceObject->IsValid())
		{
			bHasSurface = true;
		}
	}
	if (!bHasSurface)
	{
		OutError = FString::Printf(TEXT("The native tool '%s' exposes no surface."), *OutEntry.Name);
		return false;
	}

	const TArray<TSharedPtr<FJsonValue>>* Capabilities = nullptr;
	if (!Tool->TryGetArrayField(TEXT("capabilities"), Capabilities) || Capabilities == nullptr || Capabilities->Num() == 0)
	{
		OutError = FString::Printf(TEXT("The native tool '%s' declares no capabilities."), *OutEntry.Name);
		return false;
	}
	for (const TSharedPtr<FJsonValue>& Capability : *Capabilities)
	{
		if (!Capability.IsValid() || Capability->Type != EJson::String)
		{
			OutError = FString::Printf(TEXT("The native tool '%s' has a non-string capability."), *OutEntry.Name);
			return false;
		}
		OutEntry.Capabilities.Add(Capability->AsString());
	}

	if (!Tool->TryGetBoolField(TEXT("deprecated"), OutEntry.bDeprecated))
	{
		OutEntry.bDeprecated = false;
	}
	if (OutEntry.bDeprecated && (!Tool->TryGetStringField(TEXT("replacement"), OutEntry.Replacement) || OutEntry.Replacement.IsEmpty()))
	{
		OutError = FString::Printf(TEXT("The deprecated native tool '%s' has no replacement."), *OutEntry.Name);
		return false;
	}
	return true;
}

bool FNativeToolManifestLoader::Load(FNativeToolManifest& OutManifest)
{
	OutManifest = FNativeToolManifest();

	const FString ManifestPath = ResolveManifestPath();
	if (ManifestPath.IsEmpty())
	{
		OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
		OutManifest.ErrorMessage = TEXT("The UEAgentKit plugin base directory cannot be resolved.");
		return false;
	}

	FString ManifestText;
	if (!FFileHelper::LoadFileToString(ManifestText, *ManifestPath))
	{
		OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
		OutManifest.ErrorMessage = TEXT("The native tool manifest is missing.");
		return false;
	}

	TSharedPtr<FJsonObject> Document;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ManifestText);
	if (!FJsonSerializer::Deserialize(Reader, Document) || !Document.IsValid())
	{
		OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
		OutManifest.ErrorMessage = TEXT("The native tool manifest is not valid JSON.");
		return false;
	}

	FString Error;
	if (!ReadRequiredString(Document, TEXT("schemaVersion"), OutManifest.SchemaVersion, Error)
		|| OutManifest.SchemaVersion != ManifestSchemaVersion)
	{
		OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
		OutManifest.ErrorMessage = TEXT("The native tool manifest schemaVersion is unsupported.");
		return false;
	}
	if (!ReadRequiredString(Document, TEXT("registryVersion"), OutManifest.RegistryVersion, Error)
		|| !IsSemver(OutManifest.RegistryVersion))
	{
		OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
		OutManifest.ErrorMessage = TEXT("The native tool manifest registryVersion is invalid.");
		return false;
	}

	const TArray<TSharedPtr<FJsonValue>>* Tools = nullptr;
	if (!Document->TryGetArrayField(TEXT("tools"), Tools) || Tools == nullptr || Tools->Num() == 0)
	{
		OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
		OutManifest.ErrorMessage = TEXT("The native tool manifest declares no tools.");
		return false;
	}

	TSet<FString> SeenNames;
	for (const TSharedPtr<FJsonValue>& ToolValue : *Tools)
	{
		if (!ToolValue.IsValid() || ToolValue->Type != EJson::Object)
		{
			OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
			OutManifest.ErrorMessage = TEXT("A native tool entry is not an object.");
			return false;
		}
		FNativeToolManifestEntry Entry;
		if (!ValidateTool(ToolValue->AsObject(), Entry, Error))
		{
			OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
			OutManifest.ErrorMessage = Error;
			return false;
		}
		if (SeenNames.Contains(Entry.Name))
		{
			OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
			OutManifest.ErrorMessage = FString::Printf(TEXT("The native tool '%s' is declared twice."), *Entry.Name);
			return false;
		}
		SeenNames.Add(Entry.Name);
		OutManifest.Tools.Add(MoveTemp(Entry));
	}

	if (!ComputeManifestHash(Document, OutManifest.Hash, Error))
	{
		OutManifest.ErrorCode = TEXT("native-registry-invalid-manifest");
		OutManifest.ErrorMessage = FString::Printf(TEXT("The native tool manifest cannot be hashed: %s"), *Error);
		return false;
	}

	OutManifest.bValid = true;
	return true;
}
