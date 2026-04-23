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
    // Phase 4: sub-workflow invokes another saved workflow.
    // Parallel execution is now TOPOLOGY-based — from the same block, just
    // draw arrows to multiple children; the executor runs them concurrently
    // via wave execution. No dedicated parallel block needed (the old
    // "parallel" type is still supported for backward compat with saved
    // workflows, just not exposed in the palette).
    "sub-workflow":                { label: "子工作流",    icon: "📎", color: "#546e7a", category: "control" },
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

  /**
   * Convert a workflow display name → safe file-system ID (used as JSON filename stem).
   * Rules: strip Windows-invalid chars, collapse spaces→underscore, max 60 chars.
   * Chinese / alphanumeric / hyphens are all preserved.
   */
  function _sanitizeWfId(name) {
    if (!name || !name.trim()) return "";
    return name.trim()
      .replace(/[/\\:*?"<>|]/g, "")    // Windows-invalid filename chars
      .replace(/\s+/g, "_")             // spaces → underscore (URL-safe)
      .replace(/_{2,}/g, "_")           // collapse consecutive underscores
      .replace(/^_+|_+$/g, "")         // strip leading / trailing underscores
      .substring(0, 60);
  }

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
      // Store bound handlers so they can be removed by destroy()
      this._handlers = {
        dragover:        e => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; },
        drop:            e => this._onDrop(e),
        mousemove:       e => this._onMouseMove(e),
        mouseup:         e => this._onMouseUp(e),
        surfaceMousedown:e => { if (e.target === this.surface) this.select(null); },
        keydown:         e => { if (e.key === "Delete" && this.selectedId != null) this.deleteBlock(this.selectedId); },
        wheel:           e => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); this.zoom(e.deltaY > 0 ? -0.1 : 0.1); } },
      };
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

      // Drop from palette (using stored handlers for removability)
      this.surface.addEventListener("dragover", this._handlers.dragover);
      this.surface.addEventListener("drop",    this._handlers.drop);

      // Mouse events for drag + connect
      document.addEventListener("mousemove", this._handlers.mousemove);
      document.addEventListener("mouseup",   this._handlers.mouseup);

      // Deselect on canvas click
      this.surface.addEventListener("mousedown", this._handlers.surfaceMousedown);

      // Delete key
      document.addEventListener("keydown", this._handlers.keydown);

      // Zoom
      this.viewport.addEventListener("wheel", this._handlers.wheel, { passive: false });
    }

    /** Remove all event listeners added by _setup(). Call before discarding this instance. */
    destroy() {
      this.surface.removeEventListener("dragover",   this._handlers.dragover);
      this.surface.removeEventListener("drop",       this._handlers.drop);
      this.surface.removeEventListener("mousedown",  this._handlers.surfaceMousedown);
      document.removeEventListener("mousemove", this._handlers.mousemove);
      document.removeEventListener("mouseup",   this._handlers.mouseup);
      document.removeEventListener("keydown",   this._handlers.keydown);
      this.viewport.removeEventListener("wheel", this._handlers.wheel);
      // Clean up any in-progress temp path
      if (this.connecting) { try { this.connecting.tempPath.remove(); } catch (_) {} this.connecting = null; }
    }

    // ── Block CRUD ────────────────────────────────────────────
    addBlock(type, x, y, label, suppressPrefill = false) {
      const def = BLOCK_DEFS[type];
      if (!def) return null;
      const id = this.nextId++;
      const el = this._createBlockEl(id, type, def, label);
      el.style.left = snap(x) + "px";
      el.style.top = snap(y) + "px";
      this.surface.appendChild(el);
      const block = { id, type, x: snap(x), y: snap(y), label: label || def.label, config: {}, el };
      this.blocks.set(id, block);
      this._updateInfo();
      // Skill blocks: async-prefill params from skill schema so the user
      // sees a populated params tab instead of an empty one. Control nodes
      // (start/end/branch/parallel/sub-workflow) don't map to a single
      // skill so skip.
      // suppressPrefill: when restoring blocks from saved JSON (load()),
      // the caller already overrides block.config with the persisted copy,
      // so running prefill afterwards only adds extra schema defaults that
      // didn't exist in the saved file — which then shows up as a phantom
      // dirty state ("unsaved changes" dialog on exit, even when the user
      // didn't touch anything). Skip prefill in that path.
      const skipPrefill = ["start", "end", "branch", "parallel", "sub-workflow"];
      if (!suppressPrefill && !skipPrefill.includes(type)) {
        const skillName = type.startsWith("mcp-") ? type : `mcp-${type}`;
        _prefillBlockParamsFromSchema(block, skillName);
      }
      // Parallel blocks need empty config bootstrap so the first render of
      // the params tab doesn't crash on missing .branches
      if (type === "parallel") {
        block.config = block.config || {};
        block.config.branches = block.config.branches || [];
        block.config.merge_output_var = block.config.merge_output_var || `parallel_${id}_merged`;
        block.config.on_fail = block.config.on_fail || "abort";
      }
      return block;
    }

    _createBlockEl(id, type, def, label) {
      const el = document.createElement("div");
      el.className = "wf-block";
      el.dataset.id = id;
      el.innerHTML = `
        <div class="wf-block-header" style="background:${def.color}">
          <span>${def.icon}</span> <span>${label || def.label}</span>
          <button class="wf-block-menu-btn" type="button" title="節點選單"
            data-block-menu="${id}"
            style="margin-left:auto;background:transparent;border:none;color:rgba(255,255,255,0.85);cursor:pointer;font-size:14px;line-height:1;padding:2px 4px;border-radius:3px;">⋯</button>
        </div>
        <div class="wf-block-body">
          <span class="wf-block-subtitle">${CATEGORIES[def.category]?.label || def.category}</span>
          <span class="wf-block-status"></span>
        </div>
        ${type !== "start" ? '<div class="wf-port wf-port-left" data-port="in" data-side="left"></div>' : ""}
        ${type !== "end" ? '<div class="wf-port wf-port-right" data-port="out" data-side="right"></div>' : ""}
        <div class="wf-port wf-port-top" data-port="in" data-side="top"></div>
        <div class="wf-port wf-port-bottom" data-port="out" data-side="bottom"></div>
      `;

      // Block mousedown → start drag
      // IMPORTANT: Use closest() so we still skip when the click lands on a
      // child element of a port (e.g. pseudo-element / hover scale artifact).
      // Previously `e.target.classList.contains("wf-port")` missed these
      // cases — some skill blocks couldn't be dragged because the mousedown
      // target resolved to an empty wrapper rather than the port itself.
      // Also: opening the property panel is now deferred to mouseup so that
      // fetch-triggered DOM updates can't interfere with drag-state setup.
      el.addEventListener("mousedown", e => {
        if (e.target.closest(".wf-port")) return;
        // Avoid interfering with text selection in input fields (prop panel overlay)
        if (e.target.closest("input, textarea, select, button")) return;
        e.stopPropagation();
        // Track whether this mousedown turned into a drag or a simple click
        this.dragging = {
          id,
          ox: e.clientX, oy: e.clientY,
          sx: parseInt(el.style.left), sy: parseInt(el.style.top),
          moved: false,
        };
      });

      // Click on block body → just SELECT (highlight only).
      // Settings / remove are now behind the ⋯ menu button.
      el.addEventListener("click", e => {
        if (e.target.closest(".wf-port")) return;
        if (e.target.closest(".wf-block-menu-btn")) return;  // menu handles itself
        if (e.target.closest("input, textarea, select, button")) return;
        this.select(id, { openPanel: false });
      });

      // 3-dot menu button → popup with 設定 / 移除
      const menuBtn = el.querySelector(".wf-block-menu-btn");
      if (menuBtn) {
        menuBtn.addEventListener("mousedown", e => e.stopPropagation());
        menuBtn.addEventListener("click", e => {
          e.stopPropagation();
          e.preventDefault();
          this.select(id, { openPanel: false });
          _showBlockContextMenu(menuBtn, id, this);
        });
      }

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

    select(id, opts = {}) {
      // opts.openPanel (default true) — whether to open the property panel.
      // Clicks on the block body only highlight; the 3-dot menu's "設定" is
      // what opens the panel.
      const openPanel = opts.openPanel !== false;
      this.blocks.forEach(b => b.el.classList.remove("selected"));
      this.selectedId = id;
      if (id != null) {
        const b = this.blocks.get(id);
        if (b) {
          b.el.classList.add("selected");
          if (openPanel) showWfPropPanel(b, this);
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
        // Only treat as drag once mouse moved >2px — avoids tiny jitter on click
        if (!d.moved && (Math.abs(dx) > 2 || Math.abs(dy) > 2)) d.moved = true;
        if (!d.moved) return;
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
      const wfId    = this._currentWfId || "default";
      const scope   = this._currentScope || "personal";
      const owner   = this._currentOwner || "";
      const wfName  = this._wfData?.name || wfId;

      // Phase 2 UX fix: replace native prompt() with the integrated wizard.
      //
      // Previous behavior used `window.prompt()` for "ad-hoc test message"
      // when there were no required user_input vars. Problems with that:
      //   - browser-native dialog, clashes with app style
      //   - no hint which variable the text binds to
      //   - pressing OK on empty field silently sends "" → skill dies with
      //     "Missing query" / similar confusing error
      //
      // New rule:
      //   - If workflow has ANY user_input source variables → pre-open the
      //     wizard so user fills them by name (required OR optional)
      //   - If workflow has none → execute immediately, no dialog
      const _vars = _getVariableList(this._wfData);
      const _userInputVars = _vars.filter(v => v && v.source === "user_input" && v.name);

      let prompt = "";
      let _userInputs = {};
      if (_userInputVars.length > 0) {
        const _missing = _userInputVars.map(v => ({
          name: v.name,
          type: v.type || "string",
          description: v.description || "",
          required: !!v.required,
          default: v.default_value || "",
        }));
        const collected = await _showInputsWizard(wfName, _missing);
        if (!collected) return;   // user cancelled — no animation to clear yet
        _userInputs = collected;
        // Use the first collected value as accumulated_context fallback so
        // skills that don't reference any variable still get something useful.
        const firstVal = Object.values(collected).find(v => v);
        if (firstVal) prompt = String(firstVal);
      }

      // Save current state first (skip validation so partial edits don't block test)
      await this.save(null, true);

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

      // Helper to clear animation on ANY exit path (error, gate block, success).
      // Previously only the success path cleared it → blocks stayed spinning
      // forever after a 422/428/network error. Capture in closure so every
      // `return` below can call it.
      const _clearRunningAnim = () => {
        this.blocks.forEach(b => {
          b.el.classList.remove("running");
          b.el.querySelector(".wf-block-status")?.classList.remove("running");
        });
        this.connections.forEach(c => c.el.classList.remove("active-flow"));
      };

      // Call backend execution with correct scope/owner.
      // Phase 2 Gate 1 flow:
      //   - 422 Unprocessable → env/skill problem, show hard error toast
      //   - 428 Precondition Required → missing_inputs, pop wizard + retry
      // Note: _userInputs was pre-filled above if workflow had user_input vars.
      const _eq = new URLSearchParams({ scope, owner });
      let _attempt = 0;
      while (true) {
        _attempt += 1;
        if (_attempt > 2) break;  // one retry after wizard fills inputs
        let resp;
        try {
          resp = await fetch(`/api/workflows/${encodeURIComponent(wfId)}/execute?${_eq}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ initial_prompt: prompt, model: null, inputs: _userInputs }),
          });
        } catch (e) {
          _clearRunningAnim();
          if (window.showToast) window.showToast("網路錯誤: " + e.message, "error");
          if (window._wfDashboard) window._wfDashboard.addLog(`「${wfName}」網路錯誤: ${e.message}`, "failed");
          return;
        }

        // Handle Gate 1 soft-block (need user inputs)
        if (resp.status === 428) {
          _clearRunningAnim();   // pause animation while wizard is up
          const ed = await resp.json().catch(() => ({}));
          const miss = ((ed.detail || {}).missing_inputs) || ed.missing_inputs || [];
          const collected = await _showInputsWizard(wfName, miss);
          if (!collected) return;  // user cancelled — already cleared
          _userInputs = { ..._userInputs, ...collected };
          continue;  // retry with inputs filled (animation will restart below on next pass? keep it simple: leave cleared)
        }

        // Handle Gate 0/1 hard-block
        if (resp.status === 422) {
          _clearRunningAnim();
          const ed = await resp.json().catch(() => ({}));
          const det = ed.detail || {};
          const errs = det.errors || (Array.isArray(det) ? det : []);
          const msg = errs.length
            ? errs.slice(0, 5).join("; ")
            : (typeof det === "string" ? det : "工作流檢查未通過");
          if (window.showToast) window.showToast("⛔ " + msg, "error");
          if (window._wfDashboard) window._wfDashboard.addLog(`「${wfName}」檢查未通過：${msg}`, "failed");
          return;
        }

        if (!resp.ok) {
          _clearRunningAnim();
          const txt = await resp.text().catch(() => "");
          if (window.showToast) window.showToast(`執行失敗 (${resp.status}): ${txt.slice(0, 120)}`, "error");
          return;
        }

        // ── Success path ──
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

        // Log results + show output
        const success = data.results?.filter(r => r.status === "success").length || 0;
        const errors  = data.results?.filter(r => r.status === "error").length || 0;
        const skipped = data.results?.filter(r => r.status === "skipped").length || 0;

        if (window._wfDashboard) {
          window._wfDashboard.addLog(
            `「${wfName}」執行完成 — ${success} 成功 / ${errors} 失敗 / ${skipped} 略過`,
            errors > 0 ? "failed" : "success"
          );
          // Show per-block errors
          data.results?.filter(r => r.status === "error").forEach(r => {
            window._wfDashboard.addLog(`  ✗ ${r.skill || r.type}: ${r.error || ""}`, "failed");
          });
        }

        if (window.showToast) {
          if (errors > 0) {
            // Surface the FIRST error message so user knows WHY it failed,
            // not just a useless "1 失敗" count.
            const firstErr = (data.results || []).find(r => r.status === "error");
            const detail = firstErr?.error || firstErr?.reason || "";
            const short = detail.length > 120 ? detail.slice(0, 120) + "…" : detail;
            const msg = short
              ? `❌ ${firstErr.skill || "步驟"}失敗：${short}`
              : `執行完成：${success} 成功 / ${errors} 失敗`;
            window.showToast(msg, "error");
          } else {
            window.showToast(`✓ 執行完成：${data.blocks_executed} 個節點`, "success");
          }
        }

        // Always show result panel if there was ANY output or error,
        // so user can inspect per-block details
        if ((data.final_output && data.final_output.trim()) || errors > 0) {
          _showWfRunResult(wfName, data);
        }
        break;  // success — exit the retry loop
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
      if (el) {
        const wfName = this._wfData?.name || "";
        const stats = `${this.blocks.size} 節點 · ${this.connections.length} 連接`;
        el.textContent = wfName ? `${wfName}  ·  ${stats}` : stats;
      }
      if (window._wfDashboard) window._wfDashboard.updateStats(this.blocks.size, this.connections.length);
      this._refreshParallelHints();
    }

    // Annotate blocks that fan-out (trigger parallel execution) or fan-in
    // (wait for multiple upstreams). Lets users see on the canvas exactly
    // which nodes participate in concurrency — no need for a dedicated
    // "parallel" block type.
    _refreshParallelHints() {
      if (!this.blocks) return;
      const outCount = new Map();
      const inCount  = new Map();
      this.connections.forEach(c => {
        outCount.set(c.from, (outCount.get(c.from) || 0) + 1);
        inCount.set(c.to,   (inCount.get(c.to)   || 0) + 1);
      });
      this.blocks.forEach(b => {
        const oldHint = b.el.querySelector(".wf-block-parallel-hint");
        if (oldHint) oldHint.remove();
        const fanOut = outCount.get(b.id) || 0;
        const fanIn  = inCount.get(b.id) || 0;
        const labels = [];
        if (fanOut > 1) labels.push(`🔀 並行 ${fanOut}`);
        if (fanIn > 1)  labels.push(`⇢ 匯合 ${fanIn}`);
        if (!labels.length) return;
        const hint = document.createElement("div");
        hint.className = "wf-block-parallel-hint";
        hint.textContent = labels.join(" · ");
        hint.style.cssText = "position:absolute;bottom:-18px;left:50%;transform:translateX(-50%);font-size:0.62rem;background:#8e44ad;color:#fff;padding:2px 8px;border-radius:10px;white-space:nowrap;pointer-events:none;z-index:2;box-shadow:0 1px 3px rgba(0,0,0,0.2);";
        b.el.appendChild(hint);
      });
    }

    // ── Persistence (Backend API with localStorage fallback) ──
    async save(name, skipValidation = false) {
      // ── Read-only guard (opened without edit permission) ──
      if (this.readOnly) {
        if (window.showToast) window.showToast("🔒 此工作流為檢視模式，無法儲存", "error");
        return false;
      }
      const oldFlowId = name || this._currentWfId || "default";
      const _wd = this._wfData || {};

      // ── Validation (unless explicitly skipped) ──
      if (!skipValidation) {
        // 1. Required: workflow name
        if (!_wd.name || !_wd.name.trim()) {
          if (window.showToast) window.showToast("⚠️ 工作流名稱為必填欄位，請開啟「工作流設定」填寫", "error");
          if (window._openWfSettings) window._openWfSettings();
          return false;
        }
        // 2. Required: description
        if (!_wd.description || !_wd.description.trim()) {
          if (window.showToast) window.showToast("⚠️ 工作流描述為必填欄位，請開啟「工作流設定」填寫", "error");
          if (window._openWfSettings) window._openWfSettings();
          return false;
        }
        // 3. Must have at least one Start AND one End block
        let hasStart = false, hasEnd = false;
        this.blocks.forEach(b => {
          if (b.type === "start") hasStart = true;
          if (b.type === "end")   hasEnd = true;
        });
        if (!hasStart || !hasEnd) {
          const missing = (!hasStart && !hasEnd) ? "[開始] 與 [結束]"
                        : !hasStart ? "[開始]" : "[結束]";
          if (window.showToast) window.showToast(`⚠️ 工作流必須包含 ${missing} 節點`, "error");
          return false;
        }
      }

      // ── Derive file ID ──
      // LOCK semantics: once a workflow has a canonical WorkflowK_ id (assigned
      // on first save), ALWAYS save back to that same id. Renaming the display
      // name must NOT create a new file + orphan the previous one.
      // Only fresh drafts (no _currentWfId yet, or temp wf-xxx) fall back to
      // a name-based slug so legacy Chinese filenames keep working.
      const displayName = (_wd.name || "").trim();
      const nameBasedId = _sanitizeWfId(displayName);
      const isCanonical = (s) => typeof s === "string" && /^WorkflowK_[A-Za-z0-9]{20}$/.test(s);
      let targetFlowId;
      if (isCanonical(this._currentWfId)) {
        // Locked — always write to the canonical id.
        targetFlowId = this._currentWfId;
      } else {
        targetFlowId = nameBasedId || oldFlowId;
      }

      const scope  = this._currentScope  || "personal";
      const owner  = this._currentOwner  || "";
      const data = {
        id:               targetFlowId,
        name:             displayName || targetFlowId,
        description:      _wd.description || "",
        icon:             _wd.icon || "",
        tags:             _wd.tags || [],
        trigger_keywords: _wd.trigger_keywords || [],
        variables:        _wd.variables || [],
        blocks:           Array.from(this.blocks.values()).map(b => ({ id: b.id, type: b.type, x: b.x, y: b.y, label: b.label, config: b.config || {} })),
        connections:      this.connections.map(c => ({ from: c.from, to: c.to })),
        trigger:          _wd.trigger || {},
        execution:        _wd.execution || {},
        security:         _wd.security || {},
        scope,
        owner,
      };

      // ── Save to backend ──
      try {
        const resp = await fetch(`/api/workflows/${encodeURIComponent(targetFlowId)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(data),
        });

        // Permission / quota / validation errors — show explicit message and
        // DO NOT fall back to localStorage (user's data would silently vanish
        // on refresh).
        if (resp.status === 422 || resp.status === 429 || resp.status === 403) {
          const errData = await resp.json().catch(() => ({}));
          const det = errData.detail || {};
          // Backend may put errors at detail.errors (Phase 2 Gate 0 shape) or detail as string (FastAPI default)
          const messages =
            (Array.isArray(det.errors) && det.errors) ||
            (Array.isArray(det) && det.map(e => e.msg || JSON.stringify(e))) ||
            (typeof det === "string" ? [det] : []) ||
            [`伺服器回應 ${resp.status}`];
          const icon = resp.status === 429 ? "⚠️ 配額已滿"
                     : resp.status === 403 ? "❌ 權限不足"
                     : "⛔ 無法儲存";
          const body = messages.slice(0, 5).join("；");
          if (window.showToast) window.showToast(`${icon}：${body}`, "error");
          else alert(`${icon}：${body}`);
          return false;
        }

        if (!resp.ok) throw new Error(`API save failed (${resp.status})`);

        // ── Pick up backend's final slug (Phase 1.5: filename = workflow_id) ──
        // Backend may rename the file from legacy Chinese to v2 slug.
        let serverResp = {};
        try { serverResp = await resp.clone().json(); } catch (_) {}
        const serverFinalId = serverResp.id || targetFlowId;

        // If the workflow was renamed — delete the OLD legacy file so we
        // don't end up with two copies. Skip if this is a fresh draft
        // (the temp ID never actually got persisted, so DELETE would 404).
        if (serverFinalId !== oldFlowId) {
          if (!this._isNewDraft) {
            try {
              const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
              await fetch(
                `/api/workflows/${encodeURIComponent(oldFlowId)}?scope=${encodeURIComponent(scope)}&owner=${encodeURIComponent(owner)}`,
                {
                  method: "DELETE",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    reason: `重新命名為 ${serverFinalId}`,
                    user_name: _u.name || _u.employee_id || "",
                    user_id:   _u.employee_id || _u.id || "",
                  }),
                }
              );
            } catch (_) { /* ignore delete errors */ }
          }
          this._currentWfId = serverFinalId;  // update in-memory ID
          this._isNewDraft = false;           // no longer a draft after first save
        }

        if (window.showToast) window.showToast(`「${data.name}」已儲存`, "success");
        // Sync toolbar display
        const infoEl = document.getElementById("wfInfoText");
        if (infoEl) infoEl.textContent = `${data.name}  ·  ${this.blocks.size} 節點 · ${this.connections.length} 連接`;
        this._markAsSaved();
        return true;

      } catch (e) {
        // Network / unknown errors only — localStorage fallback so user
        // doesn't lose in-progress work during network blips. Clearly
        // labelled so they know it's not synced.
        localStorage.setItem("wf_flow_" + targetFlowId, JSON.stringify(data));
        if (targetFlowId !== oldFlowId) {
          localStorage.removeItem("wf_flow_" + oldFlowId);
          this._currentWfId = targetFlowId;
        }
        if (window.showToast) window.showToast(`⚠️ 伺服器無法儲存，已暫存於本機（重整後會遺失）`, "warning");
        // Don't mark as saved — we want user to still see the dirty warning
        // next time they try to leave (their data is not really persisted)
        return true;
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
        const resp = await fetch(`/api/workflows/${encodeURIComponent(flowId)}?${_q}`);
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
      // Restore blocks (with config)
      // Pass suppressPrefill=true — the persisted config is authoritative;
      // running async schema-prefill would race and add fields that weren't
      // in the saved file, tripping the dirty-state check on exit.
      data.blocks.forEach(b => {
        const block = this.addBlock(b.type, b.x, b.y, b.label, /* suppressPrefill */ true);
        if (block && b.config) block.config = b.config;
      });
      // Refresh sub-workflow block subtitles — if any are present, fetch the
      // workflow list once to populate _wfNameCache so labels show names
      // instead of IDs without the user having to open each block first.
      const _hasSub = Array.from(this.blocks.values()).some(bl =>
        bl.type === "sub-workflow" && bl.config?.sub_workflow_id
      );
      if (_hasSub) {
        _primeSubWorkflowNameCache(this).then(() => {
          this.blocks.forEach(bl => {
            if (bl.type === "sub-workflow") _updateSubWorkflowBlockLabel(bl, this);
          });
        });
      }
      // Store workflow-level data for settings (include name for display)
      // v2 files use display_name; legacy used name. Accept either so the
      // settings modal doesn't ask user to re-enter the name.
      this._wfData = {
        name: data.display_name || data.name || "",
        description: data.description || "",
        icon: data.icon || "",
        tags: data.tags || [],
        trigger_keywords: data.trigger_keywords || (data.trigger && data.trigger.patterns) || [],
        variables: data.variables || [],
        trigger: data.trigger || {},
        execution: data.execution || {},
        security: data.security || {},
      };
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

      // Snapshot state as the "clean" reference for dirty detection
      this._savedSnapshot = this._snapshotState();
    }

    // ── Dirty tracking ────────────────────────────────────────
    // Capture everything users can mutate: block positions, types, configs,
    // connections, and workflow-level metadata.
    _snapshotState() {
      try {
        const blocks = Array.from(this.blocks.values()).map(b => ({
          id: b.id, type: b.type, x: b.x, y: b.y, label: b.label,
          config: b.config || {},
        }));
        const connections = this.connections.map(c => ({ from: c.from, to: c.to }));
        return JSON.stringify({ blocks, connections, wfData: this._wfData || {} });
      } catch (_) { return ""; }
    }

    hasUnsavedChanges() {
      if (this.readOnly) return false;  // read-only can't dirty
      const now = this._snapshotState();
      // First entry (no saved snapshot yet): dirty if we already have content
      if (this._savedSnapshot == null) {
        return this.blocks.size > 1 || this.connections.length > 0;
      }
      return now !== this._savedSnapshot;
    }

    // Called after a successful save to reset the clean baseline
    _markAsSaved() {
      this._savedSnapshot = this._snapshotState();
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
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatConns" style="color:var(--brand-google-green)">0</div><div class="wf-stat-label">連接數</div></div>
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatRuns" style="color:var(--kway-orange)">0</div><div class="wf-stat-label">執行次數</div></div>
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
      "mcp-txt-llm-analyzer": "TXT 分析", "mcp-pdf-llm-analyzer": "PDF 分析",
      "mcp-docx-llm-analyzer": "DOCX 分析", "mcp-spreadsheet-llm-analyzer": "試算表分析",
      "mcp-meeting-analyzer": "會議分析", "mcp-meeting-to-notion": "會議→Notion",
      "mcp-notion-crud": "Notion 操作", "mcp-notion-query": "Notion 查詢",
      "mcp-notion-todo-edit": "Notion ToDo", "mcp-transcribe": "音訊逐字稿",
      "mcp-high-risk-demo": "高風險示範",
    };
    return map[name] || name.replace("mcp-", "").replace(/-/g, " ");
  }
  function _guessIcon(name) {
    const map = {
      "mcp-txt-llm-analyzer": "📄", "mcp-pdf-llm-analyzer": "📕",
      "mcp-docx-llm-analyzer": "📘", "mcp-spreadsheet-llm-analyzer": "📊",
      "mcp-meeting-analyzer": "🎙", "mcp-meeting-to-notion": "📝",
      "mcp-notion-crud": "📋", "mcp-notion-query": "🔎",
      "mcp-notion-todo-edit": "✅", "mcp-transcribe": "🎤",
      "mcp-high-risk-demo": "⚠️",
    };
    return map[name] || "🔧";
  }
  function _guessColor(name) {
    const map = {
      "mcp-txt-llm-analyzer": "#607d8b", "mcp-pdf-llm-analyzer": "#c62828",
      "mcp-docx-llm-analyzer": "#1565c0", "mcp-spreadsheet-llm-analyzer": "#2e7d32",
      "mcp-meeting-analyzer": "#6200ea", "mcp-meeting-to-notion": "#6200ea",
      "mcp-notion-crud": "#000000", "mcp-notion-query": "#000000",
      "mcp-notion-todo-edit": "#000000", "mcp-transcribe": "#00897b",
      "mcp-high-risk-demo": "#ff6f00",
    };
    return map[name] || "#546e7a";
  }
  function _guessCategory(name, desc) {
    if (name.includes("analyzer")) return "analysis";
    if (name.includes("notion") || name.includes("meeting")) return "analysis";
    if (name.includes("transcribe")) return "analysis";
    if (name.includes("schedule") || name.includes("calendar")) return "automation";
    if (name.includes("search")) return "search";
    if (name.includes("python") || name.includes("image") || name.includes("executor")) return "compute";
    return "analysis";
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

    // Reset to first tab
    panel.querySelectorAll(".wf-prop-tab").forEach(t => t.classList.toggle("active", t.dataset.tab === "props"));
    ["wfPropTabProps", "wfPropTabParams", "wfPropTabExec"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = id === "wfPropTabProps" ? "" : "none";
    });

    // ── Tab 1: Properties ──
    const fieldsDiv = panel.querySelector(".wf-prop-fields");
    fieldsDiv.innerHTML = `
      <div class="wf-prop-field"><label>名稱</label><input type="text" value="${block.label}" data-field="label" /></div>
      <div class="wf-prop-field"><label>類型</label><input type="text" value="${block.type}" readonly /></div>
    `;
    fieldsDiv.querySelector('[data-field="label"]').addEventListener("change", e => {
      block.label = e.target.value;
      block.el.querySelector(".wf-block-header span:last-child").textContent = e.target.value;
    });

    // ── Tab 2: Parameters (Block param mapping) ──
    const paramsDiv = document.getElementById("wfPropTabParams");
    if (paramsDiv) {
      const isControl = ["start", "end", "branch"].includes(block.type);
      if (isControl) {
        paramsDiv.innerHTML = `<div style="padding:10px;font-size:0.72rem;color:var(--text-tertiary);">控制節點無參數</div>`;
      } else if (block.type === "parallel") {
        // Phase 4: parallel branches editor
        _renderParallelBlockEditor(block, paramsDiv, fd);
      } else if (block.type === "sub-workflow") {
        // Phase 4: sub-workflow picker
        _renderSubWorkflowBlockEditor(block, paramsDiv, fd);
      } else {
        const skillName = block.type.startsWith("mcp-") ? block.type : "mcp-" + block.type;

        // Render params immediately with current config (loading state)
        _renderBlockParams(block, paramsDiv, fd, skillName, undefined);

        // Async: fetch skill parameter schema and re-render with real param names.
        // If the block has NO params yet, also retro-apply the quick-preset
        // prefill so legacy blocks (created before the prefill feature, or
        // before SKILL.md had a parameters schema) show typical defaults.
        fetch(`/skills/${skillName}`)
          .then(r => r.ok ? r.json() : null)
          .then(async (skillData) => {
            const schema = skillData?.metadata?.parameters || null;
            const hasParams = block.config?.params && Object.keys(block.config.params).length > 0;
            if (!hasParams) {
              await _prefillBlockParamsFromSchema(block, skillName);
            }
            _renderBlockParams(block, paramsDiv, fd, skillName, schema);
          })
          .catch(() => {
            _renderBlockParams(block, paramsDiv, fd, skillName, null);
          });
      }
    }

    // ── Tab 3: Execution config ──
    const execDiv = document.getElementById("wfPropTabExec");
    if (execDiv) {
      const cfg = block.config || {};
      const hasCustomCfg = cfg.model || cfg.timeout || cfg.on_error;
      execDiv.innerHTML = `
        <div class="wf-prop-field"><label>模型 (留空=全域設定)</label>
          <select data-field="model" onchange="window._updateBlockConfig(${block.id},'model',this.value)">
            <option value="" ${!cfg.model ? "selected" : ""}>自動</option>
            <option value="gpt-4o" ${cfg.model === "gpt-4o" ? "selected" : ""}>gpt-4o</option>
            <option value="gpt-4.1" ${cfg.model === "gpt-4.1" ? "selected" : ""}>gpt-4.1</option>
            <option value="gpt-4.1-mini" ${cfg.model === "gpt-4.1-mini" ? "selected" : ""}>gpt-4.1-mini</option>
            <option value="gpt-4.1-nano" ${cfg.model === "gpt-4.1-nano" ? "selected" : ""}>gpt-4.1-nano</option>
          </select></div>
        <div class="wf-prop-field"><label>逾時 (秒)</label>
          <input type="number" value="${cfg.timeout || ""}" placeholder="預設 30"
            onchange="window._updateBlockConfig(${block.id},'timeout',this.value?parseInt(this.value):null)"
            oninput="window._updateBlockConfig(${block.id},'timeout',this.value?parseInt(this.value):null)" /></div>
        <div class="wf-prop-field"><label>失敗處理</label>
          <select onchange="window._updateBlockConfig(${block.id},'on_error',this.value)">
            <option value="" ${!cfg.on_error ? "selected" : ""}>跟隨全域</option>
            <option value="stop" ${cfg.on_error === "stop" ? "selected" : ""}>停止</option>
            <option value="skip" ${cfg.on_error === "skip" ? "selected" : ""}>跳過</option>
            <option value="retry" ${cfg.on_error === "retry" ? "selected" : ""}>重試</option>
          </select></div>
        <div class="wf-prop-hint" style="margin-top:8px;font-size:0.78rem;color:var(--text-tertiary);">
          ※ 節點設定在儲存後生效；模型設定影響此節點的 LLM 呼叫。${hasCustomCfg ? ' <span style="color:var(--brand-google-green);">✓ 已有自訂設定</span>' : ""}
        </div>
      `;
    }

    // Anchor panel to block — follow it on canvas pan/zoom/scroll via rAF.
    // Previous behavior positioned once and stayed fixed in viewport; if the
    // user scrolled the canvas the panel drifted away from its target block.
    _startPropPanelAnchor(block, panel);
    panel.classList.remove("hidden");

    // Make wheel scroll inside the panel stay inside the panel — the canvas
    // viewport has its own scroll which would otherwise compete. The panel
    // already has overflow-y:auto so default behaviour is correct; we only
    // need to stop the event bubbling to avoid upstream handlers firing.
    if (!panel._wheelStopAttached) {
      panel.addEventListener("wheel", e => e.stopPropagation(), { passive: true });
      panel._wheelStopAttached = true;
    }
  }

  // ── Panel anchor tracker ──────────────────────────────────────────
  let _panelAnchorRaf = null;
  let _panelAnchorBlock = null;
  let _panelAnchorEl = null;
  function _startPropPanelAnchor(block, panel) {
    _stopPropPanelAnchor();
    _panelAnchorBlock = block;
    _panelAnchorEl = panel;
    const tick = () => {
      if (!_panelAnchorBlock || !_panelAnchorEl || _panelAnchorEl.classList.contains("hidden")) {
        _panelAnchorRaf = null;
        return;
      }
      const bEl = _panelAnchorBlock.el;
      if (!bEl || !bEl.isConnected) {
        _stopPropPanelAnchor();
        return;
      }
      const r = bEl.getBoundingClientRect();
      const pw = _panelAnchorEl.offsetWidth || 280;
      const ph = _panelAnchorEl.offsetHeight || 300;
      // Default: place to the right of the block, vertically aligned with top
      let left = r.right + 12;
      let top  = r.top;
      // If overflow right → place to the left
      if (left + pw > window.innerWidth - 8) left = Math.max(8, r.left - pw - 12);
      // Clamp to viewport vertically, leave a little margin for toolbar
      const maxTop = window.innerHeight - ph - 16;
      if (top > maxTop) top = Math.max(60, maxTop);
      if (top < 60) top = 60;
      _panelAnchorEl.style.left = left + "px";
      _panelAnchorEl.style.top  = top + "px";
      _panelAnchorRaf = requestAnimationFrame(tick);
    };
    _panelAnchorRaf = requestAnimationFrame(tick);
  }

  function _stopPropPanelAnchor() {
    if (_panelAnchorRaf) {
      cancelAnimationFrame(_panelAnchorRaf);
      _panelAnchorRaf = null;
    }
    _panelAnchorBlock = null;
    _panelAnchorEl = null;
  }

  function closeWfPropPanel() {
    const panel = document.getElementById("wfPropPanel");
    if (panel) panel.classList.add("hidden");
    _stopPropPanelAnchor();
  }

  // ── Block 3-dot context menu (設定 / 移除) ──────────────────────────
  // Anchored to the ⋯ button in the block header. Only one can be open at a
  // time; any outside click or ESC closes it. Keeps the block interaction
  // simple: single click = highlight, ⋯ = actions.
  function _showBlockContextMenu(anchorBtn, blockId, fd) {
    document.getElementById("wfBlockCtxMenu")?.remove();

    const menu = document.createElement("div");
    menu.id = "wfBlockCtxMenu";
    menu.style.cssText = "position:fixed;z-index:9800;min-width:110px;background:#fff;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.12);padding:2px;font-size:0.82rem;";
    menu.innerHTML = `
      <button type="button" data-act="config" style="display:block;width:100%;text-align:left;padding:5px 12px;background:transparent;border:none;border-radius:4px;cursor:pointer;color:var(--text-primary);">設定</button>
      <button type="button" data-act="remove" style="display:block;width:100%;text-align:left;padding:5px 12px;background:transparent;border:none;border-radius:4px;cursor:pointer;color:var(--color-error);">移除</button>
    `;

    // Position just below the anchor button, kept inside viewport
    const rect = anchorBtn.getBoundingClientRect();
    document.body.appendChild(menu);
    const mw = menu.offsetWidth || 140;
    const mh = menu.offsetHeight || 80;
    let left = rect.right - mw;
    let top  = rect.bottom + 4;
    if (left < 4) left = 4;
    if (left + mw > window.innerWidth - 4) left = window.innerWidth - mw - 4;
    if (top + mh > window.innerHeight - 4) top = rect.top - mh - 4;  // flip above
    menu.style.left = left + "px";
    menu.style.top  = top + "px";

    // Hover highlight
    menu.querySelectorAll("button").forEach(btn => {
      btn.addEventListener("mouseenter", () => { btn.style.background = btn.dataset.act === "remove" ? "#fef2f2" : "#f1f5f9"; });
      btn.addEventListener("mouseleave", () => { btn.style.background = "transparent"; });
    });

    const close = () => {
      menu.remove();
      document.removeEventListener("mousedown", outsideHandler, true);
      document.removeEventListener("keydown", escHandler, true);
    };
    const outsideHandler = (e) => {
      if (!menu.contains(e.target) && e.target !== anchorBtn) close();
    };
    const escHandler = (e) => { if (e.key === "Escape") close(); };
    // Defer binding so the triggering click doesn't immediately close us
    setTimeout(() => {
      document.addEventListener("mousedown", outsideHandler, true);
      document.addEventListener("keydown", escHandler, true);
    }, 0);

    menu.querySelector('[data-act="config"]').addEventListener("click", (e) => {
      e.stopPropagation();
      close();
      const b = fd.blocks.get(blockId);
      if (b) showWfPropPanel(b, fd);
    });

    menu.querySelector('[data-act="remove"]').addEventListener("click", (e) => {
      e.stopPropagation();
      close();
      const b = fd.blocks.get(blockId);
      if (!b) return;
      const label = b.label || b.type;
      // Prevent deleting start/end — they're structurally required
      if (b.type === "start" || b.type === "end") {
        if (window.showToast) window.showToast("「" + label + "」是必要節點，無法移除", "error");
        return;
      }
      _confirmDeleteBlock(label, () => {
        fd.deleteBlock(blockId);
        closeWfPropPanel();
      });
    });
  }

  // Custom confirm dialog for block deletion — replaces browser-native
  // confirm() which looks out of place and can't be styled.
  function _confirmDeleteBlock(label, onConfirm) {
    document.getElementById("wfBlockDeleteConfirm")?.remove();
    const mask = document.createElement("div");
    mask.id = "wfBlockDeleteConfirm";
    mask.style.cssText = "position:fixed;inset:0;z-index:9900;background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center;";
    const safeLabel = (label || "").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    mask.innerHTML = `
      <div style="background:#fff;width:var(--modal-width-sm);max-width:92vw;border-radius:var(--modal-radius);box-shadow:0 20px 60px rgba(0,0,0,0.25);padding:20px 22px;">
        <div style="font-size:1rem;font-weight:700;color:var(--text-primary);margin-bottom:10px;">移除節點</div>
        <div style="font-size:0.85rem;color:var(--text-secondary);line-height:1.55;margin-bottom:18px;">
          確定要移除「<strong>${safeLabel}</strong>」這個節點嗎？<br>
          連接到它的線會一併刪除。
        </div>
        <div style="text-align:right;">
          <button id="wfBlockDelCancel" type="button" style="padding:7px 16px;margin-right:8px;background:transparent;color:var(--text-muted);border:1px solid #e2e8f0;border-radius:6px;font-size:0.82rem;cursor:pointer;">取消</button>
          <button id="wfBlockDelOk" type="button" style="padding:7px 16px;background:var(--color-error);color:#fff;border:none;border-radius:6px;font-size:0.82rem;font-weight:600;cursor:pointer;">移除</button>
        </div>
      </div>
    `;
    document.body.appendChild(mask);

    const cleanup = () => {
      mask.remove();
      document.removeEventListener("keydown", keyHandler, true);
    };
    const keyHandler = (e) => {
      if (e.key === "Escape") cleanup();
      else if (e.key === "Enter") { cleanup(); onConfirm(); }
    };
    setTimeout(() => document.addEventListener("keydown", keyHandler, true), 0);

    mask.addEventListener("click", e => { if (e.target === mask) cleanup(); });
    mask.querySelector("#wfBlockDelCancel").addEventListener("click", cleanup);
    mask.querySelector("#wfBlockDelOk").addEventListener("click", () => {
      cleanup();
      try { onConfirm(); } catch (_) {}
    });
    // Focus the cancel button by default (safer default)
    setTimeout(() => mask.querySelector("#wfBlockDelCancel")?.focus(), 50);
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

    // Any transition out of canvas should remove the read-only banner
    document.getElementById("wfReadOnlyBanner")?.remove();

    if (_landingOpen) {
      // Landing is open → close landing, back to chat
      body.classList.remove("wf-mode");
      body.classList.remove("wf-readonly");
      if (btn) btn.classList.remove("active", "is-active");
      if (_overlay) _overlay.classList.remove("open");
      const _pp = document.getElementById("wfPropPanel");
      if (_pp) _pp.classList.add("hidden");

    } else if (isActive) {
      // In Designer/Skill Editor → back to Landing (not chat)
      if (_skillEditMode && window._wfSkillEditor?._hasUnsavedChanges()) {
        const ok = await _showConfirmAsync("技能尚未儲存，確定要退出嗎？");
        if (!ok) return;
      }

      // Workflow Designer dirty check — 3-option dialog (save/discard/cancel)
      if (!_skillEditMode && window._wfDesigner?.hasUnsavedChanges?.()) {
        const wfName = window._wfDesigner._wfData?.name || "此工作流";
        const action = await _showDirtyExitDialog(
          `「${_escHtml(wfName)}」有未儲存的變更。<br>` +
          `「儲存並退出」會先驗證流程合規（需包含 [開始]/[結束] 節點、名稱與描述），驗證失敗將保留在畫布讓您修正。`
        );
        if (action === "cancel") return;
        if (action === "save") {
          // Try save; if validation fails (returns false), stay on canvas
          const ok = await window._wfDesigner.save();
          if (!ok) {
            if (window.showToast) window.showToast("儲存失敗，請修正後再退出", "error");
            return;
          }
        }
        // "discard" falls through and exits without saving
      }
      body.classList.remove("wf-mode");
      body.classList.remove("wf-readonly");
      _skillEditMode = false;
      ["wfSkillEditArea", "wfCanvasArea", "wfPaletteWrap", "wfDashboardWrap"].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.style.display = ""; el.classList.remove("visible"); }
      });
      // Close property panel
      const _propPanel = document.getElementById("wfPropPanel");
      if (_propPanel) _propPanel.classList.add("hidden");
      // Clean up designer — destroy removes all event listeners, preventing accumulation
      if (window._wfDesigner) {
        window._wfDesigner.destroy();
        window._wfDesigner.blocks.forEach(b => b.el.remove());
        window._wfDesigner.blocks.clear();
        window._wfDesigner.connections.forEach(c => c.el.remove());
        window._wfDesigner.connections = [];
        window._wfDesigner = null;
      }
      _showWorkflowLanding();

    } else {
      // Not in workflow → open Landing
      // Add both legacy `active` (top-bar styling) and `is-active` (sidebar
      // styling) so the button lights up regardless of which location hosts
      // it — the button was moved to the sidebar 2026-04-23.
      if (btn) btn.classList.add("active", "is-active");
      _showWorkflowLanding();
    }
  }

  // ── Workflow Landing (fixed overlay) ────────────────────────────
  const _WF_COLORS = ["#34a853","#1a9aaa","#4285f4","#ea4335","#fbbc04","#8b5cf6","#ec4899","#f97316"];

  function _escHtml(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  // Workflow final_output commonly ends with a markdown download link
  // emitted by server/services/workflow_executor.py::_format_terminal_output.
  // _escHtml alone would turn the link into visible text — this helper
  // escapes HTML first, then selectively unescapes [label](url) patterns
  // into clickable anchors (download trigger for /downloads/* paths).
  function _renderFinalOutputHtml(text) {
    let out = _escHtml(text || "");
    out = out.replace(
      /\[([^\]]+?)\]\(((?:https?:\/\/|\/)[^\s)]+)\)/g,
      (_m, label, url) => {
        const isDl = /\/downloads\//.test(url) || /\.(pdf|docx|xlsx|csv|zip)(?:$|\?)/i.test(url);
        const attrs = isDl ? ' download' : ' target="_blank" rel="noopener"';
        return `<a href="${url}"${attrs} style="color:var(--kway-blue);text-decoration:underline;">${label}</a>`;
      }
    );
    out = out.replace(
      /(^|[\s>])(https?:\/\/[^\s<]+)/g,
      (_m, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener" style="color:var(--kway-blue);text-decoration:underline;">${url}</a>`
    );
    return out;
  }

  async function _showWorkflowLanding() {
    const overlay = document.getElementById("wfLandingOverlay");
    if (!overlay) return;
    overlay.classList.add("open");
    // Banner only belongs to the canvas — clear it when returning to landing
    document.getElementById("wfReadOnlyBanner")?.remove();
    // Remove anti-flash if present
    const af = document.getElementById("wfAntiFlash"); if (af) af.remove();
    const body = document.querySelector(".page-chat-body"); if (body) { body.style.visibility = "visible"; body.classList.remove("wf-readonly"); }

    // Clear grid immediately so stale cards don't show while fetching
    const _gridPre = document.getElementById("wfLandingGrid");
    if (_gridPre) _gridPre.innerHTML = "";

    // Fetch workflow list
    const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
    const _owner    = _u.employee_id || _u.id || "";
    const _deptCode = _u.dept_code || _u.department_code || _u.dept || "";
    let workflows = [];
    try {
      const _lq = new URLSearchParams();
      if (_owner)    _lq.set("owner",     _owner);
      if (_deptCode) _lq.set("dept_code", _deptCode);
      const resp = await fetch(`/api/workflows?${_lq}`);
      if (resp.ok) workflows = (await resp.json()).workflows || [];
    } catch (_) {}

    const grid = document.getElementById("wfLandingGrid");
    const leftPanel = document.getElementById("wfLandingLeft");
    const rightPanel = document.getElementById("wfLandingRight");

    // ── Restore previous landing state (view-state memory) ──
    // Preserves scope filter + search query between canvas trips so users
    // returning via 回選單 land back on the tab they were browsing.
    let _savedState = {};
    try {
      _savedState = JSON.parse(sessionStorage.getItem("kway_wf_landing_state") || "{}") || {};
    } catch (_) { _savedState = {}; }
    const _savedScope = _savedState.scope || "all";
    const _savedQuery = _savedState.query || "";

    // ── Left Panel ──
    if (leftPanel) {
      const sc = { all: workflows.length, system: 0, department: 0, personal: 0 };
      workflows.forEach(wf => { sc[wf.scope || "personal"]++; });
      const _isActive = (s) => s === _savedScope ? ' active' : '';
      leftPanel.innerHTML = `
        <div class="wf-lp-section"><div class="wf-lp-title">搜尋</div>
          <input class="wf-lp-search" id="wfLandingSearch" type="text" placeholder="搜尋工作流名稱..." autocomplete="off" value="${_escHtml(_savedQuery)}" /></div>
        <div class="wf-lp-section"><div class="wf-lp-title">分類</div>
          <div class="wf-lp-filter">
            <div class="wf-lp-filter-item${_isActive("all")}" data-scope="all"><span class="wf-lp-filter-dot" style="background:var(--text-muted);"></span><span>全部</span><span class="wf-lp-filter-count">${sc.all}</span></div>
            <div class="wf-lp-filter-item${_isActive("system")}" data-scope="system"><span class="wf-lp-filter-dot" style="background:var(--color-success);"></span><span>系統</span><span class="wf-lp-filter-count">${sc.system}</span></div>
            <div class="wf-lp-filter-item${_isActive("department")}" data-scope="department"><span class="wf-lp-filter-dot" style="background:var(--brand-google-blue);"></span><span>部門</span><span class="wf-lp-filter-count">${sc.department}</span></div>
            <div class="wf-lp-filter-item${_isActive("personal")}" data-scope="personal"><span class="wf-lp-filter-dot" style="background:#1a9aaa;"></span><span>個人</span><span class="wf-lp-filter-count">${sc.personal}</span></div>
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
          _saveWfLandingState();
          _filterWfCards();
        });
      });
      const si = leftPanel.querySelector("#wfLandingSearch");
      if (si) si.addEventListener("input", () => { _saveWfLandingState(); _filterWfCards(); });
    }

    // ── Center Cards (upgraded with description, trigger, updated_at) ──
    if (grid) {
      let html = `<div class="wf-landing-card-new" onclick="_showNewWorkflowScopePicker()">
        <div class="wf-landing-card-new-inner"><div class="wf-landing-card-new-icon">+</div><div class="wf-landing-card-new-label">新增工作流</div></div></div>
        <div class="wf-landing-card-new" style="background:linear-gradient(135deg,var(--wf-purple) 0%,var(--wf-purple-dark) 100%);color:#fff;" onclick="_showLLMGenerateModal()">
        <div class="wf-landing-card-new-inner" style="color:#fff;"><div class="wf-landing-card-new-icon" style="color:#fff;">✨</div><div class="wf-landing-card-new-label" style="color:#fff;">一次性智能流程</div></div></div>`;
      workflows.forEach((wf, i) => {
        const color = _WF_COLORS[i % _WF_COLORS.length];
        const scope = wf.scope || "personal";
        const sl = scope === "system" ? "系統" : scope === "department" ? "部門" : "個人";
        const desc = wf.description ? `<div class="wf-landing-card-desc">${_escHtml(wf.description).substring(0, 60)}</div>` : "";
        const trigBadge = wf.has_trigger ? '<span class="wf-landing-card-badge wf-badge-trigger">觸發</span>' : "";
        const kwBadge = (wf.trigger_keywords?.length) ? `<span class="wf-landing-card-badge wf-badge-kw">${wf.trigger_keywords.length} 關鍵詞</span>` : "";
        const varBadge = wf.variables_count ? `<span class="wf-landing-card-badge wf-badge-var">${wf.variables_count} 變數</span>` : "";
        const updAt = wf.updated_at ? new Date(wf.updated_at).toLocaleDateString("zh-TW", {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "";
        // Department workflows use dept_code as the directory key, not employee_id
        const _cardOwner = scope === "department" ? _deptCode : scope === "system" ? "" : _owner;
        const _wfNameEsc = _escHtml(wf.name || wf.id).replace(/'/g, "&#39;");
        html += `<div class="wf-landing-card" data-scope="${scope}" data-name="${_escHtml(wf.name || wf.id)}" onclick="_openWorkflow('${wf.id}','${scope}','${_cardOwner}')">
          <div class="wf-landing-card-header" style="background:${color};">
            ${_escHtml(wf.name || wf.id)}${desc}
            <button class="wf-card-delete-btn" title="刪除工作流"
              onclick="event.stopPropagation();_showWfDeleteConfirm('${wf.id}','${_wfNameEsc}','${scope}','${_cardOwner}')">✕</button>
          </div>
          <div class="wf-landing-card-body">
            <div class="wf-landing-card-badges">${trigBadge}${kwBadge}${varBadge}</div>
            <div class="wf-landing-card-meta">
              <span>${sl}</span><span class="wf-landing-card-meta-dot"></span><span>${wf.block_count||0} 節點</span><span class="wf-landing-card-meta-dot"></span><span>${wf.connection_count||0} 連接</span>
              ${updAt ? `<span class="wf-landing-card-meta-dot"></span><span>${updAt}</span>` : ""}
            </div>
          </div></div>`;
      });
      grid.innerHTML = html;
    }

    // ── Right Panel (upgraded with real execution logs) ──
    if (rightPanel) {
      // Fetch real execution logs
      let logsHtml = '<div class="wf-rp-empty">尚無執行紀錄</div>';
      try {
        const logResp = await fetch("/api/workflows/logs/recent?limit=10");
        if (logResp.ok) {
          const logData = await logResp.json();
          if (logData.logs?.length) {
            logsHtml = logData.logs.map(log => {
              const t = new Date(log.timestamp).toLocaleString("zh-TW", {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
              const statusDot = log.status === "success" ? "success" : "failed";
              return `<div class="wf-rp-log-item">
                <span class="wf-rp-log-dot ${statusDot}"></span>
                <span class="wf-rp-log-name">${_escHtml(log.workflow_name || log.workflow_id)}</span>
                <span class="wf-rp-log-time">${t}</span>
              </div>`;
            }).join("");
          }
        }
      } catch (_) {}

      rightPanel.innerHTML = `
        <div class="wf-rp-section"><div class="wf-rp-title">最近執行紀錄</div>${logsHtml}</div>
        <div class="wf-rp-section"><div class="wf-rp-title">LINE Bot 觸發指令</div>
          ${workflows.length ? workflows.slice(0,5).map(wf => `<div class="wf-rp-line-cmd">「執行 ${_escHtml(wf.name||wf.id)}」</div>`).join("") : '<div class="wf-rp-empty">建立工作流後可透過 LINE 觸發</div>'}
        </div>
        <div class="wf-rp-section"><div class="wf-rp-title">排程狀態</div><div class="wf-rp-empty">尚未設定排程</div></div>`;
    }

    // Apply saved filter/search immediately so returning user sees their previous view
    _filterWfCards();

    // Restore scroll position (if any) after a tiny delay for layout to settle
    if (_savedState.scrollTop != null) {
      setTimeout(() => {
        const _center = document.getElementById("wfLandingCenter") || grid?.parentElement;
        if (_center) _center.scrollTop = _savedState.scrollTop;
      }, 30);
    }
  }

  // Persist the current landing view state — scope tab, search box, scroll
  function _saveWfLandingState() {
    try {
      const scope = document.querySelector(".wf-lp-filter-item.active")?.dataset?.scope || "all";
      const query = document.getElementById("wfLandingSearch")?.value || "";
      const _center = document.getElementById("wfLandingCenter");
      const scrollTop = _center ? _center.scrollTop : 0;
      sessionStorage.setItem("kway_wf_landing_state", JSON.stringify({ scope, query, scrollTop }));
    } catch (_) {}
  }
  // Save scroll on unload-ish events too (clicking a card → opens workflow)
  document.addEventListener("click", (e) => {
    if (e.target.closest(".wf-landing-card")) _saveWfLandingState();
  }, true);

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

  // ── Workflow Run Result Panel ──
  // ── Phase 2 Gate 1: Missing-inputs wizard ──
  // Server returned 428 Precondition Required with a list of required global_inputs.
  // Pop a modal to collect them, resolve with the filled object (or null if cancelled).
  function _showInputsWizard(wfName, missingInputs) {
    return new Promise(resolve => {
      document.getElementById("wfInputsWizardOverlay")?.remove();
      const overlay = document.createElement("div");
      overlay.id = "wfInputsWizardOverlay";
      overlay.style.cssText = "position:fixed;inset:0;z-index:8500;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;";

      const rows = (missingInputs || []).map(inp => {
        const desc = inp.description ? `<div style="font-size:0.7rem;color:var(--text-muted);margin-top:3px;">${_escHtml(inp.description)}</div>` : "";
        const isMultiline = (inp.type || "").toLowerCase() === "text" || (inp.description || "").length > 60;
        const defVal = _escHtml(inp.default || "");
        const field = isMultiline
          ? `<textarea rows="3" id="wfWizInp_${_escHtml(inp.name)}" placeholder="${defVal}" style="width:100%;padding:8px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:0.85rem;resize:vertical;">${defVal}</textarea>`
          : `<input type="text" id="wfWizInp_${_escHtml(inp.name)}" value="${defVal}" style="width:100%;padding:8px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:0.85rem;" />`;
        const required = inp.required === false ? '<span style="color:var(--text-tertiary);font-size:0.72rem;">（選填）</span>' : '<span style="color:var(--color-error);">*</span>';
        return `<div style="margin-bottom:12px;">
          <label style="display:block;font-size:0.78rem;font-weight:600;color:var(--text-primary);margin-bottom:4px;">
            <code style="background:var(--bg-hover);padding:1px 5px;border-radius:3px;">${_escHtml(inp.name)}</code>
            ${required}
          </label>
          ${field}
          ${desc}
        </div>`;
      }).join("");

      overlay.innerHTML = `
        <div style="background:#fff;border-radius:var(--modal-radius);box-shadow:var(--modal-shadow);padding:var(--modal-padding);width:var(--modal-width-md);max-width:92vw;max-height:85vh;overflow-y:auto;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="font-size:1.1rem;">📝</span>
            <div style="font-size:1rem;font-weight:700;">執行「${_escHtml(wfName)}」需要以下輸入</div>
          </div>
          <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:16px;">填寫後按「確認」開始執行</div>
          ${rows || '<div style="color:var(--text-tertiary);">(沒有待輸入欄位)</div>'}
          <footer style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px;">
            <button id="wfWizCancel" style="padding:8px 18px;border-radius:8px;background:transparent;color:var(--text-muted);border:1px solid #e2e8f0;cursor:pointer;">取消</button>
            <button id="wfWizSubmit" style="padding:8px 18px;border-radius:8px;background:var(--color-info);color:#fff;border:none;font-weight:600;cursor:pointer;">確認並執行</button>
          </footer>
        </div>`;
      document.body.appendChild(overlay);

      const _cleanup = () => overlay.remove();
      overlay.addEventListener("click", e => { if (e.target === overlay) { _cleanup(); resolve(null); } });
      overlay.querySelector("#wfWizCancel").onclick = () => { _cleanup(); resolve(null); };
      overlay.querySelector("#wfWizSubmit").onclick = () => {
        const collected = {};
        let hasMissing = false;
        (missingInputs || []).forEach(inp => {
          const el = document.getElementById("wfWizInp_" + inp.name);
          const v = (el?.value || "").trim();
          if (!v && inp.required !== false) {
            hasMissing = true;
            if (el) el.style.borderColor = "var(--color-error)";
          } else {
            collected[inp.name] = v;
            if (el) el.style.borderColor = "#cbd5e1";
          }
        });
        if (hasMissing) {
          if (window.showToast) window.showToast("請填寫所有必填欄位", "error");
          return;
        }
        _cleanup();
        resolve(collected);
      };

      // Auto-focus the first input
      setTimeout(() => {
        const firstInput = overlay.querySelector("input, textarea");
        if (firstInput) firstInput.focus();
      }, 50);
    });
  }

  function _showWfRunResult(wfName, data) {
    document.getElementById("wfRunResultOverlay")?.remove();
    const overlay = document.createElement("div");
    overlay.id = "wfRunResultOverlay";
    overlay.style.cssText = "position:fixed;inset:0;z-index:8000;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;";
    const blockRows = (data.results || [])
      .filter(r => r.type !== "start" && r.type !== "end")
      .map(r => {
        const icon   = r.status === "success" ? "✓" : r.status === "error" ? "✗" : "—";
        const color  = r.status === "success" ? "#34a853" : r.status === "error" ? "#ea4335" : "#999";
        const preview = r.output_preview ? `<div style="font-size:0.72rem;color:#555;margin-top:3px;white-space:pre-wrap;max-height:60px;overflow:hidden;">${_escHtml(r.output_preview)}</div>` : "";
        return `<div style="padding:8px 0;border-bottom:1px solid #eee;">
          <span style="color:${color};font-weight:700;">${icon}</span>
          <span style="font-size:0.8rem;font-weight:600;margin-left:6px;">${_escHtml(r.skill || r.type)}</span>
          <span style="font-size:0.72rem;color:#888;margin-left:6px;">${r.status}${r.model_used ? " · " + r.model_used : ""}</span>
          ${preview}
          ${r.error ? `<div style="font-size:0.72rem;color:var(--brand-google-red);margin-top:3px;">${_escHtml(r.error)}</div>` : ""}
        </div>`;
      }).join("");
    const outputHtml = data.final_output
      ? `<div style="margin-top:12px;"><div style="font-size:0.75rem;font-weight:700;color:#555;margin-bottom:6px;">最終輸出</div>
         <div style="background:var(--bg-sidebar);border-radius:8px;padding:12px;font-size:0.8rem;white-space:pre-wrap;max-height:220px;overflow-y:auto;">${_renderFinalOutputHtml(data.final_output)}</div></div>`
      : "";
    overlay.innerHTML = `
      <div style="background:#fff;border-radius:var(--modal-radius);box-shadow:var(--modal-shadow);padding:24px 24px 18px;width:var(--modal-width-md);max-width:92vw;max-height:85vh;overflow-y:auto;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
          <div style="font-size:1rem;font-weight:700;">⚡ ${_escHtml(wfName)} 執行結果</div>
          <button onclick="document.getElementById('wfRunResultOverlay')?.remove()"
            style="border:none;background:none;font-size:1.2rem;cursor:pointer;color:#888;">✕</button>
        </div>
        <div style="font-size:0.75rem;color:#888;margin-bottom:10px;">
          ${data.blocks_executed} 個節點 · ${new Date(data.executed_at).toLocaleTimeString("zh-TW")}
        </div>
        ${blockRows}
        ${outputHtml}
      </div>`;
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }

  // ── Workflow Delete Confirm Dialog ──
  window._showWfDeleteConfirm = function (wfId, wfName, scope, owner) {
    const overlay = document.createElement("div");
    overlay.className = "wf-delete-overlay";
    overlay.innerHTML = `
      <div class="wf-delete-modal">
        <h3>確認刪除工作流</h3>
        <div class="wf-delete-skill-name">${wfName}</div>
        <label for="wfWfDeleteReason">刪除原因（必填，至少 5 個字）</label>
        <textarea id="wfWfDeleteReason" placeholder="請輸入刪除原因..."></textarea>
        <div class="wf-delete-hint">此操作不可復原，將完全移除該工作流，並同步 Commit 至遠端。</div>
        <div class="wf-delete-actions">
          <button class="wf-delete-cancel" id="wfWfDeleteCancel">取消</button>
          <button class="wf-delete-confirm" id="wfWfDeleteConfirm" disabled>確認刪除</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const textarea   = overlay.querySelector("#wfWfDeleteReason");
    const confirmBtn = overlay.querySelector("#wfWfDeleteConfirm");
    const cancelBtn  = overlay.querySelector("#wfWfDeleteCancel");

    textarea.addEventListener("input", () => {
      confirmBtn.disabled = textarea.value.trim().length < 5;
    });
    textarea.focus();

    cancelBtn.addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });

    confirmBtn.addEventListener("click", async () => {
      const reason = textarea.value.trim();
      if (reason.length < 5) return;

      confirmBtn.disabled = true;
      confirmBtn.textContent = "刪除中...";

      const user = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
      const _q = new URLSearchParams({ scope: scope || "personal", owner: owner || "" });
      try {
        const resp = await fetch(`/api/workflows/${encodeURIComponent(wfId)}?${_q}`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            reason,
            user_name: user.name || user.employee_id || "unknown",
            user_id:   user.employee_id || user.id || "unknown",
          }),
        });
        const data = await resp.json().catch(() => ({}));
        overlay.remove();
        if (!resp.ok) {
          if (window.showToast) window.showToast("刪除失敗: " + (data.detail || resp.status), "error");
          return;
        }
        const gitOk = data.git_sync?.status === "success";
        const gitMsg = gitOk ? "，已同步 Commit" : (data.git_sync?.status === "skipped" ? "" : "，Git 同步失敗請手動處理");
        if (window.showToast) window.showToast(`已刪除「${wfName}」${gitMsg}`, gitOk ? "success" : "warning");
        // Refresh landing grid
        await _showWorkflowLanding();
      } catch (e) {
        overlay.remove();
        if (window.showToast) window.showToast("刪除錯誤: " + e.message, "error");
      }
    });
  };

  // ── Phase 6: LLM One-shot Workflow Generator Modal ───────────────────
  // Lets the user type a natural-language task; server spins up a v2
  // workflow + runs it immediately. Gate 3 writes an oneshot snapshot
  // so if the run succeeds a promotion card can appear later in chat.
  window._showLLMGenerateModal = function () {
    document.getElementById("wfLLMGenModal")?.remove();
    const mask = document.createElement("div");
    mask.id = "wfLLMGenModal";
    mask.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;";
    mask.innerHTML = `
      <div style="background:#fff;width:var(--modal-width-lg);max-width:94vw;max-height:90vh;overflow-y:auto;border-radius:var(--modal-radius);box-shadow:0 20px 60px rgba(0,0,0,.3);padding:22px 24px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
          <span style="font-size:1.3rem;">✨</span>
          <h3 style="margin:0;font-size:1.05rem;">一次性智能流程</h3>
        </div>
        <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:14px;">
          描述一個任務，系統用可用技能自動組合出一個流程並立即執行。執行後若覺得好用，可以在結果卡片上一鍵永久儲存。
        </div>

        <label style="display:block;font-size:0.78rem;font-weight:600;color:var(--text-primary);margin-bottom:4px;">任務描述 <span style="color:var(--color-error);">*</span></label>
        <div style="position:relative;margin-bottom:12px;">
          <textarea id="wfLLMGenPrompt" rows="5" placeholder="例：搜尋台灣股市新聞 5 則，寫成摘要存到 Notion ToDo"
            style="width:100%;padding:10px 10px 32px 10px;border:1px solid #cbd5e1;border-radius:6px;font-size:0.85rem;resize:vertical;display:block;"></textarea>
          <button id="wfLLMGenRefineBtn" type="button" title="用 LLM 優化任務描述"
            style="position:absolute;left:8px;bottom:10px;width:26px;height:26px;padding:0;border:1px solid #cbd5e1;border-radius:5px;background:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;color:var(--wf-purple-dark);font-size:0.9rem;line-height:1;transition:all .15s;">
            ✏️
          </button>
        </div>

        <details style="margin-bottom:12px;">
          <summary style="cursor:pointer;font-size:0.78rem;color:var(--text-secondary);">進階選項</summary>
          <div style="margin-top:10px;padding-left:10px;border-left:2px solid #e2e8f0;">
            <label style="display:block;font-size:0.72rem;color:var(--text-secondary);margin-bottom:3px;">流程名稱（留空由 LLM 決定）</label>
            <input id="wfLLMGenName" type="text" placeholder="例：每日股市 → Notion"
              style="width:100%;padding:6px 10px;border:1px solid #cbd5e1;border-radius:4px;font-size:0.78rem;margin-bottom:10px;" />

            <label style="display:block;font-size:0.72rem;color:var(--text-secondary);margin-bottom:3px;">最多步驟數 (1-10)</label>
            <input id="wfLLMGenMaxSteps" type="number" min="1" max="10" value="6"
              style="width:100%;padding:6px 10px;border:1px solid #cbd5e1;border-radius:4px;font-size:0.78rem;margin-bottom:10px;" />

            <label style="display:flex;align-items:center;gap:6px;font-size:0.78rem;color:var(--text-secondary);">
              <input id="wfLLMGenExecute" type="checkbox" checked />
              立即執行（取消勾選僅產生預覽不執行）
            </label>
          </div>
        </details>

        <div id="wfLLMGenStatus" style="font-size:0.78rem;color:var(--text-secondary);margin-bottom:12px;min-height:20px;"></div>
        <div id="wfLLMGenPreview" style="display:none;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;font-family:var(--font-mono);font-size:0.72rem;color:var(--text-secondary);max-height:260px;overflow-y:auto;margin-bottom:12px;white-space:pre-wrap;"></div>

        <div style="text-align:right;">
          <button id="wfLLMGenCancel" type="button" style="padding:8px 18px;border-radius:8px;background:transparent;color:var(--text-muted);border:1px solid #e2e8f0;cursor:pointer;margin-right:8px;">取消</button>
          <button id="wfLLMGenSubmit" type="button" style="padding:8px 18px;border-radius:8px;background:linear-gradient(135deg,var(--wf-purple) 0%,var(--wf-purple-dark) 100%);color:#fff;border:none;font-weight:600;cursor:pointer;">✨ 產生並執行</button>
        </div>
      </div>
    `;
    document.body.appendChild(mask);

    const cleanup = () => mask.remove();
    mask.addEventListener("click", e => { if (e.target === mask) cleanup(); });
    mask.querySelector("#wfLLMGenCancel").onclick = cleanup;

    const submitBtn = mask.querySelector("#wfLLMGenSubmit");
    const statusEl  = mask.querySelector("#wfLLMGenStatus");
    const previewEl = mask.querySelector("#wfLLMGenPreview");
    const promptEl  = mask.querySelector("#wfLLMGenPrompt");
    const refineBtn = mask.querySelector("#wfLLMGenRefineBtn");

    // ── Pencil icon: refine user's rough prompt into a structured one ──
    // Sends current textarea content to /_actions/refine-prompt, overwrites
    // the textarea with the refined version, keeps a one-step undo so the
    // user can revert if the refinement went wrong.
    let _lastOriginalPrompt = null;
    refineBtn.onclick = async () => {
      const cur = (promptEl.value || "").trim();
      if (!cur) {
        statusEl.style.color = "var(--color-error)";
        statusEl.textContent = "⚠️ 請先填寫任務描述，再用 ✏️ 優化";
        return;
      }

      // Lock the button and show in-progress feedback
      refineBtn.disabled = true;
      const _origIcon = refineBtn.textContent;
      refineBtn.textContent = "⏳";
      refineBtn.style.cursor = "wait";
      statusEl.style.color = "var(--text-secondary)";
      statusEl.textContent = "🧠 正在優化任務描述…";

      try {
        const resp = await fetch("/api/workflows/_actions/refine-prompt", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ prompt: cur }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          const det = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `HTTP ${resp.status}`;
          throw new Error(det);
        }
        const refined = (data.refined || "").trim();
        if (!refined) throw new Error("LLM 回傳空內容");

        // Save prior value for one-step undo
        _lastOriginalPrompt = cur;
        promptEl.value = refined;

        // Show undo affordance in the status line
        statusEl.style.color = "var(--color-success)";
        statusEl.innerHTML = "✨ 已優化（請確認內容後再執行） &nbsp; <a href='#' id='wfLLMGenUndo' style='color:#6c5ce7;text-decoration:underline;font-size:0.76rem;'>↩ 還原原本的描述</a>";
        const undoLink = mask.querySelector("#wfLLMGenUndo");
        if (undoLink) {
          undoLink.onclick = (ev) => {
            ev.preventDefault();
            if (_lastOriginalPrompt != null) {
              promptEl.value = _lastOriginalPrompt;
              _lastOriginalPrompt = null;
              statusEl.style.color = "var(--text-secondary)";
              statusEl.textContent = "已還原為原本的描述";
            }
          };
        }
      } catch (e) {
        statusEl.style.color = "var(--color-error)";
        statusEl.textContent = "⚠️ 優化失敗：" + (e.message || e);
      } finally {
        refineBtn.disabled = false;
        refineBtn.textContent = _origIcon;
        refineBtn.style.cursor = "pointer";
      }
    };

    submitBtn.onclick = async () => {
      const promptText = mask.querySelector("#wfLLMGenPrompt").value.trim();
      if (!promptText) {
        statusEl.textContent = "⚠️ 請先描述任務";
        statusEl.style.color = "var(--color-error)";
        return;
      }
      const displayName = mask.querySelector("#wfLLMGenName").value.trim();
      const maxSteps    = parseInt(mask.querySelector("#wfLLMGenMaxSteps").value) || 6;
      const execute     = mask.querySelector("#wfLLMGenExecute").checked;

      submitBtn.disabled = true;
      submitBtn.textContent = "產生中...";
      statusEl.style.color = "var(--text-secondary)";
      statusEl.textContent = "🧠 LLM 正在根據你的需求組裝工作流…";
      previewEl.style.display = "none";

      try {
        const resp = await fetch("/api/workflows/_actions/llm-generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            prompt: promptText,
            display_name: displayName,
            max_steps: maxSteps,
            execute,
          }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          const detail = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `HTTP ${resp.status}`;
          throw new Error(detail);
        }
        const wf = data.workflow || {};
        const stepNames = (wf.steps || []).map(s => s.skill_id || s.type).join(" → ");
        let msg = `✅ 已產生：「${wf.display_name || wf.workflow_id}」`;
        if (stepNames) msg += ` (${stepNames})`;
        let runSuccess = false;
        if (execute && data.execution) {
          const ex = data.execution;
          if (ex.status === "success") {
            msg += `\n🚀 執行成功 (${ex.blocks_executed || 0} 個節點)`;
            statusEl.style.color = "var(--color-success)";
            runSuccess = true;
          } else {
            msg += `\n⚠️ 執行失敗：${(ex.errors || [ex.message || "unknown"]).join("；")}`;
            statusEl.style.color = "var(--color-error)";
          }
        }
        statusEl.textContent = msg;

        // Show the generated JSON preview
        previewEl.style.display = "block";
        previewEl.textContent = JSON.stringify(wf, null, 2);

        // Clean up any previous save button / output div from a prior run
        mask.querySelectorAll(".wf-llm-save-row, .wf-llm-output-div").forEach(el => el.remove());

        if (execute && data.execution?.status === "success" && data.execution?.final_output) {
          // Also show the actual output from the run
          const outDiv = document.createElement("div");
          outDiv.className = "wf-llm-output-div";
          outDiv.style.cssText = "background:#f0fdf4;border:1px solid #86efac;border-radius:6px;padding:10px;margin-top:10px;font-size:0.78rem;color:#14532d;max-height:200px;overflow-y:auto;white-space:pre-wrap;";
          // Render with clickable markdown links (PDF/image download links)
          const fo = (data.execution.final_output || "").slice(0, 1500);
          outDiv.innerHTML = "<strong>最終輸出：</strong><br>" + _renderFinalOutputHtml(fo);
          previewEl.insertAdjacentElement("afterend", outDiv);
        }

        // ── Save-as-permanent block (Phase 6 promotion inline) ──
        // Runs successful + has run_id → expose a save button right here so
        // user doesn't have to hop to the chat page to find the promotion card.
        const runId = data.run_id || data.execution?.run_id || "";
        if (runSuccess && runId) {
          const saveRow = document.createElement("div");
          saveRow.className = "wf-llm-save-row";
          saveRow.style.cssText = "background:#f8f5ff;border:1px dashed #c7b9ff;border-radius:8px;padding:12px;margin-top:12px;";
          saveRow.innerHTML = `
            <div style="font-size:0.82rem;font-weight:700;color:var(--text-primary);margin-bottom:8px;">💾 這個流程很好用嗎？</div>
            <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:10px;">按下「儲存為我的工作流」可以永久保留，之後在聊天中用關鍵字就能觸發。</div>
            <div style="display:flex;gap:8px;align-items:center;">
              <input id="wfLLMPromoteName" type="text" placeholder="工作流名稱" value="${_escHtml(wf.display_name || '')}"
                style="flex:1;padding:5px 10px;border:1px solid #cbd5e1;border-radius:4px;font-size:0.78rem;" />
              <select id="wfLLMPromoteScope" style="padding:5px 8px;border:1px solid #cbd5e1;border-radius:4px;font-size:0.78rem;">
                <option value="personal">👤 個人</option>
                <option value="department">🏢 部門</option>
                <option value="system">🌐 系統（admin）</option>
              </select>
              <button id="wfLLMPromoteBtn" type="button" style="padding:6px 14px;background:var(--wf-purple-dark);color:#fff;border:none;border-radius:4px;font-size:0.78rem;font-weight:600;cursor:pointer;white-space:nowrap;">儲存</button>
            </div>
            <div id="wfLLMPromoteStatus" style="font-size:0.72rem;margin-top:6px;min-height:16px;"></div>
          `;
          (mask.querySelector(".wf-llm-output-div") || previewEl).insertAdjacentElement("afterend", saveRow);

          saveRow.querySelector("#wfLLMPromoteBtn").addEventListener("click", async (ev) => {
            const nameIn = saveRow.querySelector("#wfLLMPromoteName");
            const scopeSel = saveRow.querySelector("#wfLLMPromoteScope");
            const statusDiv = saveRow.querySelector("#wfLLMPromoteStatus");
            const displayName = nameIn.value.trim();
            if (!displayName) {
              statusDiv.textContent = "⚠️ 請先填名稱";
              statusDiv.style.color = "var(--color-error)";
              nameIn.focus();
              return;
            }
            const btn = ev.currentTarget;
            btn.disabled = true;
            btn.textContent = "儲存中…";
            statusDiv.style.color = "var(--text-secondary)";
            statusDiv.textContent = "正在儲存…";
            try {
              const r = await fetch("/api/workflows/_actions/promote", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "include",
                body: JSON.stringify({
                  run_id: runId,
                  display_name: displayName,
                  target_scope: scopeSel.value,
                  target_owner: "",
                }),
              });
              const rd = await r.json().catch(() => ({}));
              if (!r.ok) {
                const det = rd?.detail ? (typeof rd.detail === "string" ? rd.detail : JSON.stringify(rd.detail)) : `HTTP ${r.status}`;
                throw new Error(det);
              }
              statusDiv.style.color = "var(--color-success)";
              statusDiv.textContent = `✅ 已儲存為工作流「${displayName}」`;
              btn.textContent = "已儲存";
              // Refresh the landing cards so the new workflow shows up
              // without requiring a full page reload.
              if (typeof _showWorkflowLanding === "function") {
                try { _showWorkflowLanding(); } catch (_) {}
              }
            } catch (err) {
              statusDiv.style.color = "var(--color-error)";
              statusDiv.textContent = "❌ 儲存失敗：" + (err.message || err);
              btn.disabled = false;
              btn.textContent = "儲存";
            }
          });
        }

        submitBtn.textContent = "✨ 重新產生";
        submitBtn.disabled = false;
      } catch (e) {
        statusEl.style.color = "var(--color-error)";
        statusEl.textContent = "❌ 產生失敗：" + (e.message || e);
        submitBtn.disabled = false;
        submitBtn.textContent = "✨ 產生並執行";
      }
    };

    // Auto-focus the prompt field
    setTimeout(() => mask.querySelector("#wfLLMGenPrompt")?.focus(), 50);
  };

  // ── Scope Picker Dialog ──
  window._showNewWorkflowScopePicker = function () {
    const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
    const role      = (_u.role || "").toLowerCase();          // admin / editor / viewer / guest
    const deptCode  = _u.dept_code || _u.department_code || "";
    const userId    = _u.employee_id || _u.id || "";

    // Permission checks
    const isGuest  = _isGuestUser(_u);
    // System: admin only
    const canSystem = role === "admin";
    // Department: non-guest with a dept_code AND role has edit rights
    const canDept   = !isGuest && !!deptCode && (role === "admin" || role === "editor");
    // Personal: any logged-in user (incl. guest) with a valid user identifier
    const canPerson = !!(_u.id || _u.employee_id || _u.user_id);

    function _lockMsg(scope) {
      if (scope === "system") return "僅限系統管理員";
      if (scope === "department") return role === "guest" ? "需登入編輯者帳號" : "需部門代碼與編輯者權限";
      return "需登入帳號";
    }

    function _card(icon, nameTW, path, desc, scope, allowed) {
      const dis = allowed ? "" : " disabled";
      const lock = allowed ? "" : `<div class="wf-scope-card-lock">🔒 ${_lockMsg(scope)}</div>`;
      // Phase 5: after picking scope, offer wizard OR blank canvas
      const ownerArg = scope === 'department' ? deptCode : scope === 'personal' ? userId : '';
      const click = allowed ? `onclick="window._chooseCreationMode('${scope}', '${ownerArg}')"` : "";
      return `<div class="wf-scope-card${dis}" ${click}>
        <div class="wf-scope-card-icon">${icon}</div>
        <div class="wf-scope-card-body">
          <div class="wf-scope-card-name">${nameTW}</div>
          <div class="wf-scope-card-path">${path}</div>
          <div class="wf-scope-card-desc">${desc}</div>
          ${lock}
        </div>
      </div>`;
    }

    const overlay = document.createElement("div");
    overlay.className = "wf-scope-overlay";
    overlay.id = "wfScopeOverlay";
    overlay.innerHTML = `
      <div class="wf-scope-modal">
        <div class="wf-scope-modal-title">選擇工作流類型</div>
        <div class="wf-scope-modal-sub">選擇儲存位置，建立後可透過設定頁更改描述與觸發條件</div>
        ${isGuest ? `<div style="background:#fff8e1;border:1px solid #ffd980;color:#78491a;padding:8px 12px;border-radius:8px;font-size:0.76rem;margin-bottom:12px;">⚠️ 您目前以訪客身分登入，僅能建立<b>個人工作流</b>（上限 3 個），無法編輯系統/部門工作流。完成身分驗證後可解除限制</div>` : ""}
        <div class="wf-scope-cards">
          ${_card("🌐", "系統工作流", "workspace/workflows/system/", "對所有使用者開放，需管理員權限", "system", canSystem)}
          ${_card("🏢", "部門工作流", `workspace/workflows/department/${deptCode||"(部門代碼)"}/`, "限本部門成員使用，需編輯者權限", "department", canDept)}
          ${_card("👤", "個人工作流", `workspace/workflows/personal/${userId||"(帳號ID)"}/`, "僅限自己使用，任何登入帳號可建立", "personal", canPerson)}
        </div>
        <div class="wf-scope-modal-footer">
          <button class="wf-scope-cancel-btn" onclick="document.getElementById('wfScopeOverlay')?.remove()">取消</button>
        </div>
      </div>`;

    // Close on backdrop click
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  };

  // Phase 5: Choose creation mode (wizard vs blank canvas)
  window._chooseCreationMode = function (scope, owner) {
    document.getElementById("wfScopeOverlay")?.remove();
    const overlay = document.createElement("div");
    overlay.id = "wfModeOverlay";
    overlay.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,0.45);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;";
    overlay.innerHTML = `
      <div style="background:#fff;border-radius:var(--modal-radius);box-shadow:var(--modal-shadow);padding:var(--modal-padding);width:var(--modal-width-md);max-width:92vw;">
        <div style="font-size:1.05rem;font-weight:700;margin-bottom:4px;">選擇建立方式</div>
        <div style="font-size:0.8rem;color:var(--text-muted);margin-bottom:16px;">使用精靈可在 5 個問題內生成工作流；也可以直接進入空白畫布自行拖拉</div>
        <div style="display:flex;flex-direction:column;gap:10px;">
          <div class="wf-mode-card" onclick="window._showWfWizard('${scope}','${owner}')"
            style="padding:14px 16px;border:2px solid var(--color-info);border-radius:12px;cursor:pointer;background:#f0f7ff;display:flex;align-items:flex-start;gap:14px;">
            <div style="font-size:1.6rem;flex-shrink:0;">✨</div>
            <div style="flex:1;">
              <div style="font-size:0.9rem;font-weight:700;color:var(--color-info);">精靈模式（推薦）</div>
              <div style="font-size:0.76rem;color:var(--text-secondary);margin-top:3px;">回答 5 題（目的/輸入/輸出/時機/失敗處理）自動選最適合的模板</div>
            </div>
          </div>
          <div class="wf-mode-card" onclick="window._createNewWorkflow('${scope}','${owner}');document.getElementById('wfModeOverlay')?.remove()"
            style="padding:14px 16px;border:1.5px solid #e2e8f0;border-radius:12px;cursor:pointer;display:flex;align-items:flex-start;gap:14px;">
            <div style="font-size:1.6rem;flex-shrink:0;">🎨</div>
            <div style="flex:1;">
              <div style="font-size:0.9rem;font-weight:700;">空白畫布</div>
              <div style="font-size:0.76rem;color:var(--text-muted);margin-top:3px;">從 Palette 拖拉節點自由設計</div>
            </div>
          </div>
        </div>
        <div style="display:flex;justify-content:flex-end;margin-top:16px;">
          <button onclick="document.getElementById('wfModeOverlay')?.remove()" style="padding:7px 18px;border-radius:8px;border:1px solid #e2e8f0;background:transparent;color:var(--text-muted);cursor:pointer;font-size:0.82rem;">取消</button>
        </div>
      </div>`;
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  };

  // Phase 5: 5-question wizard
  window._showWfWizard = async function (scope, owner) {
    document.getElementById("wfModeOverlay")?.remove();

    const QUESTIONS = [
      { key: "purpose", label: "您想做什麼？", options: ["情報/新聞", "資料整理", "會議整理", "備忘/提醒"] },
      { key: "input_source", label: "輸入來源是？", options: ["文字", "檔案", "網址", "LINE 訊息"] },
      { key: "output_target", label: "輸出要送到哪？", options: ["摘要", "Notion", "LINE 推播", "Email", "待辦"] },
      { key: "schedule", label: "什麼時候執行？", options: ["手動", "每日", "每週", "每月"] },
      { key: "on_fail", label: "失敗時怎麼辦？", options: ["retry", "skip", "notify"],
        labels: { retry: "自動重試", skip: "跳過繼續", notify: "通知我" } },
    ];

    const answers = {};
    let step = 0;

    const overlay = document.createElement("div");
    overlay.id = "wfWizardOverlay";
    overlay.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,0.45);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;";
    document.body.appendChild(overlay);

    function renderStep() {
      if (step >= QUESTIONS.length) { renderPreview(); return; }
      const q = QUESTIONS[step];
      const labelFor = (o) => (q.labels && q.labels[o]) || o;
      overlay.innerHTML = `
        <div style="background:#fff;border-radius:var(--modal-radius);box-shadow:var(--modal-shadow);padding:24px;width:var(--modal-width-md);max-width:92vw;">
          <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:6px;">步驟 ${step+1} / ${QUESTIONS.length}</div>
          <div style="font-size:1.05rem;font-weight:700;margin-bottom:16px;">${q.label}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            ${q.options.map(o => `
              <button class="wf-wiz-opt" data-val="${o}"
                style="padding:14px;border:1.5px solid #e2e8f0;border-radius:10px;background:var(--bg-sidebar);cursor:pointer;font-size:0.88rem;color:var(--text-primary);transition:border-color 0.15s,background 0.15s;">
                ${labelFor(o)}
              </button>
            `).join("")}
          </div>
          <div style="display:flex;justify-content:space-between;margin-top:18px;">
            <button onclick="(function(){document.getElementById('wfWizardOverlay')?.remove()})()" style="padding:7px 16px;border-radius:8px;border:1px solid #e2e8f0;background:transparent;color:var(--text-muted);cursor:pointer;font-size:0.82rem;">取消</button>
            ${step > 0 ? `<button id="wfWizBack" style="padding:7px 16px;border-radius:8px;border:1px solid #e2e8f0;background:transparent;color:var(--text-muted);cursor:pointer;font-size:0.82rem;">上一步</button>` : ""}
          </div>
        </div>`;
      overlay.querySelectorAll(".wf-wiz-opt").forEach(btn => {
        btn.onmouseenter = () => { btn.style.borderColor = "var(--color-info)"; btn.style.background = "#f0f7ff"; };
        btn.onmouseleave = () => { btn.style.borderColor = "#e2e8f0"; btn.style.background = "var(--bg-sidebar)"; };
        btn.onclick = () => {
          answers[q.key] = btn.dataset.val;
          step += 1;
          renderStep();
        };
      });
      const back = overlay.querySelector("#wfWizBack");
      if (back) back.onclick = () => { step -= 1; renderStep(); };
    }

    async function renderPreview() {
      overlay.innerHTML = `<div style="background:#fff;border-radius:var(--modal-radius);padding:24px;width:var(--modal-width-md);max-width:92vw;"><div style="text-align:center;padding:30px 0;">⏳ 正在為你選擇最適合的模板...</div></div>`;
      try {
        const resp = await fetch("/api/workflows/wizard", {
          method: "POST", headers: {"Content-Type":"application/json"},
          body: JSON.stringify(answers),
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== "success") {
          overlay.innerHTML = `<div style="background:#fff;border-radius:16px;padding:24px;"><div style="color:var(--color-error);">Wizard 建立失敗：${JSON.stringify(data.detail || data).slice(0,200)}</div><div style="text-align:right;margin-top:12px;"><button onclick="document.getElementById('wfWizardOverlay')?.remove()">關閉</button></div></div>`;
          return;
        }
        const wf = data.workflow;
        const stepsPreview = (wf.blocks || []).filter(b => !["start","end","branch"].includes(b.type)).map(b => b.type).join(" → ") || "(無節點)";
        overlay.innerHTML = `
          <div style="background:#fff;border-radius:var(--modal-radius);box-shadow:var(--modal-shadow);padding:24px;width:var(--modal-width-md);max-width:92vw;max-height:85vh;overflow-y:auto;">
            <div style="font-size:1.05rem;font-weight:700;margin-bottom:6px;">✨ 已為你選擇模板</div>
            <div style="font-size:0.88rem;color:var(--color-info);font-weight:600;margin-bottom:12px;">${wf.icon || "📋"} ${_escHtml(wf.display_name)}</div>
            <div style="background:var(--bg-sidebar);border-radius:10px;padding:12px;margin-bottom:14px;">
              <div style="font-size:0.76rem;color:var(--text-muted);margin-bottom:4px;">描述</div>
              <div style="font-size:0.85rem;color:var(--text-primary);">${_escHtml(wf.description || "")}</div>
              <div style="font-size:0.76rem;color:var(--text-muted);margin-top:10px;margin-bottom:4px;">流程</div>
              <div style="font-size:0.82rem;color:var(--text-primary);font-family:var(--font-mono);">${_escHtml(stepsPreview)}</div>
              ${(wf.variables?.env_requirements || []).length ? `
                <div style="font-size:0.76rem;color:var(--text-muted);margin-top:10px;margin-bottom:4px;">需要的環境變數</div>
                <div style="font-size:0.78rem;color:#b45309;">${wf.variables.env_requirements.map(e => `<code style="background:#fff8e1;padding:2px 5px;border-radius:3px;margin-right:5px;">${e}</code>`).join("")}</div>
              ` : ""}
            </div>
            <div style="display:flex;gap:8px;margin-bottom:12px;">
              <input id="wfWizName" type="text" placeholder="輸入工作流名稱" value="${_escHtml(wf.display_name)}" style="flex:1;padding:8px 12px;border:1.5px solid #cbd5e1;border-radius:8px;font-size:0.88rem;" />
            </div>
            <div style="display:flex;justify-content:flex-end;gap:10px;">
              <button onclick="document.getElementById('wfWizardOverlay')?.remove()" style="padding:8px 18px;border-radius:8px;border:1px solid #e2e8f0;background:transparent;color:var(--text-muted);cursor:pointer;">取消</button>
              <button id="wfWizCreate" style="padding:8px 18px;border-radius:8px;background:var(--color-info);color:#fff;border:none;font-weight:600;cursor:pointer;">建立並開啟畫布</button>
            </div>
          </div>`;
        overlay.querySelector("#wfWizCreate").onclick = async () => {
          const finalName = overlay.querySelector("#wfWizName").value.trim() || wf.display_name;
          wf.display_name = finalName;
          wf.name = finalName;
          wf.scope = scope;
          wf.owner = owner;
          // Generate a slug-friendly workflow_id from the name OR timestamp
          const wfId = "wf-" + Date.now();
          try {
            const saveResp = await fetch(`/api/workflows/${wfId}`, {
              method: "POST", headers: {"Content-Type":"application/json"}, credentials:"include",
              body: JSON.stringify(wf),
            });
            const saveData = await saveResp.json();
            if (!saveResp.ok) {
              const det = saveData.detail?.errors || [saveData.detail || saveResp.status];
              if (window.showToast) window.showToast("建立失敗：" + (Array.isArray(det) ? det.join("；") : det), "error");
              return;
            }
            overlay.remove();
            const finalId = saveData.id || wfId;
            _enterWorkflowCanvas(finalId, scope, owner);
            if (window.showToast) window.showToast(`已從「${wf.icon} ${wf.display_name}」模板建立`, "success");
          } catch (e) {
            if (window.showToast) window.showToast("網路錯誤：" + e.message, "error");
          }
        };
      } catch (err) {
        overlay.innerHTML = `<div style="background:#fff;border-radius:16px;padding:24px;"><div style="color:var(--color-error);">錯誤：${err.message}</div></div>`;
      }
    }

    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    renderStep();
  };

  window._createNewWorkflow = async function (scope, owner) {
    // Close scope picker if open
    document.getElementById("wfScopeOverlay")?.remove();

    scope = scope || "personal";
    const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
    owner = owner || _u.employee_id || _u.id || "";

    // Client-side only: no backend stub is created. The workflow is
    // materialized on disk only when the user explicitly saves (Phase 2
    // Gate 0 enforces display_name + at least one skill block).
    // This prevents orphan files from being left behind when users open
    // the canvas and then abandon without saving.
    const wfId = "wf-" + Date.now();
    _enterWorkflowCanvas(wfId, scope, owner, { isNewDraft: true });
  };

  // ── Read-only banner shown when entering a workflow without edit rights ──
  function _showReadOnlyBanner(isReadOnly, scope) {
    // Remove any existing banner
    document.getElementById("wfReadOnlyBanner")?.remove();
    if (!isReadOnly) return;

    const scopeLabel = scope === "system" ? "系統" : scope === "department" ? "部門" : "個人";
    const reason = _isGuestUser()
      ? "訪客帳號僅能檢視此工作流，無法編輯。請完成身分驗證以取得編輯權限"
      : `您沒有編輯此${scopeLabel}工作流的權限，目前為「檢視模式」`;

    const banner = document.createElement("div");
    banner.id = "wfReadOnlyBanner";
    banner.style.cssText =
      "position:fixed;top:60px;left:50%;transform:translateX(-50%);z-index:800;" +
      "background:#b45309;color:#fff;padding:8px 18px;border-radius:8px;" +
      "box-shadow:0 6px 18px rgba(0,0,0,0.15);font-size:0.82rem;font-weight:600;" +
      "display:flex;align-items:center;gap:10px;max-width:92vw;";
    banner.innerHTML =
      '<span style="font-size:1.05rem;">🔒</span>' +
      '<span>' + reason + '</span>';
    document.body.appendChild(banner);
  }

  // ── Central role / write-permission helpers ────────────────────────
  // Mirror of server/services/permissions.py — must stay in sync.
  function _getCurrentUser() {
    return JSON.parse(sessionStorage.getItem("kway_user") || "{}");
  }

  function _isGuestUser(u) {
    u = u || _getCurrentUser();
    const role = (u.role || "").toLowerCase();
    if (role === "guest") return true;
    if ((u.name || "") === "訪客") return true;
    // No role AND no employee_id AND no onboarding → guest
    if (!role && !u.employee_id && !u.onboarding_completed) return true;
    return false;
  }

  function _allUserIds(u) {
    u = u || _getCurrentUser();
    const ids = new Set();
    ["user_id", "employee_id", "id"].forEach(k => {
      const v = u[k];
      if (v) {
        ids.add(String(v));
        if (String(v).startsWith("line_")) ids.add(String(v).slice(5));
      }
    });
    return ids;
  }

  // Returns true if the current user can WRITE (create/modify/delete) a
  // resource at the given (scope, owner).
  function _canEditScope(scope, owner, u) {
    u = u || _getCurrentUser();
    const role = (u.role || "").toLowerCase();
    const dept = u.department_code || u.dept_code || "";

    if (role === "admin") return true;
    if (_isGuestUser(u)) {
      // Guests: only their own personal scope
      return scope === "personal" && _allUserIds(u).has(String(owner || ""));
    }
    if (scope === "system") return false;   // non-admin can't touch system
    if (scope === "department") {
      if (!dept) return false;
      return dept === String(owner || "") && (role === "admin" || role === "editor" || role === "viewer");
    }
    if (scope === "personal") {
      return _allUserIds(u).has(String(owner || ""));
    }
    return false;
  }
  window._canEditScope = _canEditScope;  // expose for other handlers

  window._openWorkflow = function (id, scope, owner) {
    // Permission pre-flight: if not editable, open in read-only mode so user
    // can still inspect but can't accidentally make changes that fail at save.
    const canEdit = _canEditScope(scope, owner);
    _enterWorkflowCanvas(id, scope, owner, { readOnly: !canEdit });
  };

  async function _enterWorkflowCanvas(wfId, scope, owner, opts) {
    opts = opts || {};
    const readOnly = !!opts.readOnly;
    // Close landing overlay, enter wf-mode with canvas
    const overlay = document.getElementById("wfLandingOverlay");
    if (overlay) overlay.classList.remove("open");
    const body = document.querySelector(".page-chat-body");
    if (body) {
      body.classList.add("wf-mode");
      body.classList.toggle("wf-readonly", readOnly);
    }
    // Remove anti-flash style if present (from ?wf= redirect)
    const antiFlash = document.getElementById("wfAntiFlash");
    if (antiFlash) antiFlash.remove();
    if (body) body.style.visibility = "visible";

    // Build palette
    const paletteWrap = document.getElementById("wfPaletteWrap");
    if (paletteWrap) await _rebuildPaletteForFlow(paletteWrap);

    // Clean up old designer — destroy() removes all event listeners to prevent accumulation
    if (window._wfDesigner) {
      window._wfDesigner.destroy();
      window._wfDesigner.blocks.forEach(b => b.el.remove());
      window._wfDesigner.blocks.clear();
      window._wfDesigner.connections.forEach(c => c.el.remove());
      window._wfDesigner.connections = [];
      window._wfDesigner = null;
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
      window._wfDesigner.readOnly = readOnly;
      window._wfDesigner._isNewDraft = !!opts.isNewDraft;

      if (opts.isNewDraft) {
        // No backend stub exists yet — initialize an empty in-memory designer
        window._wfDesigner._wfData = { name: "", description: "", icon: "", tags: [], variables: { global_inputs: [], env_requirements: [], definitions: [] }, trigger: {}, execution: {}, security: {} };
        window._wfDesigner._savedSnapshot = null;  // any edit becomes dirty
      } else {
        await window._wfDesigner.load(wfId, scope, owner);
      }

      // If new workflow (no blocks), initialize _wfData so settings modal shows blank name
      if (window._wfDesigner.blocks.size === 0) {
        if (!window._wfDesigner._wfData) window._wfDesigner._wfData = {};
        // Don't pre-fill name — force user to set it via settings modal
        if (!readOnly) window._wfDesigner.addBlock("start", 200, 250);
      }
    }

    // ── Read-only banner ──
    _showReadOnlyBanner(readOnly, scope);

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
      await _rebuildPaletteForEdit(paletteWrap);
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

      // ── Restore last-opened skill from view state ──
      try {
        const _lastSkill = window.viewState?.("skill_editor").restore("currentSkill", "");
        if (_lastSkill && _dynamicSkills[_lastSkill]) {
          setTimeout(() => { window._wfSkillEditor?.loadSkill(_lastSkill); }, 60);
        }
      } catch (_) {}
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

      // Remember last-opened skill so re-entering editor resumes where user was
      if (window.viewState) window.viewState("skill_editor").update({ currentSkill: skillName });

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

  // 3-option dirty-exit dialog. Resolves to one of:
  //   "save"   — save then exit
  //   "discard" — exit without saving
  //   "cancel" — stay
  function _showDirtyExitDialog(message) {
    return new Promise(resolve => {
      document.getElementById("wfConfirmOverlay")?.remove();
      const overlay = document.createElement("div");
      overlay.id = "wfConfirmOverlay";
      overlay.className = "wf-confirm-overlay";
      overlay.innerHTML = `
        <div class="wf-confirm-box" style="max-width:440px;">
          <div class="wf-confirm-icon">⚠️</div>
          <div class="wf-confirm-msg">${message}</div>
          <div class="wf-confirm-actions" style="flex-wrap:wrap;gap:8px;">
            <button class="wf-confirm-btn wf-confirm-cancel" data-a="cancel">取消</button>
            <button class="wf-confirm-btn" data-a="discard"
              style="background:#fff5f5;color:var(--color-error);border:1px solid #fca5a5;">直接退出</button>
            <button class="wf-confirm-btn wf-confirm-ok" data-a="save"
              style="background:var(--kway-blue,#4a90d9);">儲存並退出</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      const _close = (a) => { overlay.remove(); resolve(a); };
      overlay.querySelectorAll("button[data-a]").forEach(btn => {
        btn.onclick = () => _close(btn.dataset.a);
      });
      overlay.onclick = (e) => { if (e.target === overlay) _close("cancel"); };
      // Esc = cancel
      overlay._onKey = (e) => { if (e.key === "Escape") _close("cancel"); };
      document.addEventListener("keydown", overlay._onKey, { once: true });
    });
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

  // ── Workflow Settings Modal ──────────────────────────────────
  let _settingsActiveTab = "basic";

  window._openWfSettings = function () {
    const overlay = document.getElementById("wfSettingsOverlay");
    if (!overlay) return;
    overlay.classList.add("open");
    _settingsActiveTab = "basic";
    // Reset tab buttons
    overlay.querySelectorAll(".wf-settings-tab").forEach(t => t.classList.toggle("active", t.dataset.stab === "basic"));
    _renderSettingsTab("basic");
  };

  window._closeWfSettings = function () {
    const overlay = document.getElementById("wfSettingsOverlay");
    if (overlay) overlay.classList.remove("open");
  };

  window._switchSettingsTab = function (btn, tab) {
    const overlay = document.getElementById("wfSettingsOverlay");
    if (!overlay) return;
    // Collect current tab data before switching
    const fd = window._wfDesigner;
    if (fd && fd._wfData) _collectSettingsFromDOM(fd._wfData);
    overlay.querySelectorAll(".wf-settings-tab").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    _settingsActiveTab = tab;
    _renderSettingsTab(tab);
  };

  function _renderSettingsTab(tab) {
    const body = document.getElementById("wfSettingsBody");
    if (!body) return;
    const fd = window._wfDesigner;
    if (!fd) return;
    const wd = fd._wfData || {};

    if (tab === "basic") {
      body.innerHTML = `
        <div class="wf-settings-field"><label>工作流名稱 <span style="color:var(--brand-google-red)">*</span></label>
          <input type="text" id="wfSetName" value="${_escHtml(wd.name || "")}" placeholder="請輸入工作流名稱" /></div>
        <div class="wf-settings-field"><label>描述 <span style="color:var(--brand-google-red)">*</span></label>
          <textarea id="wfSetDesc" rows="3" placeholder="請簡述此工作流的用途">${_escHtml(wd.description || "")}</textarea></div>
        <div class="wf-settings-field"><label>圖示</label>
          <input type="text" id="wfSetIcon" value="${_escHtml(wd.icon || "")}" placeholder="例如: chart, document" /></div>
        <div class="wf-settings-field"><label>標籤 (逗號分隔)</label>
          <input type="text" id="wfSetTags" value="${(wd.tags || []).join(", ")}" /></div>
        <div class="wf-settings-field"><label>觸發關鍵詞 (逗號分隔)</label>
          <input type="text" id="wfSetKeywords" value="${(wd.trigger_keywords || []).join(", ")}" />
          <div class="wf-settings-hint">使用者說出這些關鍵詞時，系統自動匹配此工作流（0 Token 匹配）</div></div>
      `;
    } else if (tab === "variables") {
      _renderVariablesTab(body, wd);
    } else if (tab === "trigger") {
      const tr = wd.trigger || {};
      const kwList = wd.trigger_keywords || [];
      const hasKw = kwList.length > 0;
      const hasCron = !!(tr.cron || "").trim();
      // Inline warning: enabled but neither keywords nor cron → silent no-op
      const showWarn = tr.enabled && !hasKw && !hasCron;
      body.innerHTML = `
        <div class="wf-settings-field">
          <label class="wf-settings-toggle-label">
            <input type="checkbox" id="wfSetTriggerEnabled" ${tr.enabled ? "checked" : ""} />
            啟用自動觸發
          </label></div>
        ${showWarn ? `<div class="wf-settings-field" style="background:var(--color-error-bg);border:1px solid #fca5a5;border-radius:6px;padding:10px;margin-bottom:10px;">
          <div style="color:var(--color-error);font-weight:700;font-size:0.85rem;margin-bottom:4px;">⚠️ 此工作流永遠不會自動觸發</div>
          <div style="color:#7f1d1d;font-size:0.78rem;">已啟用觸發但沒有設定任何關鍵詞或排程。請至少做一件：<br>
            1. 到「<strong>基本</strong>」tab 填寫<strong>觸發關鍵詞</strong>（如「每日新聞」），或<br>
            2. 在下方填寫<strong>排程 Cron 表達式</strong>，或<br>
            3. 取消勾「啟用自動觸發」
          </div>
        </div>` : ""}
        <div class="wf-settings-field">
          <label>觸發關鍵詞摘要（在「基本」tab 編輯）</label>
          <div class="wf-settings-hint" style="padding:6px 10px;background:var(--bg-hover);border-radius:4px;color:var(--text-secondary);">
            ${hasKw ? kwList.map(k => `<code style="background:#e2e8f0;padding:1px 6px;border-radius:3px;margin-right:4px;">${_escHtml(k)}</code>`).join("") : '<span style="color:var(--text-tertiary);">（尚未設定，自動觸發不會生效）</span>'}
          </div>
        </div>
        <div class="wf-settings-field"><label>觸發模式</label>
          <select id="wfSetTriggerMode">
            <option value="auto" ${tr.mode === "auto" ? "selected" : ""}>自動執行</option>
            <option value="confirm" ${tr.mode === "confirm" ? "selected" : ""}>確認後執行</option>
          </select>
          <div class="wf-settings-hint">confirm 模式下匹配後會先詢問使用者</div></div>
        <div class="wf-settings-field"><label>優先順序 (1=最高)</label>
          <input type="number" id="wfSetTriggerPriority" value="${tr.priority || 10}" min="1" max="99" /></div>
        <div class="wf-settings-field"><label>排程 Cron 表達式</label>
          <input type="text" id="wfSetTriggerCron" value="${_escHtml(tr.schedule || tr.cron || "")}" placeholder="例如: 0 9 * * 1-5" />
          <div class="wf-settings-hint">
            儲存時會自動註冊到系統排程，到點執行整個工作流。範例：<br>
            &nbsp;&nbsp;<code>0 9 * * 1-5</code>&nbsp;= 週一到五上午 9:00<br>
            &nbsp;&nbsp;<code>*/10 * * * *</code>&nbsp;= 每 10 分鐘<br>
            &nbsp;&nbsp;<code>30 17 * * 5</code>&nbsp;= 每週五下午 5:30<br>
            留空表示不排程。一次性延遲（如「10 分鐘後」）請用排程管理技能，不能用 cron。
          </div></div>
        <div class="wf-settings-field">
          <button type="button" class="wf-settings-btn-secondary" onclick="window._showWfCronJobs && window._showWfCronJobs()" style="padding:6px 12px;font-size:0.85rem;">
            🕒 查看已排程工作流
          </button>
          <div class="wf-settings-hint">列出目前 APScheduler 中所有已註冊的工作流 cron job（含下次執行時間）。</div>
        </div>
      `;
    } else if (tab === "execution") {
      const ex = wd.execution || {};
      body.innerHTML = `
        <div class="wf-settings-field"><label>預設模型</label>
          <select id="wfSetExecModel">
            <option value="" ${!ex.default_model ? "selected" : ""}>自動選擇</option>
            <option value="gpt-4o" ${ex.default_model === "gpt-4o" ? "selected" : ""}>gpt-4o</option>
            <option value="gpt-4.1" ${ex.default_model === "gpt-4.1" ? "selected" : ""}>gpt-4.1</option>
            <option value="gpt-4.1-mini" ${ex.default_model === "gpt-4.1-mini" ? "selected" : ""}>gpt-4.1-mini</option>
            <option value="gpt-4.1-nano" ${ex.default_model === "gpt-4.1-nano" ? "selected" : ""}>gpt-4.1-nano</option>
            <option value="gemini-2.0-flash" ${ex.default_model === "gemini-2.0-flash" ? "selected" : ""}>gemini-2.0-flash</option>
          </select></div>
        <div class="wf-settings-field"><label>全域逾時 (秒)</label>
          <input type="number" id="wfSetExecTimeout" value="${ex.timeout || 120}" min="10" max="600" /></div>
        <div class="wf-settings-field"><label>Token 預算上限</label>
          <input type="number" id="wfSetExecTokenBudget" value="${ex.token_budget || 0}" min="0" />
          <div class="wf-settings-hint">0 表示不限制</div></div>
        <div class="wf-settings-field"><label>錯誤處理策略</label>
          <select id="wfSetExecOnError">
            <option value="stop" ${ex.on_error === "stop" ? "selected" : ""}>停止執行</option>
            <option value="skip" ${ex.on_error === "skip" ? "selected" : ""}>跳過失敗節點</option>
            <option value="retry" ${ex.on_error === "retry" ? "selected" : ""}>自動重試 (最多 3 次)</option>
          </select></div>
        <div class="wf-settings-field"><label>最大重試次數</label>
          <input type="number" id="wfSetExecRetries" value="${ex.max_retries || 3}" min="1" max="10" /></div>
        <div class="wf-settings-field"><label>最終輸出格式</label>
          <select id="wfSetExecOutputMode">
            <option value="last" ${(ex.output_mode || "last") === "last" ? "selected" : ""}>只顯示最後一步結果（推薦）</option>
            <option value="concat" ${ex.output_mode === "concat" ? "selected" : ""}>顯示每一步的輸出（設計 / 除錯用）</option>
          </select>
          <div class="wf-settings-hint">
            預設「最後一步」— 工作流是管道式執行，只給使用者最終成品（例如 PDF 下載連結）。<br>
            切「每一步」可在設計時看到每個 skill 的中間輸出，確認流程正確。
          </div></div>
      `;
    } else if (tab === "security") {
      const sec = wd.security || {};
      body.innerHTML = `
        <div class="wf-settings-field">
          <label class="wf-settings-toggle-label">
            <input type="checkbox" id="wfSetSecRequireAuth" ${sec.require_auth ? "checked" : ""} />
            執行前需要身分驗證
          </label></div>
        <div class="wf-settings-field"><label>允許的角色 (逗號分隔)</label>
          <input type="text" id="wfSetSecRoles" value="${(sec.allowed_roles || []).join(", ")}" placeholder="admin, editor" />
          <div class="wf-settings-hint">留空表示所有角色皆可執行</div></div>
        <div class="wf-settings-field"><label>頻率限制 (次/小時)</label>
          <input type="number" id="wfSetSecRateLimit" value="${sec.rate_limit || 0}" min="0" />
          <div class="wf-settings-hint">0 表示不限制</div></div>
        <div class="wf-settings-field">
          <label class="wf-settings-toggle-label">
            <input type="checkbox" id="wfSetSecAudit" ${sec.audit_log !== false ? "checked" : ""} />
            啟用審計日誌
          </label></div>
        <div class="wf-settings-field">
          <label class="wf-settings-toggle-label">
            <input type="checkbox" id="wfSetSecEncryptVars" ${sec.encrypt_variables ? "checked" : ""} />
            加密敏感變數
          </label></div>
      `;
    }
  }

  // ── Variables Tab ─────────────────────────────────────────────
  const VAR_SOURCES = [
    { value: "user_input", label: "使用者輸入" },
    { value: "fixed", label: "固定值" },
    { value: "system", label: "系統變數" },
    { value: "previous_step", label: "上一步輸出" },
    { value: "auto", label: "自動 (LLM)" },
    { value: "secret", label: "機密值" },
  ];

  const SYSTEM_VARS = ["{{current_date}}", "{{current_time}}", "{{user_name}}", "{{user_dept}}", "{{session_id}}", "{{workflow_name}}"];

  // ── Variables schema compatibility helpers ──
  // Phase 1 migration changed wf.variables from legacy array to v2 dict
  // {global_inputs, env_requirements, definitions}. These helpers let old UI
  // code keep working with either shape without scattered if-else.
  function _getVariableList(wd) {
    if (!wd) return [];
    const v = wd.variables;
    if (Array.isArray(v)) return v;
    if (v && Array.isArray(v.definitions)) return v.definitions;
    return [];
  }
  function _ensureVariableContainer(wd) {
    if (!wd) return [];
    if (Array.isArray(wd.variables)) {
      wd.variables = { global_inputs: [], env_requirements: [], definitions: wd.variables };
    } else if (!wd.variables || typeof wd.variables !== "object") {
      wd.variables = { global_inputs: [], env_requirements: [], definitions: [] };
    }
    if (!Array.isArray(wd.variables.definitions)) wd.variables.definitions = [];
    if (!Array.isArray(wd.variables.global_inputs)) wd.variables.global_inputs = [];
    if (!Array.isArray(wd.variables.env_requirements)) wd.variables.env_requirements = [];
    return wd.variables.definitions;
  }

  function _renderVariablesTab(body, wd) {
    const vars = _getVariableList(wd);
    let html = `
      <div class="wf-var-header">
        <span class="wf-var-title">自訂變數</span>
        <span class="wf-var-count">${vars.length} / 20</span>
        <button class="wf-var-add-btn" onclick="window._addWfVariable()" ${vars.length >= 20 ? "disabled" : ""}>+ 新增</button>
      </div>
      <div class="wf-var-list" id="wfVarList">`;
    vars.forEach((v, i) => {
      html += _renderVariableCard(v, i);
    });
    html += `</div>
      <div class="wf-var-system-section">
        <div class="wf-var-system-title">系統變數 (唯讀)</div>
        <div class="wf-var-system-list">${SYSTEM_VARS.map(sv => `<span class="wf-var-system-tag">${sv}</span>`).join("")}</div>
      </div>`;
    body.innerHTML = html;
  }

  function _renderVariableCard(v, idx) {
    const sourceOpts = VAR_SOURCES.map(s => `<option value="${s.value}" ${v.source === s.value ? "selected" : ""}>${s.label}</option>`).join("");
    return `<div class="wf-var-card" data-idx="${idx}">
      <div class="wf-var-card-row">
        <div class="wf-var-card-field" style="flex:2"><label>變數名稱</label>
          <input type="text" value="${_escHtml(v.name || "")}" onchange="window._updateWfVar(${idx},'name',this.value)" placeholder="my_variable" /></div>
        <div class="wf-var-card-field" style="flex:1"><label>類型</label>
          <select onchange="window._updateWfVar(${idx},'type',this.value)">
            <option value="string" ${v.type === "string" ? "selected" : ""}>文字</option>
            <option value="number" ${v.type === "number" ? "selected" : ""}>數字</option>
            <option value="boolean" ${v.type === "boolean" ? "selected" : ""}>布林</option>
            <option value="array" ${v.type === "array" ? "selected" : ""}>陣列</option>
          </select></div>
        <div class="wf-var-card-field" style="flex:1.5"><label>來源</label>
          <select onchange="window._updateWfVar(${idx},'source',this.value)">${sourceOpts}</select></div>
        <button class="wf-var-card-del" onclick="window._removeWfVar(${idx})">&times;</button>
      </div>
      <div class="wf-var-card-row">
        <div class="wf-var-card-field" style="flex:2"><label>預設值</label>
          <input type="text" value="${_escHtml(v.default_value || "")}" onchange="window._updateWfVar(${idx},'default_value',this.value)" placeholder="" /></div>
        <div class="wf-var-card-field" style="flex:3"><label>描述</label>
          <input type="text" value="${_escHtml(v.description || "")}" onchange="window._updateWfVar(${idx},'description',this.value)" placeholder="變數用途說明" /></div>
      </div>
      <div class="wf-var-card-row">
        <label class="wf-var-card-check"><input type="checkbox" ${v.required ? "checked" : ""} onchange="window._updateWfVar(${idx},'required',this.checked)" /> 必填</label>
      </div>
    </div>`;
  }

  window._addWfVariable = function () {
    const fd = window._wfDesigner;
    if (!fd) return;
    if (!fd._wfData) fd._wfData = {};
    const defs = _ensureVariableContainer(fd._wfData);
    if (defs.length >= 20) { if (window.showToast) window.showToast("最多 20 個變數", "error"); return; }
    defs.push({ name: "var_" + (defs.length + 1), type: "string", source: "user_input", default_value: "", description: "", required: false });
    _renderVariablesTab(document.getElementById("wfSettingsBody"), fd._wfData);
  };

  window._removeWfVar = function (idx) {
    const fd = window._wfDesigner;
    if (!fd || !fd._wfData) return;
    const defs = _ensureVariableContainer(fd._wfData);
    defs.splice(idx, 1);
    _renderVariablesTab(document.getElementById("wfSettingsBody"), fd._wfData);
  };

  window._updateWfVar = function (idx, field, value) {
    const fd = window._wfDesigner;
    if (!fd || !fd._wfData) return;
    const defs = _ensureVariableContainer(fd._wfData);
    if (!defs[idx]) return;
    // Trim whitespace for string fields — leading/trailing spaces in variable
    // names cause subtle bugs (e.g. ' searchQuery' != 'searchQuery').
    if (typeof value === "string" && (field === "name" || field === "default_value" || field === "description")) {
      value = value.trim();
    }
    defs[idx][field] = value;
  };

  // ── Save Settings ────────────────────────────────────────────
  window._saveWfSettings = async function () {
    const fd = window._wfDesigner;
    if (!fd) return;
    if (!fd._wfData) fd._wfData = {};

    // Read from current tab's form inputs (all tabs accumulate into _wfData)
    _collectSettingsFromDOM(fd._wfData);

    // Validate required fields in settings before closing
    const wd = fd._wfData;
    if (!wd.name || !wd.name.trim()) {
      if (window.showToast) window.showToast("⚠️ 工作流名稱為必填欄位", "error");
      const nameEl = document.getElementById("wfSetName");
      if (nameEl) nameEl.focus();
      return;
    }
    if (!wd.description || !wd.description.trim()) {
      if (window.showToast) window.showToast("⚠️ 工作流描述為必填欄位", "error");
      const descEl = document.getElementById("wfSetDesc");
      if (descEl) descEl.focus();
      return;
    }

    // Update toolbar name display
    const infoEl = document.getElementById("wfInfoText");
    if (infoEl) infoEl.textContent = `${wd.name}  ·  ${fd.blocks.size} 節點 · ${fd.connections.length} 連接`;

    // ── Consistency check: cron filled but trigger disabled ──
    // Users often fill the cron field and forget to tick 「啟用自動觸發」,
    // then wonder why the schedule never fires. Confirm their intent.
    const _tr = wd.trigger || {};
    const _cronFilled = !!((_tr.schedule || _tr.cron || "").trim());
    if (_cronFilled && !_tr.enabled) {
      const proceed = confirm(
        "⚠️ 您填寫了 Cron 表達式，但「啟用自動觸發」尚未勾選。\n\n" +
        "此工作流將不會依排程執行。\n\n" +
        "確定要以「停用」狀態儲存嗎？\n" +
        "（取消 → 回到設定畫面勾選啟用）"
      );
      if (!proceed) {
        // Switch to Trigger tab so the checkbox is visible
        const trigTabBtn = document.querySelector('.wf-settings-tab[data-stab="trigger"]');
        if (trigTabBtn) trigTabBtn.click();
        const enCk = document.getElementById("wfSetTriggerEnabled");
        if (enCk) enCk.focus();
        return;
      }
    }

    // ── Apply button loading state ──
    // Keep the modal open while saving so the user sees the in-progress
    // feedback; only close once the backend responds (success or failure).
    const overlay = document.getElementById("wfSettingsOverlay");
    const applyBtn = overlay ? overlay.querySelector(".wf-settings-btn-primary") : null;
    const _origLabel = applyBtn ? applyBtn.textContent : "";
    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.textContent = "套用中...";
      applyBtn.classList.add("is-loading");
    }

    let ok = false;
    try {
      ok = await fd.save();
    } catch (err) {
      console.warn("[WF] save error:", err);
    } finally {
      if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.textContent = ok ? "套用成功" : _origLabel;
        applyBtn.classList.remove("is-loading");
        if (ok) {
          setTimeout(() => {
            applyBtn.textContent = _origLabel;
            window._closeWfSettings && window._closeWfSettings();
          }, 600);
        }
      } else if (ok) {
        window._closeWfSettings && window._closeWfSettings();
      }
    }
  };

  // ── Cron Jobs Viewer ─────────────────────────────────────────────
  // Opens a modal listing all APScheduler-registered workflow cron jobs.
  // Useful to verify that trigger.schedule was actually accepted by the
  // scheduler (vs. sitting idle in the JSON with no running job).
  window._showWfCronJobs = async function () {
    const overlay = document.createElement("div");
    overlay.className = "wf-settings-overlay";
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:10000;";
    overlay.innerHTML = `
      <div style="background:#fff;border-radius:8px;width:720px;max-width:92vw;max-height:82vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,0.2);">
        <div style="padding:16px 20px;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between;">
          <div style="font-weight:600;font-size:1rem;">🕒 已排程的工作流 Cron Jobs</div>
          <button style="background:none;border:none;font-size:1.4rem;cursor:pointer;color:var(--text-muted);" onclick="this.closest('.wf-settings-overlay').remove()">×</button>
        </div>
        <div id="wfCronJobsBody" style="padding:16px 20px;overflow:auto;flex:1;">
          <div style="color:var(--text-muted);">載入中...</div>
        </div>
      </div>
    `;
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);

    const body = overlay.querySelector("#wfCronJobsBody");
    try {
      const resp = await fetch("/api/workflows/_actions/cron-jobs", { credentials: "include" });
      const data = await resp.json();

      if (data.status !== "ok") {
        body.innerHTML = `<div style="color:var(--color-error);">⚠️ 無法取得排程資訊：${_escHtml(data.reason || data.status)}</div>`;
        return;
      }

      const stateColor = data.scheduler_state === "RUNNING" ? "#16a34a" : "#b91c1c";
      let html = `
        <div style="margin-bottom:12px;font-size:0.85rem;color:var(--text-muted);">
          APScheduler 狀態：<span style="color:${stateColor};font-weight:600;">${_escHtml(data.scheduler_state)}</span>
          &nbsp;·&nbsp; 共 ${data.count} 個工作流 cron job
        </div>
      `;

      if (!data.jobs.length) {
        html += `<div style="padding:32px;text-align:center;color:var(--text-tertiary);">目前沒有任何已註冊的工作流 cron job。<br><br>設定 <code>trigger.enabled=true</code> 且 <code>trigger.schedule</code> 填入 cron 表達式後，儲存工作流即會自動註冊。</div>`;
      } else {
        html += `
          <table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
            <thead>
              <tr style="background:var(--bg-hover);">
                <th style="text-align:left;padding:8px 10px;border-bottom:1px solid #cbd5e1;">工作流</th>
                <th style="text-align:left;padding:8px 10px;border-bottom:1px solid #cbd5e1;">Cron</th>
                <th style="text-align:left;padding:8px 10px;border-bottom:1px solid #cbd5e1;">下次執行</th>
                <th style="text-align:left;padding:8px 10px;border-bottom:1px solid #cbd5e1;">Scope</th>
              </tr>
            </thead>
            <tbody>
        `;
        for (const j of data.jobs) {
          const nextRun = j.next_run_time
            ? new Date(j.next_run_time).toLocaleString("zh-TW", { hour12: false })
            : "<span style='color:#94a3b8;'>—</span>";
          const enabledBadge = j.trigger_enabled
            ? ""
            : `<span style="display:inline-block;margin-left:6px;padding:1px 6px;background:var(--color-warning-bg);color:#92400e;border-radius:3px;font-size:0.7rem;">已停用</span>`;
          html += `
            <tr style="border-bottom:1px solid #e5e7eb;">
              <td style="padding:8px 10px;">
                <div style="font-weight:500;">${_escHtml(j.display_name || j.workflow_id)}${enabledBadge}</div>
                <div style="color:var(--text-tertiary);font-size:0.75rem;font-family:var(--font-mono);">${_escHtml(j.workflow_id)}</div>
              </td>
              <td style="padding:8px 10px;font-family:var(--font-mono);">${_escHtml(j.cron || "—")}</td>
              <td style="padding:8px 10px;">${nextRun}</td>
              <td style="padding:8px 10px;color:var(--text-muted);">${_escHtml(j.scope || "—")}${j.owner ? ` / ${_escHtml(j.owner)}` : ""}</td>
            </tr>
          `;
        }
        html += `</tbody></table>`;
      }

      body.innerHTML = html;
    } catch (e) {
      body.innerHTML = `<div style="color:var(--color-error);">⚠️ 請求失敗：${_escHtml(String(e))}</div>`;
    }
  };

  function _collectSettingsFromDOM(wd) {
    // Basic — name is the user-facing display name (separate from workflow ID)
    const nameEl = document.getElementById("wfSetName");
    if (nameEl) wd.name = nameEl.value.trim();
    const desc = document.getElementById("wfSetDesc");
    if (desc) wd.description = desc.value;
    const icon = document.getElementById("wfSetIcon");
    if (icon) wd.icon = icon.value;
    // Accept both ASCII and CJK delimiters — users on Chinese keyboards
    // often type 「每日新聞，新聞搜尋」 (fullwidth comma) which used to be
    // stored as a single blob that never matched.
    const _splitList = (s) => (s || "")
      .split(/[,，、;；]+/)
      .map(t => t.trim())
      .filter(Boolean);
    const tags = document.getElementById("wfSetTags");
    if (tags) wd.tags = _splitList(tags.value);
    const kw = document.getElementById("wfSetKeywords");
    if (kw) wd.trigger_keywords = _splitList(kw.value);

    // Trigger
    const trigEnabled = document.getElementById("wfSetTriggerEnabled");
    if (trigEnabled) {
      if (!wd.trigger) wd.trigger = {};
      wd.trigger.enabled = trigEnabled.checked;
      const trigMode = document.getElementById("wfSetTriggerMode");
      if (trigMode) wd.trigger.mode = trigMode.value;
      const trigPri = document.getElementById("wfSetTriggerPriority");
      if (trigPri) wd.trigger.priority = parseInt(trigPri.value) || 10;
      const trigCron = document.getElementById("wfSetTriggerCron");
      if (trigCron) {
        wd.trigger.schedule = trigCron.value.trim();
        // Backward compat: keep cron too so older UI code reading it still works
        wd.trigger.cron = trigCron.value.trim();
      }
    }

    // Execution
    const exModel = document.getElementById("wfSetExecModel");
    if (exModel) {
      if (!wd.execution) wd.execution = {};
      wd.execution.default_model = exModel.value;
      const exTimeout = document.getElementById("wfSetExecTimeout");
      if (exTimeout) wd.execution.timeout = parseInt(exTimeout.value) || 120;
      const exBudget = document.getElementById("wfSetExecTokenBudget");
      if (exBudget) wd.execution.token_budget = parseInt(exBudget.value) || 0;
      const exOnErr = document.getElementById("wfSetExecOnError");
      if (exOnErr) wd.execution.on_error = exOnErr.value;
      const exRetries = document.getElementById("wfSetExecRetries");
      if (exRetries) wd.execution.max_retries = parseInt(exRetries.value) || 3;
      const exOutMode = document.getElementById("wfSetExecOutputMode");
      if (exOutMode) wd.execution.output_mode = exOutMode.value;
    }

    // Security
    const secAuth = document.getElementById("wfSetSecRequireAuth");
    if (secAuth) {
      if (!wd.security) wd.security = {};
      wd.security.require_auth = secAuth.checked;
      const secRoles = document.getElementById("wfSetSecRoles");
      if (secRoles) wd.security.allowed_roles = _splitList(secRoles.value);
      const secRate = document.getElementById("wfSetSecRateLimit");
      if (secRate) wd.security.rate_limit = parseInt(secRate.value) || 0;
      const secAudit = document.getElementById("wfSetSecAudit");
      if (secAudit) wd.security.audit_log = secAudit.checked;
      const secEncrypt = document.getElementById("wfSetSecEncryptVars");
      if (secEncrypt) wd.security.encrypt_variables = secEncrypt.checked;
    }

    // Variables are already live-updated via _updateWfVar
  }

  // ── Property Panel Tabs ──────────────────────────────────────
  window._switchPropTab = function (btn, tab) {
    const panel = document.getElementById("wfPropPanel");
    if (!panel) return;
    panel.querySelectorAll(".wf-prop-tab").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    ["wfPropTabProps", "wfPropTabParams", "wfPropTabExec"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = "none";
    });
    const targetId = tab === "props" ? "wfPropTabProps" : tab === "params" ? "wfPropTabParams" : "wfPropTabExec";
    const target = document.getElementById(targetId);
    if (target) target.style.display = "";
  };

  // ── Block Config Helpers ─────────────────────────────────────
  window._updateBlockConfig = function (blockId, field, value) {
    const fd = window._wfDesigner;
    if (!fd) return;
    const block = fd.blocks.get(blockId);
    if (!block) return;
    if (!block.config) block.config = {};
    if (value === null || value === "") delete block.config[field];
    else block.config[field] = value;
  };

  // ── Skill schema cache (populated lazily) ───────────────────────────
  // GET /skills/{id} is cheap but running it every time a block renders
  // (or drops) is wasteful. Cache per-session.
  const _skillSchemaCache = new Map();

  async function _fetchSkillSchema(skillName) {
    if (_skillSchemaCache.has(skillName)) return _skillSchemaCache.get(skillName);
    try {
      const r = await fetch(`/skills/${encodeURIComponent(skillName)}`);
      if (!r.ok) { _skillSchemaCache.set(skillName, null); return null; }
      const data = await r.json();
      const schema = data?.metadata?.parameters || null;
      _skillSchemaCache.set(skillName, schema);
      return schema;
    } catch (_) {
      _skillSchemaCache.set(skillName, null);
      return null;
    }
  }

  // Opinionated per-skill "quick start" presets that pick sensible typical
  // values when the JSON schema doesn't have defaults. Keeps the first-time
  // experience frictionless — user drops a block and most fields are already
  // filled with the most common choice; they only need to tweak what matters.
  const _SKILL_QUICK_PRESETS = {
    "mcp-web-search":      { max_results: 5, search_depth: "basic" },
    "mcp-notion-crud":     { action: "list" },           // safest default — no writes
    "mcp-google-calendar": { action: "today" },
    "mcp-schedule-manager":{ action: "list" },
  };

  // Populate config.params with every schema property so user SEES all the
  // fields and can decide which to fill. Values come from (in order):
  //   1. Existing value (don't clobber)
  //   2. Quick-preset map above (typical value for common skills)
  //   3. schema.default
  //   4. Empty string (user fills in)
  // Required fields without a default are kept empty so the UI can highlight
  // them red and prompt the user.
  //
  // Even when the skill's SKILL.md has NO `parameters:` block, we still apply
  // the quick-preset keys so common skills don't render an empty panel.
  async function _prefillBlockParamsFromSchema(block, skillName) {
    const schema = await _fetchSkillSchema(skillName);
    if (!block || !block.config) return;
    if (!block.config.params) block.config.params = {};
    const presets = _SKILL_QUICK_PRESETS[skillName] || {};

    // 1) From schema (if available)
    if (schema && schema.properties) {
      Object.entries(schema.properties).forEach(([pname, pdef]) => {
        if (block.config.params[pname]) return;
        pdef = pdef || {};
        let val = "";
        if (presets[pname] !== undefined) val = String(presets[pname]);
        else if (pdef.default !== undefined && pdef.default !== null) val = String(pdef.default);
        block.config.params[pname] = { source: "fixed", value: val };
      });
    }

    // 2) From presets (guarantees common fields appear even without schema)
    Object.entries(presets).forEach(([pname, pval]) => {
      if (block.config.params[pname]) return;
      block.config.params[pname] = { source: "fixed", value: String(pval) };
    });

    if (window._wfDesigner && typeof window._wfDesigner._markDirty === "function") {
      try { window._wfDesigner._markDirty(); } catch (_) {}
    }
  }

  // ── Helper: render the "fixed" value input with schema-aware widgets ──
  // If the skill's parameter schema declares enum / type=integer / etc, render
  // a <select> or <input type=number> accordingly so users can't accidentally
  // fill "search_depth=1" (valid text, but Tavily rejects with 400).
  function _renderFixedParamInput(blockId, pName, currentVal, paramSchema) {
    const updateFn = `window._updateBlockParam(${blockId},'${pName}','value',this.value)`;
    // 1. Enum → dropdown. If the currently-stored value is not in the enum
    // (e.g. legacy "1" from before we added validation), show it as a red
    // invalid option at the top so the user can SEE there's a problem.
    if (paramSchema && Array.isArray(paramSchema.enum) && paramSchema.enum.length > 0) {
      const def = paramSchema.default != null ? String(paramSchema.default) : "";
      const cur = currentVal != null && currentVal !== "" ? String(currentVal) : def;
      const isValid = paramSchema.enum.map(String).includes(cur);
      const invalidOpt = (!isValid && currentVal) ? `<option value="${_escHtml(String(currentVal))}" selected style="color:var(--color-error);background:var(--color-error-bg);">⚠️ 目前值：${_escHtml(String(currentVal))}（不合法，請選新值）</option>` : "";
      const opts = paramSchema.enum.map(v => {
        const vs = String(v);
        const sel = isValid && vs === cur ? " selected" : "";
        return `<option value="${_escHtml(vs)}"${sel}>${_escHtml(vs)}</option>`;
      }).join("");
      return `<select class="wf-param-map-val" onchange="${updateFn}">${invalidOpt}${opts}</select>`;
    }
    // 2. Integer / number → numeric input with min/max
    const t = paramSchema && paramSchema.type;
    if (t === "integer" || t === "number") {
      const min = paramSchema.minimum != null ? ` min="${paramSchema.minimum}"` : "";
      const max = paramSchema.maximum != null ? ` max="${paramSchema.maximum}"` : "";
      const step = t === "integer" ? ' step="1"' : "";
      const def = paramSchema.default != null ? String(paramSchema.default) : "";
      const cur = currentVal != null && currentVal !== "" ? _escHtml(String(currentVal)) : "";
      const placeholder = def ? `placeholder="預設: ${_escHtml(def)}"` : 'placeholder="數字"';
      return `<input class="wf-param-map-val" type="number"${min}${max}${step} value="${cur}" ${placeholder} onchange="${updateFn}" />`;
    }
    // 3. Array / object → JSON textarea with sample
    if (t === "array" || t === "object") {
      const cur = currentVal != null ? _escHtml(typeof currentVal === "string" ? currentVal : JSON.stringify(currentVal)) : "";
      const sample = t === "array" ? '["item1","item2"]' : '{"key":"value"}';
      return `<input class="wf-param-map-val" type="text" value="${cur}" placeholder="JSON: ${sample}" onchange="${updateFn}" />`;
    }
    // 4. Default — string text input
    const cur = _escHtml(currentVal || "");
    const def = paramSchema && paramSchema.default != null ? `預設: ${_escHtml(String(paramSchema.default))}` : "固定值";
    return `<input class="wf-param-map-val" type="text" value="${cur}" onchange="${updateFn}" placeholder="${def}" />`;
  }

  // ── Phase 4: Parallel Block Editor ──────────────────────────────────
  // A parallel block has config.branches = [{skill_id, params, output_var}, ...]
  // plus config.merge_output_var and config.on_fail. Users add/remove branches
  // and pick a skill per branch. Each branch's skill params are read from
  // SKILL.md (same machinery as sequential blocks, but flattened into one row
  // per param for space).
  function _renderParallelBlockEditor(block, container, fd) {
    if (!block.config) block.config = {};
    if (!Array.isArray(block.config.branches)) block.config.branches = [];
    if (!block.config.merge_output_var) block.config.merge_output_var = `parallel_${block.id}_merged`;
    if (!block.config.on_fail) block.config.on_fail = "abort";

    const skillOpts = Object.entries(_dynamicSkills || {})
      .filter(([name]) => !["start", "end", "branch", "parallel", "sub-workflow"].includes(name))
      .map(([name, info]) => `<option value="${_escHtml(name)}">${_escHtml(name)}${info.ready === false ? " (未就緒)" : ""}</option>`)
      .join("");

    const branchRows = block.config.branches.map((br, i) => {
      const paramCount = Object.keys(br.params || {}).length;
      return `<div class="wf-parallel-branch" data-bi="${i}" style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;margin-bottom:8px;background:#fff;">
        <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
          <span style="background:#ede7f6;color:var(--wf-purple);font-size:0.7rem;font-weight:700;padding:2px 8px;border-radius:4px;">分支 ${i + 1}</span>
          <input type="text" placeholder="標籤（選填）" value="${_escHtml(br.label || "")}" style="flex:1;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;"
            onchange="window._updateParallelBranch(${block.id}, ${i}, 'label', this.value)" />
          <button title="移除分支" onclick="window._removeParallelBranch(${block.id}, ${i})"
            style="background:transparent;border:none;color:var(--color-error);cursor:pointer;font-size:0.9rem;">✕</button>
        </div>
        <label style="font-size:0.7rem;color:var(--text-muted);display:block;margin-bottom:3px;">技能</label>
        <select style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;margin-bottom:6px;"
          onchange="window._updateParallelBranch(${block.id}, ${i}, 'skill_id', this.value)">
          <option value="">選擇技能</option>
          ${skillOpts.replace(`value="${_escHtml(br.skill_id || "")}"`, `value="${_escHtml(br.skill_id || "")}" selected`)}
        </select>
        <label style="font-size:0.7rem;color:var(--text-muted);display:block;margin-bottom:3px;">輸出變數名稱</label>
        <input type="text" placeholder="例：news_result" value="${_escHtml(br.output_var || "")}"
          style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;margin-bottom:6px;"
          onchange="window._updateParallelBranch(${block.id}, ${i}, 'output_var', this.value)" />
        <div style="font-size:0.68rem;color:var(--text-tertiary);">
          參數設定：${paramCount} 個（打開此分支的屬性面板編輯，或直接改 JSON）
          <button style="float:right;background:transparent;border:1px solid #e5e7eb;border-radius:4px;padding:2px 8px;font-size:0.68rem;cursor:pointer;"
            onclick="window._editParallelBranchParams(${block.id}, ${i})">參數...</button>
        </div>
      </div>`;
    }).join("");

    container.innerHTML = `
      <div style="background:#f3e5f5;padding:8px 10px;border-radius:6px;margin-bottom:10px;font-size:0.72rem;color:#6a1b9a;">
        🔀 <strong>並行分支</strong>：以下分支會<strong>同時</strong>執行，全部完成後結果會匯合到下方的「匯合變數」。
      </div>
      ${branchRows || '<div style="padding:10px;text-align:center;color:var(--text-tertiary);font-size:0.72rem;">尚無分支 — 點下方「+ 新增分支」</div>'}
      <button style="width:100%;padding:6px;background:var(--wf-purple);color:#fff;border:none;border-radius:6px;font-size:0.72rem;cursor:pointer;margin-bottom:10px;"
        onclick="window._addParallelBranch(${block.id})">+ 新增分支</button>
      <div style="margin-bottom:8px;">
        <label style="font-size:0.72rem;color:var(--text-secondary);display:block;margin-bottom:3px;">匯合後變數名稱</label>
        <input type="text" value="${_escHtml(block.config.merge_output_var)}"
          style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;"
          onchange="window._updateBlockConfig(${block.id}, 'merge_output_var', this.value)" />
        <div style="font-size:0.66rem;color:var(--text-tertiary);margin-top:3px;">後續節點可用 <code>\${${_escHtml(block.config.merge_output_var)}}</code> 引用匯合結果 (JSON)</div>
      </div>
      <div>
        <label style="font-size:0.72rem;color:var(--text-secondary);display:block;margin-bottom:3px;">分支失敗策略</label>
        <select style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;"
          onchange="window._updateBlockConfig(${block.id}, 'on_fail', this.value)">
          <option value="abort" ${block.config.on_fail === "abort" ? "selected" : ""}>中止整個工作流 (abort)</option>
          <option value="continue" ${block.config.on_fail === "continue" ? "selected" : ""}>繼續（此分支標記失敗）(continue)</option>
          <option value="skip" ${block.config.on_fail === "skip" ? "selected" : ""}>跳過此分支 (skip)</option>
        </select>
      </div>
    `;
  }

  window._addParallelBranch = function (blockId) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block) return;
    if (!Array.isArray(block.config.branches)) block.config.branches = [];
    const idx = block.config.branches.length + 1;
    block.config.branches.push({
      branch_id: `branch_${blockId}_${idx}`,
      skill_id: "",
      label: "",
      params: {},
      output_var: `branch_${blockId}_${idx}_output`,
    });
    _renderParallelBlockEditor(block, document.getElementById("wfPropTabParams"), fd);
  };

  window._removeParallelBranch = function (blockId, branchIdx) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block || !Array.isArray(block.config?.branches)) return;
    block.config.branches.splice(branchIdx, 1);
    _renderParallelBlockEditor(block, document.getElementById("wfPropTabParams"), fd);
  };

  window._updateParallelBranch = function (blockId, branchIdx, field, value) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block || !Array.isArray(block.config?.branches)) return;
    if (!block.config.branches[branchIdx]) return;
    block.config.branches[branchIdx][field] = value;
  };

  // Inline params editor for a single branch — small modal with JSON textarea
  // (complete param UI like sequential blocks is overkill for parallel since
  // most branches use simple fixed/variable mappings)
  window._editParallelBranchParams = function (blockId, branchIdx) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block?.config?.branches?.[branchIdx]) return;
    const branch = block.config.branches[branchIdx];
    const existing = document.getElementById("wf-branch-params-modal");
    if (existing) existing.remove();
    const mask = document.createElement("div");
    mask.id = "wf-branch-params-modal";
    mask.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:99999;display:flex;align-items:center;justify-content:center;";
    const current = JSON.stringify(branch.params || {}, null, 2);
    mask.innerHTML = `
      <div style="background:#fff;width:var(--modal-width-md);max-width:92vw;border-radius:var(--modal-radius);padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.25);">
        <h3 style="margin:0 0 8px;font-size:15px;">分支 ${branchIdx + 1} 參數 (${_escHtml(branch.skill_id || "未選技能")})</h3>
        <div style="font-size:12px;color:#666;margin-bottom:10px;">JSON 格式。每個 key 是技能的參數名，value 是 {source, value} 或直接字串。</div>
        <textarea id="wf-branch-params-ta" style="width:100%;height:240px;font-family:var(--font-mono);font-size:12px;padding:10px;border:1px solid #ddd;border-radius:6px;">${_escHtml(current)}</textarea>
        <div style="text-align:right;margin-top:12px;">
          <button id="wf-branch-params-cancel" style="padding:6px 14px;margin-right:8px;background:transparent;color:#666;border:1px solid #ddd;border-radius:4px;cursor:pointer;">取消</button>
          <button id="wf-branch-params-save" style="padding:6px 14px;background:var(--wf-purple);color:#fff;border:none;border-radius:4px;cursor:pointer;">儲存</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.querySelector("#wf-branch-params-cancel").onclick = () => mask.remove();
    mask.addEventListener("click", e => { if (e.target === mask) mask.remove(); });
    mask.querySelector("#wf-branch-params-save").onclick = () => {
      const text = document.getElementById("wf-branch-params-ta").value;
      try {
        const parsed = JSON.parse(text);
        if (typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("必須是物件");
        branch.params = parsed;
        mask.remove();
        _renderParallelBlockEditor(block, document.getElementById("wfPropTabParams"), fd);
      } catch (e) {
        if (window.showToast) window.showToast("❌ JSON 格式錯誤：" + e.message, "error");
      }
    };
  };

  // ── Phase 4: Sub-Workflow Block Editor ──────────────────────────────
  // A sub-workflow block has config.sub_workflow_id pointing to another
  // workflow that should be executed as a step here. Cycle detection
  // (_execution_stack in executor) prevents A→B→A loops automatically.
  async function _renderSubWorkflowBlockEditor(block, container, fd) {
    if (!block.config) block.config = {};
    container.innerHTML = `<div style="padding:10px;color:var(--text-muted);font-size:0.72rem;">載入工作流清單...</div>`;

    // Fetch available workflows (all scopes) and filter out self to prevent
    // the most obvious cycle at UI level.
    let workflows = [];
    try {
      const user = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
      const q = new URLSearchParams();
      if (user.department_code) q.set("dept_code", user.department_code);
      if (user.employee_id || user.id) q.set("owner", user.employee_id || user.id);
      const r = await fetch(`/api/workflows?${q.toString()}`);
      if (r.ok) {
        const data = await r.json();
        workflows = data.workflows || [];
      }
    } catch (_) {}

    // Cache the name→id map on designer so the block subtitle can look up
    // the display name without re-fetching every time.
    if (fd) {
      fd._wfNameCache = fd._wfNameCache || {};
      workflows.forEach(w => {
        const wid = w.workflow_id || w.id;
        if (wid) fd._wfNameCache[wid] = w.display_name || w.name || wid;
      });
    }
    // Refresh THIS block's subtitle now that cache is populated
    _updateSubWorkflowBlockLabel(block, fd);

    const selfId = fd?._currentWfId || "";
    const options = workflows
      .filter(w => (w.workflow_id || w.id) !== selfId)
      .map(w => {
        const wid = w.workflow_id || w.id;
        const name = w.display_name || w.name || wid;
        const scope = w.scope ? ` [${w.scope}]` : "";
        const sel = block.config.sub_workflow_id === wid ? " selected" : "";
        return `<option value="${_escHtml(wid)}"${sel}>${_escHtml(name)}${scope}</option>`;
      }).join("");

    const passVars = Array.isArray(block.config.pass_vars) ? block.config.pass_vars : [];
    const wfVars = _getVariableList(fd._wfData);

    container.innerHTML = `
      <div style="background:#eceff1;padding:8px 10px;border-radius:6px;margin-bottom:10px;font-size:0.72rem;color:#455a64;">
        📎 <strong>子工作流</strong>：呼叫另一個已儲存的工作流作為本步驟。系統會偵測循環呼叫 (A→B→A)，最多允許 5 層巢狀。
      </div>
      <div style="margin-bottom:10px;">
        <label style="font-size:0.72rem;color:var(--text-secondary);display:block;margin-bottom:3px;">選擇子工作流</label>
        <select style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;"
          onchange="window._updateSubWorkflowId(${block.id}, this.value, this.options[this.selectedIndex]?.textContent || '')">
          <option value="">— 請選擇 —</option>
          ${options}
        </select>
        ${!options ? '<div style="font-size:0.66rem;color:var(--color-error);margin-top:4px;">⚠️ 沒有可選的工作流（自己無法引用自己）</div>' : ""}
      </div>
      <div style="margin-bottom:10px;">
        <label style="font-size:0.72rem;color:var(--text-secondary);display:block;margin-bottom:3px;">傳入變數 (以逗號分隔的變數名)</label>
        <input type="text" value="${_escHtml(passVars.join(", "))}"
          placeholder="例：topic, lang"
          style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;"
          onchange="window._updateSubWorkflowPassVars(${block.id}, this.value)" />
        <div style="font-size:0.66rem;color:var(--text-tertiary);margin-top:3px;">
          本工作流目前可傳入的變數：${wfVars.map(v => `<code style="background:var(--bg-hover);padding:1px 4px;border-radius:3px;margin:0 2px;">${_escHtml(v.name)}</code>`).join("") || "（尚未定義）"}
        </div>
      </div>
      <div style="margin-bottom:10px;">
        <label style="font-size:0.72rem;color:var(--text-secondary);display:block;margin-bottom:3px;">接收輸出的變數名</label>
        <input type="text" value="${_escHtml(block.config.output_var || "")}"
          placeholder="例：sub_result"
          style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;"
          onchange="window._updateBlockConfig(${block.id}, 'output_var', this.value)" />
      </div>
      <div>
        <label style="font-size:0.72rem;color:var(--text-secondary);display:block;margin-bottom:3px;">子工作流失敗策略</label>
        <select style="width:100%;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;font-size:0.72rem;"
          onchange="window._updateBlockConfig(${block.id}, 'on_fail', this.value)">
          <option value="abort" ${block.config.on_fail === "abort" || !block.config.on_fail ? "selected" : ""}>中止整個工作流 (abort)</option>
          <option value="continue" ${block.config.on_fail === "continue" ? "selected" : ""}>繼續執行主流程 (continue)</option>
          <option value="skip" ${block.config.on_fail === "skip" ? "selected" : ""}>跳過 (skip)</option>
        </select>
      </div>
    `;
  }

  window._updateSubWorkflowPassVars = function (blockId, raw) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block) return;
    const list = (raw || "").split(/[,，、;；]+/).map(s => s.trim()).filter(Boolean);
    if (!block.config) block.config = {};
    block.config.pass_vars = list;
  };

  // When user picks a sub-workflow, update config AND refresh the block's
  // subtitle so the canvas shows the target workflow's name instead of the
  // generic "控制" category label.
  window._updateSubWorkflowId = function (blockId, wfId, optionLabel) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block) return;
    if (!block.config) block.config = {};
    block.config.sub_workflow_id = wfId;
    // Cache the display name so refresh works after save/reload
    if (wfId && optionLabel) {
      fd._wfNameCache = fd._wfNameCache || {};
      // Option label was "顯示名 [scope]" — strip the scope suffix
      const cleanLabel = String(optionLabel).replace(/\s*\[[^\]]*\]\s*$/, "").trim();
      fd._wfNameCache[wfId] = cleanLabel || wfId;
    }
    _updateSubWorkflowBlockLabel(block, fd);
  };

  // Pre-populate _wfNameCache with all visible workflows so block subtitles
  // render their target names immediately on page load (without having to
  // open the sub-workflow block's config first).
  async function _primeSubWorkflowNameCache(fd) {
    if (!fd) return;
    if (fd._wfNameCachePrimed) return;
    fd._wfNameCache = fd._wfNameCache || {};
    try {
      const user = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
      const q = new URLSearchParams();
      if (user.department_code) q.set("dept_code", user.department_code);
      if (user.employee_id || user.id) q.set("owner", user.employee_id || user.id);
      const r = await fetch(`/api/workflows?${q.toString()}`);
      if (!r.ok) return;
      const data = await r.json();
      (data.workflows || []).forEach(w => {
        const wid = w.workflow_id || w.id;
        if (wid) fd._wfNameCache[wid] = w.display_name || w.name || wid;
      });
      fd._wfNameCachePrimed = true;
    } catch (_) { /* non-fatal */ }
  }

  // Read block.config.sub_workflow_id, look up its friendly name in the
  // workflow cache, and rewrite the subtitle span. Falls back to the
  // default category label when no selection is made.
  function _updateSubWorkflowBlockLabel(block, fd) {
    if (!block || !block.el) return;
    const sub = document.querySelector(`.wf-block[data-id="${block.id}"] .wf-block-subtitle`);
    if (!sub) return;
    if (block.type !== "sub-workflow") return;
    const wfId = block.config?.sub_workflow_id;
    if (!wfId) {
      // Reset to default category label
      const def = BLOCK_DEFS[block.type];
      sub.textContent = CATEGORIES[def.category]?.label || def.category;
      sub.style.color = "";
      sub.title = "";
      return;
    }
    const name = fd?._wfNameCache?.[wfId] || wfId;
    sub.textContent = "→ " + name;
    sub.style.color = "var(--wf-purple)";
    sub.title = `子工作流 ID: ${wfId}`;
  }

  // Simple-mode row: native widget (text/number/select/checkbox) + variable-pick button.
  // No "source" dropdown. User writes literal values or ${varName}.
  function _renderParamRowSimple(block, pName, pv, pSchema, wfVars, extraStyle) {
    const bid = block.id;
    const val = pv.value == null ? "" : pv.value;
    const updateFn = `window._updateBlockParamSimple(${bid}, '${_escHtml(pName)}', this.value)`;
    const INPUT_CSS = "width:100%;padding:5px 8px;border:1px solid #cbd5e1;border-radius:5px;font-size:0.74rem;box-sizing:border-box;background:#fff;";

    // 1. Enum → dropdown
    if (pSchema?.enum && Array.isArray(pSchema.enum) && pSchema.enum.length > 0) {
      const def = pSchema.default != null ? String(pSchema.default) : "";
      const cur = val !== "" ? String(val) : def;
      const isValid = pSchema.enum.map(String).includes(cur);
      const invalidOpt = (!isValid && val) ? `<option value="${_escHtml(String(val))}" selected style="color:var(--color-error);">⚠️ ${_escHtml(String(val))}</option>` : "";
      const opts = pSchema.enum.map(v => {
        const vs = String(v);
        const sel = isValid && vs === cur ? " selected" : "";
        return `<option value="${_escHtml(vs)}"${sel}>${_escHtml(vs)}</option>`;
      }).join("");
      const placeholder = !cur ? '<option value="">— 請選擇 —</option>' : '';
      return `<select onchange="${updateFn}" ${extraStyle} style="${INPUT_CSS}">${placeholder}${invalidOpt}${opts}</select>`;
    }

    // 2. Integer / number → number input
    if (pSchema?.type === "integer" || pSchema?.type === "number") {
      const min = pSchema.minimum != null ? ` min="${pSchema.minimum}"` : "";
      const max = pSchema.maximum != null ? ` max="${pSchema.maximum}"` : "";
      const step = pSchema.type === "integer" ? ' step="1"' : "";
      const ph = pSchema.default != null ? `預設 ${pSchema.default}` : "數字";
      return `<input type="number"${min}${max}${step} value="${_escHtml(String(val))}" placeholder="${ph}" onchange="${updateFn}" ${extraStyle} style="${INPUT_CSS}" />`;
    }

    // 3. Boolean → checkbox
    if (pSchema?.type === "boolean") {
      const checked = val === true || val === "true" || val === 1 ? "checked" : "";
      return `<label style="display:inline-flex;align-items:center;gap:6px;font-size:0.74rem;cursor:pointer;padding:3px 0;">
        <input type="checkbox" ${checked} onchange="window._updateBlockParamSimple(${bid}, '${_escHtml(pName)}', this.checked)" />
        啟用
      </label>`;
    }

    // 4. String / default → text input with inline variable-picker icon
    // Variable icon is a small 📎 absolutely positioned at the right edge of
    // the input so it doesn't steal horizontal space.
    const ph = pSchema?.default != null ? `預設：${_escHtml(String(pSchema.default))}` : "輸入值或 ${變數名}";
    const varIcon = wfVars.length > 0
      ? `<button type="button" title="插入變數" onclick="window._openVarPicker(event, ${bid}, '${_escHtml(pName)}')" style="position:absolute;right:4px;top:50%;transform:translateY(-50%);width:22px;height:22px;padding:0;border:none;background:transparent;color:var(--text-tertiary);cursor:pointer;font-size:0.75rem;border-radius:3px;display:flex;align-items:center;justify-content:center;">📎</button>`
      : "";
    const inputStyle = INPUT_CSS + (wfVars.length ? "padding-right:28px;" : "");
    return `<div style="position:relative;">
      <input type="text" data-param-input="${_escHtml(pName)}" value="${_escHtml(String(val))}" placeholder="${ph}" onchange="${updateFn}" oninput="${updateFn}" ${extraStyle} style="${inputStyle}" />
      ${varIcon}
    </div>`;
  }

  // Advanced-mode row: show source dropdown + legacy UI
  function _renderParamRowAdvanced(block, pName, pv, pSchema, wfVars) {
    const bid = block.id;
    const varOpts = wfVars.map(v => `<option value="{{${v.name}}}">${v.name}</option>`).join("");
    const selectedVarOpts = pv.source === "variable" && pv.value
      ? varOpts.replace(`value="${_escHtml(pv.value)}"`, `value="${_escHtml(pv.value)}" selected`)
      : varOpts;
    return `<div style="display:flex;gap:6px;align-items:stretch;">
      <select onchange="window._updateBlockParam(${bid},'${_escHtml(pName)}','source',this.value)" style="padding:5px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:0.72rem;flex:0 0 110px;">
        <option value="fixed" ${pv.source === "fixed" ? "selected" : ""}>固定值</option>
        <option value="variable" ${pv.source === "variable" ? "selected" : ""}>綁定變數</option>
        <option value="previous_step" ${pv.source === "previous_step" ? "selected" : ""}>前一步輸出</option>
        <option value="auto" ${pv.source === "auto" ? "selected" : ""}>自動 (LLM)</option>
      </select>
      ${pv.source === "variable"
        ? `<select onchange="window._updateBlockParam(${bid},'${_escHtml(pName)}','value',this.value)" style="flex:1;padding:5px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:0.72rem;">
            <option value="">選擇變數</option>${selectedVarOpts}</select>`
        : pv.source === "fixed"
          ? _renderFixedParamInput(bid, pName, pv.value, pSchema)
          : `<span style="flex:1;padding:5px 8px;color:var(--text-muted);font-size:0.72rem;">${pv.source === "previous_step" ? "自動帶入前一節點的輸出" : "由 LLM 根據描述推斷"}</span>`}
    </div>`;
  }

  // Simple-mode updater — always stores as source=fixed. Executor interpolates
  // ${varName} at runtime, so users don't need to pick a "source".
  window._updateBlockParamSimple = function (blockId, paramName, value) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block) return;
    if (!block.config) block.config = {};
    if (!block.config.params) block.config.params = {};
    block.config.params[paramName] = { source: "fixed", value };
    // Don't re-render whole panel on every keystroke — just clear red border
    // if value is now non-empty
    const card = document.querySelector(`.wf-param-card[data-param="${CSS.escape(paramName)}"]`);
    if (card && value !== "" && value != null) {
      card.querySelectorAll("input, select").forEach(el => {
        el.style.borderColor = "#cbd5e1";
        el.style.background = "";
      });
    }
  };

  window._toggleBlockParamsAdvanced = function (blockId, on) {
    const fd = window._wfDesigner;
    const block = fd?.blocks?.get(blockId);
    if (!block) return;
    block._paramsAdvanced = !!on;
    // Re-render
    const skillName = block.type.startsWith("mcp-") ? block.type : "mcp-" + block.type;
    const container = document.getElementById("wfPropTabParams");
    _fetchSkillSchema(skillName).then(schema => {
      _renderBlockParams(block, container, fd, skillName, schema);
    });
  };

  // Variable picker popover — anchored to the button that triggered it.
  // Lists defined workflow variables + system vars; click inserts ${name}
  // into the nearest text input.
  window._openVarPicker = function (evt, blockId, paramName) {
    evt.stopPropagation();
    evt.preventDefault();
    document.getElementById("wfVarPickerPopup")?.remove();
    const fd = window._wfDesigner;
    const wfVars = _getVariableList(fd._wfData || {});
    const systemVars = ["_current_date", "_current_time", "_user_name", "_user_dept", "_session_id"];
    const rows = [];
    if (wfVars.length) {
      rows.push(`<div style="padding:4px 10px;font-size:0.65rem;color:var(--text-muted);background:#f9fafb;">自訂變數</div>`);
      wfVars.forEach(v => {
        rows.push(`<button type="button" data-var="${_escHtml(v.name)}" style="display:block;width:100%;text-align:left;padding:6px 12px;background:transparent;border:none;cursor:pointer;font-size:0.75rem;color:var(--text-primary);">
          <strong>\${${_escHtml(v.name)}}</strong>
          ${v.description ? `<span style="color:var(--text-tertiary);font-size:0.68rem;"> — ${_escHtml(v.description).slice(0, 40)}</span>` : ""}
        </button>`);
      });
    }
    rows.push(`<div style="padding:4px 10px;font-size:0.65rem;color:var(--text-muted);background:#f9fafb;">系統變數</div>`);
    systemVars.forEach(v => {
      rows.push(`<button type="button" data-var="${_escHtml(v)}" style="display:block;width:100%;text-align:left;padding:6px 12px;background:transparent;border:none;cursor:pointer;font-size:0.75rem;color:var(--text-primary);">
        <strong>\${${_escHtml(v)}}</strong>
      </button>`);
    });

    const popup = document.createElement("div");
    popup.id = "wfVarPickerPopup";
    popup.style.cssText = "position:fixed;z-index:9900;min-width:220px;max-height:320px;overflow-y:auto;background:#fff;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.15);padding:4px 0;";
    popup.innerHTML = rows.join("");
    document.body.appendChild(popup);

    const btn = evt.currentTarget || evt.target;
    const rect = btn.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;
    const pw = popup.offsetWidth || 220;
    const ph = popup.offsetHeight || 200;
    if (left + pw > window.innerWidth - 4) left = window.innerWidth - pw - 4;
    if (top + ph > window.innerHeight - 4) top = rect.top - ph - 4;
    popup.style.left = left + "px";
    popup.style.top = top + "px";

    const close = () => {
      popup.remove();
      document.removeEventListener("mousedown", outside, true);
      document.removeEventListener("keydown", escHandler, true);
    };
    const outside = (e) => { if (!popup.contains(e.target) && e.target !== btn) close(); };
    const escHandler = (e) => { if (e.key === "Escape") close(); };
    setTimeout(() => {
      document.addEventListener("mousedown", outside, true);
      document.addEventListener("keydown", escHandler, true);
    }, 0);

    popup.querySelectorAll("button[data-var]").forEach(b => {
      b.addEventListener("mouseenter", () => { b.style.background = "var(--bg-hover)"; });
      b.addEventListener("mouseleave", () => { b.style.background = "transparent"; });
      b.addEventListener("click", () => {
        const varName = b.dataset.var;
        const inp = document.querySelector(`input[data-param-input="${CSS.escape(paramName)}"]`);
        if (!inp) { close(); return; }
        const cur = inp.value || "";
        const pos = inp.selectionStart != null ? inp.selectionStart : cur.length;
        const newVal = cur.slice(0, pos) + "${" + varName + "}" + cur.slice(pos);
        inp.value = newVal;
        inp.focus();
        inp.setSelectionRange(pos + varName.length + 3, pos + varName.length + 3);
        // trigger update
        window._updateBlockParamSimple(blockId, paramName, newVal);
        close();
      });
    });
  };

  // ── Block Params Renderer — Simple Mode by default ─────────────────
  // Non-programmer UX: each param is ONE row with label + native widget.
  // No "source" dropdown visible by default. Text inputs accept ${varName}
  // for variable substitution (executor auto-interpolates). Advanced toggle
  // reveals the source dropdown for edge cases (previous_step / auto LLM).
  function _renderBlockParams(block, container, fd, skillName, schema) {
    const cfg    = block.config || {};
    const params = cfg.params || {};
    const wfVars = _getVariableList(fd._wfData);

    let schemaParams = [];
    if (schema?.properties) schemaParams = Object.keys(schema.properties);
    const configuredParams = Object.keys(params);
    const allParams = [...new Set([...schemaParams, ...configuredParams])];
    if (allParams.length === 0 && schema !== undefined) allParams.push("input");

    // Header + advanced toggle on the same row — compact
    const isAdv = !!block._paramsAdvanced;
    let summary = "";
    if (schema === undefined) summary = "載入中…";
    else if (!schema?.properties || schemaParams.length === 0) summary = "⚠️ 無參數規格";
    else {
      const reqCount = (schema.required || []).length;
      summary = `${schemaParams.length} 個參數 · <strong>${reqCount}</strong> 個必填`;
    }
    const header = `<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 2px 8px;border-bottom:1px solid #e2e8f0;margin-bottom:4px;font-size:0.68rem;color:var(--text-muted);">
      <span>${summary}</span>
      <label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px;user-select:none;">
        <input type="checkbox" ${isAdv ? "checked" : ""} onchange="window._toggleBlockParamsAdvanced(${block.id}, this.checked)" style="margin:0;" />
        進階
      </label>
    </div>`;
    const advToggle = "";

    // Build rows — simple mode uses compact single-row layout
    let pHtml = header + advToggle;
    allParams.forEach(pName => {
      const pv = params[pName] || { source: "fixed", value: "" };
      const pSchema = schema?.properties?.[pName] || null;
      const isReq = (schema?.required || []).includes(pName);
      const desc = pSchema?.description || "";
      const isEmpty = (pv.value === "" || pv.value == null) && pv.source !== "auto" && pv.source !== "previous_step";
      const emptyClass = isReq && isEmpty ? 'style="border-color:var(--color-error);background:var(--color-error-bg);"' : "";
      const removeBtn = !schemaParams.includes(pName)
        ? `<button title="移除" onclick="window._removeBlockParam(${block.id},'${_escHtml(pName)}')" style="border:none;background:transparent;color:#cbd5e1;cursor:pointer;font-size:0.75rem;padding:0 0 0 4px;">✕</button>`
        : "";

      pHtml += `<div class="wf-param-card" data-param="${pName}">
        <div style="display:flex;align-items:baseline;gap:4px;margin-bottom:4px;">
          <label>${_escHtml(pName)}${isReq ? ' <span style="color:var(--color-error);">*</span>' : ''}</label>
          <span style="flex:1;"></span>
          ${removeBtn}
        </div>
        ${desc ? `<div style="font-size:0.66rem;color:var(--text-tertiary);margin-bottom:5px;line-height:1.4;">${_escHtml(desc)}</div>` : ""}
        ${isAdv
          ? _renderParamRowAdvanced(block, pName, pv, pSchema, wfVars)
          : _renderParamRowSimple(block, pName, pv, pSchema, wfVars, emptyClass)}
      </div>`;
    });

    // Custom param add (still available but de-emphasized)
    pHtml += `<details style="margin-top:10px;"><summary style="cursor:pointer;font-size:0.7rem;color:var(--text-muted);">+ 新增自訂參數</summary>
      <div style="margin-top:6px;display:flex;gap:6px;align-items:center;">
        <input id="wfNewParamKey_${block.id}" style="flex:1;padding:4px 8px;border:1px solid #e5e7eb;border-radius:6px;font-size:0.72rem;" placeholder="參數名稱（英文）" />
        <button onclick="window._addBlockParam(${block.id})"
          style="padding:4px 10px;border-radius:6px;border:none;background:#4a90d9;color:#fff;font-size:0.72rem;cursor:pointer;">新增</button>
      </div>
    </details>`;

    container.innerHTML = pHtml;

    // Re-select the correct option in variable selects (innerHTML replaces DOM)
    container.querySelectorAll(".wf-param-map-val select").forEach(sel => {
      const pn = sel.closest(".wf-param-map-card")?.dataset?.paramKey;
      if (pn && params[pn]?.value) sel.value = params[pn].value;
    });
  }

  window._removeBlockParam = function (blockId, paramName) {
    const fd = window._wfDesigner; if (!fd) return;
    const block = fd.blocks.get(blockId); if (!block) return;
    if (!block.config.params) return;
    delete block.config.params[paramName];
    // Re-render
    const paramsDiv = document.getElementById("wfPropTabParams");
    const skillName = block.type.startsWith("mcp-") ? block.type : "mcp-" + block.type;
    if (paramsDiv) _renderBlockParams(block, paramsDiv, fd, skillName, undefined);
    fetch(`/skills/${skillName}`).then(r => r.ok ? r.json() : null)
      .then(d => { if (paramsDiv) _renderBlockParams(block, paramsDiv, fd, skillName, d?.metadata?.parameters || null); }).catch(() => {});
  };

  window._addBlockParam = function (blockId) {
    const fd = window._wfDesigner; if (!fd) return;
    const block = fd.blocks.get(blockId); if (!block) return;
    const inp = document.getElementById(`wfNewParamKey_${blockId}`);
    const pName = (inp?.value || "").trim();
    if (!pName) return;
    if (!block.config) block.config = {};
    if (!block.config.params) block.config.params = {};
    if (!block.config.params[pName]) block.config.params[pName] = { source: "auto", value: "" };
    if (inp) inp.value = "";
    // Re-render
    const paramsDiv = document.getElementById("wfPropTabParams");
    const skillName = block.type.startsWith("mcp-") ? block.type : "mcp-" + block.type;
    if (paramsDiv) _renderBlockParams(block, paramsDiv, fd, skillName, undefined);
    fetch(`/skills/${skillName}`).then(r => r.ok ? r.json() : null)
      .then(d => { if (paramsDiv) _renderBlockParams(block, paramsDiv, fd, skillName, d?.metadata?.parameters || null); }).catch(() => {});
  };

  window._updateBlockParam = function (blockId, paramName, field, value) {
    const fd = window._wfDesigner;
    if (!fd) return;
    const block = fd.blocks.get(blockId);
    if (!block) return;
    if (!block.config) block.config = {};
    if (!block.config.params) block.config.params = {};
    if (!block.config.params[paramName]) block.config.params[paramName] = { source: "auto", value: "" };
    block.config.params[paramName][field] = value;

    // Re-render params tab when source changes (to show/hide input field)
    if (field === "source") {
      showWfPropPanel(block, fd);
      window._switchPropTab(document.querySelector('.wf-prop-tab[data-tab="params"]'), "params");
    }
  };

  // ── Utility ───────────────────────────────────────────────────
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // ── Sidebar entry: open skill-editor directly ───────────────────
  // Called from chat.html's 「技能管理」 sidebar button (added 2026-04-23).
  // Previously the skill editor was only reachable via the workflow
  // designer's palette header → this shortcut cuts the detour: enter
  // workflow mode if needed, then flip into skill-edit mode in one step.
  async function openSkillManager() {
    const body = document.querySelector(".page-chat-body");
    const inWfMode = body && body.classList.contains("wf-mode");
    // Not yet in workflow mode → enter it first (this shows the designer
    // palette + canvas). toggleWorkflowView handles the open-landing flow.
    if (!inWfMode) {
      await toggleWorkflowView();
    }
    // If landing overlay is visible, close it so we go straight to the
    // designer + palette where skill-edit mode can take over.
    const overlay = document.getElementById("wfLandingOverlay");
    if (overlay && overlay.classList.contains("open")) {
      overlay.classList.remove("open");
    }
    // Flip to skill-edit mode if we're not already there.
    if (!_skillEditMode) {
      await toggleSkillEditMode();
    }
  }

  // Expose to global
  window.toggleWorkflowView = toggleWorkflowView;
  window.toggleSkillEditMode = toggleSkillEditMode;
  window.openSkillManager = openSkillManager;
  window.closeWfPropPanel = closeWfPropPanel;

  // Auto-open workflow landing or specific workflow from query params
  (function () {
    const params = new URLSearchParams(window.location.search);

    // ?openWorkflow=1 → open landing page
    if (params.get("openWorkflow")) {
      history.replaceState(null, "", window.location.pathname);
      function _tryLanding() {
        const btn = document.getElementById("btnWorkflowDesigner");
        if (!btn) { requestAnimationFrame(_tryLanding); return; }
        btn.classList.add("active", "is-active");
        _showWorkflowLanding();
      }
      if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _tryLanding);
      else _tryLanding();
      return;
    }

    // ?wf=xxx → open specific workflow canvas
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
      if (btn) btn.classList.add("active", "is-active");
      _enterWorkflowCanvas(wfId, scope, owner);
    }
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", _tryEnter);
    } else {
      _tryEnter();
    }
  })();

})();
