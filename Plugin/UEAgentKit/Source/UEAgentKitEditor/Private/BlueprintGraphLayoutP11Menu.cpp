#include "BlueprintGraphLayoutP11Menu.h"

#include "EditorBridge.h"

#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Framework/MultiBox/MultiBoxBuilder.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/MessageDialog.h"
#include "Misc/Paths.h"
#include "IPythonScriptPlugin.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "ToolMenu.h"
#include "ToolMenuDelegates.h"
#include "ToolMenuEntry.h"
#include "ToolMenuSection.h"
#include "ToolMenus.h"

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP11Menu"

namespace
{
	const TCHAR* P11SectionName = TEXT("UEAgentKitGraphLayoutP11");
	const TCHAR* P11EntryName = TEXT("UEAgentKitAutoArrangePreviewP11");
	const TCHAR* P11MarkerFile = TEXT("P11TestOnlyBuild.json");
	const TCHAR* P11HostProject = TEXT("P11AutomationHost");

	bool SaveJsonObject(const FString& Path, const TSharedPtr<FJsonObject>& Object, FString& OutError)
	{
		if (!Object.IsValid())
		{
			OutError = TEXT("No JSON object was supplied.");
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer)
			|| !FFileHelper::SaveStringToFile(
				Text,
				*Path,
				FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write %s."), *Path);
			return false;
		}
		return true;
	}

	TSharedPtr<FJsonObject> ReadJsonObject(const FString& Path, FString& OutError)
	{
		FString Text;
		if (!FFileHelper::LoadFileToString(Text, *Path))
		{
			OutError = FString::Printf(TEXT("Could not read %s."), *Path);
			return nullptr;
		}
		TSharedPtr<FJsonObject> Object;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
		if (!FJsonSerializer::Deserialize(Reader, Object) || !Object.IsValid())
		{
			OutError = FString::Printf(TEXT("Could not parse JSON in %s."), *Path);
			return nullptr;
		}
		return Object;
	}

	FString PreviewDirectory()
	{
		return FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("P11Preview"));
	}

	bool bSuppressTestDialogs = false;

	void ShowMessage(const FString& Text)
	{
		if (bSuppressTestDialogs)
		{
			UE_LOG(LogTemp, Display, TEXT("UEAgentKit P11 menu message (suppressed for tests): %s"), *Text);
			return;
		}
		FMessageDialog::Open(
			EAppMsgType::Ok,
			FText::FromString(Text),
			FText::FromString(TEXT("UEAgentKit Graph Layout Preview (P11 test host)")));
	}
}

FUEAgentKitGraphLayoutP11Menu::FUEAgentKitGraphLayoutP11Menu(FUEAgentKitEditorBridge* InBridge)
	: Bridge(InBridge)
{
}

bool FUEAgentKitGraphLayoutP11Menu::IsAvailable()
{
	return IsAvailableFor(
		FString(FApp::GetProjectName()),
		FPaths::FileExists(FPaths::Combine(FPaths::ProjectDir(), P11MarkerFile)));
}

bool FUEAgentKitGraphLayoutP11Menu::IsAvailableFor(const FString& ProjectName, bool bMarkerExists)
{
	return bMarkerExists && ProjectName.Equals(P11HostProject, ESearchCase::CaseSensitive);
}

void FUEAgentKitGraphLayoutP11Menu::SetTestDialogSuppression(bool bSuppress)
{
	bSuppressTestDialogs = bSuppress;
}

bool FUEAgentKitGraphLayoutP11Menu::ShouldAddEntry(const UToolMenu* Menu)
{
	if (Menu == nullptr || !IsAvailable())
	{
		return false;
	}
	const UGraphNodeContextMenuContext* Context = Menu->FindContext<UGraphNodeContextMenuContext>();
	if (Context == nullptr)
	{
		return false;
	}
	return Context->Node != nullptr || Context->Pin != nullptr;
}

