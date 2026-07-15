using UnrealBuildTool;

public class BlueprintContextToolEditor : ModuleRules
{
	public BlueprintContextToolEditor(ReadOnlyTargetRules Target) : base(Target)
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
				"Projects",
				"UnrealEd"
			});
	}
}
