"""Explicit opt-in pure-live MCP mode (no SQLite index).

This module is only used when the operator starts the MCP server with the
explicit ``--pure-live`` flag (or ``RunMcp.ps1 -PureLive``).  In that mode the
server:

  * never constructs ``IndexQueryService`` and never opens/creates a SQLite file,
  * registers only the bounded, read-only Live Editor tools below,
  * keeps the fixed project identity and the Editor Bridge handshake unchanged,
  * reports ``mode="pure-live"`` with an explicit "index not opened" contract.

It is not a fallback: the default MCP startup still requires a schema-v2 index.
"""

from __future__ import annotations

from typing import Any

from .tool_registry import TOOL_DEFINITIONS_BY_NAME

PURE_LIVE_MODE = "pure-live"

# Exactly the tools registered by the pure-live startup path:
# two read-only meta tools plus the bounded Live Editor read set.
PURE_LIVE_READ_TOOL_NAMES: tuple[str, ...] = (
    "ue_get_capabilities",
    "ue_get_project_status",
    "ue_editor_status",
    "ue_get_selection",
    "ue_get_open_assets",
    "ue_get_dirty_assets",
    "ue_get_current_level",
    "ue_get_pie_state",
    "ue_get_pie_world_contexts",
    "ue_list_pie_actors",
    "ue_inspect_pie_actor",
    "ue_get_pie_scalars",
    "ue_list_pie_object_properties",
    "ue_read_pie_object_properties",
    "ue_get_output_log",
    "ue_get_compile_errors",
    "ue_inspect_asset_live",
    "ue_get_niagara_module_stack",
    "ue_get_blueprint_graph_selection",
    "ue_get_blueprint_graph_layout_preflight",
    "ue_get_focused_blueprint_graph_context",
    "ue_get_blueprint_graph_context",
)

PURE_LIVE_PIE_TOOL_NAMES: tuple[str, ...] = (
    "ue_get_pie_world_contexts",
    "ue_list_pie_actors",
    "ue_inspect_pie_actor",
    "ue_get_pie_scalars",
    "ue_list_pie_object_properties",
    "ue_read_pie_object_properties",
)

_PURE_LIVE_INDEX_STATUS: dict[str, Any] = {
    "configured": False,
    "required": False,
    "opened": False,
    "state": "not-opened",
    "reasonCode": "pure-live-mode",
}

_PURE_LIVE_SAFETY: dict[str, Any] = {
    "arbitrarySql": False,
    "arbitraryShell": False,
    "arbitraryFilesystem": False,
    "arbitraryConsole": False,
    "arbitraryPython": False,
    "arbitraryUObject": False,
    "writeToolsEnabled": False,
    "commitToolsEnabled": False,
    "graphEditEnabled": False,
    "memoryEnabled": False,
    "sourceControlEnabled": False,
    "offlineToolsEnabled": False,
    "indexOpened": False,
    "createsSqlite": False,
}


def pure_live_tool_descriptors() -> list[dict[str, Any]]:
    """Read-only descriptors for exactly the exposed pure-live tools."""
    descriptors: list[dict[str, Any]] = []
    for name in PURE_LIVE_READ_TOOL_NAMES:
        definition = TOOL_DEFINITIONS_BY_NAME.get(name)
        descriptors.append(
            {
                "name": name,
                "readOnly": bool(definition.read_only) if definition is not None else True,
                "destructive": False,
            }
        )
    return descriptors


def _live_status(live_editor_service: Any) -> dict[str, Any]:
    try:
        status = live_editor_service.status()
    except (OSError, RuntimeError, ValueError) as exc:  # defensive: never fake data
        return {
            "configured": True,
            "state": "unavailable",
            "reasonCode": "live-editor-unavailable",
            "reason": str(exc),
        }
    return status if isinstance(status, dict) else {"configured": True, "state": "unavailable"}


