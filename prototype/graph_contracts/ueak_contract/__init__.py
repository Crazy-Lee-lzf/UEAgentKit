"""G0.3 approved-decision contract candidate (stdlib only).

This package is a *validation prototype* for the shared contract.  It does not
import Unreal, does not write assets, and must not be presented as UE Editor
integration.
"""

from .canonical import canonical_json, domain_hash, is_sha256  # noqa: F401
from .errors import ContractError, load_registry  # noqa: F401
from .graph_patch import (  # noqa: F401
    Environment,
    GraphPatchIdempotencyStore,
    IdempotencyLedger,
    plan_patch,
    preview_patch,
    simulate_apply,
    verify_result,
)
from .layout import layout_graph, recompute_layout_hash  # noqa: F401
from .typed_graph import build_typed_graph, comment_hash, semantic_hash, validate  # noqa: F401
