using UnrealBuildTool;

public class UEAgentKitEditor : ModuleRules
{
	public UEAgentKitEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(
			new string[]
			{
				"Core",
				"CoreUObject",
				"Engine"
			});

		PrivateDependencyModuleNames.AddRange(
			new string[]
			{
				"AdvancedPreviewScene",
				"ApplicationCore",
				"AssetRegistry",
				"AssetTools",
				"BlueprintGraph",
				"CurveEditor",
				"DataHierarchyEditor",
				"DataValidation",
				"EditorFramework",
				"EditorWidgets",
				"GraphEditor",
				"IKRig",
				"IKRigEditor",
				"InputCore",
				"Json",
				"JsonUtilities",
				"Kismet",
				"LevelSequence",
				"MaterialEditor",
				"MovieScene",
				"MovieSceneTracks",
				"Networking",
				"Niagara",
				"NiagaraEditor",
				"Projects",
				"PropertyEditor",
				"RHI",
				"RenderCore",
				"Renderer",
				"Sequencer",
				"TimeManagement",
				"ToolWidgets",
				"PythonScriptPlugin",
				"Slate",
				"SlateCore",
				"SlateRHIRenderer",
				"Sockets",
				"ToolMenus",
				"UnrealEd"
			});
	}
}