def build_pure_live_capabilities(
    *,
    server_name: str,
    server_version: str,
    live_status: dict[str, Any],
    exposed_tools: tuple[str, ...] = PURE_LIVE_READ_TOOL_NAMES,
) -> dict[str, Any]:
    tools = list(exposed_tools)
    return {
        "schemaVersion": "1.0",
        "tool": "ue_get_capabilities",
        "ok": True,
        "readOnly": True,
        "mode": PURE_LIVE_MODE,
        "server": {
            "name": server_name,
            "version": server_version,
            "transport": "stdio",
        },
        "index": dict(_PURE_LIVE_INDEX_STATUS),
        "offline": {
            "configured": False,
            "reasonCode": "pure-live-mode",
            "searchAvailable": False,
            "referencesAvailable": False,
            "assetQueryAvailable": False,
            "codeKnowledgeAvailable": False,
        },
        "workflow": {"configured": False},
        "projectMemory": {"configured": False},
        "sourceControl": {"configured": False},
        "tools": pure_live_tool_descriptors(),
        "liveEditor": {
            "configured": True,
            "readOnly": True,
            "transport": "localhost-tcp",
            "source": "editor-memory",
            "state": live_status.get("state", "unavailable"),
            "reasonCode": live_status.get("reasonCode", ""),
            "tools": tools,
            "fixedProject": True,
            "fixedEndpoint": True,
            "projectVersionHandshake": True,
            "arbitraryEndpointArguments": False,
            "writeSupported": False,
            "assetWriteSupported": False,
            "requiresDatabase": False,
            "serverStartupRequiresIndexDatabase": False,
            "indexRequired": False,
            "pieRuntimeInspection": {
                "available": True,
                "readOnly": True,
                "requiresDatabase": False,
                "serverStartupRequiresIndexDatabase": False,
                "pieControlSupported": False,
                "tools": list(PURE_LIVE_PIE_TOOL_NAMES),
                "bridgeMethods": ["editor.getPieWorldContexts", "editor.inspectPieActor"],
                "maxWorldContexts": 32,
                "maxActorComponents": 128,
                "maxComponentAssets": 32,
                "maxAmbiguousCandidates": 8,
                "actorIdentityFields": ["actorPath", "actorGuid", "actorName", "actorLabel"],
                "actorIdentityPolicy": "exactly-one-field-and-reject-ambiguous-names",
                "serverClientIdentification": True,
                "errorCodes": [
                    "pie-not-running",
                    "pie-instance-not-found",
                    "pie-instance-stale",
                    "pie-instance-ambiguous",
                    "pie-world-stale",
                    "pie-actor-not-found",
                    "pie-actor-ambiguous",
                    "pie-actor-stale",
                    "pie-actor-scan-truncated",
                    "pie-context-scan-truncated",
                    "pie-invalid-parameters",
                ],
            },
            "pieScalarInspection": {
                "available": True,
                "tool": "ue_get_pie_scalars",
                "bridgeMethod": "editor.getPieScalars",
                "readOnly": True,
                "requiresDatabase": False,
                "serverStartupRequiresIndexDatabase": False,
                "source": "pie-live",
                "maxProperties": 8,
                "maxPropertyNameChars": 64,
                "defaultStringChars": 256,
                "maxStringChars": 512,
                "supportedTypes": [
                    "bool",
                    "int32",
                    "int64",
                    "uint32",
                    "uint8",
                    "float32",
                    "float64",
                    "name",
                    "string",
                    "enum",
                ],
                "rejects": [
                    "dotted-paths",
                    "arrays",
                    "maps",
                    "sets",
                    "structs",
                    "text",
                    "object-references",
                    "methods",
                    "native-private",
                    "deprecated",
                    "not-edit-or-blueprint-visible",
                ],
                "identityPolicy": "exact-pie-instance-and-single-actor-identity",
                "componentPolicy": "at-most-one-owned-component-path-or-name-with-optional-class-check",
                "perPropertyStatus": ["ok", "unknown", "not-exposed", "unsupported-type"],
                "propertyProvenance": {
                    "onlyForResolvedProperties": True,
                    "fields": [
                        "declaringClassPath",
                        "declaringClassName",
                        "declaringClassKind",
                        "declaringClassPathTruncated",
                        "declaringBlueprintPath",
                        "declaringBlueprintPathTruncated",
                    ],
                    "kinds": ["native", "blueprint", "unknown"],
                    "source": "UE reflection: FProperty::GetOwnerClass()",
                    "policy": (
                        "reflected declaring owner class only; no filesystem path, no source-file "
                        "guessing, no raw object address; blueprint asset path only when "
                        "UClass::ClassGeneratedBy is a UBlueprint"
                    ),
                    "maxClassPathChars": 512,
                },
                "errorCodes": [
                    "pie-component-not-found",
                    "pie-component-ambiguous",
                    "pie-component-stale",
                ],
            },
            "pieObjectPropertyRead": {
                "available": True,
                "tools": [
                    "ue_list_pie_object_properties",
                    "ue_read_pie_object_properties",
                ],
                "bridgeMethods": [
                    "editor.listPieObjectProperties",
                    "editor.readPieObjectProperties",
                ],
                "readOnly": True,
                "requiresDatabase": False,
                "serverStartupRequiresIndexDatabase": False,
                "source": "pie-live",
                "reflectionDriven": True,
                "visibilityGate": "none",
                "visibilityGateNote": (
                    "CPF_Edit / CPF_BlueprintVisible are not a read gate; only unsafe or "
                    "undecodable types are refused"
                ),
                "supportedTypeKinds": [
                    "bool",
                    "int8",
                    "int16",
                    "int32",
                    "int64",
                    "uint8",
                    "uint16",
                    "uint32",
                    "uint64",
                    "float",
                    "double",
                    "name",
                    "string",
                    "enum",
                    "struct",
                    "array",
                    "set",
                    "map",
                    "object",
                    "class",
                    "softObject",
                    "softClass",
                    "weakObject",
                ],
                "refusedTypeKinds": [
                    "text",
                    "delegate",
                    "multicastDelegate",
                    "interface",
                    "fieldPath",
                    "bitfield",
                    "lazyObject",
                    "unknown",
                ],
                "maxListedProperties": 512,
                "maxReadProperties": 16,
                "maxPropertyNameChars": 128,
                "defaultContainerLimit": 50,
                "maxContainerLimit": 200,
                "defaultStringChars": 256,
                "minStringChars": 32,
                "maxStringChars": 512,
                "maxStructDepth": 4,
                "maxStructMembers": 64,
                "maxReturnedContainerItems": 512,
                "containerPaging": "per-request-offset-and-limit-with-totalCount-and-hasMore",
                "objectReferencePolicy": "identity-only-no-recursion-no-load",
                "targetKinds": ["actor", "component", "object"],
                "objectPathTarget": {
                    "accepted": True,
                    "scope": "any-UObject-inside-the-exact-pie-world",
                    "mutuallyExclusiveWith": [
                        "actorPath",
                        "actorGuid",
                        "actorName",
                        "actorLabel",
                        "componentPath",
                        "componentName",
                        "componentClassPath",
                    ],
                    "identityFields": [
                        "objectPath",
                        "classPath",
                        "pieInstanceId",
                        "worldKey",
                        "outerPath",
                        "outerClassPath",
                    ],
                    "chainPolicy": "explicit-hop-per-call-never-auto-recursive",
                    "chainEntryPoint": "objectRef.path-is-directly-consumable-as-objectPath",
                    "notFoundCode": "pie-object-not-found",
                    "crossWorldCode": "pie-world-stale",
                },
                "objectReferenceNavigation": {
                    "available": True,
                    "fields": [
                        "path",
                        "class",
                        "worldKey",
                        "pieInstanceId",
                        "outerPath",
                        "outerClass",
                        "isNull",
                        "inPieWorld",
                        "crossWorld",
                        "propertiesReadable",
                    ],
                    "crossWorldPolicy": "flagged-refused-when-followed",
                    "autoRecursion": False,
                    "loadsPackages": False,
                },
                "perPropertyStatus": ["ok", "unsupported-type", "unknown", "not-exposed"],
                "emittedStatuses": ["ok", "unsupported-type", "unknown"],
                "errorCodes": [
                    "pie-not-running",
                    "pie-instance-not-found",
                    "pie-instance-stale",
                    "pie-instance-ambiguous",
                    "pie-world-stale",
                    "pie-actor-not-found",
                    "pie-actor-ambiguous",
                    "pie-actor-stale",
                    "pie-component-not-found",
                    "pie-component-ambiguous",
                    "pie-component-stale",
                    "pie-actor-scan-truncated",
                    "pie-context-scan-truncated",
                    "pie-object-not-found",
                    "pie-invalid-parameters",
                ],
            },
        },
        "safety": dict(_PURE_LIVE_SAFETY),
    }


