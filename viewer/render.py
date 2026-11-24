# SPDX-License-Identifier: GPL-3.0-or-later
"""Keeps a rendered copy of every model in the data folder.

A read-only headless IngeTrazo (no --allow-save) renders, per model, every
sheet (PNG), every saved view (PNG), the sheet atlas (PDF) and the model in
3D (GLB, with the saved views' cameras, for the browser's 3D view) into
``/cache/<slug>/r<rev>/`` and describes it in ``/cache/<slug>/index.json``.
A model is rendered again when its modification time or size changes and
has stayed put for a moment (a save in progress is not read half-written).
Each render goes to a fresh ``r<rev>`` folder, so a page never sees a mix
of old and new images, and URLs change with every render (no stale caches).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp_http.appbridge import DATA_DIR, App, list_models, log

CACHE_DIR = Path(os.environ.get("INGETRAZO_CACHE", "/cache"))
POLL = float(os.environ.get("VIEWER_POLL_SECONDS", 3))
SETTLE = float(os.environ.get("VIEWER_SETTLE_SECONDS", 2))
RETRY = float(os.environ.get("VIEWER_RETRY_SECONDS", 120))   # a failed render, again
SHEET_DPI = int(os.environ.get("VIEWER_SHEET_DPI", 120))
VIEW_W = int(os.environ.get("VIEWER_VIEW_WIDTH", 1600))
VIEW_H = int(os.environ.get("VIEWER_VIEW_HEIGHT", 1000))
WITH_PDF = os.environ.get("VIEWER_PDF", "1").strip().lower() not in ("0", "false", "no", "off")
#: The 3D model (GLB) for the browser's 3D view: one bigger than this many
#: MB is not offered (0 = no 3D view at all).
GLB_MAX_MB = float(os.environ.get("VIEWER_GLB_MAX_MB", 50) or 0)


#: Bumped when index.json gains something a page needs: a model rendered
#: by an older viewer is rendered again, even though its file is unchanged.
INDEX_FORMAT = 2


def slug_of(model: Path) -> str:
    rel = str(model.relative_to(DATA_DIR)).replace(os.sep, "/")
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", rel[:-4] if rel.endswith(".igz") else rel)
    return f"{base.strip('-')[:60]}-{hashlib.sha1(rel.encode()).hexdigest()[:6]}"


def _signature(model: Path) -> list:
    st = model.stat()
    return [st.st_mtime_ns, st.st_size]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(folder) -> list[str]:
    """A folder as the app reports it ("A/B", a list, or nothing) as a list
    of names, top first."""
    if isinstance(folder, (list, tuple)):
        return [str(p) for p in folder if str(p).strip()]
    return [p for p in str(folder or "").split("/") if p.strip()]


_MARK = "@@itz-viewer@@"
#: Read-only: each saved view's folder path, walking the Scenes tab's
#: nesting (core.scene_folders). Runs in a function so it leaves nothing
#: behind in the app's Python scope.
_SCENE_FOLDERS = f"""
def _itz_viewer_paths():
    import json
    from core import scene_folders as sf
    out = {{}}
    for v in scene.saved_views:
        path, f, seen = [], sf.folder_of(scene, v), set()
        while f and f not in seen:
            seen.add(f)
            path.insert(0, f)
            f = sf.folder_parent(scene, f)
        out[v.name] = path
    print({_MARK!r} + json.dumps(out, ensure_ascii=False))
_itz_viewer_paths()
del _itz_viewer_paths
"""

#: Read-only: each saved view's camera (core.saved_views.SavedView, the
#: orbit camera's own terms: target, distance, yaw, pitch, fov), for the
#: 3D view's camera presets.
_SCENE_CAMERAS = f"""
def _itz_viewer_cameras():
    import json
    out = []
    for v in scene.saved_views:
        out.append({{"name": v.name, "target": [float(c) for c in v.target],
                    "distance": float(v.distance), "yaw": float(v.yaw),
                    "pitch": float(v.pitch), "fov": float(v.fov_deg),
                    "perspective": bool(v.perspective)}})
    print({_MARK!r} + json.dumps(out, ensure_ascii=False))
