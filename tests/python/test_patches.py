from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.cli import main  # noqa: E402
from ue_agent_kit.patches import (  # noqa: E402
    LIVE_WRITE_OPERATION_REGISTRY,
    _validate_additive_base_pose_fix_value,
    _validate_animation_scale_fix_value,
    validate_patch,
)


PROJECT_NAME = "我的项目"
ASSET_PATH = "/Game/UEAgentKitWriteTests/BP_PatchTarget.BP_PatchTarget"
ASSET_CLASS = "/Script/Engine.Blueprint"
REVISION = "sha256:" + "a" * 64
GRAPH_GUID = "11111111-1111-1111-1111-111111111111"
NODE_GUID = "22222222-2222-2222-2222-222222222222"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\r\n",
    )


def make_policy() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "validationEnabled": True,
        "commitEnabled": False,
        "allowedProjectNames": [PROJECT_NAME],
        "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
        "allowedReferenceRoots": [],
        "allowedReferenceClasses": [],
        "allowedOperations": [
            "setVariableDefault",
            "setComponentProperty",
            "setPinDefault",
            "setBlueprintDescription",
        ],
        "allowedAssetClasses": [
            "/Script/Engine.Blueprint",
            "/Script/UMGEditor.WidgetBlueprint",
            "/Script/Engine.AnimBlueprint",
        ],
        "allowedAssetProperties": [],
        "allowedMaterialParameters": [],
        "allowedDataTableFields": [],
        "requireRevision": True,
        "rejectDirtyPackages": True,
        "maxAssetsPerPatch": 10,
        "maxOperationsPerAsset": 32,
        "maxValueBytes": 65536,
    }


def make_patch() -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "patchId": "patch-baseline-test",
        "projectName": PROJECT_NAME,
        "description": "Validation-only baseline test.",
        "assets": [
            {
                "assetPath": ASSET_PATH,
                "expectedRevision": REVISION,
                "expectedAssetClass": ASSET_CLASS,
                "operations": [
                    {
                        "operationId": "set-health",
                        "operation": "setVariableDefault",
                        "target": {"variableName": "Health"},
                        "value": 125.0,
                    },
                    {
                        "operationId": "set-component-visible",
                        "operation": "setComponentProperty",
                        "target": {
                            "componentName": "StaticMesh",
                            "propertyPath": "Rendering.Visible",
                        },
                        "value": True,
                    },
                    {
                        "operationId": "set-pin-default",
                        "operation": "setPinDefault",
                        "target": {
                            "graphGuid": GRAPH_GUID,
                            "nodeGuid": NODE_GUID,
                            "pinName": "NewValue",
                        },
                        "value": "42",
                    },
                ],
            }
        ],
    }


def make_canonical() -> dict[str, Any]:
    return {
        "schemaVersion": "1.1",
        "exporterVersion": "0.2.6",
        "engineVersion": "5.6.1-test",
        "profile": "logic",
        "projectName": PROJECT_NAME,
        "assetPath": ASSET_PATH,
        "packageName": ASSET_PATH.rsplit(".", 1)[0],
        "assetClass": ASSET_CLASS,
        "revision": {
            "strategy": "package-sha256-v1",
            "available": True,
            "packageDirty": False,
            "value": REVISION,
        },
        "variables": [],
        "components": [],
        "functions": [],
        "graphs": [],
        "symbols": [],
        "references": [],
        "summary": {},
    }


def write_export(root: Path, canonical: dict[str, Any], *, failure_count: int = 0) -> None:
    canonical_path = root / "canonical" / "Game" / "UEAgentKitWriteTests" / "BP_PatchTarget.json"
    write_json(canonical_path, canonical)
    write_json(
        root / "manifest.json",
        {
            "schemaVersion": "1.1",
            "exporterVersion": "0.2.6",
            "engineVersion": "5.6.1-test",
            "profile": "logic",
            "projectName": PROJECT_NAME,
            "successCount": 1,
            "failureCount": failure_count,
            "assets": [{"assetPath": canonical["assetPath"], "success": True}],
        },
    )


