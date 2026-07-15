#include "BlueprintContextAnalysis.h"

#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "Engine/Blueprint.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "HAL/FileManager.h"
#include "K2Node_CallFunction.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"

namespace BlueprintContextAnalysisPrivate
{
	struct FGraphEntry
	{
		UEdGraph* Graph = nullptr;
		FString Kind;
	};

	FString GuidToString(const FGuid& Guid)
	{
		return Guid.IsValid() ? Guid.ToString(EGuidFormats::DigitsWithHyphensLower) : FString();
	}

	FString ObjectPath(const UObject* Object)
	{
		return Object ? Object->GetPathName() : FString();
	}

	FString MakeSymbolId(const FString& Kind, const FString& OwnerPath, const FString& StableKey = FString())
	{
		return StableKey.IsEmpty()
			? FString::Printf(TEXT("%s|%s"), *Kind, *OwnerPath)
			: FString::Printf(TEXT("%s|%s|%s"), *Kind, *OwnerPath, *StableKey);
	}

	FString StableKey(const FGuid& Guid, const FString& FallbackName)
	{
		const FString GuidString = GuidToString(Guid);
		return GuidString.IsEmpty() ? FallbackName : GuidString;
	}

	bool ProfileIncludesStructure(const EBlueprintContextProfile Profile)
	{
		return Profile != EBlueprintContextProfile::Index;
	}

	bool ProfileIncludesGraphs(const EBlueprintContextProfile Profile)
	{
		return Profile != EBlueprintContextProfile::Defaults;
	}

	bool ProfileIncludesNodeReferences(const EBlueprintContextProfile Profile)
	{
		return Profile == EBlueprintContextProfile::Logic
			|| Profile == EBlueprintContextProfile::Full
			|| Profile == EBlueprintContextProfile::AI;
	}

	void CollectGraphs(
		UBlueprint* Blueprint,
		const FString& GraphFilter,
		TArray<FGraphEntry>& OutGraphs)
	{
		if (!Blueprint)
		{
			return;
		}

		TSet<UEdGraph*> AddedGraphs;
		auto AddGraphs = [&](const TArray<TObjectPtr<UEdGraph>>& SourceGraphs, const FString& Kind)
		{
			for (UEdGraph* Graph : SourceGraphs)
			{
				if (!Graph || AddedGraphs.Contains(Graph))
				{
					continue;
				}
				if (!GraphFilter.IsEmpty() && !Graph->GetName().Equals(GraphFilter, ESearchCase::IgnoreCase))
				{
					continue;
				}

				AddedGraphs.Add(Graph);
				OutGraphs.Add({ Graph, Kind });
			}
		};

		AddGraphs(Blueprint->UbergraphPages, TEXT("uber"));
		AddGraphs(Blueprint->FunctionGraphs, TEXT("function"));
		AddGraphs(Blueprint->MacroGraphs, TEXT("macro"));
		AddGraphs(Blueprint->DelegateSignatureGraphs, TEXT("delegate-signature"));
	}

	TSharedRef<FJsonObject> MakeSymbol(
		const FString& Id,
		const FString& Kind,
		const FString& Name,
		const FString& AssetPath)
	{
		TSharedRef<FJsonObject> Symbol = MakeShared<FJsonObject>();
		Symbol->SetStringField(TEXT("id"), Id);
		Symbol->SetStringField(TEXT("kind"), Kind);
		Symbol->SetStringField(TEXT("name"), Name);
		Symbol->SetStringField(TEXT("assetPath"), AssetPath);
		return Symbol;
	}

	void AddSymbol(
		const TSharedRef<FJsonObject>& Symbol,
		TArray<TSharedPtr<FJsonValue>>& OutSymbols,
		TSet<FString>& SymbolIds)
	{
		FString Id;
		if (!Symbol->TryGetStringField(TEXT("id"), Id) || Id.IsEmpty() || SymbolIds.Contains(Id))
		{
			return;
		}

		SymbolIds.Add(Id);
		OutSymbols.Add(MakeShared<FJsonValueObject>(Symbol));
	}

