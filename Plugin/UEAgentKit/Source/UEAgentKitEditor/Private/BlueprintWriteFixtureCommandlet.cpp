#include "BlueprintWriteFixtureCommandlet.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "K2Node.h"
#include "K2Node_Event.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_IfThenElse.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "UObject/Interface.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintWriteFixture, Log, All);

namespace BlueprintWriteFixtureCommandletPrivate
{
	bool SaveBlueprint(UBlueprint* Blueprint, FString& OutError)
	{
		if (!Blueprint)
		{
			OutError = TEXT("Blueprint is null.");
			return false;
		}

		UPackage* Package = Blueprint->GetOutermost();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			Package->GetName(),
			FPackageName::GetAssetPackageExtension());
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Filename), true);

		FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::SkipGarbageCollection);
		if (Blueprint->Status == BS_Error)
		{
			OutError = FString::Printf(TEXT("Blueprint compilation failed: %s"), *Blueprint->GetPathName());
			return false;
		}

		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		if (!UPackage::SavePackage(Package, Blueprint, *Filename, SaveArgs))
		{
			OutError = FString::Printf(TEXT("Failed to save fixture: %s"), *Filename);
			return false;
		}
		return true;
	}

	bool CreateFixture(
		const FString& Root,
		const FString& AssetName,
		UClass* ParentClass,
		EBlueprintType BlueprintType,
		FString& OutPath,
		FString& OutError)
	{
		const FString PackageName = Root / AssetName;
		OutPath = PackageName + TEXT(".") + AssetName;
		if (LoadObject<UBlueprint>(nullptr, *OutPath))
		{
			return true;
		}

		UPackage* Package = CreatePackage(*PackageName);
		if (!Package)
		{
			OutError = FString::Printf(TEXT("Could not create package: %s"), *PackageName);
			return false;
		}

		UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
			ParentClass,
			Package,
			FName(*AssetName),
			BlueprintType,
			FName(TEXT("UEAgentKitWriteFixture")));
		if (!Blueprint)
		{
			OutError = FString::Printf(TEXT("Could not create Blueprint: %s"), *PackageName);
			return false;
		}

		Blueprint->BlueprintDescription = FString::Printf(
			TEXT("UEAgentKit generated %s fixture."),
			*AssetName);
		FAssetRegistryModule::AssetCreated(Blueprint);
		Package->MarkPackageDirty();
		return SaveBlueprint(Blueprint, OutError);
	}

	bool AddAccessorFixtureContent(UBlueprint* Blueprint, FString& OutError)
	{
		if (Blueprint == nullptr)
		{
			OutError = TEXT("Accessor fixture Blueprint is null.");
			return false;
		}

		auto AddMember = [Blueprint, &OutError](
			const TCHAR* Name,
			const FEdGraphPinType& Type,
			const TCHAR* DefaultValue) -> bool
		{
			const FName VariableName(Name);
			if (Blueprint->NewVariables.ContainsByPredicate(
				[&VariableName](const FBPVariableDescription& Variable)
				{
					return Variable.VarName == VariableName;
				}))
			{
				return true;
			}
			if (!FBlueprintEditorUtils::AddMemberVariable(
				Blueprint,
				VariableName,
				Type,
				DefaultValue))
			{
				OutError = FString::Printf(TEXT("Could not add fixture variable %s."), Name);
				return false;
			}
			return true;
		};

		FEdGraphPinType BoolType;
		BoolType.PinCategory = UEdGraphSchema_K2::PC_Boolean;
		FEdGraphPinType IntType;
		IntType.PinCategory = UEdGraphSchema_K2::PC_Int;
		FEdGraphPinType DoubleType;
		DoubleType.PinCategory = UEdGraphSchema_K2::PC_Real;
		DoubleType.PinSubCategory = UEdGraphSchema_K2::PC_Double;
		FEdGraphPinType StringType;
		StringType.PinCategory = UEdGraphSchema_K2::PC_String;

		if (!AddMember(TEXT("AccessBool"), BoolType, TEXT("true"))
			|| !AddMember(TEXT("AccessInt"), IntType, TEXT("7"))
			|| !AddMember(TEXT("AccessInt2"), IntType, TEXT("11"))
			|| !AddMember(TEXT("AccessDouble"), DoubleType, TEXT("1.5"))
			|| !AddMember(TEXT("AccessLabel"), StringType, TEXT("agent")))
		{
			return false;
		}

		bool bHasAccessorFunction = false;
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph != nullptr && Graph->GetName() == TEXT("AccessorFunction"))
			{
				bHasAccessorFunction = true;
				break;
			}
		}
		if (!bHasAccessorFunction)
		{
			UEdGraph* FunctionGraph = FBlueprintEditorUtils::CreateNewGraph(
				Blueprint,
				FName(TEXT("AccessorFunction")),
				UEdGraph::StaticClass(),
				UEdGraphSchema_K2::StaticClass());
			if (FunctionGraph == nullptr)
			{
				OutError = TEXT("Could not create the accessor fixture function graph.");
				return false;
			}
			FBlueprintEditorUtils::AddFunctionGraph<UFunction>(
				Blueprint,
				FunctionGraph,
				true,
				nullptr);
			// A local variable must never be reachable through the member
			// accessor request, even when its name is supplied correctly.
			FBlueprintEditorUtils::AddLocalVariable(
				Blueprint,
				FunctionGraph,
				FName(TEXT("AccessLocal")),
				IntType,
				TEXT("3"));
		}

		// One compiled self-owned pure function with one int input and one bool
		// return value.  Native acceptance must resolve this real generated-class
		// UFunction and its exact graph GUID/signature.
		bool bHasOwnedPureFunction = false;
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph != nullptr && Graph->GetName() == TEXT("OwnedPureFlag"))
			{
				bHasOwnedPureFunction = true;
				break;
			}
		}
		if (!bHasOwnedPureFunction)
		{
			UEdGraph* FunctionGraph = FBlueprintEditorUtils::CreateNewGraph(
				Blueprint,
				FName(TEXT("OwnedPureFlag")),
				UEdGraph::StaticClass(),
				UEdGraphSchema_K2::StaticClass());
			if (FunctionGraph == nullptr)
			{
				OutError = TEXT("Could not create the owned pure fixture function graph.");
				return false;
			}
			FBlueprintEditorUtils::AddFunctionGraph<UFunction>(
				Blueprint,
				FunctionGraph,
				true,
				nullptr);
			UK2Node_FunctionEntry* FunctionEntry = nullptr;
			for (UEdGraphNode* Node : FunctionGraph->Nodes)
			{
				if (UK2Node_FunctionEntry* Candidate = Cast<UK2Node_FunctionEntry>(Node))
				{
					FunctionEntry = Candidate;
					break;
				}
			}
			if (FunctionEntry == nullptr)
			{
				OutError = TEXT("The owned pure fixture has no function entry.");
				return false;
			}
			FunctionEntry->AddExtraFlags(FUNC_BlueprintPure);
			FunctionEntry->CreateUserDefinedPin(TEXT("A"), IntType, EGPD_Output);

			FGraphNodeCreator<UK2Node_FunctionResult> ResultCreator(*FunctionGraph);
			UK2Node_FunctionResult* FunctionResult = ResultCreator.CreateNode();
			FunctionResult->NodePosX = 420;
			FunctionResult->NodePosY = 0;
			ResultCreator.Finalize();
			FunctionResult->CreateUserDefinedPin(
				TEXT("ReturnValue"),
				BoolType,
				EGPD_Input);
			UEdGraphPin* EntryThen = FunctionEntry->FindPin(TEXT("then"), EGPD_Output);
			UEdGraphPin* ResultExec = FunctionResult->FindPin(TEXT("execute"), EGPD_Input);
			if (EntryThen == nullptr || ResultExec == nullptr)
			{
				OutError = TEXT("The owned pure fixture cannot link entry to result.");
				return false;
			}
			EntryThen->MakeLinkTo(ResultExec);
		}

		// Two existing Branch nodes and two pre-existing Event->Branch edges.
		// Disconnect acceptance removes exactly one of these edges while the
		// other must survive unchanged.
		if (Blueprint->UbergraphPages.Num() > 0)
		{
			UEdGraph* EventGraph = Blueprint->UbergraphPages[0];
			TArray<UK2Node_IfThenElse*> Branches;
			TArray<UEdGraphPin*> EventThenPins;
			for (UEdGraphNode* Node : EventGraph->Nodes)
			{
				if (UK2Node_IfThenElse* Branch = Cast<UK2Node_IfThenElse>(Node))
				{
					Branches.Add(Branch);
					continue;
				}
				if (UK2Node_Event* Event = Cast<UK2Node_Event>(Node))
				{
					UEdGraphPin* ThenPin = Event->FindPin(TEXT("then"), EGPD_Output);
					if (ThenPin != nullptr && ThenPin->LinkedTo.Num() == 0)
					{
						EventThenPins.Add(ThenPin);
					}
				}
			}
			while (Branches.Num() < 4)
			{
				FGraphNodeCreator<UK2Node_IfThenElse> Creator(*EventGraph);
				UK2Node_IfThenElse* Branch = Creator.CreateNode();
				Branch->NodePosX = -600;
				Branch->NodePosY = 300 + 220 * Branches.Num();
				Creator.Finalize();
				if (!Branch->NodeGuid.IsValid())
				{
					Branch->CreateNewGuid();
				}
				Branches.Add(Branch);
			}
			for (int32 Index = 0; Index < 2 && Index < EventThenPins.Num() && Index < Branches.Num(); ++Index)
			{
				UEdGraphPin* ExecutePin = Branches[Index]->FindPin(TEXT("execute"), EGPD_Input);
				if (ExecutePin != nullptr
					&& EventThenPins[Index]->LinkedTo.Num() == 0
					&& ExecutePin->LinkedTo.Num() == 0)
				{
					EventThenPins[Index]->MakeLinkTo(ExecutePin);
				}
			}
		}

		Blueprint->Modify();
		Blueprint->GetOutermost()->MarkPackageDirty();
		return SaveBlueprint(Blueprint, OutError);
	}

	bool WriteAccessorManifest(UBlueprint* Blueprint, FString& OutError)
	{
		TSharedRef<FJsonObject> RootObject = MakeShared<FJsonObject>();
		RootObject->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());

		TArray<TSharedPtr<FJsonValue>> Variables;
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(
				TEXT("guid"),
				Variable.VarGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetStringField(TEXT("name"), Variable.VarName.ToString());
			Variables.Add(MakeShared<FJsonValueObject>(Item));
		}
		RootObject->SetArrayField(TEXT("variables"), Variables);

		TArray<TSharedPtr<FJsonValue>> Graphs;
		auto AddGraph = [&Graphs](UEdGraph* Graph, const TCHAR* Kind)
		{
			if (Graph == nullptr)
			{
				return;
			}
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(
				TEXT("guid"),
				Graph->GraphGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
			Item->SetStringField(TEXT("name"), Graph->GetName());
			Item->SetStringField(TEXT("kind"), Kind);
			Graphs.Add(MakeShared<FJsonValueObject>(Item));
		};
		for (UEdGraph* Graph : Blueprint->UbergraphPages)
		{
			AddGraph(Graph, TEXT("uber"));
		}
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			AddGraph(Graph, TEXT("function"));
		}
		for (UEdGraph* Graph : Blueprint->MacroGraphs)
		{
			AddGraph(Graph, TEXT("macro"));
		}
		RootObject->SetArrayField(TEXT("graphs"), Graphs);

		TArray<TSharedPtr<FJsonValue>> Nodes;
		for (UEdGraph* Graph : Blueprint->UbergraphPages)
		{
			if (Graph == nullptr)
			{
				continue;
			}
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node == nullptr)
				{
					continue;
				}
				TSharedRef<FJsonObject> NodeObject = MakeShared<FJsonObject>();
				NodeObject->SetStringField(
					TEXT("guid"),
					Node->NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));
				NodeObject->SetStringField(TEXT("class"), Node->GetClass()->GetPathName());
				NodeObject->SetStringField(TEXT("name"), Node->GetName());
				TArray<TSharedPtr<FJsonValue>> Pins;
				for (UEdGraphPin* Pin : Node->Pins)
				{
					if (Pin == nullptr)
					{
						continue;
					}
					TSharedRef<FJsonObject> PinObject = MakeShared<FJsonObject>();
					PinObject->SetStringField(
						TEXT("pinGuid"),
						Pin->PinId.ToString(EGuidFormats::DigitsWithHyphensLower));
					PinObject->SetStringField(TEXT("pinName"), Pin->PinName.ToString());
					PinObject->SetStringField(
						TEXT("direction"),
						Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
					PinObject->SetStringField(TEXT("category"), Pin->PinType.PinCategory.ToString());
					PinObject->SetStringField(TEXT("subCategory"), Pin->PinType.PinSubCategory.ToString());
					PinObject->SetNumberField(TEXT("linkCount"), Pin->LinkedTo.Num());
					Pins.Add(MakeShared<FJsonValueObject>(PinObject));
				}
				NodeObject->SetArrayField(TEXT("pins"), Pins);
				Nodes.Add(MakeShared<FJsonValueObject>(NodeObject));
			}
		}
		RootObject->SetArrayField(TEXT("nodes"), Nodes);

		FString JsonText;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
		if (!FJsonSerializer::Serialize(RootObject, Writer))
		{
			OutError = TEXT("Could not serialize the accessor fixture manifest.");
			return false;
		}
		const FString ManifestPath = FPaths::Combine(
			FPaths::ProjectSavedDir(),
			TEXT("UEAgentKitAccessorE2E"),
			TEXT("fixture-manifest.json"));
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(ManifestPath), true);
		if (!FFileHelper::SaveStringToFile(
			JsonText,
			*ManifestPath,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			OutError = FString::Printf(TEXT("Could not write %s."), *ManifestPath);
			return false;
		}
		return true;
	}
}

