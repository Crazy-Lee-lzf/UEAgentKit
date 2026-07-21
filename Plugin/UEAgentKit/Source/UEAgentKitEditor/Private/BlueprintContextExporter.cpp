#include "BlueprintContextExporter.h"

#include "BlueprintContextAnalysis.h"

#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "Engine/SCS_Node.h"
#include "Engine/SimpleConstructionScript.h"
#include "HAL/FileManager.h"
#include "K2Node_PromotableOperator.h"
#include "Misc/EngineVersion.h"
#include "Misc/App.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/FieldIterator.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

namespace BlueprintContextExporterPrivate
{
	static constexpr const TCHAR* SchemaVersion = TEXT("1.1");
	static constexpr const TCHAR* ExporterVersion = TEXT("0.3.7");

	FString GuidToString(const FGuid& Guid)
	{
		return Guid.IsValid() ? Guid.ToString(EGuidFormats::DigitsWithHyphensLower) : FString();
	}

	FString ObjectPath(const UObject* Object)
	{
		return Object ? Object->GetPathName() : FString();
	}

	bool NeedsDerivedPinId(const UEdGraphPin* Pin)
	{
		const UEdGraphNode* Node = Pin ? Pin->GetOwningNode() : nullptr;
		return Pin
			&& Node
			&& Node->IsA<UK2Node_PromotableOperator>()
			&& Pin->PinName == TEXT("ErrorTolerance")
			&& Pin->bHidden
			&& Pin->LinkedTo.IsEmpty()
			&& Pin->PinType.PinCategory.IsNone()
			&& Pin->DefaultValue.IsEmpty()
			&& !Pin->DefaultObject;
	}

	FString ExportPinId(const UEdGraphPin* Pin)
	{
		if (!Pin)
		{
			return FString();
		}

		if (!NeedsDerivedPinId(Pin))
		{
			return GuidToString(Pin->PinId);
		}

		const UEdGraphNode* Node = Pin->GetOwningNode();
		const FString StableKey = FString::Printf(
			TEXT("%s|%s|%s|transient-promotable-pin"),
			*GuidToString(Node ? Node->NodeGuid : FGuid()),
			Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"),
			*Pin->PinName.ToString());
		const FString Digest = FMD5::HashAnsiString(*StableKey).ToLower();
		return Digest.Len() == 32
			? FString::Printf(
				TEXT("%s-%s-%s-%s-%s"),
				*Digest.Mid(0, 8),
				*Digest.Mid(8, 4),
				*Digest.Mid(12, 4),
				*Digest.Mid(16, 4),
				*Digest.Mid(20, 12))
			: GuidToString(Pin->PinId);
	}

	FString BlueprintTypeToString(const EBlueprintType Type)
	{
		switch (Type)
		{
		case BPTYPE_Normal:
			return TEXT("normal");
		case BPTYPE_Const:
			return TEXT("const");
		case BPTYPE_MacroLibrary:
			return TEXT("macro-library");
		case BPTYPE_Interface:
			return TEXT("interface");
		case BPTYPE_LevelScript:
			return TEXT("level-script");
		case BPTYPE_FunctionLibrary:
			return TEXT("function-library");
		default:
			return TEXT("unknown");
		}
	}

	FString ContainerTypeToString(const EPinContainerType ContainerType)
	{
		switch (ContainerType)
		{
		case EPinContainerType::Array:
			return TEXT("array");
		case EPinContainerType::Set:
			return TEXT("set");
		case EPinContainerType::Map:
			return TEXT("map");
		default:
			return TEXT("none");
		}
	}

	TSharedRef<FJsonObject> PinTypeToJson(const FEdGraphPinType& PinType)
	{
		TSharedRef<FJsonObject> TypeObject = MakeShared<FJsonObject>();
		TypeObject->SetStringField(TEXT("category"), PinType.PinCategory.ToString());
		TypeObject->SetStringField(TEXT("subcategory"), PinType.PinSubCategory.ToString());
		TypeObject->SetStringField(TEXT("subcategoryObject"), ObjectPath(PinType.PinSubCategoryObject.Get()));
		TypeObject->SetStringField(TEXT("container"), ContainerTypeToString(PinType.ContainerType));
		TypeObject->SetBoolField(TEXT("isReference"), PinType.bIsReference);
		TypeObject->SetBoolField(TEXT("isConst"), PinType.bIsConst);
		TypeObject->SetBoolField(TEXT("isWeakPointer"), PinType.bIsWeakPointer);

		if (PinType.ContainerType == EPinContainerType::Map)
		{
			TSharedRef<FJsonObject> ValueTypeObject = MakeShared<FJsonObject>();
			ValueTypeObject->SetStringField(TEXT("category"), PinType.PinValueType.TerminalCategory.ToString());
			ValueTypeObject->SetStringField(TEXT("subcategory"), PinType.PinValueType.TerminalSubCategory.ToString());
			ValueTypeObject->SetStringField(
				TEXT("subcategoryObject"),
				ObjectPath(PinType.PinValueType.TerminalSubCategoryObject.Get()));
			ValueTypeObject->SetBoolField(TEXT("isConst"), PinType.PinValueType.bTerminalIsConst);
			ValueTypeObject->SetBoolField(TEXT("isWeakPointer"), PinType.PinValueType.bTerminalIsWeakPointer);
			TypeObject->SetObjectField(TEXT("valueType"), ValueTypeObject);
		}

		return TypeObject;
	}

	FString PinTypeToCompactString(const TSharedPtr<FJsonObject>& TypeObject)
	{
		if (!TypeObject.IsValid())
		{
			return TEXT("unknown");
		}

		FString Category;
		FString SubcategoryObject;
		FString Container;
		TypeObject->TryGetStringField(TEXT("category"), Category);
		TypeObject->TryGetStringField(TEXT("subcategoryObject"), SubcategoryObject);
		TypeObject->TryGetStringField(TEXT("container"), Container);

		FString BaseType = Category.IsEmpty() ? TEXT("unknown") : Category;
		if (!SubcategoryObject.IsEmpty())
		{
			BaseType += FString::Printf(TEXT("<%s>"), *SubcategoryObject);
		}

		if (Container == TEXT("array"))
		{
			return FString::Printf(TEXT("array<%s>"), *BaseType);
		}
		if (Container == TEXT("set"))
		{
			return FString::Printf(TEXT("set<%s>"), *BaseType);
		}
		if (Container == TEXT("map"))
		{
			const TSharedPtr<FJsonObject>* ValueTypeObject = nullptr;
			FString ValueType = TEXT("unknown");
			if (TypeObject->TryGetObjectField(TEXT("valueType"), ValueTypeObject) && ValueTypeObject)
			{
				ValueType = PinTypeToCompactString(*ValueTypeObject);
			}
			return FString::Printf(TEXT("map<%s,%s>"), *BaseType, *ValueType);
		}

		return BaseType;
	}

	FString NormalizeTransientPropertyBagNames(FString Value)
	{
		static const FString Prefix = TEXT("/Engine/Transient.PropertyBag_");
		static const FString Marker = TEXT("<transient>");
		int32 SearchFrom = 0;
		while (SearchFrom < Value.Len())
		{
			const int32 PrefixIndex = Value.Find(
				Prefix,
				ESearchCase::CaseSensitive,
				ESearchDir::FromStart,
				SearchFrom);
			if (PrefixIndex == INDEX_NONE)
			{
				break;
			}

			const int32 SuffixStart = PrefixIndex + Prefix.Len();
			int32 SuffixEnd = SuffixStart;
			while (SuffixEnd < Value.Len() && FChar::IsHexDigit(Value[SuffixEnd]))
			{
				++SuffixEnd;
			}

			if (SuffixEnd > SuffixStart)
			{
				Value.RemoveAt(SuffixStart, SuffixEnd - SuffixStart, EAllowShrinking::No);
				Value.InsertAt(SuffixStart, Marker);
				SearchFrom = SuffixStart + Marker.Len();
			}
			else
			{
				SearchFrom = SuffixStart;
			}
		}
		return Value;
	}

	FString ExportPropertyValue(const FProperty* Property, const void* Container, UObject* ParentObject)
	{
		if (!Property || !Container)
		{
			return FString();
		}

		const void* ValuePtr = Property->ContainerPtrToValuePtr<void>(Container);
		FString Value;
		Property->ExportTextItem_Direct(Value, ValuePtr, nullptr, ParentObject, PPF_None);
		Value = NormalizeTransientPropertyBagNames(MoveTemp(Value));
		if (Value.Len() > 65536)
		{
			Value.LeftInline(65536, EAllowShrinking::No);
			Value += TEXT("<truncated>");
		}
		return Value;
	}