def build_pure_live_project_status(
    *,
    live_status: dict[str, Any],
    project_name: str,
    project_path_hash: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "tool": "ue_get_project_status",
        "ok": True,
        "readOnly": True,
        "mode": PURE_LIVE_MODE,
        "index": dict(_PURE_LIVE_INDEX_STATUS),
        "project": {
            "name": project_name,
            "pathHash": project_path_hash,
            "fixed": True,
            "identitySource": "startup --project + Editor Bridge descriptor handshake",
        },
        "liveEditor": live_status,
        "workflow": {"configured": False},
        "projectMemory": {"configured": False},
        "sourceControl": {"configured": False},
    }


def register_pure_live_meta_tools(
    *,
    server: Any,
    live_editor_service: Any,
    server_name: str,
    server_version: str,
    read_annotations: Any,
    exposed_tools: tuple[str, ...] = PURE_LIVE_READ_TOOL_NAMES,
) -> None:
    """Register exactly the two read-only meta tools for pure-live mode."""

    @server.tool(annotations=read_annotations)
    def ue_get_capabilities() -> dict[str, Any]:
        """Return the pure-live read-only contract: no index, no workflow, no writes."""
        return build_pure_live_capabilities(
            server_name=server_name,
            server_version=server_version,
            live_status=_live_status(live_editor_service),
            exposed_tools=exposed_tools,
        )

    @server.tool(annotations=read_annotations)
    def ue_get_project_status() -> dict[str, Any]:
        """Return the fixed project identity and Live Editor state without opening an index."""
        config = live_editor_service.config
        return build_pure_live_project_status(
            live_status=_live_status(live_editor_service),
            project_name=config.project_name,
            project_path_hash=config.project_path_hash,
        )
