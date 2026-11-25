# SPDX-License-Identifier: AGPL-3.0-or-later
"""The model page's 3D view: the model's GLB (render.py) in three.js, turned
with the mouse or a finger (OrbitControls), with the edges drawn as lines
and the saved views as camera presets – picked in the top bar's 3D
dropdown (server.py), which turns the camera here without a reload.

three.js is not in this repo: the Dockerfile fetches a pinned release
(checksummed) into ``viewer/static/three/``, and server.py serves it under
``/static/three/`` behind the viewer's login, so the page needs no CDN.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

THREE_DIR = Path(__file__).resolve().parent / "static" / "three"

CSS = """
figure.stage{margin:0;display:flex;flex-direction:column}
.stage .canvas{position:relative;height:calc(100vh - 136px);min-height:320px;background:var(--sheet)}
.stage canvas{display:block;width:100%;height:100%;outline:none;touch-action:none}
.stage .status{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
color:#6b6b66;font-size:14px;pointer-events:none;text-align:center;padding:16px}
.stage .status[hidden]{display:none}
.stage figcaption{flex-wrap:wrap;align-items:center}
.stage .btn{cursor:pointer;font:inherit;font-size:13px}
@media (max-width:820px){.stage .canvas{height:70vh}.stage .hint{display:none}}
"""

#: The 3D view's script. Reads its settings from the stage's data-*
#: attributes; keeps the camera across the page's live reloads
#: (sessionStorage) and the edge switch in the user's settings (without
#: an account: localStorage).
JS = r"""
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const stage = document.getElementById('stage');
const box = stage.querySelector('.canvas');
const status = stage.querySelector('.status');
// The 3D dropdown of the top bar: its entries name a camera (data-cam,
// 0 = the whole model); a pick turns the camera here instead of reloading.
const dd = document.querySelector('details.dd[data-kind="3d"]');
let cur = -1;                         // the camera picked, -1: none
function mark(i) {
  cur = i;
  if (!dd) return;
  let label = null;
  dd.querySelectorAll('a[data-cam]').forEach((a) => {
    const on = +a.dataset.cam === i + 1;
    a.classList.toggle('on', on);
    if (on) { a.setAttribute('aria-current', 'page'); label = a; } else a.removeAttribute('aria-current');
  });
  const span = dd.querySelector('summary .cur');
  if (span && label) {
    span.className = 'cur';
    span.textContent = label.textContent;
    span.title = (label.dataset.folder ? label.dataset.folder + ' \u203a ' : '') + label.textContent;
  }
}
const edgeBtn = document.getElementById('edges');
const cameras = JSON.parse(stage.dataset.cameras || '[]');
const camKey = 'cam3d:' + (document.body.dataset.slug || '');
const say = (t) => { status.textContent = t; status.hidden = !t; };
const store = (s, k, v) => { try { v === undefined ? s.removeItem(k) : s.setItem(k, v); } catch (e) {} };
const load = (s, k) => { try { return s.getItem(k); } catch (e) { return null; } };

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
} catch (e) {
  say('This browser cannot show 3D (WebGL is off or not supported).');
  throw e;
}
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
box.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(
  getComputedStyle(document.documentElement).getPropertyValue('--sheet').trim() || '#ffffff');
scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a84, 2.0));
const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
const head = new THREE.DirectionalLight(0xffffff, 1.6);   // a light at the eye
head.position.set(0.3, 0.6, 0);
head.target.position.set(0, 0, -1);                      // ahead of the eye
camera.add(head, head.target);
scene.add(camera);

const controls = new OrbitControls(camera, renderer.domElement);
controls.screenSpacePanning = true;
let model = null, edges = null, size = 1;

function draw() {
  // Near and far follow the camera: close to a wall and across the site.
  const d = camera.position.distanceTo(controls.target);
  camera.near = Math.max(d / 2000, size / 1e6);
  camera.far = d + size * 4;
  camera.updateProjectionMatrix();
  renderer.render(scene, camera);
}
controls.addEventListener('change', draw);

function fitSize() {
  const w = box.clientWidth, h = box.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  draw();
}
new ResizeObserver(fitSize).observe(box);

// IngeTrazo's orbit camera (Z up): eye = target + d·(cos p·cos y, cos p·sin y, sin p).
// glTF is Y up: (x, y, z) → (x, z, −y), as the export bakes it.
function orbit(target, distance, yaw, pitch, fov) {
  const p = Math.max(-1.5698, Math.min(1.5698, pitch));   // just short of straight down
  const ex = distance * Math.cos(p) * Math.cos(yaw);
  const ey = distance * Math.cos(p) * Math.sin(yaw);
  const ez = distance * Math.sin(p);
  const t = new THREE.Vector3(target[0], target[2], -target[1]);
  camera.fov = fov || 45;
  camera.up.set(0, 1, 0);
  camera.position.set(t.x + ex, t.y + ez, t.z - ey);
  controls.target.copy(t);
  controls.update();
  draw();
}

function fit() {
  if (!model) return;
  const sphere = new THREE.Box3().setFromObject(model).getBoundingSphere(new THREE.Sphere());
  const r = Math.max(sphere.radius, 1e-3);
  const fov = 45;
  const d = r / Math.sin(THREE.MathUtils.degToRad(fov) / 2) * 1.05;
  const c = sphere.center;   // back to Z up for orbit()
  orbit([c.x, -c.z, c.y], d, -Math.PI / 4, Math.PI / 6, fov);   // IngeTrazo's default view
}

