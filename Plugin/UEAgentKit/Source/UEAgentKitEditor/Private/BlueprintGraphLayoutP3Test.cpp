#include "BlueprintGraphLayoutApply.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "BlueprintContextAnalysis.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "Editor.h"
#include "Editor/Transactor.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_Knot.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#endif

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitGraphLayoutP3
{
	namespace
	{
		const TCHAR* FixturePackageName = TEXT("/Game/UEAgentKitP3/BP_LayoutP3");
		const TCHAR* FixtureAssetName = TEXT("BP_LayoutP3");
		const TCHAR* FixtureAssetPath = TEXT("/Game/UEAgentKitP3/BP_LayoutP3.BP_LayoutP3");
		constexpr int32 FixtureShiftX = 320;
		constexpr int32 FixtureShiftY = 160;

		FString GuidLower(const FGuid& Guid)
		{
			return Guid.ToString(EGuidFormats::DigitsWithHyphensLower);
		}

		FString NodeId(const FGuid& Guid)
		{
			return FString(TEXT("n:")) + GuidLower(Guid);
		}

		FString EvidencePath(const TCHAR* FileName)
		{
			return FPaths::Combine(FPaths::ProjectDir(), TEXT("Evidence"), FileName);
		}

		bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
		{
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Blueprint is null.");
				return false;
			}
			UPackage* Package = Blueprint->GetOutermost();
			if (Package == nullptr)
			{
				OutError = TEXT("Blueprint has no package.");
				return false;
			}
			const FString Filename = FPackageName::LongPackageNameToFilename(
				Package->GetName(),
				FPackageName::GetAssetPackageExtension());
			IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);

			FSavePackageArgs SaveArgs;
			SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
			SaveArgs.SaveFlags = SAVE_NoError;
			SaveArgs.Error = GError;
			if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
			{
				OutError = FString::Printf(TEXT("SavePackage failed for %s."), *Filename);
				return false;
			}
			return true;
		}

		UEdGraph* GetPrimaryGraph(UBlueprint* Blueprint)
		{
			if (Blueprint == nullptr || Blueprint->UbergraphPages.Num() == 0)
			{
				return nullptr;
			}
			return Blueprint->UbergraphPages[0];
		}

		bool LinkPins(UEdGraph* Graph, UEdGraphPin* From, UEdGraphPin* To)
		{
			if (Graph == nullptr || From == nullptr || To == nullptr)
			{
				return false;
			}
			if (Graph->GetSchema() != nullptr && Graph->GetSchema()->TryCreateConnection(From, To))
			{
				return From->LinkedTo.Contains(To) || To->LinkedTo.Contains(From);
			}
			From->MakeLinkTo(To);
			return From->LinkedTo.Contains(To) || To->LinkedTo.Contains(From);
		}

		UBlueprint* CreateFixture(FString& OutError)
		{
			UPackage* Package = CreatePackage(FixturePackageName);
			if (Package == nullptr)
			{
				OutError = TEXT("Could not create the disposable fixture package.");
				return nullptr;
			}

			UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
				AActor::StaticClass(),
				Package,
				FName(FixtureAssetName),
				BPTYPE_Normal,
				FName(TEXT("UEAgentKitP3Fixture")));
			if (Blueprint == nullptr)
			{
				OutError = TEXT("Could not create the disposable K2 Blueprint.");
				return nullptr;
			}

			UEdGraph* Graph = GetPrimaryGraph(Blueprint);
			if (Graph == nullptr)
			{
				OutError = TEXT("The disposable Blueprint has no ubergraph.");
				return nullptr;
			}

			auto CreateSequence = [Graph](const int32 X, const int32 Y) -> UK2Node_ExecutionSequence*
			{
				FGraphNodeCreator<UK2Node_ExecutionSequence> Creator(*Graph);
				UK2Node_ExecutionSequence* Node = Creator.CreateNode(false);
				Node->NodePosX = X;
				Node->NodePosY = Y;
				Creator.Finalize();
				Node->NodeWidth = 180;
				Node->NodeHeight = 120;
				Node->NodeComment = TEXT("P3 disposable execution sequence");
				return Node;
			};

			UK2Node_ExecutionSequence* First = CreateSequence(-240, 0);
			UK2Node_ExecutionSequence* Second = CreateSequence(20, 0);
			UK2Node_ExecutionSequence* Third = CreateSequence(420, 0);
			if (First == nullptr || Second == nullptr || Third == nullptr)
			{
				OutError = TEXT("Could not create execution sequence nodes.");
				return nullptr;
			}

			FGraphNodeCreator<UK2Node_Knot> KnotCreator(*Graph);
			UK2Node_Knot* Knot = KnotCreator.CreateNode(false);
			Knot->NodePosX = 230;
			Knot->NodePosY = 80;
			KnotCreator.Finalize();
			Knot->NodeWidth = 30;
			Knot->NodeHeight = 30;

			UEdGraphPin* FirstThen = First->GetThenPinGivenIndex(0);
			UEdGraphPin* SecondExecute = Second->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			UEdGraphPin* SecondThen = Second->GetThenPinGivenIndex(0);
			UEdGraphPin* ThirdExecute = Third->FindPin(UEdGraphSchema_K2::PN_Execute, EGPD_Input);
			if (!LinkPins(Graph, FirstThen, SecondExecute)
				|| !LinkPins(Graph, SecondThen, Knot->GetInputPin())
				|| !LinkPins(Graph, Knot->GetOutputPin(), ThirdExecute))
			{
				OutError = TEXT("Could not create the execution/reroute link chain.");
				return nullptr;
			}

			FGraphNodeCreator<UEdGraphNode_Comment> CommentCreator(*Graph);
			UEdGraphNode_Comment* Comment = CommentCreator.CreateNode(false);
			Comment->NodePosX = -320;
			Comment->NodePosY = -160;
			CommentCreator.Finalize();
			Comment->NodeWidth = 1120;
			Comment->NodeHeight = 620;
			Comment->NodeComment = TEXT("P3 disposable comment fixture");

			FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
			FKismetEditorUtilities::CompileBlueprint(
				Blueprint,
				EBlueprintCompileOptions::SkipGarbageCollection);
			if (Blueprint->Status == BS_Error)
			{
				OutError = TEXT("The disposable fixture Blueprint failed to compile.");
				return nullptr;
			}

			FAssetRegistryModule::AssetCreated(Blueprint);
			Package->MarkPackageDirty();
			if (!SaveBlueprint(Blueprint, OutError))
			{
				return nullptr;
			}
			return Blueprint;
		}

		UBlueprint* LoadFixture()
		{
			return LoadObject<UBlueprint>(nullptr, FixtureAssetPath);
		}

		bool WriteJson(
			const TCHAR* FileName,
			const TSharedPtr<FJsonObject>& Object,
			FString& OutError)
		{
			if (!Object.IsValid())
			{
				OutError = TEXT("No JSON object to write.");
				return false;
			}
			const FString Path = EvidencePath(FileName);
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

		TSharedPtr<FJsonObject> ReadJson(const TCHAR* FileName, FString& OutError)
		{
			const FString Path = EvidencePath(FileName);
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

		const TArray<TSharedPtr<FJsonValue>>* GetPreflightNodes(const TSharedPtr<FJsonObject>& Preflight)
		{
			const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
			if (Preflight.IsValid())
			{
				Preflight->TryGetArrayField(TEXT("nodes"), Nodes);
			}
			return Nodes;
		}

		TSharedPtr<FJsonObject> FindPreflightNode(
			const TSharedPtr<FJsonObject>& Preflight,
			const FGuid& Guid)
		{
			const TArray<TSharedPtr<FJsonValue>>* Nodes = GetPreflightNodes(Preflight);
			if (Nodes == nullptr)
			{
				return nullptr;
			}
			const FString Wanted = GuidLower(Guid);
			for (const TSharedPtr<FJsonValue>& Entry : *Nodes)
			{
				if (!Entry.IsValid() || Entry->Type != EJson::Object)
				{
					continue;
				}
				const TSharedPtr<FJsonObject> Node = Entry->AsObject();
				FString Candidate;
				if (Node->TryGetStringField(TEXT("guid"), Candidate) && Candidate == Wanted)
				{
					return Node;
				}
			}
			return nullptr;
		}

		TMap<FGuid, UEdGraphNode*> MapLiveNodes(UEdGraph* Graph)
		{
			TMap<FGuid, UEdGraphNode*> Result;
			if (Graph == nullptr)
			{
				return Result;
			}
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr)
				{
					Result.Add(Node->NodeGuid, Node);
				}
			}
			return Result;
		}

		TSharedPtr<FJsonObject> BuildShiftedPlan(
			const TSharedPtr<FJsonObject>& Preflight,
			FString& OutError)
		{
			const TArray<TSharedPtr<FJsonValue>>* Nodes = GetPreflightNodes(Preflight);
			if (Nodes == nullptr || Nodes->IsEmpty())
			{
				OutError = TEXT("Preflight has no nodes.");
				return nullptr;
			}

			TSharedRef<FJsonObject> Plan = MakeShared<FJsonObject>();
			Plan->SetStringField(TEXT("schemaVersion"), TEXT("ueak.layoutplan/1.0"));
			Plan->SetStringField(TEXT("planId"), TEXT("layout:p3-fixture-shift"));
			Plan->SetStringField(
				TEXT("baseGraphHash"),
				TEXT("sha256:1111111111111111111111111111111111111111111111111111111111111111"));
			Plan->SetStringField(TEXT("mode"), TEXT("full"));
			Plan->SetStringField(TEXT("status"), TEXT("ok"));
			Plan->SetStringField(TEXT("errorCode"), TEXT(""));
			Plan->SetStringField(TEXT("evidenceLevel"), TEXT("same-process"));
			Plan->SetBoolField(TEXT("acceptanceEligible"), false);
			Plan->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));

			TArray<TSharedPtr<FJsonValue>> Positions;
			TArray<TSharedPtr<FJsonValue>> Moved;
			TArray<TSharedPtr<FJsonValue>> Scope;
			for (const TSharedPtr<FJsonValue>& Entry : *Nodes)
			{
				if (!Entry.IsValid() || Entry->Type != EJson::Object)
				{
					OutError = TEXT("Preflight contains a non-object node.");
					return nullptr;
				}
				const TSharedPtr<FJsonObject> Node = Entry->AsObject();
				FString NodeText;
				FString Kind;
				if (!Node->TryGetStringField(TEXT("nodeId"), NodeText)
					|| !Node->TryGetStringField(TEXT("kind"), Kind))
				{
					OutError = TEXT("Preflight node is missing nodeId/kind.");
					return nullptr;
				}
				const int32 X = static_cast<int32>(Node->GetNumberField(TEXT("x")));
				const int32 Y = static_cast<int32>(Node->GetNumberField(TEXT("y")));
				const int32 Width = static_cast<int32>(Node->GetNumberField(TEXT("width")));
				const int32 Height = static_cast<int32>(Node->GetNumberField(TEXT("height")));

				TSharedRef<FJsonObject> Position = MakeShared<FJsonObject>();
				Position->SetStringField(TEXT("nodeId"), NodeText);
				Position->SetNumberField(TEXT("x"), X + FixtureShiftX);
				Position->SetNumberField(TEXT("y"), Y + FixtureShiftY);
				TSharedRef<FJsonObject> Size = MakeShared<FJsonObject>();
				Size->SetNumberField(TEXT("width"), Width);
				Size->SetNumberField(TEXT("height"), Height);
				Size->SetStringField(TEXT("source"), TEXT("stored"));
				Position->SetObjectField(TEXT("size"), Size);
				Positions.Add(MakeShared<FJsonValueObject>(Position));

				Moved.Add(MakeShared<FJsonValueString>(NodeText));
				if (!Kind.Equals(TEXT("comment"), ESearchCase::CaseSensitive))
				{
					Scope.Add(MakeShared<FJsonValueString>(NodeText));
				}
			}

			Plan->SetArrayField(TEXT("positions"), Positions);
			Plan->SetArrayField(TEXT("movedNodeIds"), Moved);
			Plan->SetArrayField(TEXT("scopeNodeIds"), Scope);
			Plan->SetArrayField(TEXT("fixedNodeIds"), TArray<TSharedPtr<FJsonValue>>());

			FString LayoutHash;
			if (!UEAgentKitBlueprintGraphLayout::ComputeLayoutHash(Plan, LayoutHash))
			{
				OutError = TEXT("Could not compute the fixture layout hash.");
				return nullptr;
			}
			Plan->SetStringField(TEXT("layoutHash"), LayoutHash);
			return Plan;
		}

		TSharedPtr<FJsonObject> BuildApplyRequest(
			const TSharedPtr<FJsonObject>& Preflight,
			const TSharedPtr<FJsonObject>& Plan)
		{
			TSharedRef<FJsonObject> Request = MakeShared<FJsonObject>();
			Request->SetStringField(TEXT("schemaVersion"), TEXT("ueak.graphlayoutapply/1.0"));
			Request->SetStringField(TEXT("assetPath"), Preflight->GetStringField(TEXT("assetPath")));
			Request->SetStringField(TEXT("graphGuid"), Preflight->GetStringField(TEXT("graphGuid")));
			Request->SetStringField(TEXT("requestedMode"), TEXT("position-only"));
			Request->SetBoolField(TEXT("allowSave"), false);
			Request->SetBoolField(TEXT("allowCompile"), false);
			Request->SetBoolField(TEXT("acceptanceEligible"), false);
			Request->SetStringField(TEXT("requiredAcceptanceEvidence"), TEXT("independent-reload"));
			Request->SetStringField(
				TEXT("expectedPackageRevision"),
				Preflight->GetStringField(TEXT("packageRevision")));
			Request->SetStringField(
				TEXT("expectedGraphFingerprint"),
				Preflight->GetStringField(TEXT("graphFingerprint")));
			Request->SetObjectField(TEXT("plan"), Plan);
			return Request;
		}

		TSharedPtr<FJsonObject> CloneObject(const TSharedPtr<FJsonObject>& Source)
		{
			TSharedRef<FJsonObject> Copy = MakeShared<FJsonObject>();
			if (Source.IsValid())
			{
				Copy->Values = Source->Values;
			}
			return Copy;
		}

		bool ExpectFailure(
			FAutomationTestBase& Test,
			const TCHAR* Label,
			const bool bSucceeded,
			const FString& ActualCode,
			const TCHAR* ExpectedCode)
		{
			Test.TestFalse(Label, bSucceeded);
			if (bSucceeded)
			{
				return false;
			}
			Test.TestEqual(
				*FString::Printf(TEXT("%s error code"), Label),
				ActualCode,
				FString(ExpectedCode));
			return ActualCode.Equals(ExpectedCode, ESearchCase::CaseSensitive);
		}

		TArray<TSharedPtr<FJsonValue>> MakePositionArray(
			const TMap<FGuid, FIntPoint>& Positions)
		{
			TArray<TSharedPtr<FJsonValue>> Result;
			for (const TPair<FGuid, FIntPoint>& Pair : Positions)
			{
				TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
				Item->SetStringField(TEXT("nodeGuid"), GuidLower(Pair.Key));
				Item->SetNumberField(TEXT("x"), Pair.Value.X);
				Item->SetNumberField(TEXT("y"), Pair.Value.Y);
				Result.Add(MakeShared<FJsonValueObject>(Item));
			}
			return Result;
		}

		/**
		 * Position-free structural state.  Used to prove the apply transaction
		 * changes NodePosX/NodePosY and nothing else: GUIDs, classes, comments,
		 * pin identities, pin types, sizes and links must all stay identical.
		 */
		FString StructuralState(UEdGraph* Graph)
		{
			TArray<UEdGraphNode*> SortedNodes;
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node != nullptr)
				{
					SortedNodes.Add(Node);
				}
			}
			SortedNodes.Sort([](const UEdGraphNode& Left, const UEdGraphNode& Right)
			{
				return Left.NodeGuid.ToString(EGuidFormats::Digits)
					< Right.NodeGuid.ToString(EGuidFormats::Digits);
			});

			FString State;
			for (const UEdGraphNode* Node : SortedNodes)
			{
				State += GuidLower(Node->NodeGuid);
				State += TEXT("|");
				State += Node->GetClass() != nullptr ? Node->GetClass()->GetPathName() : FString();
				State += TEXT("|");
				State += Node->NodeComment;
				State += TEXT("|");
				State += FString::FromInt(Node->NodeWidth);
				State += TEXT("x");
				State += FString::FromInt(Node->NodeHeight);
				State += TEXT("|");

				TArray<UEdGraphPin*> Pins;
				for (UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin != nullptr)
					{
						Pins.Add(Pin);
					}
				}
				Pins.Sort([](const UEdGraphPin& Left, const UEdGraphPin& Right)
				{
					return Left.PinId.ToString(EGuidFormats::Digits)
						< Right.PinId.ToString(EGuidFormats::Digits);
				});
				for (const UEdGraphPin* Pin : Pins)
				{
					State += Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower);
					State += TEXT(":");
					State += Pin->PinName.ToString();
					State += TEXT(":");
					State += Pin->Direction == EGPD_Input ? TEXT("in") : TEXT("out");
					State += TEXT(":");
					State += Pin->PinType.PinCategory.ToString();
					State += TEXT(":");
					State += Pin->PinType.PinSubCategory.ToString();
					State += TEXT(":");

					TArray<FString> Links;
					for (const UEdGraphPin* Linked : Pin->LinkedTo)
					{
						if (Linked != nullptr && Linked->GetOwningNode() != nullptr)
						{
							Links.Add(
								GuidLower(Linked->GetOwningNode()->NodeGuid)
								+ TEXT("/")
								+ Linked->PinName.ToString());
						}
					}
					Links.Sort();
					for (const FString& Link : Links)
					{
						State += Link;
						State += TEXT(",");
					}
					State += TEXT(";");
				}
				State += TEXT("||");
			}
			return State;
		}
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutPreflightContractTest,
	"UEAgentKit.GraphLayout.PreflightContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutPreflightContractTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace UEAgentKitGraphLayoutP3;

	FString Error;
	bool bFixtureCreated = false;
	UBlueprint* Blueprint = LoadFixture();
	if (Blueprint == nullptr)
	{
		Blueprint = CreateFixture(Error);
		bFixtureCreated = true;
	}
	if (!TestNotNull(TEXT("disposable fixture Blueprint exists"), Blueprint))
	{
		AddError(Error);
		return false;
	}
	UEdGraph* Graph = GetPrimaryGraph(Blueprint);
	if (!TestNotNull(TEXT("disposable fixture has an ubergraph"), Graph))
	{
		return false;
	}
	TestTrue(TEXT("fixture graph GUID is valid"), Graph->GraphGuid.IsValid());
	TestTrue(TEXT("fixture graph is editable"), Graph->bEditable);
	TestTrue(
		TEXT("fixture graph uses the K2 schema"),
		Cast<UEdGraphSchema_K2>(Graph->GetSchema()) != nullptr);
	TestTrue(TEXT("fixture package is clean after save"), !Blueprint->GetOutermost()->IsDirty());

	FString ErrorCode;
	FString ErrorMessage;
	TSharedPtr<FJsonObject> Preflight;
	if (!TestTrue(
		TEXT("BuildPreflight succeeds on the live disposable Blueprint"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			Preflight,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("BuildPreflight failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}

	TestEqual(
		TEXT("preflight schema"),
		Preflight->GetStringField(TEXT("schemaVersion")),
		FString(TEXT("ueak.graphlayoutpreflight/1.0")));
	TestEqual(
		TEXT("preflight asset path is exact"),
		Preflight->GetStringField(TEXT("assetPath")),
		Blueprint->GetPathName());
	TestEqual(
		TEXT("preflight graph GUID is exact"),
		Preflight->GetStringField(TEXT("graphGuid")),
		GuidLower(Graph->GraphGuid));
	TestEqual(
		TEXT("preflight graph is the K2 ubergraph"),
		Preflight->GetStringField(TEXT("graphClassPath")),
		FString(TEXT("/Script/Engine.EdGraph")));
	TestEqual(
		TEXT("preflight schema class is K2"),
		Preflight->GetStringField(TEXT("schemaClassPath")),
		FString(TEXT("/Script/BlueprintGraph.EdGraphSchema_K2")));

	const FString Revision = Preflight->GetStringField(TEXT("packageRevision"));
	TestEqual(TEXT("package revision is sha256"), Revision.Len(), 71);
	TestTrue(TEXT("package revision uses sha256 prefix"), Revision.StartsWith(TEXT("sha256:")));
	const FString Fingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
	TestEqual(TEXT("graph fingerprint is sha1"), Fingerprint.Len(), 45);
	TestTrue(TEXT("graph fingerprint uses sha1 prefix"), Fingerprint.StartsWith(TEXT("sha1:")));
	TestEqual(
		TEXT("preflight reports a clean package"),
		Preflight->GetBoolField(TEXT("packageDirty")),
		false);
	TestEqual(
		TEXT("preflight is same-process evidence"),
		Preflight->GetStringField(TEXT("evidenceLevel")),
		FString(TEXT("same-process")));
	TestFalse(
		TEXT("preflight is not acceptance eligible"),
		Preflight->GetBoolField(TEXT("acceptanceEligible")));
	TestEqual(
		TEXT("preflight requires independent reload"),
		Preflight->GetStringField(TEXT("requiredAcceptanceEvidence")),
		FString(TEXT("independent-reload")));

	const TSharedRef<FJsonObject> AssetRevision =
		FBlueprintContextAnalysis::BuildAssetRevision(Blueprint);
	FString ContentSha256;
	AssetRevision->TryGetStringField(TEXT("contentSha256"), ContentSha256);
	TestEqual(
		TEXT("preflight revision is the exact on-disk package hash"),
		Revision,
		FString(TEXT("sha256:")) + ContentSha256);

	const TArray<TSharedPtr<FJsonValue>>* Nodes = GetPreflightNodes(Preflight);
	if (!TestNotNull(TEXT("preflight node array exists"), Nodes))
	{
		return false;
	}
	const int32 NodeCount = static_cast<int32>(Preflight->GetNumberField(TEXT("nodeCount")));
	TestEqual(TEXT("node count matches live graph"), NodeCount, Graph->Nodes.Num());
	TestEqual(TEXT("node array length matches nodeCount"), Nodes->Num(), NodeCount);
	TestTrue(
		TEXT("fixture has at least one comment"),
		static_cast<int32>(Preflight->GetNumberField(TEXT("commentCount"))) >= 1);
	TestTrue(
		TEXT("fixture has at least one reroute"),
		static_cast<int32>(Preflight->GetNumberField(TEXT("rerouteCount"))) >= 1);

	const TMap<FGuid, UEdGraphNode*> LiveNodes = MapLiveNodes(Graph);
	TestEqual(TEXT("live node map covers graph"), LiveNodes.Num(), NodeCount);

	TSet<FGuid> SeenNodeGuids;
	TSet<FGuid> SeenPinIds;
	int32 CommentCount = 0;
	int32 RerouteCount = 0;
	int32 SequenceCount = 0;
	for (const TSharedPtr<FJsonValue>& Entry : *Nodes)
	{
		if (!Entry.IsValid() || Entry->Type != EJson::Object)
		{
			AddError(TEXT("preflight node entry is not an object"));
			return false;
		}
		const TSharedPtr<FJsonObject> NodeObject = Entry->AsObject();
		FString NodeText;
		FString GuidText;
		FString Kind;
		FString ClassPath;
		if (!NodeObject->TryGetStringField(TEXT("nodeId"), NodeText)
			|| !NodeObject->TryGetStringField(TEXT("guid"), GuidText)
			|| !NodeObject->TryGetStringField(TEXT("kind"), Kind)
			|| !NodeObject->TryGetStringField(TEXT("classPath"), ClassPath))
		{
			AddError(TEXT("preflight node is missing nodeId/guid/kind/classPath"));
			return false;
		}
		TestEqual(TEXT("nodeId prefixes the guid"), NodeText, FString(TEXT("n:")) + GuidText);
		FGuid Guid;
		TestTrue(TEXT("preflight guid parses"), FGuid::Parse(GuidText, Guid));
		TestEqual(
			TEXT("preflight guid is canonical lowercase"),
			GuidText,
			GuidLower(Guid));
		TestFalse(TEXT("preflight node guid is unique"), SeenNodeGuids.Contains(Guid));
		SeenNodeGuids.Add(Guid);

		UEdGraphNode* const* LiveNode = LiveNodes.Find(Guid);
		if (!TestNotNull(TEXT("preflight node exists on the live graph"), LiveNode))
		{
			return false;
		}
		TestEqual(
			TEXT("preflight x matches live node"),
			static_cast<int32>(NodeObject->GetNumberField(TEXT("x"))),
			(*LiveNode)->NodePosX);
		TestEqual(
			TEXT("preflight y matches live node"),
			static_cast<int32>(NodeObject->GetNumberField(TEXT("y"))),
			(*LiveNode)->NodePosY);
		TestEqual(
			TEXT("preflight class path matches live node"),
			ClassPath,
			(*LiveNode)->GetClass()->GetPathName());

		if (Kind.Equals(TEXT("comment"), ESearchCase::CaseSensitive))
		{
			++CommentCount;
			TestTrue(TEXT("comment kind maps to comment class"), (*LiveNode)->IsA<UEdGraphNode_Comment>());
			TestEqual(TEXT("comment has no pins"), (*LiveNode)->Pins.Num(), 0);
		}
		else if (Kind.Equals(TEXT("reroute"), ESearchCase::CaseSensitive))
		{
			++RerouteCount;
			TestTrue(TEXT("reroute kind maps to knot class"), (*LiveNode)->IsA<UK2Node_Knot>());
		}
		else
		{
			TestEqual(TEXT("ordinary node kind"), Kind, FString(TEXT("node")));
		}
		if ((*LiveNode)->IsA<UK2Node_ExecutionSequence>())
		{
			++SequenceCount;
		}

		for (UEdGraphPin* Pin : (*LiveNode)->Pins)
		{
			if (!TestNotNull(TEXT("node pin is non-null"), Pin))
			{
				continue;
			}
			TestTrue(TEXT("node pin has a generated PinId"), Pin->PinId.IsValid());
			TestFalse(TEXT("node PinId is unique"), SeenPinIds.Contains(Pin->PinId));
			SeenPinIds.Add(Pin->PinId);
		}
	}
	TestEqual(TEXT("preflight comment count"), CommentCount, 1);
	TestEqual(TEXT("preflight reroute count"), RerouteCount, 1);
	TestEqual(TEXT("fixture sequence count"), SequenceCount, 3);

	UK2Node_Knot* Knot = nullptr;
	for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
	{
		if (UK2Node_Knot* Candidate = Cast<UK2Node_Knot>(Pair.Value))
		{
			Knot = Candidate;
			break;
		}
	}
	if (!TestNotNull(TEXT("fixture reroute knot exists"), Knot))
	{
		return false;
	}
	TestEqual(TEXT("knot has exactly two pins"), Knot->Pins.Num(), 2);
	UEdGraphPin* KnotInput = Knot->GetInputPin();
	UEdGraphPin* KnotOutput = Knot->GetOutputPin();
	if (!TestNotNull(TEXT("knot input pin"), KnotInput)
		|| !TestNotNull(TEXT("knot output pin"), KnotOutput))
	{
		return false;
	}
	TestEqual(TEXT("knot input has one exec link"), KnotInput->LinkedTo.Num(), 1);
	TestEqual(TEXT("knot output has one exec link"), KnotOutput->LinkedTo.Num(), 1);
	TestTrue(
		TEXT("knot is fed by an execution sequence"),
		KnotInput->LinkedTo.Num() == 1
			&& KnotInput->LinkedTo[0]->GetOwningNode()->IsA<UK2Node_ExecutionSequence>());
	TestTrue(
		TEXT("knot feeds an execution sequence"),
		KnotOutput->LinkedTo.Num() == 1
			&& KnotOutput->LinkedTo[0]->GetOwningNode()->IsA<UK2Node_ExecutionSequence>());

	// Fingerprint stability and sensitivity: a one-pixel in-memory move must
	// change the live fingerprint, and restoring it must restore the exact hash.
	TSharedPtr<FJsonObject> FingerprintAgain;
	FString IgnoredCode;
	FString IgnoredMessage;
	TestTrue(
		TEXT("second preflight succeeds"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			FingerprintAgain,
			IgnoredCode,
			IgnoredMessage));
	TestEqual(
		TEXT("graph fingerprint is stable across preflights"),
		FingerprintAgain->GetStringField(TEXT("graphFingerprint")),
		Fingerprint);

	UEdGraphNode* ProbeNode = nullptr;
	for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
	{
		if (!Pair.Value->IsA<UEdGraphNode_Comment>())
		{
			ProbeNode = Pair.Value;
			break;
		}
	}
	if (!TestNotNull(TEXT("fingerprint probe node exists"), ProbeNode))
	{
		return false;
	}
	const int32 ProbeX = ProbeNode->NodePosX;
	ProbeNode->NodePosX = ProbeX + 1;
	TSharedPtr<FJsonObject> ChangedPreflight;
	TestTrue(
		TEXT("preflight succeeds after an in-memory probe move"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			ChangedPreflight,
			IgnoredCode,
			IgnoredMessage));
	TestTrue(
		TEXT("one-pixel move changes the graph fingerprint"),
		ChangedPreflight->GetStringField(TEXT("graphFingerprint")) != Fingerprint);
	ProbeNode->NodePosX = ProbeX;
	TSharedPtr<FJsonObject> RestoredPreflight;
	TestTrue(
		TEXT("preflight succeeds after restoring the probe"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			RestoredPreflight,
			IgnoredCode,
			IgnoredMessage));
	TestEqual(
		TEXT("restoring the probe restores the exact fingerprint"),
		RestoredPreflight->GetStringField(TEXT("graphFingerprint")),
		Fingerprint);
	TestFalse(
		TEXT("in-memory probe did not leave the package dirty"),
		RestoredPreflight->GetBoolField(TEXT("packageDirty")));

	UPackage* Package = Blueprint->GetOutermost();
	Package->SetDirtyFlag(true);
	TSharedPtr<FJsonObject> DirtyPreflight;
	TestTrue(
		TEXT("preflight succeeds while the package is dirty"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			DirtyPreflight,
			IgnoredCode,
			IgnoredMessage));
	TestTrue(
		TEXT("preflight reports a dirty package"),
		DirtyPreflight->GetBoolField(TEXT("packageDirty")));
	Package->SetDirtyFlag(false);

	// Negative identity gates: missing objects, invalid GUID, and a graph that
	// is not one of this Blueprint's graph objects.
	TSharedPtr<FJsonObject> IgnoredPreflight;
	TestTrue(
		TEXT("null Blueprint is rejected"),
		ExpectFailure(
			*this,
			TEXT("null Blueprint preflight fails"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				nullptr,
				Graph,
				IgnoredPreflight,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-graph-unavailable")));
	TestTrue(
		TEXT("null graph is rejected"),
		ExpectFailure(
			*this,
			TEXT("null graph preflight fails"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				nullptr,
				IgnoredPreflight,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-graph-unavailable")));

	UEdGraph* InvalidGuidGraph = NewObject<UEdGraph>(GetTransientPackage());
	InvalidGuidGraph->GraphGuid.Invalidate();
	TestTrue(
		TEXT("invalid graph GUID is rejected"),
		ExpectFailure(
			*this,
			TEXT("invalid graph GUID preflight fails"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				InvalidGuidGraph,
				IgnoredPreflight,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-graph-unavailable")));

	UEdGraph* ForeignGraph = NewObject<UEdGraph>(GetTransientPackage());
	TestTrue(TEXT("foreign graph has a valid GUID"), ForeignGraph->GraphGuid.IsValid());
	TestTrue(
		TEXT("foreign graph is rejected"),
		ExpectFailure(
			*this,
			TEXT("foreign graph preflight fails"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				Blueprint,
				ForeignGraph,
				IgnoredPreflight,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-graph-mismatch")));

	// A /Game Blueprint with no on-disk package must fail closed with a
	// revision-unavailable error, never a fabricated sha256 revision.
	UPackage* UnsavedPackage = CreatePackage(TEXT("/Game/UEAgentKitP3/BP_UnsavedP3"));
	UBlueprint* UnsavedBlueprint = FKismetEditorUtilities::CreateBlueprint(
		AActor::StaticClass(),
		UnsavedPackage,
		FName(TEXT("BP_UnsavedP3")),
		BPTYPE_Normal,
		FName(TEXT("UEAgentKitP3Unsaved")));
	if (!TestNotNull(TEXT("unsaved probe Blueprint exists"), UnsavedBlueprint))
	{
		return false;
	}
	UEdGraph* UnsavedGraph = GetPrimaryGraph(UnsavedBlueprint);
	if (!TestNotNull(TEXT("unsaved probe has an ubergraph"), UnsavedGraph))
	{
		return false;
	}
	TestTrue(
		TEXT("unsaved package fails closed on revision"),
		ExpectFailure(
			*this,
			TEXT("unsaved Blueprint preflight fails"),
			UEAgentKitBlueprintGraphLayout::BuildPreflight(
				UnsavedBlueprint,
				UnsavedGraph,
				IgnoredPreflight,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-revision-unavailable")));

	TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.PreflightContract"));
	Evidence->SetBoolField(TEXT("fixtureCreated"), bFixtureCreated);
	Evidence->SetObjectField(TEXT("preflight"), Preflight);
	if (!TestTrue(
		TEXT("preflight evidence JSON is written"),
		WriteJson(TEXT("preflight.json"), Evidence, Error)))
	{
		AddError(Error);
		return false;
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutApplyContractTest,
	"UEAgentKit.GraphLayout.ApplyContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutApplyContractTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace UEAgentKitGraphLayoutP3;

	UBlueprint* Blueprint = LoadFixture();
	if (!TestNotNull(TEXT("disposable fixture Blueprint loads"), Blueprint))
	{
		return false;
	}
	UEdGraph* Graph = GetPrimaryGraph(Blueprint);
	if (!TestNotNull(TEXT("disposable fixture has an ubergraph"), Graph))
	{
		return false;
	}
	TestTrue(
		TEXT("fixture graph uses the K2 schema"),
		Cast<UEdGraphSchema_K2>(Graph->GetSchema()) != nullptr);
	TestFalse(TEXT("fixture package starts clean"), Blueprint->GetOutermost()->IsDirty());

	FString Error;
	const TSharedPtr<FJsonObject> StoredEvidence = ReadJson(TEXT("preflight.json"), Error);
	if (!TestTrue(TEXT("stored preflight evidence exists"), StoredEvidence.IsValid()))
	{
		AddError(Error);
		return false;
	}
	const TSharedPtr<FJsonObject>* StoredPreflightPtr = nullptr;
	if (!StoredEvidence->TryGetObjectField(TEXT("preflight"), StoredPreflightPtr)
		|| StoredPreflightPtr == nullptr)
	{
		AddError(TEXT("stored preflight evidence has no preflight object"));
		return false;
	}
	const TSharedPtr<FJsonObject> StoredPreflight = *StoredPreflightPtr;

	FString ErrorCode;
	FString ErrorMessage;
	TSharedPtr<FJsonObject> Preflight;
	if (!TestTrue(
		TEXT("live preflight succeeds before apply"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			Preflight,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("BuildPreflight failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	const FString PreRevision = Preflight->GetStringField(TEXT("packageRevision"));
	const FString PreFingerprint = Preflight->GetStringField(TEXT("graphFingerprint"));
	TestEqual(
		TEXT("stored and live revision match"),
		StoredPreflight->GetStringField(TEXT("packageRevision")),
		PreRevision);
	TestEqual(
		TEXT("stored and live fingerprint match"),
		StoredPreflight->GetStringField(TEXT("graphFingerprint")),
		PreFingerprint);

	const TMap<FGuid, UEdGraphNode*> LiveNodes = MapLiveNodes(Graph);
	TMap<FGuid, FIntPoint> BeforePositions;
	for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
	{
		BeforePositions.Add(Pair.Key, FIntPoint(Pair.Value->NodePosX, Pair.Value->NodePosY));
	}

	const TSharedPtr<FJsonObject> Plan = BuildShiftedPlan(Preflight, Error);
	if (!TestTrue(TEXT("fixture layout plan is built"), Plan.IsValid()))
	{
		AddError(Error);
		return false;
	}
	FString RecomputedHash;
	TestTrue(
		TEXT("fixture layout hash recomputes"),
		UEAgentKitBlueprintGraphLayout::ComputeLayoutHash(Plan, RecomputedHash));
	TestEqual(
		TEXT("fixture layout hash matches the plan"),
		Plan->GetStringField(TEXT("layoutHash")),
		RecomputedHash);

	TSharedPtr<FJsonObject> Request = BuildApplyRequest(Preflight, Plan);
	TSharedPtr<FJsonObject> Result;
	const FString StructuralBefore = StructuralState(Graph);
	const auto PositionsUnchanged = [&LiveNodes, &BeforePositions]()
	{
		for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
		{
			const FIntPoint* Before = BeforePositions.Find(Pair.Key);
			if (Before == nullptr
				|| Pair.Value->NodePosX != Before->X
				|| Pair.Value->NodePosY != Before->Y)
			{
				return false;
			}
		}
		return true;
	};

	// Negative gates must reject without changing any coordinate.
	TSharedPtr<FJsonObject> WrongRevision = CloneObject(Request);
	WrongRevision->SetStringField(
		TEXT("expectedPackageRevision"),
		TEXT("sha256:0000000000000000000000000000000000000000000000000000000000000000"));
	if (!TestTrue(
		TEXT("wrong package revision is rejected"),
		ExpectFailure(
			*this,
			TEXT("wrong revision apply fails"),
			UEAgentKitBlueprintGraphLayout::ApplyToGraph(
				Blueprint,
				Graph,
				WrongRevision,
				Result,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-revision-conflict"))))
	{
		return false;
	}
	TestTrue(TEXT("wrong revision changed no position"), PositionsUnchanged());

	TSharedPtr<FJsonObject> WrongFingerprint = CloneObject(Request);
	WrongFingerprint->SetStringField(
		TEXT("expectedGraphFingerprint"),
		TEXT("sha1:0000000000000000000000000000000000000000"));
	if (!TestTrue(
		TEXT("wrong graph fingerprint is rejected"),
		ExpectFailure(
			*this,
			TEXT("wrong fingerprint apply fails"),
			UEAgentKitBlueprintGraphLayout::ApplyToGraph(
				Blueprint,
				Graph,
				WrongFingerprint,
				Result,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-fingerprint-conflict"))))
	{
		return false;
	}
	TestTrue(TEXT("wrong fingerprint changed no position"), PositionsUnchanged());

	UPackage* Package = Blueprint->GetOutermost();
	Package->SetDirtyFlag(true);
	const bool bDirtyRejected = ExpectFailure(
		*this,
		TEXT("dirty package apply fails"),
		UEAgentKitBlueprintGraphLayout::ApplyToGraph(
			Blueprint,
			Graph,
			Request,
			Result,
			ErrorCode,
			ErrorMessage),
		ErrorCode,
		TEXT("graph-layout-package-dirty"));
	Package->SetDirtyFlag(false);
	if (!TestTrue(TEXT("dirty package is rejected"), bDirtyRejected))
	{
		return false;
	}
	TestTrue(TEXT("dirty rejection changed no position"), PositionsUnchanged());

	const int32 BlockingTransaction = GEditor->BeginTransaction(
		FText::FromString(TEXT("P3 blocking transaction")));
	TestTrue(TEXT("blocking transaction started"), BlockingTransaction >= 0);
	TestTrue(TEXT("blocking transaction is active"), GEditor->Trans->IsActive());
	const bool bActiveRejected = ExpectFailure(
		*this,
		TEXT("apply while another transaction is active fails"),
		UEAgentKitBlueprintGraphLayout::ApplyToGraph(
			Blueprint,
			Graph,
			Request,
			Result,
			ErrorCode,
			ErrorMessage),
		ErrorCode,
		TEXT("graph-layout-transaction-active"));
	GEditor->CancelTransaction(BlockingTransaction);
	TestFalse(TEXT("blocking transaction was cancelled"), GEditor->Trans->IsActive());
	if (!TestTrue(TEXT("active transaction is rejected"), bActiveRejected))
	{
		return false;
	}
	TestTrue(TEXT("active transaction rejection changed no position"), PositionsUnchanged());

	// The product entrypoint (TryApply) must fail closed when the exact
	// Blueprint Editor is not open.  The automation host deliberately does not
	// open a Blueprint Editor, so this is a real negative gate, not a mock.
	TSharedPtr<FJsonObject> TryApplyResult;
	if (!TestTrue(
		TEXT("TryApply fails closed without an open Blueprint Editor"),
		ExpectFailure(
			*this,
			TEXT("TryApply without an editor fails"),
			UEAgentKitBlueprintGraphLayout::TryApply(
				Request,
				TryApplyResult,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-editor-not-open"))))
	{
		return false;
	}
	TestTrue(TEXT("TryApply fail-closed changed no position"), PositionsUnchanged());

	const TSharedPtr<FJsonObject> BadHashPlan = CloneObject(Plan);
	BadHashPlan->SetStringField(
		TEXT("layoutHash"),
		TEXT("sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"));
	TSharedPtr<FJsonObject> BadHashRequest = CloneObject(Request);
	BadHashRequest->SetObjectField(TEXT("plan"), BadHashPlan);
	if (!TestTrue(
		TEXT("tampered layout hash is rejected"),
		ExpectFailure(
			*this,
			TEXT("tampered layout hash apply fails"),
			UEAgentKitBlueprintGraphLayout::ApplyToGraph(
				Blueprint,
				Graph,
				BadHashRequest,
				Result,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-layout-hash-mismatch"))))
	{
		return false;
	}
	TestTrue(TEXT("tampered hash changed no position"), PositionsUnchanged());

	// Positive apply: one transaction, exact readback, Undo/Redo, Save.
	TSharedPtr<FJsonObject> Applied;
	if (!TestTrue(
		TEXT("position-only apply succeeds"),
		UEAgentKitBlueprintGraphLayout::ApplyToGraph(
			Blueprint,
			Graph,
			Request,
			Applied,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("ApplyToGraph failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	TestTrue(TEXT("apply reports changed"), Applied->GetBoolField(TEXT("changed")));
	TestTrue(TEXT("apply reports one transaction"), Applied->GetBoolField(TEXT("singleTransaction")));
	TestFalse(TEXT("apply did not save"), Applied->GetBoolField(TEXT("saved")));
	TestFalse(TEXT("apply did not compile"), Applied->GetBoolField(TEXT("compiled")));
	TestFalse(TEXT("apply started from a clean package"), Applied->GetBoolField(TEXT("packageDirtyBefore")));
	TestTrue(TEXT("apply left the package dirty"), Applied->GetBoolField(TEXT("packageDirtyAfter")));
	TestEqual(
		TEXT("apply moved every fixture node"),
		static_cast<int32>(Applied->GetNumberField(TEXT("movedNodeCount"))),
		LiveNodes.Num());
	TestEqual(
		TEXT("apply node count matches the graph"),
		static_cast<int32>(Applied->GetNumberField(TEXT("nodeCount"))),
		LiveNodes.Num());
	TestEqual(
		TEXT("apply graph fingerprint before matches preflight"),
		Applied->GetStringField(TEXT("graphFingerprintBefore")),
		PreFingerprint);
	TestEqual(
		TEXT("apply package revision before matches preflight"),
		Applied->GetStringField(TEXT("packageRevisionBefore")),
		PreRevision);
	TestEqual(
		TEXT("apply changed no node identity, pin, size, comment or link"),
		StructuralState(Graph),
		StructuralBefore);

	for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
	{
		const FIntPoint* Before = BeforePositions.Find(Pair.Key);
		if (!TestNotNull(TEXT("before position exists"), Before))
		{
			return false;
		}
		TestEqual(TEXT("applied x is exact"), Pair.Value->NodePosX, Before->X + FixtureShiftX);
		TestEqual(TEXT("applied y is exact"), Pair.Value->NodePosY, Before->Y + FixtureShiftY);
	}
	const TArray<TSharedPtr<FJsonValue>>* MovedNodes = nullptr;
	TestTrue(TEXT("apply result has movedNodes"), Applied->TryGetArrayField(TEXT("movedNodes"), MovedNodes));
	if (MovedNodes != nullptr)
	{
		TestEqual(TEXT("movedNodes length"), MovedNodes->Num(), LiveNodes.Num());
	}

	TestTrue(TEXT("apply created an undoable transaction"), GEditor->Trans->CanUndo());
	if (!TestTrue(TEXT("UndoTransaction succeeds"), GEditor->UndoTransaction()))
	{
		return false;
	}
	for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
	{
		const FIntPoint* Before = BeforePositions.Find(Pair.Key);
		TestEqual(TEXT("undo restores x"), Pair.Value->NodePosX, Before->X);
		TestEqual(TEXT("undo restores y"), Pair.Value->NodePosY, Before->Y);
	}
	if (!TestTrue(TEXT("RedoTransaction succeeds"), GEditor->RedoTransaction()))
	{
		return false;
	}
	for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
	{
		const FIntPoint* Before = BeforePositions.Find(Pair.Key);
		TestEqual(TEXT("redo restores applied x"), Pair.Value->NodePosX, Before->X + FixtureShiftX);
		TestEqual(TEXT("redo restores applied y"), Pair.Value->NodePosY, Before->Y + FixtureShiftY);
	}
	TestEqual(
		TEXT("undo/redo changed no node identity, pin, size, comment or link"),
		StructuralState(Graph),
		StructuralBefore);

	TSharedPtr<FJsonObject> PostApplyPreflight;
	if (!TestTrue(
		TEXT("preflight succeeds after redo"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			PostApplyPreflight,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("post-apply preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	const FString AppliedFingerprint = PostApplyPreflight->GetStringField(TEXT("graphFingerprint"));
	TestTrue(TEXT("apply changed the live fingerprint"), AppliedFingerprint != PreFingerprint);

	if (!TestTrue(TEXT("fixture Blueprint saves"), SaveBlueprint(Blueprint, Error)))
	{
		AddError(Error);
		return false;
	}
	TestFalse(TEXT("save clears the package dirty flag"), Package->IsDirty());

	TSharedPtr<FJsonObject> SavedPreflight;
	if (!TestTrue(
		TEXT("preflight succeeds after save"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			SavedPreflight,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("post-save preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	const FString PostRevision = SavedPreflight->GetStringField(TEXT("packageRevision"));
	TestTrue(TEXT("save changed the on-disk revision"), PostRevision != PreRevision);
	TestFalse(TEXT("saved package is clean"), SavedPreflight->GetBoolField(TEXT("packageDirty")));
	TestEqual(
		TEXT("saved fingerprint survives the in-process save"),
		SavedPreflight->GetStringField(TEXT("graphFingerprint")),
		AppliedFingerprint);

	// A plan built against the pre-save revision must not apply after Save.
	if (!TestTrue(
		TEXT("stale pre-save request is rejected after save"),
		ExpectFailure(
			*this,
			TEXT("stale request apply fails"),
			UEAgentKitBlueprintGraphLayout::ApplyToGraph(
				Blueprint,
				Graph,
				Request,
				Result,
				ErrorCode,
				ErrorMessage),
			ErrorCode,
			TEXT("graph-layout-revision-conflict"))))
	{
		return false;
	}

	TMap<FGuid, FIntPoint> Targets;
	for (const TPair<FGuid, UEdGraphNode*>& Pair : LiveNodes)
	{
		const FIntPoint* Before = BeforePositions.Find(Pair.Key);
		Targets.Add(Pair.Key, FIntPoint(Before->X + FixtureShiftX, Before->Y + FixtureShiftY));
	}

	TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
	Evidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.ApplyContract"));
	Evidence->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	Evidence->SetStringField(TEXT("graphGuid"), GuidLower(Graph->GraphGuid));
	Evidence->SetStringField(TEXT("planId"), Plan->GetStringField(TEXT("planId")));
	Evidence->SetStringField(TEXT("layoutHash"), Plan->GetStringField(TEXT("layoutHash")));
	Evidence->SetStringField(TEXT("preRevision"), PreRevision);
	Evidence->SetStringField(TEXT("postRevision"), PostRevision);
	Evidence->SetStringField(TEXT("preFingerprint"), PreFingerprint);
	Evidence->SetStringField(TEXT("postFingerprint"), AppliedFingerprint);
	Evidence->SetNumberField(TEXT("nodeCount"), LiveNodes.Num());
	Evidence->SetArrayField(TEXT("beforePositions"), MakePositionArray(BeforePositions));
	Evidence->SetArrayField(TEXT("targets"), MakePositionArray(Targets));
	Evidence->SetBoolField(TEXT("undoRestored"), true);
	Evidence->SetBoolField(TEXT("redoRestored"), true);
	Evidence->SetBoolField(TEXT("saved"), true);
	Evidence->SetBoolField(TEXT("staleRequestRejected"), true);
	if (!TestTrue(
		TEXT("apply evidence JSON is written"),
		WriteJson(TEXT("apply-result.json"), Evidence, Error)))
	{
		AddError(Error);
		return false;
	}
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitGraphLayoutReloadContractTest,
	"UEAgentKit.GraphLayout.ReloadContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitGraphLayoutReloadContractTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace UEAgentKitGraphLayoutP3;

	FString Error;
	const TSharedPtr<FJsonObject> Evidence = ReadJson(TEXT("apply-result.json"), Error);
	if (!TestTrue(TEXT("apply evidence exists for the reload process"), Evidence.IsValid()))
	{
		AddError(Error);
		return false;
	}
	const FString AssetPath = Evidence->GetStringField(TEXT("assetPath"));
	const FString GraphGuidText = Evidence->GetStringField(TEXT("graphGuid"));
	const FString PostRevision = Evidence->GetStringField(TEXT("postRevision"));
	const FString PostFingerprint = Evidence->GetStringField(TEXT("postFingerprint"));

	UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
	if (!TestNotNull(TEXT("saved fixture Blueprint loads in the second process"), Blueprint))
	{
		return false;
	}
	FGuid WantedGraphGuid;
	TestTrue(TEXT("saved graph GUID parses"), FGuid::Parse(GraphGuidText, WantedGraphGuid));
	UEdGraph* Graph = nullptr;
	auto FindGraph = [&WantedGraphGuid, &Graph](const TArray<TObjectPtr<UEdGraph>>& Graphs)
	{
		for (UEdGraph* Candidate : Graphs)
		{
			if (Candidate != nullptr && Candidate->GraphGuid == WantedGraphGuid)
			{
				Graph = Candidate;
			}
		}
	};
	FindGraph(Blueprint->UbergraphPages);
	FindGraph(Blueprint->FunctionGraphs);
	FindGraph(Blueprint->MacroGraphs);
	FindGraph(Blueprint->DelegateSignatureGraphs);
	if (!TestNotNull(TEXT("saved graph is found by exact GUID"), Graph))
	{
		return false;
	}

	TSharedPtr<FJsonObject> Preflight;
	FString ErrorCode;
	FString ErrorMessage;
	if (!TestTrue(
		TEXT("reload preflight succeeds"),
		UEAgentKitBlueprintGraphLayout::BuildPreflight(
			Blueprint,
			Graph,
			Preflight,
			ErrorCode,
			ErrorMessage)))
	{
		AddError(FString::Printf(TEXT("reload preflight failed: %s %s"), *ErrorCode, *ErrorMessage));
		return false;
	}
	TestFalse(
		TEXT("reloaded package is clean"),
		Preflight->GetBoolField(TEXT("packageDirty")));
	TestEqual(
		TEXT("reloaded package revision matches the saved disk hash"),
		Preflight->GetStringField(TEXT("packageRevision")),
		PostRevision);
	TestEqual(
		TEXT("reloaded graph fingerprint matches the post-apply value"),
		Preflight->GetStringField(TEXT("graphFingerprint")),
		PostFingerprint);

	const TArray<TSharedPtr<FJsonValue>>* Targets = nullptr;
	if (!TestTrue(TEXT("apply evidence has targets"), Evidence->TryGetArrayField(TEXT("targets"), Targets)))
	{
		return false;
	}
	if (!TestNotNull(TEXT("target array exists"), Targets))
	{
		return false;
	}
	const TMap<FGuid, UEdGraphNode*> LiveNodes = MapLiveNodes(Graph);
	TestEqual(TEXT("reloaded node count matches evidence"), LiveNodes.Num(), Targets->Num());
	for (const TSharedPtr<FJsonValue>& Entry : *Targets)
	{
		const TSharedPtr<FJsonObject> Target = Entry->AsObject();
		FGuid Guid;
		TestTrue(TEXT("target GUID parses"), FGuid::Parse(Target->GetStringField(TEXT("nodeGuid")), Guid));
		UEdGraphNode* const* Node = LiveNodes.Find(Guid);
		if (!TestNotNull(TEXT("target node exists after reload"), Node))
		{
			return false;
		}
		TestEqual(
			TEXT("reloaded x equals applied target"),
			(*Node)->NodePosX,
			static_cast<int32>(Target->GetNumberField(TEXT("x"))));
		TestEqual(
			TEXT("reloaded y equals applied target"),
			(*Node)->NodePosY,
			static_cast<int32>(Target->GetNumberField(TEXT("y"))));
	}

	TSharedRef<FJsonObject> ReloadEvidence = MakeShared<FJsonObject>();
	ReloadEvidence->SetStringField(TEXT("test"), TEXT("UEAgentKit.GraphLayout.ReloadContract"));
	ReloadEvidence->SetStringField(TEXT("assetPath"), AssetPath);
	ReloadEvidence->SetStringField(TEXT("graphGuid"), GraphGuidText);
	ReloadEvidence->SetStringField(TEXT("packageRevision"), PostRevision);
	ReloadEvidence->SetStringField(TEXT("graphFingerprint"), PostFingerprint);
	ReloadEvidence->SetNumberField(TEXT("nodeCount"), LiveNodes.Num());
	ReloadEvidence->SetBoolField(TEXT("independentReloadVerified"), true);
	if (!TestTrue(
		TEXT("reload evidence JSON is written"),
		WriteJson(TEXT("reload-result.json"), ReloadEvidence, Error)))
	{
		AddError(Error);
		return false;
	}
	return true;
}
#endif
