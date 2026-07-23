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
				"BlueprintGraph",
				"Json",
				"JsonUtilities",
				"MaterialEditor",
				"Networking",
				"Niagara",
				"Projects",
				"Sockets",
				"UnrealEd"
			});
	}
}
