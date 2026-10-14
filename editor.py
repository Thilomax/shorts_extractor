"""
Interactive editor for reviewing and adjusting highlight cuts.
Launched via the --edit flag after the main processing pipeline finishes.
"""

import os
import socket
import threading
import webbrowser
from datetime import datetime
from pathlib import Path


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


# ─────────────────────────────────────────────────────────────────────────────
# HTML / CSS / JS  (single-file SPA, no build step)
# ─────────────────────────────────────────────────────────────────────────────

EDITOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Shorts Editor</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{
  background:#141414;color:#d4d4d4;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:13px;display:flex;flex-direction:column;height:100vh;
}

/* ── Header ─────────────────────────────────────────────────────────── */
#header{
  display:flex;align-items:center;gap:8px;padding:10px 16px;
  background:#0d0d0d;border-bottom:1px solid #272727;flex-shrink:0;
}
#header h1{font-size:14px;font-weight:700;color:#fff;letter-spacing:-.2px;margin-right:2px}
#tabs{display:flex;gap:4px}
.tab-btn{
  padding:4px 12px;border-radius:4px;border:1px solid #2e2e2e;
  background:#1c1c1c;color:#777;cursor:pointer;font-size:11px;font-weight:600;
  transition:all .15s;
}
.tab-btn:hover{border-color:#484848;color:#bbb}
.tab-btn.active{background:#b83d0c;border-color:#b83d0c;color:#fff}
.spacer{flex:1}
#status-msg{
  font-size:11px;color:#555;max-width:260px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  transition:color .3s;
}
#status-msg.ok{color:#42b880}
#status-msg.err{color:#e05c2d}
.btn{
  padding:5px 14px;border-radius:5px;border:none;cursor:pointer;
  font-size:12px;font-weight:600;transition:opacity .15s;
}
.btn:hover{opacity:.8}
.btn:disabled{opacity:.35;cursor:default}
.btn-export{background:#b83d0c;color:#fff}
.btn-done{background:#232323;color:#888;border:1px solid #333}

/* ── Timeline ────────────────────────────────────────────────────────── */
#timeline-wrap{
  padding:10px 16px 8px;background:#191919;
  border-bottom:1px solid #272727;flex-shrink:0;
  user-select:none;
}
#timeline{display:block;width:100%;height:78px;border-radius:3px}
#timeline-foot{
  display:flex;align-items:center;gap:10px;margin-top:6px;
  font-size:11px;color:#555;flex-wrap:nowrap;
}
#time-display{color:#666;font-family:monospace;min-width:100px;flex-shrink:0}
.tl-ctrl{
  display:flex;align-items:center;gap:5px;cursor:pointer;
  white-space:nowrap;flex-shrink:0;
}
.tl-ctrl:hover{color:#999}
.tl-ctrl input{cursor:pointer;accent-color:#b83d0c}
.btn-sm{
  padding:3px 9px;border-radius:4px;border:1px solid #2e2e2e;
  background:#1c1c1c;color:#777;cursor:pointer;font-size:11px;font-weight:600;
  transition:all .15s;white-space:nowrap;flex-shrink:0;
}
.btn-sm:hover{border-color:#484848;color:#bbb}
.btn-add-clip{border-color:#1e4a7855;color:#4a8ec8}
.btn-add-clip:hover{border-color:#1e6ec8;color:#6ab4ff;background:#0d1520}
#zoom-info{color:#404040;font-size:10px;font-family:monospace;flex-shrink:0}
#drag-hint{color:#2e2e2e;font-size:10px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}

/* ── Main ────────────────────────────────────────────────────────────── */
#main{display:flex;flex:1;min-height:0;overflow:hidden}

/* ── Video ───────────────────────────────────────────────────────────── */
#video-wrap{
  flex:1;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  padding:16px;background:#0f0f0f;min-width:0;gap:8px;
}
#player-wrap{
  position:relative;width:100%;max-width:780px;flex-shrink:0;
}
#player{
  width:100%;display:block;
  border-radius:6px;background:#000;outline:none;
}

#video-tools{
  display:flex;gap:8px;align-items:center;flex-shrink:0;
}
.btn-preview{
  padding:5px 16px;border-radius:5px;border:1px solid #2e4a2e;
  background:#1a2a1a;color:#5a9e5a;cursor:pointer;
  font-size:12px;font-weight:600;transition:all .15s;
}
.btn-preview:hover{border-color:#3a7a3a;color:#7ec87e;background:#1e301e}
.btn-preview.active{background:#1a3a1a;border-color:#42b880;color:#42b880}

/* ── Segment panel ───────────────────────────────────────────────────── */
#seg-panel{
  width:290px;flex-shrink:0;background:#161616;
  border-left:1px solid #272727;overflow-y:auto;padding:10px;
  display:flex;flex-direction:column;
}
#seg-panel-hdr{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:8px;padding-bottom:7px;
  border-bottom:1px solid #202020;flex-shrink:0;
}
#seg-count{font-size:10px;color:#444;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.seg-card{
  border-radius:6px;border:1px solid #252525;margin-bottom:8px;
  overflow:hidden;cursor:pointer;transition:border-color .15s;flex-shrink:0;
}
.seg-card:hover{border-color:#383838}
.seg-card.looping{border-color:#b83d0c55;box-shadow:0 0 0 1px #b83d0c33}
.seg-header{
  display:flex;align-items:center;gap:5px;padding:6px 8px;
  background:#1d1d1d;border-bottom:1px solid #222;
}
.role-badge{
  padding:2px 6px;border-radius:3px;font-size:9px;
  font-weight:700;letter-spacing:.6px;text-transform:uppercase;
  cursor:pointer;transition:opacity .15s;flex-shrink:0;
}
.role-badge:hover{opacity:.65}
.seg-times{font-family:monospace;font-size:10px;color:#5a5a5a;margin-left:auto}
.seg-dur{font-family:monospace;font-size:9px;color:#3a3a3a;margin-left:3px;flex-shrink:0}
.seg-del{
  background:none;border:none;cursor:pointer;color:#2e2e2e;
  font-size:15px;line-height:1;padding:0 1px;margin-left:2px;
  transition:color .15s;flex-shrink:0;
}
.seg-del:hover{color:#b84040}
.seg-body{padding:7px 10px}
.seg-reason{
  font-size:11px;color:#505050;font-style:italic;
  margin-bottom:5px;line-height:1.4;
}
.seg-text{
  font-size:11px;color:#6a6a6a;line-height:1.5;
  max-height:64px;overflow:hidden;position:relative;
}
.seg-text::after{
  content:'';position:absolute;bottom:0;left:0;right:0;height:18px;
  background:linear-gradient(transparent,#161616);
}
.seg-hint{font-size:10px;color:#2c2c2c;margin-top:4px}
</style>
</head>
<body>

<div id="header">
  <h1>Shorts Editor</h1>
  <div id="tabs"></div>
  <div class="spacer"></div>
  <span id="status-msg"></span>
  <button class="btn btn-export" id="export-btn" onclick="doExport()">Export</button>
  <button class="btn btn-done" onclick="doDone()">Done</button>
</div>

<div id="timeline-wrap">
  <canvas id="timeline"></canvas>
  <div id="timeline-foot">
    <span id="time-display">0:00.0 / 0:00</span>

    <label class="tl-ctrl" title="Loop the currently selected segment">
      <input type="checkbox" id="loop-cb" onchange="onLoopToggle()">Loop
    </label>

    <label class="tl-ctrl" title="Snap clip edges to transcript word boundaries. Turn off for frame-precise control.">
      <input type="checkbox" id="snap-cb" checked onchange="snapEnabled=this.checked;dirty=true">Snap
    </label>

    <button class="btn-sm" onclick="resetZoom()" title="Zoom out to show full video">Fit</button>
    <span id="zoom-info">1×</span>

    <button class="btn-sm btn-add-clip" onclick="addClip()" title="Add a new clip starting at the current playhead position">+ Clip</button>

    <span id="drag-hint">Scroll = zoom &bull; Drag = pan &bull; Click = seek &bull; Drag handles to trim</span>
  </div>
</div>

<div id="main">
  <div id="video-wrap">
    <div id="player-wrap">
      <video id="player" controls preload="auto">
        <source src="/video" type="video/mp4">
      </video>
    </div>
    <div id="video-tools">
      <button class="btn-preview" id="preview-btn" onclick="togglePreview()" title="Play only the selected clips in sequence, skipping gaps">&#9654; Preview</button>
    </div>
  </div>
  <div id="seg-panel">
    <div id="seg-panel-hdr">
      <span id="seg-count">0 clips</span>
    </div>
    <div id="seg-list"></div>
  </div>
</div>

<script>
'use strict';

// ── Constants ─────────────────────────────────────────────────────────────────
const ROLE_COL = {
  grab:       '#c0440e',
  anchor:     '#1e6ec8',
  payoff:     '#1a9458',
  // legacy names
  hook:       '#c0440e',
  build:      '#1e6ec8',
  resolution: '#1a9458',
};
const ROLES_CYCLE = ['grab', 'anchor', 'payoff', 'custom'];
function roleCol(r){ return ROLE_COL[r] || '#7040c0'; }

// Timeline canvas geometry
const RULER_Y = 2,  RULER_H = 13;  // time ruler strip
const TX_Y    = 17, TX_H   = 16;   // transcript blocks
const SEL_Y   = 37, SEL_H  = 28;   // selected-clip blocks
const HANDLE_R = 7;
const HIT_R    = 14;                // px hit radius for handles
const SNAP_PX  = 18;                // px radius within which snap activates

// ── State ─────────────────────────────────────────────────────────────────────
let session     = null;
let transcript  = [];
let vidDuration = 0;
let txBounds    = [];
let variants    = [];
let curVariant  = 0;
let drag        = null;   // {segIdx, side:'start'|'end'}
let panDrag     = null;   // {startX, zs0, ze0, moved}
let hoverHandle = null;
let loopSeg     = null;
let loopOn      = false;
let dirty       = true;
let snapEnabled = true;
let previewMode = false;

// Zoom  (zoomStart/zoomEnd are times in seconds of the visible window)
let zoomStart = 0;
let zoomEnd   = 0;  // initialised to vidDuration in init()

function visDur(){ return Math.max(0.001, zoomEnd - zoomStart); }

const canvas = document.getElementById('timeline');
const ctx    = canvas.getContext('2d');
const video  = document.getElementById('player');

// ── Geometry (zoom-aware) ─────────────────────────────────────────────────────
function toPx(t){ return ((t - zoomStart) / visDur()) * canvas.width; }
function toTime(px){ return (px / canvas.width) * visDur() + zoomStart; }

// ── Zoom ──────────────────────────────────────────────────────────────────────
function resetZoom(){
  zoomStart = 0; zoomEnd = vidDuration;
  _updateZoomInfo(); dirty = true;
}

function _updateZoomInfo(){
  const z = vidDuration / visDur();
  document.getElementById('zoom-info').textContent =
    z < 1.05 ? '1\u00d7' : z.toFixed(1) + '\u00d7';
}

canvas.addEventListener('wheel', e => {
  if(!session) return;
  e.preventDefault();
  const rect  = canvas.getBoundingClientRect();
  const mx    = e.clientX - rect.left;
  const pivot = toTime(mx);
  const frac  = mx / canvas.width;

  const factor = e.deltaY > 0 ? 1.25 : 0.8;
  let newDur = Math.max(1, Math.min(vidDuration, visDur() * factor));

  let ns = pivot - frac * newDur;
  let ne = ns + newDur;
  if(ns < 0){ ns = 0; ne = newDur; }
  if(ne > vidDuration){ ne = vidDuration; ns = vidDuration - newDur; }

  zoomStart = Math.max(0, ns);
  zoomEnd   = Math.min(vidDuration, ne);
  _updateZoomInfo();
  dirty = true;
}, { passive: false });

// ── Transcript boundary snapping ──────────────────────────────────────────────
function buildBounds(){
  const s = new Set();
  for(const seg of transcript){ s.add(seg.start); s.add(seg.end); }
  txBounds = [...s].sort((a,b) => a - b);
}

function snapTime(t, lo, hi){
  // Always clamp to the allowed range first
  t = Math.max(lo, Math.min(hi, t));
  if(!snapEnabled) return t;

  // Only snap when the cursor is within SNAP_PX pixels of a boundary
  const snapThresh = visDur() * SNAP_PX / canvas.width;
  let best = null, bd = Infinity;
  for(const b of txBounds){
    if(b < lo || b > hi) continue;
    const d = Math.abs(b - t);
    if(d < bd && d <= snapThresh){ bd = d; best = b; }
  }
  return best !== null ? best : t;
}

// ── Time ruler ────────────────────────────────────────────────────────────────
const TICK_INTERVALS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

function drawRuler(){
  const W = canvas.width;
  ctx.fillStyle = '#191919';
  ctx.fillRect(0, RULER_Y, W, RULER_H);

  // Pick interval that gives at least 50px between ticks
  let iv = TICK_INTERVALS[TICK_INTERVALS.length - 1];
  for(const t of TICK_INTERVALS){
    if(W / (visDur() / t) >= 50){ iv = t; break; }
  }

  ctx.lineWidth = 1;
  ctx.textAlign = 'left';
  ctx.font = '9px monospace';

  const first = Math.ceil(zoomStart / iv) * iv;
  for(let t = first; t <= zoomEnd + iv * 0.1; t = Math.round((t + iv) * 1e6) / 1e6){
    const x = Math.round(toPx(t));
    if(x < 0 || x > W) continue;

    // Tick mark
    ctx.strokeStyle = '#383838';
    ctx.beginPath();
    ctx.moveTo(x + 0.5, RULER_Y + RULER_H - 5);
    ctx.lineTo(x + 0.5, RULER_Y + RULER_H);
    ctx.stroke();

    // Label (skip if too close to edges)
    if(x > 4 && x < W - 20){
      ctx.fillStyle = '#4a4a4a';
      ctx.fillText(fmtRuler(t, visDur()), x + 3, RULER_Y + RULER_H - 4);
    }
  }
}

function fmtRuler(s, vd){
  if(!isFinite(s)) return '';
  const m = Math.floor(s / 60);
  if(vd < 8){
    return m + ':' + (s % 60).toFixed(1).padStart(4, '0');
  }
  return m + ':' + String(Math.floor(s % 60)).padStart(2, '0');
}

// ── Rendering ─────────────────────────────────────────────────────────────────
function render(){
  if(!session) return;
  const W = canvas.width, H = canvas.height;
  const segs = variants[curVariant];

  ctx.fillStyle = '#1e1e1e';
  ctx.fillRect(0, 0, W, H);

  drawRuler();

  // Transcript blocks (gray)
  ctx.fillStyle = '#2c2c2c';
  for(const s of transcript){
    const x1 = toPx(s.start), x2 = toPx(s.end);
    if(x2 < 0 || x1 > W) continue;
    const cx1 = Math.max(0, x1), cx2 = Math.min(W, x2);
    ctx.fillRect(cx1, TX_Y, Math.max(1, cx2 - cx1 - 1), TX_H);
  }

  // Selected clips
  for(let i = 0; i < segs.length; i++){
    const seg = segs[i];
    const x1 = toPx(seg.start_seconds);
    const x2 = toPx(seg.end_seconds);
    if(x2 < 0 || x1 > W) continue;
    const w   = Math.max(2, x2 - x1);
    const col = roleCol(seg.role);

    // Tint on transcript track
    ctx.fillStyle = col + '44';
    const ox1 = Math.max(0, x1), ox2 = Math.min(W, x2);
    ctx.fillRect(ox1, TX_Y, Math.max(1, ox2 - ox1), TX_H);

    // Main clip bar
    ctx.fillStyle = col + 'cc';
    ctx.fillRect(x1, SEL_Y, w, SEL_H);

    // Role label inside bar
    const lbl = (seg.role || 'clip').toUpperCase();
    ctx.font = '600 9px -apple-system,sans-serif';
    const lw = ctx.measureText(lbl).width;
    if(w > lw + 12){
      ctx.fillStyle = '#ffffffbb';
      ctx.fillText(lbl, x1 + (w - lw) / 2, SEL_Y + SEL_H / 2 + 4);
    }

    drawHandle(x1, i, 'start', col);
    drawHandle(x2, i, 'end',   col);
  }

  // Playhead
  const ph = toPx(video.currentTime);
  if(ph >= 0 && ph <= W){
    ctx.strokeStyle = '#ffffffaa';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(ph, 0);
    ctx.lineTo(ph, H);
    ctx.stroke();
  }
}

function drawHandle(x, segIdx, side, col){
  const isHov = hoverHandle && hoverHandle.segIdx === segIdx && hoverHandle.side === side;
  const isDrg = drag        && drag.segIdx        === segIdx && drag.side        === side;
  const r = (isHov || isDrg) ? HANDLE_R + 3 : HANDLE_R;

  ctx.strokeStyle = isDrg ? '#fff' : col + 'cc';
  ctx.lineWidth   = isDrg ? 2 : 1.5;
  ctx.beginPath();
  ctx.moveTo(x, TX_Y);
  ctx.lineTo(x, SEL_Y + SEL_H);
  ctx.stroke();

  const cy = SEL_Y + SEL_H / 2;
  ctx.beginPath();
  ctx.arc(x, cy, r, 0, Math.PI * 2);
  ctx.fillStyle = isDrg ? '#fff' : col;
  ctx.fill();
}

function animate(){
  if(dirty || !video.paused){ render(); dirty = false; }
  requestAnimationFrame(animate);
}

function resizeCanvas(){
  canvas.width  = canvas.offsetWidth;
  canvas.height = canvas.offsetHeight;
  dirty = true;
}
window.addEventListener('resize', resizeCanvas);

// ── Hit-testing ───────────────────────────────────────────────────────────────
function hitTest(mx){
  if(!session) return null;
  const segs = variants[curVariant];
  // Test in reverse so top-drawn handles (higher index) get priority
  for(let i = segs.length - 1; i >= 0; i--){
    const seg = segs[i];
    if(Math.abs(mx - toPx(seg.start_seconds)) <= HIT_R) return {segIdx:i, side:'start'};
    if(Math.abs(mx - toPx(seg.end_seconds))   <= HIT_R) return {segIdx:i, side:'end'};
  }
  return null;
}

// ── Mouse: drag handles + pan + seek ─────────────────────────────────────────
canvas.addEventListener('mousedown', e => {
  if(!session) return;
  e.preventDefault();
  const mx  = e.clientX - canvas.getBoundingClientRect().left;
  const hit = hitTest(mx);
  if(hit){
    drag = hit;
    canvas.style.cursor = 'ew-resize';
  } else {
    panDrag = { startX: mx, zs0: zoomStart, ze0: zoomEnd, moved: false };
    canvas.style.cursor = visDur() < vidDuration ? 'grab' : 'pointer';
  }
});

canvas.addEventListener('mousemove', e => {
  if(!session) return;
  const mx = e.clientX - canvas.getBoundingClientRect().left;

  if(drag){
    const segs = variants[curVariant];
    const seg  = segs[drag.segIdx];
    const MIN_DUR = 0.05;
    if(drag.side === 'start'){
      const lo = drag.segIdx > 0 ? segs[drag.segIdx - 1].end_seconds : 0;
      const hi = seg.end_seconds - MIN_DUR;
      seg.start_seconds = snapTime(toTime(mx), lo, hi);
    } else {
      const lo = seg.start_seconds + MIN_DUR;
      const hi = drag.segIdx < segs.length - 1
        ? segs[drag.segIdx + 1].start_seconds
        : vidDuration;
      seg.end_seconds = snapTime(toTime(mx), lo, hi);
    }
    updateSegPanel();
    dirty = true;

  } else if(panDrag){
    const dx = mx - panDrag.startX;
    if(Math.abs(dx) > 3) panDrag.moved = true;
    if(panDrag.moved){
      const dur = panDrag.ze0 - panDrag.zs0;
      const dt  = -(dx / canvas.width) * dur;
      let ns = panDrag.zs0 + dt;
      let ne = panDrag.ze0 + dt;
      if(ns < 0){ ns = 0; ne = dur; }
      if(ne > vidDuration){ ne = vidDuration; ns = vidDuration - dur; }
      zoomStart = ns; zoomEnd = ne;
      _updateZoomInfo();
      canvas.style.cursor = 'grabbing';
      dirty = true;
    }

  } else {
    const hit = hitTest(mx);
    if(JSON.stringify(hit) !== JSON.stringify(hoverHandle)){
      hoverHandle = hit; dirty = true;
    }
    canvas.style.cursor = hit ? 'ew-resize' : (visDur() < vidDuration ? 'grab' : 'pointer');
  }
});

window.addEventListener('mouseup', e => {
  if(drag){ drag = null; dirty = true; }
  if(panDrag){
    if(!panDrag.moved){
      // Treat as a click → seek
      const rect = canvas.getBoundingClientRect();
      const t    = toTime(e.clientX - rect.left);
      video.currentTime = Math.max(0, Math.min(vidDuration, t));
    }
    panDrag = null;
    canvas.style.cursor = 'pointer';
    dirty = true;
  }
});

canvas.addEventListener('mouseleave', () => {
  if(hoverHandle){ hoverHandle = null; dirty = true; }
});

// ── Video events ──────────────────────────────────────────────────────────────
video.addEventListener('timeupdate', () => {
  const cur = video.currentTime;
  const tot = video.duration || vidDuration;
  document.getElementById('time-display').textContent = fmtD(cur) + ' / ' + fmt(tot);

  if(previewMode){
    _runPreviewLogic(cur);
    return;  // preview takes exclusive control
  }

  if(loopOn && loopSeg !== null){
    const seg = variants[curVariant][loopSeg];
    if(seg && cur >= seg.end_seconds - 0.12)
      video.currentTime = seg.start_seconds;
  }
});

video.addEventListener('pause', () => {
  // If user manually pauses, stop preview mode
  if(previewMode) _stopPreview();
});

video.addEventListener('loadedmetadata', () => {
  document.getElementById('time-display').textContent = '0:00.0 / ' + fmt(video.duration);
});

// format without decimals (totals / ruler)
function fmt(s){
  if(!isFinite(s)) return '0:00';
  const m = Math.floor(s / 60);
  return m + ':' + String(Math.floor(s % 60)).padStart(2, '0');
}
// format with one decimal place (current position, clip times)
function fmtD(s){
  if(!isFinite(s)) return '0:00.0';
  const m = Math.floor(s / 60);
  return m + ':' + (s % 60).toFixed(1).padStart(4, '0');
}

// ── Loop ──────────────────────────────────────────────────────────────────────
function onLoopToggle(){
  loopOn = document.getElementById('loop-cb').checked;
  if(!loopOn) loopSeg = null;
  updateSegPanel();
}

function activateLoop(idx){
  loopSeg = idx; loopOn = true;
  document.getElementById('loop-cb').checked = true;
  const seg = variants[curVariant][idx];
  video.currentTime = seg.start_seconds;
  video.play().catch(()=>{});
  updateSegPanel();
}

// ── Preview playback ──────────────────────────────────────────────────────────
function _previewSegs(){
  // Sorted copy of current variant's clips
  return [...variants[curVariant]].sort((a, b) => a.start_seconds - b.start_seconds);
}

function _runPreviewLogic(cur){
  const segs = _previewSegs();
  if(!segs.length){ _stopPreview(); return; }

  // Find which segment the playhead is currently inside
  const idx = segs.findIndex(s => cur >= s.start_seconds && cur < s.end_seconds);

  if(idx !== -1){
    // Inside a clip — jump to next clip when within 0.1s of its end
    if(cur >= segs[idx].end_seconds - 0.1){
      if(idx + 1 < segs.length){
        video.currentTime = segs[idx + 1].start_seconds;
      } else {
        // Last clip finished
        _stopPreview();
      }
    }
  } else {
    // In a gap — jump to the next clip that starts after current time
    const next = segs.find(s => s.start_seconds > cur + 0.05);
    if(next){
      video.currentTime = next.start_seconds;
    } else {
      _stopPreview();
    }
  }
}

function togglePreview(){
  if(previewMode) _stopPreview();
  else _startPreview();
}

function _startPreview(){
  const segs = _previewSegs();
  if(!segs.length) return;
  // Cancel loop mode so preview has exclusive control
  loopOn = false; loopSeg = null;
  document.getElementById('loop-cb').checked = false;
  previewMode = true;
  document.getElementById('preview-btn').textContent = '\u25a0 Stop';
  document.getElementById('preview-btn').classList.add('active');
  video.currentTime = segs[0].start_seconds;
  video.play().catch(() => {});
}

function _stopPreview(){
  previewMode = false;
  video.pause();
  document.getElementById('preview-btn').textContent = '\u25b6 Preview';
  document.getElementById('preview-btn').classList.remove('active');
}


// ── Add / Delete / Cycle clips ────────────────────────────────────────────────
function addClip(){
  const segs = variants[curVariant];
  let start  = video.currentTime;
  let end    = Math.min(start + 5, vidDuration);

  // Nudge forward until there's no overlap
  for(let tries = 0; tries < 200; tries++){
    const overlap = segs.some(s => start < s.end_seconds && end > s.start_seconds);
    if(!overlap) break;
    start += 0.5;
    end    = Math.min(start + 5, vidDuration);
    if(end >= vidDuration){ start = 0; end = Math.min(5, vidDuration); break; }
  }

  const newSeg = { start_seconds: start, end_seconds: end, role: 'custom', reason: 'Manually added' };
  segs.push(newSeg);
  segs.sort((a, b) => a.start_seconds - b.start_seconds);
  updateSegPanel();
  dirty = true;
}

function deleteClip(idx){
  const segs = variants[curVariant];
  segs.splice(idx, 1);
  if(loopSeg !== null){
    if(loopSeg === idx){
      loopSeg = null; loopOn = false;
      document.getElementById('loop-cb').checked = false;
    } else if(loopSeg > idx){
      loopSeg--;
    }
  }
  updateSegPanel();
  dirty = true;
}

function cycleRole(idx){
  const seg = variants[curVariant][idx];
  const i   = ROLES_CYCLE.indexOf(seg.role);
  seg.role  = ROLES_CYCLE[(i + 1) % ROLES_CYCLE.length];
  updateSegPanel();
  dirty = true;
}

// ── Segment panel ─────────────────────────────────────────────────────────────
function txText(t0, t1){
  return transcript
    .filter(s => s.start < t1 && s.end > t0)
    .map(s => s.text.trim())
    .join(' ');
}

function updateSegPanel(){
  const list = document.getElementById('seg-list');
  const segs = variants[curVariant];
  list.innerHTML = '';

  document.getElementById('seg-count').textContent =
    segs.length + ' clip' + (segs.length !== 1 ? 's' : '');

  for(let i = 0; i < segs.length; i++){
    const seg = segs[i];
    const col = roleCol(seg.role);
    const dur = seg.end_seconds - seg.start_seconds;
    const txt = txText(seg.start_seconds, seg.end_seconds);
    const isLooping = loopOn && loopSeg === i;

    const card = document.createElement('div');
    card.className = 'seg-card' + (isLooping ? ' looping' : '');
    card.innerHTML = `
      <div class="seg-header">
        <span class="role-badge"
          style="background:${col}22;color:${col}"
          title="Click to cycle role"
          data-idx="${i}">
          ${esc((seg.role || 'clip').toUpperCase())}
        </span>
        <span class="seg-times">${fmtD(seg.start_seconds)}&nbsp;&rarr;&nbsp;${fmtD(seg.end_seconds)}</span>
        <span class="seg-dur">${dur.toFixed(2)}s</span>
        <button class="seg-del" title="Delete clip" data-del="${i}">&times;</button>
      </div>
      <div class="seg-body">
        ${seg.reason ? `<div class="seg-reason">${esc(seg.reason)}</div>` : ''}
        <div class="seg-text">${esc(txt)}</div>
        <div class="seg-hint">Double-click to loop &bull; Click role badge to cycle</div>
      </div>`;

    // Bind events (use data-idx to avoid closure-over-loop issue)
    card.addEventListener('click', () => { video.currentTime = segs[i].start_seconds; });
    card.addEventListener('dblclick', e => { e.stopPropagation(); activateLoop(i); });
    card.querySelector('.role-badge').addEventListener('click', e => {
      e.stopPropagation(); cycleRole(parseInt(e.currentTarget.dataset.idx));
    });
    card.querySelector('.seg-del').addEventListener('click', e => {
      e.stopPropagation(); deleteClip(parseInt(e.currentTarget.dataset.del));
    });

    list.appendChild(card);
  }
}

function esc(s){
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Variant tabs ──────────────────────────────────────────────────────────────
function buildTabs(){
  const el = document.getElementById('tabs');
  el.innerHTML = '';
  if(variants.length < 2) return;
  variants.forEach((_, i) => {
    const b = document.createElement('button');
    b.className = 'tab-btn' + (i === 0 ? ' active' : '');
    b.textContent = 'V' + (i + 1);
    b.onclick = () => switchVariant(i);
    el.appendChild(b);
  });
}

function switchVariant(idx){
  curVariant = idx;
  loopSeg = null; loopOn = false;
  document.getElementById('loop-cb').checked = false;
  if(previewMode) _stopPreview();
  document.querySelectorAll('.tab-btn').forEach((b, i) => b.classList.toggle('active', i === idx));
  updateSegPanel();
  dirty = true;
}

// ── Export / Done ─────────────────────────────────────────────────────────────
async function doExport(){
  const btn = document.getElementById('export-btn');
  const msg = document.getElementById('status-msg');
  btn.disabled = true;
  msg.className = '';
  msg.textContent = 'Exporting\u2026';
  try{
    const res  = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ variant_index: curVariant, segments: variants[curVariant] }),
    });
    const data = await res.json();
    if(data.success){
      const fname = data.file.replace(/\\/g, '/').split('/').pop();
      msg.className = 'ok';
      msg.textContent = '\u2713 ' + fname;
    } else {
      msg.className = 'err';
      msg.textContent = 'Export failed: ' + data.error;
    }
  } catch(err){
    msg.className = 'err';
    msg.textContent = 'Error: ' + err.message;
  }
  btn.disabled = false;
}

async function doDone(){
  await fetch('/api/shutdown', { method: 'POST' }).catch(() => {});
  window.close();
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init(){
  const res   = await fetch('/api/session');
  session     = await res.json();
  transcript  = session.transcript;
  vidDuration = session.video_duration;
  zoomStart   = 0;
  zoomEnd     = vidDuration;

  variants = session.variants.map(v => v.map(s => ({ ...s })));

  buildBounds();
  buildTabs();
  resizeCanvas();
  updateSegPanel();
  dirty = true;
}

init().then(() => requestAnimationFrame(animate));
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Flask server
# ─────────────────────────────────────────────────────────────────────────────

def launch_editor(
    video_path: str,
    transcript_segments: list,
    all_variants: list,
    output_dir: Path,
    export_fn,
) -> None:
    """
    Start a local web editor for reviewing / adjusting highlight cuts.
    Opens the browser automatically. Blocks until the user clicks Done or Ctrl+C.

    Parameters
    ----------
    video_path          : path to the original source video (served for in-browser playback)
    transcript_segments : Whisper segments  [{"start", "end", "text"}, ...]
    all_variants        : list[list[seg]]  — one inner list per variant
    output_dir          : directory where re-exported files will be saved
    export_fn           : callable matching extract_highlights.export_variant's signature
    """
    try:
        from flask import Flask, request, Response, jsonify
    except ImportError:
        print(
            "\n[ERROR] Flask is required for --edit mode.\n"
            "        Install it with:  pip install flask\n"
        )
        return

    import logging as _logging
    import re as _re

    port = _find_free_port()
    file_size = os.path.getsize(video_path)

    app = Flask(__name__, static_folder=None)
    _logging.getLogger('werkzeug').setLevel(_logging.ERROR)
    app.logger.setLevel(_logging.ERROR)

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.route('/')
    def index():
        return EDITOR_HTML, 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.route('/video')
    def serve_video():
        """Serve the source video with HTTP Range support so the browser can seek."""
        rng = request.headers.get('Range')

        if not rng:
            def _full():
                with open(video_path, 'rb') as fh:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk:
                            break
                        yield chunk
            return Response(
                _full(), 200, mimetype='video/mp4',
                headers={'Accept-Ranges': 'bytes', 'Content-Length': str(file_size)},
            )

        m = _re.match(r'bytes=(\d+)-(\d*)', rng)
        if not m:
            return Response('Bad Range', 400)

        b1 = int(m.group(1))
        b2 = int(m.group(2)) if m.group(2) else file_size - 1
        length = b2 - b1 + 1

        def _partial():
            with open(video_path, 'rb') as fh:
                fh.seek(b1)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return Response(
            _partial(), 206, mimetype='video/mp4',
            headers={
                'Content-Range': f'bytes {b1}-{b2}/{file_size}',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(length),
            },
        )

    @app.route('/api/session')
    def get_session():
        return jsonify({
            'transcript':     transcript_segments,
            'variants':       all_variants,
            'video_duration': transcript_segments[-1]['end'] if transcript_segments else 0,
        })

    @app.route('/api/export', methods=['POST'])
    def do_export():
        data     = request.get_json()
        v_idx    = int(data.get('variant_index', 0))
        segments = data.get('segments', [])

        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        suffix   = f'_v{v_idx + 1}' if len(all_variants) > 1 else ''
        out_name = f'highlight_{ts}{suffix}_edited.mp4'
        output_file = str(output_dir / out_name)

        try:
            export_fn(
                video_path, segments, transcript_segments,
                output_file, f'v{v_idx + 1}_edited',
            )
            return jsonify({'success': True, 'file': output_file})
        except Exception as exc:
            return jsonify({'success': False, 'error': str(exc)}), 500

    @app.route('/api/shutdown', methods=['POST'])
    def shutdown():
        threading.Timer(0.4, lambda: os._exit(0)).start()
        return jsonify({'ok': True})

    # ── Open browser after a short delay ──────────────────────────────────────

    def _open_browser():
        import time
        time.sleep(0.9)
        webbrowser.open(f'http://localhost:{port}')

    threading.Thread(target=_open_browser, daemon=True).start()

    print(f'\n[EDITOR] Highlight editor  →  http://localhost:{port}')
    print(f'         Scroll to zoom · Drag to pan · Click to seek · Drag handles to trim\n')

    try:
        app.run(host='localhost', port=port, debug=False,
                use_reloader=False, threaded=True)
    except (KeyboardInterrupt, SystemExit):
        pass
