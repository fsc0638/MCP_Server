(function () {
  "use strict";

  /* ================================================================
     AgentK Workflow Designer
     Vanilla JS — FlowDesigner (canvas) + WorkflowDashboard (right)
     ================================================================ */

  // ── Block Definitions (AgentK Skills) ──────────────────────────
  const BLOCK_DEFS = {
    start:                         { label: "開始",       icon: "▶",  color: "#34a853", category: "control" },
    end:                           { label: "結束",       icon: "⏹",  color: "#ea4335", category: "control" },
    branch:                        { label: "條件分支",    icon: "⑂",  color: "#00897b", category: "control" },
    "web-search":                  { label: "網路搜尋",    icon: "🔍", color: "#4285f4", category: "search" },
    "python-executor":             { label: "Python 執行", icon: "🐍", color: "#306998", category: "compute" },
    "image-generator":             { label: "圖像生成",    icon: "🖼", color: "#ad1457", category: "compute" },
    "schedule-manager":            { label: "排程管理",    icon: "📅", color: "#f5a623", category: "automation" },
    "google-calendar":             { label: "Google 日曆", icon: "📆", color: "#4285f4", category: "automation" },
    "groovenauts-meeting-analyst": { label: "會議分析",    icon: "🎙", color: "#6200ea", category: "analysis" },
    "gai-worksheet-facilitator":   { label: "GAI 學習單",  icon: "📊", color: "#1565c0", category: "analysis" },
  };

  const CATEGORIES = {
    control:    { label: "控制",   color: "#34a853" },
    search:     { label: "搜尋",   color: "#4285f4" },
    compute:    { label: "運算",   color: "#306998" },
    automation: { label: "自動化", color: "#f5a623" },
    analysis:   { label: "分析",   color: "#6200ea" },
  };

  const GRID = 10, GRID_L = 40;  // Small grid 10px, large grid 40px (4x4=16 small cells)
  const BLOCK_W = GRID * 12, BLOCK_H = GRID * 8;  // 120×80px = 12×8 small cells
  const snap = v => Math.round(v / GRID) * GRID;
  const snapL = v => Math.round(v / GRID_L) * GRID_L;

  // ── FlowDesigner ──────────────────────────────────────────────
  class FlowDesigner {
    constructor(surfaceEl, svgEl, viewportEl) {
      this.surface = surfaceEl;
      this.svg = svgEl;
      this.viewport = viewportEl;
      this.blocks = new Map();
      this.connections = [];
      this.nextId = 1;
      this.nextConnId = 1;
      this.selectedId = null;
      this.scale = 1;
      this.dragging = null;   // {id, ox, oy}
      this.connecting = null; // {fromId, tempPath}
      this._setup();
    }

    _setup() {
      // Arrow marker (use createElementNS for SVG compatibility)
      const NS = "http://www.w3.org/2000/svg";
      let defs = this.svg.querySelector("defs");
      if (!defs) { defs = document.createElementNS(NS, "defs"); this.svg.appendChild(defs); }
      if (!defs.querySelector("#wf-arrow")) {
        const marker = document.createElementNS(NS, "marker");
        marker.setAttribute("id", "wf-arrow");
        marker.setAttribute("viewBox", "0 0 10 10");
        marker.setAttribute("refX", "10"); marker.setAttribute("refY", "5");
        marker.setAttribute("markerWidth", "5"); marker.setAttribute("markerHeight", "5");
        marker.setAttribute("orient", "auto-start-reverse");
        const arrowPath = document.createElementNS(NS, "path");
        arrowPath.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
        arrowPath.setAttribute("fill", "#94A3B8");
        marker.appendChild(arrowPath);
        defs.appendChild(marker);
      }

      // Drop from palette
      this.surface.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
      this.surface.addEventListener("drop", e => this._onDrop(e));

      // Mouse events for drag + connect
      document.addEventListener("mousemove", e => this._onMouseMove(e));
      document.addEventListener("mouseup", e => this._onMouseUp(e));

      // Deselect on canvas click
      this.surface.addEventListener("mousedown", e => {
        if (e.target === this.surface) this.select(null);
      });

      // Delete key
      document.addEventListener("keydown", e => {
        if (e.key === "Delete" && this.selectedId != null) this.deleteBlock(this.selectedId);
      });

      // Zoom
      this.viewport.addEventListener("wheel", e => {
        if (e.ctrlKey || e.metaKey) { e.preventDefault(); this.zoom(e.deltaY > 0 ? -0.1 : 0.1); }
      }, { passive: false });
    }

    // ── Block CRUD ────────────────────────────────────────────
    addBlock(type, x, y, label) {
      const def = BLOCK_DEFS[type];
      if (!def) return null;
      const id = this.nextId++;
      const el = this._createBlockEl(id, type, def, label);
      el.style.left = snap(x) + "px";
      el.style.top = snap(y) + "px";
      this.surface.appendChild(el);
      const block = { id, type, x: snap(x), y: snap(y), label: label || def.label, el };
      this.blocks.set(id, block);
      this._updateInfo();
      return block;
    }

    _createBlockEl(id, type, def, label) {
      const el = document.createElement("div");
      el.className = "wf-block";
      el.dataset.id = id;
      el.innerHTML = `
        <div class="wf-block-header" style="background:${def.color}">
          <span>${def.icon}</span> <span>${label || def.label}</span>
        </div>
        <div class="wf-block-body">
          <span>${CATEGORIES[def.category]?.label || def.category}</span>
          <span class="wf-block-status"></span>
        </div>
        ${type !== "start" ? '<div class="wf-port wf-port-left" data-port="in" data-side="left"></div>' : ""}
        ${type !== "end" ? '<div class="wf-port wf-port-right" data-port="out" data-side="right"></div>' : ""}
        <div class="wf-port wf-port-top" data-port="in" data-side="top"></div>
        <div class="wf-port wf-port-bottom" data-port="out" data-side="bottom"></div>
      `;

      // Block mousedown → start drag
      el.addEventListener("mousedown", e => {
        if (e.target.classList.contains("wf-port")) return;
        e.stopPropagation();
        this.select(id);
        this.dragging = { id, ox: e.clientX, oy: e.clientY, sx: parseInt(el.style.left), sy: parseInt(el.style.top) };
      });

      // Port mousedown → start connection
      el.querySelectorAll(".wf-port").forEach(port => {
        port.addEventListener("mousedown", e => {
          e.stopPropagation();
          if (port.dataset.port === "out") this._startConnect(id, port.dataset.side, e);
        });
        port.addEventListener("mouseup", e => {
          if (this.connecting && port.dataset.port === "in") this._finishConnect(id, port.dataset.side);
        });
      });

      return el;
    }

    deleteBlock(id) {
      const b = this.blocks.get(id);
      if (!b) return;
      b.el.remove();
      this.blocks.delete(id);
      this.connections = this.connections.filter(c => {
        if (c.from === id || c.to === id) { c.el.remove(); return false; }
        return true;
      });
      if (this.selectedId === id) this.select(null);
      this._updateInfo();
    }

    select(id) {
      this.blocks.forEach(b => b.el.classList.remove("selected"));
      this.selectedId = id;
      if (id != null) {
        const b = this.blocks.get(id);
        if (b) {
          b.el.classList.add("selected");
          showWfPropPanel(b, this);
        }
      } else {
        closeWfPropPanel();
      }
    }

    // ── Drag ──────────────────────────────────────────────────
    _onDrop(e) {
      e.preventDefault();
      const type = e.dataTransfer.getData("blockType");
      if (!type || !BLOCK_DEFS[type]) return;
      const rect = this.surface.getBoundingClientRect();
      const x = (e.clientX - rect.left) / this.scale;
      const y = (e.clientY - rect.top) / this.scale;
      this.addBlock(type, x - BLOCK_W / 2, y - BLOCK_H / 2);
    }

    _onMouseMove(e) {
      // Dragging block
      if (this.dragging) {
        const d = this.dragging;
        const dx = (e.clientX - d.ox) / this.scale;
        const dy = (e.clientY - d.oy) / this.scale;
        const b = this.blocks.get(d.id);
        if (!b) return;
        b.x = snap(d.sx + dx);
        b.y = snap(d.sy + dy);
        b.el.style.left = b.x + "px";
        b.el.style.top = b.y + "px";
        this._updateConnections(d.id);
      }
      // Drawing connection
      if (this.connecting) {
        const rect = this.surface.getBoundingClientRect();
        const mx = (e.clientX - rect.left) / this.scale;
        const my = (e.clientY - rect.top) / this.scale;
        const from = this.blocks.get(this.connecting.fromId);
        if (!from) return;
        const fp = this._getPortEdge(from, this.connecting.fromSide);
        this.connecting.tempPath.setAttribute("d", this._routePath(fp.x, fp.y, mx, my, this.connecting.fromSide, "left", this.connecting.fromId, -1));
      }
    }

    _onMouseUp(e) {
      if (this.dragging) {
        // Anti-overlap: push dragged block away if overlapping another
        const dragId = this.dragging.id;
        this._resolveOverlap(dragId);
        this.dragging = null;
        // Refresh all connections after move
        this.connections.forEach(c => this._updateConnectionPath(c));
      }
      if (this.connecting) {
        this.connecting.tempPath.remove();
        this.connecting = null;
      }
    }

    _resolveOverlap(movedId) {
      const GAP = 20; // minimum distance between blocks
      const moved = this.blocks.get(movedId);
      if (!moved) return;

      this.blocks.forEach(other => {
        if (other.id === movedId) return;
        // Check overlap
        const ol = moved.x < other.x + BLOCK_W + GAP &&
                   moved.x + BLOCK_W + GAP > other.x &&
                   moved.y < other.y + BLOCK_H + GAP &&
                   moved.y + BLOCK_H + GAP > other.y;
        if (!ol) return;

        // Find nearest non-overlapping position
        // Calculate push direction: move the dragged block to the nearest edge
        const pushRight = (other.x + BLOCK_W + GAP) - moved.x;
        const pushLeft  = moved.x - (other.x - BLOCK_W - GAP);
        const pushDown  = (other.y + BLOCK_H + GAP) - moved.y;
        const pushUp    = moved.y - (other.y - BLOCK_H - GAP);

        // Pick smallest push
        const min = Math.min(pushRight, pushLeft, pushDown, pushUp);
        if (min === pushRight)     moved.x = snap(other.x + BLOCK_W + GAP);
        else if (min === pushLeft) moved.x = snap(other.x - BLOCK_W - GAP);
        else if (min === pushDown) moved.y = snap(other.y + BLOCK_H + GAP);
        else                       moved.y = snap(other.y - BLOCK_H - GAP);

        moved.el.style.left = moved.x + "px";
        moved.el.style.top = moved.y + "px";
      });
    }

    // ── Connections ───────────────────────────────────────────
    _startConnect(fromId, fromSide, e) {
      e.stopPropagation();
      const tempPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
      tempPath.classList.add("wf-conn-temp");
      this.svg.appendChild(tempPath);
      this.connecting = { fromId, fromSide: fromSide || "right", tempPath };
    }

    _finishConnect(toId, toSide) {
      if (!this.connecting) return;
      const fromId = this.connecting.fromId;
      if (fromId === toId) return;
      if (this.connections.some(c => c.from === fromId && c.to === toId)) return;
      this._addConnection(fromId, toId, this.connecting.fromSide, toSide || "left");
    }

    _addConnection(fromId, toId, fromSide, toSide) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.classList.add("wf-conn-path");
      path.setAttribute("marker-end", "url(#wf-arrow)");
      this.svg.appendChild(path);

      const conn = {
        id: this.nextConnId++, from: fromId, to: toId,
        fromSide: fromSide || "right", toSide: toSide || "left",
        el: path,
      };
      this.connections.push(conn);
      this._updateConnectionPath(conn);

      path.addEventListener("click", () => {
        path.remove();
        this.connections = this.connections.filter(c => c.id !== conn.id);
        this._updateInfo();
      });

      this._updateInfo();
    }

    _updateConnections(blockId) {
      this.connections.forEach(c => {
        if (c.from === blockId || c.to === blockId) this._updateConnectionPath(c);
      });
    }

    _getPortEdge(block, side) {
      switch (side) {
        case "right":  return { x: block.x + BLOCK_W, y: block.y + BLOCK_H / 2 };
        case "left":   return { x: block.x,           y: block.y + BLOCK_H / 2 };
        case "bottom": return { x: block.x + BLOCK_W / 2, y: block.y + BLOCK_H };
        case "top":    return { x: block.x + BLOCK_W / 2, y: block.y };
        default:       return { x: block.x + BLOCK_W, y: block.y + BLOCK_H / 2 };
      }
    }

    _updateConnectionPath(conn) {
      const fb = this.blocks.get(conn.from);
      const tb = this.blocks.get(conn.to);
      if (!fb || !tb) return;

      const p1 = this._getPortEdge(fb, conn.fromSide || "right");
      const p2 = this._getPortEdge(tb, conn.toSide || "left");

      // Route directly from port edge to port edge
      conn.el.setAttribute("d", this._routePath(p1.x, p1.y, p2.x, p2.y, conn.fromSide, conn.toSide, conn.from, conn.to));
    }

    _routePath(x1, y1, x2, y2, fromSide, toSide, fromBlockId, toBlockId) {
      // Clean rewrite: draw.io style routing
      //
      // Step 1: From port edge, extend 1 grid cell in exit direction → "start point"
      // Step 2: From port edge, extend 1 grid cell in entry direction → "end point"
      // Step 3: Connect start→end with orthogonal segments that don't cross any block
      //
      // The extension segments (step 1 & 2) are always safe because they go
      // AWAY from the block, so they can't cross any other block that's not
      // overlapping (anti-overlap prevents that).

      const G = GRID; // 10px = 1 small grid cell

      // Extension vectors per side
      const ext = { right: [G,0], left: [-G,0], bottom: [0,G], top: [0,-G] };
      const fExt = ext[fromSide || "right"];
      const tExt = ext[toSide || "left"];

      // Extended start/end points (1 grid cell outside block)
      const sx = x1 + fExt[0], sy = y1 + fExt[1];
      const ex = x2 + tExt[0], ey = y2 + tExt[1];

      // Build block rects for collision (ALL blocks, no exceptions)
      const rects = [];
      this.blocks.forEach(b => {
        rects.push({ l: b.x, t: b.y, r: b.x + BLOCK_W, b: b.y + BLOCK_H });
      });

      // Collision checks for orthogonal segments
      const hOK = (y, xa, xb) => {
        const lo = Math.min(xa, xb), hi = Math.max(xa, xb);
        for (const r of rects) {
          if (y > r.t && y < r.b && hi > r.l && lo < r.r) return false;
        }
        return true;
      };
      const vOK = (x, ya, yb) => {
        const lo = Math.min(ya, yb), hi = Math.max(ya, yb);
        for (const r of rects) {
          if (x > r.l && x < r.r && hi > r.t && lo < r.b) return false;
        }
        return true;
      };

      // Candidate corridor positions (1 grid outside every block edge)
      const cxs = new Set();
      const cys = new Set();
      this.blocks.forEach(b => {
        cxs.add(b.x - G); cxs.add(b.x + BLOCK_W + G);
        cys.add(b.y - G); cys.add(b.y + BLOCK_H + G);
      });
      cxs.add(snap((sx + ex) / 2));
      cys.add(snap((sy + ey) / 2));

      const pathLen = (pts) => {
        let l = 0;
        for (let i = 1; i < pts.length; i++) l += Math.abs(pts[i][0]-pts[i-1][0]) + Math.abs(pts[i][1]-pts[i-1][1]);
        return l;
      };

      // Remove consecutive duplicates, then collinear intermediate points
      const simplify = (pts) => {
        if (pts.length <= 2) return pts;
        // Step 1: dedup consecutive identical points
        const d = [pts[0]];
        for (let i = 1; i < pts.length; i++) {
          if (pts[i][0] !== d[d.length-1][0] || pts[i][1] !== d[d.length-1][1]) d.push(pts[i]);
        }
        if (d.length <= 2) return d;
        // Step 2: remove collinear intermediate points
        const s = [d[0]];
        for (let i = 1; i < d.length - 1; i++) {
          const p = d[i-1], c = d[i], n = d[i+1];
          if (!((p[0]===c[0]&&c[0]===n[0]) || (p[1]===c[1]&&c[1]===n[1]))) s.push(c);
        }
        s.push(d[d.length-1]);
        return s;
      };

      // Build clean SVG path from waypoint array:
      //   [port1] → [ext1] → [mid waypoints] → [ext2] → [port2]
      // simplify() removes collinear points (e.g. ext merges into straight segments)
      const toSvg = (midPts) => {
        const all = [[x1,y1], ...midPts, [x2,y2]];
        const s = simplify(all);
        return "M " + s.map(p => `${p[0]} ${p[1]}`).join(" L ");
      };

      // Try direct connection (straight line if aligned)
      if (Math.abs(sx - ex) < 2 && vOK(sx, sy, ey)) {
        return toSvg([[sx,sy],[ex,ey]]);
      }
      if (Math.abs(sy - ey) < 2 && hOK(sy, sx, ex)) {
        return toSvg([[sx,sy],[ex,ey]]);
      }

      // Try 3-segment paths
      const results = [];

      // H-V-H: horizontal from sx → vertical → horizontal to ex
      for (const mx of [...cxs].sort((a,b) => Math.abs(a-(sx+ex)/2) - Math.abs(b-(sx+ex)/2))) {
        if (hOK(sy, sx, mx) && vOK(mx, sy, ey) && hOK(ey, mx, ex)) {
          const pts = [[sx,sy],[mx,sy],[mx,ey],[ex,ey]];
          results.push({ pts, len: pathLen(pts) });
        }
      }

      // V-H-V: vertical from sy → horizontal → vertical to ey
      for (const my of [...cys].sort((a,b) => Math.abs(a-(sy+ey)/2) - Math.abs(b-(sy+ey)/2))) {
        if (vOK(sx, sy, my) && hOK(my, sx, ex) && vOK(ex, my, ey)) {
          const pts = [[sx,sy],[sx,my],[ex,my],[ex,ey]];
          results.push({ pts, len: pathLen(pts) });
        }
      }

      // 5-segment detours
      for (const mx of [...cxs].sort((a,b) => Math.abs(a-(sx+ex)/2) - Math.abs(b-(sx+ex)/2)).slice(0,4)) {
        for (const my of [...cys].sort((a,b) => Math.abs(a-(sy+ey)/2) - Math.abs(b-(sy+ey)/2)).slice(0,4)) {
          // H-V-H-V-H
          if (hOK(sy,sx,mx) && vOK(mx,sy,my) && hOK(my,mx,ex) && vOK(ex,my,ey)) {
            const pts = [[sx,sy],[mx,sy],[mx,my],[ex,my],[ex,ey]];
            results.push({ pts, len: pathLen(pts) });
          }
          // V-H-V-H-V
          if (vOK(sx,sy,my) && hOK(my,sx,mx) && vOK(mx,my,ey) && hOK(ey,mx,ex)) {
            const pts = [[sx,sy],[sx,my],[mx,my],[mx,ey],[ex,ey]];
            results.push({ pts, len: pathLen(pts) });
          }
        }
      }

      // Pick shortest
      if (results.length > 0) {
        results.sort((a, b) => a.len - b.len);
        return toSvg(results[0].pts);
      }

      // Fallback: route via extreme outer edge
      const farR = Math.max(...[...cxs]) + G * 3;
      const farL = Math.min(...[...cxs]) - G * 3;
      const farT = Math.min(...[...cys]) - G * 3;
      const farB = Math.max(...[...cys]) + G * 3;
      const fbs = [
        { pts: [[sx,sy],[farR,sy],[farR,ey],[ex,ey]], len: 0 },
        { pts: [[sx,sy],[farL,sy],[farL,ey],[ex,ey]], len: 0 },
        { pts: [[sx,sy],[sx,farT],[ex,farT],[ex,ey]], len: 0 },
        { pts: [[sx,sy],[sx,farB],[ex,farB],[ex,ey]], len: 0 },
      ];
      fbs.forEach(f => f.len = pathLen(f.pts));
      fbs.sort((a,b) => a.len - b.len);
      return toSvg(fbs[0].pts);
    }

    // ── Reset ────────────────────────────────────────────────
    reset() {
      // Clear all blocks and connections
      this.blocks.forEach(b => b.el.remove());
      this.blocks.clear();
      this.connections.forEach(c => c.el.remove());
      this.connections = [];
      this.nextId = 1;
      this.nextConnId = 1;
      this.select(null);
      // Remove saved flow (backend + localStorage)
      fetch("/api/workflows/default", { method: "DELETE" }).catch(() => {});
      localStorage.removeItem("wf_flow_default");
      // Add default Start block
      this.addBlock("start", 200, 250);
      this.zoomFit();
      this._updateInfo();
      if (window.showToast) window.showToast("工作流已重置", "info");
    }

    // ── Zoom ─────────────────────────────────────────────────
    zoom(delta) {
      this.scale = Math.min(2, Math.max(0.3, this.scale + delta));
      this.surface.style.transform = `scale(${this.scale})`;
      const lbl = document.getElementById("wfZoomLabel");
      if (lbl) lbl.textContent = Math.round(this.scale * 100) + "%";
    }

    zoomFit() {
      this.scale = 1;
      this.surface.style.transform = "scale(1)";
      const lbl = document.getElementById("wfZoomLabel");
      if (lbl) lbl.textContent = "100%";
    }

    // ── Run Flow (Backend execution + Frontend animation) ────
    async runFlow() {
      // Save first to ensure backend has latest
      await this.save("default");

      // Start frontend animation
      const order = this._topoSort();
      for (const id of order) {
        const b = this.blocks.get(id);
        if (!b) continue;
        b.el.classList.add("running");
        b.el.querySelector(".wf-block-status")?.classList.add("running");
        this.connections.filter(c => c.to === id).forEach(c => c.el.classList.add("active-flow"));
        await sleep(300);
      }

      // Call backend execution
      try {
        const resp = await fetch("/api/workflows/default/execute", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initial_prompt: "", model: null }),
        });
        const data = await resp.json();

        // Update block statuses from results
        if (data.results) {
          data.results.forEach(r => {
            // Find block by matching type
            this.blocks.forEach(b => {
              const skillName = b.type.startsWith("mcp-") ? b.type : `mcp-${b.type}`;
              if (r.block_id === b.id || r.skill === skillName) {
                const statusEl = b.el.querySelector(".wf-block-status");
                if (statusEl) {
                  statusEl.classList.remove("running");
                  statusEl.classList.add(r.status === "success" ? "ok" : r.status === "error" ? "error" : "ok");
                }
              }
            });
          });
        }

        // Log results
        if (window._wfDashboard) {
          const success = data.results?.filter(r => r.status === "success").length || 0;
          const errors = data.results?.filter(r => r.status === "error").length || 0;
          window._wfDashboard.addLog(
            `Flow 執行完成 (${success} 成功, ${errors} 錯誤)`,
            errors > 0 ? "failed" : "success"
          );
        }

        if (window.showToast) {
          window.showToast(`執行完成：${data.blocks_executed} 個節點`, "success");
        }
      } catch (e) {
        if (window.showToast) window.showToast("執行失敗: " + e.message, "error");
        if (window._wfDashboard) window._wfDashboard.addLog("Flow 執行失敗", "failed");
      }

      // Clean up animations
      this.blocks.forEach(b => {
        b.el.classList.remove("running");
        b.el.querySelector(".wf-block-status")?.classList.remove("running");
      });
      this.connections.forEach(c => c.el.classList.remove("active-flow"));

      // Reset status after 3s
      setTimeout(() => {
        this.blocks.forEach(b => {
          const s = b.el.querySelector(".wf-block-status");
          if (s) { s.classList.remove("ok", "error"); }
        });
      }, 3000);
    }

    _topoSort() {
      const indeg = new Map();
      this.blocks.forEach((_, id) => indeg.set(id, 0));
      this.connections.forEach(c => indeg.set(c.to, (indeg.get(c.to) || 0) + 1));
      const queue = [];
      indeg.forEach((d, id) => { if (d === 0) queue.push(id); });
      const result = [];
      while (queue.length) {
        const id = queue.shift();
        result.push(id);
        this.connections.filter(c => c.from === id).forEach(c => {
          indeg.set(c.to, indeg.get(c.to) - 1);
          if (indeg.get(c.to) === 0) queue.push(c.to);
        });
      }
      return result;
    }

    _updateInfo() {
      const el = document.getElementById("wfInfoText");
      if (el) el.textContent = `${this.blocks.size} 節點 · ${this.connections.length} 連接`;
      if (window._wfDashboard) window._wfDashboard.updateStats(this.blocks.size, this.connections.length);
    }

    // ── Persistence (Backend API with localStorage fallback) ──
    async save(name) {
      const flowId = name || this._currentWfId || "default";
      const data = {
        name: flowId,
        blocks: Array.from(this.blocks.values()).map(b => ({ id: b.id, type: b.type, x: b.x, y: b.y, label: b.label })),
        connections: this.connections.map(c => ({ from: c.from, to: c.to })),
        scope: this._currentScope || "personal",
        owner: this._currentOwner || "",
      };
      // Save to backend
      try {
        const resp = await fetch(`/api/workflows/${flowId}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        if (resp.ok) {
          if (window.showToast) window.showToast("工作流已儲存", "success");
        } else {
          throw new Error("API save failed");
        }
      } catch (e) {
        // Fallback to localStorage
        localStorage.setItem("wf_flow_" + flowId, JSON.stringify(data));
        if (window.showToast) window.showToast("工作流已儲存（本地）", "success");
      }
    }

    async load(name, scope, owner) {
      const flowId = name || "default";
      let data = null;
      // Try backend first
      try {
        const _q = new URLSearchParams();
        if (scope) _q.set("scope", scope);
        if (owner) _q.set("owner", owner);
        const resp = await fetch(`/api/workflows/${flowId}?${_q}`);
        if (resp.ok) data = await resp.json();
      } catch (_) {}
      // Fallback to localStorage
      if (!data) {
        const raw = localStorage.getItem("wf_flow_" + flowId);
        if (raw) data = JSON.parse(raw);
      }
      if (!data || !data.blocks) return;

      // Clear
      this.blocks.forEach(b => b.el.remove());
      this.blocks.clear();
      this.connections.forEach(c => c.el.remove());
      this.connections = [];
      this.nextId = 1;
      this.nextConnId = 1;
      // Restore blocks
      data.blocks.forEach(b => {
        this.addBlock(b.type, b.x, b.y, b.label);
      });
      // Map old IDs to new sequential IDs
      const idMap = new Map();
      let idx = 1;
      data.blocks.forEach(b => { idMap.set(b.id, idx++); });
      // Restore connections
      (data.connections || []).forEach(c => {
        const from = idMap.get(c.from);
        const to = idMap.get(c.to);
        if (from && to) this._addConnection(from, to);
      });
    }
  }

  // ── Dashboard (matches NewsAnalysis layout) ────────────────────
  class WorkflowDashboard {
    constructor(container) {
      this.container = container;
      this.actChart = null;
      this.distChart = null;
      this._render();
      this._initCharts();
      // Live log demo every 8s
      setInterval(() => this._addRandomLog(), 8000);
    }

    _render() {
      this.container.innerHTML = `
        <div class="wf-dashboard-header">
          <span>執行監控</span>
          <button style="background:none;border:none;cursor:pointer;color:var(--text-tertiary);font-size:0.9rem;" onclick="window._wfDashboard && window._wfDashboard.refresh()">↻</button>
        </div>
        <div class="wf-dashboard-body">
          <!-- Stat Cards -->
          <div class="wf-stats-grid">
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatBlocks">0</div><div class="wf-stat-label">節點數</div></div>
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatConns" style="color:#34a853">0</div><div class="wf-stat-label">連接數</div></div>
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatRuns" style="color:#f5a623">0</div><div class="wf-stat-label">執行次數</div></div>
          </div>

          <!-- Activity Chart -->
          <div class="wf-section-title">活動趨勢</div>
          <div class="wf-chart-wrap"><canvas id="wfActivityChart"></canvas></div>

          <!-- Skill Distribution -->
          <div class="wf-section-title">技能分布</div>
          <div class="wf-chart-row">
            <div class="wf-chart-wrap wf-chart-sm"><canvas id="wfDistChart"></canvas></div>
            <div class="wf-chart-legend" id="wfDistLegend">
              <div class="wf-legend-item" style="color:var(--text-tertiary);font-style:italic;">尚無技能節點</div>
            </div>
          </div>

          <!-- Keywords -->
          <div class="wf-section-title">常用技能</div>
          <div class="wf-keyword-wrap" id="wfKeywords"></div>

          <!-- Execution Log -->
          <div class="wf-log-section-title"><span class="wf-live-dot"></span> 執行日誌</div>
          <div class="wf-log-list" id="wfLogList"></div>
        </div>
      `;
    }

    _initCharts() {
      // Load Chart.js from CDN if not present
      if (typeof Chart === "undefined") {
        const script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js";
        script.onload = () => this._buildCharts();
        document.head.appendChild(script);
      } else {
        this._buildCharts();
      }
    }

    _buildCharts() {
      // Activity line chart (daily token usage)
      const actCtx = document.getElementById("wfActivityChart");
      if (actCtx) {
        this.actChart = new Chart(actCtx, {
          type: "line",
          data: { labels: [], datasets: [{
            data: [], borderColor: "#0D6EFD", backgroundColor: "rgba(13,110,253,0.08)",
            borderWidth: 2, pointRadius: 3, pointBackgroundColor: "#0D6EFD", tension: 0.4, fill: true,
          }] },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              x: { grid: { color: "#f0f2f5" }, ticks: { font: { size: 9 }, color: "#94a3b8" } },
              y: { grid: { color: "#f0f2f5" }, ticks: { font: { size: 9 }, color: "#94a3b8", maxTicksLimit: 4 } },
            },
          },
        });
      }

      // Skill distribution doughnut (from canvas blocks, real-time)
      const distCtx = document.getElementById("wfDistChart");
      if (distCtx) {
        this.distChart = new Chart(distCtx, {
          type: "doughnut",
          data: { labels: [], datasets: [{ data: [], backgroundColor: [], borderWidth: 0, hoverOffset: 4 }] },
          options: { responsive: true, maintainAspectRatio: false, cutout: "65%", plugins: { legend: { display: false }, tooltip: { enabled: false } } },
        });
      }

      // Fetch real data from API + sync canvas state
      this._fetchStats();
      // Sync distribution chart with current canvas blocks
      setTimeout(() => this._updateDistChart(), 300);
    }

    async _fetchStats() {
      try {
        const resp = await fetch("/skills/workflow/stats");
        if (!resp.ok) return;
        const data = await resp.json();
        this._applyDailyChart(data.daily || {});
        this._applySkillKeywords(data.by_skill || {});
      } catch (e) { console.warn("[WF Dashboard] Stats fetch failed:", e); }
    }

    _applyDailyChart(daily) {
      if (!this.actChart) return;
      // Fixed 7-day window: today minus 6 days → today
      const labels = [];
      const data = [];
      const now = new Date();
      for (let i = 6; i >= 0; i--) {
        const d = new Date(now);
        d.setDate(d.getDate() - i);
        const key = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
        const label = (d.getMonth() + 1) + "/" + String(d.getDate()).padStart(2, "0");
        labels.push(label);
        const val = daily[key] ? Math.round((daily[key].total_tokens || 0) / 1000) : 0;
        data.push(val);
      }
      this.actChart.data.labels = labels;
      this.actChart.data.datasets[0].data = data;
      this.actChart.update();
    }

    _applySkillKeywords(bySkill) {
      // Full skill name → display label mapping
      const SKILL_LABELS = {
        "mcp-python-executor": "Python 執行",
        "mcp-web-search": "網路搜尋",
        "mcp-schedule-manager": "排程管理",
        "mcp-google-calendar": "Google 日曆",
        "mcp-image-generator": "圖像生成",
        "mcp-groovenauts-meeting-analyst": "會議分析",
        "mcp-groovenaust-meeting-analyst": "會議分析",
        "mcp-gai-worksheet-facilitator": "GAI 學習單",
        "mcp-txt-llm-analyzer": "TXT 分析",
        "mcp-pdf-llm-analyzer": "PDF 分析",
        "mcp-docx-llm-analyzer": "DOCX 分析",
        "mcp-spreadsheet-llm-analyzer": "試算表分析",
        "mcp-meeting-to-notion": "會議→Notion",
        "(chat)": "純對話",
      };
      // Sort skills by total_tokens descending, exclude (chat)
      const sorted = Object.entries(bySkill)
        .filter(([name]) => name !== "(chat)")
        .sort(([, a], [, b]) => (b.total_tokens || 0) - (a.total_tokens || 0));
      const wrap = document.getElementById("wfKeywords");
      if (wrap) {
        wrap.innerHTML = sorted.slice(0, 8).map(([name, stats]) => {
          const label = SKILL_LABELS[name] || name.replace("mcp-", "");
          const tokens = Math.round((stats.total_tokens || 0) / 1000);
          return `<span class="wf-keyword-tag" title="${tokens}K tokens">${label}</span>`;
        }).join("");
      }
    }

    _updateDistChart() {
      const chartWrap = document.querySelector(".wf-chart-row");
      if (!chartWrap || !window._wfDesigner) return;

      // Count blocks on canvas by type (exclude control: start/end/branch)
      const counts = {};
      const colors = {};
      window._wfDesigner.blocks.forEach(bl => {
        const def = BLOCK_DEFS[bl.type];
        if (!def || def.category === "control") return; // Skip control nodes
        const label = def.label;
        counts[label] = (counts[label] || 0) + 1;
        colors[label] = def.color;
      });
      const labels = Object.keys(counts);

      // No skill blocks → show placeholder
      const legend = document.getElementById("wfDistLegend");
      if (!labels.length) {
        if (this.distChart) {
          this.distChart.data.labels = [];
          this.distChart.data.datasets[0].data = [];
          this.distChart.update();
        }
        if (legend) legend.innerHTML = '<div class="wf-legend-item" style="color:var(--text-tertiary);font-style:italic;">尚無技能節點</div>';
        return;
      }

      if (this.distChart) {
        this.distChart.data.labels = labels;
        this.distChart.data.datasets[0].data = labels.map(l => counts[l]);
        this.distChart.data.datasets[0].backgroundColor = labels.map(l => colors[l]);
        this.distChart.update();
      }

      // Update legend with name + count + percentage
      if (legend) {
        const total = labels.reduce((s, l) => s + counts[l], 0);
        legend.innerHTML = "";
        labels.forEach(l => {
          const pct = total > 0 ? Math.round(counts[l] / total * 100) : 0;
          const item = document.createElement("div");
          item.className = "wf-legend-item";
          item.innerHTML = `<span class="wf-legend-dot" style="background:${colors[l]}"></span><span style="flex:1">${l}</span><span style="color:var(--text-tertiary);font-size:0.6rem;">${counts[l]}個 ${pct}%</span>`;
          legend.appendChild(item);
        });
      }
    }

    updateStats(blocks, conns) {
      const b = document.getElementById("wfStatBlocks");
      const c = document.getElementById("wfStatConns");
      if (b) b.textContent = blocks;
      if (c) c.textContent = conns;
      this._updateDistChart();
    }

    addLog(msg, status) {
      const list = document.getElementById("wfLogList");
      if (!list) return;
      const now = new Date();
      const time = now.toTimeString().slice(0, 8);
      const dur = Math.floor(Math.random() * 10 + 1) + "s";
      const entry = document.createElement("div");
      entry.className = "wf-log-entry";
      entry.innerHTML = `
        <span class="wf-log-dot ${status}"></span>
        <span class="wf-log-name">${msg}</span>
        <div class="wf-log-meta">
          <span class="wf-log-time">${time}</span>
          <span class="wf-log-dur">${dur}</span>
        </div>
      `;
      list.prepend(entry);
      while (list.children.length > 20) list.lastChild.remove();
      const r = document.getElementById("wfStatRuns");
      if (r) r.textContent = parseInt(r.textContent || "0") + 1;
    }

    _addRandomLog() {
      const flows = ["Skill Pipeline", "排程推播", "資料分析", "網路搜尋"];
      const statuses = ["success", "success", "running"];
      this.addLog(
        flows[Math.floor(Math.random() * flows.length)],
        statuses[Math.floor(Math.random() * statuses.length)]
      );
    }

    refresh() {
      if (window._wfDesigner) this.updateStats(window._wfDesigner.blocks.size, window._wfDesigner.connections.length);
    }
  }

  // ── Dynamic Skill Registry ─────────────────────────────────────
  // Loaded from /skills/list API, merged with BLOCK_DEFS
  let _dynamicSkills = {};  // { "mcp-web-search": {label, icon, color, category}, ... }
  let _skillsLoaded = false;

  async function _loadSkillsFromAPI() {
    if (_skillsLoaded) return;
    try {
      // Pass user context for department/personal skill filtering
      const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
      const _params = new URLSearchParams();
      if (_u.department_code) _params.set("dept", _u.department_code);
      if (_u.employee_id || _u.id) _params.set("uid", _u.employee_id || _u.id);
      const resp = await fetch("/skills/list" + (_params.toString() ? "?" + _params : ""));
      if (!resp.ok) return;
      const data = await resp.json();
      const skills = data.skills || {};
      Object.entries(skills).forEach(([registryKey, info]) => {
        // Use short_name (e.g. "mcp-test") not registry key (e.g. "dept:Y200:mcp-test")
        const skillName = info.short_name || registryKey;
        const shortName = skillName.replace("mcp-", "");
        const apiDisplayName = info.display_name || "";
        if (!BLOCK_DEFS[shortName]) {
          BLOCK_DEFS[shortName] = {
            label: apiDisplayName || _guessLabel(skillName, info.description),
            icon: _guessIcon(skillName),
            color: _guessColor(skillName),
            category: _guessCategory(skillName, info.description),
          };
        } else if (apiDisplayName) {
          BLOCK_DEFS[shortName].label = apiDisplayName;
        }
        // Key by short_name so palette and editor use clean names
        _dynamicSkills[skillName] = { ready: info.ready !== false, description: info.description || "", scope: info.scope || "system", short_name: skillName, editable: info.editable === true };
      });
      window._wfIsGuest = data.guest === true;
      _skillsLoaded = true;
    } catch (e) {
      console.warn("[WF] Failed to load skills from API:", e);
    }
  }

  function _guessLabel(name, desc) {
    const map = {
      "mcp-txt-llm-analyzer": "TXT 分析",
      "mcp-pdf-llm-analyzer": "PDF 分析",
      "mcp-docx-llm-analyzer": "DOCX 分析",
      "mcp-spreadsheet-llm-analyzer": "試算表分析",
      "mcp-meeting-to-notion": "會議→Notion",
      "mcp-high-risk-demo": "高風險示範",
    };
    return map[name] || name.replace("mcp-", "").replace(/-/g, " ");
  }
  function _guessIcon(name) {
    const map = {
      "mcp-txt-llm-analyzer": "📄", "mcp-pdf-llm-analyzer": "📕",
      "mcp-docx-llm-analyzer": "📘", "mcp-spreadsheet-llm-analyzer": "📊",
      "mcp-meeting-to-notion": "📝", "mcp-high-risk-demo": "⚠️",
    };
    return map[name] || "🔧";
  }
  function _guessColor(name) {
    const map = {
      "mcp-txt-llm-analyzer": "#607d8b", "mcp-pdf-llm-analyzer": "#c62828",
      "mcp-docx-llm-analyzer": "#1565c0", "mcp-spreadsheet-llm-analyzer": "#2e7d32",
      "mcp-meeting-to-notion": "#6200ea", "mcp-high-risk-demo": "#ff6f00",
    };
    return map[name] || "#546e7a";
  }
  function _guessCategory(name, desc) {
    if (name.includes("analyzer")) return "analysis";
    if (name.includes("notion") || name.includes("meeting")) return "analysis";
    return "compute";
  }

  // ── Palette Builder ───────────────────────────────────────────
  async function buildPalette(container) {
    await _loadSkillsFromAPI();

    let html = `<div class="wf-palette-header">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 17h7M17.5 14v7"/></svg>
      Skill 節點
    </div><div class="wf-palette-body">`;

    Object.entries(CATEGORIES).forEach(([catKey, cat]) => {
      const items = Object.entries(BLOCK_DEFS).filter(([, d]) => d.category === catKey);
      if (!items.length) return;
      html += `<div class="wf-palette-category"><div class="wf-palette-category-title">${cat.label}</div>`;
      items.forEach(([type, def]) => {
        html += `<div class="wf-palette-item" draggable="true" data-type="${type}">
          <div class="wf-palette-item-accent" style="background:${def.color}"></div>
          <div class="wf-palette-item-icon" style="background:${def.color}">${def.icon}</div>
          <span>${def.label}</span>
        </div>`;
      });
      html += `</div>`;
    });

    html += `</div>`;
    container.innerHTML = html;

    // Drag events
    container.querySelectorAll(".wf-palette-item").forEach(item => {
      item.addEventListener("dragstart", e => {
        e.dataTransfer.setData("blockType", item.dataset.type);
        e.dataTransfer.effectAllowed = "copy";
      });
    });
  }

  // ── Property Panel ────────────────────────────────────────────
  function showWfPropPanel(block, fd) {
    const panel = document.getElementById("wfPropPanel");
    if (!panel) return;
    const def = BLOCK_DEFS[block.type] || {};
    panel.querySelector(".wf-prop-panel-title span").textContent = def.label || block.type;

    const fieldsDiv = panel.querySelector(".wf-prop-fields");
    fieldsDiv.innerHTML = `
      <div class="wf-prop-field"><label>名稱</label><input type="text" value="${block.label}" data-field="label" /></div>
      <div class="wf-prop-field"><label>類型</label><input type="text" value="${block.type}" readonly /></div>
    `;

    // Bind label change
    fieldsDiv.querySelector('[data-field="label"]').addEventListener("change", e => {
      block.label = e.target.value;
      block.el.querySelector(".wf-block-header span:last-child").textContent = e.target.value;
    });

    // Position near block
    const rect = block.el.getBoundingClientRect();
    panel.style.top = Math.max(60, rect.top) + "px";
    panel.style.left = (rect.right + 12) + "px";
    panel.classList.remove("hidden");
  }

  function closeWfPropPanel() {
    const panel = document.getElementById("wfPropPanel");
    if (panel) panel.classList.add("hidden");
  }

  // ── View Toggle ───────────────────────────────────────────────
  let _initialized = false;

  async function toggleWorkflowView() {
    const body = document.querySelector(".page-chat-body");
    const btn = document.getElementById("btnWorkflowDesigner");
    if (!body) return;

    const isActive = body.classList.contains("wf-mode");

    const _overlay = document.getElementById("wfLandingOverlay");
    const _landingOpen = _overlay?.classList.contains("open");

    if (_landingOpen) {
      // Landing is open → close landing, back to chat
      body.classList.remove("wf-mode");
      if (btn) btn.classList.remove("active");
      if (_overlay) _overlay.classList.remove("open");
      const _pp = document.getElementById("wfPropPanel");
      if (_pp) _pp.classList.add("hidden");

    } else if (isActive) {
      // In Designer/Skill Editor → back to Landing (not chat)
      if (_skillEditMode && window._wfSkillEditor?._hasUnsavedChanges()) {
        const ok = await _showConfirmAsync("技能尚未儲存，確定要退出嗎？");
        if (!ok) return;
      }
      body.classList.remove("wf-mode");
      _skillEditMode = false;
      ["wfSkillEditArea", "wfCanvasArea", "wfPaletteWrap", "wfDashboardWrap"].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.style.display = ""; el.classList.remove("visible"); }
      });
      // Close property panel
      const _propPanel = document.getElementById("wfPropPanel");
      if (_propPanel) _propPanel.classList.add("hidden");
      // Clean up designer blocks from DOM
      if (window._wfDesigner) {
        window._wfDesigner.blocks.forEach(b => b.el.remove());
        window._wfDesigner.blocks.clear();
        window._wfDesigner.connections.forEach(c => c.el.remove());
        window._wfDesigner.connections = [];
      }
      _showWorkflowLanding();

    } else {
      // Not in workflow → open Landing
      if (btn) btn.classList.add("active");
      _showWorkflowLanding();
    }
  }

  // ── Workflow Landing (fixed overlay) ────────────────────────────
  const _WF_COLORS = ["#34a853","#1a9aaa","#4285f4","#ea4335","#fbbc04","#8b5cf6","#ec4899","#f97316"];

  function _escHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  async function _showWorkflowLanding() {
    const overlay = document.getElementById("wfLandingOverlay");
    if (!overlay) return;
    overlay.classList.add("open");

    // Fetch workflow list
    const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
    const _owner = _u.employee_id || _u.id || "";
    let workflows = [];
    try {
      const resp = await fetch(`/api/workflows?owner=${_owner}`);
      if (resp.ok) workflows = (await resp.json()).workflows || [];
    } catch (_) {}

    const grid = document.getElementById("wfLandingGrid");
    const leftPanel = document.getElementById("wfLandingLeft");
    const rightPanel = document.getElementById("wfLandingRight");

    // ── Left Panel ──
    if (leftPanel) {
      const sc = { all: workflows.length, system: 0, department: 0, personal: 0 };
      workflows.forEach(wf => { sc[wf.scope || "personal"]++; });
      leftPanel.innerHTML = `
        <div class="wf-lp-section"><div class="wf-lp-title">搜尋</div>
          <input class="wf-lp-search" id="wfLandingSearch" type="text" placeholder="搜尋工作流名稱..." autocomplete="off" /></div>
        <div class="wf-lp-section"><div class="wf-lp-title">分類</div>
          <div class="wf-lp-filter">
            <div class="wf-lp-filter-item active" data-scope="all"><span class="wf-lp-filter-dot" style="background:#64748B;"></span><span>全部</span><span class="wf-lp-filter-count">${sc.all}</span></div>
            <div class="wf-lp-filter-item" data-scope="system"><span class="wf-lp-filter-dot" style="background:#059669;"></span><span>系統</span><span class="wf-lp-filter-count">${sc.system}</span></div>
            <div class="wf-lp-filter-item" data-scope="department"><span class="wf-lp-filter-dot" style="background:#4285f4;"></span><span>部門</span><span class="wf-lp-filter-count">${sc.department}</span></div>
            <div class="wf-lp-filter-item" data-scope="personal"><span class="wf-lp-filter-dot" style="background:#1a9aaa;"></span><span>個人</span><span class="wf-lp-filter-count">${sc.personal}</span></div>
          </div></div>
        <div class="wf-lp-section"><div class="wf-lp-title">最近編輯</div>
          <div class="wf-lp-recent">${workflows.slice(0, 5).map(wf =>
            `<div class="wf-lp-recent-item" onclick="_openWorkflow('${wf.id}','${wf.scope||"personal"}','${_owner}')">${_escHtml(wf.name || wf.id)}</div>`
          ).join("") || '<div class="wf-rp-empty">尚無紀錄</div>'}</div></div>`;
      // Filter handlers
      leftPanel.querySelectorAll(".wf-lp-filter-item").forEach(item => {
        item.addEventListener("click", () => {
          leftPanel.querySelectorAll(".wf-lp-filter-item").forEach(i => i.classList.remove("active"));
          item.classList.add("active");
          _filterWfCards();
        });
      });
      const si = leftPanel.querySelector("#wfLandingSearch");
      if (si) si.addEventListener("input", () => _filterWfCards());
    }

    // ── Center Cards ──
    if (grid) {
      let html = `<div class="wf-landing-card-new" onclick="_createNewWorkflow()">
        <div class="wf-landing-card-new-inner"><div class="wf-landing-card-new-icon">+</div><div class="wf-landing-card-new-label">新增工作流</div></div></div>`;
      workflows.forEach((wf, i) => {
        const color = _WF_COLORS[i % _WF_COLORS.length];
        const key = wf.workflow_key || wf.id;
        const scope = wf.scope || "personal";
        const sl = scope === "system" ? "系統" : scope === "department" ? "部門" : "個人";
        html += `<div class="wf-landing-card" data-scope="${scope}" data-name="${_escHtml(wf.name || wf.id)}" onclick="_openWorkflow('${wf.id}','${scope}','${_owner}')">
          <div class="wf-landing-card-header" style="background:${color};">${_escHtml(wf.name || wf.id)}<div class="wf-landing-card-key">${_escHtml(key)}</div></div>
          <div class="wf-landing-card-body"><div class="wf-landing-card-meta">
            <span>${sl}</span><span class="wf-landing-card-meta-dot"></span><span>${wf.block_count||0} 節點</span><span class="wf-landing-card-meta-dot"></span><span>${wf.connection_count||0} 連接</span>
          </div></div></div>`;
      });
      grid.innerHTML = html;
    }

    // ── Right Panel ──
    if (rightPanel) {
      rightPanel.innerHTML = `
        <div class="wf-rp-section"><div class="wf-rp-title">執行紀錄</div><div class="wf-rp-empty">尚無執行紀錄</div></div>
        <div class="wf-rp-section"><div class="wf-rp-title">LINE Bot 觸發指令</div>
          ${workflows.length ? workflows.slice(0,5).map(wf => `<div class="wf-rp-line-cmd">「執行 ${_escHtml(wf.name||wf.id)}」</div>`).join("") : '<div class="wf-rp-empty">建立工作流後可透過 LINE 觸發</div>'}
        </div>
        <div class="wf-rp-section"><div class="wf-rp-title">排程狀態</div><div class="wf-rp-empty">尚未設定排程</div></div>`;
    }
  }

  function _filterWfCards() {
    const grid = document.getElementById("wfLandingGrid");
    if (!grid) return;
    const scope = document.querySelector(".wf-lp-filter-item.active")?.dataset?.scope || "all";
    const q = (document.getElementById("wfLandingSearch")?.value || "").toLowerCase();
    grid.querySelectorAll(".wf-landing-card").forEach(c => {
      const match = (scope === "all" || c.dataset.scope === scope) && (!q || (c.dataset.name || "").toLowerCase().includes(q));
      c.style.display = match ? "" : "none";
    });
  }

  window._createNewWorkflow = async function () {
    const _chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let _r = ""; for (let i = 0; i < 20; i++) _r += _chars.charAt(Math.floor(Math.random() * _chars.length));
    const wfKey = "WorkflowK_" + _r, wfId = "wf-" + Date.now();
    const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
    try {
      await fetch(`/api/workflows/${wfId}`, { method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ name: "新工作流", blocks: [], connections: [], scope: "personal", owner: _u.employee_id || _u.id || "", context: { workflow_key: wfKey } }) });
    } catch (_) {}
    _enterWorkflowCanvas(wfId, "personal", _u.employee_id || _u.id || "");
  };

  window._openWorkflow = function (id, scope, owner) { _enterWorkflowCanvas(id, scope, owner); };

  async function _enterWorkflowCanvas(wfId, scope, owner) {
    // Close landing overlay, enter wf-mode with canvas
    const overlay = document.getElementById("wfLandingOverlay");
    if (overlay) overlay.classList.remove("open");
    const body = document.querySelector(".page-chat-body");
    if (body) body.classList.add("wf-mode");
    // Remove anti-flash style if present (from ?wf= redirect)
    const antiFlash = document.getElementById("wfAntiFlash");
    if (antiFlash) antiFlash.remove();
    if (body) body.style.visibility = "visible";

    // Build palette
    const paletteWrap = document.getElementById("wfPaletteWrap");
    if (paletteWrap) await _rebuildPaletteForFlow(paletteWrap);

    // Clean up old designer if exists (prevent block accumulation)
    if (window._wfDesigner) {
      window._wfDesigner.blocks.forEach(b => b.el.remove());
      window._wfDesigner.blocks.clear();
      window._wfDesigner.connections.forEach(c => c.el.remove());
      window._wfDesigner.connections = [];
    }

    // Init FlowDesigner
    const surface = document.getElementById("wfCanvasSurface");
    const svg = document.getElementById("wfConnectionsSvg");
    const viewport = document.getElementById("wfCanvasViewport");
    if (surface && svg && viewport) {
      window._wfDesigner = new FlowDesigner(surface, svg, viewport);
      window._wfDesigner._currentWfId = wfId;
      window._wfDesigner._currentScope = scope;
      window._wfDesigner._currentOwner = owner;
      await window._wfDesigner.load(wfId, scope, owner);
      if (window._wfDesigner.blocks.size === 0) window._wfDesigner.addBlock("start", 200, 250);
    }

    // Init Dashboard
    const dashWrap = document.getElementById("wfDashboardWrap");
    if (dashWrap) {
      window._wfDashboard = new WorkflowDashboard(dashWrap);
      const _sync = () => { if (window._wfDashboard && window._wfDesigner) window._wfDashboard.updateStats(window._wfDesigner.blocks.size, window._wfDesigner.connections.length); };
      setTimeout(_sync, 500); setTimeout(_sync, 2000);
    }
  }

  // ── Skill Edit Mode ────────────────────────────────────────────
  let _skillEditMode = false;

  async function toggleSkillEditMode() {
    // Check for unsaved changes before exiting
    if (_skillEditMode && window._wfSkillEditor?._hasUnsavedChanges()) {
      const ok = await _showConfirmAsync("技能尚未儲存，確定要退出嗎？");
      if (!ok) return;
    }

    _skillEditMode = !_skillEditMode;
    const paletteWrap = document.getElementById("wfPaletteWrap");
    const canvasArea = document.getElementById("wfCanvasArea");
    const editArea = document.getElementById("wfSkillEditArea");

    if (_skillEditMode) {
      // Enter skill edit mode — reset to initial state
      if (canvasArea) canvasArea.style.display = "none";
      if (editArea) { editArea.style.display = "flex"; editArea.classList.add("visible"); }
      _rebuildPaletteForEdit(paletteWrap);
      if (!window._wfSkillEditor) window._wfSkillEditor = new SkillEditor();
      // Reset editor to empty state
      const empty = document.getElementById("wfEditorEmpty");
      const content = document.getElementById("wfEditorContent");
      if (empty) empty.style.display = "";
      if (content) content.style.display = "none";
      // Reset test chat
      const msgArea = document.getElementById("wfTestMessages");
      if (msgArea) msgArea.innerHTML = '<div class="wf-test-msg system">選擇 Skill 後即可開始測試對話</div>';
      document.getElementById("wfTestSkillName").textContent = "—";
      // Clear palette selection
      document.querySelectorAll(".wf-palette-item--clickable").forEach(el => el.classList.remove("is-active"));
      if (window._wfSkillEditor) window._wfSkillEditor.currentSkill = null;
    } else {
      // Exit skill edit mode
      if (editArea) { editArea.style.display = "none"; editArea.classList.remove("visible"); }
      if (canvasArea) canvasArea.style.display = "flex";
      _rebuildPaletteForFlow(paletteWrap);
    }
  }

  async function _rebuildPaletteForEdit(container) {
    if (!container) return;
    await _loadSkillsFromAPI();

    let html = `<div class="wf-palette-header">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
      Skills 維護
      <button class="wf-palette-header-btn" onclick="toggleSkillEditMode()">編輯節點</button>
    </div><div class="wf-palette-body">`;

    // Group skills by scope for three-tier collapsible display
    const SCOPE_LABELS = { "system": "系統技能", "dept": "部門技能", "user": "個人技能" };
    const scopeGroups = { system: [], dept: [], user: [] };

    // Classify skills by scope (skip control blocks)
    Object.entries(BLOCK_DEFS).forEach(([type, def]) => {
      if (def.category === "control") return;
      const skillName = type.startsWith("mcp-") ? type : "mcp-" + type;
      const info = _dynamicSkills[skillName] || {};
      const scope = (info.scope || "system").split(":")[0];
      (scopeGroups[scope] || scopeGroups.system).push([type, def, skillName]);
    });

    // Render each scope as a collapsible section
    for (const [scopeKey, label] of Object.entries(SCOPE_LABELS)) {
      const items = scopeGroups[scopeKey];
      const count = items.length;
      const id = `wfScopeGroup_${scopeKey}`;
      // Default: system expanded, others collapsed
      const open = scopeKey === "system";
      html += `<div class="wf-palette-scope">
        <div class="wf-palette-scope-header${open ? " open" : ""}" onclick="this.classList.toggle('open');document.getElementById('${id}').classList.toggle('collapsed');">
          <span>${label}</span>
          <span class="wf-palette-scope-count">${count}</span>
          <svg class="wf-palette-scope-arrow" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
        </div>
        <div class="wf-palette-scope-body${open ? "" : " collapsed"}" id="${id}">`;
      if (count === 0) {
        html += `<div style="font-size:0.65rem;color:var(--text-tertiary);padding:6px 8px;">（無）</div>`;
      }
      items.forEach(([type, def, skillName]) => {
        html += `<div class="wf-palette-item wf-palette-item--clickable" data-type="${type}"
          onclick="window._wfSkillEditor&&window._wfSkillEditor.loadSkill('${skillName}')">
          <div class="wf-palette-item-accent" style="background:${def.color}"></div>
          <div class="wf-palette-item-icon" style="background:${def.color}">${def.icon}</div>
          <span>${def.label}</span></div>`;
      });
      html += `</div></div>`;
    }
    html += `</div>`;
    container.innerHTML = html;

    // Hide "新增 Skill" button for guests
    const _createBtn = document.getElementById("wfBtnCreateSkill");
    if (_createBtn && window._wfIsGuest) _createBtn.style.display = "none";
  }

  async function _rebuildPaletteForFlow(container) {
    if (!container) return;
    await buildPalette(container);
    // Add edit button to header
    const header = container.querySelector(".wf-palette-header");
    if (header && !header.querySelector(".wf-palette-header-btn")) {
      const btn = document.createElement("button");
      btn.className = "wf-palette-header-btn";
      btn.textContent = "編輯技能";
      btn.onclick = toggleSkillEditMode;
      header.appendChild(btn);
    }
  }

  // ── SkillEditor Class ─────────────────────────────────────────
  class SkillEditor {
    constructor() {
      this.currentSkill = null;
      this.models = ["gpt-4o", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"];
      this.modelIdx = 0;
      this.testMessages = [];
      this._backup = null; // snapshot for rollback
    }

    _hasUnsavedChanges() {
      return this._dirty === true;
    }

    _markDirty() {
      this._dirty = true;
    }

    _clearDirty() {
      this._dirty = false;
    }

    createNewSkill() {
      this.currentSkill = null;
      this._backup = null;
      this._editState = { skillName: "", meta: {}, rawContent: "" };
      this._isNew = true;
      this._markDirty();

      // Show editor, hide empty state
      const empty = document.getElementById("wfEditorEmpty");
      const content = document.getElementById("wfEditorContent");
      if (empty) empty.style.display = "none";
      if (content) content.style.display = "flex";

      // Update header
      const scopeLabel = "新增 Skill";
      document.getElementById("wfEditorTitle").textContent = scopeLabel;
      const _idEl = document.getElementById("wfEditorSkillId");
      if (_idEl) _idEl.textContent = "ID 將在首次儲存後自動產生";
      document.getElementById("wfTestSkillName").textContent = "—";

      // Render empty form
      const body = document.getElementById("wfEditorBody");
      if (!body) return;
      const _defaultCat = "System";
      body.innerHTML = `
        <div class="wf-editor-field" style="display:flex;gap:10px;">
          <div style="flex:2"><label>顯示名稱 (Display Name)</label>
            <input type="text" id="wfEditDisplayName" value="" placeholder="例如：我的新技能" /></div>
          <div style="flex:1"><label>技能群組 (Category)</label>
            <select id="wfEditCategory">
              <option value="System"${_defaultCat==="System"?" selected":""}>System</option>
              <option value="Department"${_defaultCat==="Department"?" selected":""}>Department</option>
              <option value="Personal"${_defaultCat==="Personal"?" selected":""}>Personal</option>
            </select></div>
        </div>
        <div class="wf-editor-field">
          <label>名稱 (Name)</label>
          <input type="text" id="wfEditName" value="mcp-" data-original="" placeholder="mcp-my-new-skill" />
        </div>
        <div class="wf-editor-field">
          <label>簡介 (Description)</label>
          <textarea id="wfEditDesc" rows="3" style="min-height:60px;font-family:inherit;" placeholder="描述這個技能的用途，LLM 會根據此文字決定是否呼叫此技能..."></textarea>
        </div>
        <div class="wf-editor-field" style="display:flex;gap:10px;">
          <div style="flex:1"><label>技能版本 (Version)</label><input type="text" id="wfEditVersion" value="1.0.0" /></div>
          <div style="flex:1"><label>操作風險 (Risk)</label>
            <select id="wfEditRisk"><option value="low" selected>low</option><option value="high">high</option></select>
          </div>
          <div style="flex:1"><label>逾時等待 (Timeout)</label><input type="number" id="wfEditTimeout" value="30" /></div>
        </div>
        <div class="wf-editor-section-title">提示詞 (Prompt)</div>
        <div class="wf-editor-field">
          <textarea id="wfEditBody" rows="12" placeholder="在此輸入 Skill 的指示內容..."></textarea>
        </div>
        <div class="wf-editor-file-section">
          <div class="wf-editor-file-title"><span>知識參考 (References)</span></div>
          <div style="font-size:0.65rem;color:var(--text-tertiary);padding:4px 0;">儲存後即可上傳檔案</div>
        </div>
        <div class="wf-editor-file-section">
          <div class="wf-editor-file-title"><span>程式操作 (Scripts)</span></div>
          <div style="font-size:0.65rem;color:var(--text-tertiary);padding:4px 0;">儲存後即可上傳檔案</div>
        </div>
        <div class="wf-editor-file-section">
          <div class="wf-editor-file-title"><span>模板檔案 (Assets)</span></div>
          <div style="font-size:0.65rem;color:var(--text-tertiary);padding:4px 0;">儲存後即可上傳檔案</div>
        </div>
      `;

      // Reset test chat
      const msgArea = document.getElementById("wfTestMessages");
      if (msgArea) msgArea.innerHTML = '<div class="wf-test-msg system">儲存 Skill 後即可開始測試</div>';
    }

    async loadSkill(skillName) {
      // Check unsaved before switching
      if (this._hasUnsavedChanges() && this.currentSkill && this.currentSkill !== skillName) {
        const ok = await _showConfirmAsync("技能尚未儲存，確定要切換嗎？");
        if (!ok) return;
      }
      this.currentSkill = skillName;
      this._isNew = false;
      this._clearDirty();

      // Highlight active in palette
      document.querySelectorAll(".wf-palette-item--clickable").forEach(el => el.classList.remove("is-active"));
      const activeItem = document.querySelector(`.wf-palette-item--clickable[data-type="${skillName.replace("mcp-","")}"]`);
      if (activeItem) activeItem.classList.add("is-active");

      // Show editor
      const empty = document.getElementById("wfEditorEmpty");
      const content = document.getElementById("wfEditorContent");
      if (empty) empty.style.display = "none";
      if (content) content.style.display = "flex";

      // Update titles
      const def = BLOCK_DEFS[skillName.replace("mcp-", "")] || {};
      document.getElementById("wfEditorTitle").textContent = def.label || skillName;
      document.getElementById("wfTestSkillName").textContent = def.label || skillName;
      const _idEl = document.getElementById("wfEditorSkillId");
      if (_idEl) _idEl.textContent = "";

      // Fetch skill data
      try {
        const [detail, files] = await Promise.all([
          fetch(`/skills/${skillName}`).then(r => r.json()),
          fetch(`/skills/${skillName}/files`).then(r => r.json()),
        ]);
        // Store backup for rollback
        this._backup = detail.raw_content || "";
        // Display SkillK_ ID below title
        if (_idEl && detail.metadata?.skillk_id) {
          _idEl.textContent = detail.metadata.skillk_id;
        }
        this._renderEditor(skillName, detail, files);

        // Toggle edit buttons based on editable permission
        const _info = _dynamicSkills[skillName] || {};
        const _canEdit = _info.editable === true;
        ["wfBtnDelete","wfBtnRollback","wfBtnSave"].forEach(id => {
          const btn = document.getElementById(id);
          if (btn) btn.style.display = _canEdit ? "" : "none";
        });
      } catch (e) {
        console.error("[SkillEditor] Load failed:", e);
        if (window.showToast) window.showToast("載入失敗: " + e.message, "error");
      }

      // Reset test chat
      this.testMessages = [];
      const msgArea = document.getElementById("wfTestMessages");
      if (msgArea) msgArea.innerHTML = `<div class="wf-test-msg system">已載入 ${def.label || skillName}，可以開始測試</div>`;
    }

    _renderEditor(skillName, detail, files) {
      const body = document.getElementById("wfEditorBody");
      if (!body) return;

      const meta = detail.metadata || {};
      const rawContent = detail.raw_content || "";

      // Split YAML frontmatter from Markdown body
      const { yamlMeta, mdBody } = this._splitSkillMd(rawContent);

      // Store parsed state for save
      this._editState = { skillName, meta, rawContent };

      // Display name: metadata > BLOCK_DEFS > fallback
      const def = BLOCK_DEFS[skillName.replace("mcp-", "")] || {};
      const displayName = meta.display_name || def.label || skillName.replace("mcp-", "").replace(/-/g, " ");

      const _cat = meta.category || "System";
      body.innerHTML = `
        <div class="wf-editor-field" style="display:flex;gap:10px;">
          <div style="flex:2"><label>顯示名稱 (Display Name)</label>
            <input type="text" id="wfEditDisplayName" value="${this._escapeHtml(displayName)}" /></div>
          <div style="flex:1"><label>技能群組 (Category)</label>
            <select id="wfEditCategory">
              <option value="System"${_cat==="System"?" selected":""}>System</option>
              <option value="Department"${_cat==="Department"?" selected":""}>Department</option>
              <option value="Personal"${_cat==="Personal"?" selected":""}>Personal</option>
            </select></div>
        </div>
        <div class="wf-editor-field">
          <label>名稱 (Name)</label>
          <input type="text" id="wfEditName" value="${meta.name || skillName}" data-original="${meta.name || skillName}" />
        </div>
        <div class="wf-editor-field">
          <label>簡介 (Description)</label>
          <textarea id="wfEditDesc" rows="3" style="min-height:60px;font-family:inherit;">${this._escapeHtml((meta.description || "").trim())}</textarea>
        </div>
        <div class="wf-editor-field" style="display:flex;gap:10px;">
          <div style="flex:1"><label>技能版本 (Version)</label><input type="text" id="wfEditVersion" value="${meta.version || "1.0.0"}" /></div>
          <div style="flex:1"><label>操作風險 (Risk)</label>
            <select id="wfEditRisk">
              <option value="low" ${meta.risk_level==="low"?"selected":""}>low</option>
              <option value="high" ${meta.risk_level==="high"?"selected":""}>high</option>
            </select>
          </div>
          <div style="flex:1"><label>逾時等待 (Timeout)</label><input type="number" id="wfEditTimeout" value="${meta.execution_timeout || 30}" /></div>
        </div>

        <div class="wf-editor-section-title">建議模型 (Recommended Models)</div>
        <div class="wf-editor-field" style="display:flex;gap:10px;">
          <div style="flex:1"><label>OpenAI</label><input type="text" id="wfEditModelOpenai" value="${(meta.recommended_models?.openai) || ''}" placeholder="自動評估" /></div>
          <div style="flex:1"><label>Gemini</label><input type="text" id="wfEditModelGemini" value="${(meta.recommended_models?.gemini) || ''}" placeholder="自動評估" /></div>
          <div style="flex:1"><label>Claude</label><input type="text" id="wfEditModelClaude" value="${(meta.recommended_models?.claude) || ''}" placeholder="自動評估" /></div>
        </div>
        <div style="font-size:0.6rem;color:var(--text-tertiary);margin:-6px 0 8px 2px;">儲存時自動評估，或手動指定具體模型名稱</div>

        <div class="wf-editor-section-title">提示詞 (Prompt)</div>
        <div class="wf-editor-field">
          <textarea id="wfEditBody" rows="12">${this._escapeHtml(mdBody.trim())}</textarea>
        </div>

        ${this._renderFileSection("references", "知識參考 (References)", files.references || [])}
        ${this._renderFileSection("scripts", "程式操作 (Scripts)", files.scripts || [])}
        ${this._renderFileSection("assets", "模板檔案 (Assets)", files.assets || [])}
      `;

      // Bind change events to mark dirty
      body.querySelectorAll("input, textarea, select").forEach(el => {
        el.addEventListener("input", () => this._markDirty());
      });
    }

    _splitSkillMd(raw) {
      // Split "---\nyaml\n---\nmarkdown body"
      const match = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
      if (match) return { yamlMeta: match[1], mdBody: match[2] };
      return { yamlMeta: "", mdBody: raw };
    }

    // (parameter card methods removed — parameters not used by system)

    _renderFileSection(folder, title, fileList) {
      // Map folder name to API file_type: references→knowledge, scripts→script, assets→asset
      const typeMap = { references: "knowledge", scripts: "script", assets: "asset" };
      const fileType = typeMap[folder] || folder;
      const items = fileList.map(f =>
        `<div class="wf-editor-file-item">
          <span>${f}</span>
          <button class="wf-editor-file-del" onclick="window._wfSkillEditor._deleteFile('${folder}','${f}')">&times;</button>
        </div>`
      ).join("");
      return `
        <div class="wf-editor-file-section">
          <div class="wf-editor-file-title">
            <span>${title}</span>
            <label class="wf-editor-file-upload-btn">
              上傳
              <input type="file" style="display:none" onchange="window._wfSkillEditor._uploadFile('${fileType}',this)" />
            </label>
          </div>
          ${items || '<div style="font-size:0.65rem;color:var(--text-tertiary);padding:4px 0;">（無檔案）</div>'}
        </div>
      `;
    }

    _assembleSkillMd() {
      // Build YAML frontmatter from form fields + assemble with Markdown body
      const name = document.getElementById("wfEditName")?.value || this.currentSkill;
      const desc = document.getElementById("wfEditDesc")?.value || "";
      const version = document.getElementById("wfEditVersion")?.value || "1.0.0";
      const risk = document.getElementById("wfEditRisk")?.value || "low";
      const timeout = document.getElementById("wfEditTimeout")?.value || "30";
      const mdBody = document.getElementById("wfEditBody")?.value || "";

      const meta = this._editState?.meta || {};

      const displayName = document.getElementById("wfEditDisplayName")?.value?.trim() || "";

      const category = document.getElementById("wfEditCategory")?.value || "System";

      // Generate or preserve SkillK_ ID (immutable after first save)
      let _skillkId = meta.skillk_id || "";
      if (!_skillkId) {
        const _chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
        let _rand = "";
        for (let i = 0; i < 20; i++) _rand += _chars.charAt(Math.floor(Math.random() * _chars.length));
        _skillkId = "SkillK_" + _rand;
      }

      // Assemble YAML frontmatter
      let yaml = `---\nname: ${name}\n`;
      yaml += `skillk_id: ${_skillkId}\n`;
      if (displayName) yaml += `display_name: "${displayName}"\n`;
      yaml += `category: ${category}\n`;
      if (meta.provider) yaml += `provider: ${meta.provider}\n`;
      yaml += `version: "${version}"\n`;
      if (desc) {
        yaml += `description: >\n  ${desc.replace(/\n/g, "\n  ")}\n`;
      }
      if (meta.runtime_requirements?.length) yaml += `runtime_requirements: [${meta.runtime_requirements.join(", ")}]\n`;
      else yaml += `runtime_requirements: []\n`;
      yaml += `risk_level: ${risk}\n`;
      if (meta.risk_description) yaml += `risk_description: >\n  ${String(meta.risk_description).trim().replace(/\n/g, "\n  ")}\n`;
      if (parseInt(timeout) !== 30) yaml += `execution_timeout: ${timeout}\n`;

      // Recommended models (user-specified override — if all empty, auto-evaluated on save)
      const _rmOpenai = document.getElementById("wfEditModelOpenai")?.value?.trim();
      const _rmGemini = document.getElementById("wfEditModelGemini")?.value?.trim();
      const _rmClaude = document.getElementById("wfEditModelClaude")?.value?.trim();
      if (_rmOpenai || _rmGemini || _rmClaude) {
        yaml += `recommended_models:\n`;
        if (_rmOpenai) yaml += `  openai: ${_rmOpenai}\n`;
        if (_rmGemini) yaml += `  gemini: ${_rmGemini}\n`;
        if (_rmClaude) yaml += `  claude: ${_rmClaude}\n`;
      }
      // If none specified, backend will auto-evaluate on save

      yaml += `---\n\n`;
      yaml += mdBody;

      return yaml;
    }

    // ── Save Status Modal helpers ───────────────────────────
    _showSaveModal() {
      const ov = document.createElement("div");
      ov.className = "wf-save-overlay";
      ov.innerHTML = `
        <div class="wf-save-modal">
          <img src="../assets/images/kw_logo.png" alt="Agent K" />
          <div class="wf-save-text" id="wfSaveStatus">
            Saving<span class="wf-save-dots"><span>.</span><span>.</span><span>.</span></span>
          </div>
        </div>
      `;
      document.body.appendChild(ov);
      return ov;
    }

    _saveModalSuccess(overlay) {
      const status = overlay.querySelector("#wfSaveStatus");
      if (!status) return;
      // Stop logo pulse
      const img = overlay.querySelector("img");
      if (img) img.style.animation = "none";
      // Replace "Saving..." with a clickable success button
      status.innerHTML = "";
      const btn = document.createElement("button");
      btn.className = "wf-save-ok";
      btn.textContent = "Success";
      btn.addEventListener("click", () => {
        overlay.remove();
        // Refresh editor + palette
        this._postSaveRefresh();
      });
      status.parentElement.appendChild(btn);
    }

    _saveModalError(overlay, msg) {
      const status = overlay.querySelector("#wfSaveStatus");
      if (!status) return;
      const img = overlay.querySelector("img");
      if (img) img.style.animation = "none";
      status.innerHTML = `
        <div class="wf-save-error">
          ${msg}
          <br/><button onclick="this.closest('.wf-save-overlay').remove()">關閉</button>
        </div>
      `;
    }

    async _postSaveRefresh() {
      await fetch("/skills/rescan", { method: "POST" });
      _skillsLoaded = false;
      // Remember which scope sections are expanded before rebuild
      const _openState = {};
      document.querySelectorAll(".wf-palette-scope-header").forEach(h => {
        const bodyId = h.nextElementSibling?.id;
        if (bodyId) _openState[bodyId] = h.classList.contains("open");
      });
      if (this.currentSkill) this.loadSkill(this.currentSkill);
      const paletteWrap = document.getElementById("wfPaletteWrap");
      if (paletteWrap) {
        await _rebuildPaletteForEdit(paletteWrap);
        // Restore expanded state
        Object.entries(_openState).forEach(([id, wasOpen]) => {
          const body = document.getElementById(id);
          const header = body?.previousElementSibling;
          if (body && header) {
            if (wasOpen) { header.classList.add("open"); body.classList.remove("collapsed"); }
            else { header.classList.remove("open"); body.classList.add("collapsed"); }
          }
        });
      }
    }

    // ── Save ─────────────────────────────────────────────────
    async save() {
      // Handle new skill creation
      if (this._isNew) {
        const newName = document.getElementById("wfEditName")?.value?.trim();
        if (!newName || !/^mcp-[a-z0-9-]+$/.test(newName)) {
          if (window.showToast) window.showToast("名稱格式錯誤，必須為 mcp-{小寫英數字-}", "error");
          return;
        }
        try {
          // Resolve scope + owner from Category dropdown
          const _catVal = (document.getElementById("wfEditCategory")?.value || "System").toLowerCase();
          const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
          let _scope = "system", _owner = "";
          if (_catVal === "department") {
            _scope = "department";
            _owner = _u.department_code || "unknown";
          } else if (_catVal === "personal") {
            _scope = "personal";
            _owner = _u.employee_id || _u.id || "unknown";
          }
          const createResp = await fetch("/skills/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: newName,
              scope: _scope,
              owner: _owner,
            }),
          });
          if (!createResp.ok) {
            const err = await createResp.json();
            if (window.showToast) window.showToast("建立失敗: " + (err.detail || ""), "error");
            return;
          }
          this.currentSkill = newName;
          this._isNew = false;
          this._editState = { skillName: newName, meta: {}, rawContent: "" };
        } catch (e) {
          if (window.showToast) window.showToast("建立錯誤: " + e.message, "error");
          return;
        }
      }

      if (!this.currentSkill) return;
      const skillMd = this._assembleSkillMd();
      if (!skillMd) return;

      const nameInput = document.getElementById("wfEditName");
      const newName = nameInput?.value?.trim();
      const originalName = nameInput?.dataset?.original;
      const renamed = newName && originalName && newName !== originalName;

      // Show saving modal
      const overlay = this._showSaveModal();

      try {
        const _user = JSON.parse(sessionStorage.getItem("kway_user") || "{}");

        // Step 1: Move directory FIRST if category changed (before PUT triggers rescan)
        const _newCat = (document.getElementById("wfEditCategory")?.value || "System");
        const _oldCat = (this._editState?.meta?.category || "System");
        if (_newCat !== _oldCat && !this._isNew) {
          const _scopeMap = { "System": "system", "Department": "department", "Personal": "personal" };
          const _targetScope = _scopeMap[_newCat] || "system";
          let _targetOwner = "";
          if (_targetScope === "department") _targetOwner = _user.department_code || "unknown";
          else if (_targetScope === "personal") _targetOwner = _user.employee_id || _user.id || "unknown";

          const moveResp = await fetch(`/skills/${this.currentSkill}/move?target_scope=${_targetScope}&target_owner=${_targetOwner}`, {
            method: "POST",
          });
          if (!moveResp.ok) {
            const moveErr = await moveResp.json();
            this._saveModalError(overlay, "搬移失敗: " + (moveErr.detail || ""));
            return;
          }
        }

        // Step 2: PUT to update SKILL.md content (now in the new directory)
        const resp = await fetch(`/skills/${this.currentSkill}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            yaml_content: skillMd,
            user_name: _user.name || "unknown",
            user_id: _user.id || "unknown",
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          this._saveModalError(overlay, "儲存失敗: " + (data.detail || ""));
          return;
        }

        // Step 3: Handle rename if name changed
        if (renamed) {
          const renameResp = await fetch(`/skills/${this.currentSkill}/rename`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_name: newName }),
          });
          if (renameResp.ok) {
            this.currentSkill = newName;
          } else {
            const renameErr = await renameResp.json();
            this._saveModalError(overlay, "更名失敗: " + (renameErr.detail || ""));
            return;
          }
        }

        this._clearDirty();
        this._saveModalSuccess(overlay);
      } catch (e) {
        this._saveModalError(overlay, "儲存錯誤: " + e.message);
      }
    }

    async deleteSkill() {
      if (!this.currentSkill || this._isNew) return;
      const skillName = this.currentSkill;

      // Build confirmation modal
      const overlay = document.createElement("div");
      overlay.className = "wf-delete-overlay";
      overlay.innerHTML = `
        <div class="wf-delete-modal">
          <h3>確認刪除 Agent Skill</h3>
          <div class="wf-delete-skill-name">${skillName}</div>
          <label for="wfDeleteReason">刪除原因（必填）</label>
          <textarea id="wfDeleteReason" placeholder="請輸入刪除原因，至少 5 個字..."></textarea>
          <div class="wf-delete-hint">此操作不可復原，將完全移除該 Skill 及所有相關檔案，並同步 Commit。</div>
          <div class="wf-delete-actions">
            <button class="wf-delete-cancel" id="wfDeleteCancel">取消</button>
            <button class="wf-delete-confirm" id="wfDeleteConfirm" disabled>確認刪除</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);

      const textarea = overlay.querySelector("#wfDeleteReason");
      const confirmBtn = overlay.querySelector("#wfDeleteConfirm");
      const cancelBtn = overlay.querySelector("#wfDeleteCancel");

      // Enable confirm only when reason >= 5 chars
      textarea.addEventListener("input", () => {
        confirmBtn.disabled = textarea.value.trim().length < 5;
      });
      textarea.focus();

      // Cancel
      cancelBtn.addEventListener("click", () => overlay.remove());
      overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

      // Confirm
      confirmBtn.addEventListener("click", async () => {
        const reason = textarea.value.trim();
        if (reason.length < 5) return;

        confirmBtn.disabled = true;
        confirmBtn.textContent = "刪除中...";

        const user = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
        try {
          const resp = await fetch(`/skills/${skillName}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              reason,
              user_name: user.name || "unknown",
              user_id: user.id || "unknown",
            }),
          });
          const data = await resp.json();
          if (!resp.ok) {
            if (window.showToast) window.showToast("刪除失敗: " + (data.detail || ""), "error");
            return;
          }
          if (window.showToast) window.showToast(`已刪除 ${skillName} 並同步 Commit`, "success");

          // Clear editor
          this.currentSkill = null;
          this._isNew = false;
          this._clearDirty();
          const content = document.getElementById("wfEditorContent");
          const empty = document.getElementById("wfEditorEmpty");
          if (content) content.style.display = "none";
          if (empty) empty.style.display = "flex";

          // Rebuild palette
          _skillsLoaded = false;
          const paletteWrap = document.getElementById("wfPaletteWrap");
          if (paletteWrap) await _rebuildPaletteForEdit(paletteWrap);
        } catch (e) {
          if (window.showToast) window.showToast("刪除錯誤: " + e.message, "error");
        } finally {
          overlay.remove();
        }
      });
    }

    async rollback() {
      if (!this.currentSkill) return;
      if (this._backup) {
        // Reload from in-memory snapshot (re-render entire editor)
        if (window.showToast) window.showToast("已還原至開啟時的版本", "success");
        this.loadSkill(this.currentSkill);
        return;
      } else {
        // Fallback: try server-side .bak rollback
        try {
          await fetch(`/skills/${this.currentSkill}/rollback`, { method: "POST" });
          if (window.showToast) window.showToast("已還原至伺服器備份", "success");
          this.loadSkill(this.currentSkill);
        } catch (e) {
          if (window.showToast) window.showToast("還原失敗", "error");
        }
      }
    }

    async _uploadFile(fileType, input) {
      // fileType: "knowledge" (references), "script" (scripts), "asset" (assets)
      if (!this.currentSkill || !input.files[0]) return;
      const formData = new FormData();
      formData.append("file", input.files[0]);
      formData.append("file_type", fileType);
      try {
        const resp = await fetch(`/skills/${this.currentSkill}/upload`, { method: "POST", body: formData });
        const data = await resp.json();
        if (resp.ok) {
          if (window.showToast) window.showToast(`已上傳 ${data.filename || ""}`, "success");
          this.loadSkill(this.currentSkill);
        } else {
          if (window.showToast) window.showToast("上傳失敗: " + (data.detail || ""), "error");
        }
      } catch (e) {
        if (window.showToast) window.showToast("上傳錯誤: " + e.message, "error");
      }
    }

    async _deleteFile(folder, filename) {
      if (!this.currentSkill) return;
      try {
        await fetch(`/skills/${this.currentSkill}/files/${folder}/${filename}`, { method: "DELETE" });
        if (window.showToast) window.showToast("已刪除", "success");
        this.loadSkill(this.currentSkill);
      } catch (e) {
        if (window.showToast) window.showToast("刪除失敗", "error");
      }
    }

    cycleModel() {
      this.modelIdx = (this.modelIdx + 1) % this.models.length;
      const btn = document.getElementById("wfTestModelBtn");
      if (btn) btn.textContent = this.models[this.modelIdx];
    }

    async sendTest() {
      const input = document.getElementById("wfTestInput");
      const msgArea = document.getElementById("wfTestMessages");
      const sendBtn = document.getElementById("wfTestSendBtn");
      if (!input || !input.value.trim() || !this.currentSkill) return;

      const userMsg = input.value.trim();
      input.value = "";

      // Add user message
      this._addTestMsg(userMsg, "user");

      // Disable send
      if (sendBtn) sendBtn.disabled = true;

      try {
        const model = this.models[this.modelIdx];
        const resp = await fetch("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_input: userMsg,
            session_id: "skill_test_" + this.currentSkill,
            model: model,
            injected_skill: this.currentSkill,
            execute: true,
          }),
        });

        // SSE stream
        if (resp.headers.get("content-type")?.includes("text/event-stream")) {
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let assistantText = "";
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value);
            const lines = chunk.split("\n");
            for (const line of lines) {
              if (line.startsWith("data: ")) {
                try {
                  const d = JSON.parse(line.slice(6));
                  if (d.content) assistantText += d.content;
                  if (d.status === "success" && d.content) assistantText = d.content;
                } catch (_) {}
              }
            }
          }
          if (assistantText) this._addTestMsg(assistantText, "assistant");
          else this._addTestMsg("（無回應）", "system");
        } else {
          const data = await resp.json();
          const reply = data.reply || data.content || data.message || JSON.stringify(data);
          this._addTestMsg(reply, "assistant");
        }
      } catch (e) {
        this._addTestMsg("Error: " + e.message, "system");
      }

      if (sendBtn) sendBtn.disabled = false;
    }

    _addTestMsg(text, role) {
      const msgArea = document.getElementById("wfTestMessages");
      if (!msgArea) return;
      const el = document.createElement("div");
      el.className = `wf-test-msg ${role}`;
      el.textContent = text;
      msgArea.appendChild(el);
      msgArea.scrollTop = msgArea.scrollHeight;
    }

    _escapeHtml(s) {
      return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }
  }

  // ── Confirm Dialog (styled) ────────────────────────────────────
  function _showConfirmDialog(message) {
    // Synchronous confirm — styled dialogs would need async refactor
    // For now use native confirm but wrapped for future replacement
    return window.confirm(message);
  }

  // Inject styled confirm modal into DOM (async version for future use)
  function _showConfirmAsync(message) {
    return new Promise(resolve => {
      // Remove existing
      let existing = document.getElementById("wfConfirmOverlay");
      if (existing) existing.remove();

      const overlay = document.createElement("div");
      overlay.id = "wfConfirmOverlay";
      overlay.className = "wf-confirm-overlay";
      overlay.innerHTML = `
        <div class="wf-confirm-box">
          <div class="wf-confirm-icon">⚠️</div>
          <div class="wf-confirm-msg">${message}</div>
          <div class="wf-confirm-actions">
            <button class="wf-confirm-btn wf-confirm-cancel">取消</button>
            <button class="wf-confirm-btn wf-confirm-ok">確定退出</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);

      overlay.querySelector(".wf-confirm-cancel").onclick = () => { overlay.remove(); resolve(false); };
      overlay.querySelector(".wf-confirm-ok").onclick = () => { overlay.remove(); resolve(true); };
      overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(false); } };
    });
  }

  // ── Utility ───────────────────────────────────────────────────
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // Expose to global
  window.toggleWorkflowView = toggleWorkflowView;
  window.toggleSkillEditMode = toggleSkillEditMode;
  window.closeWfPropPanel = closeWfPropPanel;

  // Auto-open workflow if ?wf=xxx query param present (from admin.html)
  (function () {
    const params = new URLSearchParams(window.location.search);
    const wfId = params.get("wf");
    if (!wfId) return;
    const scope = params.get("scope") || "personal";
    const owner = params.get("owner") || "";
    history.replaceState(null, "", window.location.pathname);

    // Wait for DOM elements to exist, then enter canvas
    function _tryEnter() {
      const surface = document.getElementById("wfCanvasSurface");
      if (!surface) { requestAnimationFrame(_tryEnter); return; }
      const body = document.querySelector(".page-chat-body");
      if (body) body.classList.add("wf-mode");
      const btn = document.getElementById("btnWorkflowDesigner");
      if (btn) btn.classList.add("active");
      _enterWorkflowCanvas(wfId, scope, owner);
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", _tryEnter);
    } else {
      _tryEnter();
    }
  })();

})();