UBlueprintWriteFixtureCommandlet::UBlueprintWriteFixtureCommandlet()
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;
	ShowErrorCount = true;
}

int32 UBlueprintWriteFixtureCommandlet::Main(const FString& Params)
{
	using namespace BlueprintWriteFixtureCommandletPrivate;

	FString Root = TEXT("/Game/UEAgentKitWriteTests");
	FParse::Value(*Params, TEXT("Root="), Root);
	Root.RemoveFromEnd(TEXT("/"));
	if (!Root.StartsWith(TEXT("/Game/"), ESearchCase::CaseSensitive))
	{
		UE_LOG(LogBlueprintWriteFixture, Error, TEXT("Root must be a specific directory below /Game."));
		return 1;
	}

	struct FFixtureDefinition
	{
		const TCHAR* Name;
		UClass* ParentClass;
		EBlueprintType BlueprintType;
		bool bAccessorFixture = false;
	};

	const TArray<FFixtureDefinition> Fixtures = {
		{TEXT("BFL_PatchTarget"), UBlueprintFunctionLibrary::StaticClass(), BPTYPE_FunctionLibrary},
		{TEXT("BML_PatchTarget"), AActor::StaticClass(), BPTYPE_MacroLibrary},
		{TEXT("BPI_PatchTarget"), UInterface::StaticClass(), BPTYPE_Interface},
		// P5 Agent end-to-end: a normal Actor Blueprint with the default
		// BeginPlay event graph, so live focused-graph writes have a real
		// top-level K2 event graph to target.
		{TEXT("BP_AgentE2E"), AActor::StaticClass(), BPTYPE_Normal},
		// Variable accessor acceptance: a normal Actor Blueprint with four
		// already-compiled primitive member variables and one function graph.
		{TEXT("BP_AccessorE2E"), AActor::StaticClass(), BPTYPE_Normal, true},
	};

	for (const FFixtureDefinition& Fixture : Fixtures)
	{
		FString AssetPath;
		FString Error;
		if (!CreateFixture(Root, Fixture.Name, Fixture.ParentClass, Fixture.BlueprintType, AssetPath, Error))
		{
			UE_LOG(LogBlueprintWriteFixture, Error, TEXT("%s"), *Error);
			return 2;
		}
		if (Fixture.bAccessorFixture)
		{
			UBlueprint* AccessorBlueprint = LoadObject<UBlueprint>(nullptr, *AssetPath);
			if (AccessorBlueprint == nullptr
				|| !AddAccessorFixtureContent(AccessorBlueprint, Error)
				|| !WriteAccessorManifest(AccessorBlueprint, Error))
			{
				UE_LOG(LogBlueprintWriteFixture, Error, TEXT("%s"), *Error);
				return 2;
			}
		}
		UE_LOG(LogBlueprintWriteFixture, Display, TEXT("Fixture ready: %s"), *AssetPath);
	}

	return 0;
}
