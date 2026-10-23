#include "BlueprintGraphLayoutP8Menu.h"

#include "BlueprintGraphLayoutP8TestOnly.h"
#include "EditorBridge.h"

#include "Framework/MultiBox/MultiBoxBuilder.h"
#include "GraphEditorModule.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/FileHelper.h"
#include "Misc/MessageDialog.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP8Menu"

namespace
{
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
			OutError = FString::Printf(TEXT("Could not parse %s."), *Path);
			return nullptr;
		}
		return Object;
	}

	void ShowPreviewMessage(const FString& Text)
	{
		FMessageDialog::Open(
			EAppMsgType::Ok,
			FText::FromString(Text),
			FText::FromString(TEXT("UEAgentKit Graph Layout Preview (P8 test host)")));
	}
}

FUEAgentKitGraphLayoutP8Menu::FUEAgentKitGraphLayoutP8Menu(FUEAgentKitEditorBridge* InBridge)
	: Bridge(InBridge)
{
}

void FUEAgentKitGraphLayoutP8Menu::Startup()
{
	FGraphEditorModule& GraphEditorModule =
		FModuleManager::LoadModuleChecked<FGraphEditorModule>(TEXT("GraphEditor"));
	FGraphEditorModule::FGraphEditorMenuExtender_SelectedNode Delegate =
		FGraphEditorModule::FGraphEditorMenuExtender_SelectedNode::CreateRaw(
			this,
			&FUEAgentKitGraphLayoutP8Menu::ExtendGraphContextMenu);
	ExtenderHandle = Delegate.GetHandle();
	GraphEditorModule.GetAllGraphEditorContextMenuExtender().Add(MoveTemp(Delegate));
}

void FUEAgentKitGraphLayoutP8Menu::Shutdown()
{
	if (!ExtenderHandle.IsValid())
	{
		return;
	}
	if (FGraphEditorModule* GraphEditorModule =
		FModuleManager::GetModulePtr<FGraphEditorModule>(TEXT("GraphEditor")))
	{
		GraphEditorModule->GetAllGraphEditorContextMenuExtender().RemoveAll(
			[this](const FGraphEditorModule::FGraphEditorMenuExtender_SelectedNode& Delegate)
			{
				return Delegate.GetHandle() == ExtenderHandle;
			});
	}
	ExtenderHandle.Reset();
}

TSharedRef<FExtender> FUEAgentKitGraphLayoutP8Menu::ExtendGraphContextMenu(
	TSharedRef<FUICommandList> CommandList,
	const UEdGraph* Graph,
	const UEdGraphNode* Node,
	const UEdGraphPin* Pin,
	bool bReadOnly)
{
	TSharedRef<FExtender> Extender = MakeShared<FExtender>();
	if (Graph == nullptr
		|| !UEAgentKitBlueprintGraphLayoutP8::IsTestOnlyWriteAvailable())
	{
		return Extender;
	}
	Extender->AddMenuExtension(
		TEXT("GraphEditorGraphContextMenu"),
		EExtensionHook::After,
		CommandList,
		FMenuExtensionDelegate::CreateRaw(
			this,
			&FUEAgentKitGraphLayoutP8Menu::AddMenuEntry,
			Graph));
	return Extender;
}

void FUEAgentKitGraphLayoutP8Menu::AddMenuEntry(FMenuBuilder& MenuBuilder, const UEdGraph* Graph)
{
	MenuBuilder.BeginSection(
		TEXT("UEAgentKitGraphLayoutP8"),
		LOCTEXT("Section", "UEAgentKit Graph Layout (P8 test host)"));
	MenuBuilder.AddMenuEntry(
		LOCTEXT("AutoArrangePreview", "Auto Arrange (Preview)"),
		LOCTEXT(
			"AutoArrangePreviewTip",
			"Compute a read-only P0 LayoutPlan for this graph. Never applies, saves, or compiles."),
		FSlateIcon(),
		FUIAction(FExecuteAction::CreateRaw(
			this,
			&FUEAgentKitGraphLayoutP8Menu::ExecutePreview,
			Graph)));
	MenuBuilder.EndSection();
}