	TSharedRef<FJsonObject> ExportReflectedProperties(
		const UObject* Object,
		const TSet<FName>& SkippedProperties,
		const bool bChangedOnly)
	{
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		if (!Object)
		{
			return Result;
		}

		const UObject* Archetype = Object->GetArchetype();
		for (TFieldIterator<FProperty> It(Object->GetClass(), EFieldIterationFlags::IncludeSuper); It; ++It)
		{
			FProperty* Property = *It;
			if (!Property || SkippedProperties.Contains(Property->GetFName()))
			{
				continue;
			}
			if (Property->HasAnyPropertyFlags(CPF_Transient | CPF_DuplicateTransient | CPF_Deprecated | CPF_SkipSerialization))
			{
				continue;
			}
			if (bChangedOnly && Archetype && Property->Identical_InContainer(Object, Archetype))
			{
				continue;
			}

			const FString Value = ExportPropertyValue(Property, Object, const_cast<UObject*>(Object));
			if (!Value.IsEmpty())
			{
				Result->SetStringField(Property->GetName(), Value);
			}
		}
		return Result;
	}

	FString PropertyFlagsToString(const EPropertyFlags Flags)
	{
		return FString::Printf(TEXT("0x%016llX"), static_cast<unsigned long long>(Flags));
	}

	FString FunctionFlagsToString(const EFunctionFlags Flags)
	{
		return FString::Printf(TEXT("0x%08X"), static_cast<uint32>(Flags));
	}

	bool ProfileIncludesStructure(const EBlueprintContextProfile Profile)
	{
		return Profile != EBlueprintContextProfile::Index;
	}

	bool ProfileIncludesDefaults(const EBlueprintContextProfile Profile)
	{
		return Profile == EBlueprintContextProfile::Structure
			|| Profile == EBlueprintContextProfile::Logic
			|| Profile == EBlueprintContextProfile::Defaults
			|| Profile == EBlueprintContextProfile::Full
			|| Profile == EBlueprintContextProfile::AI;
	}

	bool ProfileIncludesGraphNodes(const EBlueprintContextProfile Profile)
	{
		return Profile == EBlueprintContextProfile::Logic
			|| Profile == EBlueprintContextProfile::Full
			|| Profile == EBlueprintContextProfile::AI;
	}

	FString MakeRelativeAssetFileStem(const UBlueprint* Blueprint)
	{
		FString FileStem = Blueprint ? Blueprint->GetPathName() : TEXT("UnknownBlueprint");
		FileStem.RemoveFromStart(TEXT("/"));
		FileStem.ReplaceInline(TEXT("."), TEXT("_"));
		FileStem.ReplaceInline(TEXT(":"), TEXT("_"));
		return FileStem;
	}

	FString EscapeBpctx(const FString& Value)
	{
		FString Escaped = Value;
		Escaped.ReplaceInline(TEXT("\\"), TEXT("\\\\"));
		Escaped.ReplaceInline(TEXT("|"), TEXT("\\p"));
		Escaped.ReplaceInline(TEXT("\r"), TEXT(""));
		Escaped.ReplaceInline(TEXT("\n"), TEXT("\\n"));
		return Escaped;
	}

	void AddOptionalField(TArray<FString>& Fields, const TCHAR* Key, const FString& Value)
	{
		if (!Value.IsEmpty())
		{
			Fields.Add(FString::Printf(TEXT("%s=%s"), Key, *EscapeBpctx(Value)));
		}
	}

	void AppendBpctxLine(FString& Output, const TArray<FString>& Fields)
	{
		Output += FString::Join(Fields, TEXT("|"));
		Output += LINE_TERMINATOR;
	}

	TSharedRef<FJsonObject> ExportFunction(UFunction* Function)
	{
		TSharedRef<FJsonObject> FunctionObject = MakeShared<FJsonObject>();
		if (!Function)
		{
			return FunctionObject;
		}

		FunctionObject->SetStringField(TEXT("name"), Function->GetName());
		FunctionObject->SetStringField(TEXT("path"), Function->GetPathName());
		FunctionObject->SetStringField(TEXT("flags"), FunctionFlagsToString(Function->FunctionFlags));

		TArray<TSharedPtr<FJsonValue>> Inputs;
		TArray<TSharedPtr<FJsonValue>> Outputs;
		for (TFieldIterator<FProperty> It(Function); It; ++It)
		{
			FProperty* Property = *It;
			if (!Property || !Property->HasAnyPropertyFlags(CPF_Parm))
			{
				continue;
			}

			TSharedRef<FJsonObject> ParameterObject = MakeShared<FJsonObject>();
			ParameterObject->SetStringField(TEXT("name"), Property->GetName());
			ParameterObject->SetStringField(TEXT("cppType"), Property->GetCPPType());
			ParameterObject->SetStringField(TEXT("flags"), PropertyFlagsToString(Property->GetPropertyFlags()));

			if (Property->HasAnyPropertyFlags(CPF_ReturnParm | CPF_OutParm))
			{
				Outputs.Add(MakeShared<FJsonValueObject>(ParameterObject));
			}
			else
			{
				Inputs.Add(MakeShared<FJsonValueObject>(ParameterObject));
			}
		}

		FunctionObject->SetArrayField(TEXT("inputs"), Inputs);
		FunctionObject->SetArrayField(TEXT("outputs"), Outputs);
		return FunctionObject;
	}

	void ExportComponentNode(
		USCS_Node* Node,
		const FString& ParentId,
		const FBlueprintContextExportOptions& Options,
		TArray<TSharedPtr<FJsonValue>>& Components,
		FBlueprintContextExportResult& Result)
	{
		if (!Node)
		{
			return;
		}

		const FString ComponentId = FString::Printf(TEXT("c%d"), Result.ComponentCount++);
		TSharedRef<FJsonObject> ComponentObject = MakeShared<FJsonObject>();
		ComponentObject->SetStringField(TEXT("id"), ComponentId);
		ComponentObject->SetStringField(TEXT("name"), Node->GetVariableName().ToString());
		ComponentObject->SetStringField(TEXT("class"), ObjectPath(Node->ComponentClass.Get()));
		ComponentObject->SetStringField(TEXT("parentId"), ParentId);
		ComponentObject->SetStringField(TEXT("templatePath"), ObjectPath(Node->ComponentTemplate));

		if (ProfileIncludesDefaults(Options.Profile) && Node->ComponentTemplate)
		{
			static const TSet<FName> SkippedTemplateProperties;
			ComponentObject->SetObjectField(
				TEXT("templateOverrides"),
				ExportReflectedProperties(
					Node->ComponentTemplate,
					SkippedTemplateProperties,
					!Options.bIncludeUnchangedDefaults));
		}

		if (Options.Profile == EBlueprintContextProfile::Full)
		{
			static const TSet<FName> SkippedNodeProperties = {
				TEXT("ChildNodes"),
				TEXT("ComponentTemplate"),
				TEXT("CookedComponentInstancingData")
			};
			ComponentObject->SetObjectField(
				TEXT("scsProperties"),
				ExportReflectedProperties(Node, SkippedNodeProperties, false));
		}

		Components.Add(MakeShared<FJsonValueObject>(ComponentObject));
		for (USCS_Node* ChildNode : Node->GetChildNodes())
		{
			ExportComponentNode(ChildNode, ComponentId, Options, Components, Result);
		}
	}

