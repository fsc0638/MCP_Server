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

  const BLOCK_W = 160, BLOCK_H = 68, GRID = 24;  // Snap to dot grid (24px)
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
        arrowPath.setAttribute("fill", "#94a3b8");
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
      // Orthogonal (right-angle) routing snapped to grid
      const midX = snap((x1 + x2) / 2);
      return `M ${x1} ${y1} L ${midX} ${y1} L ${midX} ${y2} L ${x2} ${y2}`;
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
      // Remove saved flow
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
            <div class="wf-chart-legend" id="wfDistLegend"></div>
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
      if (!this.distChart || !window._wfDesigner) return;
      // Count blocks on canvas by type
      const counts = {};
      const colors = {};
      window._wfDesigner.blocks.forEach(bl => {
        const def = BLOCK_DEFS[bl.type];
        if (!def) return;
        const label = def.label;
        counts[label] = (counts[label] || 0) + 1;
        colors[label] = def.color;
      });
      const labels = Object.keys(counts);
      this.distChart.data.labels = labels;
      this.distChart.data.datasets[0].data = labels.map(l => counts[l]);
      this.distChart.data.datasets[0].backgroundColor = labels.map(l => colors[l]);
      this.distChart.update();

      // Update legend
      const legend = document.getElementById("wfDistLegend");
      if (legend) {
        legend.innerHTML = "";
        labels.forEach(l => {
          const item = document.createElement("div");
          item.className = "wf-legend-item";
          item.innerHTML = `<span class="wf-legend-dot" style="background:${colors[l]}"></span>${l}`;
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
      const resp = await fetch("/skills/list");
      if (!resp.ok) return;
      const data = await resp.json();
      const skills = data.skills || {};
      Object.entries(skills).forEach(([name, info]) => {
        const shortName = name.replace("mcp-", "");
        // If already in BLOCK_DEFS, keep it; otherwise add dynamically
        if (!BLOCK_DEFS[shortName]) {
          BLOCK_DEFS[shortName] = {
            label: _guessLabel(name, info.description),
            icon: _guessIcon(name),
            color: _guessColor(name),
            category: _guessCategory(name, info.description),
          };
        }
        _dynamicSkills[name] = { ready: info.ready !== false, description: info.description || "" };
      });
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

  function toggleWorkflowView() {
    const body = document.querySelector(".page-chat-body");
    const btn = document.getElementById("btnWorkflowDesigner");
    if (!body) return;

    const isActive = body.classList.contains("wf-mode");

    if (isActive) {
      // Exit workflow mode — clean up everything
      body.classList.remove("wf-mode");
      if (btn) btn.classList.remove("active");

      // Force exit skill-edit mode if active
      if (_skillEditMode) {
        _skillEditMode = false;
        const editArea = document.getElementById("wfSkillEditArea");
        if (editArea) { editArea.style.display = "none"; editArea.classList.remove("visible"); }
        const canvasArea = document.getElementById("wfCanvasArea");
        if (canvasArea) canvasArea.style.display = "flex";
      }

      // Hide all workflow containers
      const editArea2 = document.getElementById("wfSkillEditArea");
      if (editArea2) { editArea2.style.display = "none"; editArea2.classList.remove("visible"); }
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

  async function _initWorkflow() {
    // Build palette (async — loads skills from API)
    const paletteWrap = document.getElementById("wfPaletteWrap");
    if (paletteWrap) await _rebuildPaletteForFlow(paletteWrap);

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
        // Default: only a Start block
        window._wfDesigner.addBlock("start", 200, 250);
      }
    }

    // Init Dashboard — sync immediately with canvas state
    const dashWrap = document.getElementById("wfDashboardWrap");
    if (dashWrap) {
      window._wfDashboard = new WorkflowDashboard(dashWrap);
      // Wait for Chart.js to load, then sync
      const _syncDash = () => {
        if (window._wfDashboard && window._wfDesigner) {
          window._wfDashboard.updateStats(window._wfDesigner.blocks.size, window._wfDesigner.connections.length);
        }
      };
      setTimeout(_syncDash, 500);
      setTimeout(_syncDash, 2000); // retry after Chart.js CDN loads
    }
  }

  // ── Skill Edit Mode ────────────────────────────────────────────
  let _skillEditMode = false;

  function toggleSkillEditMode() {
    _skillEditMode = !_skillEditMode;
    const paletteWrap = document.getElementById("wfPaletteWrap");
    const canvasArea = document.getElementById("wfCanvasArea");
    const editArea = document.getElementById("wfSkillEditArea");

    if (_skillEditMode) {
      // Enter skill edit mode
      if (canvasArea) canvasArea.style.display = "none";
      if (editArea) { editArea.style.display = "flex"; editArea.classList.add("visible"); }
      _rebuildPaletteForEdit(paletteWrap);
      if (!window._wfSkillEditor) window._wfSkillEditor = new SkillEditor();
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

    Object.entries(CATEGORIES).forEach(([catKey, cat]) => {
      const items = Object.entries(BLOCK_DEFS).filter(([, d]) => d.category === catKey);
      if (!items.length) return;
      html += `<div class="wf-palette-category"><div class="wf-palette-category-title">${cat.label}</div>`;
      items.forEach(([type, def]) => {
        const isControl = catKey === "control";
        const skillName = type.startsWith("mcp-") ? type : "mcp-" + type;
        const cls = isControl ? "wf-palette-item wf-palette-item--disabled" : "wf-palette-item wf-palette-item--clickable";
        const onclick = isControl ? "" : `onclick="window._wfSkillEditor&&window._wfSkillEditor.loadSkill('${skillName}')"`;
        html += `<div class="${cls}" data-type="${type}" ${onclick}>
          <div class="wf-palette-item-accent" style="background:${def.color}"></div>
          <div class="wf-palette-item-icon" style="background:${def.color}">${def.icon}</div>
          <span>${def.label}</span>
        </div>`;
      });
      html += `</div>`;
    });
    html += `</div>`;
    container.innerHTML = html;
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

    async loadSkill(skillName) {
      this.currentSkill = skillName;

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

      // Fetch skill data
      try {
        const [detail, files] = await Promise.all([
          fetch(`/skills/${skillName}`).then(r => r.json()),
          fetch(`/skills/${skillName}/files`).then(r => r.json()),
        ]);
        // Store backup for rollback
        this._backup = detail.raw_content || "";
        this._renderEditor(skillName, detail, files);
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
      const skillMd = detail.raw_content || "";

      body.innerHTML = `
        <div class="wf-editor-field">
          <label>名稱 (name)</label>
          <input type="text" id="wfEditName" value="${meta.name || skillName}" readonly />
        </div>
        <div class="wf-editor-field">
          <label>簡介 (description)</label>
          <textarea id="wfEditDesc" rows="3" style="min-height:60px;font-family:inherit;">${(meta.description || "").trim()}</textarea>
        </div>
        <div class="wf-editor-field" style="display:flex;gap:10px;">
          <div style="flex:1">
            <label>Version</label>
            <input type="text" id="wfEditVersion" value="${meta.version || "1.0.0"}" />
          </div>
          <div style="flex:1">
            <label>Risk Level</label>
            <select id="wfEditRisk">
              <option value="low" ${meta.risk_level==="low"?"selected":""}>low</option>
              <option value="high" ${meta.risk_level==="high"?"selected":""}>high</option>
            </select>
          </div>
          <div style="flex:1">
            <label>Timeout (s)</label>
            <input type="number" id="wfEditTimeout" value="${meta.execution_timeout || 30}" />
          </div>
        </div>
        <div class="wf-editor-field">
          <label>SKILL.md（完整內容）</label>
          <textarea id="wfEditSkillMd" rows="12">${this._escapeHtml(skillMd)}</textarea>
        </div>
        ${this._renderFileSection("references", "📁 References", files.references || [])}
        ${this._renderFileSection("scripts", "📁 Scripts", files.scripts || [])}
        ${this._renderFileSection("assets", "📁 Assets", files.assets || [])}
      `;
    }

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

    async save() {
      if (!this.currentSkill) return;
      const skillMd = document.getElementById("wfEditSkillMd")?.value;
      if (!skillMd) return;
      try {
        const resp = await fetch(`/skills/${this.currentSkill}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ yaml_content: skillMd }),
        });
        const data = await resp.json();
        if (resp.ok) {
          // Rescan skills
          await fetch("/skills/rescan", { method: "POST" });
          if (window.showToast) window.showToast("已儲存並同步 ✅", "success");
          // Refresh editor
          this.loadSkill(this.currentSkill);
        } else {
          if (window.showToast) window.showToast("儲存失敗: " + (data.detail || ""), "error");
        }
      } catch (e) {
        if (window.showToast) window.showToast("儲存錯誤: " + e.message, "error");
      }
    }

    async rollback() {
      if (!this.currentSkill) return;
      if (this._backup) {
        // Restore from in-memory snapshot (before any edits)
        const textarea = document.getElementById("wfEditSkillMd");
        if (textarea) textarea.value = this._backup;
        if (window.showToast) window.showToast("已還原至開啟時的版本", "success");
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
            session_id: "skill_test_" + this.currentSkill,
            message: userMsg,
            model: model,
            injected_skill: this.currentSkill,
          }),
        });

        // SSE stream or JSON
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
                } catch (_) {}
              }
            }
          }
          if (assistantText) this._addTestMsg(assistantText, "assistant");
        } else {
          const data = await resp.json();
          this._addTestMsg(data.reply || data.content || JSON.stringify(data), "assistant");
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

  // ── Utility ───────────────────────────────────────────────────
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // Expose to global
  window.toggleWorkflowView = toggleWorkflowView;
  window.toggleSkillEditMode = toggleSkillEditMode;
  window.closeWfPropPanel = closeWfPropPanel;

})();
