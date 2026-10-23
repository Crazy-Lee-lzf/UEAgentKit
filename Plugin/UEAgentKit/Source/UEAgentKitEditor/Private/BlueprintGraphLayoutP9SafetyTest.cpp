#include "BlueprintGraphLayoutP9TestOnly.h"

#include "BlueprintEditor.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphNode_Comment.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformTime.h"
#include "Misc/AutomationTest.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "ScopedTransaction.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "Tests/AutomationEditorCommon.h"
#include "Toolkits/IToolkitHost.h"
#include "UObject/Package.h"

#if WITH_DEV_AUTOMATION_TESTS

#define LOCTEXT_NAMESPACE "UEAgentKitGraphLayoutP9Safety"

namespace UEAgentKitGraphLayoutP9Safety
{
	namespace
	{
		const TCHAR* FixtureAssetPath = TEXT("/Game/UEAgentKitP9/BP_LayoutP9.BP_LayoutP9");

		FString GuidLower(const FGuid& Guid)
		{
			return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
		}

		FString EvidencePath(const TCHAR* FileName)
		{
			return FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), FileName);
		}

		bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& Object, FString& OutError)
		{
			if (!Object.IsValid())
			{
				OutError = TEXT("No JSON object to write.");
				return false;
			}
			IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
			FString Text;
			const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
			if (!FJsonSerializer::Serialize(Object.ToSharedRef(), Writer))
			{
				OutError = FString::Printf(TEXT("Could not serialize %s."), *Path);
				return false;
			}
			if (!FFileHelper::SaveStringToFile(
				Text,
				*Path,
				FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
			{
				OutError = FString::Printf(TEXT("Could not write %s."), *Path);
				return false;
			}
			return true;
		}

		FString StructuralState(UEdGraph* Graph)
		{
			TArray<UEdGraphNode*> Nodes;
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr)
				{
					Nodes.Add(Node);
				}
			}
			Nodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
			{
				return Left.NodeGuid.ToString(EGuidFormats::Digits)
					< Right.NodeGuid.ToString(EGuidFormats::Digits);
			});
			FString State;
			for (const UEdGraphNode* Node : Nodes)
			{
				State += Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower);
				State += TEXT("|");
				State += Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString();
				State += TEXT("|");
				State += FString::Printf(TEXT("%d|%d|"), Node->NodeWidth, Node->NodeHeight);
				TArray<FString> Pins;
				for (const UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin == nullptr)
					{
						continue;
					}
					TArray<FString> Links;
					for (const UEdGraphPin* Linked : Pin->LinkedTo)
					{
						if (Linked != nullptr && Linked->GetOwningNode() != nullptr)
						{
							Links.Add(
								Linked->GetOwningNode()->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower)
								+ TEXT("/")
								+ Linked->PinName.ToString());
						}
					}
					Links.Sort();
					Pins.Add(
						Pin->PinName.ToString()
						+ TEXT(":")
						+ Pin->PinType.PinCategory.ToString()
						+ TEXT(":")
						+ FString::FromInt(static_cast<int32>(Pin->Direction))
						+ TEXT("{")
						+ FString::Join(Links, TEXT(","))
						+ TEXT("}"));
				}
				Pins.Sort();
				State += FString::Join(Pins, TEXT(","));
				State += TEXT(";");
			}
			return State;
		}

		FString Sha1Text(const FString& Text)
		{
			FTCHARToUTF8 Utf8(*Text);
			uint8 Digest[20];
			FSHA1::HashBuffer(Utf8.Get(), static_cast<uint32>(Utf8.Length()), Digest);
			FString Hex;
			for (const uint8 Byte : Digest)
			{
				Hex += FString::Printf(TEXT("%02x"), static_cast<uint32>(Byte));
			}
			return FString(TEXT("sha1:")) + Hex;
		}

		TSharedPtr<FJsonObject> MakePlan(UEdGraph* Graph, const FString& PlanId)
		{
			TSharedRef<FJsonObject> Plan = MakeShared<FJsonObject>();
			Plan->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layoutplan/1.0"));
			Plan->SetStringField(TEXT("status"), TEXT("ok"));
			Plan->SetStringField(TEXT("errorCode"), TEXT(""));
			Plan->SetStringField(TEXT("planId"), PlanId);
			Plan->SetStringField(TEXT("baseGraphHash"), TEXT("sha256:0000000000000000000000000000000000000000000000000000000000000000"));
			Plan->SetStringField(TEXT("layoutHash"), TEXT("sha256:0000000000000000000000000000000000000000000000000000000000000000"));
			Plan->SetStringField(TEXT("mode"), TEXT("full"));
			Plan->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
			Plan->SetBoolField(TEXT("acceptanceEligible"), false);
			Plan->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
			TArray<TSharedPtr<FJsonValue>> Positions;
			TArray<TSharedPtr<FJsonValue>> Scope;
			TArray<TSharedPtr<FJsonValue>> Moved;
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node == nullptr)
				{
					continue;
				}
				TSharedRef<FJsonObject> Position = MakeShared<FJsonObject>();
				Position->SetStringField(
					TEXT("nodeId"),
					FString(TEXT("n:")) + GuidLower(Node->NodeGuid));
				Position->SetNumberField(TEXT("x"), Node->NodePosX);
				Position->SetNumberField(TEXT("y"), Node->NodePosY);
				Positions.Add(MakeShared<FJsonValueObject>(Position));
				if (Cast<UEdGraphNode_Comment>(Node) == nullptr)
				{
					Scope.Add(MakeShared<FJsonValueString>(
						FString(TEXT("n:")) + GuidLower(Node->NodeGuid)));
				}
			}
			Plan->SetArrayField(TEXT("positions"), Positions);
			Plan->SetArrayField(TEXT("movedNodeIds"), Moved);
			Plan->SetArrayField(TEXT("scopeNodeIds"), Scope);
			Plan->SetArrayField(TEXT("fixedNodeIds"), {});
			return Plan;
		}

		bool ResolveFocusedGraph(
			UBlueprint*& OutBlueprint,
			UEdGraph*& OutGraph,
			FBlueprintEditor*& OutEditor,
			FString& OutError)
		{
			OutBlueprint = LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
			if (OutBlueprint == nullptr)
			{
				OutError = TEXT("The exact P9 fixture Blueprint is not loaded.");
				return false;
			}
			OutGraph = OutBlueprint->UbergraphPages.Num() > 0
				? OutBlueprint->UbergraphPages[0]
				: nullptr;
			if (OutGraph == nullptr)
			{
				OutError = TEXT("The P9 fixture has no ubergraph.");
				return false;
			}
			if (GEditor == nullptr)
			{
				OutError = TEXT("GEditor is unavailable.");
				return false;
			}
			UAssetEditorSubsystem* Subsystem = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>();
			if (Subsystem == nullptr)
			{
				OutError = TEXT("The Asset Editor Subsystem is unavailable.");
				return false;
			}
			if (!Subsystem->OpenEditorForAsset(
				OutBlueprint,
				EToolkitMode::Standalone,
				TSharedPtr<IToolkitHost>(),
				false))
			{
				OutError = TEXT("Could not open the P9 Blueprint Editor.");
				return false;
			}
			IAssetEditorInstance* Instance = Subsystem->FindEditorForAsset(OutBlueprint, false);
			OutEditor = Instance != nullptr && Instance->GetEditorName() == FName(TEXT("BlueprintEditor"))
				? static_cast<FBlueprintEditor*>(Instance)
				: nullptr;
			if (OutEditor == nullptr)
			{
				OutError = TEXT("The P9 Blueprint Editor is unavailable.");
				return false;
			}
			if (!OutEditor->OpenGraphAndBringToFront(OutGraph, true).IsValid()
				|| OutEditor->GetFocusedGraph() != OutGraph)
			{
				OutError = TEXT("The P9 fixture graph could not be focused.");
				return false;
			}
			return true;
		}

		bool CallApply(
			UEdGraph* Graph,
			FString& OutErrorCode,
			FString& OutErrorMessage,
			TSharedPtr<FJsonObject>& OutResult)
		{
			TSharedRef<FJsonObject> Request = MakeShared<FJsonObject>();
			Request->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphlayoutapply/1.0"));
			Request->SetStringField(TEXT("assetPath"), FixtureAssetPath);
			Request->SetStringField(
				TEXT("graphGuid"),
				GuidLower(Graph->GraphGuid));
			Request->SetStringField(TEXT("requestedMode"), TEXT("position-only"));
			Request->SetBoolField(TEXT("allowSave"), false);
			Request->SetBoolField(TEXT("allowCompile"), false);
			Request->SetBoolField(TEXT("acceptanceEligible"), false);
			Request->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
			Request->SetStringField(
				TEXT("expectedPackageRevision"),
				TEXT("sha256:0000000000000000000000000000000000000000000000000000000000000000"));
			Request->SetStringField(
				TEXT("expectedGraphFingerprint"),
				TEXT("sha1:0000000000000000000000000000000000000000"));
			Request->SetObjectField(TEXT("plan"), MakePlan(Graph, TEXT("layout:p9-safety")));

			TSharedRef<FJsonObject> Params = MakeShared<FJsonObject>();
			Params->SetObjectField(TEXT("request"), Request);
			Params->SetStringField(
				TEXT("confirmation"),
				TEXT("APPLY P9 TEST-ONLY layout:p9-safety"));
			return UEAgentKitBlueprintGraphLayoutP9::TryApplyTestOnly(
				Params,
				OutResult,
				OutErrorCode,
				OutErrorMessage);
		}

		bool CheckApplyRejection(
			UEdGraph* Graph,
			const TCHAR* ExpectedCode,
			TSharedPtr<FJsonObject>& OutEvidence,
			FString& OutError)
		{
			const FString BeforeStructure = StructuralState(Graph);
			const FString BeforeTopology = Sha1Text(BeforeStructure);
			const bool bDirtyBefore = Graph->GetOutermost()->IsDirty();
			FString ErrorCode;
			FString ErrorMessage;
			TSharedPtr<FJsonObject> Result;
			const bool bApplied = CallApply(Graph, ErrorCode, ErrorMessage, Result);
			const FString AfterStructure = StructuralState(Graph);
			const bool bUnchanged = AfterStructure.Equals(BeforeStructure, ESearchCase::CaseSensitive)
				&& Graph->GetOutermost()->IsDirty() == bDirtyBefore;
			if (bApplied || !ErrorCode.Equals(ExpectedCode, ESearchCase::CaseSensitive) || !bUnchanged)
			{
				OutError = FString::Printf(
					TEXT("Expected %s, got applied=%s code=%s message=%s unchanged=%s"),
					ExpectedCode,
					bApplied ? TEXT("true") : TEXT("false"),
					*ErrorCode,
					*ErrorMessage,
					bUnchanged ? TEXT("true") : TEXT("false"));
				return false;
			}
			TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
			Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p9.safetygate/1.0"));
			Evidence->SetStringField(TEXT("assetPath"), FixtureAssetPath);
			Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
			Evidence->SetStringField(TEXT("expectedErrorCode"), ExpectedCode);
			Evidence->SetStringField(TEXT("errorCode"), ErrorCode);
			Evidence->SetStringField(TEXT("errorMessage"), ErrorMessage);
			Evidence->SetBoolField(TEXT("applied"), bApplied);
			Evidence->SetBoolField(TEXT("positionsAndStructureUnchanged"), bUnchanged);
			Evidence->SetBoolField(TEXT("packageDirtyBefore"), bDirtyBefore);
			Evidence->SetBoolField(TEXT("packageDirtyAfter"), Graph->GetOutermost()->IsDirty());
			Evidence->SetStringField(TEXT("topologyHashBefore"), BeforeTopology);
			Evidence->SetStringField(TEXT("topologyHashAfter"), Sha1Text(AfterStructure));
			Evidence->SetStringField(TEXT("savedAtUtc"), FDateTime::UtcNow().ToIso8601());
			Evidence->SetBoolField(TEXT("success"), true);
			OutEvidence = Evidence;
			return true;
		}

		struct FPieGateState
		{
			FAutomationTestBase* Test = nullptr;
			UEdGraph* Graph = nullptr;
			bool bSimulate = false;
			bool bCompleted = false;
			double StartedAt = 0.0;
		};

		class FWaitForPieThenReject final : public IAutomationLatentCommand
		{
		public:
			explicit FWaitForPieThenReject(TSharedRef<FPieGateState> InState)
				: State(InState)
			{
			}

			virtual bool Update() override
			{
				if (State->bCompleted)
				{
					return true;
				}
				if (State->StartedAt <= 0.0)
				{
					State->StartedAt = FPlatformTime::Seconds();
				}
				const bool bPlaying = GEditor != nullptr
					&& GEditor->IsPlaySessionInProgress()
					&& GEditor->PlayWorld != nullptr;
				const bool bSimulating = GEditor != nullptr && GEditor->bIsSimulatingInEditor;
				if (bPlaying && bSimulating == State->bSimulate)
				{
					TSharedPtr<FJsonObject> Evidence;
					FString Error;
					const TCHAR* Expected = TEXT("graph-layout-pie-active");
					const bool bOk = CheckApplyRejection(
						State->Graph,
						Expected,
						Evidence,
						Error);
					if (Evidence.IsValid())
					{
						Evidence->SetStringField(
							TEXT("mode"),
							State->bSimulate ? TEXT("simulate-in-editor") : TEXT("play-in-editor"));
						Evidence->SetStringField(
							TEXT("pieState"),
							State->bSimulate ? TEXT("simulating") : TEXT("playing"));
						WriteJson(
							EvidencePath(State->bSimulate
								? TEXT("P9-sie-gate-result.json")
								: TEXT("P9-pie-gate-result.json")),
							Evidence,
							Error);
					}
					if (State->Test != nullptr)
					{
						State->Test->TestTrue(
							State->bSimulate
								? TEXT("P9 SIE apply is rejected and state is unchanged")
								: TEXT("P9 PIE apply is rejected and state is unchanged"),
							bOk);
						if (!bOk)
						{
							State->Test->AddError(Error);
						}
					}
					State->bCompleted = true;
					return true;
				}
				if (FPlatformTime::Seconds() - State->StartedAt > 60.0)
				{
					if (State->Test != nullptr)
					{
						State->Test->AddError(
							State->bSimulate
								? TEXT("SIE did not start within 60 seconds; apply negative was not run.")
								: TEXT("PIE did not start within 60 seconds; apply negative was not run."));
					}
					State->bCompleted = true;
					return true;
				}
				return false;
			}

		private:
			TSharedRef<FPieGateState> State;
		};

		class FWaitForPieStop final : public IAutomationLatentCommand
		{
		public:
			explicit FWaitForPieStop(TSharedRef<FPieGateState> InState)
				: State(InState)
			{
			}

			virtual bool Update() override
			{
				return GEditor == nullptr
					|| (!GEditor->IsPlaySessionInProgress() && GEditor->PlayWorld == nullptr);
			}

		private:
			TSharedRef<FPieGateState> State;
		};

		bool RunPieGate(
			const bool bSimulate,
			FAutomationTestBase* Test)
		{
			UBlueprint* Blueprint = nullptr;
			UEdGraph* Graph = nullptr;
			FBlueprintEditor* Editor = nullptr;
			FString Error;
			if (!ResolveFocusedGraph(Blueprint, Graph, Editor, Error))
			{
				Test->AddError(Error);
				return false;
			}
			TSharedRef<FPieGateState> State = MakeShared<FPieGateState>();
			State->Test = Test;
			State->Graph = Graph;
			State->bSimulate = bSimulate;
			ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(bSimulate));
			ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieThenReject(State));
			ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
			ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieStop(State));
			return true;
		}
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP9ActiveTransactionTest,
		"UEAgentKit.GraphLayout.P9ActiveTransactionContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP9ActiveTransactionTest::RunTest(const FString& Parameters)
	{
		UBlueprint* Blueprint = nullptr;
		UEdGraph* Graph = nullptr;
		FBlueprintEditor* Editor = nullptr;
		FString Error;
		if (!ResolveFocusedGraph(Blueprint, Graph, Editor, Error))
		{
			AddError(Error);
			return false;
		}
		if (!TestFalse(TEXT("P9 active-transaction fixture starts clean"), Blueprint->GetOutermost()->IsDirty()))
		{
			return false;
		}
		if (!TestTrue(TEXT("P9 editor can transact"), GEditor != nullptr && GEditor->CanTransact()))
		{
			return false;
		}
		GEditor->BeginTransaction(LOCTEXT("P9ActiveTransactionGate", "UE Agent Kit P9 Active Transaction Gate"));
		TestTrue(TEXT("P9 active transaction is active"), GEditor->Trans != nullptr && GEditor->Trans->IsActive());
		TSharedPtr<FJsonObject> Evidence;
		const bool bOk = CheckApplyRejection(
			Graph,
			TEXT("graph-layout-transaction-active"),
			Evidence,
			Error);
		GEditor->EndTransaction();
		TestTrue(TEXT("P9 active-transaction apply is rejected and state is unchanged"), bOk);
		if (!bOk)
		{
			AddError(Error);
			return false;
		}
		Evidence->SetStringField(TEXT("mode"), TEXT("active-editor-transaction"));
		if (!TestTrue(
			TEXT("P9 active-transaction evidence is written"),
			WriteJson(EvidencePath(TEXT("P9-active-transaction-result.json")), Evidence, Error)))
		{
			AddError(Error);
			return false;
		}
		return true;
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP9PieGateTest,
		"UEAgentKit.GraphLayout.P9PieGateContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP9PieGateTest::RunTest(const FString& Parameters)
	{
		return RunPieGate(false, this);
	}

	IMPLEMENT_SIMPLE_AUTOMATION_TEST(
		FUEAgentKitGraphLayoutP9SieGateTest,
		"UEAgentKit.GraphLayout.P9SieGateContract",
		EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

	bool FUEAgentKitGraphLayoutP9SieGateTest::RunTest(const FString& Parameters)
	{
		return RunPieGate(true, this);
	}
}

#undef LOCTEXT_NAMESPACE

#endif
