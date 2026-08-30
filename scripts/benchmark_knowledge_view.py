"""Deterministic V2 performance gate (Track V plan 7.8 / V2 plan section 8.2).

Builds synthetic read-only fixture databases, starts the Knowledge Web server
on an ephemeral loopback port, and measures every V2 endpoint plus the V1
status endpoint. The graph measurement uses the stress path (5000 nodes).

Measurements are recorded as facts (node/edge counts, JSON bytes, server
queryMs from meta, HTTP round-trip ms) plus a best-effort client-side Canvas
render/frame-time sample via a headless browser (Edge/Chrome --headless=new
--dump-dom) when one is available. No new runtime dependency is used.

Usage:
    .venv/Scripts/python.exe scripts/benchmark_knowledge_view.py \
        [--nodes 5000] [--records 2000] [--out benchmarks/knowledge_view_5000.json]

Exit code 0 when every endpoint answers 200; otherwise non-zero.
"""

from __future__ import annotations

import argparse
import http.client
import json
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.knowledge_view import (  # noqa: E402
    GRAPH_STRESS_LIMIT,
    KnowledgeViewConfig,
    make_server,
)
from ue_agent_kit.memory_tree import KnowledgeNodeDraft, create_knowledge_node  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRecordType,
    MemorySourceKind,
    MemoryScope,
    MemoryScopeType,
    create_memory_record,
    open_project_memory_database,
)

PROJECT_KEY = "benchmark"
BRANCHING = 17  # layer sizes 1, 17, 289, 4913 -> 5220 graph nodes at depth 3
LAYER_SIZES = (1, BRANCHING, BRANCHING**2, BRANCHING**3)
ROOT_ASSET = "/Game/Perf/L0/A0"
HUB_ASSET = "/Game/Perf/HUB"
MEMORY_NODE_COUNT = 20
BROWSER_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)


def _request(host: str, port: int, path: str) -> tuple[int, dict[str, object], float]:
    started = time.perf_counter()
    connection = http.client.HTTPConnection(host, port, timeout=120)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        content_type = response.getheader("Content-Type") or ""
        if "application/json" in content_type:
            payload = json.loads(body.decode("utf-8"))
        else:
            payload = {"_bytes": len(body)}
        return response.status, payload, elapsed_ms
    finally:
        connection.close()


def _build_asset_database(path: Path) -> dict[str, int]:
    paths: list[str] = []
    asset_rows: list[tuple[str, str, str]] = []
    for layer, size in enumerate(LAYER_SIZES):
        for index in range(size):
            asset_path = f"/Game/Perf/L{layer}/A{index}"
            paths.append(asset_path)
            asset_rows.append((asset_path, f"A{layer}_{index}", "Blueprint"))
    asset_rows.append((HUB_ASSET, "HUB", "Blueprint"))
    with open_database(path) as connection:
        connection.executemany(
            """
            INSERT INTO assets(
                asset_path, package_name, asset_name, asset_class, blueprint_type,
                parent_class, generated_class, status, revision_value, package_guid,
                file_size, modified_utc, content_sha256, package_dirty, schema_version,
                exporter_version, profile, canonical_sha256, canonical_relpath,
                bpctx_relpath, summary_json, indexed_at_utc
            ) VALUES (?, 'perf', ?, ?, 'BlueprintClass',
                'Object', '/Script/Perf.Example_C', 0, 'rev-1', 'guid-1',
                1024, '2026-08-29T00:00:00Z', 'sha256-1', 0, '1.1',
                '1', 'default', 'canonical-1', 'canonical/example.json',
                '', '{}', '2026-08-29T00:00:00Z')
            """,
            asset_rows,
        )
        ids: dict[str, int] = {}
        for row in connection.execute("SELECT id, asset_path FROM assets"):
            ids[str(row["asset_path"])] = int(row["id"])
        reference_rows: list[tuple[int, str, str]] = []
        stable = 0
        offset = 0
        for layer in range(len(LAYER_SIZES) - 1):
            next_size = LAYER_SIZES[layer + 1]
            for index in range(LAYER_SIZES[layer]):
                source = paths[offset + index]
                for hop in range(BRANCHING):
                    target_index = (index * BRANCHING + hop) % next_size
                    target = paths[offset + LAYER_SIZES[layer] + target_index]
                    reference_rows.append(
                        (ids[source], f"ref-{stable}", source, target, "hardReference")
                    )
                    stable += 1
            offset += LAYER_SIZES[layer]
        for index in range(LAYER_SIZES[-1]):
            source = paths[offset + index]
            reference_rows.append(
                (ids[source], f"ref-{stable}", source, HUB_ASSET, "hardReference")
            )
            stable += 1
        connection.executemany(
            """
            INSERT INTO references_table(
                asset_id, stable_id, kind, source_symbol_id, target_symbol_id,
                target_kind, target_name, target_asset_path, target_path,
                graph_guid, graph_name, node_guid, node_class, node_title, details_json
            ) VALUES (?, ?, ?, '', '', 'Blueprint', ?, ?, '', '', '', '', '', '', '{}')
            """,
            [
                (asset_id, stable_id, kind, target, target)
                for asset_id, stable_id, _source, target, kind in reference_rows
            ],
        )
        connection.commit()
    return {
        "assets": len(ids),
        "references": len(reference_rows),
    }


