// Disposable acceptance helper: open one cloned Blueprint and focus its Event
// Graph so the normal Editor Bridge preview/apply/save methods can run against a
// real Slate Blueprint Editor. This test never writes a layout or a package.

#include "BlueprintGraphLayoutApply.h"

#include "BlueprintEditor.h"
#include "EdGraph/EdGraph.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Misc/AutomationTest.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitLayoutRealReloadFocus
{
	const TCHAR* AssetPath = TEXT("/Game/NodeCoverage/BP_NodeCoverage.BP_NodeCoverage");

	FString GuidLower(const FGuid& Guid)
	{
		return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
	}

	bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
		{
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		return FFileHelper::SaveStringToFile(
			Text,
			*Path,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	FBlueprintEditor* OpenAndFocus(UBlueprint* Blueprint, UEdGraph* Graph, FString& OutError)
	{
		if (GEditor == nullptr)
		{
			OutError = TEXT("GEditor is unavailable.");
			return nullptr;
		}
		UAssetEditorSubsystem* Subsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
		if (Subsystem == nullptr)
		{
			OutError = TEXT("The Asset Editor Subsystem is unavailable.");
			return nullptr;
		}
		if (!Subsystem->OpenEditorForAsset(
			Blueprint,
			EToolkitMode::Standalone,
			TSharedPtr<IToolkitHost>(),
			false))
		{
			OutError = TEXT("OpenEditorForAsset returned false.");
			return nullptr;
		}
		IAssetEditorInstance* Instance = Subsystem->FindEditorForAsset(Blueprint, false);
		if (Instance == nullptr || Instance->GetEditorName() != FName(TEXT("BlueprintEditor")))
		{
			OutError = TEXT("FindEditorForAsset did not return a BlueprintEditor.");
			return nullptr;
		}
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (Editor->GetBlueprintObj() != Blueprint)
		{
			OutError = TEXT("The Blueprint Editor does not own the fixture.");
			return nullptr;
		}
		if (!Editor->OpenGraphAndBringToFront(Graph, true).IsValid())
		{
			OutError = TEXT("OpenGraphAndBringToFront returned an invalid graph editor.");
			return nullptr;
		}
		if (Editor->GetFocusedGraph() != Graph)
		{
			OutError = TEXT("The fixture graph is not focused.");
			return nullptr;
		}
		return Editor;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitLayoutRealReloadFocusTest,
	"UEAgentKit.LayoutRealReload.Focus",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitLayoutRealReloadFocusTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitLayoutRealReloadFocus;

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, AssetPath);
	if (!TestNotNull(TEXT("reload fixture Blueprint loads"), Blueprint))
	{
		return false;
	}
	if (!TestFalse(TEXT("reload fixture package is clean"), Blueprint->GetOutermost()->IsDirty()))
	{
		return false;
	}

	UEdGraph* Graph = nullptr;
	for (UEdGraph* Candidate : Blueprint->FunctionGraphs)
	{
		if (Candidate != nullptr && Candidate->GetName() == TEXT("CoverageFunction"))
		{
			Graph = Candidate;
			break;
		}
	}
	if (Graph == nullptr)
	{
		AddError(TEXT("The reload fixture CoverageFunction graph was not found."));
		return false;
	}
	if (!TestNotNull(TEXT("reload fixture function graph loads"), Graph))
	{
		return false;
	}

	FString Error;
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
	if (!TestNotNull(TEXT("reload fixture Blueprint Editor opens"), Editor))
	{
		AddError(Error);
		return false;
	}
	if (!TestEqual(TEXT("reload fixture graph is focused"), Editor->GetFocusedGraph(), Graph))
	{
		return false;
	}

	TSharedPtr<FJsonObject> Preflight;
	FString Code;
	FString Message;
	if (!TestTrue(
		TEXT("reload fixture preflight is available"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(Blueprint, Graph, Preflight, Code, Message)))
	{
		AddError(Code + TEXT(": ") + Message);
		return false;
	}

	TSharedRef<FJsonObject> Focus = MakeShared<FJsonObject>();
	Focus->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layout-real-reload.focus/1.0"));
	Focus->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Focus->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Focus->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Focus->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Focus->SetStringField(TEXT("graphName"), Graph->GetName());
	Focus->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
	Focus->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
	Focus->SetNumberField(TEXT("nodeCount"), Preflight->GetNumberField(TEXT("nodeCount")));
	Focus->SetBoolField(TEXT("packageDirty"), false);
	Focus->SetBoolField(TEXT("focused"), true);

	const FString FocusPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("LayoutRealReload"),
		TEXT("focus.json"));
	if (!TestTrue(TEXT("reload fixture focus evidence is written"), WriteJson(FocusPath, Focus)))
	{
		return false;
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitLayoutLargeProjectionFocusTest,
	"UEAgentKit.LayoutLargeProjection.Focus",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitLayoutLargeProjectionFocusTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitLayoutRealReloadFocus;

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, AssetPath);
	if (!TestNotNull(TEXT("large projection fixture Blueprint loads"), Blueprint))
	{
		return false;
	}
	if (!TestFalse(TEXT("large projection fixture package is clean"), Blueprint->GetOutermost()->IsDirty()))
	{
		return false;
	}

	UEdGraph* Graph = nullptr;
	for (UEdGraph* Candidate : Blueprint->UbergraphPages)
	{
		if (Candidate != nullptr && Candidate->GetName() == TEXT("EventGraph"))
		{
			Graph = Candidate;
			break;
		}
	}
	if (!TestNotNull(TEXT("large projection EventGraph loads"), Graph))
	{
		return false;
	}
	if (!TestEqual(TEXT("large projection fixture has exactly 134 nodes"), Graph->Nodes.Num(), 134))
	{
		return false;
	}

	FString Error;
	FBlueprintEditor* Editor = OpenAndFocus(Blueprint, Graph, Error);
	if (!TestNotNull(TEXT("large projection Blueprint Editor opens"), Editor))
	{
		AddError(Error);
		return false;
	}

	TSharedPtr<FJsonObject> Preflight;
	FString Code;
	FString Message;
	if (!TestTrue(
		TEXT("large projection preflight is available"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(Blueprint, Graph, Preflight, Code, Message)))
	{
		AddError(Code + TEXT(": ") + Message);
		return false;
	}

	TSharedRef<FJsonObject> Focus = MakeShared<FJsonObject>();
	Focus->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layout-large-projection.focus/1.0"));
	Focus->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
	Focus->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
	Focus->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Focus->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Focus->SetStringField(TEXT("graphName"), Graph->GetName());
	Focus->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
	Focus->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
	Focus->SetNumberField(TEXT("nodeCount"), Preflight->GetNumberField(TEXT("nodeCount")));
	Focus->SetBoolField(TEXT("focused"), true);

	const FString FocusPath = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("LargeProjection"),
		TEXT("focus.json"));
	return TestTrue(
		TEXT("large projection focus evidence is written"),
		WriteJson(FocusPath, Focus));
}

#endif // WITH_DEV_AUTOMATION_TESTS
