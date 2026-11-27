# SPDX-License-Identifier: AGPL-3.0-or-later
"""Does an IngeTrazo checkout have what the container needs?

    python tests/upstream_ready.py /opt/ingetrazo   (inside the engine image)

Reads the app's own MCP tool list (scripts/ingetrazo_mcp.py) and source,
prints a Markdown table and ends with ``READY`` or ``NOT READY``. Exit code
0 when every *required* item is there, 1 otherwise. CI's informational
"upstream" job runs it on IngeTrazo's newest release
(ingelibre/ingetrazo#1717).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_tools(app: Path) -> dict[str, dict]:
    spec = importlib.util.spec_from_file_location("ingetrazo_mcp", app / "scripts" / "ingetrazo_mcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {t["name"]: t for t in module.TOOLS}


def props(tools: dict, name: str) -> set[str]:
    schema = (tools.get(name) or {}).get("inputSchema") or {}
    return set((schema.get("properties") or {}).keys())


def source_has(app: Path, needle: str) -> bool:
    for rel in ("main.py", "scripts/ingetrazo_mcp.py", "plugins/ai_bridge.py"):
        f = app / rel
        if f.is_file() and needle in f.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def main() -> int:
    app = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/ingetrazo")
    tools = load_tools(app)
    # (required, what, upstream PR, present?)
    checks = [
        (True, "headless mode (`--headless`)", "0.6.3", source_has(app, "--headless")),
        (True, "`list_sheets` (sheet folders, print flag)", "#1544", "list_sheets" in tools),
        (True, "`export_pdf` with `format` (sheet PNGs)", "#1544", "format" in props(tools, "export_pdf")),
        (True, "`list_scenes`", "–", "list_scenes" in tools),
        (True, "`export_view`, `export_file`, `screenshot`", "–",
         all(t in tools for t in ("export_view", "export_file", "screenshot"))),
        (True, "`run_python` (scene folders, cameras, layers)", "–", "run_python" in tools),
        (False, "`INGETRAZO_AI_TIMEOUT` (long renders)", "#1586", source_has(app, "INGETRAZO_AI_TIMEOUT")),
        (False, "`--no_mcp_python` (lock run_python)", "#1683", source_has(app, "no_mcp_python")),
        (False, "`export_file` with `layers` (3D layer tree)", "#1685", "layers" in props(tools, "export_file")),
    ]
    print("| | Needed | What | Upstream |")
    print("|---|---|---|---|")
    ready = True
    for required, what, pr, ok in checks:
        ready &= ok or not required
        print(f"| {'✅' if ok else '❌'} | {'required' if required else 'optional'} | {what} | {pr} |")
    print()
    print("READY" if ready else "NOT READY")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