def _build_memory_database(path: Path, record_count: int) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    statuses = ("valid", "stale", "conflicted", "superseded", "unverified")
    record_types = tuple(item.value for item in MemoryRecordType)
    with open_project_memory_database(path) as connection:
        root = create_knowledge_node(
            connection,
            KnowledgeNodeDraft(
                project_key=PROJECT_KEY,
                path="/project",
                node_type="project",
                title="基准项目根",
                summary="确定性基准内存数据",
            ),
        )
        node_ids: list[str] = []
        for index in range(MEMORY_NODE_COUNT):
            node = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT_KEY,
                    path=f"/project/n{index}",
                    node_type="system",
                    title=f"基准节点 {index}",
                    summary="确定性基准内存数据",
                    parent_node_id=root.node_id,
                ),
            )
            node_ids.append(node.node_id)
        record_ids: list[str] = []
        for index in range(record_count):
            scopes = (
                (MemoryScope(MemoryScopeType.ASSET, f"asset:bench:{index % 7}"),)
                if index % 3 == 0
                else ()
            )
            record = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT_KEY,
                    record_type=record_types[index % len(record_types)],
                    subject_key=f"bench:subject:{index}",
                    title=f"基准记录 {index}",
                    body="确定性基准正文内容。" * 10,
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    confidence=0.8,
                    node_id=node_ids[index % MEMORY_NODE_COUNT],
                    scopes=scopes,
                ),
            )
            record_ids.append(record.record_id)
        connection.executemany(
            "UPDATE memory_records SET status = ?, updated_at_utc = ? WHERE record_id = ?",
            [
                (
                    statuses[index % len(statuses)],
                    (now - timedelta(days=index % 200)).isoformat(),
                    record_ids[index],
                )
                for index in range(record_count)
            ],
        )
        connection.commit()
    return {"nodes": MEMORY_NODE_COUNT, "records": record_count}