	FString GetBlueprintAssetPathFromClass(const UClass* Class)
	{
		if (!Class)
		{
			return FString();
		}

		if (const UBlueprint* Blueprint = Cast<UBlueprint>(Class->ClassGeneratedBy))
		{
			return Blueprint->GetPathName();
		}

		return FString();
	}

	FString GetOwnerPathForClass(const UClass* Class)
	{
		const FString BlueprintAssetPath = GetBlueprintAssetPathFromClass(Class);
		return BlueprintAssetPath.IsEmpty() ? ObjectPath(Class) : BlueprintAssetPath;
	}

	UEdGraph* FindGraphByName(const UBlueprint* Blueprint, const FName GraphName)
	{
		if (!Blueprint || GraphName.IsNone())
		{
			return nullptr;
		}

		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			if (Graph && Graph->GetFName() == GraphName)
			{
				return Graph;
			}
		}

		for (UEdGraph* Graph : Blueprint->MacroGraphs)
		{
			if (Graph && Graph->GetFName() == GraphName)
			{
				return Graph;
			}
		}

		return nullptr;
	}

	void AddComponentSymbols(
		USCS_Node* Node,
		const FString& AssetPath,
		const FString& AssetSymbolId,
		const FString& ParentSymbolId,
		TArray<TSharedPtr<FJsonValue>>& OutSymbols,
		TSet<FString>& SymbolIds)
	{
		if (!Node)
		{
			return;
		}

		const FString Name = Node->GetVariableName().ToString();
		const FString Id = MakeSymbolId(TEXT("component"), AssetPath, StableKey(Node->VariableGuid, Name));
		TSharedRef<FJsonObject> Symbol = MakeSymbol(Id, TEXT("component"), Name, AssetPath);
		Symbol->SetStringField(TEXT("guid"), GuidToString(Node->VariableGuid));
		Symbol->SetStringField(TEXT("class"), ObjectPath(Node->ComponentClass.Get()));
		Symbol->SetStringField(TEXT("ownerSymbolId"), AssetSymbolId);
		Symbol->SetStringField(TEXT("parentSymbolId"), ParentSymbolId);
		AddSymbol(Symbol, OutSymbols, SymbolIds);

		for (USCS_Node* ChildNode : Node->GetChildNodes())
		{
			AddComponentSymbols(ChildNode, AssetPath, AssetSymbolId, Id, OutSymbols, SymbolIds);
		}
	}

	TSharedRef<FJsonObject> MakeReference(
		const FString& Id,
		const FString& Kind,
		const FString& SourceSymbolId,
		const FString& TargetSymbolId,
		const FString& TargetKind,
		const FString& TargetName,
		const FString& TargetAssetPath)
	{
		TSharedRef<FJsonObject> Reference = MakeShared<FJsonObject>();
		Reference->SetStringField(TEXT("id"), Id);
		Reference->SetStringField(TEXT("kind"), Kind);
		Reference->SetStringField(TEXT("sourceSymbolId"), SourceSymbolId);
		Reference->SetStringField(TEXT("targetSymbolId"), TargetSymbolId);
		Reference->SetStringField(TEXT("targetKind"), TargetKind);
		Reference->SetStringField(TEXT("targetName"), TargetName);
		Reference->SetStringField(TEXT("targetAssetPath"), TargetAssetPath);
		return Reference;
	}

	void AddReference(
		const TSharedRef<FJsonObject>& Reference,
		TArray<TSharedPtr<FJsonValue>>& OutReferences,
		TSet<FString>& ReferenceIds)
	{
		FString Id;
		if (!Reference->TryGetStringField(TEXT("id"), Id) || Id.IsEmpty() || ReferenceIds.Contains(Id))
		{
			return;
		}

		ReferenceIds.Add(Id);
		OutReferences.Add(MakeShared<FJsonValueObject>(Reference));
	}

	void AddNodeLocation(TSharedRef<FJsonObject>& Reference, const UEdGraph* Graph, const UEdGraphNode* Node)
	{
		Reference->SetStringField(TEXT("graphGuid"), Graph ? GuidToString(Graph->GraphGuid) : FString());
		Reference->SetStringField(TEXT("graphName"), Graph ? Graph->GetName() : FString());
		Reference->SetStringField(TEXT("nodeGuid"), Node ? GuidToString(Node->NodeGuid) : FString());
		Reference->SetStringField(TEXT("nodeClass"), Node && Node->GetClass() ? Node->GetClass()->GetPathName() : FString());
		Reference->SetStringField(TEXT("nodeTitle"), Node ? Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString() : FString());
	}

	FString MakeNodeReferenceId(
		const FString& Kind,
		const UEdGraph* Graph,
		const UEdGraphNode* Node,
		const FString& TargetSymbolId)
	{
		return FString::Printf(
			TEXT("reference|%s|%s|%s|%s"),
			*Kind,
			*GuidToString(Graph ? Graph->GraphGuid : FGuid()),
			*GuidToString(Node ? Node->NodeGuid : FGuid()),
			*TargetSymbolId);
	}
}

