#pragma once

#include "CoreMinimal.h"
#include "Blueprint/BlueprintExtension.h"
#include "EdGraph/EdGraph.h"
#include "Engine/Blueprint.h"

/**
 * One graph discovered in a Blueprint, with the classification used by the
 * canonical exporter and the symbol/reference analysis.
 *
 * The top-level Blueprint graph arrays keep their historical kinds so existing
 * graph symbols and graph ordering remain stable.  Graphs which are only
 * reachable through UEdGraph::SubGraphs (collapsed graphs, state machine
 * internals, nested editor graphs) are classified as "subgraph".
 */
struct FBlueprintContextGraphEntry
{
	UEdGraph* Graph = nullptr;
	FString Kind;
	FString ParentGraphName;
};

namespace UEAgentKitGraphDiscovery
{
	inline bool IsTransientGraph(const UEdGraph* Graph)
	{
		return Graph == nullptr
			|| Graph->HasAnyFlags(RF_Transient | RF_BeginDestroyed | RF_FinishDestroyed);
	}

	/**
	 * Discover every persistent graph reachable from the Blueprint.
	 *
	 * UBlueprint::GetAllGraphs() is the engine's own definition of the graph
	 * set: the four historical arrays, implemented-interface graphs, extension
	 * graphs, and all UEdGraph::SubGraphs recursively.  We preserve the
	 * historical top-level order first, then append the newly discovered
	 * graphs, so graph short IDs for existing graphs do not move.
	 */
	inline void CollectBlueprintGraphs(
		UBlueprint* Blueprint,
		const FString& GraphFilter,
		TArray<FBlueprintContextGraphEntry>& OutGraphs)
	{
		OutGraphs.Reset();
		if (Blueprint == nullptr)
		{
			return;
		}

		TMap<UEdGraph*, FString> KindByGraph;
		TArray<UEdGraph*> OrderedGraphs;

		auto AddTopLevel = [&KindByGraph, &OrderedGraphs](UEdGraph* Graph, const TCHAR* Kind)
		{
			if (Graph == nullptr || KindByGraph.Contains(Graph))
			{
				return;
			}
			KindByGraph.Add(Graph, Kind);
			OrderedGraphs.Add(Graph);
		};

		for (UEdGraph* Graph : Blueprint->UbergraphPages)
		{
			AddTopLevel(Graph, TEXT("uber"));
		}
		for (UEdGraph* Graph : Blueprint->FunctionGraphs)
		{
			AddTopLevel(Graph, TEXT("function"));
		}
		for (UEdGraph* Graph : Blueprint->MacroGraphs)
		{
			AddTopLevel(Graph, TEXT("macro"));
		}
		for (UEdGraph* Graph : Blueprint->DelegateSignatureGraphs)
		{
			AddTopLevel(Graph, TEXT("delegate-signature"));
		}
		for (const FBPInterfaceDescription& InterfaceDescription : Blueprint->ImplementedInterfaces)
		{
			for (UEdGraph* Graph : InterfaceDescription.Graphs)
			{
				AddTopLevel(Graph, TEXT("interface"));
			}
		}
		for (const TObjectPtr<UBlueprintExtension>& Extension : Blueprint->GetExtensions())
		{
			if (Extension == nullptr)
			{
				continue;
			}
			TArray<UEdGraph*> ExtensionGraphs;
			Extension->GetAllGraphs(ExtensionGraphs);
			for (UEdGraph* Graph : ExtensionGraphs)
			{
				AddTopLevel(Graph, TEXT("extension"));
			}
		}

		TArray<UEdGraph*> AllGraphs;
		Blueprint->GetAllGraphs(AllGraphs);

		TSet<UEdGraph*> AddedGraphs;
		auto AddGraph = [&](UEdGraph* Graph, const FString& Kind)
		{
			if (Graph == nullptr || IsTransientGraph(Graph) || AddedGraphs.Contains(Graph))
			{
				return;
			}
			if (!GraphFilter.IsEmpty()
				&& !Graph->GetName().Equals(GraphFilter, ESearchCase::IgnoreCase))
			{
				return;
			}

			const UEdGraph* ParentGraph = UEdGraph::GetOuterGraph(Graph);
			AddedGraphs.Add(Graph);
			OutGraphs.Add({ Graph, Kind, ParentGraph ? ParentGraph->GetName() : FString() });
		};

		// Keep the historical top-level order first.  This is the order used by
		// the existing canonical output and therefore by graph short IDs.
		for (UEdGraph* Graph : OrderedGraphs)
		{
			AddGraph(Graph, KindByGraph.FindRef(Graph));
		}

		// Append everything else (collapsed graphs, nested subgraphs, state
		// machine graphs, and any extension child graph) deterministically in
		// the engine's GetAllGraphs order.
		for (UEdGraph* Graph : AllGraphs)
		{
			if (Graph == nullptr || AddedGraphs.Contains(Graph))
			{
				continue;
			}
			const FString* KnownKind = KindByGraph.Find(Graph);
			AddGraph(Graph, KnownKind ? *KnownKind : FString(TEXT("subgraph")));
		}
	}
}
