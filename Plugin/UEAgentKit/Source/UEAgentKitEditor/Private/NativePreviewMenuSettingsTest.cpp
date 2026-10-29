// M3.1 runtime test for the test-only startup override. The test observes the
// immutable startup settings and writes a machine-readable record; it never
// mutates production config files.

#include "CoreMinimal.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/App.h"
#include "Misc/AutomationTest.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Paths.h"
#include "NativePreviewMenuSettings.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

#if WITH_DEV_AUTOMATION_TESTS

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeM3SettingsTest,
	"UEAgentKit.Native.M3Settings",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeM3SettingsTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	const FUEAgentKitNativePreviewSettings Before = FUEAgentKitNativePreviewSettings::Get();
	// Prove the startup snapshot is immutable even if GConfig is touched later.
	GConfig->SetBool(TEXT("UEAgentKit"), TEXT("NativeLayoutPreview"), true, GEngineIni);
	const FUEAgentKitNativePreviewSettings& After = FUEAgentKitNativePreviewSettings::Get();
	TestEqual(TEXT("M3 flag is read once"), After.bNativeLayoutPreview, Before.bNativeLayoutPreview);
	TestEqual(
		TEXT("M3 fallback flag is read once"),
		After.bLegacyLayoutPreviewFallback,
		Before.bLegacyLayoutPreviewFallback);
	TestEqual(
		TEXT("M3 override marker is read once"),
		After.bTestOverrideApplied,
		Before.bTestOverrideApplied);

	TSharedPtr<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m3.settings/1.0"));
	Evidence->SetStringField(TEXT("project"), FApp::GetProjectName());
	Evidence->SetBoolField(TEXT("nativeLayoutPreview"), Before.bNativeLayoutPreview);
	Evidence->SetBoolField(TEXT("legacyLayoutPreviewFallback"), Before.bLegacyLayoutPreviewFallback);
	Evidence->SetBoolField(TEXT("nativeToolRegistry"), Before.bNativeToolRegistry);
	Evidence->SetBoolField(TEXT("testOverrideApplied"), Before.bTestOverrideApplied);
	Evidence->SetBoolField(TEXT("flagReadOnce"), After.bNativeLayoutPreview == Before.bNativeLayoutPreview);
	FString Text;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
	if (!FJsonSerializer::Serialize(Evidence.ToSharedRef(), Writer))
	{
		AddError(TEXT("Could not serialize M3 settings evidence."));
		return false;
	}
	const FString Path = FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), TEXT("m3-settings.json"));
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
	if (!FFileHelper::SaveStringToFile(
			Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
	{
		AddError(FString::Printf(TEXT("Could not write %s."), *Path));
		return false;
	}
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