def _find_browser() -> Path | None:
    for candidate in BROWSER_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _measure_client_render(browser: Path | None, graph_payload: dict[str, object]) -> dict[str, object]:
    """Best-effort headless Canvas render/frame-time sample (no runtime dep)."""
    if browser is None:
        return {"renderMs": None, "frameMs": None, "fps": None, "browser": None, "note": "no headless browser found"}
    nodes = json.dumps(graph_payload.get("nodes", []), ensure_ascii=False)
    edges = json.dumps(graph_payload.get("edges", []), ensure_ascii=False)
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<canvas id="cv" width="1200" height="800"></canvas>
<div id="result"></div>
<script>
"use strict";
const NODES = {nodes};
const EDGES = {edges};
const canvas = document.getElementById("cv");
const ctx = canvas.getContext("2d");
function layout() {{
  const depthOf = {{}};
  const adj = {{}};
  for (const e of EDGES) {{
    (adj[e.source] = adj[e.source] || []).push(e);
    if (e.source !== e.target) (adj[e.target] = adj[e.target] || []).push(e);
  }}
  const queue = [{json.dumps(graph_payload.get("meta", {}).get("root", ""))}];
  depthOf[queue[0]] = 0;
  while (queue.length) {{
    const cur = queue.shift();
    for (const e of (adj[cur] || [])) {{
      const nb = e.source === cur ? e.target : e.source;
      if (depthOf[nb] === undefined) {{ depthOf[nb] = depthOf[cur] + 1; queue.push(nb); }}
    }}
  }}
  const byDepth = {{}};
  for (const n of NODES) {{
    const d = depthOf[n.assetPath] === undefined ? 1 : depthOf[n.assetPath];
    (byDepth[d] = byDepth[d] || []).push(n.assetPath);
  }}
  const positions = {{}};
  for (const dText of Object.keys(byDepth)) {{
    const d = Number(dText);
    const list = byDepth[d];
    const radius = 90 + d * 130;
    const step = (Math.PI * 2) / list.length;
    list.forEach((p, i) => {{ const a = -Math.PI / 2 + i * step; positions[p] = {{x: radius * Math.cos(a), y: radius * Math.sin(a)}}; }});
  }}
  return positions;
}}
function render(positions, tx, ty, scale) {{
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, 1200, 800);
  ctx.save();
  ctx.translate(600 + tx, 400 + ty);
  ctx.scale(scale, scale);
  ctx.lineWidth = 1 / scale;
  for (const e of EDGES) {{
    const s = positions[e.source], t = positions[e.target];
    if (!s || !t) continue;
    ctx.strokeStyle = "#c3cbd8";
    ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(t.x, t.y); ctx.stroke();
  }}
  for (const n of NODES) {{
    const p = positions[n.assetPath];
    if (!p) continue;
    ctx.beginPath(); ctx.arc(p.x, p.y, n.root ? 9 : 6, 0, Math.PI * 2);
    ctx.fillStyle = n.root ? "#2563eb" : "#3f8cff"; ctx.fill();
  }}
  ctx.restore();
}}
function run() {{
  const t0 = performance.now();
  const positions = layout();
  const t1 = performance.now();
  render(positions, 0, 0, 1);
  const t2 = performance.now();
  const frameTimes = [];
  let tx = 0, ty = 0, scale = 1;
  for (let f = 0; f < 60; f++) {{
    const f0 = performance.now();
    tx += 3; ty -= 2; scale = 1 + (f % 30) * 0.02;
    render(positions, tx, ty, scale);
    const f1 = performance.now();
    frameTimes.push(f1 - f0);
  }}
  const avgFrame = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length;
  const result = {{
    layoutMs: t1 - t0,
    renderMs: t2 - t1,
    frameMs: avgFrame,
    fps: 1000 / avgFrame,
    nodeCount: NODES.length,
    edgeCount: EDGES.length
  }};
  document.getElementById("result").textContent = JSON.stringify(result);
}}
run();
</script></body></html>"""
    with tempfile.TemporaryDirectory(prefix="ueak_bench_render_") as temporary:
        page_path = Path(temporary) / "bench_render.html"
        page_path.write_text(html, encoding="utf-8")
        url = page_path.as_uri()
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--virtual-time-budget=60000",
            "--dump-dom",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"renderMs": None, "frameMs": None, "fps": None, "browser": browser.name, "note": f"headless run failed: {exc}"}
        match = re.search(r"\{[^{}]*\"layoutMs\"[^{}]*\}", completed.stdout)
        if not match:
            return {
                "renderMs": None,
                "frameMs": None,
                "fps": None,
                "browser": browser.name,
                "note": "headless run produced no timing result",
            }
        parsed = json.loads(match.group(0))
        return {
            "renderMs": parsed["renderMs"],
            "frameMs": parsed["frameMs"],
            "fps": parsed["fps"],
            "browser": browser.name,
            "note": "headless chrome --dump-dom under --virtual-time-budget; "
            "fps/frameMs are relative (virtual time), renderMs is the layout+first-draw sample",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=GRAPH_STRESS_LIMIT, help="graph node limit (default 5000)")
    parser.add_argument("--records", type=int, default=2000, help="memory records (default 2000)")
    parser.add_argument("--out", type=str, default="benchmarks/knowledge_view_benchmark_5000.json", help="JSON report path")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="ueak_bench_") as temporary:
        temporary_path = Path(temporary)
        memory_path = temporary_path / "memory.sqlite3"
        asset_path = temporary_path / "index.sqlite3"

        print("building synthetic fixture databases ...", flush=True)
        asset_counts = _build_asset_database(asset_path)
        memory_counts = _build_memory_database(memory_path, args.records)

        config = KnowledgeViewConfig(
            memory_database=memory_path,
            database=asset_path,
            project_key=PROJECT_KEY,
            host="127.0.0.1",
            port=0,
        )
        server = make_server(config)
        host, port = server.server_address[:2]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            root_query = f"/api/graph?root={ROOT_ASSET}&depth=3&direction=outgoing&limit={args.nodes}&stress=1"
            measurements: dict[str, object] = {}

            def measure(label: str, path: str) -> None:
                status, payload, http_ms = _request(host, port, path)
                if status != 200:
                    raise RuntimeError(f"{label} returned {status}: {path}")
                body = json.dumps(payload, ensure_ascii=False)
                entry: dict[str, object] = {
                    "httpMs": round(http_ms, 1),
                    "jsonBytes": len(body.encode("utf-8")),
                }
                if isinstance(payload, dict) and "meta" in payload:
                    entry["serverQueryMs"] = payload["meta"].get("queryMs")
                measurements[label] = entry
                print(f"  {label}: {status} http={http_ms:.0f}ms bytes={len(body)}", flush=True)

            measure("status", "/api/status")
            measure("graph", root_query)
            measure("impact", f"/api/impact/{quote(HUB_ASSET, safe='')}?limit=200")
            measure("coverage", "/api/coverage?limit=200")
            measure("timeline", "/api/timeline?limit=200&includeStatusEvents=true")
            measure("stale", "/api/stale?groupBy=nodePath&limit=200")

            graph_payload: dict[str, object]
            status, graph_payload, _http = _request(host, port, root_query)
            if status != 200:
                raise RuntimeError("graph re-fetch failed")
            graph_meta = graph_payload.get("meta", {})  # type: ignore[union-attr]
            measurements["graph"]["nodeCount"] = graph_meta.get("nodeCount")
            measurements["graph"]["edgeCount"] = graph_meta.get("edgeCount")
            measurements["graph"]["truncated"] = graph_payload.get("truncated")  # type: ignore[union-attr]
            impact_payload: dict[str, object]
            status, impact_payload, _ = _request(
                host, port, f"/api/impact/{quote(HUB_ASSET, safe='')}?limit=200"
            )
            if status != 200:
                raise RuntimeError("impact re-fetch failed")
            measurements["impact"]["totalConsumerAssets"] = impact_payload.get("totalConsumerAssets")  # type: ignore[union-attr]
            measurements["impact"]["truncated"] = impact_payload.get("truncated")  # type: ignore[union-attr]

            browser = _find_browser()
            measurements["client"] = _measure_client_render(browser, graph_payload)  # type: ignore[assignment]

            report: dict[str, object] = {
                "schema": "knowledge-view-benchmark/1.0",
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                },
                "syntheticData": {
                    "assetDatabase": asset_counts,
                    "memoryDatabase": memory_counts,
                    "graphNodeLimit": args.nodes,
                },
                "measurements": measurements,
            }
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = TOOL_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
