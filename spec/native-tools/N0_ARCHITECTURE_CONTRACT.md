# Native Tools — Experimental Architecture and Compatibility Boundary

> **Status:** experimental architecture in the local development tree, **default OFF**. This document is not a declaration of production support and does not extend the published v0.8.0 capability set. The original N0 design and gate-review evidence are retained in private development documentation.

## Overview

The native tooling design aims to converge the Unreal Editor menu, Editor Bridge and MCP integration on versioned tool descriptors, bounded requests, a single authorization path and consistent error semantics.

The design must retain all existing public and legacy functionality when the new registry is unavailable or disabled. In particular, activating a missing/incompatible experimental registry must not silently weaken Write Policy, revision checks, transaction rules or disposable-project authorization.

## Availability

- The new Native Tool Registry and Native Layout Preview are **not generally available in the public v0.8.0 release**.
- The local integrated code keeps `NativeToolRegistry` and `NativeLayoutPreview` disabled by default. Even if an implementation exists in the checkout, **do not enable it in production without the separate rollout gates and operator approval**.
- `spec/native-tools/m2/*.schema.json` contains protocol schemas for layout broker development. A file labeled `draft` is not a stable production interface.
- No generic Blueprint GraphPatch Apply, unrestricted C++ invocation, arbitrary Editor command or bypass of project identity guards is granted here.

## How an AI Agent should use UE Agent Kit

1. Read the [AI Agent Quick Start](../../docs/AGENT_QUICKSTART_EN.md).
2. Discover actual tools via `ue_get_capabilities`.
3. Perform queries with registered read-only tools and respect the server's fixed project identity.
4. When a write workflow is registered and explicitly authorized, follow Policy → Revision → Plan/Dry Run → confirmation → Apply → Save (if required) → independent Verify.
5. Treat an unregistered or default-off experimental method as unavailable.

For the currently published MCP and Editor contracts, see [MCP Server](../MCP_SERVER.md) and [Live Editor Bridge](../LIVE_EDITOR_BRIDGE.md). Experimental native implementation details and release-gate reports are **internal development evidence**, not instructions for end-user activation.