void FUEAgentKitGraphLayoutP8Menu::ExecutePreview(const UEdGraph* Graph)
{
	if (Bridge == nullptr
		|| Graph == nullptr
		|| !UEAgentKitBlueprintGraphLayoutP8::IsTestOnlyWriteAvailable())
	{
		ShowPreviewMessage(TEXT("The P8 disposable graph-layout preview gate is closed."));
		return;
	}

	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!Bridge->BuildP8MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage))
	{
		ShowPreviewMessage(
			FString::Printf(TEXT("Preview preflight failed.\n\n%s\n%s"), *ErrorCode, *ErrorMessage));
		return;
	}

	const FString OutputDir = FPaths::Combine(
		FPaths::ProjectSavedDir(),
		TEXT("UEAgentKit"),
		TEXT("P8Preview"));
	const FString PreflightPath = FPaths::Combine(OutputDir, TEXT("preflight.json"));
	const FString EnvelopePath = FPaths::Combine(OutputDir, TEXT("context-envelope.json"));
	const FString PreviewPath = FPaths::Combine(OutputDir, TEXT("preview.json"));
	if (!SaveJsonObject(PreflightPath, Preflight, ErrorMessage)
		|| !SaveJsonObject(EnvelopePath, Envelope, ErrorMessage))
	{
		ShowPreviewMessage(ErrorMessage);
		return;
	}
	IFileManager::Get().Delete(*PreviewPath, false, true, true);

	FString WorkerPath = FPaths::ConvertRelativePathToFull(
		FPaths::Combine(FPaths::ProjectDir(), TEXT("../../tools/p8_layout_preview_worker.py")));
	FPaths::CollapseRelativeDirectories(WorkerPath);
	if (!FPaths::FileExists(WorkerPath))
	{
		ShowPreviewMessage(FString::Printf(TEXT("Preview worker is missing:\n%s"), *WorkerPath));
		return;
	}

	const FString Arguments = FString::Printf(
		TEXT("-3.12 \"%s\" --envelope \"%s\" --preflight \"%s\" --out \"%s\""),
		*WorkerPath,
		*EnvelopePath,
		*PreflightPath,
		*PreviewPath);
	uint32 ProcessId = 0;
	FProcHandle Process = FPlatformProcess::CreateProc(
		TEXT("py.exe"),
		*Arguments,
		true,
		true,
		false,
		&ProcessId,
		0,
		*OutputDir,
		nullptr,
		nullptr);
	if (!Process.IsValid())
	{
		ShowPreviewMessage(
			TEXT("Could not start the local Python preview worker. No graph was modified."));
		return;
	}
	FPlatformProcess::WaitForProc(Process);
	int32 ReturnCode = INDEX_NONE;
	FPlatformProcess::GetProcReturnCode(Process, &ReturnCode);
	FPlatformProcess::CloseProc(Process);
	if (!FPaths::FileExists(PreviewPath))
	{
		ShowPreviewMessage(
			FString::Printf(
				TEXT("Preview worker exited with code %d and wrote no result.\nNo graph was modified."),
				ReturnCode));
		return;
	}

	const TSharedPtr<FJsonObject> Payload = ReadJsonObject(PreviewPath, ErrorMessage);
	if (!Payload.IsValid())
	{
		ShowPreviewMessage(ErrorMessage);
		return;
	}
	if (!Payload->GetBoolField(TEXT("ok")))
	{
		const TSharedPtr<FJsonObject>* Error = nullptr;
		FString Code = TEXT("p8-menu-preview-failed");
		FString Message = TEXT("The preview worker rejected the graph.");
		if (Payload->TryGetObjectField(TEXT("error"), Error)
			&& Error != nullptr
			&& Error->IsValid())
		{
			(*Error)->TryGetStringField(TEXT("code"), Code);
			(*Error)->TryGetStringField(TEXT("message"), Message);
		}
		ShowPreviewMessage(FString::Printf(TEXT("%s\n\n%s"), *Code, *Message));
		return;
	}

	const TSharedPtr<FJsonObject>* ResultField = nullptr;
	if (!Payload->TryGetObjectField(TEXT("result"), ResultField)
		|| ResultField == nullptr
		|| !ResultField->IsValid())
	{
		ShowPreviewMessage(TEXT("Preview worker returned no result object."));
		return;
	}
	const TSharedPtr<FJsonObject>& Result = *ResultField;
	const TSharedPtr<FJsonObject>* Metrics = nullptr;
	double Overlaps = 0.0;
	double Crossings = 0.0;
	if (Result->TryGetObjectField(TEXT("metrics"), Metrics)
		&& Metrics != nullptr
		&& Metrics->IsValid())
	{
		Overlaps = (*Metrics)->GetNumberField(TEXT("overlapCount"));
		Crossings = (*Metrics)->GetNumberField(TEXT("crossings"));
	}
	const FString Text = FString::Printf(
		TEXT("Read-only LayoutPlan (no apply, no save, no compile)\n\n")
		TEXT("Asset: %s\nGraph: %s\nPlan: %s\nLayout hash: %s\n")
		TEXT("Nodes: %d   Moved: %d\nOverlaps: %.0f   Crossings: %.0f\n")
		TEXT("Evidence: same-process preview only; independent reload still required.\n\n")
		TEXT("Preview JSON:\n%s"),
		*Result->GetStringField(TEXT("assetPath")),
		*Result->GetStringField(TEXT("graphGuid")),
		*Result->GetStringField(TEXT("planId")),
		*Result->GetStringField(TEXT("layoutHash")),
		static_cast<int32>(Result->GetNumberField(TEXT("nodeCount"))),
		static_cast<int32>(Result->GetNumberField(TEXT("movedCount"))),
		Overlaps,
		Crossings,
		*PreviewPath);
	ShowPreviewMessage(Text);
}

#undef LOCTEXT_NAMESPACE
