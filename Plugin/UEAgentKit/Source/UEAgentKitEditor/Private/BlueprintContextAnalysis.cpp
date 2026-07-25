#include "BlueprintContextAnalysis.h"

#include "BlueprintContextExporter.h"
#include "BlueprintContextSha256.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "Engine/PrimaryAssetLabel.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "HAL/FileManager.h"
#include "K2Node_AddDelegate.h"
#include "K2Node_AssignDelegate.h"
#include "K2Node_BaseMCDelegate.h"
#include "K2Node_CallDelegate.h"
#include "K2Node_CallFunction.h"
#include "K2Node_ClearDelegate.h"
#include "K2Node_CreateDelegate.h"
#include "K2Node_CustomEvent.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_Event.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_Message.h"
#include "K2Node_RemoveDelegate.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

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

	FString PropertyPath(const FProperty* Property)
	{
		if (!Property)
		{
			return FString();
		}

		const UStruct* OwnerStruct = Property->GetOwnerStruct();
		return OwnerStruct
			? FString::Printf(TEXT("%s:%s"), *OwnerStruct->GetPathName(), *Property->GetName())
			: Property->GetName();
	}

	FString NameOrEmpty(const FName Name)
	{
		return Name.IsNone() ? FString() : Name.ToString();
	}

	FString DependencyDomainName(const FString& PackageName)
	{
		if (PackageName.StartsWith(TEXT("/Game/")))
		{
			return TEXT("project");
		}
		if (PackageName.StartsWith(TEXT("/Engine/")))
		{
			return TEXT("engine-content");
		}
		if (PackageName.StartsWith(TEXT("/Script/")))
		{
			return TEXT("script");
		}
		return PackageName.StartsWith(TEXT("/")) ? TEXT("plugin-or-mounted") : TEXT("external");
	}

	FString DependencyCategoryName(const UE::AssetRegistry::EDependencyCategory Category)
	{
		using namespace UE::AssetRegistry;
		if (EnumHasAnyFlags(Category, EDependencyCategory::Package))
		{
			return TEXT("package");
		}
		if (EnumHasAnyFlags(Category, EDependencyCategory::Manage))
		{
			return TEXT("manage");
		}
		if (EnumHasAnyFlags(Category, EDependencyCategory::SearchableName))
		{
			return TEXT("searchable-name");
		}
		return TEXT("unknown");
	}

	FString DependencyPropertiesName(
		const UE::AssetRegistry::EDependencyCategory Category,
		const UE::AssetRegistry::EDependencyProperty Properties)
	{
		using namespace UE::AssetRegistry;
		TArray<FString> Names;
		if (EnumHasAnyFlags(Category, EDependencyCategory::Package))
		{
			Names.Add(EnumHasAnyFlags(Properties, EDependencyProperty::Hard) ? TEXT("hard") : TEXT("soft"));
			Names.Add(EnumHasAnyFlags(Properties, EDependencyProperty::Game) ? TEXT("game") : TEXT("editor-only"));
			if (EnumHasAnyFlags(Properties, EDependencyProperty::Build))
			{
				Names.Add(TEXT("build"));
			}
		}
		if (EnumHasAnyFlags(Category, EDependencyCategory::Manage))
		{
			Names.Add(EnumHasAnyFlags(Properties, EDependencyProperty::Direct) ? TEXT("direct") : TEXT("indirect"));
		}
		return FString::Join(Names, TEXT(","));
	}

	FString ResolveAssetPathForPackage(IAssetRegistry& AssetRegistry, const FName PackageName)
	{
		if (PackageName.IsNone())
		{
			return FString();
		}

		TArray<FAssetData> Assets;
		if (!AssetRegistry.GetAssetsByPackageName(PackageName, Assets, true) || Assets.IsEmpty())
		{
			return FString();
		}

		Assets.Sort([](const FAssetData& Left, const FAssetData& Right)
		{
			return Left.GetSoftObjectPath().ToString() < Right.GetSoftObjectPath().ToString();
		});
		return Assets[0].GetSoftObjectPath().ToString();
	}

	FString AssetDependencyReferenceKind(const FAssetDependency& Dependency)
	{
		using namespace UE::AssetRegistry;
		if (EnumHasAnyFlags(Dependency.Category, EDependencyCategory::Package))
		{
			return EnumHasAnyFlags(Dependency.Properties, EDependencyProperty::Hard)
				? TEXT("depends-hard-package")
				: TEXT("depends-soft-package");
		}
		if (EnumHasAnyFlags(Dependency.Category, EDependencyCategory::Manage))
		{
			return EnumHasAnyFlags(Dependency.Properties, EDependencyProperty::Direct)
				? TEXT("manages-direct")
				: TEXT("manages-indirect");
		}
		if (EnumHasAnyFlags(Dependency.Category, EDependencyCategory::SearchableName))
		{
			return TEXT("depends-searchable-name");
		}
		return TEXT("depends-asset-registry");
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

	FString MakeScopedVariableLookupKey(const FString& ScopeName, const FGuid& Guid, const FName VariableName)
	{
		const FString GuidString = GuidToString(Guid);
		const FString MemberKey = GuidString.IsEmpty() ? VariableName.ToString() : GuidString;
		return ScopeName.ToLower() + TEXT("|") + MemberKey.ToLower();
	}

	FString MakeLocalVariableStableKey(const FString& ScopeName, const FGuid& Guid, const FName VariableName)
	{
		return FString::Printf(
			TEXT("local:%s:%s"),
			*ScopeName,
			*StableKey(Guid, VariableName.ToString()));
	}

	FString GetLinkedNodeGuids(const UEdGraphPin* Pin)
	{
		if (!Pin)
		{
			return FString();
		}

		TArray<FString> NodeGuids;
		for (const UEdGraphPin* LinkedPin : Pin->LinkedTo)
		{
			const UEdGraphNode* LinkedNode = LinkedPin ? LinkedPin->GetOwningNode() : nullptr;
			if (LinkedNode && LinkedNode->NodeGuid.IsValid())
			{
				NodeGuids.AddUnique(GuidToString(LinkedNode->NodeGuid));
			}
		}
		NodeGuids.Sort();
		return FString::Join(NodeGuids, TEXT(","));
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

		for (UEdGraph* Graph : Blueprint->DelegateSignatureGraphs)
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
	TMap<FName, FString> EventIdByName;
	TMap<FGuid, FString> EventIdByNodeGuid;
	TMap<FGuid, FString> DelegateIdByGuid;
	TMap<FName, FString> DelegateIdByName;
	TSet<FString> DelegateSymbolIds;
	TMap<FString, FString> LocalVariableIdByScope;

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
			const bool bDelegate = Variable.VarType.PinCategory == UEdGraphSchema_K2::PC_MCDelegate;
			const FString SymbolKind = bDelegate ? TEXT("delegate") : TEXT("variable");
			const FString Id = MakeSymbolId(SymbolKind, AssetPath, StableKey(Variable.VarGuid, Name));
			TSharedRef<FJsonObject> Symbol = MakeSymbol(Id, SymbolKind, Name, AssetPath);
			Symbol->SetStringField(TEXT("guid"), GuidToString(Variable.VarGuid));
			Symbol->SetStringField(TEXT("ownerSymbolId"), AssetSymbolId);

			if (bDelegate)
			{
				UEdGraph* SignatureGraph = FindGraphByName(Blueprint, Variable.VarName);
				const FString SignatureGraphGuid = GuidToString(SignatureGraph ? SignatureGraph->GraphGuid : FGuid());
				const FString SignatureGraphSymbolId = SignatureGraph
					? MakeSymbolId(TEXT("graph"), AssetPath, StableKey(SignatureGraph->GraphGuid, SignatureGraph->GetName()))
					: FString();
				UClass* BlueprintClass = Blueprint->SkeletonGeneratedClass
					? Blueprint->SkeletonGeneratedClass.Get()
					: Blueprint->GeneratedClass.Get();
				const FMulticastDelegateProperty* DelegateProperty = BlueprintClass
					? FindFProperty<FMulticastDelegateProperty>(BlueprintClass, Variable.VarName)
					: nullptr;

				Symbol->SetStringField(TEXT("delegateKind"), TEXT("event-dispatcher"));
				Symbol->SetStringField(TEXT("delegateScope"), TEXT("member"));
				Symbol->SetStringField(TEXT("signatureGraphGuid"), SignatureGraphGuid);
				Symbol->SetStringField(TEXT("signatureGraphSymbolId"), SignatureGraphSymbolId);
				Symbol->SetStringField(
					TEXT("signaturePath"),
					DelegateProperty && DelegateProperty->SignatureFunction
						? ObjectPath(DelegateProperty->SignatureFunction)
						: FString());
				Symbol->SetBoolField(TEXT("multicast"), true);
				DelegateSymbolIds.Add(Id);
				if (Variable.VarGuid.IsValid())
				{
					DelegateIdByGuid.Add(Variable.VarGuid, Id);
				}
				DelegateIdByName.Add(Variable.VarName, Id);
			}
			else
			{
				Symbol->SetStringField(TEXT("variableScope"), TEXT("member"));
				Symbol->SetStringField(TEXT("variableType"), Variable.VarType.PinCategory.ToString());
				Symbol->SetStringField(TEXT("declaredTypePath"), ObjectPath(Variable.VarType.PinSubCategoryObject.Get()));
			}

			AddSymbol(Symbol, OutSymbols, SymbolIds);
			if (Variable.VarGuid.IsValid())
			{
				VariableIdByGuid.Add(Variable.VarGuid, Id);
			}
			VariableIdByName.Add(Variable.VarName, Id);

			const bool bSoftObject = Variable.VarType.PinCategory == UEdGraphSchema_K2::PC_SoftObject;
			const bool bSoftClass = Variable.VarType.PinCategory == UEdGraphSchema_K2::PC_SoftClass;
			if (!bDelegate && (bSoftObject || bSoftClass) && Blueprint->GeneratedClass)
			{
				UClass* BlueprintClass = Blueprint->GeneratedClass.Get();
				UObject* DefaultObject = BlueprintClass ? BlueprintClass->GetDefaultObject(false) : nullptr;
				const FSoftObjectProperty* SoftProperty = BlueprintClass
					? FindFProperty<FSoftObjectProperty>(BlueprintClass, Variable.VarName)
					: nullptr;
				if (SoftProperty && DefaultObject)
				{
					const FSoftObjectPtr& SoftValue = SoftProperty->GetPropertyValue_InContainer(DefaultObject);
					const FSoftObjectPath& SoftPath = SoftValue.ToSoftObjectPath();
					if (!SoftPath.IsNull())
					{
						IAssetRegistry& AssetRegistry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(
							TEXT("AssetRegistry")).Get();
						const FString TargetPath = SoftPath.ToString();
						const FString TargetPackageName = SoftPath.GetLongPackageName();
						const FString TargetAssetPath = ResolveAssetPathForPackage(
							AssetRegistry,
							SoftPath.GetLongPackageFName());
						const FString ReferenceKind = bSoftClass
							? TEXT("soft-class-reference")
							: TEXT("soft-object-reference");
						const FString TargetKind = bSoftClass ? TEXT("class") : TEXT("asset");
						const FString TargetSymbolId = MakeSymbolId(
							TargetKind,
							bSoftClass || TargetAssetPath.IsEmpty() ? TargetPath : TargetAssetPath);
						TSharedRef<FJsonObject> Reference = MakeReference(
							MakeSymbolId(TEXT("reference"), ReferenceKind, Id + TEXT("|") + TargetSymbolId),
							ReferenceKind,
							Id,
							TargetSymbolId,
							TargetKind,
							SoftPath.GetAssetName(),
							TargetAssetPath);
						Reference->SetStringField(TEXT("targetPath"), TargetPath);
						Reference->SetStringField(TEXT("targetPackageName"), TargetPackageName);
						Reference->SetStringField(TEXT("softReferenceKind"), bSoftClass ? TEXT("class") : TEXT("object"));
						Reference->SetStringField(TEXT("sourceVariableName"), Name);
						Reference->SetStringField(TEXT("declaredTypePath"), ObjectPath(Variable.VarType.PinSubCategoryObject.Get()));
						Reference->SetStringField(TEXT("dependencyDomain"), DependencyDomainName(TargetPackageName));
						AddReference(Reference, OutReferences, ReferenceIds);
						Symbol->SetStringField(TEXT("defaultTargetPath"), TargetPath);
						Symbol->SetStringField(TEXT("softReferenceKind"), bSoftClass ? TEXT("class") : TEXT("object"));
					}
				}
			}
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

			const UK2Node_FunctionEntry* FunctionEntry = nullptr;
			TArray<const UK2Node_FunctionResult*> FunctionResults;
			for (UEdGraphNode* Node : FunctionGraph->Nodes)
			{
				if (!FunctionEntry)
				{
					FunctionEntry = Cast<UK2Node_FunctionEntry>(Node);
				}
				if (const UK2Node_FunctionResult* FunctionResult = Cast<UK2Node_FunctionResult>(Node))
				{
					FunctionResults.Add(FunctionResult);
				}
			}

			if (!FunctionEntry)
			{
				continue;
			}

			const FName GeneratedFunctionName = FunctionEntry->CustomGeneratedFunctionName.IsNone()
				? FunctionGraph->GetFName()
				: FunctionEntry->CustomGeneratedFunctionName;
			TArray<FString> ScopeNames = { FunctionGraph->GetName() };
			if (!GeneratedFunctionName.IsNone() && GeneratedFunctionName != FunctionGraph->GetFName())
			{
				ScopeNames.Add(GeneratedFunctionName.ToString());
			}

			TSet<FName> ResultParameterNames;
			for (const UK2Node_FunctionResult* FunctionResult : FunctionResults)
			{
				for (const UEdGraphPin* ResultPin : FunctionResult->Pins)
				{
					if (ResultPin && ResultPin->PinType.PinCategory != UEdGraphSchema_K2::PC_Exec)
					{
						ResultParameterNames.Add(ResultPin->PinName);
					}
				}
			}

			TMap<FName, FString> ParameterIdByName;
			for (UEdGraphPin* ParameterPin : FunctionEntry->Pins)
			{
				if (!ParameterPin || ParameterPin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec)
				{
					continue;
				}

				const FName ParameterName = ParameterPin->PinName;
				const FString ScopeName = FunctionGraph->GetName();
				const FString ParameterDirection = ResultParameterNames.Contains(ParameterName)
					? TEXT("inout")
					: TEXT("input");
				const FString ParameterId = MakeSymbolId(
					TEXT("variable"),
					AssetPath,
					MakeLocalVariableStableKey(ScopeName, FGuid(), ParameterName));
				TSharedRef<FJsonObject> ParameterSymbol = MakeSymbol(
					ParameterId,
					TEXT("variable"),
					ParameterName.ToString(),
					AssetPath);
				ParameterSymbol->SetStringField(TEXT("guid"), GuidToString(ParameterPin->PinId));
				ParameterSymbol->SetStringField(TEXT("pinGuid"), GuidToString(ParameterPin->PinId));
				ParameterSymbol->SetStringField(TEXT("ownerSymbolId"), Id);
				ParameterSymbol->SetStringField(TEXT("graphGuid"), GuidToString(FunctionGraph->GraphGuid));
				ParameterSymbol->SetStringField(TEXT("variableScope"), TEXT("local"));
				ParameterSymbol->SetStringField(TEXT("variableRole"), TEXT("parameter"));
				ParameterSymbol->SetStringField(TEXT("scopeName"), ScopeName);
				ParameterSymbol->SetStringField(TEXT("parameterDirection"), ParameterDirection);
				ParameterSymbol->SetStringField(
					TEXT("parameterPassing"),
					ParameterPin->PinType.bIsReference ? TEXT("reference") : TEXT("value"));
				ParameterSymbol->SetBoolField(TEXT("parameterConst"), ParameterPin->PinType.bIsConst);
				AddSymbol(ParameterSymbol, OutSymbols, SymbolIds);
				ParameterIdByName.Add(ParameterName, ParameterId);

				for (const FString& ScopeAlias : ScopeNames)
				{
					LocalVariableIdByScope.Add(
						MakeScopedVariableLookupKey(ScopeAlias, FGuid(), ParameterName),
						ParameterId);
				}
			}

			for (const UK2Node_FunctionResult* FunctionResult : FunctionResults)
			{
				for (const UEdGraphPin* ResultPin : FunctionResult->Pins)
				{
					if (!ResultPin || ResultPin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec)
					{
						continue;
					}

					const FName ParameterName = ResultPin->PinName;
					const FString ScopeName = FunctionGraph->GetName();
					const bool bReturnValue = ParameterName == UEdGraphSchema_K2::PN_ReturnValue;
					const bool bInOut = ParameterIdByName.Contains(ParameterName);
					const FString ParameterDirection = bReturnValue
						? TEXT("return")
						: (bInOut ? TEXT("inout") : TEXT("output"));
					FString ParameterId = ParameterIdByName.FindRef(ParameterName);
					if (ParameterId.IsEmpty())
					{
						const FString OutputStableName = ParameterDirection + TEXT(":") + ParameterName.ToString();
						ParameterId = MakeSymbolId(
							TEXT("variable"),
							AssetPath,
							MakeLocalVariableStableKey(ScopeName, FGuid(), FName(*OutputStableName)));
						TSharedRef<FJsonObject> ParameterSymbol = MakeSymbol(
							ParameterId,
							TEXT("variable"),
							ParameterName.ToString(),
							AssetPath);
						ParameterSymbol->SetStringField(TEXT("guid"), GuidToString(ResultPin->PinId));
						ParameterSymbol->SetStringField(TEXT("pinGuid"), GuidToString(ResultPin->PinId));
						ParameterSymbol->SetStringField(TEXT("ownerSymbolId"), Id);
						ParameterSymbol->SetStringField(TEXT("graphGuid"), GuidToString(FunctionGraph->GraphGuid));
						ParameterSymbol->SetStringField(TEXT("variableScope"), TEXT("local"));
						ParameterSymbol->SetStringField(TEXT("variableRole"), TEXT("parameter"));
						ParameterSymbol->SetStringField(TEXT("scopeName"), ScopeName);
						ParameterSymbol->SetStringField(TEXT("parameterDirection"), ParameterDirection);
						ParameterSymbol->SetStringField(
							TEXT("parameterPassing"),
							ResultPin->PinType.bIsReference ? TEXT("reference") : TEXT("value"));
						ParameterSymbol->SetBoolField(TEXT("parameterConst"), ResultPin->PinType.bIsConst);
						AddSymbol(ParameterSymbol, OutSymbols, SymbolIds);
						ParameterIdByName.Add(ParameterName, ParameterId);
					}

					if (ProfileIncludesNodeReferences(Options.Profile))
					{
						TSharedRef<FJsonObject> Reference = MakeReference(
							MakeNodeReferenceId(TEXT("returns"), FunctionGraph, FunctionResult, ParameterId),
							TEXT("returns"),
							Id,
							ParameterId,
							TEXT("variable"),
							ParameterName.ToString(),
							AssetPath);
						Reference->SetStringField(TEXT("parameterDirection"), ParameterDirection);
						Reference->SetStringField(
							TEXT("parameterPassing"),
							ResultPin->PinType.bIsReference ? TEXT("reference") : TEXT("value"));
						Reference->SetBoolField(TEXT("parameterConst"), ResultPin->PinType.bIsConst);
						Reference->SetStringField(TEXT("valueNodeGuids"), GetLinkedNodeGuids(ResultPin));
						Reference->SetStringField(TEXT("resultNodeGuid"), GuidToString(FunctionResult->NodeGuid));
						AddNodeLocation(Reference, FunctionGraph, FunctionResult);
						AddReference(Reference, OutReferences, ReferenceIds);
					}
				}
			}

			for (const FBPVariableDescription& LocalVariable : FunctionEntry->LocalVariables)
			{
				const FString LocalName = LocalVariable.VarName.ToString();
				const FString ScopeName = FunctionGraph->GetName();
				const FString LocalId = MakeSymbolId(
					TEXT("variable"),
					AssetPath,
					MakeLocalVariableStableKey(ScopeName, LocalVariable.VarGuid, LocalVariable.VarName));
				TSharedRef<FJsonObject> LocalSymbol = MakeSymbol(LocalId, TEXT("variable"), LocalName, AssetPath);
				LocalSymbol->SetStringField(TEXT("guid"), GuidToString(LocalVariable.VarGuid));
				LocalSymbol->SetStringField(TEXT("ownerSymbolId"), Id);
				LocalSymbol->SetStringField(TEXT("graphGuid"), GuidToString(FunctionGraph->GraphGuid));
				LocalSymbol->SetStringField(TEXT("variableScope"), TEXT("local"));
				LocalSymbol->SetStringField(TEXT("variableRole"), TEXT("local"));
				LocalSymbol->SetStringField(TEXT("scopeName"), ScopeName);
				AddSymbol(LocalSymbol, OutSymbols, SymbolIds);

				for (const FString& ScopeAlias : ScopeNames)
				{
					LocalVariableIdByScope.Add(
						MakeScopedVariableLookupKey(ScopeAlias, FGuid(), LocalVariable.VarName),
						LocalId);
					if (LocalVariable.VarGuid.IsValid())
					{
						LocalVariableIdByScope.Add(
							MakeScopedVariableLookupKey(ScopeAlias, LocalVariable.VarGuid, LocalVariable.VarName),
							LocalId);
					}
				}
			}
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
			const FString DelegateOwnerSymbolId = Entry.Kind == TEXT("delegate-signature")
				? DelegateIdByName.FindRef(Graph->GetFName())
				: FString();
			Symbol->SetStringField(
				TEXT("ownerSymbolId"),
				DelegateOwnerSymbolId.IsEmpty() ? AssetSymbolId : DelegateOwnerSymbolId);
			AddSymbol(Symbol, OutSymbols, SymbolIds);
			GraphIdByObject.Add(Graph, Id);

			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (!Node)
				{
					continue;
				}

				if (const UK2Node_Event* EventNode = Cast<UK2Node_Event>(Node))
				{
					const FName EventFunctionName = EventNode->GetFunctionName();
					const FString EventName = EventFunctionName.IsNone()
						? Node->GetNodeTitle(ENodeTitleType::ListView).ToString()
						: EventFunctionName.ToString();
					const FString EventId = MakeSymbolId(TEXT("event"), AssetPath, StableKey(Node->NodeGuid, EventName));
					TSharedRef<FJsonObject> EventSymbol = MakeSymbol(EventId, TEXT("event"), EventName, AssetPath);
					EventSymbol->SetStringField(TEXT("guid"), GuidToString(Node->NodeGuid));
					EventSymbol->SetStringField(TEXT("nodeGuid"), GuidToString(Node->NodeGuid));
					EventSymbol->SetStringField(TEXT("graphGuid"), GuidToString(Graph->GraphGuid));
					EventSymbol->SetStringField(TEXT("ownerSymbolId"), Id);
					EventSymbol->SetStringField(
						TEXT("eventKind"),
						Cast<UK2Node_CustomEvent>(EventNode)
							? TEXT("custom")
							: (EventNode->bOverrideFunction ? TEXT("override") : TEXT("event")));
					EventSymbol->SetStringField(TEXT("signaturePath"), ObjectPath(EventNode->FindEventSignatureFunction()));
					AddSymbol(EventSymbol, OutSymbols, SymbolIds);

					if (!EventFunctionName.IsNone())
					{
						EventIdByName.Add(EventFunctionName, EventId);
					}
					if (Node->NodeGuid.IsValid())
					{
						EventIdByNodeGuid.Add(Node->NodeGuid, EventId);
					}
					continue;
				}

				if (const UK2Node_FunctionEntry* FunctionEntry = Cast<UK2Node_FunctionEntry>(Node))
				{
					const FName GeneratedFunctionName = FunctionEntry->CustomGeneratedFunctionName.IsNone()
						? Graph->GetFName()
						: FunctionEntry->CustomGeneratedFunctionName;
					const FString EntryName = GeneratedFunctionName.ToString();
					const FString EntryId = MakeSymbolId(TEXT("function-entry"), AssetPath, StableKey(Node->NodeGuid, EntryName));
					TSharedRef<FJsonObject> EntrySymbol = MakeSymbol(EntryId, TEXT("function-entry"), EntryName, AssetPath);
					EntrySymbol->SetStringField(TEXT("guid"), GuidToString(Node->NodeGuid));
					EntrySymbol->SetStringField(TEXT("nodeGuid"), GuidToString(Node->NodeGuid));
					EntrySymbol->SetStringField(TEXT("graphGuid"), GuidToString(Graph->GraphGuid));
					const FString FunctionSymbolId = FunctionIdByName.FindRef(Graph->GetFName());
					EntrySymbol->SetStringField(TEXT("ownerSymbolId"), FunctionSymbolId.IsEmpty() ? Id : FunctionSymbolId);
					AddSymbol(EntrySymbol, OutSymbols, SymbolIds);
				}
			}
		}
	}

	auto ResolveCallableSymbol = [&](const FName CallableName,
		UClass* ScopeClass,
		FString& OutKind,
		FString& OutAssetPath,
		FString& OutTargetPath) -> FString
	{
		OutKind = TEXT("function");
		OutAssetPath = GetBlueprintAssetPathFromClass(ScopeClass);
		const FString OwnerPath = OutAssetPath.IsEmpty() ? GetOwnerPathForClass(ScopeClass) : OutAssetPath;
		if (OutAssetPath == AssetPath || !ScopeClass)
		{
			FString LocalId = EventIdByName.FindRef(CallableName);
			if (!LocalId.IsEmpty())
			{
				OutKind = TEXT("event");
				return LocalId;
			}

			LocalId = FunctionIdByName.FindRef(CallableName);
			if (!LocalId.IsEmpty())
			{
				return LocalId;
			}
		}

		UFunction* TargetFunction = ScopeClass ? ScopeClass->FindFunctionByName(CallableName) : nullptr;
		OutTargetPath = ObjectPath(TargetFunction);
		FGuid FunctionGraphGuid;
		if (const UBlueprint* TargetBlueprint = Cast<UBlueprint>(ScopeClass ? ScopeClass->ClassGeneratedBy : nullptr))
		{
			if (UEdGraph* TargetGraph = FindGraphByName(TargetBlueprint, CallableName))
			{
				FunctionGraphGuid = TargetGraph->GraphGuid;
			}
		}
		return MakeSymbolId(
			TEXT("function"),
			OwnerPath.IsEmpty() ? ObjectPath(ScopeClass) : OwnerPath,
			StableKey(FunctionGraphGuid, CallableName.ToString()));
	};

	auto AddDelegateHandlerFields = [&](const UK2Node_BaseMCDelegate* DelegateNode, TSharedRef<FJsonObject>& Reference)
	{
		const UEdGraphPin* DelegatePin = DelegateNode ? DelegateNode->GetDelegatePin() : nullptr;
		if (!DelegatePin)
		{
			return;
		}

		for (const UEdGraphPin* LinkedPin : DelegatePin->LinkedTo)
		{
			const UEdGraphNode* HandlerNode = LinkedPin ? LinkedPin->GetOwningNode() : nullptr;
			if (!HandlerNode)
			{
				continue;
			}

			FString HandlerSymbolId;
			FString HandlerKind;
			FString HandlerName;
			FString HandlerAssetPath;
			FString HandlerPath;
			if (const UK2Node_CreateDelegate* CreateDelegateNode = Cast<UK2Node_CreateDelegate>(HandlerNode))
			{
				HandlerName = CreateDelegateNode->GetFunctionName().ToString();
				HandlerSymbolId = ResolveCallableSymbol(
					CreateDelegateNode->GetFunctionName(),
					CreateDelegateNode->GetScopeClass(),
					HandlerKind,
					HandlerAssetPath,
					HandlerPath);
			}
			else if (const UK2Node_Event* EventNode = Cast<UK2Node_Event>(HandlerNode))
			{
				HandlerSymbolId = EventIdByNodeGuid.FindRef(EventNode->NodeGuid);
				HandlerKind = TEXT("event");
				HandlerName = EventNode->GetFunctionName().IsNone()
					? EventNode->GetNodeTitle(ENodeTitleType::ListView).ToString()
					: EventNode->GetFunctionName().ToString();
				HandlerAssetPath = AssetPath;
				HandlerPath = ObjectPath(EventNode->FindEventSignatureFunction());
			}
			else
			{
				HandlerKind = TEXT("node");
				HandlerName = HandlerNode->GetNodeTitle(ENodeTitleType::ListView).ToString();
			}

			Reference->SetStringField(TEXT("handlerSymbolId"), HandlerSymbolId);
			Reference->SetStringField(TEXT("handlerKind"), HandlerKind);
			Reference->SetStringField(TEXT("handlerName"), HandlerName);
			Reference->SetStringField(TEXT("handlerAssetPath"), HandlerAssetPath);
			Reference->SetStringField(TEXT("handlerPath"), HandlerPath);
			Reference->SetStringField(TEXT("handlerNodeGuid"), GuidToString(HandlerNode->NodeGuid));
			break;
		}
	};

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
					const bool bLocalScope = MemberReference.IsLocalScope();
					const FString MemberScopeName = MemberReference.GetMemberScopeName();
					UClass* OwnerClass = MemberReference.IsSelfContext() || bLocalScope
						? (Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass.Get() : Blueprint->GeneratedClass.Get())
						: MemberReference.GetMemberParentClass();
					const FString TargetAssetPath = bLocalScope ? AssetPath : GetBlueprintAssetPathFromClass(OwnerClass);
					const FString TargetOwnerPath = TargetAssetPath.IsEmpty() ? GetOwnerPathForClass(OwnerClass) : TargetAssetPath;

					FString TargetSymbolId;
					if (bLocalScope)
					{
						if (MemberGuid.IsValid())
						{
							TargetSymbolId = LocalVariableIdByScope.FindRef(
								MakeScopedVariableLookupKey(MemberScopeName, MemberGuid, MemberName));
						}
						if (TargetSymbolId.IsEmpty())
						{
							TargetSymbolId = LocalVariableIdByScope.FindRef(
								MakeScopedVariableLookupKey(MemberScopeName, FGuid(), MemberName));
						}
					}
					else if (MemberReference.IsSelfContext() || TargetAssetPath == AssetPath)
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
						const FString VariableStableKey = bLocalScope
							? MakeLocalVariableStableKey(MemberScopeName, MemberGuid, MemberName)
							: StableKey(MemberGuid, MemberName.ToString());
						TargetSymbolId = MakeSymbolId(
							TEXT("variable"),
							TargetOwnerPath.IsEmpty() ? AssetPath : TargetOwnerPath,
							VariableStableKey);
					}

					const FString Kind = Cast<UK2Node_VariableSet>(Node) ? TEXT("writes") : TEXT("reads");
					const bool bDelegateTarget = DelegateSymbolIds.Contains(TargetSymbolId)
						|| CastField<FMulticastDelegateProperty>(VariableNode->GetPropertyForVariable()) != nullptr;
					TSharedRef<FJsonObject> Reference = MakeReference(
						MakeNodeReferenceId(Kind, Graph, Node, TargetSymbolId),
						Kind,
						SourceSymbolId,
						TargetSymbolId,
						bDelegateTarget ? TEXT("delegate") : TEXT("variable"),
						MemberName.ToString(),
						TargetAssetPath);
					Reference->SetStringField(TEXT("variableScope"), bLocalScope ? TEXT("local") : TEXT("member"));
					if (bLocalScope)
					{
						Reference->SetStringField(TEXT("scopeName"), MemberScopeName);
					}
					AddNodeLocation(Reference, Graph, Node);
					AddReference(Reference, OutReferences, ReferenceIds);
					continue;
				}

				if (const UK2Node_CreateDelegate* CreateDelegateNode = Cast<UK2Node_CreateDelegate>(Node))
				{
					const FName HandlerFunctionName = CreateDelegateNode->GetFunctionName();
					FString HandlerKind;
					FString HandlerAssetPath;
					FString HandlerPath;
					const FString HandlerSymbolId = ResolveCallableSymbol(
						HandlerFunctionName,
						CreateDelegateNode->GetScopeClass(),
						HandlerKind,
						HandlerAssetPath,
						HandlerPath);
					const UEdGraphPin* ObjectPin = CreateDelegateNode->GetObjectInPin();
					const UObject* ObjectType = ObjectPin ? ObjectPin->PinType.PinSubCategoryObject.Get() : nullptr;
					TSharedRef<FJsonObject> Reference = MakeReference(
						MakeNodeReferenceId(TEXT("delegate-creates"), Graph, Node, HandlerSymbolId),
						TEXT("delegate-creates"),
						SourceSymbolId,
						HandlerSymbolId,
						HandlerKind,
						HandlerFunctionName.ToString(),
						HandlerAssetPath);
					Reference->SetStringField(TEXT("targetPath"), HandlerPath);
					Reference->SetStringField(TEXT("signaturePath"), ObjectPath(CreateDelegateNode->GetDelegateSignature()));
					Reference->SetStringField(TEXT("objectTypePath"), ObjectPath(ObjectType));
					Reference->SetStringField(TEXT("objectNodeGuids"), GetLinkedNodeGuids(ObjectPin));
					Reference->SetStringField(TEXT("delegateOutputNodeGuids"), GetLinkedNodeGuids(CreateDelegateNode->GetDelegateOutPin()));
					AddNodeLocation(Reference, Graph, Node);
					AddReference(Reference, OutReferences, ReferenceIds);
					continue;
				}

				if (const UK2Node_BaseMCDelegate* DelegateNode = Cast<UK2Node_BaseMCDelegate>(Node))
				{
					const FMemberReference& MemberReference = DelegateNode->DelegateReference;
					const FGuid DelegateGuid = MemberReference.GetMemberGuid();
					const FName DelegateName = MemberReference.GetMemberName();
					UClass* OwnerClass = MemberReference.IsSelfContext()
						? (Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass.Get() : Blueprint->GeneratedClass.Get())
						: MemberReference.GetMemberParentClass();
					const FString TargetAssetPath = GetBlueprintAssetPathFromClass(OwnerClass);
					const FString TargetOwnerPath = TargetAssetPath.IsEmpty() ? GetOwnerPathForClass(OwnerClass) : TargetAssetPath;
					FString TargetSymbolId;
					if (TargetAssetPath == AssetPath || MemberReference.IsSelfContext())
					{
						if (DelegateGuid.IsValid())
						{
							TargetSymbolId = DelegateIdByGuid.FindRef(DelegateGuid);
						}
						if (TargetSymbolId.IsEmpty())
						{
							TargetSymbolId = DelegateIdByName.FindRef(DelegateName);
						}
					}
					if (TargetSymbolId.IsEmpty())
					{
						TargetSymbolId = MakeSymbolId(
							TEXT("delegate"),
							TargetOwnerPath.IsEmpty() ? ObjectPath(OwnerClass) : TargetOwnerPath,
							StableKey(DelegateGuid, DelegateName.ToString()));
					}

					FString ReferenceKind;
					FString DelegateOperation;
					if (Cast<UK2Node_AssignDelegate>(DelegateNode))
					{
						ReferenceKind = TEXT("delegate-assigns");
						DelegateOperation = TEXT("assign");
					}
					else if (Cast<UK2Node_AddDelegate>(DelegateNode))
					{
						ReferenceKind = TEXT("delegate-binds");
						DelegateOperation = TEXT("bind");
					}
					else if (Cast<UK2Node_RemoveDelegate>(DelegateNode))
					{
						ReferenceKind = TEXT("delegate-unbinds");
						DelegateOperation = TEXT("unbind");
					}
					else if (Cast<UK2Node_CallDelegate>(DelegateNode))
					{
						ReferenceKind = TEXT("delegate-broadcasts");
						DelegateOperation = TEXT("broadcast");
					}
					else if (Cast<UK2Node_ClearDelegate>(DelegateNode))
					{
						ReferenceKind = TEXT("delegate-clears");
						DelegateOperation = TEXT("clear");
					}
					else
					{
						ReferenceKind = TEXT("delegate-uses");
						DelegateOperation = TEXT("use");
					}

					FProperty* DelegateProperty = DelegateNode->GetProperty();
					TSharedRef<FJsonObject> Reference = MakeReference(
						MakeNodeReferenceId(ReferenceKind, Graph, Node, TargetSymbolId),
						ReferenceKind,
						SourceSymbolId,
						TargetSymbolId,
						TEXT("delegate"),
						DelegateName.ToString(),
						TargetAssetPath);
					Reference->SetStringField(TEXT("targetPath"), PropertyPath(DelegateProperty));
					Reference->SetStringField(TEXT("signaturePath"), ObjectPath(DelegateNode->GetDelegateSignature()));
					Reference->SetStringField(TEXT("delegateOperation"), DelegateOperation);
					Reference->SetStringField(TEXT("delegateOwnerClassPath"), ObjectPath(OwnerClass));
					Reference->SetStringField(
						TEXT("targetObjectNodeGuids"),
						GetLinkedNodeGuids(DelegateNode->FindPin(UEdGraphSchema_K2::PN_Self)));
					if (DelegateOperation == TEXT("bind")
						|| DelegateOperation == TEXT("unbind")
						|| DelegateOperation == TEXT("assign"))
					{
						AddDelegateHandlerFields(DelegateNode, Reference);
					}
					AddNodeLocation(Reference, Graph, Node);
					AddReference(Reference, OutReferences, ReferenceIds);
					continue;
				}

				if (const UK2Node_DynamicCast* DynamicCastNode = Cast<UK2Node_DynamicCast>(Node))
				{
					UClass* TargetType = DynamicCastNode->TargetType.Get();
					const FString TargetPath = ObjectPath(TargetType);
					const FString TargetAssetPath = GetBlueprintAssetPathFromClass(TargetType);
					const FString TargetSymbolId = MakeSymbolId(
						TEXT("class"),
						TargetAssetPath.IsEmpty() ? TargetPath : TargetAssetPath);
					const UEdGraphPin* SourcePin = DynamicCastNode->GetCastSourcePin();
					const UObject* SourceTypeObject = SourcePin ? SourcePin->PinType.PinSubCategoryObject.Get() : nullptr;

					TSharedRef<FJsonObject> Reference = MakeReference(
						MakeNodeReferenceId(TEXT("casts"), Graph, Node, TargetSymbolId),
						TEXT("casts"),
						SourceSymbolId,
						TargetSymbolId,
						TEXT("class"),
						TargetType ? TargetType->GetName() : FString(),
						TargetAssetPath);
					Reference->SetStringField(TEXT("targetPath"), TargetPath);
					Reference->SetStringField(TEXT("sourceTypePath"), ObjectPath(SourceTypeObject));
					Reference->SetStringField(TEXT("castMode"), DynamicCastNode->IsNodePure() ? TEXT("pure") : TEXT("impure"));
					Reference->SetStringField(TEXT("successNodeGuids"), GetLinkedNodeGuids(DynamicCastNode->GetValidCastPin()));
					Reference->SetStringField(TEXT("failureNodeGuids"), GetLinkedNodeGuids(DynamicCastNode->GetInvalidCastPin()));
					AddNodeLocation(Reference, Graph, Node);
					AddReference(Reference, OutReferences, ReferenceIds);
					continue;
				}

				if (const UK2Node_CallFunction* CallFunctionNode = Cast<UK2Node_CallFunction>(Node))
				{
					const bool bInterfaceMessage = Cast<UK2Node_Message>(Node) != nullptr;
					const FString ReferenceKind = bInterfaceMessage ? TEXT("interface-calls") : TEXT("calls");
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
					FString TargetKind = bInterfaceMessage ? TEXT("interface-function") : TEXT("function");

					if (TargetAssetPath == AssetPath || CallFunctionNode->FunctionReference.IsSelfContext())
					{
						TargetSymbolId = FunctionIdByName.FindRef(FunctionName);
						if (TargetSymbolId.IsEmpty())
						{
							TargetSymbolId = EventIdByName.FindRef(FunctionName);
							if (!TargetSymbolId.IsEmpty())
							{
								TargetKind = TEXT("event");
							}
						}
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
						MakeNodeReferenceId(ReferenceKind, Graph, Node, TargetSymbolId),
						ReferenceKind,
						SourceSymbolId,
						TargetSymbolId,
						TargetKind,
						FunctionName.ToString(),
						TargetAssetPath);
					Reference->SetStringField(TEXT("targetPath"), ObjectPath(TargetFunction));
					if (bInterfaceMessage)
					{
						Reference->SetStringField(TEXT("dispatchKind"), TEXT("message"));
					}
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

	if (UPackage* Package = Blueprint->GetOutermost())
	{
		IAssetRegistry& AssetRegistry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(
			TEXT("AssetRegistry")).Get();
		TArray<FAssetDependency> Dependencies;
		if (AssetRegistry.GetDependencies(
			FAssetIdentifier(Package->GetFName()),
			Dependencies,
			UE::AssetRegistry::EDependencyCategory::All))
		{
			Dependencies.Sort([](const FAssetDependency& Left, const FAssetDependency& Right)
			{
				return Left.LexicalLess(Right);
			});

			for (const FAssetDependency& Dependency : Dependencies)
			{
				if (!Dependency.AssetId.IsValid()
					|| Dependency.AssetId.PackageName == Package->GetFName())
				{
					continue;
				}

				const FString Identifier = Dependency.AssetId.ToString();
				const FString TargetPackageName = Dependency.AssetId.PackageName.ToString();
				const FString TargetAssetPath = ResolveAssetPathForPackage(
					AssetRegistry,
					Dependency.AssetId.PackageName);
				const bool bSearchableName = Dependency.AssetId.IsValue();
				const FString TargetKind = bSearchableName
					? TEXT("searchable-name")
					: (!TargetAssetPath.IsEmpty()
						? TEXT("asset")
						: (Dependency.AssetId.GetPrimaryAssetId().IsValid() ? TEXT("primary-asset") : TEXT("package")));
				const FString TargetSymbolId = MakeSymbolId(
					TargetKind,
					bSearchableName ? Identifier : (TargetAssetPath.IsEmpty() ? Identifier : TargetAssetPath));
				const FString ReferenceKind = AssetDependencyReferenceKind(Dependency);
				const FString DependencyCategory = DependencyCategoryName(Dependency.Category);
				const FString DependencyProperties = DependencyPropertiesName(
					Dependency.Category,
					Dependency.Properties);
				const FString TargetName = !Dependency.AssetId.ValueName.IsNone()
					? Dependency.AssetId.ValueName.ToString()
					: (!Dependency.AssetId.ObjectName.IsNone()
						? Dependency.AssetId.ObjectName.ToString()
						: FPackageName::GetShortName(TargetPackageName));
				TSharedRef<FJsonObject> Reference = MakeReference(
					MakeSymbolId(
						TEXT("reference"),
						ReferenceKind,
						AssetSymbolId + TEXT("|") + TargetSymbolId + TEXT("|") + DependencyProperties),
					ReferenceKind,
					AssetSymbolId,
					TargetSymbolId,
					TargetKind,
					TargetName,
					TargetAssetPath);
				Reference->SetStringField(TEXT("targetPath"), Identifier);
				Reference->SetStringField(TEXT("targetPackageName"), TargetPackageName);
				Reference->SetStringField(TEXT("targetObjectName"), NameOrEmpty(Dependency.AssetId.ObjectName));
				Reference->SetStringField(TEXT("targetValueName"), NameOrEmpty(Dependency.AssetId.ValueName));
				Reference->SetStringField(
					TEXT("targetPrimaryAssetType"),
					NameOrEmpty(Dependency.AssetId.PrimaryAssetType.GetName()));
				Reference->SetStringField(TEXT("dependencyCategory"), DependencyCategory);
				Reference->SetStringField(TEXT("dependencyProperties"), DependencyProperties);
				Reference->SetStringField(TEXT("dependencyDomain"), DependencyDomainName(TargetPackageName));
				Reference->SetBoolField(
					TEXT("hard"),
					EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Hard));
				Reference->SetBoolField(
					TEXT("game"),
					EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Game));
				Reference->SetBoolField(
					TEXT("build"),
					EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Build));
				Reference->SetBoolField(
					TEXT("direct"),
					EnumHasAnyFlags(Dependency.Properties, UE::AssetRegistry::EDependencyProperty::Direct));
				AddReference(Reference, OutReferences, ReferenceIds);
			}
		}

		auto AddManageReference = [&OutReferences, &ReferenceIds, &AssetSymbolId, &AssetPath, Blueprint, Package](
			const FString& ManagerIdentifier,
			const FString& ManagerPackageName,
			const FString& ManagerAssetPath,
			const FString& ManagerName,
			const bool bDirect,
			const FString& Resolution)
		{
			const FString ManagerKind = !ManagerAssetPath.IsEmpty() ? TEXT("asset") : TEXT("package");
			const FString ManagerSymbolId = MakeSymbolId(
				ManagerKind,
				ManagerAssetPath.IsEmpty() ? ManagerIdentifier : ManagerAssetPath);
			const FString ReferenceKind = bDirect ? TEXT("manages-direct") : TEXT("manages-indirect");
			const FString DependencyProperties = bDirect ? TEXT("direct") : TEXT("indirect");
			TSharedRef<FJsonObject> Reference = MakeReference(
				MakeSymbolId(
					TEXT("reference"),
					ReferenceKind,
					ManagerSymbolId + TEXT("|") + AssetSymbolId + TEXT("|") + DependencyProperties),
				ReferenceKind,
				ManagerSymbolId,
				AssetSymbolId,
				TEXT("asset"),
				Blueprint->GetName(),
				AssetPath);
			Reference->SetStringField(TEXT("targetPath"), AssetPath);
			Reference->SetStringField(TEXT("targetPackageName"), Package->GetName());
			Reference->SetStringField(TEXT("managerName"), ManagerName);
			Reference->SetStringField(TEXT("managerAssetPath"), ManagerAssetPath);
			Reference->SetStringField(TEXT("managerPackageName"), ManagerPackageName);
			Reference->SetStringField(TEXT("managerPath"), ManagerIdentifier);
			Reference->SetStringField(TEXT("managerResolution"), Resolution);
			Reference->SetStringField(TEXT("dependencyCategory"), TEXT("manage"));
			Reference->SetStringField(TEXT("dependencyProperties"), DependencyProperties);
			Reference->SetStringField(TEXT("dependencyDomain"), DependencyDomainName(Package->GetName()));
			Reference->SetBoolField(TEXT("direct"), bDirect);
			Reference->SetBoolField(TEXT("incoming"), true);
			AddReference(Reference, OutReferences, ReferenceIds);
		};

		TSet<FName> ResolvedManagerPackages;
		TArray<FAssetDependency> ManageReferencers;
		if (AssetRegistry.GetReferencers(
			FAssetIdentifier(Package->GetFName()),
			ManageReferencers,
			UE::AssetRegistry::EDependencyCategory::Manage))
		{
			ManageReferencers.Sort([](const FAssetDependency& Left, const FAssetDependency& Right)
			{
				return Left.LexicalLess(Right);
			});

			for (const FAssetDependency& Referencer : ManageReferencers)
			{
				if (!Referencer.AssetId.IsValid()
					|| Referencer.AssetId.PackageName == Package->GetFName())
				{
					continue;
				}

				const FString ManagerIdentifier = Referencer.AssetId.ToString();
				const FString ManagerPackageName = Referencer.AssetId.PackageName.ToString();
				const FString ManagerAssetPath = ResolveAssetPathForPackage(
					AssetRegistry,
					Referencer.AssetId.PackageName);
				const FString ManagerName = !Referencer.AssetId.ObjectName.IsNone()
					? Referencer.AssetId.ObjectName.ToString()
					: FPackageName::GetShortName(ManagerPackageName);
				const bool bDirect = EnumHasAnyFlags(
					Referencer.Properties,
					UE::AssetRegistry::EDependencyProperty::Direct);
				ResolvedManagerPackages.Add(Referencer.AssetId.PackageName);
				AddManageReference(
					ManagerIdentifier,
					ManagerPackageName,
					ManagerAssetPath,
					ManagerName,
					bDirect,
					TEXT("asset-registry"));
			}
		}

		TArray<FAssetData> PrimaryAssetLabels;
		if (AssetRegistry.GetAssetsByClass(
			UPrimaryAssetLabel::StaticClass()->GetClassPathName(),
			PrimaryAssetLabels,
			true))
		{
			PrimaryAssetLabels.Sort([](const FAssetData& Left, const FAssetData& Right)
			{
				return Left.GetSoftObjectPath().ToString() < Right.GetSoftObjectPath().ToString();
			});

			for (const FAssetData& LabelData : PrimaryAssetLabels)
			{
				if (ResolvedManagerPackages.Contains(LabelData.PackageName))
				{
					continue;
				}

				const UPrimaryAssetLabel* Label = Cast<UPrimaryAssetLabel>(LabelData.GetAsset());
				if (!Label)
				{
					continue;
				}

				bool bExplicitMatch = false;
				for (const TSoftObjectPtr<UObject>& ExplicitAsset : Label->ExplicitAssets)
				{
					if (ExplicitAsset.ToSoftObjectPath().GetLongPackageFName() == Package->GetFName())
					{
						bExplicitMatch = true;
						break;
					}
				}
				if (!bExplicitMatch)
				{
					for (const TSoftClassPtr<UObject>& ExplicitBlueprint : Label->ExplicitBlueprints)
					{
						if (ExplicitBlueprint.ToSoftObjectPath().GetLongPackageFName() == Package->GetFName())
						{
							bExplicitMatch = true;
							break;
						}
					}
				}
				if (!bExplicitMatch)
				{
					continue;
				}

				AddManageReference(
					LabelData.PackageName.ToString(),
					LabelData.PackageName.ToString(),
					LabelData.GetSoftObjectPath().ToString(),
					LabelData.AssetName.ToString(),
					true,
					TEXT("primary-asset-label-explicit"));
			}
		}
	}

	OutSymbolCount = OutSymbols.Num();
	OutReferenceCount = OutReferences.Num();
}
