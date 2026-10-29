// M2.2 disposable-host integration test: real Editor snapshot -> external
// Python M1 broker -> native worker client -> game-thread validation.
//
// The test never starts the broker (no CreateProc). A harness outside the Editor
// starts it after this test exports the P9 snapshot and its session id.

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintEditor.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraphNode_Comment.h"
#include "Editor.h"
#include "BlueprintContextSha256.h"
#include "Editor/Transactor.h"
#include "EditorBridge.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "K2Node_CallFunction.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/AutomationTest.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "NativeLayoutBrokerDescriptor.h"
#include "NativeLayoutPreviewService.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitNativeM2Test
{
	const TCHAR* FixturePackage = TEXT("/Game/M2Native/BP_M2LayoutNative");
	const TCHAR* FixtureName = TEXT("BP_M2LayoutNative");
	const TCHAR* FixturePath = TEXT("/Game/M2Native/BP_M2LayoutNative.BP_M2LayoutNative");

	FString EvidenceDir()
	{
		return FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"));
	}

	bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object, FString& OutError)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
		{
			OutError = FString::Printf(TEXT("Could not serialize %s."), *Path);
			return false;
		}
		if (!FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write %s."), *Path);
			return false;
		}
		return true;
	}

	bool SaveFixture(UBlueprint* Blueprint, FString& OutError)
	{
		UPackage* Package = Blueprint != nullptr ? Blueprint->GetOutermost() : nullptr;
		if (Package == nullptr)
		{
			OutError = TEXT("The M2 fixture package is unavailable.");
			return false;
		}
		Package->MarkPackageDirty();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(), FPackageName::GetAssetPackageExtension());
		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			OutError = TEXT("Could not save the M2 fixture Blueprint.");
			return false;
		}
		return true;
	}

	UBlueprint* LoadOrCreateFixture(FString& OutError)
	{
		if (UBlueprint* Existing = LoadObject<UBlueprint>(nullptr, FixturePath))
		{
			bool bRepaired = false;
			if (Existing->UbergraphPages.Num() > 0)
			{
				for (UEdGraphNode* Node : Existing->UbergraphPages[0]->Nodes)
				{
					if (Node != nullptr && !Node->NodeGuid.IsValid())
					{
						Node->CreateNewGuid();
						bRepaired = true;
					}
				}
			}
			if (bRepaired)
			{
				FString RepairError;
				if (!SaveFixture(Existing, RepairError))
				{
					OutError = RepairError;
					return nullptr;
				}
			}
			return Existing;
		}
		UPackage* Package = CreatePackage(FixturePackage);
		if (Package == nullptr)
		{
			OutError = TEXT("Could not create the M2 fixture package.");
			return nullptr;
		}
		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			AActor::StaticClass(),
			Package,
			FName(FixtureName),
			BPTYPE_Normal,
			FName(TEXT("UEAgentKitM2NativeFixture")));
		if (Blueprint == nullptr || Blueprint->UbergraphPages.Num() == 0)
		{
			OutError = TEXT("Could not create the M2 fixture Blueprint.");
			return nullptr;
		}
		UEdGraph* Graph = Blueprint->UbergraphPages[0];
		FGraphNodeCreator<UK2Node_CallFunction> PrintCreator(*Graph);
		UK2Node_CallFunction* Print = PrintCreator.CreateNode();
		Print->FunctionReference.SetExternalMember(
			GET_FUNCTION_NAME_CHECKED(UKismetSystemLibrary, PrintString),
			UKismetSystemLibrary::StaticClass());
		Print->NodePosX = 360;
		Print->NodePosY = 0;
		PrintCreator.Finalize();

		// No comment node: keep the fixture minimal and GUID-warning-free.

		FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
		FKismetEditorUtilities::CompileBlueprint(Blueprint);
		if (Blueprint->Status == BS_Error)
		{
			OutError = TEXT("The M2 fixture Blueprint failed to compile.");
			return nullptr;
		}
		FAssetRegistryModule::AssetCreated(Blueprint);
		if (!SaveFixture(Blueprint, OutError))
		{
			return nullptr;
		}
		return Blueprint;
	}

	FBlueprintEditor* OpenAndFocus(UBlueprint* Blueprint, UEdGraph* Graph, FString& OutError)
	{
		if (GEditor == nullptr)
		{
			OutError = TEXT("GEditor is unavailable.");
			return nullptr;
		}
		UAssetEditorSubsystem* AssetEditor = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
		if (AssetEditor == nullptr || !AssetEditor->OpenEditorForAsset(Blueprint))
		{
			OutError = TEXT("Could not open the fixture Blueprint Editor.");
			return nullptr;
		}
		IAssetEditorInstance* Instance = AssetEditor->FindEditorForAsset(Blueprint, false);
		FBlueprintEditor* Editor = static_cast<FBlueprintEditor*>(Instance);
		if (Editor == nullptr || !Editor->OpenGraphAndBringToFront(Graph, true).IsValid())
		{
			OutError = TEXT("Could not focus the fixture graph.");
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
	FUEAgentKitNativeM2LayoutBrokerTest,
	"UEAgentKit.Native.M2LayoutBrokerPositive",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeM2LayoutBrokerTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString Error;
	UBlueprint* Blueprint = UEAgentKitNativeM2Test::LoadOrCreateFixture(Error);
	if (!TestNotNull(TEXT("M2 fixture loads"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	if (!TestTrue(TEXT("M2 fixture package is clean"), !Blueprint->GetOutermost()->IsDirty()))
	{
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	if (!TestNotNull(TEXT("M2 graph exists"), Graph))
	{
		return false;
	}
	FBlueprintEditor* Editor = UEAgentKitNativeM2Test::OpenAndFocus(Blueprint, Graph, Error);
	if (!TestNotNull(TEXT("M2 Blueprint Editor opens"), Editor))
	{
		AddError(Error);
		return false;
	}

	{
		FUEAgentKitNativeLayoutPreviewService Disabled(nullptr);
		TestFalse(TEXT("M2 native preview is off before any opt-in"), Disabled.StartPreview());
		TestEqual(
			TEXT("M2 disabled code"),
			Disabled.GetOutcome().Code,
			FString(TEXT("native-preview-disabled")));
	}

	FUEAgentKitEditorBridge Bridge;
	if (!TestTrue(TEXT("M2 test bridge starts"), Bridge.Start()))
	{
		return false;
	}
	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
			TEXT("M2 real P9 snapshot builds"),
			Bridge.BuildP9MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *ErrorCode, *ErrorMessage));
		Bridge.Stop();
		return false;
	}
	if (!TestTrue(TEXT("M2 snapshot is available"), Preflight->GetBoolField(TEXT("available")))
		|| !TestTrue(TEXT("M2 snapshot is complete"), Preflight->GetBoolField(TEXT("complete")))
		|| !TestTrue(TEXT("M2 snapshot is clean"), !Preflight->GetBoolField(TEXT("packageDirty")))
		|| !TestTrue(TEXT("M2 snapshot has a session"), !Preflight->GetStringField(TEXT("sessionId")).IsEmpty()))
	{
		Bridge.Stop();
		return false;
	}

	const FString BrokerDescriptor = FPaths::Combine(
		FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("LayoutBroker.json"));
	{
		FUEAgentKitNativeLayoutPreviewService MissingService(&Bridge);
		TestFalse(
			TEXT("M2 missing broker descriptor fails closed"),
			MissingService.StartPreviewWithSnapshot(Preflight, Envelope, true));
		TestEqual(
			TEXT("M2 missing broker code"),
			MissingService.GetOutcome().Code,
			FString(TEXT("native-preview-broker-descriptor-missing")));
	}

	const FString Evidence = UEAgentKitNativeM2Test::EvidenceDir();
	IFileManager::Get().MakeDirectory(*Evidence, true);
	if (!TestTrue(TEXT("M2 preflight evidence writes"), UEAgentKitNativeM2Test::WriteJson(FPaths::Combine(Evidence, TEXT("snapshot-preflight.json")), Preflight, Error))
		|| !TestTrue(TEXT("M2 context evidence writes"), UEAgentKitNativeM2Test::WriteJson(FPaths::Combine(Evidence, TEXT("snapshot-context.json")), Envelope, Error)))
	{
		AddError(Error);
		Bridge.Stop();
		return false;
	}
	TSharedPtr<FJsonObject> Ready = MakeShared<FJsonObject>();
	Ready->SetStringField(TEXT("sessionId"), Preflight->GetStringField(TEXT("sessionId")));
	Ready->SetStringField(TEXT("projectName"), Preflight->GetStringField(TEXT("projectName")));
	Ready->SetStringField(TEXT("assetPath"), Preflight->GetStringField(TEXT("assetPath")));
	Ready->SetStringField(TEXT("graphGuid"), Preflight->GetStringField(TEXT("graphGuid")));
	Ready->SetNumberField(TEXT("nodeCount"), Preflight->GetNumberField(TEXT("nodeCount")));
	if (!TestTrue(TEXT("M2 snapshot-ready marker writes"), UEAgentKitNativeM2Test::WriteJson(FPaths::Combine(Evidence, TEXT("snapshot-ready.json")), Ready, Error)))
	{
		AddError(Error);
		Bridge.Stop();
		return false;
	}

	const double WaitStarted = FPlatformTime::Seconds();
	while (!IFileManager::Get().FileExists(*BrokerDescriptor)
		&& FPlatformTime::Seconds() - WaitStarted < 60.0)
	{
		FPlatformProcess::Sleep(0.1f);
	}
	if (!TestTrue(TEXT("External M1 broker descriptor appears"), IFileManager::Get().FileExists(*BrokerDescriptor)))
	{
		Bridge.Stop();
		return false;
	}
	{
		FString DescriptorText;
		const FString InsecureDescriptor = FPaths::Combine(
			FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("LayoutBroker-insecure.json"));
		if (FFileHelper::LoadFileToString(DescriptorText, *BrokerDescriptor))
		{
			FFileHelper::SaveStringToFile(
				DescriptorText,
				*InsecureDescriptor,
				FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
			FUEAgentKitLayoutBrokerDescriptor Insecure;
			FString InsecureCode;
			FString InsecureMessage;
			TestFalse(
				TEXT("M2 inherited-DACL descriptor is rejected"),
				FUEAgentKitLayoutBrokerDescriptor::LoadFromPath(
					InsecureDescriptor, Insecure, InsecureCode, InsecureMessage));
			TestEqual(
				TEXT("M2 insecure descriptor code"),
				InsecureCode,
				FString(TEXT("native-preview-broker-descriptor-invalid")));
			IFileManager::Get().Delete(*InsecureDescriptor);
		}
	}

	FUEAgentKitNativeLayoutPreviewService Service(&Bridge);
	if (!TestTrue(
			TEXT("M2 native preview starts against the external broker"),
			Service.StartPreviewWithSnapshot(Preflight, Envelope, true)))
	{
		const FUEAgentKitNativeLayoutPreviewOutcome Failed = Service.GetOutcome();
		AddError(FString::Printf(TEXT("%s: %s"), *Failed.Code, *Failed.Message));
		Bridge.Stop();
		return false;
	}
	const double PreviewStarted = FPlatformTime::Seconds();
	while (!Service.IsTerminal() && FPlatformTime::Seconds() - PreviewStarted < 10.0)
	{
		Service.Tick();
		FPlatformProcess::Sleep(0.02f);
	}
	Service.Tick();
	const FUEAgentKitNativeLayoutPreviewOutcome Outcome = Service.GetOutcome();
	if (!TestTrue(TEXT("M2 native preview reaches a terminal state"), Service.IsTerminal()))
	{
		Bridge.Stop();
		return false;
	}
	if (!TestTrue(TEXT("M2 native preview succeeds"), Outcome.bOk))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *Outcome.Code, *Outcome.Message));
	}
	if (Outcome.Receipt.IsValid())
	{
		TestTrue(TEXT("M2 receipt is read-only"), Outcome.Receipt->GetBoolField(TEXT("readOnly")));
		TestFalse(TEXT("M2 receipt did not save"), Outcome.Receipt->GetBoolField(TEXT("saved")));
		TestFalse(TEXT("M2 receipt did not compile"), Outcome.Receipt->GetBoolField(TEXT("compiled")));
		TestEqual(TEXT("M2 receipt previewPath"), Outcome.Receipt->GetStringField(TEXT("previewPath")), FString(TEXT("native-broker")));
		FUEAgentKitLayoutBrokerDescriptor Descriptor;
		FString DescriptorCode;
		FString DescriptorMessage;
		if (FUEAgentKitLayoutBrokerDescriptor::Load(Descriptor, DescriptorCode, DescriptorMessage))
		{
			FString ReceiptText;
			const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&ReceiptText);
			FJsonSerializer::Serialize(Outcome.Receipt.ToSharedRef(), Writer);
			TestFalse(TEXT("M2 receipt never contains the broker token"), ReceiptText.Contains(Descriptor.Token));
		}
	}
	TestFalse(TEXT("M2 preview leaves the package clean"), Blueprint->GetOutermost()->IsDirty());
	TestTrue(TEXT("M2 preview produces a plan"), Outcome.Plan.IsValid());
	if (Outcome.Plan.IsValid())
	{
		const FString PlanId = Outcome.Plan->GetStringField(TEXT("planId"));
		const FString LayoutHash = Outcome.Plan->GetStringField(TEXT("layoutHash"));
		TestTrue(TEXT("M2 planId is canonical-shaped"), PlanId.StartsWith(TEXT("layout:")) && PlanId.Len() == 23);
		TestTrue(TEXT("M2 layoutHash is sha256"), LayoutHash.StartsWith(TEXT("sha256:")) && LayoutHash.Len() == 71);
	}

	{
		const TSharedPtr<FJsonObject>* Context = nullptr;
		if (Envelope->TryGetObjectField(TEXT("context"), Context) && Context != nullptr && Context->IsValid())
		{
			(*Context)->SetStringField(
				TEXT("M2OversizePadding"), FString::ChrN(1100000, TEXT('x')));
			FUEAgentKitNativeLayoutPreviewService OversizeService(&Bridge);
			TestFalse(
				TEXT("M2 oversized request is rejected locally"),
				OversizeService.StartPreviewWithSnapshot(Preflight, Envelope, true));
			TestEqual(
				TEXT("M2 oversized request code"),
				OversizeService.GetOutcome().Code,
				FString(TEXT("native-preview-request-too-large")));
			TestFalse(
				TEXT("M2 oversized request never accepts a plan"),
				OversizeService.GetOutcome().Plan.IsValid());
		}
	}

	TSharedPtr<FJsonObject> EvidenceResult = MakeShared<FJsonObject>();
	EvidenceResult->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m2.native-result/1.0"));
	EvidenceResult->SetBoolField(TEXT("ok"), Outcome.bOk);
	EvidenceResult->SetStringField(TEXT("code"), Outcome.Code);
	EvidenceResult->SetStringField(TEXT("message"), Outcome.Message);
	EvidenceResult->SetBoolField(TEXT("packageDirtyAfter"), Blueprint->GetOutermost()->IsDirty());
	if (Outcome.Receipt.IsValid())
	{
		EvidenceResult->SetObjectField(TEXT("receipt"), Outcome.Receipt);
	}
	if (Outcome.Plan.IsValid())
	{
		EvidenceResult->SetObjectField(TEXT("result"), Outcome.Plan);
	}
	if (!TestTrue(TEXT("M2 result evidence writes"), UEAgentKitNativeM2Test::WriteJson(FPaths::Combine(Evidence, TEXT("native-result.json")), EvidenceResult, Error)))
	{
		AddError(Error);
	}
	Bridge.Stop();
	return Outcome.bOk;
}


IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeM2LayoutBrokerNegativeTest,
	"UEAgentKit.Native.M2LayoutBrokerNegative",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeM2LayoutBrokerNegativeTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString Scenario;
	FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM2Scenario="), Scenario);
	if (!TestFalse(TEXT("M2 negative scenario is provided"), Scenario.IsEmpty()))
	{
		return false;
	}
	FString Error;
	UBlueprint* Blueprint = UEAgentKitNativeM2Test::LoadOrCreateFixture(Error);
	if (!TestNotNull(TEXT("M2 negative fixture loads"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	if (!TestNotNull(TEXT("M2 negative graph exists"), Graph))
	{
		return false;
	}
	if (!TestNotNull(TEXT("M2 negative Editor opens"), UEAgentKitNativeM2Test::OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}
	FUEAgentKitEditorBridge Bridge;
	if (!TestTrue(TEXT("M2 negative bridge starts"), Bridge.Start()))
	{
		return false;
	}
	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
			TEXT("M2 negative snapshot builds"),
			Bridge.BuildP9MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *ErrorCode, *ErrorMessage));
		Bridge.Stop();
		return false;
	}
	FUEAgentKitNativeLayoutPreviewService Service(&Bridge);
	if (!TestTrue(
			TEXT("M2 negative preview starts"),
			Service.StartPreviewWithSnapshot(Preflight, Envelope, true)))
	{
		const FUEAgentKitNativeLayoutPreviewOutcome Failed = Service.GetOutcome();
		AddError(FString::Printf(TEXT("%s: %s"), *Failed.Code, *Failed.Message));
		Bridge.Stop();
		return false;
	}
	const double Started = FPlatformTime::Seconds();
	while (!Service.IsTerminal() && FPlatformTime::Seconds() - Started < 9.0)
	{
		Service.Tick();
		FPlatformProcess::Sleep(0.02f);
	}
	Service.Tick();
	const FUEAgentKitNativeLayoutPreviewOutcome Outcome = Service.GetOutcome();
	FString Expected;
	if (Scenario == TEXT("spoof"))
	{
		Expected = TEXT("native-preview-plan-id-mismatch");
	}
	else if (Scenario == TEXT("oversize"))
	{
		Expected = TEXT("native-preview-response-too-large");
	}
	else if (Scenario == TEXT("timeout"))
	{
		Expected = TEXT("native-preview-timeout");
	}
	else if (Scenario == TEXT("token-error") || Scenario == TEXT("wrong-token"))
	{
		Expected = TEXT("native-preview-token-rejected");
	}
	else
	{
		Expected = TEXT("native-preview-worker-failed");
	}
	TestTrue(TEXT("M2 negative preview is terminal"), Service.IsTerminal());
	TestEqual(TEXT("M2 negative stable code"), Outcome.Code, Expected);
	TestFalse(TEXT("M2 negative never accepts a plan"), Outcome.Plan.IsValid());

	TSharedPtr<FJsonObject> EvidenceResult = MakeShared<FJsonObject>();
	EvidenceResult->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m2.native-negative/1.0"));
	EvidenceResult->SetStringField(TEXT("scenario"), Scenario);
	EvidenceResult->SetStringField(TEXT("code"), Outcome.Code);
	EvidenceResult->SetStringField(TEXT("expectedCode"), Expected);
	EvidenceResult->SetBoolField(TEXT("ok"), Outcome.Code == Expected);
	EvidenceResult->SetBoolField(TEXT("planAccepted"), Outcome.Plan.IsValid());
	if (Outcome.Receipt.IsValid())
	{
		EvidenceResult->SetObjectField(TEXT("receipt"), Outcome.Receipt);
	}
	TestTrue(
		TEXT("M2 negative evidence writes"),
		UEAgentKitNativeM2Test::WriteJson(
			FPaths::Combine(UEAgentKitNativeM2Test::EvidenceDir(), TEXT("native-negative-result.json")),
			EvidenceResult,
			Error));
	Bridge.Stop();
	return Outcome.Code == Expected && !Outcome.Plan.IsValid();
}


IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeM2LayoutBrokerStaleTest,
	"UEAgentKit.Native.M2LayoutBrokerStale",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeM2LayoutBrokerStaleTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString Scenario;
	FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM2Scenario="), Scenario);
	if (!TestTrue(TEXT("M2 stale scenario is revision or fingerprint"),
		Scenario == TEXT("revision") || Scenario == TEXT("fingerprint")))
	{
		return false;
	}
	FString Error;
	UBlueprint* Blueprint = UEAgentKitNativeM2Test::LoadOrCreateFixture(Error);
	if (!TestNotNull(TEXT("M2 stale fixture loads"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	if (!TestNotNull(TEXT("M2 stale graph exists"), Graph))
	{
		return false;
	}
	if (!TestNotNull(TEXT("M2 stale Editor opens"), UEAgentKitNativeM2Test::OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}
	FUEAgentKitEditorBridge Bridge;
	if (!TestTrue(TEXT("M2 stale bridge starts"), Bridge.Start()))
	{
		return false;
	}
	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
			TEXT("M2 stale snapshot builds"),
			Bridge.BuildP9MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *ErrorCode, *ErrorMessage));
		Bridge.Stop();
		return false;
	}
	FUEAgentKitNativeLayoutPreviewService Service(&Bridge);
	if (!TestTrue(
			TEXT("M2 stale preview starts"),
			Service.StartPreviewWithSnapshot(Preflight, Envelope, true)))
	{
		const FUEAgentKitNativeLayoutPreviewOutcome Failed = Service.GetOutcome();
		AddError(FString::Printf(TEXT("%s: %s"), *Failed.Code, *Failed.Message));
		Bridge.Stop();
		return false;
	}
	const double WaitStarted = FPlatformTime::Seconds();
	while (!Service.HasWorkerResponseForTest() && FPlatformTime::Seconds() - WaitStarted < 8.0)
	{
		FPlatformProcess::Sleep(0.02f);
	}
	if (!TestTrue(TEXT("M2 stale broker response is ready"), Service.HasWorkerResponseForTest()))
	{
		Bridge.Stop();
		return false;
	}

	UK2Node_CallFunction* Print = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		Print = Cast<UK2Node_CallFunction>(Node);
		if (Print != nullptr)
		{
			break;
		}
	}
	if (!TestNotNull(TEXT("M2 stale fixture has a call node"), Print))
	{
		Bridge.Stop();
		return false;
	}
	Print->NodePosX += 40;
	if (Scenario == TEXT("revision"))
	{
		if (!TestTrue(TEXT("M2 stale mutation saves the package"), UEAgentKitNativeM2Test::SaveFixture(Blueprint, Error)))
		{
			AddError(Error);
			Bridge.Stop();
			return false;
		}
	}
	else
	{
		Blueprint->GetOutermost()->SetDirtyFlag(false);
	}
	const int32 UndoBefore = GEditor != nullptr && GEditor->Trans != nullptr ? GEditor->Trans->GetUndoCount() : 0;
	Service.Tick();
	const FUEAgentKitNativeLayoutPreviewOutcome Outcome = Service.GetOutcome();
	const int32 UndoAfter = GEditor != nullptr && GEditor->Trans != nullptr ? GEditor->Trans->GetUndoCount() : 0;
	const FString Expected = Scenario == TEXT("revision")
		? TEXT("native-preview-revision-conflict")
		: TEXT("native-preview-fingerprint-conflict");
	TestTrue(TEXT("M2 stale preview is terminal"), Service.IsTerminal());
	TestEqual(TEXT("M2 stale code"), Outcome.Code, Expected);
	TestFalse(TEXT("M2 stale preview accepts no plan"), Outcome.Plan.IsValid());
	TestEqual(TEXT("M2 stale preview writes no transaction"), UndoAfter, UndoBefore);
	TestFalse(TEXT("M2 stale preview does not dirty the package"), Blueprint->GetOutermost()->IsDirty());
	if (Outcome.Receipt.IsValid())
	{
		TestFalse(TEXT("M2 stale receipt did not save"), Outcome.Receipt->GetBoolField(TEXT("saved")));
		TestFalse(TEXT("M2 stale receipt did not compile"), Outcome.Receipt->GetBoolField(TEXT("compiled")));
	}

	TSharedPtr<FJsonObject> EvidenceResult = MakeShared<FJsonObject>();
	EvidenceResult->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m2.native-stale/1.0"));
	EvidenceResult->SetStringField(TEXT("scenario"), Scenario);
	EvidenceResult->SetStringField(TEXT("code"), Outcome.Code);
	EvidenceResult->SetStringField(TEXT("expectedCode"), Expected);
	EvidenceResult->SetBoolField(TEXT("ok"), Outcome.Code == Expected && !Outcome.Plan.IsValid());
	EvidenceResult->SetBoolField(TEXT("planAccepted"), Outcome.Plan.IsValid());
	EvidenceResult->SetNumberField(TEXT("undoBefore"), UndoBefore);
	EvidenceResult->SetNumberField(TEXT("undoAfter"), UndoAfter);
	EvidenceResult->SetBoolField(TEXT("packageDirtyAfter"), Blueprint->GetOutermost()->IsDirty());
	if (Outcome.Receipt.IsValid())
	{
		EvidenceResult->SetObjectField(TEXT("receipt"), Outcome.Receipt);
	}
	TestTrue(
		TEXT("M2 stale evidence writes"),
		UEAgentKitNativeM2Test::WriteJson(
			FPaths::Combine(UEAgentKitNativeM2Test::EvidenceDir(), TEXT("native-stale-result.json")),
			EvidenceResult,
			Error));
	Bridge.Stop();
	return Outcome.Code == Expected && !Outcome.Plan.IsValid();
}


IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNativeM2FingerprintCaptureTest,
	"UEAgentKit.Native.M2FingerprintCapture",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNativeM2FingerprintCaptureTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	FString CapturePath;
	if (!TestTrue(
			TEXT("M2 fingerprint capture path is provided"),
			FParse::Value(FCommandLine::Get(), TEXT("UEAgentKitM2Capture="), CapturePath)
			&& !CapturePath.IsEmpty()))
	{
		return false;
	}
	FString Error;
	UBlueprint* Blueprint = UEAgentKitNativeM2Test::LoadOrCreateFixture(Error);
	if (!TestNotNull(TEXT("M2 fingerprint fixture loads"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = Blueprint->UbergraphPages[0];
	if (!TestNotNull(TEXT("M2 fingerprint graph exists"), Graph))
	{
		return false;
	}
	if (!TestNotNull(TEXT("M2 fingerprint Editor opens"), UEAgentKitNativeM2Test::OpenAndFocus(Blueprint, Graph, Error)))
	{
		AddError(Error);
		return false;
	}
	FUEAgentKitEditorBridge Bridge;
	if (!TestTrue(TEXT("M2 fingerprint bridge starts"), Bridge.Start()))
	{
		return false;
	}
	TSharedPtr<FJsonObject> Preflight;
	TSharedPtr<FJsonObject> Envelope;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
			TEXT("M2 fingerprint P9 snapshot builds"),
			Bridge.BuildP9MenuPreviewInputs(Preflight, Envelope, ErrorCode, ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("%s: %s"), *ErrorCode, *ErrorMessage));
		Bridge.Stop();
		return false;
	}

	TSharedPtr<FJsonObject> Capture = MakeShared<FJsonObject>();
	Capture->SetStringField(TEXT("schemaVersion"), TEXT("ueak.m2.fingerprint-capture/1.0"));
	Capture->SetStringField(TEXT("project"), FApp::GetProjectName());
	Capture->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
	Capture->SetStringField(TEXT("assetPath"), Preflight->GetStringField(TEXT("assetPath")));
	Capture->SetStringField(TEXT("graphGuid"), Preflight->GetStringField(TEXT("graphGuid")));
	Capture->SetStringField(TEXT("packageRevision"), Preflight->GetStringField(TEXT("packageRevision")));
	Capture->SetStringField(TEXT("graphFingerprint"), Preflight->GetStringField(TEXT("graphFingerprint")));
	Capture->SetNumberField(TEXT("nodeCount"), Preflight->GetNumberField(TEXT("nodeCount")));
	Capture->SetBoolField(TEXT("packageDirty"), Preflight->GetBoolField(TEXT("packageDirty")));
	Capture->SetBoolField(TEXT("blueprintDirty"), Blueprint->GetOutermost()->IsDirty());
	const TArray<TSharedPtr<FJsonValue>>* PreflightNodes = nullptr;
	if (Preflight->TryGetArrayField(TEXT("nodes"), PreflightNodes) && PreflightNodes != nullptr)
	{
		Capture->SetArrayField(TEXT("preflightNodes"), *PreflightNodes);
	}
	TArray<TSharedPtr<FJsonValue>> LiveNodes;
	for (const UEdGraphNode* Node : Graph->Nodes)
	{
		if (Node == nullptr)
		{
			continue;
		}
		TSharedPtr<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("guid"), Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
		Item->SetStringField(TEXT("name"), Node->GetName());
		Item->SetStringField(TEXT("class"), Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString());
		Item->SetNumberField(TEXT("x"), Node->NodePosX);
		Item->SetNumberField(TEXT("y"), Node->NodePosY);
		Item->SetNumberField(TEXT("width"), Node->NodeWidth);
		Item->SetNumberField(TEXT("height"), Node->NodeHeight);
		Item->SetNumberField(TEXT("pinCount"), Node->Pins.Num());
		LiveNodes.Add(MakeShared<FJsonValueObject>(Item));
	}
	Capture->SetArrayField(TEXT("liveNodeOrder"), LiveNodes);
	const TSharedPtr<FJsonObject>* Context = nullptr;
	if (Envelope->TryGetObjectField(TEXT("context"), Context) && Context != nullptr && Context->IsValid())
	{
		const TArray<TSharedPtr<FJsonValue>>* Graphs = nullptr;
		if ((*Context)->TryGetArrayField(TEXT("graphs"), Graphs) && Graphs != nullptr && Graphs->Num() > 0)
		{
			const TSharedPtr<FJsonObject> GraphObject = (*Graphs)[0]->AsObject();
			const TArray<TSharedPtr<FJsonValue>>* ContextNodes = nullptr;
			if (GraphObject.IsValid() && GraphObject->TryGetArrayField(TEXT("nodes"), ContextNodes) && ContextNodes != nullptr)
			{
				Capture->SetArrayField(TEXT("contextNodeOrder"), *ContextNodes);
			}
		}
	}
	const FString PackageFilename = FPackageName::LongPackageNameToFilename(
		Blueprint->GetOutermost()->GetName(), FPackageName::GetAssetPackageExtension());
	FString PackageSha;
	if (FBlueprintContextSha256::HashFile(PackageFilename, PackageSha))
	{
		Capture->SetStringField(TEXT("packageFileSha256"), FString(TEXT("sha256:")) + PackageSha.ToLower());
	}
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(CapturePath), true);
	const bool bWritten = UEAgentKitNativeM2Test::WriteJson(CapturePath, Capture, Error);
	TestTrue(TEXT("M2 fingerprint capture writes"), bWritten);
	if (!bWritten)
	{
		AddError(Error);
	}
	Bridge.Stop();
	return bWritten;
}

#endif // WITH_DEV_AUTOMATION_TESTS