TSharedRef<FJsonObject> FBlueprintContextAnalysis::BuildAssetRevision(UBlueprint* Blueprint)
{
	using namespace BlueprintContextAnalysisPrivate;

	TSharedRef<FJsonObject> Revision = MakeShared<FJsonObject>();
	Revision->SetStringField(TEXT("strategy"), TEXT("package-sha256-v1"));
	Revision->SetBoolField(TEXT("available"), false);
	Revision->SetBoolField(TEXT("packageDirty"), false);
	Revision->SetStringField(TEXT("value"), FString());
	Revision->SetStringField(TEXT("packageGuid"), FString());
	Revision->SetNumberField(TEXT("fileSize"), 0.0);
	Revision->SetStringField(TEXT("modifiedUtc"), FString());
	Revision->SetStringField(TEXT("contentSha256"), FString());

	if (!Blueprint)
	{
		return Revision;
	}

	UPackage* Package = Blueprint->GetOutermost();
	if (!Package)
	{
		return Revision;
	}

	const FString PackageGuid = GuidToString(Package->GetPersistentGuid());
	Revision->SetStringField(TEXT("packageGuid"), PackageGuid);
	Revision->SetBoolField(TEXT("packageDirty"), Package->IsDirty());

	FString PackageFilename;
	const bool bResolvedFilename = FPackageName::TryConvertLongPackageNameToFilename(
		Package->GetName(),
		PackageFilename,
		FPackageName::GetAssetPackageExtension());

	if (!bResolvedFilename || !IFileManager::Get().FileExists(*PackageFilename))
	{
		if (!PackageGuid.IsEmpty())
		{
			Revision->SetBoolField(TEXT("available"), true);
			Revision->SetStringField(TEXT("value"), TEXT("guid:") + PackageGuid);
		}
		return Revision;
	}

	const int64 FileSize = IFileManager::Get().FileSize(*PackageFilename);
	const FDateTime ModifiedUtc = IFileManager::Get().GetTimeStamp(*PackageFilename);
	Revision->SetNumberField(TEXT("fileSize"), static_cast<double>(FMath::Max<int64>(FileSize, 0)));
	Revision->SetStringField(TEXT("modifiedUtc"), ModifiedUtc.ToIso8601());

	FString ContentSha256;
	if (FileSize >= 0 && FBlueprintContextSha256::HashFile(PackageFilename, ContentSha256))
	{
		Revision->SetStringField(TEXT("contentSha256"), ContentSha256);
	}

	if (!ContentSha256.IsEmpty())
	{
		Revision->SetBoolField(TEXT("available"), true);
		Revision->SetStringField(TEXT("value"), TEXT("sha256:") + ContentSha256);
	}
	else if (!PackageGuid.IsEmpty())
	{
		Revision->SetBoolField(TEXT("available"), true);
		Revision->SetStringField(TEXT("value"), TEXT("guid:") + PackageGuid);
	}

	return Revision;
}

