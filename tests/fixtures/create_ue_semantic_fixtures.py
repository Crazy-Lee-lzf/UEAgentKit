from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

import unreal

FIXTURE_ROOT = "/Game/UEAgentKitTests"
SOFT_BLUEPRINT_PATH = f"{FIXTURE_ROOT}/BP_SoftReferenceFixture"
MANAGE_LABEL_PATH = f"{FIXTURE_ROOT}/PAL_UEAgentKitManageFixture"
SEARCHABLE_TABLE_PATH = f"{FIXTURE_ROOT}/DT_SearchableNameFixture"
SEARCHABLE_BLUEPRINT_PATH = f"{FIXTURE_ROOT}/BP_SearchableNameFixture"
SEARCHABLE_ROW_NAME = "Row_Alpha"

OBJECT_TARGET = os.environ.get(
    "UEAK_FIXTURE_OBJECT_TARGET",
    "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter.BP_ThirdPersonCharacter",
)
CLASS_TARGET_BLUEPRINT = os.environ.get(
    "UEAK_FIXTURE_CLASS_TARGET_BLUEPRINT",
    "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter",
)

saved_root = Path(
    unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
) / "UEAgentKitFixtures"
saved_root.mkdir(parents=True, exist_ok=True)
result_path = saved_root / "semantic_fixtures.json"

result: dict[str, object] = {
    "fixtureRoot": FIXTURE_ROOT,
    "objectTarget": OBJECT_TARGET,
    "classTargetBlueprint": CLASS_TARGET_BLUEPRINT,
    "assets": {},
    "steps": [],
    "success": False,
}


def delete_asset(asset_path: str) -> None:
    if not unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        return
    if not unreal.EditorAssetLibrary.delete_asset(asset_path):
        raise RuntimeError(f"Failed to delete existing fixture: {asset_path}")
    result["steps"].append({"deleted": asset_path})


def save_asset(asset: unreal.Object, label: str) -> None:
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset, False):
        raise RuntimeError(f"Failed to save {label}: {asset.get_path_name()}")


def create_soft_reference_fixture() -> unreal.Blueprint:
    blueprint = unreal.BlueprintEditorLibrary.create_blueprint_asset_with_parent(
        SOFT_BLUEPRINT_PATH,
        unreal.Actor,
    )
    if blueprint is None:
        raise RuntimeError("Failed to create soft-reference Blueprint")

    soft_object_type = unreal.BlueprintEditorLibrary.get_object_reference_type(unreal.Object)
    soft_object_type.import_text(
        soft_object_type.export_text().replace(
            'PinCategory="object"',
            'PinCategory="softobject"',
            1,
        )
    )

    soft_class_type = unreal.BlueprintEditorLibrary.get_class_reference_type(unreal.Actor)
    soft_class_type.import_text(
        soft_class_type.export_text().replace(
            'PinCategory="class"',
            'PinCategory="softclass"',
            1,
        )
    )

    if not unreal.BlueprintEditorLibrary.add_member_variable(
        blueprint,
        "SoftObjectTarget",
        soft_object_type,
    ):
        raise RuntimeError("Failed to add SoftObjectTarget")
    if not unreal.BlueprintEditorLibrary.add_member_variable(
        blueprint,
        "SoftClassTarget",
        soft_class_type,
    ):
        raise RuntimeError("Failed to add SoftClassTarget")

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    generated_class = unreal.EditorAssetLibrary.load_blueprint_class(SOFT_BLUEPRINT_PATH)
    cdo = unreal.get_default_object(generated_class) if generated_class else None
    object_target = unreal.EditorAssetLibrary.load_asset(OBJECT_TARGET)
    class_target = unreal.EditorAssetLibrary.load_blueprint_class(CLASS_TARGET_BLUEPRINT)
    if cdo is None or object_target is None or class_target is None:
        raise RuntimeError("Failed to resolve soft-reference fixture defaults")

    cdo.set_editor_property("SoftObjectTarget", object_target)
    cdo.set_editor_property("SoftClassTarget", class_target)
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    save_asset(blueprint, "soft-reference Blueprint")

    loaded_class = unreal.EditorAssetLibrary.load_blueprint_class(SOFT_BLUEPRINT_PATH)
    loaded_cdo = unreal.get_default_object(loaded_class) if loaded_class else None
    if loaded_cdo is None:
        raise RuntimeError("Failed to reload soft-reference fixture CDO")

    soft_object_value = loaded_cdo.get_editor_property("SoftObjectTarget")
    soft_class_value = loaded_cdo.get_editor_property("SoftClassTarget")
    result["assets"]["softReference"] = {
        "assetPath": blueprint.get_path_name(),
        "softObject": soft_object_value.get_path_name() if soft_object_value else "",
        "softClass": soft_class_value.get_path_name() if soft_class_value else "",
        "pinTypes": {
            "softObject": soft_object_type.export_text(),
            "softClass": soft_class_type.export_text(),
        },
    }
    return blueprint