_itz_viewer_cameras()
del _itz_viewer_cameras
"""


class Renderer:
    def __init__(self, app: App) -> None:
        self.app = app
        self.changed = threading.Condition()
        self.generation = 0          # bumped on every finished render
        self.last_event: dict = {}
        self.heartbeat = time.monotonic()
        self.rendering: str | None = None

    # ---- state the web side reads ------------------------------------------
    def models(self) -> list[dict]:
        out = []
        for model in list_models():
            slug = slug_of(model)
            index = self.index(slug) or {}
            out.append({"slug": slug,
                        "name": str(model.relative_to(DATA_DIR)).replace(os.sep, "/"),
                        "index": index,
                        "rendering": self.rendering == slug})
        return out

    def index(self, slug: str) -> dict | None:
        try:
            return json.loads((CACHE_DIR / slug / "index.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def _notify(self, event: dict) -> None:
        with self.changed:
            self.generation += 1
            self.last_event = {**event, "generation": self.generation}
            self.changed.notify_all()

    # ---- the loop ------------------------------------------------------------
    def run(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        while True:
            self.heartbeat = time.monotonic()
            try:
                self._pass()
            except Exception as exc:                 # keep watching
                log(f"viewer: render pass failed: {exc!r}")
            time.sleep(POLL)

    def _pass(self) -> None:
        models = list_models()
        live = set()
        for model in models:
            slug = slug_of(model)
            live.add(slug)
            try:
                sig = _signature(model)
            except OSError:
                continue
            index = self.index(slug)
            if index and index.get("source_signature") == sig \
                    and index.get("format") == INDEX_FORMAT and not (
                    index.get("errors") and time.time() - index.get("rendered_ts", 0) > RETRY):
                continue                             # up to date (failures retried)
            if time.time() - sig[0] / 1e9 < SETTLE:
                continue                             # still being written
            self._render(model, slug, sig)
            self.heartbeat = time.monotonic()
        # Models that are gone: their renders go too.
        for d in CACHE_DIR.iterdir() if CACHE_DIR.is_dir() else ():
            if d.is_dir() and d.name not in live:
                shutil.rmtree(d, ignore_errors=True)
                self._notify({"slug": d.name, "removed": True})

    def _call(self, tool: str, args: dict | None = None) -> dict:
        reply = self.app.call(tool, args or {})
        if not reply.get("ok"):
            raise RuntimeError(f"{tool}: {reply.get('error')}")
        return reply.get("result") or {}

    def _render(self, model: Path, slug: str, sig: list) -> None:
        name = str(model.relative_to(DATA_DIR)).replace(os.sep, "/")
        log(f"viewer: rendering {name}")
        self.rendering = slug
        self._notify({"slug": slug, "rendering": True})
        t0 = time.monotonic()
        base = CACHE_DIR / slug
        rev = f"r{time.time_ns() // 1_000_000}"
        out = base / rev
        out.mkdir(parents=True, exist_ok=True)
        index = {"format": INDEX_FORMAT, "name": name, "slug": slug, "rev": rev,
                 "source_signature": sig, "rendered_at": _now(),
                 "rendered_ts": time.time(),
                 "sheets": [], "views": [], "pdf": None, "errors": [],
                 "glb": None, "glb_note": None, "cameras": []}
        try:
            # The viewer never saves: whatever a render left changed goes.
            info = self._call("open_document", {"path": str(model),
                                                "discard_changes": True})
            # The 3D model first, while the document is as it was saved
            # (rendering a saved view switches layers on and off).
            if GLB_MAX_MB > 0:
                self._render_glb(out, index)
            sheets = self._sheet_list(info)
            if sheets:
                index["sheets"], index["errors"] = self._render_sheets(out, sheets)
                if WITH_PDF:
                    try:
                        self._call("export_pdf", {
                            "path": str(out / "sheets.pdf"), "format": "pdf",
                            "sheets": list(range(1, len(sheets) + 1))})
                        index["pdf"] = "sheets.pdf"
                    except RuntimeError as exc:
                        index["errors"].append(f"PDF: {exc}")
            views = list(info.get("views") or [])
            folders = self._view_folders() if views else {}
            for i, view in enumerate(views):
                file = f"view-{i:02d}.png"
                try:
                    self._call("export_view", {"view": view, "path": str(out / file),
                                               "width": VIEW_W, "height": VIEW_H})
                    index["views"].append({"name": view, "file": file,
                                           "folder": folders.get(view, [])})
                except RuntimeError as exc:
                    index["errors"].append(f"view {view}: {exc}")
        except Exception as exc:
            index["errors"].append(str(exc))
            log(f"viewer: {name}: {exc}")
        finally:
            self.rendering = None
        index["render_seconds"] = round(time.monotonic() - t0, 1)
        tmp = base / "index.json.part"
        tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False), "utf-8")
        tmp.replace(base / "index.json")
        for old in base.iterdir():                   # keep only this render
            if old.is_dir() and old.name != rev:
                shutil.rmtree(old, ignore_errors=True)
        log(f"viewer: {name}: {len(index['sheets'])} sheets, "
            f"{len(index['views'])} views"
            + (f", 3D {index['glb_mb']} MB" if index.get("glb") else "")
            + f" in {index['render_seconds']}s"
            + (f", {len(index['errors'])} problems" if index["errors"] else ""))
        self._notify({"slug": slug, "rev": rev})

    def _render_glb(self, out: Path, index: dict) -> None:
        """``model.glb`` for the 3D view, and the saved views' cameras. A
        file over ``VIEWER_GLB_MAX_MB`` is deleted and noted, not counted as
        an error: an error renders the model again every few minutes, and
        that would not make it any smaller."""
        glb = out / "model.glb"
        try:
            self._call("export_file", {"path": str(glb)})
        except RuntimeError as exc:
            index["errors"].append(f"3D model: {exc}")
            glb.unlink(missing_ok=True)
            return
        size = glb.stat().st_size if glb.is_file() else 0
        if not size:
            index["errors"].append("3D model: nothing was written")
            return
        mb = size / 1_000_000
        if mb > GLB_MAX_MB:
            glb.unlink(missing_ok=True)
            index["glb_note"] = (f"The 3D model is {mb:.1f} MB, more than the "
                                 f"{GLB_MAX_MB:g} MB limit (VIEWER_GLB_MAX_MB).")
            log(f"viewer: {index['name']}: 3D model left out ({mb:.1f} MB)")
            return
        index["glb"], index["glb_mb"] = glb.name, round(mb, 2)
        index["cameras"] = self._view_cameras()

    def _view_cameras(self) -> list[dict]:
        """The saved views' cameras (read-only); none if they cannot be read."""
        try:
            reply = self._call("run_python", {"code": _SCENE_CAMERAS})
            if not reply.get("error"):
                for line in (reply.get("stdout") or "").splitlines():
                    if line.startswith(_MARK):
                        got = json.loads(line[len(_MARK):])
                        return [c for c in got if isinstance(c, dict)]
        except (RuntimeError, ValueError, AttributeError):
            pass
        return []

    def _sheet_list(self, info: dict) -> list[dict]:
        """Every sheet in document order, with its folder path (a list of
        names, top first) and print flag when the app reports them."""
        names = list(info.get("sheets") or [])
        if not names:
            return []
        try:
            listed = self._call("list_sheets")
            rows = listed.get("sheets") if isinstance(listed, dict) else listed
            if isinstance(rows, list) and len(rows) == len(names):
                return [{"name": r.get("name", n), "folder": _path(r.get("folder")),
                         "printed": r.get("printed", r.get("print_in_all", True))}
                        for r, n in zip(rows, names)]
        except RuntimeError:
            pass
        return [{"name": n, "folder": [], "printed": True} for n in names]

    def _view_folders(self) -> dict[str, list[str]]:
        """Saved view name → its folder path (top first). ``list_scenes``
        names only the innermost folder, so the nesting is read from the
        document (read-only); without it, the innermost folder alone."""
        try:
            reply = self._call("run_python", {"code": _SCENE_FOLDERS})
            if not reply.get("error"):
                for line in (reply.get("stdout") or "").splitlines():
                    if line.startswith(_MARK):
                        got = json.loads(line[len(_MARK):])
                        return {str(k): [str(p) for p in v] for k, v in got.items()}
        except (RuntimeError, ValueError, AttributeError):
            pass
        try:
            listed = self._call("list_scenes")
            rows = listed.get("scenes") if isinstance(listed, dict) else listed
            return {r["name"]: _path(r.get("folder")) for r in rows or []
                    if isinstance(r, dict) and "name" in r}
        except (RuntimeError, KeyError, TypeError):
            return {}

    def _render_sheets(self, out: Path, sheets: list[dict]) -> tuple[list, list]:
        errors: list[str] = []
        # Places are 1-based; per sheet, the views that did not render.
        result = self._call("export_pdf", {
            "path": str(out / "sheet.png"), "format": "png",
            "dpi": SHEET_DPI, "sheets": list(range(1, len(sheets) + 1))})
        files = [Path(f) for f in result.get("files") or []]
        for sheet, msgs in (result.get("views_not_rendered") or {}).items():
            for m in msgs if isinstance(msgs, list) else [msgs]:
                errors.append(f"sheet {sheet}: {m}")
        rows = []
        for i, sheet in enumerate(sheets):
            row = dict(sheet)
            if i < len(files) and files[i].is_file():
                target = out / f"sheet-{i:02d}.png"
                files[i].replace(target)
                row["file"] = target.name
            else:
                errors.append(f"sheet {sheet['name']}: not rendered")
            rows.append(row)
        return rows, errors
