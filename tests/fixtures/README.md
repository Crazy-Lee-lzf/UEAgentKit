# Unreal semantic fixtures

`create_ue_semantic_fixtures.py` recreates isolated assets under `/Game/UEAgentKitTests` for read-only exporter regression tests.

Created assets:

- `BP_SoftReferenceFixture`: Soft Object and Soft Class member variables with real defaults.
- `PAL_UEAgentKitManageFixture`: a `PrimaryAssetLabel` with explicit direct-manage targets.
- `DT_SearchableNameFixture`: a DataTable containing `Row_Alpha`.
- `BP_SearchableNameFixture`: a `DataTableRowHandle` default that produces a real Searchable Name dependency.

Run through the repository wrapper:

```powershell
scripts\CreateTestFixtures.ps1 -ProjectPath "E:\Path\To\Project.uproject"
```

The default reference target is the UE third-person template character. Override it when the project uses a different asset:

```powershell
scripts\CreateTestFixtures.ps1 `
    -ProjectPath "E:\Path\To\Project.uproject" `
    -ObjectTarget "/Game/Test/BP_Target.BP_Target" `
    -ClassTargetBlueprint "/Game/Test/BP_Target"
```

Requirements:

- Unreal Engine 5.6.
- `PythonScriptPlugin` and `EditorScriptingUtilities`; the wrapper enables them for the commandlet process.
- A disposable or explicitly authorized test project. The script deletes and recreates assets with the fixture names above.

The result report is written to `<Project>/Saved/UEAgentKitFixtures/semantic_fixtures.json`.

## Write fixtures

`write_fixture_plan.example.json` demonstrates the repository-shipped Blueprint-only Plan. Project-specific plans may add `duplicateAsset` entries for DataTables, Data Assets, Textures, Static Meshes, Material Instances, Input Actions, or other single-file assets available in that test project.

```powershell
scripts\RunWriteFixturePlan.ps1 `
    -ProjectPath "E:\Path\To\Project.uproject" `
    -Plan "tests\fixtures\write_fixture_plan.example.json" `
    -Mode Reset
```

The wrapper validates the Plan before launching Unreal, recreates only explicitly listed targets, then uses a second Unreal process to verify every fixture. See [`../../spec/WRITE_FIXTURE_PLAN.md`](../../spec/WRITE_FIXTURE_PLAN.md).
