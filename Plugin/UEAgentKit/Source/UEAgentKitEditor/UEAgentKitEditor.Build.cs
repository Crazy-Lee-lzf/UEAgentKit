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
				"AssetRegistry",
				"AssetTools",
				"BlueprintGraph",
				"DataValidation",
				"IKRig",
				"IKRigEditor",
				"Json",
				"JsonUtilities",
				"Kismet",
				"MaterialEditor",
				"Networking",
				"Niagara",
				"Projects",
				"Slate",
				"SlateCore",
				"SlateRHIRenderer",
				"Sockets",
				"UnrealEd"
			});
	}
}