void FUEAgentKitGraphLayoutP11Menu::Startup()
{
	if (!IsAvailable())
	{
		return;
	}
	UToolMenus* ToolMenus = UToolMenus::Get();
	if (ToolMenus == nullptr)
	{
		return;
	}
	// The common root is the ancestor of every schema/node context menu.  The
	// section stays empty for a background menu because the dynamic delegate
	// checks that a node or pin context is present.
	UToolMenu* Menu = ToolMenus->ExtendMenu(TEXT("GraphEditor.GraphContextMenu.Common"));
	if (Menu == nullptr)
	{
		return;
	}
	Menu->AddDynamicSection(
		P11SectionName,
		FNewToolMenuDelegate::CreateRaw(this, &FUEAgentKitGraphLayoutP11Menu::AddContextMenuSection));
	bRegistered = true;
}

void FUEAgentKitGraphLayoutP11Menu::Shutdown()
{
	if (!bRegistered)
	{
		return;
	}
	if (UToolMenus* ToolMenus = UToolMenus::Get())
	{
		ToolMenus->RemoveSection(TEXT("GraphEditor.GraphContextMenu.Common"), P11SectionName);
	}
	bRegistered = false;
}

void FUEAgentKitGraphLayoutP11Menu::AddContextMenuSection(UToolMenu* Menu)
{
	if (!ShouldAddEntry(Menu))
	{
		return;
	}
	UGraphNodeContextMenuContext* Context = Menu->FindContext<UGraphNodeContextMenuContext>();
	if (Context == nullptr)
	{
		return;
	}

	FToolMenuSection& Section = Menu->FindOrAddSection(
		P11SectionName,
		LOCTEXT("Section", "UEAgentKit Graph Layout (P11 test host)"));
	Section.AddMenuEntry(
		P11EntryName,
		LOCTEXT("AutoArrangePreview", "Auto Arrange (Preview)"),
		LOCTEXT(
			"AutoArrangePreviewTip",
			"Compute a read-only LayoutPlan for this graph. Never applies, saves, or compiles."),
		FSlateIcon(),
		FToolUIActionChoice(FToolUIAction(FToolMenuExecuteAction::CreateRaw(
			this,
			&FUEAgentKitGraphLayoutP11Menu::ExecutePreviewFromContext))));
}

void FUEAgentKitGraphLayoutP11Menu::ExecutePreviewFromContext(const FToolMenuContext& Context)
{
	const UGraphNodeContextMenuContext* MenuContext = Context.FindContext<UGraphNodeContextMenuContext>();
	if (MenuContext == nullptr)
	{
		ShowMessage(TEXT("No graph node or pin context was supplied."));
		return;
	}
	const UEdGraphNode* Node = MenuContext->Node;
	if (Node == nullptr && MenuContext->Pin != nullptr)
	{
		Node = MenuContext->Pin->GetOwningNode();
	}
	ExecutePreview(
		TWeakObjectPtr<UEdGraph>(const_cast<UEdGraph*>(MenuContext->Graph.Get())),
		Node != nullptr ? Node->NodeGuid : FGuid(),
		MenuContext->Pin != nullptr ? MenuContext->Pin->PinName.ToString() : FString(),
		MenuContext->Pin != nullptr);
}