	TSharedRef<FJsonObject> ExportPin(
		const UEdGraphPin* Pin,
		FBlueprintContextExportResult& Result)
	{
		TSharedRef<FJsonObject> PinObject = MakeShared<FJsonObject>();
		if (!Pin)
		{
			return PinObject;
		}

		++Result.PinCount;
		PinObject->SetStringField(TEXT("id"), ExportPinId(Pin));
		PinObject->SetStringField(TEXT("name"), Pin->PinName.ToString());
		PinObject->SetStringField(TEXT("friendlyName"), Pin->PinFriendlyName.ToString());
		PinObject->SetStringField(TEXT("direction"), Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
		PinObject->SetObjectField(TEXT("type"), PinTypeToJson(Pin->PinType));
		PinObject->SetStringField(TEXT("defaultValue"), Pin->DefaultValue);
		PinObject->SetStringField(TEXT("autogeneratedDefaultValue"), Pin->AutogeneratedDefaultValue);
		PinObject->SetStringField(TEXT("defaultObject"), ObjectPath(Pin->DefaultObject));
		PinObject->SetStringField(TEXT("defaultTextValue"), Pin->DefaultTextValue.ToString());
		PinObject->SetBoolField(TEXT("hidden"), Pin->bHidden);
		PinObject->SetBoolField(TEXT("notConnectable"), Pin->bNotConnectable);
		PinObject->SetBoolField(TEXT("defaultValueReadOnly"), Pin->bDefaultValueIsReadOnly);
		PinObject->SetBoolField(TEXT("defaultValueIgnored"), Pin->bDefaultValueIsIgnored);
		PinObject->SetBoolField(TEXT("orphaned"), Pin->bOrphanedPin);
		PinObject->SetBoolField(TEXT("advancedView"), Pin->bAdvancedView);

		TArray<TSharedPtr<FJsonValue>> Links;
		for (const UEdGraphPin* LinkedPin : Pin->LinkedTo)
		{
			if (!LinkedPin)
			{
				continue;
			}

			TSharedRef<FJsonObject> LinkObject = MakeShared<FJsonObject>();
			LinkObject->SetStringField(
				TEXT("targetNodeGuid"),
				LinkedPin->GetOwningNode() ? GuidToString(LinkedPin->GetOwningNode()->NodeGuid) : FString());
			LinkObject->SetStringField(TEXT("targetPinId"), ExportPinId(LinkedPin));
			LinkObject->SetStringField(TEXT("targetPinName"), LinkedPin->PinName.ToString());
			Links.Add(MakeShared<FJsonValueObject>(LinkObject));

			if (Pin->Direction == EGPD_Output)
			{
				++Result.LinkCount;
			}
		}
		PinObject->SetArrayField(TEXT("links"), Links);
		return PinObject;
	}

	TSharedRef<FJsonObject> ExportNode(
		UEdGraphNode* Node,
		const FBlueprintContextExportOptions& Options,
		FBlueprintContextExportResult& Result)
	{
		TSharedRef<FJsonObject> NodeObject = MakeShared<FJsonObject>();
		if (!Node)
		{
			return NodeObject;
		}

		++Result.NodeCount;
		NodeObject->SetStringField(TEXT("guid"), GuidToString(Node->NodeGuid));
		NodeObject->SetStringField(TEXT("name"), Node->GetName());
		NodeObject->SetStringField(TEXT("class"), Node->GetClass()->GetPathName());
		NodeObject->SetStringField(TEXT("title"), Node->GetNodeTitle(ENodeTitleType::FullTitle).ToString());
		NodeObject->SetStringField(TEXT("comment"), Node->NodeComment);

		if (Options.bIncludeLayout)
		{
			TSharedRef<FJsonObject> LayoutObject = MakeShared<FJsonObject>();
			LayoutObject->SetNumberField(TEXT("x"), Node->NodePosX);
			LayoutObject->SetNumberField(TEXT("y"), Node->NodePosY);
			LayoutObject->SetNumberField(TEXT("width"), Node->NodeWidth);
			LayoutObject->SetNumberField(TEXT("height"), Node->NodeHeight);
			NodeObject->SetObjectField(TEXT("layout"), LayoutObject);
		}

		if (Options.bIncludeReflectedNodeProperties)
		{
			static const TSet<FName> SkippedNodeProperties = {
				TEXT("Pins"),
				TEXT("NodeGuid"),
				TEXT("NodePosX"),
				TEXT("NodePosY"),
				TEXT("NodeWidth"),
				TEXT("NodeHeight"),
				TEXT("NodeComment"),
				TEXT("ErrorMsg")
			};
			NodeObject->SetObjectField(
				TEXT("properties"),
				ExportReflectedProperties(Node, SkippedNodeProperties, false));
		}

		TArray<TSharedPtr<FJsonValue>> Pins;
		for (const UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin)
			{
				Pins.Add(MakeShared<FJsonValueObject>(ExportPin(Pin, Result)));
			}
		}
		NodeObject->SetArrayField(TEXT("pins"), Pins);
		return NodeObject;
	}

	TSharedRef<FJsonObject> ExportGraph(
		UEdGraph* Graph,
		const FString& Kind,
		const FBlueprintContextExportOptions& Options,
		FBlueprintContextExportResult& Result)
	{
		TSharedRef<FJsonObject> GraphObject = MakeShared<FJsonObject>();
		if (!Graph)
		{
			return GraphObject;
		}

		++Result.GraphCount;
		GraphObject->SetStringField(TEXT("guid"), GuidToString(Graph->GraphGuid));
		GraphObject->SetStringField(TEXT("name"), Graph->GetName());
		GraphObject->SetStringField(TEXT("kind"), Kind);
		GraphObject->SetStringField(TEXT("schema"), Graph->GetSchema() ? Graph->GetSchema()->GetClass()->GetPathName() : FString());

		if (ProfileIncludesGraphNodes(Options.Profile))
		{
			TArray<UEdGraphNode*> SortedNodes;
			SortedNodes.Reserve(Graph->Nodes.Num());
			for (UEdGraphNode* Node : Graph->Nodes)
			{
				if (Node)
				{
					SortedNodes.Add(Node);
				}
			}
			SortedNodes.Sort([](const UEdGraphNode& A, const UEdGraphNode& B)
			{
				return A.NodeGuid.ToString(EGuidFormats::Digits) < B.NodeGuid.ToString(EGuidFormats::Digits);
			});

			TArray<TSharedPtr<FJsonValue>> Nodes;
			for (UEdGraphNode* Node : SortedNodes)
			{
				Nodes.Add(MakeShared<FJsonValueObject>(ExportNode(Node, Options, Result)));
			}
			GraphObject->SetArrayField(TEXT("nodes"), Nodes);
		}
		else
		{
			GraphObject->SetNumberField(TEXT("nodeCount"), Graph->Nodes.Num());
		}

		return GraphObject;
	}
}

bool FBlueprintContextExporter::ParseProfile(const FString& Value, EBlueprintContextProfile& OutProfile)
{
	if (Value.Equals(TEXT("index"), ESearchCase::IgnoreCase))
	{
		OutProfile = EBlueprintContextProfile::Index;
		return true;
	}
	if (Value.Equals(TEXT("structure"), ESearchCase::IgnoreCase))
	{
		OutProfile = EBlueprintContextProfile::Structure;
		return true;
	}
	if (Value.Equals(TEXT("logic"), ESearchCase::IgnoreCase))
	{
		OutProfile = EBlueprintContextProfile::Logic;
		return true;
	}
	if (Value.Equals(TEXT("defaults"), ESearchCase::IgnoreCase))
	{
		OutProfile = EBlueprintContextProfile::Defaults;
		return true;
	}
	if (Value.Equals(TEXT("full"), ESearchCase::IgnoreCase))
	{
		OutProfile = EBlueprintContextProfile::Full;
		return true;
	}
	if (Value.Equals(TEXT("ai"), ESearchCase::IgnoreCase))
	{
		OutProfile = EBlueprintContextProfile::AI;
		return true;
	}
	return false;
}

FString FBlueprintContextExporter::ProfileToString(const EBlueprintContextProfile Profile)
{
	switch (Profile)
	{
	case EBlueprintContextProfile::Index:
		return TEXT("index");
	case EBlueprintContextProfile::Structure:
		return TEXT("structure");
	case EBlueprintContextProfile::Logic:
		return TEXT("logic");
	case EBlueprintContextProfile::Defaults:
		return TEXT("defaults");
	case EBlueprintContextProfile::Full:
		return TEXT("full");
	case EBlueprintContextProfile::AI:
		return TEXT("ai");
	default:
		return TEXT("unknown");
	}
}

void FBlueprintContextExporter::ApplyProfileDefaults(FBlueprintContextExportOptions& Options)
{
	switch (Options.Profile)
	{
	case EBlueprintContextProfile::Index:
	case EBlueprintContextProfile::Structure:
	case EBlueprintContextProfile::Defaults:
		Options.bIncludeLayout = false;
		Options.bIncludeReflectedNodeProperties = false;
		Options.bIncludeUnchangedDefaults = false;
		break;
	case EBlueprintContextProfile::Logic:
		Options.bIncludeLayout = false;
		Options.bIncludeReflectedNodeProperties = true;
		Options.bIncludeUnchangedDefaults = false;
		break;
	case EBlueprintContextProfile::Full:
		Options.bIncludeLayout = true;
		Options.bIncludeReflectedNodeProperties = true;
		Options.bIncludeUnchangedDefaults = true;
		break;
	case EBlueprintContextProfile::AI:
		Options.bIncludeLayout = false;
		Options.bIncludeReflectedNodeProperties = false;
		Options.bIncludeUnchangedDefaults = false;
		Options.bPrettyJson = false;
		break;
	default:
		break;
	}
}

bool FBlueprintContextExporter::ExportBlueprint(
	UBlueprint* Blueprint,
	const FBlueprintContextExportOptions& Options,
	FBlueprintContextExportResult& OutResult)
{
	OutResult = FBlueprintContextExportResult();
	if (!Blueprint)
	{
		OutResult.Error = TEXT("Blueprint is null.");
		return false;
	}
	if (Options.OutputDirectory.IsEmpty())
	{
		OutResult.Error = TEXT("Output directory is empty.");
		return false;
	}

	OutResult.AssetPath = Blueprint->GetPathName();
	const TSharedRef<FJsonObject> RootObject = BuildCanonicalJson(Blueprint, Options, OutResult);
	const FString FileStem = BlueprintContextExporterPrivate::MakeRelativeAssetFileStem(Blueprint);

	if (Options.bWriteJson)
	{
		OutResult.JsonPath = FPaths::Combine(Options.OutputDirectory, TEXT("canonical"), FileStem + TEXT(".json"));
		if (!SaveJson(RootObject, OutResult.JsonPath, Options.bPrettyJson))
		{
			OutResult.Error = FString::Printf(TEXT("Failed to write JSON: %s"), *OutResult.JsonPath);
			return false;
		}
	}

	if (Options.bWriteBpctx)
	{
		OutResult.BpctxPath = FPaths::Combine(Options.OutputDirectory, TEXT("bpctx"), FileStem + TEXT(".bpctx"));
		if (!SaveText(BuildBpctx(RootObject), OutResult.BpctxPath))
		{
			OutResult.Error = FString::Printf(TEXT("Failed to write BPCTX: %s"), *OutResult.BpctxPath);
			return false;
		}
	}

	OutResult.bSuccess = true;
	return true;
}

