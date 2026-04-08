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

  const BLOCK_W = 160, BLOCK_H = 68, GRID = 8;
  const snap = v => Math.round(v / GRID) * GRID;

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
      // Arrow marker
      const defs = this.svg.querySelector("defs") || this.svg.appendChild(document.createElementNS("http://www.w3.org/2000/svg", "defs"));
      defs.innerHTML = '<marker id="wf-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker>';

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
        ${type !== "start" ? '<div class="wf-port wf-port-in" data-port="in"></div>' : ""}
        ${type !== "end" ? '<div class="wf-port wf-port-out" data-port="out"></div>' : ""}
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
          if (port.dataset.port === "out") this._startConnect(id, e);
        });
        port.addEventListener("mouseup", e => {
          if (this.connecting && port.dataset.port === "in") this._finishConnect(id);
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
        const fx = from.x + BLOCK_W + 1;
        const fy = from.y + BLOCK_H / 2;
        this.connecting.tempPath.setAttribute("d", this._bezier(fx, fy, mx, my));
      }
    }

    _onMouseUp(e) {
      if (this.dragging) this.dragging = null;
      if (this.connecting) {
        this.connecting.tempPath.remove();
        this.connecting = null;
      }
    }

    // ── Connections ───────────────────────────────────────────
    _startConnect(fromId, e) {
      e.stopPropagation();
      const tempPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
      tempPath.classList.add("wf-conn-temp");
      this.svg.appendChild(tempPath);
      this.connecting = { fromId, tempPath };
    }

    _finishConnect(toId) {
      if (!this.connecting) return;
      const fromId = this.connecting.fromId;
      if (fromId === toId) return;
      // Prevent duplicate
      if (this.connections.some(c => c.from === fromId && c.to === toId)) return;
      this._addConnection(fromId, toId);
    }

    _addConnection(fromId, toId) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.classList.add("wf-conn-path");
      path.setAttribute("marker-end", "url(#wf-arrow)");
      this.svg.appendChild(path);

      const conn = { id: this.nextConnId++, from: fromId, to: toId, el: path };
      this.connections.push(conn);
      this._updateConnectionPath(conn);

      // Click to delete
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

    _updateConnectionPath(conn) {
      const fb = this.blocks.get(conn.from);
      const tb = this.blocks.get(conn.to);
      if (!fb || !tb) return;
      const x1 = fb.x + BLOCK_W + 1, y1 = fb.y + BLOCK_H / 2;
      const x2 = tb.x - 1, y2 = tb.y + BLOCK_H / 2;
      conn.el.setAttribute("d", this._bezier(x1, y1, x2, y2));
    }

    _bezier(x1, y1, x2, y2) {
      const cx = Math.abs(x2 - x1) * 0.5;
      return `M ${x1} ${y1} C ${x1 + cx} ${y1}, ${x2 - cx} ${y2}, ${x2} ${y2}`;
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

    // ── Run Flow (Animation) ─────────────────────────────────
    async runFlow() {
      const order = this._topoSort();
      for (const id of order) {
        const b = this.blocks.get(id);
        if (!b) continue;
        b.el.classList.add("running");
        b.el.querySelector(".wf-block-status")?.classList.add("running");
        // Animate incoming connections
        this.connections.filter(c => c.to === id).forEach(c => c.el.classList.add("active-flow"));
        await sleep(400);
        b.el.classList.remove("running");
        b.el.querySelector(".wf-block-status")?.classList.remove("running");
        b.el.querySelector(".wf-block-status")?.classList.add("ok");
        this.connections.filter(c => c.to === id).forEach(c => c.el.classList.remove("active-flow"));
      }
      // Reset status after 2s
      setTimeout(() => {
        this.blocks.forEach(b => b.el.querySelector(".wf-block-status")?.classList.remove("ok"));
      }, 2000);
      if (window._wfDashboard) window._wfDashboard.addLog("Flow 執行完成", "success");
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

    // ── Persistence (localStorage) ───────────────────────────
    save(name) {
      const data = {
        blocks: Array.from(this.blocks.values()).map(b => ({ id: b.id, type: b.type, x: b.x, y: b.y, label: b.label })),
        connections: this.connections.map(c => ({ from: c.from, to: c.to })),
      };
      localStorage.setItem("wf_flow_" + (name || "default"), JSON.stringify(data));
      if (window.showToast) window.showToast("工作流已儲存", "success");
    }

    load(name) {
      const raw = localStorage.getItem("wf_flow_" + (name || "default"));
      if (!raw) return;
      const data = JSON.parse(raw);
      // Clear
      this.blocks.forEach(b => b.el.remove());
      this.blocks.clear();
      this.connections.forEach(c => c.el.remove());
      this.connections = [];
      this.nextId = 1;
      this.nextConnId = 1;
      // Restore blocks
      let maxId = 0;
      data.blocks.forEach(b => {
        this.addBlock(b.type, b.x, b.y, b.label);
        if (b.id > maxId) maxId = b.id;
      });
      // Map old IDs to new sequential IDs
      const idMap = new Map();
      let idx = 1;
      data.blocks.forEach(b => { idMap.set(b.id, idx++); });
      // Restore connections
      data.connections.forEach(c => {
        const from = idMap.get(c.from);
        const to = idMap.get(c.to);
        if (from && to) this._addConnection(from, to);
      });
    }
  }

  // ── Dashboard ─────────────────────────────────────────────────
  class WorkflowDashboard {
    constructor(container) {
      this.container = container;
      this.logs = [];
      this._render();
    }

    _render() {
      this.container.innerHTML = `
        <div class="wf-dashboard-header">
          <span>執行監控</span>
          <button style="background:none;border:none;cursor:pointer;color:var(--text-tertiary);font-size:0.9rem;" onclick="window._wfDashboard && window._wfDashboard.refresh()">↻</button>
        </div>
        <div class="wf-dashboard-body">
          <div class="wf-stats-grid">
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatBlocks">0</div><div class="wf-stat-label">節點數</div></div>
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatConns" style="color:#34a853">0</div><div class="wf-stat-label">連接數</div></div>
            <div class="wf-stat-card"><div class="wf-stat-value" id="wfStatRuns" style="color:#f5a623">0</div><div class="wf-stat-label">執行次數</div></div>
          </div>
          <div class="wf-log-section-title"><span class="wf-live-dot"></span> 執行日誌</div>
          <div class="wf-log-list" id="wfLogList"></div>
        </div>
      `;
    }

    updateStats(blocks, conns) {
      const b = document.getElementById("wfStatBlocks");
      const c = document.getElementById("wfStatConns");
      if (b) b.textContent = blocks;
      if (c) c.textContent = conns;
    }

    addLog(msg, status) {
      const list = document.getElementById("wfLogList");
      if (!list) return;
      const now = new Date();
      const time = now.getHours().toString().padStart(2, "0") + ":" + now.getMinutes().toString().padStart(2, "0") + ":" + now.getSeconds().toString().padStart(2, "0");
      const entry = document.createElement("div");
      entry.className = "wf-log-entry";
      entry.innerHTML = `<span class="wf-log-dot ${status}"></span><span class="wf-log-name">${msg}</span><span class="wf-log-time">${time}</span>`;
      list.prepend(entry);
      // Keep max 20
      while (list.children.length > 20) list.lastChild.remove();
      // Update run count
      const r = document.getElementById("wfStatRuns");
      if (r) r.textContent = parseInt(r.textContent || "0") + 1;
    }

    refresh() {
      if (window._wfDesigner) this.updateStats(window._wfDesigner.blocks.size, window._wfDesigner.connections.length);
    }
  }

  // ── Palette Builder ───────────────────────────────────────────
  function buildPalette(container) {
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

  function toggleWorkflowView() {
    const body = document.querySelector(".page-chat-body");
    const btn = document.getElementById("btnWorkflowDesigner");
    if (!body) return;

    const isActive = body.classList.contains("wf-mode");

    if (isActive) {
      body.classList.remove("wf-mode");
      if (btn) btn.classList.remove("active");
      // Hide old placeholder
      const oldView = document.getElementById("workflowView");
      if (oldView) oldView.style.display = "none";
      const chatBody = document.getElementById("chatBody");
      if (chatBody) chatBody.style.display = "";
    } else {
      body.classList.add("wf-mode");
      if (btn) btn.classList.add("active");
      // Hide old placeholder
      const oldView = document.getElementById("workflowView");
      if (oldView) oldView.style.display = "none";
      const chatBody = document.getElementById("chatBody");
      if (chatBody) chatBody.style.display = "";

      if (!_initialized) {
        _initialized = true;
        _initWorkflow();
      }
    }
  }

  function _initWorkflow() {
    // Build palette
    const paletteWrap = document.getElementById("wfPaletteWrap");
    if (paletteWrap) buildPalette(paletteWrap);

    // Init FlowDesigner
    const surface = document.getElementById("wfCanvasSurface");
    const svg = document.getElementById("wfConnectionsSvg");
    const viewport = document.getElementById("wfCanvasViewport");
    if (surface && svg && viewport) {
      window._wfDesigner = new FlowDesigner(surface, svg, viewport);
      // Load saved or add demo blocks
      const saved = localStorage.getItem("wf_flow_default");
      if (saved) {
        window._wfDesigner.load("default");
      } else {
        // Demo flow
        window._wfDesigner.addBlock("start", 80, 200);
        window._wfDesigner.addBlock("web-search", 320, 140);
        window._wfDesigner.addBlock("python-executor", 320, 280);
        window._wfDesigner.addBlock("end", 560, 200);
        window._wfDesigner._addConnection(1, 2);
        window._wfDesigner._addConnection(1, 3);
        window._wfDesigner._addConnection(2, 4);
        window._wfDesigner._addConnection(3, 4);
      }
    }

    // Init Dashboard
    const dashWrap = document.getElementById("wfDashboardWrap");
    if (dashWrap) {
      window._wfDashboard = new WorkflowDashboard(dashWrap);
      window._wfDashboard.refresh();
    }
  }

  // ── Utility ───────────────────────────────────────────────────
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // Expose to global
  window.toggleWorkflowView = toggleWorkflowView;
  window.closeWfPropPanel = closeWfPropPanel;

})();