void FUEAgentKitGraphLayoutP11Menu::ExecutePreview(
	TWeakObjectPtr<UEdGraph> Graph,
	FGuid ContextNodeGuid,
	FString ContextPinName,
	bool bFromPin)
{
	if (!IsAvailable() || Bridge == nullptr || !Graph.IsValid())
	{
		ShowMessage(TEXT("The P11 disposable graph-layout preview gate is closed."));
		return;
	}
	UEdGraph* GraphObject = Graph.Get();
	UPackage* Package = GraphObject->GetOutermost();
	const bool bDirtyBefore = Package != nullptr && Package->IsDirty();

	const FString OutputDir = PreviewDirectory();
	const FString ReceiptPath = FPaths::Combine(OutputDir, TEXT("menu-invocation.json"));
	TSharedRef<FJsonObject> Receipt = MakeShared<FJsonObject>();
	Receipt->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p11.menu-invocation/1.0"));
	Receipt->SetStringField(TEXT("project"), FApp::GetProjectName());
	Receipt->SetStringField(TEXT("graphGuid"), GraphObject->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	Receipt->SetStringField(TEXT("graphName"), GraphObject->GetName());
	Receipt->SetStringField(TEXT("contextKind"), bFromPin ? TEXT("pin") : TEXT("node"));
	Receipt->SetStringField(TEXT("contextNodeGuid"), ContextNodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
	Receipt->SetStringField(TEXT("contextPinName"), ContextPinName);
	Receipt->SetBoolField(TEXT("readOnly"), true);
	Receipt->SetBoolField(TEXT("saved"), false);
	Receipt->SetBoolField(TEXT("compiled"), false);
	Receipt->SetBoolField(TEXT("packageDirtyBefore"), bDirtyBefore);
	Receipt->SetStringField(TEXT("invokedAtUtc"), FDateTime::UtcNow().ToIso8601());

	FString Error;
	SaveJsonObject(ReceiptPath, Receipt, Error);

	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!Bridge->BuildP9MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage))
	{
		Receipt->SetBoolField(TEXT("ok"), false);
		Receipt->SetStringField(TEXT("errorCode"), ErrorCode);
		Receipt->SetStringField(TEXT("errorMessage"), ErrorMessage);
		Receipt->SetBoolField(TEXT("packageDirtyAfter"), Package != nullptr && Package->IsDirty());
		SaveJsonObject(ReceiptPath, Receipt, Error);
		ShowMessage(FString::Printf(TEXT("Preview preflight failed.\n\n%s\n%s"), *ErrorCode, *ErrorMessage));
		return;
	}

	Receipt->SetStringField(TEXT("assetPath"), Preflight->GetStringField(TEXT("assetPath")));
	Receipt->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
	Receipt->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
	Receipt->SetNumberField(TEXT("nodeCount"), Preflight->GetNumberField(TEXT("nodeCount")));

	const FString PreflightPath = FPaths::Combine(OutputDir, TEXT("preflight.json"));
	const FString EnvelopePath = FPaths::Combine(OutputDir, TEXT("context-envelope.json"));
	const FString PreviewPath = FPaths::Combine(OutputDir, TEXT("preview.json"));
	if (!SaveJsonObject(PreflightPath, Preflight, Error) || !SaveJsonObject(EnvelopePath, Envelope, Error))
	{
		Receipt->SetBoolField(TEXT("ok"), false);
		Receipt->SetStringField(TEXT("errorCode"), TEXT("p11-menu-evidence-write-failed"));
		Receipt->SetStringField(TEXT("errorMessage"), Error);
		SaveJsonObject(ReceiptPath, Receipt, Error);
		ShowMessage(Error);
		return;
	}
	IFileManager::Get().Delete(*PreviewPath, false, true, true);

	FString WorkerPath = FPaths::ConvertRelativePathToFull(
		FPaths::Combine(FPaths::ProjectDir(), TEXT("../../tools/p9_layout_preview_worker.py")));
	FPaths::CollapseRelativeDirectories(WorkerPath);
	if (!FPaths::FileExists(WorkerPath))
	{
		Receipt->SetBoolField(TEXT("ok"), false);
		Receipt->SetStringField(TEXT("errorCode"), TEXT("p11-menu-worker-missing"));
		Receipt->SetStringField(TEXT("errorMessage"), WorkerPath);
		SaveJsonObject(ReceiptPath, Receipt, Error);
		ShowMessage(FString::Printf(TEXT("Preview worker is missing:\n%s"), *WorkerPath));
		return;
	}

	// Headless hosts do not reliably inherit a console/launcher environment:
	// both py.exe and cmd.exe spawns returned exit 2 with no output.  Run the
	// same bounded worker through the Editor's own Python plugin instead, which
	// writes the identical preview.json and keeps the operation read-only.
	IPythonScriptPlugin* Python = IPythonScriptPlugin::Get();
	const bool bPythonAvailable = Python != nullptr && Python->IsPythonAvailable();
	int32 ReturnCode = 1;
	Receipt->SetStringField(
		TEXT("workerMode"),
		bPythonAvailable ? TEXT("in-process-python") : TEXT("unavailable"));
	if (bPythonAvailable)
	{
		const FString Code = FString::Printf(
			TEXT("import runpy, sys\n"
				 "sys.argv = [r'%s', '--envelope', r'%s', '--preflight', r'%s', '--out', r'%s']\n"
				 "runpy.run_path(r'%s', run_name='__main__')\n"),
			*WorkerPath,
			*EnvelopePath,
			*PreflightPath,
			*PreviewPath,
			*WorkerPath);
		Python->ExecPythonCommand(*Code);
		ReturnCode = FPaths::FileExists(PreviewPath) ? 0 : 1;
	}
	else
	{
		Receipt->SetBoolField(TEXT("ok"), false);
		Receipt->SetStringField(TEXT("errorCode"), TEXT("p11-menu-python-unavailable"));
		Receipt->SetStringField(TEXT("errorMessage"), TEXT("The Editor Python plugin is unavailable; no graph was modified."));
		SaveJsonObject(ReceiptPath, Receipt, Error);
		ShowMessage(TEXT("The Editor Python plugin is unavailable. No graph was modified."));
		return;
	}

	TSharedPtr<FJsonObject> Preview = FPaths::FileExists(PreviewPath)
		? ReadJsonObject(PreviewPath, Error)
		: nullptr;
	bool bPlanProduced = false;
	if (Preview.IsValid() && Preview->GetBoolField(TEXT("ok")))
	{
		const TSharedPtr<FJsonObject>* Result = nullptr;
		const TSharedPtr<FJsonObject>* Plan = nullptr;
		if (Preview->TryGetObjectField(TEXT("result"), Result)
			&& Result != nullptr
			&& (*Result)->TryGetObjectField(TEXT("plan"), Plan)
			&& Plan != nullptr)
		{
			Receipt->SetStringField(TEXT("planId"), (*Plan)->GetStringField(TEXT("planId")));
			Receipt->SetStringField(TEXT("layoutHash"), (*Plan)->GetStringField(TEXT("layoutHash")));
			Receipt->SetStringField(TEXT("planStatus"), (*Plan)->GetStringField(TEXT("status")));
			Receipt->SetNumberField(TEXT("movedCount"), (*Plan)->GetArrayField(TEXT("movedNodeIds")).Num());
			bPlanProduced = (*Plan)->GetStringField(TEXT("status")) == TEXT("ok");
		}
	}
	else if (Preview.IsValid())
	{
		const TSharedPtr<FJsonObject>* PreviewError = nullptr;
		if (Preview->TryGetObjectField(TEXT("error"), PreviewError) && PreviewError != nullptr)
		{
			Receipt->SetStringField(TEXT("errorCode"), (*PreviewError)->GetStringField(TEXT("code")));
			Receipt->SetStringField(TEXT("errorMessage"), (*PreviewError)->GetStringField(TEXT("message")));
		}
	}
	Receipt->SetBoolField(TEXT("ok"), bPlanProduced);
	Receipt->SetBoolField(TEXT("planProduced"), bPlanProduced);
	Receipt->SetNumberField(TEXT("workerExitCode"), ReturnCode);
	Receipt->SetBoolField(TEXT("packageDirtyAfter"), Package != nullptr && Package->IsDirty());
	Receipt->SetStringField(TEXT("completedAtUtc"), FDateTime::UtcNow().ToIso8601());
	SaveJsonObject(ReceiptPath, Receipt, Error);

	if (bPlanProduced)
	{
		ShowMessage(FString::Printf(
			TEXT("Read-only preview ready.\nPlan: %s\nReadOnly: true | Saved: false | Compiled: false"),
			*Receipt->GetStringField(TEXT("planId"))));
	}
	else
	{
		ShowMessage(FString::Printf(
			TEXT("Preview did not produce a plan.\n%s\nNo graph was modified."),
			*Receipt->GetStringField(TEXT("errorMessage"))));
	}
}

#undef LOCTEXT_NAMESPACE