TSharedRef<FJsonObject> FBlueprintContextExporter::BuildCanonicalJson(
	UBlueprint* Blueprint,
	const FBlueprintContextExportOptions& Options,
	FBlueprintContextExportResult& InOutResult)
{
	using namespace BlueprintContextExporterPrivate;

	TSharedRef<FJsonObject> RootObject = MakeShared<FJsonObject>();
	RootObject->SetStringField(TEXT("schemaVersion"), SchemaVersion);
	RootObject->SetStringField(TEXT("exporterVersion"), ExporterVersion);
	RootObject->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
	RootObject->SetStringField(TEXT("projectName"), FApp::GetProjectName());
	RootObject->SetStringField(TEXT("profile"), ProfileToString(Options.Profile));
	RootObject->SetStringField(TEXT("assetPath"), Blueprint->GetPathName());
	RootObject->SetStringField(TEXT("packageName"), Blueprint->GetOutermost()->GetName());
	RootObject->SetStringField(TEXT("assetClass"), Blueprint->GetClass()->GetPathName());
	RootObject->SetStringField(TEXT("blueprintType"), BlueprintTypeToString(Blueprint->BlueprintType));
	RootObject->SetStringField(TEXT("parentClass"), ObjectPath(Blueprint->ParentClass));
	RootObject->SetStringField(TEXT("generatedClass"), ObjectPath(Blueprint->GeneratedClass));
	RootObject->SetStringField(TEXT("skeletonGeneratedClass"), ObjectPath(Blueprint->SkeletonGeneratedClass));
	RootObject->SetNumberField(TEXT("status"), static_cast<int32>(Blueprint->Status));
	RootObject->SetObjectField(TEXT("revision"), FBlueprintContextAnalysis::BuildAssetRevision(Blueprint));

	TArray<TSharedPtr<FJsonValue>> Interfaces;
	for (const FBPInterfaceDescription& InterfaceDescription : Blueprint->ImplementedInterfaces)
	{
		if (InterfaceDescription.Interface)
		{
			TSharedRef<FJsonObject> InterfaceObject = MakeShared<FJsonObject>();
			InterfaceObject->SetStringField(TEXT("class"), InterfaceDescription.Interface->GetPathName());
			InterfaceObject->SetNumberField(TEXT("graphCount"), InterfaceDescription.Graphs.Num());
			Interfaces.Add(MakeShared<FJsonValueObject>(InterfaceObject));
		}
	}
	RootObject->SetArrayField(TEXT("interfaces"), Interfaces);

	if (ProfileIncludesStructure(Options.Profile))
	{
		TArray<TSharedPtr<FJsonValue>> Variables;
		UObject* ClassDefaultObject = Blueprint->GeneratedClass ? Blueprint->GeneratedClass->GetDefaultObject(false) : nullptr;
		for (const FBPVariableDescription& Variable : Blueprint->NewVariables)
		{
			++InOutResult.VariableCount;
			TSharedRef<FJsonObject> VariableObject = MakeShared<FJsonObject>();
			VariableObject->SetStringField(TEXT("guid"), GuidToString(Variable.VarGuid));
			VariableObject->SetStringField(TEXT("name"), Variable.VarName.ToString());
			VariableObject->SetStringField(TEXT("friendlyName"), Variable.FriendlyName);
			VariableObject->SetStringField(TEXT("category"), Variable.Category.ToString());
			VariableObject->SetObjectField(TEXT("type"), PinTypeToJson(Variable.VarType));
			VariableObject->SetStringField(TEXT("propertyFlags"), PropertyFlagsToString(static_cast<EPropertyFlags>(Variable.PropertyFlags)));
			VariableObject->SetStringField(TEXT("repNotifyFunction"), Variable.RepNotifyFunc.ToString());

			if (ProfileIncludesDefaults(Options.Profile) && Blueprint->GeneratedClass && ClassDefaultObject)
			{
				if (FProperty* Property = FindFProperty<FProperty>(Blueprint->GeneratedClass, Variable.VarName))
				{
					VariableObject->SetStringField(
						TEXT("defaultValue"),
						ExportPropertyValue(Property, ClassDefaultObject, ClassDefaultObject));
				}
			}
			Variables.Add(MakeShared<FJsonValueObject>(VariableObject));
		}
		RootObject->SetArrayField(TEXT("variables"), Variables);

		TArray<TSharedPtr<FJsonValue>> Components;
		if (Blueprint->SimpleConstructionScript)
		{
			for (USCS_Node* RootNode : Blueprint->SimpleConstructionScript->GetRootNodes())
			{
				ExportComponentNode(RootNode, FString(), Options, Components, InOutResult);
			}
		}
		RootObject->SetArrayField(TEXT("components"), Components);

		TArray<TSharedPtr<FJsonValue>> Functions;
		UClass* FunctionOwnerClass = Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass : Blueprint->GeneratedClass;
		for (UEdGraph* FunctionGraph : Blueprint->FunctionGraphs)
		{
			if (!FunctionGraph || !FunctionOwnerClass)
			{
				continue;
			}
			if (UFunction* Function = FunctionOwnerClass->FindFunctionByName(FunctionGraph->GetFName()))
			{
				Functions.Add(MakeShared<FJsonValueObject>(ExportFunction(Function)));
			}
		}
		RootObject->SetArrayField(TEXT("functions"), Functions);
	}

	if (Options.Profile != EBlueprintContextProfile::Defaults)
	{
		TArray<TSharedPtr<FJsonValue>> Graphs;
		TSet<UEdGraph*> AddedGraphs;

		auto AddGraphs = [&](const TArray<TObjectPtr<UEdGraph>>& SourceGraphs, const FString& Kind)
		{
			for (UEdGraph* Graph : SourceGraphs)
			{
				if (!Graph || AddedGraphs.Contains(Graph))
				{
					continue;
				}
				if (!Options.GraphFilter.IsEmpty()
					&& !Graph->GetName().Equals(Options.GraphFilter, ESearchCase::IgnoreCase))
				{
					continue;
				}

				AddedGraphs.Add(Graph);
				Graphs.Add(MakeShared<FJsonValueObject>(ExportGraph(Graph, Kind, Options, InOutResult)));
			}
		};

		AddGraphs(Blueprint->UbergraphPages, TEXT("uber"));
		AddGraphs(Blueprint->FunctionGraphs, TEXT("function"));
		AddGraphs(Blueprint->MacroGraphs, TEXT("macro"));
		AddGraphs(Blueprint->DelegateSignatureGraphs, TEXT("delegate-signature"));
		RootObject->SetArrayField(TEXT("graphs"), Graphs);
	}

	TArray<TSharedPtr<FJsonValue>> Symbols;
	TArray<TSharedPtr<FJsonValue>> References;
	FBlueprintContextAnalysis::BuildSymbolsAndReferences(
		Blueprint,
		Options,
		Symbols,
		References,
		InOutResult.SymbolCount,
		InOutResult.ReferenceCount);
	RootObject->SetArrayField(TEXT("symbols"), Symbols);
	RootObject->SetArrayField(TEXT("references"), References);

	TSharedRef<FJsonObject> SummaryObject = MakeShared<FJsonObject>();
	SummaryObject->SetNumberField(TEXT("variables"), InOutResult.VariableCount);
	SummaryObject->SetNumberField(TEXT("components"), InOutResult.ComponentCount);
	SummaryObject->SetNumberField(TEXT("graphs"), InOutResult.GraphCount);
	SummaryObject->SetNumberField(TEXT("nodes"), InOutResult.NodeCount);
	SummaryObject->SetNumberField(TEXT("pins"), InOutResult.PinCount);
	SummaryObject->SetNumberField(TEXT("links"), InOutResult.LinkCount);
	SummaryObject->SetNumberField(TEXT("symbols"), InOutResult.SymbolCount);
	SummaryObject->SetNumberField(TEXT("references"), InOutResult.ReferenceCount);
	RootObject->SetObjectField(TEXT("summary"), SummaryObject);
	return RootObject;
}