function preset(i) {
  const c = cameras[i];
  if (c) orbit(c.target, c.distance, c.yaw, c.pitch, c.fov); else fit();
}

function showEdges(on) {
  if (on && !edges && model) {
    edges = new THREE.Group();
    const mat = new THREE.LineBasicMaterial({ color: 0x1d1d1b });
    const made = new Map();          // copies of a component share one geometry
    model.updateMatrixWorld(true);
    model.traverse((o) => {
      if (!o.isMesh) return;
      let g = made.get(o.geometry);
      if (!g) { g = new THREE.EdgesGeometry(o.geometry, 25); made.set(o.geometry, g); }
      const line = new THREE.LineSegments(g, mat);
      line.matrixAutoUpdate = false;
      line.matrix.copy(o.matrixWorld);
      edges.add(line);
    });
    scene.add(edges);
  }
  if (edges) edges.visible = on;
  edgeBtn.classList.toggle('on', on);
  edgeBtn.setAttribute('aria-pressed', String(on));
  draw();
}

function saveEdges(on) {
  if (document.body.dataset.csrf && window.saveSetting) window.saveSetting('edges_3d', on ? 1 : 0);
  else store(localStorage, 'edges3d', on ? '1' : '0');
}

edgeBtn.addEventListener('click', () => {
  const on = !(edges && edges.visible);
  showEdges(on);
  saveEdges(on);
});
function choose(i) {
  mark(i);
  preset(i);
  // The address names the camera, so a link opens the same one (?3d=1&cam=N).
  const u = new URL(location.href);
  if (i < 0) u.searchParams.delete('cam'); else u.searchParams.set('cam', String(i + 1));
  history.replaceState(null, '', u);
}
document.getElementById('fit').addEventListener('click', () => choose(-1));
if (dd) dd.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-cam]');
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
  e.preventDefault();
  dd.open = false;
  choose(+a.dataset.cam - 1);
});
// A render that finished reloads the page: the camera stays where it was.
window.addEventListener('pagehide', () => store(sessionStorage, camKey, JSON.stringify({
  p: camera.position.toArray(), t: controls.target.toArray(), f: camera.fov,
  c: cur })));

say('Loading the 3D model…');
new GLTFLoader().load(stage.dataset.glb, (gltf) => {
  model = gltf.scene;
  model.traverse((o) => {
    if (!o.isMesh) return;
    // The edge lines win over the faces they lie on.
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      m.polygonOffset = true; m.polygonOffsetFactor = 1; m.polygonOffsetUnits = 1;
    }
  });
  scene.add(model);
  size = new THREE.Box3().setFromObject(model).getSize(new THREE.Vector3()).length() || 1;
  say('');
  fitSize();
  let kept = null;
  try { kept = JSON.parse(load(sessionStorage, camKey) || 'null'); } catch (e) {}
  const start = +(new URL(location.href).searchParams.get('cam') || 0) - 1;
  if (cameras[start] && !(kept && kept.c === start)) {
    mark(start);
    preset(start);
  } else if (kept && kept.p && kept.t) {
    if (cameras[kept.c]) mark(kept.c);
    camera.fov = kept.f || 45;
    camera.position.fromArray(kept.p);
    controls.target.fromArray(kept.t);
    controls.update();
  } else {
    fit();
  }
  // The user's setting (data-edges), or without an account this browser's.
  const own = document.body.dataset.csrf ? null : load(localStorage, 'edges3d');
  showEdges(own !== null ? own !== '0' : document.body.dataset.edges !== '0');
}, (ev) => {
  if (ev.total) say(`Loading the 3D model… ${Math.round(100 * ev.loaded / ev.total)} %`);
}, (err) => {
  say('The 3D model could not be loaded.');
  console.error(err);
});
"""


def view3d(glb_url: str, cameras: list, mb: float | None = None) -> str:
    """The 3D view of a model page: the stage, its controls and its script,
    for the model's GLB at ``glb_url`` (``mb`` big) and its saved views'
    ``cameras`` (picked in the top bar's 3D dropdown)."""
    importmap = json.dumps({"imports": {
        "three": "/static/three/three.module.min.js",
        "three/addons/": "/static/three/addons/"}})
    return (f'<style>{CSS}</style>'
            f'<figure class="one stage" id="stage" data-glb="{escape(glb_url)}" '
            f'data-cameras="{escape(json.dumps(cameras))}">'
            '<div class="canvas"><div class="status"></div></div>'
            '<figcaption><strong>3D</strong>'
            '<button class="btn" id="fit" type="button" title="Show the whole model">Fit</button>'
            '<button class="btn" id="edges" type="button" aria-pressed="true" title="Edges on or off">Edges</button>'
            '<span class="spacer"></span>'
            '<span class="muted hint">Drag: turn · Right-drag or Shift-drag: move · Wheel: zoom</span>'
            + (f'<a class="muted" href="{escape(glb_url)}" download title="Download the model as glTF binary">'
               f'GLB {mb:g} MB</a>' if mb else "")
            + '</figcaption></figure>'
            f'<script type="importmap">{importmap}</script>'
            f'<script type="module">{JS}</script>')