class PatchValidationTests(unittest.TestCase):
    def test_live_write_registry_contains_complete_operation_metadata(self) -> None:
        expected = {
            "setAssetProperty": ("scalar", "property", "propertyPath"),
            "setAssetReferenceProperty": ("reference", "property", "propertyPath"),
            "setAssetStructuredProperty": ("structured", "property", "propertyPath"),
            "setAnimationScaleFix": ("animation-scale-fix", "animation-scale-fix", "rootBone"),
            "setAdditiveBasePoseFix": ("additive-base-pose-fix", "additive-base-pose-fix", ""),
            "setMaterialInstanceScalarParameter": ("material-scalar", "material-parameter", "parameterName"),
            "setMaterialInstanceVectorParameter": ("material-vector", "material-parameter", "parameterName"),
            "setMaterialInstanceTextureParameter": ("material-texture", "material-parameter", "parameterName"),
            "setMaterialInstanceStaticSwitchParameter": ("material-static-switch", "material-parameter", "parameterName"),
            "setDataTableCell": ("data-table-cell", "data-table-row", "rowName"),
            "setDataTableRowFields": ("data-table-row-fields", "data-table-row", "rowName"),
            "addDataTableRow": ("data-table-row-add", "data-table-row", "rowName"),
            "removeDataTableRow": ("data-table-row-remove", "data-table-row", "rowName"),
            "renameDataTableRow": ("data-table-row-rename", "data-table-row", "newRowName"),
            "setVariableDefault": ("blueprint-variable-default", "blueprint-variable", "variableName"),
            "setComponentProperty": ("blueprint-component-property", "blueprint-component", "componentName"),
            "setPinDefault": ("blueprint-pin-default", "blueprint-pin", "pinName"),
        }
        self.assertEqual(set(LIVE_WRITE_OPERATION_REGISTRY), set(expected))
        for name, metadata in expected.items():
            spec = LIVE_WRITE_OPERATION_REGISTRY[name]
            self.assertEqual(
                (spec.live_write_value_kind, spec.live_write_verification, spec.live_write_verification_target),
                metadata,
            )
            if name != "setAdditiveBasePoseFix":
                self.assertTrue(spec.target_fields)
            self.assertEqual(set(spec.target_fields), set(spec.target_validators))
    def test_animation_scale_fix_value_accepts_non_additive_contract(self) -> None:
        valid, message = _validate_animation_scale_fix_value(
            {
                "forceRootLock": True,
                "rootMotionRootLock": "RefPose",
                "rootTrackScaleMode": "Keep",
                "expectedFinalScale": 100.0,
            },
            65536,
        )
        self.assertTrue(valid, message)

    def test_animation_scale_fix_value_rejects_additive_bypass_field(self) -> None:
        valid, message = _validate_animation_scale_fix_value(
            {"forceRootLock": True, "allowAdditive": True},
            65536,
        )
        self.assertFalse(valid)
        self.assertIn("allowAdditive", message)

    def test_additive_base_pose_fix_value_accepts_contract(self) -> None:
        valid, message = _validate_additive_base_pose_fix_value(
            {
                "refSequencePath": "/Game/Animations/A_Base.A_Base",
                "refFrameIndex": 0,
                "expectedCombinedRootScale": 1.0,
            },
            65536,
        )
        self.assertTrue(valid, message)

    def test_additive_base_pose_fix_value_rejects_missing_required_fields(self) -> None:
        valid, message = _validate_additive_base_pose_fix_value(
            {"refFrameIndex": 0, "additiveAnimType": "LocalSpaceBase"},
            65536,
        )
        self.assertFalse(valid)
        self.assertIn("refSequencePath", message)
        valid, message = _validate_additive_base_pose_fix_value(
            {"refSequencePath": "/Game/Animations/A_Base.A_Base", "additiveAnimType": "LocalSpaceBase"},
            65536,
        )
        self.assertFalse(valid)
        self.assertIn("refFrameIndex", message)

    def test_additive_base_pose_fix_value_rejects_bad_ref_path(self) -> None:
        valid, message = _validate_additive_base_pose_fix_value(
            {"refSequencePath": "NotAValidPath", "refFrameIndex": 0},
            65536,
        )
        self.assertFalse(valid)
        self.assertIn("refSequencePath", message)

    def test_additive_base_pose_fix_value_rejects_bad_frame_index(self) -> None:
        valid, message = _validate_additive_base_pose_fix_value(
            {"refSequencePath": "/Game/Animations/A_Base.A_Base", "refFrameIndex": -1},
            65536,
        )
        self.assertFalse(valid)
        self.assertIn("refFrameIndex", message)

    def test_additive_base_pose_fix_value_rejects_unknown_field(self) -> None:
        valid, message = _validate_additive_base_pose_fix_value(
            {
                "refSequencePath": "/Game/Animations/A_Base.A_Base",
                "refFrameIndex": 0,
                "allowAdditive": True,
            },
            65536,
        )
        self.assertFalse(valid)
        self.assertIn("allowAdditive", message)

    def test_additive_base_pose_fix_value_rejects_bad_base_pose_type(self) -> None:
        valid, message = _validate_additive_base_pose_fix_value(
            {
                "refSequencePath": "/Game/Animations/A_Base.A_Base",
                "refFrameIndex": 0,
                "additiveBasePoseType": "NotAType",
            },
            65536,
        )
        self.assertFalse(valid)
        self.assertIn("additiveBasePoseType", message)


    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_patch_")
        self.root = Path(self.temporary.name)
        self.patch_path = self.root / "patch.json"
        self.policy_path = self.root / "policy.json"
        self.export_root = self.root / "export"
        self.patch = make_patch()
        self.policy = make_policy()
        self.canonical = make_canonical()
        self.flush()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def flush(self, *, failure_count: int = 0) -> None:
        write_json(self.patch_path, self.patch)
        write_json(self.policy_path, self.policy)
        write_export(self.export_root, self.canonical, failure_count=failure_count)

    def validate(self) -> dict[str, Any]:
        return validate_patch(self.patch_path, self.policy_path, self.export_root)

    def error_codes(self, result: dict[str, Any]) -> set[str]:
        return {item["code"] for item in result["errors"]}

    def configure_data_table_row_operation(
        self,
        operation: str,
        target: dict[str, str],
        value: Any,
        *,
        row_names: list[str] | None = None,
        authorized_fields: list[str] | None = None,
    ) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        asset_class = "/Script/Engine.DataTable"
        row_struct = "/Script/GameplayTags.GameplayTagTableRow"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": f"{operation}-test",
                "operation": operation,
                "target": target,
                "value": value,
            }
        ]
        self.policy["allowedOperations"].append(operation)
        self.policy["allowedAssetClasses"].append(asset_class)
        fields = authorized_fields if authorized_fields is not None else ["DevComment", "Tag"]
        self.policy["allowedDataTableFields"] = [
            f"{asset_class}#{row_struct}#{field_name}" for field_name in fields
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {
            "rowStructPath": row_struct,
            "rowNames": list(row_names if row_names is not None else ["Row_Alpha"]),
        }
        self.flush()

    def test_valid_patch_is_validation_only(self) -> None:
        result = self.validate()
        self.assertTrue(result["valid"])
        self.assertEqual(result["summary"]["validatedAssets"], 1)
        self.assertEqual(result["summary"]["validatedOperations"], 3)
        self.assertFalse(result["willLoadOrModifyUObjects"])
        self.assertFalse(result["willWriteDisk"])
        self.assertTrue(result["commitSupported"])
        self.assertTrue(all(item["valid"] for item in result["assets"][0]["operations"]))

    def test_asset_property_operation_is_valid(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/T_PatchTarget.T_PatchTarget"
        asset_class = "/Script/Engine.Texture2D"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-srgb",
                "operation": "setAssetProperty",
                "target": {"propertyPath": "SRGB"},
                "value": False,
            }
        ]
        self.policy["allowedOperations"].append("setAssetProperty")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedAssetProperties"] = [f"{asset_class}#SRGB"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "asset-property")

    def configure_asset_reference_operation(
        self,
        *,
        property_name: str = "ObjectValue",
        reference_type: str = "Object",
        value: Any | None = None,
    ) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DA_ReferencePatchTarget.DA_ReferencePatchTarget"
        asset_class = "/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset"
        if value is None:
            value = {
                "referenceType": reference_type,
                "path": (
                    "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter.BP_ThirdPersonCharacter_C"
                    if reference_type in {"Class", "SoftClass"}
                    else "/Game/Characters/Mannequins/Textures/Manny/T_Manny_02_D.T_Manny_02_D"
                ),
            }
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-reference",
                "operation": "setAssetReferenceProperty",
                "target": {"propertyPath": property_name},
                "value": value,
            }
        ]
        self.policy["allowedOperations"].append("setAssetReferenceProperty")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedAssetProperties"] = [f"{asset_class}#{property_name}"]
        self.policy["allowedReferenceRoots"] = [
            "/Game/Characters/Mannequins/Textures/Manny",
            "/Game/ThirdPerson/Blueprints",
        ]
        self.policy["allowedReferenceClasses"] = [
            "/Script/Engine.Texture2D",
            "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter.BP_ThirdPersonCharacter_C",
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {
            "type": "data-asset",
            "properties": [
                {
                    "name": property_name,
                    "propertyClass": f"{reference_type}Property",
                    "referenceType": reference_type,
                    "referenceClassPath": (
                        "/Script/Engine.Actor"
                        if reference_type in {"Class", "SoftClass"}
                        else "/Script/Engine.Texture2D"
                    ),
                    "conversionSucceeded": True,
                    "value": "",
                }
            ],
        }
        self.flush()

    def configure_asset_structured_operation(
        self,
        *,
        property_name: str = "StructValue",
        structured_type: str = "Struct",
        value: Any | None = None,
    ) -> None:
        scalar_int = {"kind": "Scalar", "scalarType": "Int32"}
        scalar_name = {"kind": "Scalar", "scalarType": "Name"}
        record_schema = {
            "kind": "Struct",
            "structPath": "/Script/UEAgentKitEditor.UEAgentKitStructuredFixtureRecord",
            "fields": [
                {"name": "Count", "schema": scalar_int},
                {"name": "Label", "schema": {"kind": "Scalar", "scalarType": "String"}},
                {"name": "bEnabled", "schema": {"kind": "Scalar", "scalarType": "Bool"}},
            ],
        }
        schemas = {
            "Struct": record_schema,
            "Array": {"kind": "Array", "element": scalar_int},
            "Set": {"kind": "Set", "element": scalar_name},
            "Map": {"kind": "Map", "key": scalar_name, "value": record_schema},
        }
        values = {
            "Struct": {
                "valueType": "Struct",
                "fields": {"Count": 7, "Label": "Updated", "bEnabled": False},
            },
            "Array": {"valueType": "Array", "items": [1, 4, 9]},
            "Set": {"valueType": "Set", "items": ["Alpha", "Gamma"]},
            "Map": {
                "valueType": "Map",
                "entries": [
                    {
                        "key": "Primary",
                        "value": {
                            "valueType": "Struct",
                            "fields": {"Count": 11, "Label": "Primary Updated", "bEnabled": False},
                        },
                    },
                    {
                        "key": "Tertiary",
                        "value": {
                            "valueType": "Struct",
                            "fields": {"Count": 30, "Label": "Tertiary", "bEnabled": True},
                        },
                    },
                ],
            },
        }
        asset_path = "/Game/UEAgentKitWriteTests/DA_StructuredPatchTarget.DA_StructuredPatchTarget"
        asset_class = "/Script/UEAgentKitEditor.UEAgentKitStructuredWriteFixtureAsset"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-structured",
                "operation": "setAssetStructuredProperty",
                "target": {"propertyPath": property_name},
                "value": copy.deepcopy(values[structured_type] if value is None else value),
            }
        ]
        self.policy["allowedOperations"].append("setAssetStructuredProperty")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedAssetProperties"] = [f"{asset_class}#{property_name}"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {
            "type": "data-asset",
            "properties": [
                {
                    "name": property_name,
                    "propertyClass": f"{structured_type}Property",
                    "structuredType": structured_type,
                    "structuredSupported": True,
                    "structuredSchema": copy.deepcopy(schemas[structured_type]),
                    "structuredError": "",
                    "conversionSucceeded": True,
                    "value": copy.deepcopy(values[structured_type]),
                }
            ],
        }
        self.flush()

    def test_asset_structured_property_models_are_valid(self) -> None:
        property_names = {
            "Struct": "StructValue",
            "Array": "ArrayValue",
            "Set": "SetValue",
            "Map": "MapValue",
        }
        for structured_type, property_name in property_names.items():
            with self.subTest(structured_type=structured_type):
                self.patch = make_patch()
                self.policy = make_policy()
                self.canonical = make_canonical()
                self.configure_asset_structured_operation(
                    property_name=property_name,
                    structured_type=structured_type,
                )
                result = self.validate()
                self.assertTrue(result["valid"], result["errors"])
                expected = result["assets"][0]["operations"][0]["expectedChange"]
                self.assertEqual(expected["kind"], "asset-structured-property")

    def test_asset_structured_property_rejects_incomplete_struct(self) -> None:
        self.configure_asset_structured_operation()
        del self.patch["assets"][0]["operations"][0]["value"]["fields"]["Label"]
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_asset_structured_property_rejects_unsorted_set(self) -> None:
        self.configure_asset_structured_operation(property_name="SetValue", structured_type="Set")
        self.patch["assets"][0]["operations"][0]["value"]["items"] = ["Gamma", "Alpha"]
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_asset_structured_property_rejects_canonical_numeric_duplicates(self) -> None:
        self.configure_asset_structured_operation(property_name="SetValue", structured_type="Set")
        property_details = self.canonical["assetDetails"]["properties"][0]
        property_details["structuredSchema"] = {
            "kind": "Set",
            "element": {"kind": "Scalar", "scalarType": "Double"},
        }
        self.patch["assets"][0]["operations"][0]["value"]["items"] = [1, 1.0]
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_asset_structured_property_rejects_duplicate_map_keys(self) -> None:
        self.configure_asset_structured_operation(property_name="MapValue", structured_type="Map")
        entries = self.patch["assets"][0]["operations"][0]["value"]["entries"]
        entries[1]["key"] = entries[0]["key"]
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_asset_structured_property_rejects_type_mismatch(self) -> None:
        self.configure_asset_structured_operation(property_name="ArrayValue", structured_type="Array")
        self.patch["assets"][0]["operations"][0]["value"] = {
            "valueType": "Set",
            "items": [1, 2],
        }
        self.flush()
        codes = self.error_codes(self.validate())
        self.assertTrue({"operation-value-type", "asset-structured-type-mismatch"}.intersection(codes))

    def test_asset_structured_property_rejects_unsupported_reader_schema(self) -> None:
        self.configure_asset_structured_operation()
        property_details = self.canonical["assetDetails"]["properties"][0]
        property_details["structuredSupported"] = False
        property_details["structuredError"] = "Unsupported object reference leaf"
        self.flush()
        self.assertIn("asset-structured-property-unsupported", self.error_codes(self.validate()))

    def test_asset_reference_property_object_model_is_valid(self) -> None:
        self.configure_asset_reference_operation()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "asset-reference-property")

    def test_asset_reference_property_null_clear_is_valid(self) -> None:
        self.configure_asset_reference_operation(value=None)
        self.patch["assets"][0]["operations"][0]["value"] = None
        self.flush()
        self.assertTrue(self.validate()["valid"])

    def test_asset_reference_property_rejects_type_mismatch(self) -> None:
        self.configure_asset_reference_operation(reference_type="Object")
        self.patch["assets"][0]["operations"][0]["value"]["referenceType"] = "SoftObject"
        self.flush()
        self.assertIn("asset-reference-type-mismatch", self.error_codes(self.validate()))

    def test_asset_reference_property_rejects_unauthorized_root(self) -> None:
        self.configure_asset_reference_operation()
        self.patch["assets"][0]["operations"][0]["value"]["path"] = "/Game/Outside/T_Outside.T_Outside"
        self.flush()
        self.assertIn("reference-not-allowed", self.error_codes(self.validate()))

    def test_asset_reference_property_requires_reader_metadata(self) -> None:
        self.configure_asset_reference_operation()
        self.canonical["assetDetails"]["properties"] = []
        self.flush()
        self.assertIn("asset-reference-property-missing", self.error_codes(self.validate()))

    def configure_additive_base_pose_fix_operation(
        self,
        *,
        ref_sequence_path: str = "/Game/UEAgentKitWriteTests/BasePoses/AM_Base.AM_Base",
    ) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/AM_Additive.AM_Additive"
        asset_class = "/Script/Engine.AnimSequence"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-additive-base-pose",
                "operation": "setAdditiveBasePoseFix",
                "target": {},
                "value": {
                    "refSequencePath": ref_sequence_path,
                    "refFrameIndex": 0,
                },
            }
        ]
        self.policy["allowedOperations"].append("setAdditiveBasePoseFix")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedReferenceRoots"] = ["/Game/UEAgentKitWriteTests/BasePoses"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()

    def test_additive_base_pose_fix_operation_is_valid(self) -> None:
        self.configure_additive_base_pose_fix_operation()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "additive-base-pose-fix")

    def test_additive_base_pose_fix_rejects_outside_reference_root(self) -> None:
        self.configure_additive_base_pose_fix_operation()
        self.patch["assets"][0]["operations"][0]["value"]["refSequencePath"] = (
            "/Game/Outside/AM_Outside.AM_Outside"
        )
        self.flush()
        self.assertIn("reference-not-allowed", self.error_codes(self.validate()))

    def test_asset_property_rejects_blueprint(self) -> None:
        self.patch["assets"][0]["operations"] = [
            {
                "operationId": "set-property",
                "operation": "setAssetProperty",
                "target": {"propertyPath": "Description"},
                "value": "not allowed",
            }
        ]
        self.policy["allowedOperations"].append("setAssetProperty")
        self.policy["allowedAssetProperties"] = [f"{ASSET_CLASS}#Description"]
        self.flush()
        self.assertIn("operation-asset-type", self.error_codes(self.validate()))

    def test_asset_property_requires_policy_allowlist(self) -> None:
        self.policy["allowedOperations"].append("setAssetProperty")
        self.policy["allowedAssetProperties"] = []
        self.flush()
        self.assertIn("policy-asset-properties", self.error_codes(self.validate()))

    def test_asset_property_requires_exact_authorization(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/T_PatchTarget.T_PatchTarget"
        asset_class = "/Script/Engine.Texture2D"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-srgb",
                "operation": "setAssetProperty",
                "target": {"propertyPath": "SRGB"},
                "value": False,
            }
        ]
        self.policy["allowedOperations"].append("setAssetProperty")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedAssetProperties"] = [f"{asset_class}#CompressionSettings"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("asset-property-not-allowed", self.error_codes(self.validate()))

    def test_invalid_asset_property_allowlist_entry_is_rejected(self) -> None:
        self.policy["allowedAssetProperties"] = ["not-a-class-or-property"]
        self.flush()
        self.assertIn("policy-asset-property-format", self.error_codes(self.validate()))

    def test_material_scalar_parameter_operation_is_valid(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_PatchTarget.MI_PatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-roughness",
                "operation": "setMaterialInstanceScalarParameter",
                "target": {"parameterName": "Roughness"},
                "value": 0.42,
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceScalarParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Scalar#Roughness"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "material-instance-scalar-parameter")

    def test_material_scalar_parameter_rejects_wrong_asset_class(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/T_PatchTarget.T_PatchTarget"
        asset_class = "/Script/Engine.Texture2D"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-roughness",
                "operation": "setMaterialInstanceScalarParameter",
                "target": {"parameterName": "Roughness"},
                "value": 0.42,
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceScalarParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Scalar#Roughness"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        codes = self.error_codes(self.validate())
        self.assertIn("operation-asset-type", codes)
        self.assertIn("policy-material-parameter-format", codes)

    def test_material_scalar_parameter_requires_numeric_value(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_PatchTarget.MI_PatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-roughness",
                "operation": "setMaterialInstanceScalarParameter",
                "target": {"parameterName": "Roughness"},
                "value": "0.42",
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceScalarParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Scalar#Roughness"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_material_scalar_parameter_requires_exact_authorization(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_PatchTarget.MI_PatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-roughness",
                "operation": "setMaterialInstanceScalarParameter",
                "target": {"parameterName": "Roughness"},
                "value": 0.42,
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceScalarParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Scalar#Metallic"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("material-parameter-not-allowed", self.error_codes(self.validate()))

    def test_material_scalar_parameter_requires_policy_allowlist(self) -> None:
        self.policy["allowedOperations"].append("setMaterialInstanceScalarParameter")
        self.policy["allowedMaterialParameters"] = []
        self.flush()
        self.assertIn("policy-material-parameters", self.error_codes(self.validate()))

    def test_invalid_material_parameter_allowlist_entry_is_rejected(self) -> None:
        self.policy["allowedMaterialParameters"] = ["not-a-material-parameter"]
        self.flush()
        self.assertIn("policy-material-parameter-format", self.error_codes(self.validate()))

    def test_material_vector_parameter_operation_is_valid(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_PatchTarget.MI_PatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-base-color",
                "operation": "setMaterialInstanceVectorParameter",
                "target": {"parameterName": "BaseColor"},
                "value": {"r": 0.25, "g": 0.5, "b": 0.75, "a": 1.0},
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceVectorParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Vector#BaseColor"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "material-instance-vector-parameter")

    def test_material_vector_parameter_requires_exact_rgba_object(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_PatchTarget.MI_PatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-base-color",
                "operation": "setMaterialInstanceVectorParameter",
                "target": {"parameterName": "BaseColor"},
                "value": {"r": 0.25, "g": 0.5, "b": 0.75},
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceVectorParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Vector#BaseColor"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_material_vector_parameter_requires_exact_authorization(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_PatchTarget.MI_PatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-base-color",
                "operation": "setMaterialInstanceVectorParameter",
                "target": {"parameterName": "BaseColor"},
                "value": {"r": 0.25, "g": 0.5, "b": 0.75, "a": 1.0},
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceVectorParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Vector#EmissiveColor"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("material-parameter-not-allowed", self.error_codes(self.validate()))

    def test_material_texture_parameter_operation_is_valid(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_TexturePatchTarget.MI_TexturePatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-base-texture",
                "operation": "setMaterialInstanceTextureParameter",
                "target": {"parameterName": "Base Texture"},
                "value": "/Game/Characters/Mannequins/Textures/Manny/T_Manny_02_D.T_Manny_02_D",
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceTextureParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Texture#Base Texture"]
        self.policy["allowedReferenceRoots"] = ["/Game/Characters/Mannequins/Textures"]
        self.policy["allowedReferenceClasses"] = ["/Script/Engine.Texture2D"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "material-instance-texture-parameter")

    def test_material_texture_parameter_rejects_outside_reference_root(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_TexturePatchTarget.MI_TexturePatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-base-texture",
                "operation": "setMaterialInstanceTextureParameter",
                "target": {"parameterName": "Base Texture"},
                "value": "/Game/Other/T_Forbidden.T_Forbidden",
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceTextureParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Texture#Base Texture"]
        self.policy["allowedReferenceRoots"] = ["/Game/Characters/Mannequins/Textures"]
        self.policy["allowedReferenceClasses"] = ["/Script/Engine.Texture2D"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("reference-not-allowed", self.error_codes(self.validate()))

    def test_material_texture_parameter_requires_reference_policy(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_TexturePatchTarget.MI_TexturePatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-base-texture",
                "operation": "setMaterialInstanceTextureParameter",
                "target": {"parameterName": "Base Texture"},
                "value": "/Game/Characters/Mannequins/Textures/Manny/T_Manny_02_D.T_Manny_02_D",
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceTextureParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#Texture#Base Texture"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        codes = self.error_codes(self.validate())
        self.assertIn("policy-reference-roots", codes)
        self.assertIn("policy-reference-classes", codes)

    def test_material_static_switch_parameter_operation_is_valid(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_StaticSwitchPatchTarget.MI_StaticSwitchPatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "disable-logo",
                "operation": "setMaterialInstanceStaticSwitchParameter",
                "target": {"parameterName": "Logo?"},
                "value": False,
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceStaticSwitchParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#StaticSwitch#Logo?"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "material-instance-static-switch-parameter")

    def test_material_static_switch_parameter_requires_boolean(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_StaticSwitchPatchTarget.MI_StaticSwitchPatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "disable-logo",
                "operation": "setMaterialInstanceStaticSwitchParameter",
                "target": {"parameterName": "Logo?"},
                "value": 0,
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceStaticSwitchParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#StaticSwitch#Logo?"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_material_static_switch_parameter_requires_exact_authorization(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/MI_StaticSwitchPatchTarget.MI_StaticSwitchPatchTarget"
        asset_class = "/Script/Engine.MaterialInstanceConstant"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "disable-logo",
                "operation": "setMaterialInstanceStaticSwitchParameter",
                "target": {"parameterName": "Logo?"},
                "value": False,
            }
        ]
        self.policy["allowedOperations"].append("setMaterialInstanceStaticSwitchParameter")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedMaterialParameters"] = [f"{asset_class}#StaticSwitch#Other"]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.flush()
        self.assertIn("material-parameter-not-allowed", self.error_codes(self.validate()))

    def test_data_table_cell_operation_is_valid(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        asset_class = "/Script/Engine.DataTable"
        row_struct = "/Script/GameplayTags.GameplayTagTableRow"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-comment",
                "operation": "setDataTableCell",
                "target": {"rowName": "Row_Alpha", "fieldName": "DevComment"},
                "value": "Verified",
            }
        ]
        self.policy["allowedOperations"].append("setDataTableCell")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedDataTableFields"] = [
            f"{asset_class}#{row_struct}#DevComment"
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {"rowStructPath": row_struct}
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "data-table-cell")

    def test_data_table_row_fields_operation_is_valid(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        asset_class = "/Script/Engine.DataTable"
        row_struct = "/Script/GameplayTags.GameplayTagTableRow"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-row-fields",
                "operation": "setDataTableRowFields",
                "target": {"rowName": "Row_Alpha"},
                "value": {"DevComment": "Verified", "Tag": "Gameplay.Test"},
            }
        ]
        self.policy["allowedOperations"].append("setDataTableRowFields")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedDataTableFields"] = [
            f"{asset_class}#{row_struct}#DevComment",
            f"{asset_class}#{row_struct}#Tag",
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {"rowStructPath": row_struct}
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"], result["errors"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "data-table-row-fields")
        self.assertEqual(expected["value"], {"DevComment": "Verified", "Tag": "Gameplay.Test"})

    def test_data_table_row_fields_requires_every_field_authorized(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        asset_class = "/Script/Engine.DataTable"
        row_struct = "/Script/GameplayTags.GameplayTagTableRow"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-row-fields",
                "operation": "setDataTableRowFields",
                "target": {"rowName": "Row_Alpha"},
                "value": {"DevComment": "Verified", "Tag": "Gameplay.Test"},
            }
        ]
        self.policy["allowedOperations"].append("setDataTableRowFields")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedDataTableFields"] = [
            f"{asset_class}#{row_struct}#DevComment",
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {"rowStructPath": row_struct}
        self.flush()
        result = self.validate()
        self.assertIn("data-table-field-not-allowed", self.error_codes(result))
        self.assertTrue(any(error["path"].endswith(".value.Tag") for error in result["errors"]))

    def test_data_table_row_fields_rejects_empty_nested_null_and_too_many(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        asset_class = "/Script/Engine.DataTable"
        row_struct = "/Script/GameplayTags.GameplayTagTableRow"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        self.policy["allowedOperations"].append("setDataTableRowFields")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedDataTableFields"] = [
            f"{asset_class}#{row_struct}#Value",
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {"rowStructPath": row_struct}
        invalid_values = [
            {},
            {"Value": None},
            {"Value": {"Nested": 1}},
            {f"Field{index}": index for index in range(33)},
        ]
        for index, value in enumerate(invalid_values):
            with self.subTest(index=index):
                asset["operations"] = [
                    {
                        "operationId": f"set-row-fields-{index}",
                        "operation": "setDataTableRowFields",
                        "target": {"rowName": "Row_Alpha"},
                        "value": value,
                    }
                ]
                self.flush()
                self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_data_table_cell_requires_exact_authorization(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        asset_class = "/Script/Engine.DataTable"
        row_struct = "/Script/GameplayTags.GameplayTagTableRow"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-comment",
                "operation": "setDataTableCell",
                "target": {"rowName": "Row_Alpha", "fieldName": "DevComment"},
                "value": "Verified",
            }
        ]
        self.policy["allowedOperations"].append("setDataTableCell")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedDataTableFields"] = [
            f"{asset_class}#{row_struct}#OtherField"
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {"rowStructPath": row_struct}
        self.flush()
        self.assertIn("data-table-field-not-allowed", self.error_codes(self.validate()))

    def test_data_table_cell_requires_policy_allowlist(self) -> None:
        self.policy["allowedOperations"].append("setDataTableCell")
        self.flush()
        self.assertIn("policy-data-table-fields", self.error_codes(self.validate()))

    def test_data_table_cell_rejects_null(self) -> None:
        asset_path = "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        asset_class = "/Script/Engine.DataTable"
        row_struct = "/Script/GameplayTags.GameplayTagTableRow"
        asset = self.patch["assets"][0]
        asset["assetPath"] = asset_path
        asset["expectedAssetClass"] = asset_class
        asset["operations"] = [
            {
                "operationId": "set-comment",
                "operation": "setDataTableCell",
                "target": {"rowName": "Row_Alpha", "fieldName": "DevComment"},
                "value": None,
            }
        ]
        self.policy["allowedOperations"].append("setDataTableCell")
        self.policy["allowedAssetClasses"].append(asset_class)
        self.policy["allowedDataTableFields"] = [
            f"{asset_class}#{row_struct}#DevComment"
        ]
        self.canonical["assetPath"] = asset_path
        self.canonical["packageName"] = asset_path.rsplit(".", 1)[0]
        self.canonical["assetClass"] = asset_class
        self.canonical["assetDetails"] = {"rowStructPath": row_struct}
        self.flush()
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_add_data_table_row_is_valid(self) -> None:
        self.configure_data_table_row_operation(
            "addDataTableRow",
            {"rowName": "Row_Beta"},
            {"DevComment": "Added", "Tag": "Gameplay.Added"},
        )
        result = self.validate()
        self.assertTrue(result["valid"], result["errors"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "data-table-row-add")

    def test_add_data_table_row_rejects_existing_row(self) -> None:
        self.configure_data_table_row_operation(
            "addDataTableRow",
            {"rowName": "Row_Alpha"},
            {},
        )
        self.assertIn("data-table-row-exists", self.error_codes(self.validate()))

    def test_add_data_table_row_requires_every_field_authorized(self) -> None:
        self.configure_data_table_row_operation(
            "addDataTableRow",
            {"rowName": "Row_Beta"},
            {"DevComment": "Added", "Tag": "Gameplay.Added"},
            authorized_fields=["DevComment"],
        )
        result = self.validate()
        self.assertIn("data-table-field-not-allowed", self.error_codes(result))
        self.assertTrue(any(error["path"].endswith(".value.Tag") for error in result["errors"]))

    def test_remove_data_table_row_is_valid(self) -> None:
        self.configure_data_table_row_operation(
            "removeDataTableRow",
            {"rowName": "Row_Alpha"},
            True,
        )
        result = self.validate()
        self.assertTrue(result["valid"], result["errors"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "data-table-row-remove")

    def test_remove_data_table_row_rejects_missing_row(self) -> None:
        self.configure_data_table_row_operation(
            "removeDataTableRow",
            {"rowName": "Row_Missing"},
            True,
        )
        self.assertIn("data-table-row-missing", self.error_codes(self.validate()))

    def test_remove_data_table_row_requires_true(self) -> None:
        self.configure_data_table_row_operation(
            "removeDataTableRow",
            {"rowName": "Row_Alpha"},
            False,
        )
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_rename_data_table_row_is_valid(self) -> None:
        self.configure_data_table_row_operation(
            "renameDataTableRow",
            {"rowName": "Row_Alpha", "newRowName": "Row_Beta"},
            True,
        )
        result = self.validate()
        self.assertTrue(result["valid"], result["errors"])
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "data-table-row-rename")

    def test_rename_data_table_row_rejects_missing_source(self) -> None:
        self.configure_data_table_row_operation(
            "renameDataTableRow",
            {"rowName": "Row_Missing", "newRowName": "Row_Beta"},
            True,
        )
        self.assertIn("data-table-row-missing", self.error_codes(self.validate()))

    def test_rename_data_table_row_rejects_existing_destination(self) -> None:
        self.configure_data_table_row_operation(
            "renameDataTableRow",
            {"rowName": "Row_Alpha", "newRowName": "Row_Beta"},
            True,
            row_names=["Row_Alpha", "Row_Beta"],
        )
        self.assertIn("data-table-row-exists", self.error_codes(self.validate()))

    def test_rename_data_table_row_rejects_unchanged_name(self) -> None:
        self.configure_data_table_row_operation(
            "renameDataTableRow",
            {"rowName": "Row_Alpha", "newRowName": "Row_Alpha"},
            True,
        )
        self.assertIn("data-table-row-name-unchanged", self.error_codes(self.validate()))

    def test_rename_data_table_row_requires_true(self) -> None:
        self.configure_data_table_row_operation(
            "renameDataTableRow",
            {"rowName": "Row_Alpha", "newRowName": "Row_Beta"},
            False,
        )
        self.assertIn("operation-value-type", self.error_codes(self.validate()))

    def test_blueprint_description_operation_is_valid(self) -> None:
        self.patch["assets"][0]["operations"] = [
            {
                "operationId": "set-description",
                "operation": "setBlueprintDescription",
                "target": {},
                "value": "Verified description.",
            }
        ]
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        self.assertEqual(result["summary"]["validatedOperations"], 1)
        expected = result["assets"][0]["operations"][0]["expectedChange"]
        self.assertEqual(expected["kind"], "blueprint-description")

    def test_blueprint_description_rejects_nonempty_target(self) -> None:
        self.patch["assets"][0]["operations"] = [
            {
                "operationId": "set-description",
                "operation": "setBlueprintDescription",
                "target": {"unexpected": True},
                "value": "Verified description.",
            }
        ]
        self.flush()
        self.assertIn("unknown-field", self.error_codes(self.validate()))

    def test_commit_enabled_policy_reports_executor_support(self) -> None:
        self.policy["commitEnabled"] = True
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        self.assertTrue(result["commitAllowedByPolicy"])
        self.assertTrue(result["commitSupported"])
        self.assertFalse(result["willWriteDisk"])

    def test_unknown_field_is_rejected(self) -> None:
        self.patch["unexpected"] = True
        self.flush()
        self.assertIn("unknown-field", self.error_codes(self.validate()))

    def test_retarget_capability_unknown_is_rejected(self) -> None:
        self.policy["retargetCapabilities"] = ["retarget.inspect", "retarget.mystery"]
        self.flush()
        self.assertIn("policy-retarget-capability-unknown", self.error_codes(self.validate()))

    def test_retarget_capabilities_are_optional_and_normalized(self) -> None:
        self.policy["retargetCapabilities"] = ["retarget.batch", "retarget.inspect"]
        self.flush()
        result = self.validate()
        self.assertTrue(result["valid"])
        self.assertEqual(result["policy"]["retargetCapabilities"], ["retarget.batch", "retarget.inspect"])

    def test_duplicate_json_key_is_rejected(self) -> None:
        self.patch_path.write_text(
            '{"schemaVersion":"1.0","schemaVersion":"1.0","patchId":"x","projectName":"我的项目","assets":[]}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            self.validate()

    def test_unknown_operation_is_rejected(self) -> None:
        self.patch["assets"][0]["operations"][0]["operation"] = "unknownOperation"
        self.flush()
        self.assertIn("unknown-operation", self.error_codes(self.validate()))

    def test_operation_not_authorized_is_rejected(self) -> None:
        self.policy["allowedOperations"] = ["setVariableDefault"]
        self.flush()
        self.assertIn("operation-not-allowed", self.error_codes(self.validate()))

    def test_project_mismatch_is_rejected(self) -> None:
        self.patch["projectName"] = "OtherProject"
        self.flush()
        codes = self.error_codes(self.validate())
        self.assertIn("project-not-allowed", codes)
        self.assertIn("project-mismatch", codes)

    def test_canonical_project_mismatch_is_rejected(self) -> None:
        self.canonical["projectName"] = "OtherProject"
        self.flush()
        self.assertIn("export-project-mismatch", self.error_codes(self.validate()))

    def test_asset_outside_authorized_root_is_rejected(self) -> None:
        self.policy["allowedAssetRoots"] = ["/Game/OtherRoot"]
        self.flush()
        result = self.validate()
        self.assertIn("asset-root-not-allowed", self.error_codes(result))
        self.assertTrue(all(not item["valid"] for item in result["assets"][0]["operations"]))

    def test_entire_game_root_cannot_be_authorized(self) -> None:
        self.policy["allowedAssetRoots"] = ["/Game/"]
        self.flush()
        self.assertIn("policy-root-too-broad", self.error_codes(self.validate()))

    def test_asset_class_mismatch_is_rejected(self) -> None:
        self.patch["assets"][0]["expectedAssetClass"] = "/Script/Engine.AnimBlueprint"
        self.flush()
        self.assertIn("asset-class-mismatch", self.error_codes(self.validate()))

    def test_revision_conflict_is_rejected(self) -> None:
        self.patch["assets"][0]["expectedRevision"] = "sha256:" + "b" * 64
        self.flush()
        self.assertIn("revision-conflict", self.error_codes(self.validate()))

    def test_revision_unavailable_is_rejected(self) -> None:
        self.canonical["revision"]["available"] = False
        self.canonical["revision"]["value"] = ""
        self.flush()
        self.assertIn("revision-unavailable", self.error_codes(self.validate()))

    def test_dirty_package_is_rejected(self) -> None:
        self.canonical["revision"]["packageDirty"] = True
        self.flush()
        self.assertIn("dirty-package", self.error_codes(self.validate()))

    def test_duplicate_asset_is_rejected(self) -> None:
        self.patch["assets"].append(copy.deepcopy(self.patch["assets"][0]))
        self.flush()
        self.assertIn("duplicate-asset", self.error_codes(self.validate()))

    def test_duplicate_operation_id_is_rejected(self) -> None:
        operations = self.patch["assets"][0]["operations"]
        operations[1]["operationId"] = operations[0]["operationId"]
        self.flush()
        self.assertIn("duplicate-operation-id", self.error_codes(self.validate()))

    def test_invalid_guid_is_rejected(self) -> None:
        self.patch["assets"][0]["operations"][2]["target"]["nodeGuid"] = "not-a-guid"
        self.flush()
        self.assertIn("operation-target-value", self.error_codes(self.validate()))

    def test_invalid_property_path_is_rejected(self) -> None:
        self.patch["assets"][0]["operations"][1]["target"]["propertyPath"] = "Rendering..Visible"
        self.flush()
        self.assertIn("operation-target-value", self.error_codes(self.validate()))

    def test_value_over_policy_limit_is_rejected(self) -> None:
        self.policy["maxValueBytes"] = 4
        self.patch["assets"][0]["operations"][0]["value"] = "12345"
        self.flush()
        self.assertIn("operation-value", self.error_codes(self.validate()))

    def test_nan_and_infinity_are_rejected(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.patch["assets"][0]["operations"][0]["value"] = value
                self.flush()
                self.assertIn("operation-value", self.error_codes(self.validate()))

    def test_incomplete_export_is_rejected(self) -> None:
        self.flush(failure_count=1)
        self.assertIn("export-incomplete", self.error_codes(self.validate()))

    def test_errors_are_stably_sorted(self) -> None:
        self.patch["unexpected"] = True
        self.patch["assets"][0]["expectedRevision"] = "bad"
        self.policy["allowedAssetRoots"] = ["/Game/OtherRoot"]
        self.flush()
        result = self.validate()
        keys = [(item["path"], item["code"], item["message"]) for item in result["errors"]]
        self.assertEqual(keys, sorted(keys))

    def test_cli_operations_and_validate_exit_codes(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            operations_code = main(["patch", "operations"])
        self.assertEqual(operations_code, 0)
        self.assertIn("setVariableDefault", output.getvalue())

        output = io.StringIO()
        with redirect_stdout(output):
            validate_code = main(
                [
                    "patch",
                    "validate",
                    "--patch",
                    str(self.patch_path),
                    "--policy",
                    str(self.policy_path),
                    "--export",
                    str(self.export_root),
                ]
            )
        self.assertEqual(validate_code, 0)

        self.patch["projectName"] = "OtherProject"
        self.flush()
        with redirect_stdout(io.StringIO()):
            invalid_code = main(
                [
                    "patch",
                    "validate",
                    "--patch",
                    str(self.patch_path),
                    "--policy",
                    str(self.policy_path),
                    "--export",
                    str(self.export_root),
                ]
            )
        self.assertEqual(invalid_code, 1)

        with redirect_stdout(io.StringIO()):
            missing_code = main(
                [
                    "patch",
                    "validate",
                    "--patch",
                    str(self.root / "missing.json"),
                    "--policy",
                    str(self.policy_path),
                    "--export",
                    str(self.export_root),
                ]
            )
        self.assertEqual(missing_code, 2)

    def test_cli_report_output_is_stable(self) -> None:
        report_path = self.root / "reports" / "patch-report.json"
        arguments = [
            "patch",
            "validate",
            "--patch",
            str(self.patch_path),
            "--policy",
            str(self.policy_path),
            "--export",
            str(self.export_root),
            "--report",
            str(report_path),
        ]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 0)
        first = report_path.read_bytes()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 0)
        second = report_path.read_bytes()
        self.assertEqual(first, second)
        report = json.loads(second.decode("utf-8"))
        self.assertTrue(report["valid"])
        self.assertFalse(report["willWriteDisk"])

    def test_multi_operation_plan_declares_atomic_transaction(self) -> None:
        result = self.validate()
        self.assertTrue(result["valid"])
        self.assertEqual(result["summary"]["operations"], 3)
        transaction = result["assets"][0]["transaction"]
        self.assertEqual(transaction["kind"], "single-asset-multi-operation")
        self.assertTrue(transaction["atomic"])
        self.assertEqual(transaction["operationCount"], 3)

    def test_multi_operation_plan_rejects_duplicate_target(self) -> None:
        duplicate = copy.deepcopy(self.patch["assets"][0]["operations"][0])
        duplicate["operationId"] = "set-health-again"
        duplicate["value"] = 200.0
        self.patch["assets"][0]["operations"].append(duplicate)
        self.flush()
        result = self.validate()
        self.assertFalse(result["valid"])
        self.assertIn("duplicate-transaction-target", self.error_codes(result))

    def test_structural_data_table_row_operation_must_be_single(self) -> None:
        self.configure_data_table_row_operation(
            "removeDataTableRow",
            {"rowName": "Row_Alpha"},
            True,
            row_names=["Row_Alpha", "Row_Beta"],
        )
        self.patch["assets"][0]["operations"].append(
            {
                "operationId": "remove-row-beta",
                "operation": "removeDataTableRow",
                "target": {"rowName": "Row_Beta"},
                "value": True,
            }
        )
        self.flush()
        result = self.validate()
        self.assertFalse(result["valid"])
        self.assertIn("transaction-operation-not-supported", self.error_codes(result))


if __name__ == "__main__":
    unittest.main()