FString FBlueprintContextExporter::BuildBpctx(const TSharedRef<FJsonObject>& RootObject)
{
	using namespace BlueprintContextExporterPrivate;

	FString Output;
	FString EngineVersion;
	FString Profile;
	FString ProjectName;
	FString CanonicalSchemaVersion;
	FString ActiveExporterVersion;
	FString AssetPath;
	FString BlueprintType;
	FString ParentClass;
	FString GeneratedClass;
	RootObject->TryGetStringField(TEXT("engineVersion"), EngineVersion);
	RootObject->TryGetStringField(TEXT("profile"), Profile);
	RootObject->TryGetStringField(TEXT("projectName"), ProjectName);
	RootObject->TryGetStringField(TEXT("schemaVersion"), CanonicalSchemaVersion);
	RootObject->TryGetStringField(TEXT("exporterVersion"), ActiveExporterVersion);
	RootObject->TryGetStringField(TEXT("assetPath"), AssetPath);
	RootObject->TryGetStringField(TEXT("blueprintType"), BlueprintType);
	RootObject->TryGetStringField(TEXT("parentClass"), ParentClass);
	RootObject->TryGetStringField(TEXT("generatedClass"), GeneratedClass);

	AppendBpctxLine(Output, {
		TEXT("H"),
		TEXT("BPCTX"),
		TEXT("1"),
		TEXT("engine=") + EscapeBpctx(EngineVersion),
		TEXT("profile=") + EscapeBpctx(Profile),
		TEXT("project=") + EscapeBpctx(ProjectName),
		TEXT("schema=") + EscapeBpctx(CanonicalSchemaVersion),
		TEXT("exporter=") + EscapeBpctx(ActiveExporterVersion)
	});
	TArray<FString> AssetFields = { TEXT("A"), TEXT("a0"), EscapeBpctx(AssetPath), EscapeBpctx(BlueprintType) };
	AddOptionalField(AssetFields, TEXT("parent"), ParentClass);
	AddOptionalField(AssetFields, TEXT("generated"), GeneratedClass);
	AppendBpctxLine(Output, AssetFields);

	const TSharedPtr<FJsonObject>* RevisionObject = nullptr;
	if (RootObject->TryGetObjectField(TEXT("revision"), RevisionObject)
		&& RevisionObject
		&& RevisionObject->IsValid())
	{
		FString RevisionValue;
		FString PackageGuid;
		FString ModifiedUtc;
		FString ContentSha256;
		double FileSize = 0.0;
		bool bAvailable = false;
		bool bPackageDirty = false;
		(*RevisionObject)->TryGetStringField(TEXT("value"), RevisionValue);
		(*RevisionObject)->TryGetStringField(TEXT("packageGuid"), PackageGuid);
		(*RevisionObject)->TryGetStringField(TEXT("modifiedUtc"), ModifiedUtc);
		(*RevisionObject)->TryGetStringField(TEXT("contentSha256"), ContentSha256);
		(*RevisionObject)->TryGetNumberField(TEXT("fileSize"), FileSize);
		(*RevisionObject)->TryGetBoolField(TEXT("available"), bAvailable);
		(*RevisionObject)->TryGetBoolField(TEXT("packageDirty"), bPackageDirty);

		TArray<FString> RevisionFields = {
			TEXT("R"),
			EscapeBpctx(RevisionValue),
			FString::Printf(TEXT("available=%d"), bAvailable ? 1 : 0),
			FString::Printf(TEXT("dirty=%d"), bPackageDirty ? 1 : 0)
		};
		AddOptionalField(RevisionFields, TEXT("guid"), PackageGuid);
		if (FileSize > 0.0)
		{
			RevisionFields.Add(FString::Printf(TEXT("size=%.0f"), FileSize));
		}
		AddOptionalField(RevisionFields, TEXT("mtime"), ModifiedUtc);
		AddOptionalField(RevisionFields, TEXT("sha256"), ContentSha256);
		AppendBpctxLine(Output, RevisionFields);
	}

	const TArray<TSharedPtr<FJsonValue>>* Interfaces = nullptr;
	if (RootObject->TryGetArrayField(TEXT("interfaces"), Interfaces) && Interfaces)
	{
		for (int32 Index = 0; Index < Interfaces->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> InterfaceObject = (*Interfaces)[Index]->AsObject();
			if (!InterfaceObject.IsValid())
			{
				continue;
			}
			FString ClassPath;
			InterfaceObject->TryGetStringField(TEXT("class"), ClassPath);
			AppendBpctxLine(Output, { TEXT("I"), FString::Printf(TEXT("i%d"), Index), EscapeBpctx(ClassPath) });
		}
	}

	const TArray<TSharedPtr<FJsonValue>>* Variables = nullptr;
	if (RootObject->TryGetArrayField(TEXT("variables"), Variables) && Variables)
	{
		for (int32 Index = 0; Index < Variables->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> VariableObject = (*Variables)[Index]->AsObject();
			if (!VariableObject.IsValid())
			{
				continue;
			}

			FString Name;
			FString DefaultValue;
			FString Flags;
			VariableObject->TryGetStringField(TEXT("name"), Name);
			VariableObject->TryGetStringField(TEXT("defaultValue"), DefaultValue);
			VariableObject->TryGetStringField(TEXT("propertyFlags"), Flags);
			const TSharedPtr<FJsonObject>* TypeObject = nullptr;
			VariableObject->TryGetObjectField(TEXT("type"), TypeObject);

			TArray<FString> Fields = {
				TEXT("V"),
				FString::Printf(TEXT("v%d"), Index),
				EscapeBpctx(Name),
				EscapeBpctx(TypeObject ? PinTypeToCompactString(*TypeObject) : TEXT("unknown"))
			};
			AddOptionalField(Fields, TEXT("default"), DefaultValue);
			AddOptionalField(Fields, TEXT("flags"), Flags);
			AppendBpctxLine(Output, Fields);
		}
	}

	const TArray<TSharedPtr<FJsonValue>>* Components = nullptr;
	if (RootObject->TryGetArrayField(TEXT("components"), Components) && Components)
	{
		for (const TSharedPtr<FJsonValue>& ComponentValue : *Components)
		{
			const TSharedPtr<FJsonObject> ComponentObject = ComponentValue->AsObject();
			if (!ComponentObject.IsValid())
			{
				continue;
			}

			FString Id;
			FString Name;
			FString ClassPath;
			FString ParentId;
			ComponentObject->TryGetStringField(TEXT("id"), Id);
			ComponentObject->TryGetStringField(TEXT("name"), Name);
			ComponentObject->TryGetStringField(TEXT("class"), ClassPath);
			ComponentObject->TryGetStringField(TEXT("parentId"), ParentId);
			TArray<FString> Fields = { TEXT("C"), EscapeBpctx(Id), EscapeBpctx(Name), EscapeBpctx(ClassPath) };
			AddOptionalField(Fields, TEXT("parent"), ParentId);
			AppendBpctxLine(Output, Fields);
		}
	}

	const TArray<TSharedPtr<FJsonValue>>* Functions = nullptr;
	if (RootObject->TryGetArrayField(TEXT("functions"), Functions) && Functions)
	{
		for (int32 Index = 0; Index < Functions->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> FunctionObject = (*Functions)[Index]->AsObject();
			if (!FunctionObject.IsValid())
			{
				continue;
			}

			FString Name;
			FString Flags;
			FunctionObject->TryGetStringField(TEXT("name"), Name);
			FunctionObject->TryGetStringField(TEXT("flags"), Flags);
			TArray<FString> Fields = { TEXT("F"), FString::Printf(TEXT("f%d"), Index), EscapeBpctx(Name) };
			AddOptionalField(Fields, TEXT("flags"), Flags);
			AppendBpctxLine(Output, Fields);
		}
	}

	const TArray<TSharedPtr<FJsonValue>> EmptyValues;
	const TArray<TSharedPtr<FJsonValue>>* Graphs = &EmptyValues;
	RootObject->TryGetArrayField(TEXT("graphs"), Graphs);
	if (!Graphs)
	{
		Graphs = &EmptyValues;
	}

	TMap<FString, FString> GraphShortIdByGuid;
	TMap<FString, FString> NodeShortIdByGuid;
	int32 GlobalNodeIndex = 0;
	for (int32 GraphIndex = 0; GraphIndex < Graphs->Num(); ++GraphIndex)
	{
		const TSharedPtr<FJsonObject> GraphObject = (*Graphs)[GraphIndex]->AsObject();
		if (!GraphObject.IsValid())
		{
			continue;
		}

		const FString GraphId = FString::Printf(TEXT("g%d"), GraphIndex);
		FString GraphGuid;
		FString GraphName;
		FString GraphKind;
		FString GraphSchema;
		GraphObject->TryGetStringField(TEXT("guid"), GraphGuid);
		GraphObject->TryGetStringField(TEXT("name"), GraphName);
		GraphObject->TryGetStringField(TEXT("kind"), GraphKind);
		GraphObject->TryGetStringField(TEXT("schema"), GraphSchema);
		GraphShortIdByGuid.Add(GraphGuid, GraphId);
		TArray<FString> GraphFields = { TEXT("G"), GraphId, EscapeBpctx(GraphName), EscapeBpctx(GraphKind) };
		AddOptionalField(GraphFields, TEXT("schema"), GraphSchema);
		AppendBpctxLine(Output, GraphFields);

		const TArray<TSharedPtr<FJsonValue>>* Nodes = nullptr;
		if (!GraphObject->TryGetArrayField(TEXT("nodes"), Nodes) || !Nodes)
		{
			continue;
		}

		TMap<FString, FString> NodeIdByGuid;
		TMap<FString, FString> PinIdByGuid;
		TArray<FString> NodeIds;
		NodeIds.Reserve(Nodes->Num());

		for (const TSharedPtr<FJsonValue>& NodeValue : *Nodes)
		{
			const TSharedPtr<FJsonObject> NodeObject = NodeValue->AsObject();
			const FString NodeId = FString::Printf(TEXT("n%d"), GlobalNodeIndex++);
			NodeIds.Add(NodeId);
			if (!NodeObject.IsValid())
			{
				continue;
			}

			FString NodeGuid;
			NodeObject->TryGetStringField(TEXT("guid"), NodeGuid);
			NodeIdByGuid.Add(NodeGuid, NodeId);
			NodeShortIdByGuid.Add(NodeGuid, NodeId);

			const TArray<TSharedPtr<FJsonValue>>* Pins = nullptr;
			if (NodeObject->TryGetArrayField(TEXT("pins"), Pins) && Pins)
			{
				for (int32 PinIndex = 0; PinIndex < Pins->Num(); ++PinIndex)
				{
					const TSharedPtr<FJsonObject> PinObject = (*Pins)[PinIndex]->AsObject();
					if (!PinObject.IsValid())
					{
						continue;
					}
					FString PinGuid;
					PinObject->TryGetStringField(TEXT("id"), PinGuid);
					PinIdByGuid.Add(NodeGuid + TEXT("|") + PinGuid, FString::Printf(TEXT("%s.p%d"), *NodeId, PinIndex));
				}
			}
		}

		for (int32 NodeIndex = 0; NodeIndex < Nodes->Num(); ++NodeIndex)
		{
			const TSharedPtr<FJsonObject> NodeObject = (*Nodes)[NodeIndex]->AsObject();
			if (!NodeObject.IsValid())
			{
				continue;
			}

			const FString& NodeId = NodeIds[NodeIndex];
			FString NodeClass;
			FString NodeTitle;
			FString NodeComment;
			FString NodeGuid;
			NodeObject->TryGetStringField(TEXT("class"), NodeClass);
			NodeObject->TryGetStringField(TEXT("title"), NodeTitle);
			NodeObject->TryGetStringField(TEXT("comment"), NodeComment);
			NodeObject->TryGetStringField(TEXT("guid"), NodeGuid);

			TArray<FString> NodeFields = { TEXT("N"), NodeId, GraphId, EscapeBpctx(NodeClass), EscapeBpctx(NodeTitle) };
			AddOptionalField(NodeFields, TEXT("comment"), NodeComment);
			AppendBpctxLine(Output, NodeFields);

			const TArray<TSharedPtr<FJsonValue>>* Pins = nullptr;
			if (!NodeObject->TryGetArrayField(TEXT("pins"), Pins) || !Pins)
			{
				continue;
			}

			for (int32 PinIndex = 0; PinIndex < Pins->Num(); ++PinIndex)
			{
				const TSharedPtr<FJsonObject> PinObject = (*Pins)[PinIndex]->AsObject();
				if (!PinObject.IsValid())
				{
					continue;
				}

				FString Direction;
				FString Name;
				FString DefaultValue;
				PinObject->TryGetStringField(TEXT("direction"), Direction);
				PinObject->TryGetStringField(TEXT("name"), Name);
				PinObject->TryGetStringField(TEXT("defaultValue"), DefaultValue);
				const TSharedPtr<FJsonObject>* TypeObject = nullptr;
				PinObject->TryGetObjectField(TEXT("type"), TypeObject);

				TArray<FString> PinFields = {
					TEXT("P"),
					FString::Printf(TEXT("%s.p%d"), *NodeId, PinIndex),
					Direction == TEXT("input") ? TEXT("in") : TEXT("out"),
					EscapeBpctx(TypeObject ? PinTypeToCompactString(*TypeObject) : TEXT("unknown")),
					EscapeBpctx(Name)
				};
				AddOptionalField(PinFields, TEXT("default"), DefaultValue);

				if (Direction == TEXT("output"))
				{
					const TArray<TSharedPtr<FJsonValue>>* Links = nullptr;
					if (PinObject->TryGetArrayField(TEXT("links"), Links) && Links)
					{
						TArray<FString> LinkIds;
						for (const TSharedPtr<FJsonValue>& LinkValue : *Links)
						{
							const TSharedPtr<FJsonObject> LinkObject = LinkValue->AsObject();
							if (!LinkObject.IsValid())
							{
								continue;
							}

							FString TargetNodeGuid;
							FString TargetPinGuid;
							LinkObject->TryGetStringField(TEXT("targetNodeGuid"), TargetNodeGuid);
							LinkObject->TryGetStringField(TEXT("targetPinId"), TargetPinGuid);
							if (const FString* TargetPinId = PinIdByGuid.Find(TargetNodeGuid + TEXT("|") + TargetPinGuid))
							{
								LinkIds.Add(*TargetPinId);
							}
						}

						if (!LinkIds.IsEmpty())
						{
							PinFields.Add(TEXT("links=") + FString::Join(LinkIds, TEXT(",")));
						}
					}
				}

				AppendBpctxLine(Output, PinFields);
			}
		}
	}

	const TArray<TSharedPtr<FJsonValue>>* Symbols = nullptr;
	TMap<FString, FString> SymbolShortIdByStableId;
	if (RootObject->TryGetArrayField(TEXT("symbols"), Symbols) && Symbols)
	{
		for (int32 Index = 0; Index < Symbols->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> SymbolObject = (*Symbols)[Index]->AsObject();
			if (!SymbolObject.IsValid())
			{
				continue;
			}

			FString StableId;
			SymbolObject->TryGetStringField(TEXT("id"), StableId);
			if (!StableId.IsEmpty())
			{
				SymbolShortIdByStableId.Add(StableId, FString::Printf(TEXT("s%d"), Index));
			}
		}

		for (int32 Index = 0; Index < Symbols->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> SymbolObject = (*Symbols)[Index]->AsObject();
			if (!SymbolObject.IsValid())
			{
				continue;
			}

			FString StableId;
			FString Kind;
			FString Name;
			FString SymbolAssetPath;
			FString Guid;
			FString OwnerSymbolId;
			FString ParentSymbolId;
			FString ClassPath;
			FString GraphGuid;
			FString NodeGuid;
			FString EventKind;
			FString SignaturePath;
			FString VariableScope;
			FString VariableRole;
			FString VariableType;
			FString DeclaredTypePath;
			FString DefaultTargetPath;
			FString SoftReferenceKind;
			FString ScopeName;
			FString ParameterDirection;
			FString ParameterPassing;
			bool bParameterConst = false;
			FString DelegateKind;
			FString DelegateScope;
			FString SignatureGraphGuid;
			FString SignatureGraphSymbolId;
			bool bMulticast = false;
			SymbolObject->TryGetStringField(TEXT("id"), StableId);
			SymbolObject->TryGetStringField(TEXT("kind"), Kind);
			SymbolObject->TryGetStringField(TEXT("name"), Name);
			SymbolObject->TryGetStringField(TEXT("assetPath"), SymbolAssetPath);
			SymbolObject->TryGetStringField(TEXT("guid"), Guid);
			SymbolObject->TryGetStringField(TEXT("ownerSymbolId"), OwnerSymbolId);
			SymbolObject->TryGetStringField(TEXT("parentSymbolId"), ParentSymbolId);
			SymbolObject->TryGetStringField(TEXT("class"), ClassPath);
			SymbolObject->TryGetStringField(TEXT("graphGuid"), GraphGuid);
			SymbolObject->TryGetStringField(TEXT("nodeGuid"), NodeGuid);
			SymbolObject->TryGetStringField(TEXT("eventKind"), EventKind);
			SymbolObject->TryGetStringField(TEXT("signaturePath"), SignaturePath);
			SymbolObject->TryGetStringField(TEXT("variableScope"), VariableScope);
			SymbolObject->TryGetStringField(TEXT("variableRole"), VariableRole);
			SymbolObject->TryGetStringField(TEXT("variableType"), VariableType);
			SymbolObject->TryGetStringField(TEXT("declaredTypePath"), DeclaredTypePath);
			SymbolObject->TryGetStringField(TEXT("defaultTargetPath"), DefaultTargetPath);
			SymbolObject->TryGetStringField(TEXT("softReferenceKind"), SoftReferenceKind);
			SymbolObject->TryGetStringField(TEXT("scopeName"), ScopeName);
			SymbolObject->TryGetStringField(TEXT("parameterDirection"), ParameterDirection);
			SymbolObject->TryGetStringField(TEXT("parameterPassing"), ParameterPassing);
			SymbolObject->TryGetBoolField(TEXT("parameterConst"), bParameterConst);
			SymbolObject->TryGetStringField(TEXT("delegateKind"), DelegateKind);
			SymbolObject->TryGetStringField(TEXT("delegateScope"), DelegateScope);
			SymbolObject->TryGetStringField(TEXT("signatureGraphGuid"), SignatureGraphGuid);
			SymbolObject->TryGetStringField(TEXT("signatureGraphSymbolId"), SignatureGraphSymbolId);
			SymbolObject->TryGetBoolField(TEXT("multicast"), bMulticast);

			const FString ShortId = SymbolShortIdByStableId.FindRef(StableId);
			TArray<FString> Fields = {
				TEXT("S"),
				EscapeBpctx(ShortId),
				EscapeBpctx(Kind),
				EscapeBpctx(Name),
				TEXT("stable=") + EscapeBpctx(StableId)
			};
			if (!SymbolAssetPath.IsEmpty() && SymbolAssetPath != AssetPath)
			{
				Fields.Add(TEXT("asset=") + EscapeBpctx(SymbolAssetPath));
			}
			AddOptionalField(Fields, TEXT("guid"), Guid);
			if (!OwnerSymbolId.IsEmpty())
			{
				const FString OwnerShortId = SymbolShortIdByStableId.FindRef(OwnerSymbolId);
				Fields.Add(TEXT("owner=") + EscapeBpctx(OwnerShortId.IsEmpty() ? OwnerSymbolId : OwnerShortId));
			}
			if (!ParentSymbolId.IsEmpty())
			{
				const FString ParentShortId = SymbolShortIdByStableId.FindRef(ParentSymbolId);
				Fields.Add(TEXT("parent=") + EscapeBpctx(ParentShortId.IsEmpty() ? ParentSymbolId : ParentShortId));
			}
			AddOptionalField(Fields, TEXT("class"), ClassPath);
			if (const FString* GraphShortId = GraphShortIdByGuid.Find(GraphGuid))
			{
				Fields.Add(TEXT("graph=") + EscapeBpctx(*GraphShortId));
			}
			AddOptionalField(Fields, TEXT("node-guid"), NodeGuid);
			AddOptionalField(Fields, TEXT("event-kind"), EventKind);
			AddOptionalField(Fields, TEXT("signature"), SignaturePath);
			AddOptionalField(Fields, TEXT("variable-scope"), VariableScope);
			AddOptionalField(Fields, TEXT("variable-role"), VariableRole);
			AddOptionalField(Fields, TEXT("variable-type"), VariableType);
			AddOptionalField(Fields, TEXT("declared-type"), DeclaredTypePath);
			AddOptionalField(Fields, TEXT("default-target"), DefaultTargetPath);
			AddOptionalField(Fields, TEXT("soft-reference-kind"), SoftReferenceKind);
			AddOptionalField(Fields, TEXT("scope-name"), ScopeName);
			AddOptionalField(Fields, TEXT("parameter-direction"), ParameterDirection);
			AddOptionalField(Fields, TEXT("parameter-passing"), ParameterPassing);
			if (!ParameterDirection.IsEmpty())
			{
				Fields.Add(FString::Printf(TEXT("parameter-const=%d"), bParameterConst ? 1 : 0));
			}
			AddOptionalField(Fields, TEXT("delegate-kind"), DelegateKind);
			AddOptionalField(Fields, TEXT("delegate-scope"), DelegateScope);
			if (const FString* SignatureGraphShortId = GraphShortIdByGuid.Find(SignatureGraphGuid))
			{
				Fields.Add(TEXT("signature-graph=") + EscapeBpctx(*SignatureGraphShortId));
			}
			else
			{
				AddOptionalField(Fields, TEXT("signature-graph-guid"), SignatureGraphGuid);
			}
			if (!SignatureGraphSymbolId.IsEmpty())
			{
				const FString SignatureGraphSymbolShortId = SymbolShortIdByStableId.FindRef(SignatureGraphSymbolId);
				Fields.Add(TEXT("signature-symbol=") + EscapeBpctx(
					SignatureGraphSymbolShortId.IsEmpty() ? SignatureGraphSymbolId : SignatureGraphSymbolShortId));
			}
			if (Kind == TEXT("delegate"))
			{
				Fields.Add(FString::Printf(TEXT("multicast=%d"), bMulticast ? 1 : 0));
			}
			AppendBpctxLine(Output, Fields);
		}
	}

	const TArray<TSharedPtr<FJsonValue>>* References = nullptr;
	if (RootObject->TryGetArrayField(TEXT("references"), References) && References)
	{
		for (int32 Index = 0; Index < References->Num(); ++Index)
		{
			const TSharedPtr<FJsonObject> ReferenceObject = (*References)[Index]->AsObject();
			if (!ReferenceObject.IsValid())
			{
				continue;
			}

			FString StableId;
			FString Kind;
			FString SourceSymbolId;
			FString TargetSymbolId;
			FString TargetKind;
			FString TargetName;
			FString TargetAssetPath;
			FString TargetPath;
			FString GraphGuid;
			FString NodeGuid;
			FString VariableScope;
			FString ScopeName;
			FString ParameterDirection;
			FString ParameterPassing;
			FString ValueNodeGuids;
			FString ResultNodeGuid;
			bool bParameterConst = false;
			FString DispatchKind;
			FString SourceTypePath;
			FString CastMode;
			FString SuccessNodeGuids;
			FString FailureNodeGuids;
			FString DelegateSignaturePath;
			FString DelegateOperation;
			FString DelegateOwnerClassPath;
			FString TargetObjectNodeGuids;
			FString HandlerSymbolId;
			FString HandlerKind;
			FString HandlerName;
			FString HandlerAssetPath;
			FString HandlerPath;
			FString HandlerNodeGuid;
			FString ObjectTypePath;
			FString ObjectNodeGuids;
			FString DelegateOutputNodeGuids;
			FString TargetPackageName;
			FString TargetObjectName;
			FString TargetValueName;
			FString TargetPrimaryAssetType;
			FString DependencyCategory;
			FString DependencyProperties;
			FString DependencyDomain;
			FString SoftReferenceKind;
			FString SourceVariableName;
			FString DeclaredTypePath;
			FString ManagerName;
			FString ManagerAssetPath;
			FString ManagerPackageName;
			FString ManagerPath;
			FString ManagerResolution;
			bool bIncoming = false;
			bool bDependencyHard = false;
			bool bDependencyGame = false;
			bool bDependencyBuild = false;
			bool bDependencyDirect = false;
			ReferenceObject->TryGetStringField(TEXT("id"), StableId);
			ReferenceObject->TryGetStringField(TEXT("kind"), Kind);
			ReferenceObject->TryGetStringField(TEXT("sourceSymbolId"), SourceSymbolId);
			ReferenceObject->TryGetStringField(TEXT("targetSymbolId"), TargetSymbolId);
			ReferenceObject->TryGetStringField(TEXT("targetKind"), TargetKind);
			ReferenceObject->TryGetStringField(TEXT("targetName"), TargetName);
			ReferenceObject->TryGetStringField(TEXT("targetAssetPath"), TargetAssetPath);
			ReferenceObject->TryGetStringField(TEXT("targetPath"), TargetPath);
			ReferenceObject->TryGetStringField(TEXT("graphGuid"), GraphGuid);
			ReferenceObject->TryGetStringField(TEXT("nodeGuid"), NodeGuid);
			ReferenceObject->TryGetStringField(TEXT("variableScope"), VariableScope);
			ReferenceObject->TryGetStringField(TEXT("scopeName"), ScopeName);
			ReferenceObject->TryGetStringField(TEXT("parameterDirection"), ParameterDirection);
			ReferenceObject->TryGetStringField(TEXT("parameterPassing"), ParameterPassing);
			ReferenceObject->TryGetStringField(TEXT("valueNodeGuids"), ValueNodeGuids);
			ReferenceObject->TryGetStringField(TEXT("resultNodeGuid"), ResultNodeGuid);
			ReferenceObject->TryGetBoolField(TEXT("parameterConst"), bParameterConst);
			ReferenceObject->TryGetStringField(TEXT("dispatchKind"), DispatchKind);
			ReferenceObject->TryGetStringField(TEXT("sourceTypePath"), SourceTypePath);
			ReferenceObject->TryGetStringField(TEXT("castMode"), CastMode);
			ReferenceObject->TryGetStringField(TEXT("successNodeGuids"), SuccessNodeGuids);
			ReferenceObject->TryGetStringField(TEXT("failureNodeGuids"), FailureNodeGuids);
			ReferenceObject->TryGetStringField(TEXT("signaturePath"), DelegateSignaturePath);
			ReferenceObject->TryGetStringField(TEXT("delegateOperation"), DelegateOperation);
			ReferenceObject->TryGetStringField(TEXT("delegateOwnerClassPath"), DelegateOwnerClassPath);
			ReferenceObject->TryGetStringField(TEXT("targetObjectNodeGuids"), TargetObjectNodeGuids);
			ReferenceObject->TryGetStringField(TEXT("handlerSymbolId"), HandlerSymbolId);
			ReferenceObject->TryGetStringField(TEXT("handlerKind"), HandlerKind);
			ReferenceObject->TryGetStringField(TEXT("handlerName"), HandlerName);
			ReferenceObject->TryGetStringField(TEXT("handlerAssetPath"), HandlerAssetPath);
			ReferenceObject->TryGetStringField(TEXT("handlerPath"), HandlerPath);
			ReferenceObject->TryGetStringField(TEXT("handlerNodeGuid"), HandlerNodeGuid);
			ReferenceObject->TryGetStringField(TEXT("objectTypePath"), ObjectTypePath);
			ReferenceObject->TryGetStringField(TEXT("objectNodeGuids"), ObjectNodeGuids);
			ReferenceObject->TryGetStringField(TEXT("delegateOutputNodeGuids"), DelegateOutputNodeGuids);
			ReferenceObject->TryGetStringField(TEXT("targetPackageName"), TargetPackageName);
			ReferenceObject->TryGetStringField(TEXT("targetObjectName"), TargetObjectName);
			ReferenceObject->TryGetStringField(TEXT("targetValueName"), TargetValueName);
			ReferenceObject->TryGetStringField(TEXT("targetPrimaryAssetType"), TargetPrimaryAssetType);
			ReferenceObject->TryGetStringField(TEXT("dependencyCategory"), DependencyCategory);
			ReferenceObject->TryGetStringField(TEXT("dependencyProperties"), DependencyProperties);
			ReferenceObject->TryGetStringField(TEXT("dependencyDomain"), DependencyDomain);
			ReferenceObject->TryGetStringField(TEXT("softReferenceKind"), SoftReferenceKind);
			ReferenceObject->TryGetStringField(TEXT("sourceVariableName"), SourceVariableName);
			ReferenceObject->TryGetStringField(TEXT("declaredTypePath"), DeclaredTypePath);
			ReferenceObject->TryGetStringField(TEXT("managerName"), ManagerName);
			ReferenceObject->TryGetStringField(TEXT("managerAssetPath"), ManagerAssetPath);
			ReferenceObject->TryGetStringField(TEXT("managerPackageName"), ManagerPackageName);
			ReferenceObject->TryGetStringField(TEXT("managerPath"), ManagerPath);
			ReferenceObject->TryGetStringField(TEXT("managerResolution"), ManagerResolution);
			ReferenceObject->TryGetBoolField(TEXT("incoming"), bIncoming);
			ReferenceObject->TryGetBoolField(TEXT("hard"), bDependencyHard);
			ReferenceObject->TryGetBoolField(TEXT("game"), bDependencyGame);
			ReferenceObject->TryGetBoolField(TEXT("build"), bDependencyBuild);
			ReferenceObject->TryGetBoolField(TEXT("direct"), bDependencyDirect);

			const FString SourceShortId = SymbolShortIdByStableId.FindRef(SourceSymbolId);
			const FString TargetShortId = SymbolShortIdByStableId.FindRef(TargetSymbolId);
			TArray<FString> Fields = {
				TEXT("D"),
				FString::Printf(TEXT("d%d"), Index),
				EscapeBpctx(Kind),
				EscapeBpctx(SourceShortId.IsEmpty() ? SourceSymbolId : SourceShortId),
				EscapeBpctx(TargetShortId.IsEmpty() ? TargetSymbolId : TargetShortId),
				TEXT("stable=") + EscapeBpctx(StableId)
			};
			AddOptionalField(Fields, TEXT("target-kind"), TargetKind);
			AddOptionalField(Fields, TEXT("name"), TargetName);
			AddOptionalField(Fields, TEXT("asset"), TargetAssetPath);
			AddOptionalField(Fields, TEXT("path"), TargetPath);
			AddOptionalField(Fields, TEXT("variable-scope"), VariableScope);
			AddOptionalField(Fields, TEXT("scope-name"), ScopeName);
			AddOptionalField(Fields, TEXT("parameter-direction"), ParameterDirection);
			AddOptionalField(Fields, TEXT("parameter-passing"), ParameterPassing);
			AddOptionalField(Fields, TEXT("value-nodes"), ValueNodeGuids);
			AddOptionalField(Fields, TEXT("result-node-guid"), ResultNodeGuid);
			if (!ParameterDirection.IsEmpty())
			{
				Fields.Add(FString::Printf(TEXT("parameter-const=%d"), bParameterConst ? 1 : 0));
			}
			AddOptionalField(Fields, TEXT("dispatch"), DispatchKind);
			AddOptionalField(Fields, TEXT("source-type"), SourceTypePath);
			AddOptionalField(Fields, TEXT("cast-mode"), CastMode);
			AddOptionalField(Fields, TEXT("success-targets"), SuccessNodeGuids);
			AddOptionalField(Fields, TEXT("failure-targets"), FailureNodeGuids);
			AddOptionalField(Fields, TEXT("signature"), DelegateSignaturePath);
			AddOptionalField(Fields, TEXT("delegate-op"), DelegateOperation);
			AddOptionalField(Fields, TEXT("delegate-owner-class"), DelegateOwnerClassPath);
			AddOptionalField(Fields, TEXT("target-object-nodes"), TargetObjectNodeGuids);
			if (!HandlerSymbolId.IsEmpty())
			{
				const FString HandlerShortId = SymbolShortIdByStableId.FindRef(HandlerSymbolId);
				Fields.Add(TEXT("handler=") + EscapeBpctx(HandlerShortId.IsEmpty() ? HandlerSymbolId : HandlerShortId));
			}
			AddOptionalField(Fields, TEXT("handler-kind"), HandlerKind);
			AddOptionalField(Fields, TEXT("handler-name"), HandlerName);
			AddOptionalField(Fields, TEXT("handler-asset"), HandlerAssetPath);
			AddOptionalField(Fields, TEXT("handler-path"), HandlerPath);
			AddOptionalField(Fields, TEXT("handler-node-guid"), HandlerNodeGuid);
			AddOptionalField(Fields, TEXT("object-type"), ObjectTypePath);
			AddOptionalField(Fields, TEXT("object-nodes"), ObjectNodeGuids);
			AddOptionalField(Fields, TEXT("delegate-output-nodes"), DelegateOutputNodeGuids);
			AddOptionalField(Fields, TEXT("soft-reference-kind"), SoftReferenceKind);
			AddOptionalField(Fields, TEXT("source-variable"), SourceVariableName);
			AddOptionalField(Fields, TEXT("declared-type"), DeclaredTypePath);
			AddOptionalField(Fields, TEXT("manager-name"), ManagerName);
			AddOptionalField(Fields, TEXT("manager-asset"), ManagerAssetPath);
			AddOptionalField(Fields, TEXT("manager-package"), ManagerPackageName);
			AddOptionalField(Fields, TEXT("manager-path"), ManagerPath);
			AddOptionalField(Fields, TEXT("manager-resolution"), ManagerResolution);
			if (!ManagerResolution.IsEmpty())
			{
				Fields.Add(FString::Printf(TEXT("incoming=%d"), bIncoming ? 1 : 0));
			}
			if (DependencyCategory.IsEmpty())
			{
				AddOptionalField(Fields, TEXT("dependency-domain"), DependencyDomain);
			}
			if (!DependencyCategory.IsEmpty())
			{
				AddOptionalField(Fields, TEXT("package"), TargetPackageName);
				AddOptionalField(Fields, TEXT("object"), TargetObjectName);
				AddOptionalField(Fields, TEXT("value"), TargetValueName);
				AddOptionalField(Fields, TEXT("primary-type"), TargetPrimaryAssetType);
				AddOptionalField(Fields, TEXT("dependency-category"), DependencyCategory);
				AddOptionalField(Fields, TEXT("dependency-properties"), DependencyProperties);
				AddOptionalField(Fields, TEXT("dependency-domain"), DependencyDomain);
				Fields.Add(FString::Printf(TEXT("hard=%d"), bDependencyHard ? 1 : 0));
				Fields.Add(FString::Printf(TEXT("game=%d"), bDependencyGame ? 1 : 0));
				Fields.Add(FString::Printf(TEXT("build=%d"), bDependencyBuild ? 1 : 0));
				Fields.Add(FString::Printf(TEXT("direct=%d"), bDependencyDirect ? 1 : 0));
			}
			if (const FString* GraphShortId = GraphShortIdByGuid.Find(GraphGuid))
			{
				Fields.Add(TEXT("graph=") + EscapeBpctx(*GraphShortId));
			}
			else
			{
				AddOptionalField(Fields, TEXT("graph-guid"), GraphGuid);
			}
			if (const FString* NodeShortId = NodeShortIdByGuid.Find(NodeGuid))
			{
				Fields.Add(TEXT("node=") + EscapeBpctx(*NodeShortId));
			}
			else
			{
				AddOptionalField(Fields, TEXT("node-guid"), NodeGuid);
			}
			AppendBpctxLine(Output, Fields);
		}
	}

	return Output;
}

bool FBlueprintContextExporter::SaveJson(
	const TSharedRef<FJsonObject>& RootObject,
	const FString& Path,
	const bool bPretty)
{
	FString JsonText;
	bool bSerialized = false;
	if (bPretty)
	{
		const TSharedRef<TJsonWriter<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		bSerialized = FJsonSerializer::Serialize(RootObject, Writer);
	}
	else
	{
		const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&JsonText);
		bSerialized = FJsonSerializer::Serialize(RootObject, Writer);
	}

	return bSerialized && SaveText(JsonText, Path);
}

bool FBlueprintContextExporter::SaveText(const FString& Text, const FString& Path)
{
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
	return FFileHelper::SaveStringToFile(Text, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
}
