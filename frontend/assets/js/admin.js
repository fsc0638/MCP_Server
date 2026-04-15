/**
 * Agent K — Admin Panel
 * Hash routing + Dashboard KPI + Token chart
 */
(function () {
  "use strict";

  const content = document.getElementById("adminContent");
  const navItems = document.querySelectorAll(".admin-nav-item");

  // ── User Info ──────────────────────────────────────────────
  const _user = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
  // Set avatar (reuse chat.html pattern)
  const avatarEl = document.getElementById("adminUserAvatar");
  if (avatarEl) {
    if (_user.picture) {
      avatarEl.style.backgroundImage = `url(${_user.picture})`;
      avatarEl.style.backgroundSize = "cover";
    } else {
      avatarEl.textContent = (_user.initials || _user.name?.slice(0, 2) || "U").toUpperCase();
      avatarEl.style.display = "flex"; avatarEl.style.alignItems = "center"; avatarEl.style.justifyContent = "center";
      avatarEl.style.fontSize = "0.65rem"; avatarEl.style.fontWeight = "700"; avatarEl.style.color = "#fff";
      avatarEl.style.background = "var(--kway-blue)";
    }
  }

  // ── Hash Router ────────────────────────────────────────────
  const pages = {
    dashboard: renderDashboard,
    skills: renderSkills,
    workflows: renderWorkflows,
    schedules: renderSchedules,
    tokens: renderTokens,
    users: renderUsers,
    settings: renderSettings,
  };

  function navigate() {
    const hash = (location.hash || "#/dashboard").replace("#/", "");
    const page = pages[hash] || pages.dashboard;

    // Update active nav
    navItems.forEach(item => {
      item.classList.toggle("active", item.dataset.page === hash);
    });

    // Render page
    page();
  }

  window.addEventListener("hashchange", navigate);
  navigate(); // initial load

  // ── Placeholder Page ───────────────────────────────────────
  function renderPlaceholder(title, desc) {
    content.innerHTML = `
      <h1 class="admin-page-title">${title}</h1>
      <p class="admin-page-desc">${desc}</p>
      <div class="admin-placeholder">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/>
        </svg>
        <p>此功能將在後續版本中實作</p>
      </div>
    `;
  }

  // ── Dashboard Page ─────────────────────────────────────────
  async function renderDashboard() {
    content.innerHTML = `
      <h1 class="admin-page-title">Dashboard</h1>
      <p class="admin-page-desc">系統總覽與即時監控</p>

      <div class="admin-kpi-grid" id="adminKpiGrid">
        <div class="admin-kpi-card admin-kpi-accent"><div class="admin-kpi-label">Skills 啟用數</div><div class="admin-kpi-value" id="kpiSkills">--</div><div class="admin-kpi-sub">System + Department</div></div>
        <div class="admin-kpi-card admin-kpi-accent-orange"><div class="admin-kpi-label">Workflow 總數</div><div class="admin-kpi-value" id="kpiWorkflows">--</div><div class="admin-kpi-sub">全部 scope</div></div>
        <div class="admin-kpi-card admin-kpi-accent-green"><div class="admin-kpi-label">本月 Token 用量</div><div class="admin-kpi-value" id="kpiTokens">--</div><div class="admin-kpi-sub">prompt + completion</div></div>
        <div class="admin-kpi-card admin-kpi-accent-red"><div class="admin-kpi-label">排程任務</div><div class="admin-kpi-value" id="kpiSchedules">--</div><div class="admin-kpi-sub">active 任務數</div></div>
      </div>

      <div class="admin-row">
        <div class="admin-col-main">
          <div class="admin-chart-card">
            <div class="admin-chart-title">Token 用量趨勢（7 日）</div>
            <div class="admin-chart-wrap"><canvas id="adminTokenChart"></canvas></div>
          </div>
          <div class="admin-chart-card">
            <div class="admin-chart-title">Skill 呼叫 Top 5</div>
            <div class="admin-chart-wrap"><canvas id="adminSkillChart"></canvas></div>
          </div>
        </div>
        <div class="admin-col-side">
          <div class="admin-chart-card">
            <div class="admin-chart-title">最近活動</div>
            <div class="admin-feed" id="adminFeed"></div>
          </div>
        </div>
      </div>
    `;

    // Load data
    await Promise.all([loadKpis(), loadTokenChart(), loadSkillChart(), loadFeed()]);
  }

  // ── KPI Data ───────────────────────────────────────────────
  async function loadKpis() {
    try {
      // Skills count
      const skillResp = await fetch("/skills/list");
      if (skillResp.ok) {
        const d = await skillResp.json();
        document.getElementById("kpiSkills").textContent = d.total || 0;
      }
    } catch (_) {}

    try {
      // Workflow count
      const wfResp = await fetch("/api/workflows?owner=" + (_user.employee_id || _user.id || ""));
      if (wfResp.ok) {
        const d = await wfResp.json();
        document.getElementById("kpiWorkflows").textContent = d.total || 0;
      }
    } catch (_) {}

    try {
      // Token summary — daily is dict(date→{total_tokens,...}), total has input/output/total_tokens
      const tokenResp = await fetch("/skills/workflow/stats");
      if (tokenResp.ok) {
        const d = await tokenResp.json();
        const total = d.total?.total_tokens || 0;
        const fmt = n => n > 100000 ? Math.round(n / 1000) + "K" : n.toLocaleString();
        document.getElementById("kpiTokens").textContent = fmt(total);
      }
    } catch (_) {}

    // Schedules — placeholder
    const sEl = document.getElementById("kpiSchedules");
    if (sEl) sEl.textContent = "—";
  }

  // ── Token Chart (7-day Area) ────────────────────────────────
  // Helper: convert daily dict → sorted array
  function _dailyToArray(dailyDict) {
    return Object.entries(dailyDict || {})
      .map(([date, val]) => ({ date, ...val }))
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  async function loadTokenChart() {
    try {
      const resp = await fetch("/skills/workflow/stats");
      if (!resp.ok) return;
      const data = await resp.json();
      const daily = _dailyToArray(data.daily).slice(-7);
      if (!daily.length) return;

      const ctx = document.getElementById("adminTokenChart");
      if (!ctx || typeof Chart === "undefined") return;

      new Chart(ctx, {
        type: "line",
        data: {
          labels: daily.map(d => d.date.slice(5)),
          datasets: [
            { label: "Total Tokens", data: daily.map(d => d.total_tokens || 0), borderColor: "#1A9AAA", backgroundColor: "rgba(26,154,170,0.15)", fill: true, tension: 0.3, borderWidth: 2 },
            { label: "Skill Calls", data: daily.map(d => d.skill_calls || 0), borderColor: "#F5A623", backgroundColor: "rgba(245,166,35,0.10)", fill: false, tension: 0.3, borderWidth: 2, yAxisID: "y1" },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: { legend: { position: "bottom", labels: { font: { size: 11 } } } },
          scales: {
            y: { beginAtZero: true, position: "left", title: { display: true, text: "Tokens", font: { size: 10 } }, ticks: { font: { size: 10 } } },
            y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "Calls", font: { size: 10 } }, ticks: { font: { size: 10 } } },
            x: { ticks: { font: { size: 10 } } },
          },
        },
      });
    } catch (_) {}
  }

  // ── Skill Top 5 (by calls, Horizontal Bar) ─────────────────
  async function loadSkillChart() {
    try {
      const resp = await fetch("/skills/workflow/stats");
      if (!resp.ok) return;
      const data = await resp.json();
      const bySkill = data.by_skill || {};
      // Sort by calls count (value is an object with .calls)
      const sorted = Object.entries(bySkill)
        .map(([k, v]) => [k, typeof v === "object" ? (v.calls || 0) : v])
        .sort((a, b) => b[1] - a[1]).slice(0, 5);
      if (!sorted.length) return;

      const ctx = document.getElementById("adminSkillChart");
      if (!ctx || typeof Chart === "undefined") return;

      new Chart(ctx, {
        type: "bar",
        data: {
          labels: sorted.map(([k]) => k.replace("mcp-", "")),
          datasets: [{ label: "呼叫次數", data: sorted.map(([, v]) => v), backgroundColor: "#1A9AAA", borderRadius: 6 }],
        },
        options: {
          responsive: true, maintainAspectRatio: false, indexAxis: "y",
          plugins: { legend: { display: false } },
          scales: {
            x: { beginAtZero: true, ticks: { stepSize: 1, font: { size: 10 } } },
            y: { ticks: { font: { size: 11 } } },
          },
        },
      });
    } catch (_) {}
  }

  // ── Activity Feed ──────────────────────────────────────────
  async function loadFeed() {
    const feed = document.getElementById("adminFeed");
    if (!feed) return;
    // Placeholder — will be replaced with real activity data
    feed.innerHTML = `
      <div class="admin-feed-item">
        <span class="admin-feed-dot" class="admin-bg-success"></span>
        <span class="admin-feed-text">系統啟動完成</span>
        <span class="admin-feed-time">剛剛</span>
      </div>
      <div class="admin-feed-item">
        <span class="admin-feed-dot" class="admin-bg-teal"></span>
        <span class="admin-feed-text">Skills 掃描完成（${document.getElementById("kpiSkills")?.textContent || "?"} 個技能）</span>
        <span class="admin-feed-time">啟動時</span>
      </div>
    `;
  }

  // ── Token Page (placeholder with chart) ─────────────────────
  // ══════════════════════════════════════════════════════════
  // Token Dashboard (Phase 4)
  // ══════════════════════════════════════════════════════════

  let _tokenData = null;
  let _tokenRange = 7;

  async function renderTokens() {
    content.innerHTML = `
      <h1 class="admin-page-title">Token 用量</h1>
      <p class="admin-page-desc">模型呼叫用量追蹤與分析</p>
      <div class="admin-toolbar">
        <button class="admin-btn admin-btn-primary" data-range="7" onclick="_setTokenRange(7)" style="font-size:0.7rem;">7 日</button>
        <button class="admin-btn" data-range="30" onclick="_setTokenRange(30)" style="font-size:0.7rem;">30 日</button>
        <button class="admin-btn" data-range="90" onclick="_setTokenRange(90)" style="font-size:0.7rem;">90 日</button>
        <div class="admin-toolbar-spacer"></div>
        <button class="admin-btn" onclick="_exportTokenCSV()" style="font-size:0.7rem;">匯出 CSV</button>
      </div>
      <div class="admin-kpi-grid">
        <div class="admin-kpi-card admin-kpi-accent"><div class="admin-kpi-label">Input Tokens（累計）</div><div class="admin-kpi-value" id="tkPrompt">--</div></div>
        <div class="admin-kpi-card admin-kpi-accent-orange"><div class="admin-kpi-label">Output Tokens（累計）</div><div class="admin-kpi-value" id="tkCompletion">--</div></div>
        <div class="admin-kpi-card admin-kpi-accent-green"><div class="admin-kpi-label">區間 Total</div><div class="admin-kpi-value" id="tkTotal">--</div><div class="admin-kpi-sub">選定天數範圍</div></div>
        <div class="admin-kpi-card"><div class="admin-kpi-label">日均 Tokens</div><div class="admin-kpi-value" id="tkAvg">--</div></div>
      </div>
      <div class="admin-row">
        <div class="admin-col-main">
          <div class="admin-chart-card">
            <div class="admin-chart-title">Token 用量趨勢</div>
            <div class="admin-chart-wrap" style="height:280px;"><canvas id="tkTrendChart"></canvas></div>
          </div>
        </div>
        <div class="admin-col-side">
          <div class="admin-chart-card">
            <div class="admin-chart-title">By Skill（Top 5）</div>
            <div class="admin-chart-wrap" style="height:220px;"><canvas id="tkSkillChart"></canvas></div>
          </div>
          <div class="admin-chart-card" style="margin-top:16px;">
            <div class="admin-chart-title">Skill Token 分佈</div>
            <div class="admin-chart-wrap" style="height:200px;"><canvas id="tkModelChart"></canvas></div>
          </div>
        </div>
      </div>
    `;

    try {
      const resp = await fetch("/skills/workflow/stats");
      if (resp.ok) _tokenData = await resp.json();
    } catch (_) {}

    _drawTokenCharts();
  }

  window._setTokenRange = function (days) {
    _tokenRange = days;
    document.querySelectorAll(".admin-toolbar [data-range]").forEach(b => {
      b.classList.toggle("admin-btn-primary", parseInt(b.dataset.range) === days);
    });
    _drawTokenCharts();
  };

  function _drawTokenCharts() {
    if (!_tokenData) return;
    // daily is dict(date→{total_tokens, skill_calls, chat_calls})
    const daily = _dailyToArray(_tokenData.daily).slice(-_tokenRange);
    const bySkill = _tokenData.by_skill || {};
    const totalObj = _tokenData.total || {};

    // KPI — use total for input/output, daily sum for period total
    const el = id => document.getElementById(id);
    const fmt = n => n > 100000 ? Math.round(n / 1000) + "K" : n.toLocaleString();
    const inputT = totalObj.input_tokens || 0;
    const outputT = totalObj.output_tokens || 0;
    let periodTotal = 0;
    daily.forEach(d => { periodTotal += (d.total_tokens || 0); });
    if (el("tkPrompt")) el("tkPrompt").textContent = fmt(inputT);
    if (el("tkCompletion")) el("tkCompletion").textContent = fmt(outputT);
    if (el("tkTotal")) el("tkTotal").textContent = fmt(periodTotal);
    if (el("tkAvg")) el("tkAvg").textContent = daily.length ? fmt(Math.round(periodTotal / daily.length)) : "--";

    if (typeof Chart === "undefined") return;

    // Destroy old charts
    ["tkTrendChart", "tkSkillChart", "tkModelChart"].forEach(id => {
      const c = Chart.getChart(id);
      if (c) c.destroy();
    });

    // Trend Area Chart (total_tokens + skill_calls dual axis)
    const trendCtx = el("tkTrendChart");
    if (trendCtx && daily.length) {
      new Chart(trendCtx, {
        type: "line",
        data: {
          labels: daily.map(d => d.date.slice(5)),
          datasets: [
            { label: "Total Tokens", data: daily.map(d => d.total_tokens || 0), borderColor: "#1A9AAA", backgroundColor: "rgba(26,154,170,0.15)", fill: true, tension: 0.3, borderWidth: 2 },
            { label: "Skill Calls", data: daily.map(d => d.skill_calls || 0), borderColor: "#F5A623", backgroundColor: "rgba(245,166,35,0.08)", fill: false, tension: 0.3, borderWidth: 2, yAxisID: "y1" },
          ],
        },
        options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
          plugins: { legend: { position: "bottom", labels: { font: { size: 11 } } } },
          scales: {
            y: { beginAtZero: true, position: "left", title: { display: true, text: "Tokens", font: { size: 10 } }, ticks: { font: { size: 10 } } },
            y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "Calls", font: { size: 10 } }, ticks: { stepSize: 1, font: { size: 10 } } },
            x: { ticks: { font: { size: 10 } } },
          },
        },
      });
    }

    // Skill Top 5 Bar (by calls)
    const skillCtx = el("tkSkillChart");
    if (skillCtx) {
      const sorted = Object.entries(bySkill)
        .map(([k, v]) => [k, typeof v === "object" ? (v.calls || 0) : v])
        .sort((a, b) => b[1] - a[1]).slice(0, 5);
      if (sorted.length) {
        new Chart(skillCtx, {
          type: "bar",
          data: { labels: sorted.map(([k]) => k.replace("mcp-", "")), datasets: [{ label: "呼叫次數", data: sorted.map(([, v]) => v), backgroundColor: "#1A9AAA", borderRadius: 4 }] },
          options: { responsive: true, maintainAspectRatio: false, indexAxis: "y", plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: true, ticks: { stepSize: 1, font: { size: 10 } } }, y: { ticks: { font: { size: 10 } } } } },
        });
      }
    }

    // Skill Token Distribution Donut (by total_tokens per skill)
    const modelCtx = el("tkModelChart");
    if (modelCtx) {
      const skillTokens = Object.entries(bySkill)
        .map(([k, v]) => [k.replace("mcp-", ""), typeof v === "object" ? (v.total_tokens || 0) : v])
        .sort((a, b) => b[1] - a[1]).slice(0, 6);
      if (skillTokens.length) {
        const mColors = ["#1A9AAA", "#F5A623", "#4285f4", "#ea4335", "#8b5cf6", "#059669"];
        new Chart(modelCtx, {
          type: "doughnut",
          data: { labels: skillTokens.map(([k]) => k), datasets: [{ data: skillTokens.map(([, v]) => v), backgroundColor: mColors, borderWidth: 0 }] },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { font: { size: 10 } } } } },
        });
      }
    }
  }

  window._exportTokenCSV = function () {
    if (!_tokenData?.daily) return;
    const daily = _dailyToArray(_tokenData.daily).slice(-_tokenRange);
    let csv = "date,total_tokens,skill_calls,chat_calls\n";
    daily.forEach(d => {
      csv += `${d.date},${d.total_tokens||0},${d.skill_calls||0},${d.chat_calls||0}\n`;
    });
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `token_usage_${_tokenRange}d.csv`;
    a.click();
  };

  // ══════════════════════════════════════════════════════════
  // Schedule Monitoring Page (Phase 4)
  // ══════════════════════════════════════════════════════════

  let _schedData = null;

  async function renderSchedules() {
    content.innerHTML = `
      <h1 class="admin-page-title">排程監控</h1>
      <p class="admin-page-desc">跨 Session 排程任務管理、暫停/恢復/刪除</p>
      <div class="admin-toolbar">
        <select class="admin-filter-select" id="adminSchedStatusFilter" autocomplete="off">
          <option value="">全部狀態</option>
          <option value="active">啟用中</option>
          <option value="paused">已暫停</option>
        </select>
        <select class="admin-filter-select" id="adminSchedTypeFilter" autocomplete="off">
          <option value="">全部類型</option>
          <option value="custom">Custom</option>
          <option value="news">News</option>
          <option value="language">Language</option>
          <option value="reminder">Reminder</option>
          <option value="pipeline">Pipeline</option>
          <option value="work_summary">Work Summary</option>
        </select>
        <input class="admin-search" id="adminSchedSearch" type="text" placeholder="搜尋排程名稱..." autocomplete="off" />
        <div class="admin-toolbar-spacer"></div>
      </div>
      <div class="admin-kpi-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:20px;">
        <div class="admin-kpi-card admin-kpi-accent-green"><div class="admin-kpi-label">啟用中</div><div class="admin-kpi-value" id="schedActive">--</div></div>
        <div class="admin-kpi-card admin-kpi-accent-orange"><div class="admin-kpi-label">已暫停</div><div class="admin-kpi-value" id="schedPaused">--</div></div>
        <div class="admin-kpi-card admin-kpi-accent"><div class="admin-kpi-label">總任務數</div><div class="admin-kpi-value" id="schedTotal">--</div></div>
        <div class="admin-kpi-card"><div class="admin-kpi-label">Session 數</div><div class="admin-kpi-value" id="schedSessions">--</div></div>
      </div>
      <div class="admin-row">
        <div class="admin-col-main">
          <div class="admin-table-wrap">
            <table class="admin-table">
              <thead><tr><th>任務名稱</th><th>類型</th><th>Cron</th><th>狀態</th><th>Session</th><th>操作</th></tr></thead>
              <tbody id="adminSchedTableBody"></tbody>
            </table>
          </div>
        </div>
        <div class="admin-col-side">
          <div class="admin-chart-card">
            <div class="admin-chart-title">類型分佈</div>
            <div class="admin-chart-wrap" style="height:180px;"><canvas id="adminSchedTypeChart"></canvas></div>
          </div>
          <div class="admin-chart-card" style="margin-top:16px;">
            <div class="admin-chart-title">Session 分佈</div>
            <div class="admin-chart-wrap" style="height:180px;"><canvas id="adminSchedSessionChart"></canvas></div>
          </div>
        </div>
      </div>
    `;

    // Fetch from new API
    try {
      const resp = await fetch("/api/schedules");
      if (resp.ok) _schedData = await resp.json();
    } catch (_) {}

    if (!_schedData) { _schedData = { total: 0, active: 0, paused: 0, sessions: [], tasks: [] }; }

    // KPIs
    const el = id => document.getElementById(id);
    if (el("schedActive")) el("schedActive").textContent = _schedData.active;
    if (el("schedPaused")) el("schedPaused").textContent = _schedData.paused;
    if (el("schedTotal")) el("schedTotal").textContent = _schedData.total;
    if (el("schedSessions")) el("schedSessions").textContent = _schedData.sessions?.length || 0;

    _renderSchedTable();
    _renderSchedCharts();

    // Filter handlers
    document.getElementById("adminSchedStatusFilter")?.addEventListener("change", _renderSchedTable);
    document.getElementById("adminSchedTypeFilter")?.addEventListener("change", _renderSchedTable);
    document.getElementById("adminSchedSearch")?.addEventListener("input", _renderSchedTable);
  }

  // Human-readable cron description
  function _cronToHuman(cron) {
    if (!cron) return "—";
    if (cron.startsWith("once ")) return "一次性：" + cron.replace("once ", "");
    if (cron.startsWith("every ")) return "每 " + cron.replace("every +", "").replace("m", " 分鐘").replace("h", " 小時");
    if (/^\d{2}:\d{2}$/.test(cron)) return "每日 " + cron;
    if (cron.startsWith("weekday ")) return "工作日 " + cron.replace("weekday ", "");
    // Standard 5-field cron
    const parts = cron.split(" ");
    if (parts.length === 5) {
      const [min, hr, dom, mon, dow] = parts;
      const dowMap = { "0": "日", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六", "1-5": "一~五", "*": "" };
      let desc = "";
      if (dow !== "*") desc += "每週" + (dowMap[dow] || dow) + " ";
      if (dom !== "*") desc += dom + "號 ";
      if (hr !== "*" && min !== "*") desc += (hr.padStart(2, "0")) + ":" + (min.padStart(2, "0"));
      else if (hr !== "*") desc += hr + "時";
      else desc += "每小時";
      return desc.trim() || cron;
    }
    return cron;
  }

  function _renderSchedTable() {
    const tbody = document.getElementById("adminSchedTableBody");
    if (!tbody || !_schedData) return;
    const statusF = document.getElementById("adminSchedStatusFilter")?.value || "";
    const typeF = document.getElementById("adminSchedTypeFilter")?.value || "";
    const q = (document.getElementById("adminSchedSearch")?.value || "").toLowerCase();
    const typeColors = { language: "#4285f4", news: "#34a853", custom: "#f5a623", reminder: "#ea4335", work_summary: "#8b5cf6", pipeline: "#1a9aaa" };

    let html = "";
    (_schedData.tasks || []).forEach(t => {
      const enabled = t.enabled !== false;
      const name = t.name || t.type || "—";
      const sid = t._session_id || "";

      if (statusF === "active" && !enabled) return;
      if (statusF === "paused" && enabled) return;
      if (typeF && t.type !== typeF) return;
      if (q && !name.toLowerCase().includes(q)) return;

      const statusBadge = enabled
        ? '<span class="admin-scope-badge system">啟用中</span>'
        : '<span class="admin-scope-badge" style="background:var(--color-warning-bg);color:var(--color-warning);">已暫停</span>';
      const typeBadge = `<span style="display:inline-flex;align-items:center;gap:4px;font-size:0.72rem;"><span style="width:7px;height:7px;border-radius:50%;background:${typeColors[t.type]||"#94a3b8"};"></span>${t.type || "—"}</span>`;
      const cronHuman = _cronToHuman(t.cron);

      html += `<tr>
        <td style="font-weight:600;color:var(--text-primary);">${_esc(name)}<br/><span style="font-size:0.6rem;color:var(--text-tertiary);font-family:monospace;">${t.id || ""}</span></td>
        <td>${typeBadge}</td>
        <td><span style="font-size:0.75rem;">${_esc(cronHuman)}</span><br/><span style="font-size:0.6rem;color:var(--text-tertiary);font-family:monospace;">${_esc(t.cron || "")}</span></td>
        <td>${statusBadge}</td>
        <td style="font-size:0.68rem;color:var(--text-secondary);word-break:break-all;">${_esc(sid)}</td>
        <td><div class="admin-table-actions">
          <button class="admin-table-action" onclick="_openSchedDrawer('${t._session_id}','${t.id}')">維護</button>
          <button class="admin-table-action" onclick="_toggleSchedTask('${t._session_id}','${t.id}')">${enabled ? "暫停" : "恢復"}</button>
          <button class="admin-table-action danger" onclick="_deleteSchedTask('${t._session_id}','${t.id}','${_esc(name)}')">刪除</button>
        </div></td>
      </tr>`;
    });
    if (!html) html = '<tr><td colspan="6" style="text-align:center;color:var(--text-tertiary);padding:30px;">無符合條件的排程</td></tr>';
    tbody.innerHTML = html;
  }

  // Schedule detail drawer (same style as Skill drawer)
  window._openSchedDrawer = function (sessionId, taskId) {
    const task = (_schedData?.tasks || []).find(t => t._session_id === sessionId && t.id === taskId);
    if (!task) return;
    document.getElementById("adminDrawerOverlay")?.remove();

    const enabled = task.enabled !== false;
    const config = task.config || {};

    const overlay = document.createElement("div");
    overlay.className = "admin-drawer-overlay open";
    overlay.id = "adminDrawerOverlay";
    overlay.innerHTML = `
      <div class="admin-drawer">
        <div class="admin-drawer-header">
          <div>
            <span class="admin-drawer-title">${_esc(task.name || task.type || "排程任務")}</span>
            <div style="font-size:0.58rem;color:var(--text-tertiary);font-family:monospace;margin-top:2px;">${task.id}</div>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <button class="admin-btn" style="font-size:0.68rem;" onclick="_toggleSchedTask('${sessionId}','${taskId}');document.getElementById('adminDrawerOverlay')?.remove();">${enabled ? "暫停" : "恢復"}</button>
            <button class="admin-btn" style="font-size:0.68rem;color:var(--color-error);border-color:var(--color-error-bg);" onclick="_deleteSchedTask('${sessionId}','${taskId}','${_esc(task.name||"")}')">刪除</button>
            <button class="admin-drawer-close" onclick="document.getElementById('adminDrawerOverlay')?.remove()">&times;</button>
          </div>
        </div>
        <div class="admin-drawer-body">
          <div class="admin-drawer-row">
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">任務名稱</label>
              <div style="font-size:0.82rem;font-weight:600;color:var(--text-primary);">${_esc(task.name || "—")}</div>
            </div>
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">類型</label>
              <div style="font-size:0.82rem;color:var(--text-primary);">${task.type || "—"}</div>
            </div>
          </div>
          <div class="admin-drawer-row">
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">Cron 表達式</label>
              <div style="font-size:0.82rem;font-family:monospace;color:var(--text-primary);">${_esc(task.cron || "—")}</div>
              <div style="font-size:0.72rem;color:var(--text-tertiary);margin-top:2px;">${_cronToHuman(task.cron)}</div>
            </div>
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">狀態</label>
              <div style="font-size:0.82rem;">${enabled ? '<span style="color:#059669;font-weight:600;">啟用中</span>' : '<span style="color:#d97706;font-weight:600;">已暫停</span>'}</div>
            </div>
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">Session ID</label>
            <div style="font-size:0.75rem;font-family:monospace;color:var(--text-secondary);word-break:break-all;">${_esc(sessionId)}</div>
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">建立時間</label>
            <div style="font-size:0.78rem;color:var(--text-secondary);">${task.created_at ? task.created_at.replace("T", " ").substring(0, 19) : "—"}</div>
          </div>
          ${task.last_run ? `<div class="admin-drawer-field">
            <label class="admin-drawer-label">上次執行</label>
            <div style="font-size:0.78rem;color:var(--text-secondary);">${task.last_run.replace("T", " ").substring(0, 19)}</div>
          </div>` : ""}
          <div class="admin-drawer-field" style="border-top:1px solid var(--border-subtle);padding-top:12px;margin-top:8px;">
            <label class="admin-drawer-label">任務設定 (Config)</label>
            <pre style="font-size:0.7rem;color:var(--text-secondary);background:var(--bg-main);padding:10px;border-radius:8px;overflow-x:auto;max-height:200px;white-space:pre-wrap;word-break:break-all;">${_esc(JSON.stringify(config, null, 2))}</pre>
          </div>
        </div>
      </div>
    `;
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  };

  function _renderSchedCharts() {
    if (!_schedData || typeof Chart === "undefined") return;

    // Type distribution donut
    const typeCtx = document.getElementById("adminSchedTypeChart");
    if (typeCtx) {
      const byType = {};
      (_schedData.tasks || []).forEach(t => { byType[t.type || "other"] = (byType[t.type || "other"] || 0) + 1; });
      const entries = Object.entries(byType).sort((a, b) => b[1] - a[1]);
      if (entries.length) {
        const colors = ["#4285f4", "#34a853", "#f5a623", "#ea4335", "#8b5cf6", "#1a9aaa"];
        new Chart(typeCtx, {
          type: "doughnut",
          data: { labels: entries.map(([k]) => k), datasets: [{ data: entries.map(([, v]) => v), backgroundColor: colors, borderWidth: 0 }] },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { font: { size: 10 } } } } },
        });
      }
    }

    // Session distribution bar
    const sessCtx = document.getElementById("adminSchedSessionChart");
    if (sessCtx && _schedData.sessions?.length) {
      const labels = _schedData.sessions.map(s => s.session_id.replace("line_", "").substring(0, 10));
      const values = _schedData.sessions.map(s => s.task_count);
      new Chart(sessCtx, {
        type: "bar",
        data: { labels, datasets: [{ label: "任務數", data: values, backgroundColor: "#1A9AAA", borderRadius: 4 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
          scales: { x: { ticks: { font: { size: 9 } } }, y: { beginAtZero: true, ticks: { stepSize: 1, font: { size: 10 } } } } },
      });
    }
  }

  window._toggleSchedTask = async function (sessionId, taskId) {
    try {
      const resp = await fetch(`/api/schedules/${sessionId}/${taskId}/toggle`, { method: "POST" });
      if (resp.ok) renderSchedules();
    } catch (_) {}
  };

  window._deleteSchedTask = function (sessionId, taskId, name) {
    document.getElementById("adminDeleteModal")?.remove();
    const overlay = document.createElement("div");
    overlay.className = "admin-delete-overlay";
    overlay.id = "adminDeleteModal";
    overlay.innerHTML = `
      <div class="admin-delete-modal">
        <h3>確認刪除排程任務</h3>
        <div class="admin-delete-skill-name">${_esc(name)} (${taskId})</div>
        <div class="admin-delete-hint">此操作將永久移除該排程任務。</div>
        <div class="admin-delete-actions">
          <button class="admin-delete-cancel" onclick="document.getElementById('adminDeleteModal')?.remove()">取消</button>
          <button class="admin-delete-confirm" id="adminSchedDelBtn">確認刪除</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.getElementById("adminSchedDelBtn").addEventListener("click", async () => {
      try { await fetch(`/api/schedules/${sessionId}/${taskId}`, { method: "DELETE" }); } catch (_) {}
      overlay.remove();
      renderSchedules();
    });
  };

  // ══════════════════════════════════════════════════════════
  // Workflows Management Page (Phase 3)
  // ══════════════════════════════════════════════════════════

  const _WF_COLORS = ["#34a853","#1a9aaa","#4285f4","#ea4335","#fbbc04","#8b5cf6","#ec4899","#f97316"];
  let _allWorkflows = [];

  async function renderWorkflows() {
    content.innerHTML = `
      <h1 class="admin-page-title">Workflows 管理</h1>
      <p class="admin-page-desc">管理工作流、執行記錄與排程</p>
      <div class="admin-toolbar">
        <input class="admin-search" id="adminWfSearch" type="text" placeholder="搜尋工作流名稱..." autocomplete="off" />
        <select class="admin-filter-select" id="adminWfScopeFilter">
          <option value="">全部 Scope</option>
          <option value="system">System</option>
          <option value="department">Department</option>
          <option value="personal">Personal</option>
        </select>
        <div class="admin-toolbar-spacer"></div>
        <button class="admin-btn admin-btn-primary" onclick="_adminCreateWorkflow()">+ 新增工作流</button>
      </div>
      <div class="admin-row">
        <div class="admin-col-main">
          <div class="admin-wf-grid" id="adminWfGrid"></div>
        </div>
        <div class="admin-col-side">
          <div class="admin-chart-card">
            <div class="admin-chart-title">最近執行記錄</div>
            <div class="admin-timeline" id="adminWfTimeline"></div>
          </div>
          <div class="admin-chart-card" style="margin-top:16px;">
            <div class="admin-chart-title">執行統計</div>
            <div class="admin-chart-wrap" style="height:180px;"><canvas id="adminWfStatsChart"></canvas></div>
          </div>
        </div>
      </div>
    `;

    // Load workflows
    const _owner = _user.employee_id || _user.id || "";
    try {
      const resp = await fetch(`/api/workflows?owner=${_owner}`);
      if (resp.ok) {
        const data = await resp.json();
        _allWorkflows = data.workflows || [];
      }
    } catch (_) {}

    _renderWfGrid();
    _renderWfTimeline();
    _renderWfStats();

    // Filter handlers
    document.getElementById("adminWfSearch")?.addEventListener("input", _renderWfGrid);
    document.getElementById("adminWfScopeFilter")?.addEventListener("change", _renderWfGrid);
  }

  function _renderWfGrid() {
    const grid = document.getElementById("adminWfGrid");
    if (!grid) return;
    const q = (document.getElementById("adminWfSearch")?.value || "").toLowerCase();
    const scopeF = document.getElementById("adminWfScopeFilter")?.value || "";

    let html = `<div class="admin-wf-card-new" onclick="_adminCreateWorkflow()">
      <div class="admin-wf-card-new-inner"><div class="admin-wf-card-new-icon">+</div><div class="admin-wf-card-new-label">新增工作流</div></div></div>`;

    _allWorkflows.forEach((wf, i) => {
      const scope = wf.scope || "personal";
      const name = wf.name || wf.id;
      if (q && !name.toLowerCase().includes(q)) return;
      if (scopeF && scope !== scopeF) return;

      const color = _WF_COLORS[i % _WF_COLORS.length];
      const key = wf.workflow_key || wf.id;
      const sl = scope === "system" ? "系統" : scope === "department" ? "部門" : "個人";
      const blocks = wf.block_count || 0;
      const conns = wf.connection_count || 0;
      // Status: draft if 0 blocks, active otherwise
      const status = blocks > 0 ? "active" : "draft";

      html += `<div class="admin-wf-card" onclick="_adminOpenWorkflow('${wf.id}','${scope}')">
        <div class="admin-wf-card-header" style="background:${color};">
          <span class="admin-wf-card-status ${status}"></span>
          ${_esc(name)}
          <div class="admin-wf-card-key">${_esc(key)}</div>
        </div>
        <div class="admin-wf-card-body"><div class="admin-wf-card-meta">
          <span>${sl}</span><span class="admin-wf-card-meta-dot"></span>
          <span>${blocks} 節點</span><span class="admin-wf-card-meta-dot"></span>
          <span>${conns} 連接</span>
        </div></div>
      </div>`;
    });
    grid.innerHTML = html;
  }

  function _renderWfTimeline() {
    const el = document.getElementById("adminWfTimeline");
    if (!el) return;
    // Placeholder — real execution logs will come from a future API
    if (_allWorkflows.length === 0) {
      el.innerHTML = '<div style="font-size:0.75rem;color:var(--text-tertiary);padding:8px 0;">尚無執行記錄</div>';
      return;
    }
    let html = "";
    _allWorkflows.slice(0, 5).forEach(wf => {
      const status = (wf.block_count || 0) > 0 ? "ok" : "run";
      html += `<div class="admin-timeline-item">
        <div class="admin-timeline-rail"><div class="admin-timeline-dot ${status}"></div><div class="admin-timeline-line"></div></div>
        <div class="admin-timeline-body">
          <div class="admin-timeline-title">${_esc(wf.name || wf.id)}</div>
          <div class="admin-timeline-desc">${wf.updated_at ? wf.updated_at.slice(0, 16).replace("T", " ") : "—"}</div>
        </div>
      </div>`;
    });
    el.innerHTML = html;
  }

  function _renderWfStats() {
    const ctx = document.getElementById("adminWfStatsChart");
    if (!ctx || typeof Chart === "undefined") return;
    const scopeCounts = { system: 0, department: 0, personal: 0 };
    _allWorkflows.forEach(wf => { scopeCounts[wf.scope || "personal"]++; });
    new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["System", "Department", "Personal"],
        datasets: [{ data: [scopeCounts.system, scopeCounts.department, scopeCounts.personal], backgroundColor: ["#059669", "#4285f4", "#1a9aaa"], borderWidth: 0 }],
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { font: { size: 11 } } } } },
    });
  }

  window._adminCreateWorkflow = async function () {
    const _chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let _r = ""; for (let i = 0; i < 20; i++) _r += _chars.charAt(Math.floor(Math.random() * _chars.length));
    const wfKey = "WorkflowK_" + _r, wfId = "wf-" + Date.now();
    try {
      await fetch(`/api/workflows/${wfId}`, { method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ name: "新工作流", blocks: [], connections: [], scope: "personal", owner: _user.employee_id || _user.id || "", context: { workflow_key: wfKey } }) });
    } catch (_) {}
    renderWorkflows(); // Refresh
  };

  window._adminOpenWorkflow = function (wfId, scope) {
    // Navigate to chat.html workflow designer with this workflow
    const owner = _user.employee_id || _user.id || "";
    window.location.href = `chat.html?wf=${wfId}&scope=${scope}&owner=${owner}`;
  };

  // ══════════════════════════════════════════════════════════
  // Skills Management Page (Phase 2)
  // ══════════════════════════════════════════════════════════

  let _allSkills = {};

  async function renderSkills() {
    content.innerHTML = `
      <h1 class="admin-page-title">Skills 管理</h1>
      <p class="admin-page-desc">管理系統、部門與個人技能</p>
      <div class="admin-toolbar">
        <input class="admin-search" id="adminSkillSearch" type="text" placeholder="搜尋技能名稱..." autocomplete="off" />
        <span class="admin-kbd">Ctrl+K</span>
        <select class="admin-filter-select" id="adminSkillScopeFilter">
          <option value="">全部 Scope</option>
          <option value="system">System</option>
          <option value="dept">Department</option>
          <option value="user">Personal</option>
        </select>
        <select class="admin-filter-select" id="adminSkillModeFilter">
          <option value="">全部 Mode</option>
          <option value="executable">Executable</option>
          <option value="code">Code</option>
          <option value="semantic">Semantic</option>
        </select>
        <div class="admin-toolbar-spacer"></div>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr><th>名稱</th><th>Mode</th><th>Scope</th><th>Timeout</th><th>操作</th></tr></thead>
          <tbody id="adminSkillTableBody"></tbody>
        </table>
      </div>
    `;

    // Load skills
    try {
      const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
      const params = new URLSearchParams();
      if (_u.department_code) params.set("dept", _u.department_code);
      if (_u.employee_id || _u.id) params.set("uid", _u.employee_id || _u.id);
      const resp = await fetch("/skills/list?" + params);
      if (resp.ok) {
        const data = await resp.json();
        _allSkills = data.skills || {};
        _renderSkillTable();
      }
    } catch (_) {}

    // Filter handlers
    document.getElementById("adminSkillSearch")?.addEventListener("input", _renderSkillTable);
    document.getElementById("adminSkillScopeFilter")?.addEventListener("change", _renderSkillTable);
    document.getElementById("adminSkillModeFilter")?.addEventListener("change", _renderSkillTable);
  }

  function _detectMode(skill) {
    const p = skill.path || "";
    // Check from path: if scripts/main.py → executable, if scripts/*.py → code, else semantic
    if (p.includes("scripts")) return "executable";
    return "semantic";
  }

  function _renderSkillTable() {
    const tbody = document.getElementById("adminSkillTableBody");
    if (!tbody) return;
    const q = (document.getElementById("adminSkillSearch")?.value || "").toLowerCase();
    const scopeF = document.getElementById("adminSkillScopeFilter")?.value || "";
    const modeF = document.getElementById("adminSkillModeFilter")?.value || "";

    let html = "";
    let count = 0;
    Object.entries(_allSkills).forEach(([key, skill]) => {
      const name = skill.short_name || key;
      const display = skill.display_name || name;
      const scope = skill.scope || "system";
      const scopeShort = scope.startsWith("dept:") ? "dept" : scope.startsWith("user:") ? "user" : scope;
      const scopeLabel = scope === "system" ? "System" : scope.startsWith("dept:") ? "Department" : "Personal";
      const mode = _detectMode(skill);
      const timeout = "30s";
      const editable = true; // Admin panel = full access to all skills

      // Filters
      if (q && !name.toLowerCase().includes(q) && !display.toLowerCase().includes(q)) return;
      if (scopeF && scopeShort !== scopeF) return;
      if (modeF && mode !== modeF) return;

      count++;
      html += `<tr>
        <td><span class="admin-table-name" onclick="_openSkillDrawer('${name}')">${_esc(display)}</span><br/><span style="font-size:0.65rem;color:var(--text-tertiary);">${name}</span></td>
        <td><span class="admin-mode-dot ${mode}">${mode}</span></td>
        <td><span class="admin-scope-badge ${scopeShort}">${scopeLabel}</span></td>
        <td>${timeout}</td>
        <td><div class="admin-table-actions">
          <button class="admin-table-action" onclick="_openSkillDrawer('${name}')">編輯</button>
          ${editable ? `<button class="admin-table-action danger" onclick="_deleteSkillFromAdmin('${name}')">刪除</button>` : ""}
        </div></td>
      </tr>`;
    });
    if (!count) html = `<tr><td colspan="5" style="text-align:center;color:var(--text-tertiary);padding:30px;">無符合條件的技能</td></tr>`;
    tbody.innerHTML = html;
  }

  function _esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
  function _fmtExt(ext) { if (!ext || ext.length <= 3) return _esc(ext); return _esc(ext.substring(0,3)) + ' / ' + _esc(ext.substring(3)); }

  // ── Skill Drawer ───────────────────────────────────────────
  window._openSkillDrawer = async function (skillName) {
    document.getElementById("adminDrawerOverlay")?.remove();

    let detail = {}, files = {};
    try {
      const [dR, fR] = await Promise.all([
        fetch(`/skills/${skillName}`).then(r => r.json()),
        fetch(`/skills/${skillName}/files`).then(r => r.json()),
      ]);
      detail = dR; files = fR;
    } catch (_) {}

    const meta = detail.metadata || {};
    const raw = detail.raw_content || "";
    const body = raw.split("---").length >= 3 ? raw.split("---").slice(2).join("---").trim() : "";
    const rm = meta.recommended_models || {};
    const _cat = meta.category || "System";
    const _editable = true; // Admin panel = full access

    // File section builder
    const _fileSection = (title, folder, fileType, fileList) => {
      let h = `<div class="admin-drawer-field" style="border-top:1px solid var(--border-subtle);padding-top:12px;margin-top:8px;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <label class="admin-drawer-label" style="margin:0;">${title}</label>
          ${_editable ? `<label style="font-size:0.68rem;color:var(--kway-blue);cursor:pointer;font-weight:600;">上傳<input type="file" style="display:none" onchange="_drawerUpload('${skillName}','${fileType}',this)" /></label>` : ""}
        </div>`;
      if (fileList.length) {
        fileList.forEach(f => {
          h += `<div style="display:flex;align-items:center;justify-content:space-between;padding:3px 0;font-size:0.72rem;color:var(--text-secondary);">
            <span>${_esc(f)}</span>
            ${_editable ? `<button style="background:none;border:none;color:var(--color-error);cursor:pointer;font-size:0.8rem;" onclick="_drawerDeleteFile('${skillName}','${folder}','${f}')">&times;</button>` : ""}
          </div>`;
        });
      } else {
        h += `<div style="font-size:0.68rem;color:var(--text-tertiary);padding:4px 0;">（無檔案）</div>`;
      }
      return h + `</div>`;
    };

    const overlay = document.createElement("div");
    overlay.className = "admin-drawer-overlay open";
    overlay.id = "adminDrawerOverlay";
    overlay.innerHTML = `
      <div class="admin-drawer">
        <div class="admin-drawer-header">
          <div>
            <span class="admin-drawer-title">${_esc(meta.display_name || skillName)}</span>
            <div style="font-size:0.58rem;color:var(--text-tertiary);font-family:monospace;margin-top:2px;">${meta.skillk_id || ""}</div>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            ${_editable ? `<button class="admin-btn" style="font-size:0.68rem;color:var(--color-error);border-color:var(--color-error-bg);" onclick="_deleteSkillFromAdmin('${skillName}')">刪除</button>` : ""}
            ${_editable ? `<button class="admin-btn" style="font-size:0.68rem;" onclick="_drawerRollback('${skillName}')">還原</button>` : ""}
            <button class="admin-drawer-close" onclick="document.getElementById('adminDrawerOverlay')?.remove()">&times;</button>
          </div>
        </div>
        <div class="admin-drawer-body">
          <div class="admin-drawer-row">
            <div class="admin-drawer-field" style="flex:2;">
              <label class="admin-drawer-label">顯示名稱 (Display Name)</label>
              <input class="admin-drawer-input" id="drawerDisplayName" value="${_esc(meta.display_name || "")}" ${_editable?"":"readonly"} />
            </div>
            <div class="admin-drawer-field" style="flex:1;">
              <label class="admin-drawer-label">技能群組 (Category)</label>
              <select class="admin-drawer-input" id="drawerCategory" ${_editable?"":"disabled"}>
                <option value="System"${_cat==="System"?" selected":""}>System</option>
                <option value="Department"${_cat==="Department"?" selected":""}>Department</option>
                <option value="Personal"${_cat==="Personal"?" selected":""}>Personal</option>
              </select>
            </div>
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">名稱 (Name)</label>
            <input class="admin-drawer-input" value="${meta.name || skillName}" readonly style="opacity:0.6;" />
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">簡介 (Description)</label>
            <textarea class="admin-drawer-input" id="drawerDesc" rows="3" ${_editable?"":"readonly"}>${_esc((meta.description || "").trim())}</textarea>
          </div>
          <div class="admin-drawer-row">
            <div class="admin-drawer-field"><label class="admin-drawer-label">技能版本 (Version)</label><input class="admin-drawer-input" id="drawerVersion" value="${meta.version || "1.0.0"}" ${_editable?"":"readonly"} /></div>
            <div class="admin-drawer-field"><label class="admin-drawer-label">操作風險 (Risk)</label>
              <select class="admin-drawer-input" id="drawerRisk" ${_editable?"":"disabled"}>
                <option value="low"${meta.risk_level==="low"?" selected":""}>low</option>
                <option value="high"${meta.risk_level==="high"?" selected":""}>high</option>
              </select></div>
            <div class="admin-drawer-field"><label class="admin-drawer-label">逾時等待 (Timeout)</label><input class="admin-drawer-input" id="drawerTimeout" type="number" value="${meta.execution_timeout || 30}" ${_editable?"":"readonly"} /></div>
          </div>
          <div class="admin-drawer-field" style="border-top:1px solid var(--border-subtle);padding-top:12px;margin-top:4px;">
            <label class="admin-drawer-label">建議模型 (Recommended Models)</label>
            <div class="admin-drawer-row">
              <div class="admin-drawer-field"><label class="admin-drawer-label" style="font-size:0.62rem;">OpenAI</label><input class="admin-drawer-input" id="drawerModelOai" value="${rm.openai||""}" placeholder="自動評估" ${_editable?"":"readonly"} /></div>
              <div class="admin-drawer-field"><label class="admin-drawer-label" style="font-size:0.62rem;">Gemini</label><input class="admin-drawer-input" id="drawerModelGem" value="${rm.gemini||""}" placeholder="自動評估" ${_editable?"":"readonly"} /></div>
              <div class="admin-drawer-field"><label class="admin-drawer-label" style="font-size:0.62rem;">Claude</label><input class="admin-drawer-input" id="drawerModelCla" value="${rm.claude||""}" placeholder="自動評估" ${_editable?"":"readonly"} /></div>
            </div>
            <div style="font-size:0.6rem;color:var(--text-tertiary);margin-top:2px;">儲存時自動評估，或手動指定具體模型名稱</div>
          </div>
          <div class="admin-drawer-field" style="border-top:1px solid var(--border-subtle);padding-top:12px;margin-top:4px;">
            <label class="admin-drawer-label">提示詞 (Prompt)</label>
            <textarea class="admin-drawer-input" id="drawerBody" rows="12" style="font-family:monospace;font-size:0.73rem;" ${_editable?"":"readonly"}>${_esc(body)}</textarea>
          </div>
          ${_fileSection("知識參考 (References)", "references", "knowledge", files.references || [])}
          ${_fileSection("程式操作 (Scripts)", "scripts", "script", files.scripts || [])}
          ${_fileSection("模板檔案 (Assets)", "assets", "asset", files.assets || [])}
        </div>
        ${_editable ? `<div class="admin-drawer-footer">
          <button class="admin-btn" style="color:var(--text-secondary);" onclick="document.getElementById('adminDrawerOverlay')?.remove()">取消</button>
          <button class="admin-btn admin-btn-primary" onclick="_saveSkillFromDrawer('${skillName}')">儲存</button>
        </div>` : ""}
      </div>
    `;
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  };

  window._saveSkillFromDrawer = async function (skillName) {
    const origMeta = (await fetch(`/skills/${skillName}`).then(r => r.json())).metadata || {};
    const name = origMeta.name || skillName;
    const displayName = document.getElementById("drawerDisplayName")?.value?.trim() || "";
    const category = document.getElementById("drawerCategory")?.value || "System";
    const desc = document.getElementById("drawerDesc")?.value || "";
    const version = document.getElementById("drawerVersion")?.value || "1.0.0";
    const risk = document.getElementById("drawerRisk")?.value || "low";
    const timeout = document.getElementById("drawerTimeout")?.value || "30";
    const body = document.getElementById("drawerBody")?.value || "";
    const mOai = document.getElementById("drawerModelOai")?.value?.trim();
    const mGem = document.getElementById("drawerModelGem")?.value?.trim();
    const mCla = document.getElementById("drawerModelCla")?.value?.trim();

    // Generate skillk_id if missing
    let skillkId = origMeta.skillk_id || "";
    if (!skillkId) {
      const _ch = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
      let _r = ""; for (let i = 0; i < 20; i++) _r += _ch.charAt(Math.floor(Math.random() * _ch.length));
      skillkId = "SkillK_" + _r;
    }

    let yaml = `---\nname: ${name}\nskillk_id: ${skillkId}\n`;
    if (displayName) yaml += `display_name: "${displayName}"\n`;
    yaml += `category: ${category}\n`;
    if (origMeta.provider) yaml += `provider: ${origMeta.provider}\n`;
    yaml += `version: "${version}"\n`;
    if (desc) yaml += `description: >\n  ${desc.replace(/\n/g, "\n  ")}\n`;
    if (origMeta.runtime_requirements?.length) yaml += `runtime_requirements: [${origMeta.runtime_requirements.join(", ")}]\n`;
    else yaml += `runtime_requirements: []\n`;
    yaml += `risk_level: ${risk}\n`;
    if (origMeta.risk_description) yaml += `risk_description: >\n  ${String(origMeta.risk_description).trim().replace(/\n/g, "\n  ")}\n`;
    if (parseInt(timeout) !== 30) yaml += `execution_timeout: ${timeout}\n`;
    if (mOai || mGem || mCla) {
      yaml += `recommended_models:\n`;
      if (mOai) yaml += `  openai: ${mOai}\n`;
      if (mGem) yaml += `  gemini: ${mGem}\n`;
      if (mCla) yaml += `  claude: ${mCla}\n`;
    }
    yaml += `---\n\n${body}`;

    const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
    try {
      const resp = await fetch(`/skills/${skillName}`, {
        method: "PUT", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ yaml_content: yaml, user_name: _u.name || "unknown", user_id: _u.id || "unknown" }),
      });
      if (resp.ok) {
        document.getElementById("adminDrawerOverlay")?.remove();
        renderSkills();
      }
    } catch (_) {}
  };

  window._drawerUpload = async function (skillName, fileType, input) {
    if (!input.files[0]) return;
    const fd = new FormData();
    fd.append("file", input.files[0]);
    fd.append("file_type", fileType);
    try {
      const resp = await fetch(`/skills/${skillName}/upload`, { method: "POST", body: fd });
      if (resp.ok) _openSkillDrawer(skillName); // Refresh drawer
    } catch (_) {}
  };

  window._drawerDeleteFile = async function (skillName, folder, filename) {
    if (!confirm(`確認刪除 ${folder}/${filename}？`)) return;
    try {
      await fetch(`/skills/${skillName}/files/${folder}/${filename}`, { method: "DELETE" });
      _openSkillDrawer(skillName); // Refresh drawer
    } catch (_) {}
  };

  window._drawerRollback = async function (skillName) {
    if (!confirm("確認還原至上一次備份？")) return;
    try {
      await fetch(`/skills/${skillName}/rollback`, { method: "POST" });
      _openSkillDrawer(skillName);
    } catch (_) {}
  };

  window._deleteSkillFromAdmin = function (skillName) {
    // Close drawer if open
    document.getElementById("adminDrawerOverlay")?.remove();
    // Build modal (same style as chat.html SkillEditor delete modal)
    const overlay = document.createElement("div");
    overlay.className = "admin-delete-overlay";
    overlay.id = "adminDeleteModal";
    overlay.innerHTML = `
      <div class="admin-delete-modal">
        <h3>確認刪除 Agent Skill</h3>
        <div class="admin-delete-skill-name">${_esc(skillName)}</div>
        <label for="adminDeleteReason">刪除原因（必填）</label>
        <textarea id="adminDeleteReason" placeholder="請輸入刪除原因，至少 5 個字..."></textarea>
        <div class="admin-delete-hint">此操作不可復原，將完全移除該 Skill 及所有相關檔案，並同步 Commit。</div>
        <div class="admin-delete-actions">
          <button class="admin-delete-cancel" onclick="document.getElementById('adminDeleteModal')?.remove()">取消</button>
          <button class="admin-delete-confirm" id="adminDeleteConfirm" disabled>確認刪除</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const ta = overlay.querySelector("#adminDeleteReason");
    const btn = overlay.querySelector("#adminDeleteConfirm");
    ta.addEventListener("input", () => { btn.disabled = ta.value.trim().length < 5; });
    ta.focus();
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });

    btn.addEventListener("click", async () => {
      const reason = ta.value.trim();
      if (reason.length < 5) return;
      btn.disabled = true;
      btn.textContent = "刪除中...";
      const _u = JSON.parse(sessionStorage.getItem("kway_user") || "{}");
      try {
        await fetch(`/skills/${skillName}`, {
          method: "DELETE", headers: {"Content-Type":"application/json"},
          body: JSON.stringify({ reason, user_name: _u.name || "unknown", user_id: _u.id || "unknown" }),
        });
        overlay.remove();
        renderSkills();
      } catch (_) { overlay.remove(); }
    });
  };

  // ══════════════════════════════════════════════════════════
  // Users Management Page (Phase 5)
  // ══════════════════════════════════════════════════════════

  async function renderUsers() {
    content.innerHTML = `
      <h1 class="admin-page-title">使用者管理</h1>
      <p class="admin-page-desc">員工列表與 Profile 檢視</p>
      <div class="admin-toolbar">
        <input class="admin-search" id="adminUserSearch" type="text" placeholder="搜尋姓名、信箱、員工編號..." />
        <select class="admin-filter-select" id="adminUserDeptFilter">
          <option value="">全部部門</option>
        </select>
        <button class="admin-btn admin-btn-primary" onclick="_openNewUserDrawer()">+ 新增人員</button>
        <div class="admin-toolbar-spacer"></div>
        <span style="font-size:0.72rem;color:var(--text-tertiary);" id="adminUserCount"></span>
      </div>
      <div class="admin-table-wrap">
        <table class="admin-table">
          <thead><tr>
            <th class="admin-sortable" data-sort="employee_id">員工編號 <span class="admin-sort-icon">▲▼</span></th>
            <th class="admin-sortable" data-sort="name">姓名 <span class="admin-sort-icon">▲▼</span></th>
            <th class="admin-sortable" data-sort="department_code">部門 <span class="admin-sort-icon">▲▼</span></th>
            <th class="admin-sortable" data-sort="title">職稱 <span class="admin-sort-icon">▲▼</span></th>
            <th class="admin-sortable" data-sort="email">信箱 <span class="admin-sort-icon">▲▼</span></th>
            <th class="admin-sortable" data-sort="extension">分機 <span class="admin-sort-icon">▲▼</span></th>
            <th>操作</th>
          </tr></thead>
          <tbody id="adminUserTableBody"></tbody>
        </table>
      </div>
    `;

    // Load departments for filter
    let allEmployees = [];
    try {
      const [empResp, deptResp] = await Promise.all([
        fetch("/api/auth/employees"),
        fetch("/api/auth/departments"),
      ]);
      if (empResp.ok) { const d = await empResp.json(); allEmployees = d.employees || []; }
      if (deptResp.ok) {
        const d = await deptResp.json();
        const sel = document.getElementById("adminUserDeptFilter");
        if (sel) (d.departments || []).forEach(dept => {
          const opt = document.createElement("option");
          opt.value = dept.code; opt.textContent = `(${dept.code}) ${dept.name}`;
          sel.appendChild(opt);
        });
      }
    } catch (_) {}

    let _userSortKey = "";
    let _userSortAsc = true;

    function _renderUserTable() {
      const tbody = document.getElementById("adminUserTableBody");
      if (!tbody) return;
      const q = (document.getElementById("adminUserSearch")?.value || "").toLowerCase();
      const deptF = document.getElementById("adminUserDeptFilter")?.value || "";

      // Sort (extension uses first 3 digits as sort key)
      let sorted = [...allEmployees];
      if (_userSortKey) {
        sorted.sort((a, b) => {
          let va = (a[_userSortKey] || "").toString().toLowerCase();
          let vb = (b[_userSortKey] || "").toString().toLowerCase();
          if (_userSortKey === "extension") { va = va.substring(0, 3); vb = vb.substring(0, 3); }
          return _userSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        });
      }

      // Update sort icons
      document.querySelectorAll(".admin-sortable").forEach(th => {
        const icon = th.querySelector(".admin-sort-icon");
        if (!icon) return;
        if (th.dataset.sort === _userSortKey) {
          icon.textContent = _userSortAsc ? "▲" : "▼";
          icon.style.opacity = "1";
        } else {
          icon.textContent = "▲▼";
          icon.style.opacity = "0.3";
        }
      });

      // Separate admins (pinned top, sorted by employee_id) from non-admins (user-sortable)
      const admins = sorted.filter(e => e.role === "admin").sort((a, b) => (a.employee_id || "").localeCompare(b.employee_id || ""));
      const others = sorted.filter(e => e.role !== "admin");
      const finalList = [...admins, ...others];

      let html = "", count = 0;
      finalList.forEach(emp => {
        const name = emp.name || "";
        const email = emp.email || "";
        const eid = emp.employee_id || "";
        if (q && !name.toLowerCase().includes(q) && !email.toLowerCase().includes(q) && !eid.includes(q)) return;
        if (deptF && emp.department_code !== deptF) return;
        count++;
        const isAdmin = emp.role === "admin";
        html += `<tr>
          <td>${_esc(eid)}</td>
          <td><div style="display:flex;align-items:center;"><span style="width:46px;flex-shrink:0;text-align:right;padding-right:6px;">${isAdmin ? '<span class="admin-scope-badge" style="background:var(--color-error-bg);color:var(--color-error);font-size:0.6rem;">Admin</span>' : ''}</span><span class="admin-table-name" onclick="_openUserDrawer('${_esc(eid)}')">${_esc(name)}</span></div></td>
          <td><span class="admin-scope-badge department">${_esc(emp.department_code || "")} ${_esc(emp.department_name || "")}</span></td>
          <td>${_esc(emp.title || "")}</td>
          <td style="font-size:0.72rem;">${_esc(email)}</td>
          <td>${_fmtExt(emp.extension || "")}</td>
          <td><button class="admin-table-action" onclick="_openUserDrawer('${_esc(eid)}')">編輯</button></td>
        </tr>`;
      });
      if (!count) html = `<tr><td colspan="7" style="text-align:center;color:var(--text-tertiary);padding:30px;">無符合條件的員工</td></tr>`;
      tbody.innerHTML = html;
      const cEl = document.getElementById("adminUserCount");
      if (cEl) cEl.textContent = `共 ${count} 人`;
    }

    _renderUserTable();
    document.getElementById("adminUserSearch")?.addEventListener("input", _renderUserTable);
    document.getElementById("adminUserDeptFilter")?.addEventListener("change", _renderUserTable);
    // Sort click handlers
    document.querySelectorAll(".admin-sortable").forEach(th => {
      th.style.cursor = "pointer";
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (_userSortKey === key) _userSortAsc = !_userSortAsc;
        else { _userSortKey = key; _userSortAsc = true; }
        _renderUserTable();
      });
    });
  }

  // ══════════════════════════════════════════════════════════
  // System Settings Page (Phase 5)
  // ══════════════════════════════════════════════════════════

  // ── User Edit Drawer ──────────────────────────────────────
  window._openUserDrawer = async function (empId) {
    // Find employee from API
    let emp = null;
    try {
      const resp = await fetch("/api/auth/employees");
      if (resp.ok) {
        const data = await resp.json();
        emp = (data.employees || []).find(e => e.employee_id === empId);
      }
    } catch (_) {}
    if (!emp) return;

    document.getElementById("adminDrawerOverlay")?.remove();
    const overlay = document.createElement("div");
    overlay.className = "admin-drawer-overlay open";
    overlay.id = "adminDrawerOverlay";
    overlay.innerHTML = `
      <div class="admin-drawer">
        <div class="admin-drawer-header">
          <span class="admin-drawer-title">${_esc(empId.padStart(4,"0"))} ${_esc(emp.name)} ${_esc(emp.title || "")}</span>
          <button class="admin-drawer-close" onclick="document.getElementById('adminDrawerOverlay')?.remove()">&times;</button>
        </div>
        <div class="admin-drawer-body">
          <div class="admin-drawer-row">
            <div class="admin-drawer-field" style="flex:1;">
              <label class="admin-drawer-label">員工編號</label>
              <input class="admin-drawer-input" value="${_esc(empId)}" readonly style="opacity:0.6;" />
            </div>
            <div class="admin-drawer-field" style="flex:2;">
              <label class="admin-drawer-label">姓名</label>
              <input class="admin-drawer-input" id="userEditName" value="${_esc(emp.name || "")}" />
            </div>
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">電子郵件</label>
            <input class="admin-drawer-input" id="userEditEmail" value="${_esc(emp.email || "")}" />
          </div>
          <div class="admin-drawer-row">
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">部門代號</label>
              <input class="admin-drawer-input" id="userEditDeptCode" value="${_esc(emp.department_code || "")}" />
            </div>
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">部門名稱</label>
              <input class="admin-drawer-input" id="userEditDeptName" value="${_esc(emp.department_name || "")}" />
            </div>
          </div>
          <div class="admin-drawer-row">
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">職稱</label>
              <input class="admin-drawer-input" id="userEditTitle" value="${_esc(emp.title || "")}" />
            </div>
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">分機</label>
              <input class="admin-drawer-input" id="userEditExt" value="${_esc(emp.extension || "")}" />
            </div>
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">角色權限</label>
            <select class="admin-drawer-input" id="userEditRole">
              <option value="editor">Editor（一般編輯者）</option>
              <option value="viewer">Viewer（唯讀）</option>
              <option value="admin">Admin（管理員）</option>
            </select>
          </div>
        </div>
        <div class="admin-drawer-footer">
          <button class="admin-btn" style="color:var(--text-secondary);" onclick="document.getElementById('adminDrawerOverlay')?.remove()">取消</button>
          <button class="admin-btn admin-btn-primary" onclick="_saveUserFromDrawer('${_esc(empId)}')">儲存</button>
        </div>
      </div>
    `;
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  };

  window._openNewUserDrawer = function () {
    document.getElementById("adminDrawerOverlay")?.remove();
    const overlay = document.createElement("div");
    overlay.className = "admin-drawer-overlay open";
    overlay.id = "adminDrawerOverlay";
    overlay.innerHTML = `
      <div class="admin-drawer">
        <div class="admin-drawer-header">
          <span class="admin-drawer-title">新增人員</span>
          <button class="admin-drawer-close" onclick="document.getElementById('adminDrawerOverlay')?.remove()">&times;</button>
        </div>
        <div class="admin-drawer-body">
          <div class="admin-drawer-row">
            <div class="admin-drawer-field" style="flex:1;">
              <label class="admin-drawer-label">員工編號</label>
              <input class="admin-drawer-input" id="userEditId" placeholder="例如：1234" />
            </div>
            <div class="admin-drawer-field" style="flex:2;">
              <label class="admin-drawer-label">姓名</label>
              <input class="admin-drawer-input" id="userEditName" />
            </div>
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">電子郵件</label>
            <input class="admin-drawer-input" id="userEditEmail" />
          </div>
          <div class="admin-drawer-row">
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">部門代號</label>
              <input class="admin-drawer-input" id="userEditDeptCode" />
            </div>
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">部門名稱</label>
              <input class="admin-drawer-input" id="userEditDeptName" />
            </div>
          </div>
          <div class="admin-drawer-row">
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">職稱</label>
              <input class="admin-drawer-input" id="userEditTitle" />
            </div>
            <div class="admin-drawer-field">
              <label class="admin-drawer-label">分機</label>
              <input class="admin-drawer-input" id="userEditExt" />
            </div>
          </div>
          <div class="admin-drawer-field">
            <label class="admin-drawer-label">角色權限</label>
            <select class="admin-drawer-input" id="userEditRole">
              <option value="editor">Editor（一般編輯者）</option>
              <option value="viewer">Viewer（唯讀）</option>
              <option value="admin">Admin（管理員）</option>
            </select>
          </div>
        </div>
        <div class="admin-drawer-footer">
          <button class="admin-btn" style="color:var(--text-secondary);" onclick="document.getElementById('adminDrawerOverlay')?.remove()">取消</button>
          <button class="admin-btn admin-btn-primary" onclick="_createNewUser()">新增</button>
        </div>
      </div>
    `;
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  };

  window._createNewUser = async function () {
    const empId = document.getElementById("userEditId")?.value?.trim();
    if (!empId) { alert("請輸入員工編號"); return; }
    const body = {
      name: document.getElementById("userEditName")?.value?.trim() || "",
      email: document.getElementById("userEditEmail")?.value?.trim() || "",
      department_code: document.getElementById("userEditDeptCode")?.value?.trim() || "",
      department_name: document.getElementById("userEditDeptName")?.value?.trim() || "",
      title: document.getElementById("userEditTitle")?.value?.trim() || "",
      extension: document.getElementById("userEditExt")?.value?.trim() || "",
      role: document.getElementById("userEditRole")?.value || "editor",
    };
    try {
      const resp = await fetch(`/api/auth/employees/${empId}`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify(body),
      });
      if (resp.ok) {
        document.getElementById("adminDrawerOverlay")?.remove();
        renderUsers();
      }
    } catch (_) {}
  };

  window._saveUserFromDrawer = async function (empId) {
    const body = {
      name: document.getElementById("userEditName")?.value?.trim() || "",
      email: document.getElementById("userEditEmail")?.value?.trim() || "",
      department_code: document.getElementById("userEditDeptCode")?.value?.trim() || "",
      department_name: document.getElementById("userEditDeptName")?.value?.trim() || "",
      title: document.getElementById("userEditTitle")?.value?.trim() || "",
      extension: document.getElementById("userEditExt")?.value?.trim() || "",
      role: document.getElementById("userEditRole")?.value || "editor",
    };
    try {
      const resp = await fetch(`/api/auth/employees/${empId}`, {
        method: "PUT", headers: {"Content-Type":"application/json"},
        body: JSON.stringify(body),
      });
      if (resp.ok) {
        document.getElementById("adminDrawerOverlay")?.remove();
        renderUsers(); // Refresh table
      }
    } catch (_) {}
  };

  async function renderSettings() {
    // Load current settings
    let logDays = 30;
    try {
      const resp = await fetch("/api/settings/log-retention");
      if (resp.ok) { const d = await resp.json(); logDays = d.days || 30; }
    } catch (_) {}

    content.innerHTML = `
      <h1 class="admin-page-title">系統設定</h1>
      <p class="admin-page-desc">Server 配置、日誌管理與模型設定</p>

      <div class="admin-settings-card">
        <div class="admin-settings-title">日誌管理</div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">Server Log 保留天數<small>超過天數的 Log 將自動清除（最低 20 天）</small></div>
          <div class="admin-settings-value">
            <input class="admin-settings-input" id="settingLogDays" type="number" value="${logDays}" min="20" />
            <span style="font-size:0.75rem;color:var(--text-muted);">天</span>
            <button class="admin-btn admin-btn-primary" style="font-size:0.7rem;padding:5px 14px;" onclick="_saveLogRetentionAdmin()">儲存</button>
          </div>
        </div>
      </div>

      <div class="admin-settings-card">
        <div class="admin-settings-title">模型配置</div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">OpenAI<small>OPENAI_MODEL 環境變數</small></div>
          <div class="admin-settings-value"><span class="admin-settings-badge ok">${_getEnvDisplay("OPENAI_MODEL", "gpt-4o")}</span></div>
        </div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">Gemini<small>GEMINI_MODEL 環境變數</small></div>
          <div class="admin-settings-value"><span class="admin-settings-badge ok">${_getEnvDisplay("GEMINI_MODEL", "gemini-2.0-flash")}</span></div>
        </div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">Claude<small>CLAUDE_MODEL 環境變數</small></div>
          <div class="admin-settings-value"><span class="admin-settings-badge ok">${_getEnvDisplay("CLAUDE_MODEL", "claude-3-5-sonnet")}</span></div>
        </div>
      </div>

      <div class="admin-settings-card">
        <div class="admin-settings-title">排程管理</div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">APScheduler 狀態<small>背景排程引擎（Profile / Token / Cache 清理等）</small></div>
          <div class="admin-settings-value"><span class="admin-settings-badge ok">運行中</span></div>
        </div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">Log 自動清理<small>每日 02:00 執行</small></div>
          <div class="admin-settings-value"><span class="admin-settings-badge ok">已排程</span></div>
        </div>
      </div>

      <div class="admin-settings-card">
        <div class="admin-settings-title">系統資訊</div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">SKILLS_HOME</div>
          <div class="admin-settings-value"><span style="font-size:0.72rem;color:var(--text-tertiary);font-family:monospace;">Agent_skills/system_skills</span></div>
        </div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">DEPT_SKILLS_HOME</div>
          <div class="admin-settings-value"><span style="font-size:0.72rem;color:var(--text-tertiary);font-family:monospace;">Agent_skills/department_skills</span></div>
        </div>
        <div class="admin-settings-row">
          <div class="admin-settings-label">PERSONAL_SKILLS_HOME</div>
          <div class="admin-settings-value"><span style="font-size:0.72rem;color:var(--text-tertiary);font-family:monospace;">Agent_skills/personal_skills</span></div>
        </div>
      </div>
    `;
  }

  function _getEnvDisplay(key, fallback) { return fallback; /* Server-side env, show default */ }

  window._saveLogRetentionAdmin = async function () {
    const input = document.getElementById("settingLogDays");
    let days = parseInt(input?.value, 10);
    if (isNaN(days) || days < 20) { days = 20; if (input) input.value = 20; }
    try {
      const resp = await fetch("/api/settings/log-retention", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ days }),
      });
      if (resp.ok) alert("Log 保留天數已設定為 " + days + " 天");
    } catch (_) {}
  };

  // ══════════════════════════════════════════════════════════
  // Global Search (Ctrl+K)
  // ══════════════════════════════════════════════════════════

  // Add Ctrl+K listener
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      _openCmdK();
    }
  });

  function _openCmdK() {
    document.getElementById("adminCmdKOverlay")?.remove();
    const overlay = document.createElement("div");
    overlay.className = "admin-cmdk-overlay open";
    overlay.id = "adminCmdKOverlay";
    overlay.innerHTML = `
      <div class="admin-cmdk">
        <input class="admin-cmdk-input" id="adminCmdKInput" type="text" placeholder="搜尋技能、工作流、頁面..." autocomplete="off" autofocus />
        <div class="admin-cmdk-results" id="adminCmdKResults"></div>
      </div>
    `;
    overlay.addEventListener("click", e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);

    let _cmdkIdx = -1;
    const input = document.getElementById("adminCmdKInput");
    input?.focus();
    input?.addEventListener("input", () => { _cmdkIdx = -1; _searchCmdK(input.value); });
    input?.addEventListener("keydown", e => {
      if (e.key === "Escape") { overlay.remove(); return; }
      const items = document.querySelectorAll("#adminCmdKResults .admin-cmdk-item");
      if (e.key === "ArrowDown") { e.preventDefault(); _cmdkIdx = Math.min(_cmdkIdx + 1, items.length - 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); _cmdkIdx = Math.max(_cmdkIdx - 1, 0); }
      else if (e.key === "Enter" && _cmdkIdx >= 0 && items[_cmdkIdx]) { e.preventDefault(); items[_cmdkIdx].click(); return; }
      else return;
      items.forEach((it, i) => it.classList.toggle("active", i === _cmdkIdx));
      if (items[_cmdkIdx]) items[_cmdkIdx].scrollIntoView({ block: "nearest" });
    });
  }

  function _searchCmdK(q) {
    const results = document.getElementById("adminCmdKResults");
    if (!results) return;
    q = q.toLowerCase().trim();
    if (!q) { results.innerHTML = '<div class="admin-cmdk-empty">輸入關鍵字開始搜尋</div>'; return; }

    let html = "";
    // Search pages
    const pageMap = { dashboard:"Dashboard", skills:"Skills 管理", workflows:"Workflows", schedules:"排程監控", tokens:"Token 用量", users:"使用者", settings:"系統設定" };
    Object.entries(pageMap).forEach(([key, label]) => {
      if (label.toLowerCase().includes(q) || key.includes(q)) {
        html += `<div class="admin-cmdk-item" onclick="location.hash='#/${key}';document.getElementById('adminCmdKOverlay')?.remove();">📄 ${label}</div>`;
      }
    });
    // Search skills
    Object.entries(_allSkills).forEach(([key, skill]) => {
      const name = skill.short_name || key;
      const display = skill.display_name || name;
      if (name.toLowerCase().includes(q) || display.toLowerCase().includes(q)) {
        html += `<div class="admin-cmdk-item" onclick="_openSkillDrawer('${name}');document.getElementById('adminCmdKOverlay')?.remove();">⚙️ ${_esc(display)}</div>`;
      }
    });
    // Search workflows
    _allWorkflows.forEach(wf => {
      const name = wf.name || wf.id;
      if (name.toLowerCase().includes(q) || wf.id.toLowerCase().includes(q)) {
        html += `<div class="admin-cmdk-item" onclick="_adminOpenWorkflow('${wf.id}','${wf.scope||"personal"}');document.getElementById('adminCmdKOverlay')?.remove();">🔀 ${_esc(name)}</div>`;
      }
    });

    results.innerHTML = html || '<div class="admin-cmdk-empty">無搜尋結果</div>';
  }

})();