def create_manage_fixture(soft_blueprint: unreal.Blueprint) -> unreal.PrimaryAssetLabel:
    factory = unreal.DataAssetFactory()
    factory.set_editor_property("data_asset_class", unreal.PrimaryAssetLabel)
    label = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        "PAL_UEAgentKitManageFixture",
        FIXTURE_ROOT,
        unreal.PrimaryAssetLabel,
        factory,
    )
    if label is None:
        raise RuntimeError("Failed to create PrimaryAssetLabel")

    third_person_blueprint = unreal.EditorAssetLibrary.load_asset(OBJECT_TARGET)
    if third_person_blueprint is None:
        raise RuntimeError(f"Failed to load manage target: {OBJECT_TARGET}")

    label.set_editor_property(
        "explicit_assets",
        [soft_blueprint, third_person_blueprint],
    )
    label.set_editor_property("explicit_blueprints", [])
    label.set_editor_property("label_assets_in_my_directory", False)
    label.set_editor_property("is_runtime_label", True)

    rules = unreal.PrimaryAssetRules()
    rules.set_editor_property("priority", 50)
    rules.set_editor_property("chunk_id", -1)
    rules.set_editor_property("apply_recursively", False)
    label.set_editor_property("rules", rules)
    save_asset(label, "PrimaryAssetLabel")

    result["assets"]["manageLabel"] = {
        "assetPath": label.get_path_name(),
        "explicitAssets": [
            item.get_path_name()
            for item in label.get_editor_property("explicit_assets")
        ],
        "isRuntimeLabel": bool(label.get_editor_property("is_runtime_label")),
        "rules": label.get_editor_property("rules").export_text(),
    }
    return label


def create_searchable_name_fixture() -> tuple[unreal.DataTable, unreal.Blueprint]:
    table_factory = unreal.DataTableFactory()
    table_factory.set_editor_property(
        "struct",
        unreal.GameplayTagTableRow.static_struct(),
    )
    data_table = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        "DT_SearchableNameFixture",
        FIXTURE_ROOT,
        unreal.DataTable,
        table_factory,
    )
    if data_table is None:
        raise RuntimeError("Failed to create searchable-name DataTable")

    csv_result = data_table.fill_from_csv_string(
        "Name,Tag,DevComment\n"
        f"{SEARCHABLE_ROW_NAME},UEAgentKit.Searchable.Row,Fixture\n"
    )
    if not data_table.does_row_exist(SEARCHABLE_ROW_NAME):
        raise RuntimeError(f"DataTable row was not created: {SEARCHABLE_ROW_NAME}")
    save_asset(data_table, "searchable-name DataTable")

    blueprint = unreal.BlueprintEditorLibrary.create_blueprint_asset_with_parent(
        SEARCHABLE_BLUEPRINT_PATH,
        unreal.Actor,
    )
    if blueprint is None:
        raise RuntimeError("Failed to create searchable-name Blueprint")

    row_handle_type = unreal.BlueprintEditorLibrary.get_object_reference_type(unreal.Object)
    pin_text = row_handle_type.export_text()
    pin_text = pin_text.replace('PinCategory="object"', 'PinCategory="struct"', 1)
    pin_text = pin_text.replace(
        "/Script/CoreUObject.Object",
        "/Script/Engine.DataTableRowHandle",
        1,
    )
    row_handle_type.import_text(pin_text)

    if not unreal.BlueprintEditorLibrary.add_member_variable(
        blueprint,
        "SearchableRow",
        row_handle_type,
    ):
        raise RuntimeError("Failed to add SearchableRow")

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    generated_class = unreal.EditorAssetLibrary.load_blueprint_class(
        SEARCHABLE_BLUEPRINT_PATH
    )
    cdo = unreal.get_default_object(generated_class) if generated_class else None
    if cdo is None:
        raise RuntimeError("Failed to load searchable-name fixture CDO")

    row_handle = unreal.DataTableRowHandle()
    row_handle.set_editor_property("data_table", data_table)
    row_handle.set_editor_property("row_name", SEARCHABLE_ROW_NAME)
    cdo.set_editor_property("SearchableRow", row_handle)
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    save_asset(blueprint, "searchable-name Blueprint")

    loaded_class = unreal.EditorAssetLibrary.load_blueprint_class(
        SEARCHABLE_BLUEPRINT_PATH
    )
    loaded_cdo = unreal.get_default_object(loaded_class) if loaded_class else None
    verified = loaded_cdo.get_editor_property("SearchableRow") if loaded_cdo else None
    if verified is None:
        raise RuntimeError("Failed to reload SearchableRow")

    project_content = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
    )
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.scan_modified_asset_files(
        [
            str(project_content / "UEAgentKitTests" / "DT_SearchableNameFixture.uasset"),
            str(project_content / "UEAgentKitTests" / "BP_SearchableNameFixture.uasset"),
        ]
    )
    options = unreal.AssetRegistryDependencyOptions()
    options.set_editor_properties(
        {
            "include_soft_package_references": False,
            "include_hard_package_references": False,
            "include_searchable_names": True,
            "include_soft_management_references": False,
            "include_hard_management_references": False,
        }
    )
    dependencies = registry.get_dependencies(SEARCHABLE_BLUEPRINT_PATH, options) or []

    result["assets"]["searchableName"] = {
        "tablePath": data_table.get_path_name(),
        "blueprintPath": blueprint.get_path_name(),
        "rowName": SEARCHABLE_ROW_NAME,
        "rowStruct": data_table.get_row_struct().get_path_name(),
        "rows": [str(name) for name in data_table.get_row_names()],
        "csvResult": str(csv_result),
        "defaultValue": verified.export_text(),
        "registryDependencies": [str(value) for value in dependencies],
        "pinType": row_handle_type.export_text(),
    }
    return data_table, blueprint


try:
    for path in (
        MANAGE_LABEL_PATH,
        SEARCHABLE_BLUEPRINT_PATH,
        SEARCHABLE_TABLE_PATH,
        SOFT_BLUEPRINT_PATH,
    ):
        delete_asset(path)

    soft_fixture = create_soft_reference_fixture()
    create_manage_fixture(soft_fixture)
    create_searchable_name_fixture()
    result["success"] = True
except Exception as exc:
    result["error"] = repr(exc)
    result["traceback"] = traceback.format_exc()
    unreal.log_error(f"UEAgentKit fixture creation failed: {exc}")
finally:
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    unreal.log(f"UEAgentKit fixture result: {result_path}")

if not result["success"]:
    raise RuntimeError(result.get("error", "Unknown fixture creation failure"))
