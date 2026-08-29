"""W5 workload definitions and W4-bound validation.

The W5 "20 logical operations" scenario is deliberately split into multiple
legal W4 bounded workflows. A single W4 batch may contain at most 4 assets,
8 operations per asset, and 16 total operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BP_ASSET = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint"
DA_ASSET = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset"
BP_CLASS = "/Script/Engine.Blueprint"
DA_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"

PIN_GRAPH = "12345678-9abc-def0-1234-56789abcdef0"
PIN_NODE = "11111111-2222-2222-3333-333344444444"

W4_MAX_ASSETS_PER_BATCH = 4
W4_MAX_OPS_PER_ASSET = 8
W4_MAX_TOTAL_OPS_PER_BATCH = 16

BP_3OP = (
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 42},
    {
        "operation": "setComponentProperty",
        "target": {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        "value": 10,
    },
    {
        "operation": "setPinDefault",
        "target": {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": "A"},
        "value": 7,
    },
)

DA_1OP = ({"operation": "setAssetProperty", "target": {"propertyPath": "IntValue"}, "value": 142},)

BP_5OP = (
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 10},
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 20},
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 42},
    {
        "operation": "setComponentProperty",
        "target": {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        "value": 10,
    },
    {
        "operation": "setPinDefault",
        "target": {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": "A"},
        "value": 7,
    },
)

BP_8OP = (
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 10},
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 20},
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 42},
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 99},
    {
        "operation": "setComponentProperty",
        "target": {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        "value": 10,
    },
    {
        "operation": "setComponentProperty",
        "target": {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        "value": 20,
    },
    {
        "operation": "setPinDefault",
        "target": {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": "A"},
        "value": 7,
    },
    {
        "operation": "setPinDefault",
        "target": {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": "A"},
        "value": 8,
    },
)

DA_8OP = tuple(
    {"operation": "setAssetProperty", "target": {"propertyPath": "IntValue"}, "value": 100 + i}
    for i in range(1, 9)
)

BP_2OP = BP_8OP[:2]
DA_2OP = DA_8OP[:2]


@dataclass(frozen=True)
class W5AssetGroup:
    asset_path: str
    operations: tuple[dict[str, Any], ...]

    def validate(self) -> None:
        if not self.operations:
            raise ValueError(f"{self.asset_path} has no operations")
        if len(self.operations) > W4_MAX_OPS_PER_ASSET:
            raise ValueError(
                f"{self.asset_path} exceeds W4 max operations per asset "
                f"({len(self.operations)} > {W4_MAX_OPS_PER_ASSET})"
            )


@dataclass(frozen=True)
class W5Batch:
    batch_index: int
    assets: tuple[W5AssetGroup, ...]

    def validate(self) -> None:
        if not self.assets:
            raise ValueError(f"batch {self.batch_index} has no assets")
        if len(self.assets) > W4_MAX_ASSETS_PER_BATCH:
            raise ValueError(
                f"batch {self.batch_index} exceeds W4 max assets "
                f"({len(self.assets)} > {W4_MAX_ASSETS_PER_BATCH})"
            )
        total_ops = 0
        seen_assets: set[str] = set()
        for group in self.assets:
            group.validate()
            if group.asset_path in seen_assets:
                raise ValueError(f"batch {self.batch_index} repeats asset {group.asset_path}")
            seen_assets.add(group.asset_path)
            total_ops += len(group.operations)
        if total_ops > W4_MAX_TOTAL_OPS_PER_BATCH:
            raise ValueError(
                f"batch {self.batch_index} exceeds W4 max total operations "
                f"({total_ops} > {W4_MAX_TOTAL_OPS_PER_BATCH})"
            )

    @property
    def operations_per_batch(self) -> int:
        return sum(len(group.operations) for group in self.assets)

    @property
    def asset_paths(self) -> list[str]:
        return [group.asset_path for group in self.assets]


@dataclass(frozen=True)
class W5Workload:
    scenario_id: str
    logical_operation_count: int
    batches: tuple[W5Batch, ...]
    cache_states: tuple[str, ...] = ("WarmLoaded", "WarmUnloaded")

    def validate(self) -> None:
        total = 0
        for batch in self.batches:
            batch.validate()
            total += batch.operations_per_batch
        if total != self.logical_operation_count:
            raise ValueError(
                f"scenario {self.scenario_id} logical_operation_count={self.logical_operation_count} "
                f"but batches total {total}"
            )

    @property
    def batch_count(self) -> int:
        return len(self.batches)

    @property
    def operations_per_batch(self) -> list[int]:
        return [batch.operations_per_batch for batch in self.batches]

    @property
    def asset_paths(self) -> list[str]:
        result: list[str] = []
        for batch in self.batches:
            for asset_path in batch.asset_paths:
                if asset_path not in result:
                    result.append(asset_path)
        return result


def default_workloads() -> tuple[W5Workload, ...]:
    """Return the W5-R core workloads without requiring a live UE service."""
    r1 = W5Workload(
        scenario_id="R1",
        logical_operation_count=1,
        batches=(
            W5Batch(
                batch_index=1,
                assets=(W5AssetGroup(asset_path=DA_ASSET, operations=DA_1OP),),
            ),
        ),
    )
    r5 = W5Workload(
        scenario_id="R5",
        logical_operation_count=5,
        batches=(
            W5Batch(
                batch_index=1,
                assets=(W5AssetGroup(asset_path=BP_ASSET, operations=BP_5OP),),
            ),
        ),
    )
    r20 = W5Workload(
        scenario_id="R20",
        logical_operation_count=20,
        batches=(
            W5Batch(
                batch_index=1,
                assets=(
                    W5AssetGroup(asset_path=BP_ASSET, operations=BP_8OP),
                    W5AssetGroup(asset_path=DA_ASSET, operations=DA_8OP),
                ),
            ),
            W5Batch(
                batch_index=2,
                assets=(
                    W5AssetGroup(asset_path=BP_ASSET, operations=BP_2OP),
                    W5AssetGroup(asset_path=DA_ASSET, operations=DA_2OP),
                ),
            ),
        ),
    )
    for workload in (r1, r5, r20):
        workload.validate()
    return r1, r5, r20


def cold_commandlet_specs(workload: W5Workload) -> list[dict[str, Any]]:
    """Return a deterministic per-asset cold commandlet spec list.

    RunPatch.ps1 is single-asset only, so a multi-asset workload is represented
    as multiple cold commandlet invocations. The runner records each launch.
    """
    specs: list[dict[str, Any]] = []
    for batch in workload.batches:
        for group in batch.assets:
            specs.append(
                {
                    "batchIndex": batch.batch_index,
                    "assetPath": group.asset_path,
                    "operations": list(group.operations),
                    "commandlet": "BlueprintPatch" if group.asset_path == BP_ASSET else "AssetPatch",
                }
            )
    return specs


def scenario_for_id(scenario_id: str) -> W5Workload:
    for workload in default_workloads():
        if workload.scenario_id == scenario_id:
            return workload
    raise ValueError(f"unknown scenario: {scenario_id}")


def ensure_all_workloads_valid() -> None:
    for workload in default_workloads():
        workload.validate()