void FBlueprintContextAnalysis::BuildSymbolsAndReferences(
	UBlueprint* Blueprint,
	const FBlueprintContextExportOptions& Options,
	TArray<TSharedPtr<FJsonValue>>& OutSymbols,
	TArray<TSharedPtr<FJsonValue>>& OutReferences,
	int32& OutSymbolCount,
	int32& OutReferenceCount)
{
	using namespace BlueprintContextAnalysisPrivate;

	OutSymbols.Reset();
	OutReferences.Reset();
	OutSymbolCount = 0;
	OutReferenceCount = 0;
	if (!Blueprint)
	{
		return;
	}

	const FString AssetPath = Blueprint->GetPathName();
	const FString AssetSymbolId = MakeSymbolId(TEXT("asset"), AssetPath);
	TSet<FString> SymbolIds;
	TSet<FString> ReferenceIds;
	TMap<FGuid, FString> VariableIdByGuid;
	TMap<FName, FString> VariableIdByName;
	TMap<UEdGraph*, FString> GraphIdByObject;
	TMap<FName, FString> FunctionIdByName;

	TSharedRef<FJsonObject> AssetSymbol = MakeSymbol(
		AssetSymbolId,
		TEXT("asset"),
		Blueprint->GetName(),
		AssetPath);
	AssetSymbol->SetStringField(TEXT("path"), AssetPath);
	AssetSymbol->SetStringField(TEXT("class"), ObjectPath(Blueprint->GetClass()));
	AddSymbol(AssetSymbol, OutSymbols, SymbolIds);

	if (Blueprint->ParentClass)
	{
		const FString ParentPath = ObjectPath(Blueprint->ParentClass);
		const FString TargetId = MakeSymbolId(TEXT("class"), ParentPath);
		const FString ReferenceId = FString::Printf(TEXT("reference|inherits|%s|%s"), *AssetSymbolId, *TargetId);
		AddReference(
			MakeReference(
				ReferenceId,
				TEXT("inherits"),
				AssetSymbolId,
				TargetId,
				TEXT("class"),
				Blueprint->ParentClass->GetName(),
				GetBlueprintAssetPathFromClass(Blueprint->ParentClass)),
			OutReferences,
			ReferenceIds);
	}

	for (const FBPInterfaceDescription& InterfaceDescription : Blueprint->ImplementedInterfaces)
	{
		if (!InterfaceDescription.Interface)
		{
			continue;
		}

		const FString InterfacePath = InterfaceDescription.Interface->GetPathName();
		const FString TargetId = MakeSymbolId(TEXT("class"), InterfacePath);
		const FString ReferenceId = FString::Printf(TEXT("reference|implements|%s|%s"), *AssetSymbolId, *TargetId);
		AddReference(
			MakeReference(
				ReferenceId,
				TEXT("implements"),
				AssetSymbolId,
				TargetId,
				TEXT("interface"),
				InterfaceDescription.Interface->GetName(),
				GetBlueprintAssetPathFromClass(InterfaceDescription.Interface)),
			OutReferences,
			ReferenceIds);
	}

	if (ProfileIncludesStructure(Options.Profile))
	{
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			const FString Name = Variable.VarName.ToString();
			const FString Id = MakeSymbolId(TEXT("variable"), AssetPath, StableKey(Variable.VarGuid, Name));
			TSharedRef<FJsonObject> Symbol = MakeSymbol(Id, TEXT("variable"), Name, AssetPath);
			Symbol->SetStringField(TEXT("guid"), GuidToString(Variable.VarGuid));
			Symbol->SetStringField(TEXT("ownerSymbolId"), AssetSymbolId);
			AddSymbol(Symbol, OutSymbols, SymbolIds);

			if (Variable.VarGuid.IsValid())
			{
				VariableIdByGuid.Add(Variable.VarGuid, Id);
			}
			VariableIdByName.Add(Variable.VarName, Id);
		}

		if (Blueprint->SimpleConstructionScript)
		{
			for (USCS_Node* RootNode : Blueprint->SimpleConstructionScript->GetRootNodes())
			{
				AddComponentSymbols(
					RootNode,
					AssetPath,
					AssetSymbolId,
					FString(),
					OutSymbols,
					SymbolIds);
			}
		}

		for (UEdGraph* FunctionGraph : Blueprint->FunctionGraphs)
		{
			if (!FunctionGraph)
			{
				continue;
			}

			const FString Name = FunctionGraph->GetName();
			const FString Id = MakeSymbolId(TEXT("function"), AssetPath, StableKey(FunctionGraph->GraphGuid, Name));
			TSharedRef<FJsonObject> Symbol = MakeSymbol(Id, TEXT("function"), Name, AssetPath);
			Symbol->SetStringField(TEXT("guid"), GuidToString(FunctionGraph->GraphGuid));
			Symbol->SetStringField(TEXT("ownerSymbolId"), AssetSymbolId);
			Symbol->SetStringField(TEXT("graphGuid"), GuidToString(FunctionGraph->GraphGuid));
			AddSymbol(Symbol, OutSymbols, SymbolIds);
			FunctionIdByName.Add(FunctionGraph->GetFName(), Id);
		}
	}

	TArray<FGraphEntry> Graphs;
	if (ProfileIncludesGraphs(Options.Profile))
	{
		CollectGraphs(Blueprint, Options.GraphFilter, Graphs);
		for (const FGraphEntry& Entry : Graphs)
		{
			UEdGraph* Graph = Entry.Graph;
			const FString Name = Graph->GetName();
			const FString Id = MakeSymbolId(TEXT("graph"), AssetPath, StableKey(Graph->GraphGuid, Name));
			TSharedRef<FJsonObject> Symbol = MakeSymbol(Id, TEXT("graph"), Name, AssetPath);
			Symbol->SetStringField(TEXT("guid"), GuidToString(Graph->GraphGuid));
			Symbol->SetStringField(TEXT("graphKind"), Entry.Kind);
			Symbol->SetStringField(TEXT("ownerSymbolId"), AssetSymbolId);
			AddSymbol(Symbol, OutSymbols, SymbolIds);
			GraphIdByObject.Add(Graph, Id);
		}
	}

	if (ProfileIncludesNodeReferences(Options.Profile))
	{
		for (const FGraphEntry& Entry : Graphs)
		{
			UEdGraph* Graph = Entry.Graph;
			const FString SourceSymbolId = GraphIdByObject.FindRef(Graph);
			if (!Graph || SourceSymbolId.IsEmpty())
			{
				continue;
			}

			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (!Node)
				{
					continue;
				}

				const UK2Node_Variable* VariableNode = Cast<UK2Node_Variable>(Node);
				if (VariableNode)
				{
					const FMemberReference& MemberReference = VariableNode->VariableReference;
					const FGuid MemberGuid = MemberReference.GetMemberGuid();
					const FName MemberName = MemberReference.GetMemberName();
					UClass* OwnerClass = MemberReference.IsSelfContext()
						? (Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass.Get() : Blueprint->GeneratedClass.Get())
						: MemberReference.GetMemberParentClass();
					const FString TargetAssetPath = GetBlueprintAssetPathFromClass(OwnerClass);
					const FString TargetOwnerPath = TargetAssetPath.IsEmpty() ? GetOwnerPathForClass(OwnerClass) : TargetAssetPath;

					FString TargetSymbolId;
					if (MemberReference.IsSelfContext() || TargetAssetPath == AssetPath)
					{
						if (MemberGuid.IsValid())
						{
							TargetSymbolId = VariableIdByGuid.FindRef(MemberGuid);
						}
						if (TargetSymbolId.IsEmpty())
						{
							TargetSymbolId = VariableIdByName.FindRef(MemberName);
						}
					}
					if (TargetSymbolId.IsEmpty())
					{
						TargetSymbolId = MakeSymbolId(
							TEXT("variable"),
							TargetOwnerPath.IsEmpty() ? AssetPath : TargetOwnerPath,
							StableKey(MemberGuid, MemberName.ToString()));
					}

					const FString Kind = Cast<UK2Node_VariableSet>(Node) ? TEXT("writes") : TEXT("reads");
					TSharedRef<FJsonObject> Reference = MakeReference(
						MakeNodeReferenceId(Kind, Graph, Node, TargetSymbolId),
						Kind,
						SourceSymbolId,
						TargetSymbolId,
						TEXT("variable"),
						MemberName.ToString(),
						TargetAssetPath);
					AddNodeLocation(Reference, Graph, Node);
					AddReference(Reference, OutReferences, ReferenceIds);
					continue;
				}

				if (const UK2Node_CallFunction* CallFunctionNode = Cast<UK2Node_CallFunction>(Node))
				{
					UFunction* TargetFunction = CallFunctionNode->GetTargetFunction();
					const FName FunctionName = TargetFunction
						? TargetFunction->GetFName()
						: CallFunctionNode->FunctionReference.GetMemberName();
					UClass* OwnerClass = TargetFunction
						? TargetFunction->GetOuterUClass()
						: CallFunctionNode->FunctionReference.GetMemberParentClass();
					const FString TargetAssetPath = GetBlueprintAssetPathFromClass(OwnerClass);
					const FString TargetOwnerPath = TargetAssetPath.IsEmpty() ? GetOwnerPathForClass(OwnerClass) : TargetAssetPath;
					FString TargetSymbolId;

					if (TargetAssetPath == AssetPath || CallFunctionNode->FunctionReference.IsSelfContext())
					{
						TargetSymbolId = FunctionIdByName.FindRef(FunctionName);
					}
					if (TargetSymbolId.IsEmpty())
					{
						FGuid FunctionGraphGuid;
						if (const UBlueprint* TargetBlueprint = Cast<UBlueprint>(OwnerClass ? OwnerClass->ClassGeneratedBy : nullptr))
						{
							if (UEdGraph* TargetGraph = FindGraphByName(TargetBlueprint, FunctionName))
							{
								FunctionGraphGuid = TargetGraph->GraphGuid;
							}
						}
						TargetSymbolId = MakeSymbolId(
							TEXT("function"),
							TargetOwnerPath.IsEmpty() ? ObjectPath(OwnerClass) : TargetOwnerPath,
							StableKey(FunctionGraphGuid, FunctionName.ToString()));
					}

					TSharedRef<FJsonObject> Reference = MakeReference(
						MakeNodeReferenceId(TEXT("calls"), Graph, Node, TargetSymbolId),
						TEXT("calls"),
						SourceSymbolId,
						TargetSymbolId,
						TEXT("function"),
						FunctionName.ToString(),
						TargetAssetPath);
					Reference->SetStringField(TEXT("targetPath"), ObjectPath(TargetFunction));
					AddNodeLocation(Reference, Graph, Node);
					AddReference(Reference, OutReferences, ReferenceIds);
					continue;
				}

				if (const UK2Node_MacroInstance* MacroNode = Cast<UK2Node_MacroInstance>(Node))
				{
					UEdGraph* TargetGraph = MacroNode->GetMacroGraph();
					UBlueprint* TargetBlueprint = MacroNode->GetSourceBlueprint();
					const FString TargetAssetPath = TargetBlueprint ? TargetBlueprint->GetPathName() : FString();
					const FString TargetName = TargetGraph ? TargetGraph->GetName() : FString();
					const FString TargetSymbolId = MakeSymbolId(
						TEXT("graph"),
						TargetAssetPath,
						StableKey(TargetGraph ? TargetGraph->GraphGuid : FGuid(), TargetName));
					TSharedRef<FJsonObject> Reference = MakeReference(
						MakeNodeReferenceId(TEXT("macro-calls"), Graph, Node, TargetSymbolId),
						TEXT("macro-calls"),
						SourceSymbolId,
						TargetSymbolId,
						TEXT("graph"),
						TargetName,
						TargetAssetPath);
					AddNodeLocation(Reference, Graph, Node);
					AddReference(Reference, OutReferences, ReferenceIds);
				}
			}
		}
	}

	OutSymbolCount = OutSymbols.Num();
	OutReferenceCount = OutReferences.Num();
}
