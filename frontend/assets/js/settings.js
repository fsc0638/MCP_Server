/* ============================================================
   K WAY AgentPortal — Settings Page Script
   Architecture: Apple progressive-enhancement pattern
   ============================================================ */

(function () {
  'use strict';

  /* ── User data ────────────────────────────────────────────── */
  let userData = JSON.parse(
    sessionStorage.getItem('kway_user') ||
    '{"name":"—","initials":"—","email":""}'
  );

  /* ── Init user display ────────────────────────────────────── */
  const topbarAvatar  = document.getElementById('topbarAvatar');
  const profileAvatar = document.getElementById('profileAvatar');
  const profileName   = document.getElementById('profileName');
  const profileEmail  = document.getElementById('profileEmail');

  function setAvatar(el, data) {
    if (!el) return;
    const d = data || userData;
    const safeName = (d && typeof d.name === 'string' && d.name.trim()) ? d.name.trim() : 'Workspace User';
    const safeInitials = (d && typeof d.initials === 'string' && d.initials.trim()) ? d.initials.trim() : safeName.charAt(0);
    const pic = (d && typeof d.picture === 'string' && d.picture.trim()) ? d.picture.trim() : '';

    if (pic) {
      el.innerHTML = '';
      const img = document.createElement('img');
      img.src = pic;
      img.alt = safeName;
      img.referrerPolicy = 'no-referrer';
      img.style.width = '100%';
      img.style.height = '100%';
      img.style.borderRadius = '50%';
      img.style.objectFit = 'cover';
      img.onerror = function () {
        el.innerHTML = '';
        el.textContent = safeInitials || 'U';
      };
      el.appendChild(img);
    } else {
      el.textContent = safeInitials || 'U';
    }
  }

  function populateProfile(data) {
    userData = data;
    setAvatar(topbarAvatar, data);
    setAvatar(profileAvatar, data);
    if (profileName) profileName.textContent = data.name || '—';
    // Show user_id (strip "line_" prefix) in profile card
    const _rawId = data.id || data.session_id || '';
    const _displayId = _rawId.replace(/^line_/, '');
    if (profileEmail) profileEmail.textContent = _displayId ? `User ID : ${_displayId}` : '—';

    // Populate basic info fields
    const fields = {
      settingDisplayName: data.name || '',
      settingEmail: data.email || '',
      settingDepartment: data.department_code ? `(${data.department_code}) ${data.department_name || data.department || ''}` : (data.department_name || data.department || ''),
      settingTitle: data.title || '',
      settingExtension: data.extension || '',
    };
    Object.entries(fields).forEach(([id, val]) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    });

    // Language preference
    const langSel = document.getElementById('settingLanguageSelect');
    if (langSel && data.preferences?.language) {
      langSel.value = data.preferences.language;
    }

    // Update identity verification status
    updateIdVerifyStatus(data);
  }

  /* ── Identity Verification Status ─────────────────────────── */
  function updateIdVerifyStatus(data) {
    const card  = document.getElementById('idVerifyCard');
    const icon  = document.getElementById('idVerifyIcon');
    const title = document.getElementById('idVerifyTitle');
    const desc  = document.getElementById('idVerifyDesc');
    const btn   = document.getElementById('idVerifyBtn');
    if (!card || !title) return;

    const verified = data && data.onboarding_completed && data.employee_id;

    if (verified) {
      card.classList.remove('unverified');
      card.classList.add('verified');
      if (icon)  icon.textContent  = '✅';
      if (title) title.textContent = `已驗證 · 員工編號 ${data.employee_id}`;
      if (desc)  desc.textContent  = `${data.name || ''}${data.department_name ? ' · ' + data.department_name : ''}${data.title ? ' · ' + data.title : ''}`;
      if (btn)   btn.style.display = 'none';
    } else {
      card.classList.remove('verified');
      card.classList.add('unverified');
      if (icon)  icon.textContent  = '⚠️';
      if (title) title.textContent = '尚未驗證身分';
      if (desc)  desc.textContent  = '首次登入需要完成身分驗證，綁定後可自動帶入部門／職稱／分機等資料';
      if (btn) {
        btn.style.display = '';
        btn.textContent   = '驗證身分';
      }
    }
  }

  /* ── Identity Verification Modal ──────────────────────────── */
  let _idVerifyType = 'employee_id';
  let _idVerifyMatchedEmployee = null;

  window.openIdVerifyModal = function () {
    const overlay = document.getElementById('idVerifyOverlay');
    if (!overlay) return;

    // Pre-flight: user must have a server-side session (mcp_session cookie).
    // Accepted providers: LINE, Password. Anyone else gets blocked.
    const provider = (userData && userData.provider) || '';
    const id = (userData && userData.id) || '';
    const hasServerSession =
      provider === 'line' || provider === 'password' ||
      (id && (id.startsWith('line_') || id.startsWith('pw_')));

    if (!hasServerSession) {
      if (window.showToast) {
        window.showToast('身分驗證需要登入 Session，請重新登入後再試', 'error');
      } else {
        alert('身分驗證需要登入 Session。\n\n請先登出並重新登入。');
      }
      return;
    }

    _showIdVerifyStep(1);
    overlay.style.display = 'flex';
    setTimeout(() => { document.getElementById('idVerifyQueryInput')?.focus(); }, 50);
    // Close on backdrop click
    overlay.onclick = (e) => { if (e.target === overlay) closeIdVerifyModal(); };
  };

  window.closeIdVerifyModal = function () {
    const overlay = document.getElementById('idVerifyOverlay');
    if (overlay) overlay.style.display = 'none';
    _idVerifyMatchedEmployee = null;
    const input = document.getElementById('idVerifyQueryInput');
    if (input) input.value = '';
    _hideIdVerifyError();
  };

  window.selectIdVerifyType = function (type) {
    _idVerifyType = type;
    document.querySelectorAll('.id-verify-type-tab').forEach(tab => {
      tab.classList.toggle('is-active', tab.dataset.type === type);
    });
    const input = document.getElementById('idVerifyQueryInput');
    if (input) {
      const placeholders = {
        employee_id: '例如：0337 或 337',
        email:       '例如：name@mail.kway.com.tw',
        name:        '例如：王小明（完整全名）',
      };
      input.placeholder = placeholders[type] || '';
      input.value = '';
      input.focus();
    }
    _hideIdVerifyError();
  };

  function _showIdVerifyStep(n) {
    [1, 2, 3, 4].forEach(i => {
      const el = document.getElementById(`idVerifyStep${i}`);
      if (el) el.style.display = (i === n) ? '' : 'none';
    });
  }

  function _showIdVerifyError(msg) {
    const el = document.getElementById('idVerifyError');
    if (el) { el.textContent = msg; el.style.display = ''; }
  }
  function _hideIdVerifyError() {
    const el = document.getElementById('idVerifyError');
    if (el) el.style.display = 'none';
  }

  window.submitIdVerify = async function () {
    const input = document.getElementById('idVerifyQueryInput');
    const btn   = document.getElementById('idVerifySubmitBtn');
    if (!input) return;
    const q = input.value.trim();
    if (!q) { _showIdVerifyError('請輸入查詢內容'); return; }
    _hideIdVerifyError();
    if (btn) { btn.disabled = true; btn.textContent = '查詢中…'; }

    try {
      const resp = await fetch('/api/auth/link-employee', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, query_type: _idVerifyType }),
      });
      const data = await resp.json();

      if (resp.status === 429) {
        _showIdVerifyError(`嘗試次數過多，請稍後再試（${data.detail || ''}）`);
        return;
      }
      if (resp.status === 401) {
        _showIdVerifyError('登入狀態已失效，請重新登入');
        setTimeout(() => { window.location.href = 'index.html?logged_out=1'; }, 1500);
        return;
      }
      if (resp.status === 404) {
        _showIdVerifyError('查無此員工資料，請確認輸入內容');
        return;
      }

      if (data.status === 'ambiguous') {
        _renderCandidates(data.candidates || []);
        _showIdVerifyStep(2);
        return;
      }

      if (data.status === 'success' && data.user) {
        _idVerifyMatchedEmployee = data.user;
        _showConfirmation(data.user);
        _showIdVerifyStep(3);
        return;
      }

      _showIdVerifyError('查詢失敗，請稍後再試');
    } catch (err) {
      _showIdVerifyError('網路錯誤：' + err.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '查詢'; }
    }
  };

  function _renderCandidates(list) {
    const wrap = document.getElementById('idVerifyCandidates');
    if (!wrap) return;
    wrap.innerHTML = list.map(c => `
      <div class="id-verify-candidate" onclick="pickIdVerifyCandidate('${encodeURIComponent(c.employee_id)}')">
        <div class="id-verify-candidate-name">${_esc(c.name)}（員編 ${_esc(c.employee_id)}）</div>
        <div class="id-verify-candidate-meta">${_esc(c.department_code)} ${_esc(c.department_name)}${c.title ? ' · ' + _esc(c.title) : ''}${c.email_hint ? ' · ' + _esc(c.email_hint) : ''}</div>
      </div>
    `).join('');
  }

  window.pickIdVerifyCandidate = async function (encodedEmpId) {
    const empId = decodeURIComponent(encodedEmpId);
    try {
      const resp = await fetch('/api/auth/link-employee', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: '', confirm_employee_id: empId }),
      });
      const data = await resp.json();
      if (data.status === 'success' && data.user) {
        _idVerifyMatchedEmployee = data.user;
        _showConfirmation(data.user);
        _showIdVerifyStep(3);
      } else {
        _showIdVerifyError('選擇綁定失敗');
        _showIdVerifyStep(1);
      }
    } catch (err) {
      _showIdVerifyError('網路錯誤：' + err.message);
      _showIdVerifyStep(1);
    }
  };

  window.backToIdVerifyStep1 = function () {
    _showIdVerifyStep(1);
    _idVerifyMatchedEmployee = null;
    _hideIdVerifyError();
  };

  function _showConfirmation(emp) {
    const card = document.getElementById('idVerifyConfirmCard');
    if (!card) return;
    const rows = [
      ['姓名',   emp.name],
      ['員工編號', emp.employee_id],
      ['部門',   `${emp.department_code || ''} ${emp.department_name || ''}`.trim()],
      ['職稱',   emp.title || '—'],
      ['分機',   emp.extension || '—'],
      ['Email',  emp.email || '—'],
    ];
    card.innerHTML = rows.map(([k, v]) =>
      `<div class="id-verify-confirm-row"><span class="id-verify-confirm-row-label">${k}</span><span class="id-verify-confirm-row-value">${_esc(v)}</span></div>`
    ).join('');
  }

  window.confirmIdVerifyBinding = function () {
    // At this point binding is already persisted server-side (happens in step 1 or step 2).
    // This button just shows success feedback and reloads the page.
    _showIdVerifyStep(4);
    const msgEl = document.getElementById('idVerifySuccessMsg');
    if (msgEl && _idVerifyMatchedEmployee) {
      msgEl.textContent = `已綁定員工「${_idVerifyMatchedEmployee.name}」，頁面將自動重新載入。`;
    }
    setTimeout(() => { window.location.reload(); }, 1500);
  };

  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // Enter key = submit
  document.addEventListener('keydown', function (e) {
    const overlay = document.getElementById('idVerifyOverlay');
    if (!overlay || overlay.style.display === 'none') return;
    if (e.key === 'Enter') {
      const step1 = document.getElementById('idVerifyStep1');
      if (step1 && step1.style.display !== 'none') { submitIdVerify(); }
    } else if (e.key === 'Escape') {
      closeIdVerifyModal();
    }
  });

  // Load profile from API
  fetch('/api/auth/me', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(d => {
      if (d.status === 'success' && d.user) {
        populateProfile(d.user);
        // Update sessionStorage for other pages
        sessionStorage.setItem('kway_user', JSON.stringify(d.user));
        // Auto-open verify modal if ?verify=1 and user hasn't bound yet
        try {
          const params = new URLSearchParams(window.location.search);
          const notVerified = !d.user.onboarding_completed || !d.user.employee_id;
          if (params.get('verify') === '1' && notVerified) {
            setTimeout(() => { if (typeof openIdVerifyModal === 'function') openIdVerifyModal(); }, 300);
            // Strip query so refresh doesn't re-trigger
            if (window.history && window.history.replaceState) {
              window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
            }
          }
        } catch (_) {}
      } else {
        // /me returned status=error. This is NORMAL for form/Google login users
        // (those paths don't set mcp_session cookie, only LINE login does).
        // Fall back to sessionStorage quietly. Only bail-out to login if we have
        // NO local auth state at all (truly anonymous visit).
        console.debug('[settings] /me no server session:', d.message || 'unknown');

        // Treat these fields as "any of these means user logged in via form/Google"
        const hasLocalAuth = !!(userData && (
          userData.provider === 'password' ||
          userData.provider === 'google' ||
          userData.provider === 'line' ||
          userData.id ||
          (userData.email && userData.email.includes('@')) ||
          (userData.name && userData.name !== '—' && userData.name !== 'Workspace User')
        ));

        if (hasLocalAuth) {
          // Legitimate form/Google login — render from sessionStorage
          console.debug('[settings] Using sessionStorage auth (provider=' + (userData.provider || 'unknown') + ')');
          populateProfile(userData);
        } else {
          // No cookie AND no meaningful sessionStorage — redirect to login
          console.warn('[settings] No auth data anywhere, redirecting to login');
          try {
            sessionStorage.removeItem('kway_user');
            localStorage.removeItem('kway_chat_session');
          } catch (_) {}
          setTimeout(() => {
            window.location.href = 'index.html?session_expired=1';
          }, 500);
        }
      }
    })
    .catch(() => populateProfile(userData));

  /* ── Theme Palette Picker ─────────────────────────────────── */
  function _syncPaletteUI(activeId) {
    document.querySelectorAll('.theme-palette-card').forEach(function (card) {
      var isActive = card.getAttribute('data-theme-id') === activeId;
      card.classList.toggle('is-active', isActive);
      card.setAttribute('aria-checked', isActive ? 'true' : 'false');
    });
  }

  var grid = document.getElementById('themePaletteGrid');
  if (grid) {
    // Init: ThemeSwitcher.init() 已在此之前執行，直接讀取目前主題
    _syncPaletteUI(ThemeSwitcher.get());

    // Click handler
    grid.addEventListener('click', function (e) {
      var card = e.target.closest('.theme-palette-card');
      if (!card) return;
      var themeId = card.getAttribute('data-theme-id');
      ThemeSwitcher.set(themeId);
      _syncPaletteUI(themeId);
      showToast('主題已套用：' + card.querySelector('.theme-palette-name').textContent, 'success');
    });

    // Keyboard support (Enter / Space)
    grid.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var card = e.target.closest('.theme-palette-card');
      if (!card) return;
      e.preventDefault();
      card.click();
    });
  }

  /* ── Section navigation ───────────────────────────────────── */
  window.showSection = function (name, navItem) {
    document.querySelectorAll('[id^="section-"]').forEach((s) => (s.style.display = 'none'));
    const target = document.getElementById('section-' + name);
    if (target) target.style.display = 'block';

    document.querySelectorAll('.page-settings-nav-item').forEach((n) => n.classList.remove('is-active'));
    if (navItem) navItem.classList.add('is-active');

    const content = document.getElementById('settingsContent');
    if (content) content.scrollTop = 0;

    // Persist so returning to settings lands on the same section
    if (window.viewState) window.viewState('settings').update({ section: name });
  };

  /* ── Restore last section on page load ─────────────────────── */
  (function _restoreSettingsSection() {
    try {
      if (!window.viewState) return;
      const last = window.viewState('settings').restore('section', '');
      if (!last || last === 'profile') return;  // profile is the default, skip
      // Find the matching nav item (has `showSection('{name}',this)` in onclick)
      const navItem = Array.from(document.querySelectorAll('.page-settings-nav-item')).find(
        el => (el.getAttribute('onclick') || '').includes("'" + last + "'")
      );
      if (navItem) window.showSection(last, navItem);
    } catch (_) {}
  })();

  /* ── Danger actions ───────────────────────────────────────── */
  window.confirmDanger = function (action) {
    if (confirm('確定要「' + action + '」嗎？此操作無法復原。')) {
      showToast(action + ' 已執行', 'success');
    }
  };

  /* ── Log Retention ─────────────────────────────────────────── */
  window.saveLogRetention = async function () {
    const input = document.getElementById('settingLogRetention');
    let days = parseInt(input?.value, 10);
    if (isNaN(days) || days < 20) { days = 20; if (input) input.value = 20; }
    try {
      const resp = await fetch('/api/settings/log-retention', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ days }),
      });
      const data = await resp.json();
      if (resp.ok) showToast('Log 保留天數已設定為 ' + days + ' 天', 'success');
      else showToast('設定失敗: ' + (data.detail || ''), 'error');
    } catch (e) { showToast('設定錯誤: ' + e.message, 'error'); }
  };

  // Load current log retention setting
  (async function () {
    try {
      const resp = await fetch('/api/settings/log-retention');
      if (resp.ok) {
        const data = await resp.json();
        const el = document.getElementById('settingLogRetention');
        if (el && data.days) el.value = data.days;
      }
    } catch (_) {}
  })();

  /* ── Logout ───────────────────────────────────────────────── */
  window.logout = async function () {
    // 1. Call server to invalidate session token + clear httpOnly cookie
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
      });
    } catch (err) {
      console.warn('[logout] API call failed, continuing with client cleanup:', err);
    }

    // 2. Clear all client-side state (sessionStorage + localStorage)
    try {
      sessionStorage.removeItem('kway_user');
      localStorage.removeItem('kway_chat_session');
      localStorage.removeItem('kway_chat_conversations');
      localStorage.removeItem('kway_chat_token_counts');
      localStorage.removeItem('kway_chat_meeting_text');
      // Nuke any other kway_* keys defensively
      Object.keys(localStorage).forEach(k => { if (k.startsWith('kway_')) localStorage.removeItem(k); });
      Object.keys(sessionStorage).forEach(k => { if (k.startsWith('kway_')) sessionStorage.removeItem(k); });
      // Also clear per-page view state memory
      if (typeof window.clearAllViewStates === 'function') window.clearAllViewStates();
    } catch (_) {}

    // 3. Hard redirect so any in-memory module state is discarded
    window.location.href = 'index.html?logged_out=1';
  };

  /* ── Toast notification ───────────────────────────────────── */
  window.showToast = function (msg, type) {
    type = type || 'success';
    const toast     = document.getElementById('toast');
    const toastMsg  = document.getElementById('toastMsg');
    const toastIcon = document.getElementById('toastIcon');
    if (!toast || !toastMsg) return;
    toastMsg.textContent = msg;
    toast.className = 'toast ' + type;
    if (toastIcon) {
      toastIcon.innerHTML = type === 'success'
        ? '<polyline points="20 6 9 17 4 12"/>'
        : '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>';
    }
    toast.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove('show'), 3000);
  };

  /* ── Auto-save on change ──────────────────────────────────── */
  const SETTINGS_KEY = 'kway_settings';

  function saveSettings() {
    const settings = {
      model: document.getElementById('settingModelSelect')?.value,
      language: document.getElementById('settingLanguageSelect')?.value,
      detail: document.getElementById('settingDetailSelect')?.value,
      finance: document.getElementById('settingFinanceToggle')?.checked
    };
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    showToast('設定已儲存', 'success');
  }

  function loadSettings() {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return;
    try {
      const settings = JSON.parse(raw);
      if (settings.model) {
        const sel = document.getElementById('settingModelSelect');
        if (sel) sel.value = settings.model;
      }
      if (settings.language) {
        const sel = document.getElementById('settingLanguageSelect');
        if (sel) sel.value = settings.language;
      }
      if (settings.detail) {
        const sel = document.getElementById('settingDetailSelect');
        if (sel) sel.value = settings.detail;
      }
      if (settings.finance !== undefined) {
        const toggle = document.getElementById('settingFinanceToggle');
        if (toggle) toggle.checked = settings.finance;
      }
    } catch (e) { console.error('Failed to load settings', e); }
  }

  async function syncModelList() {
    const sel = document.getElementById('settingModelSelect');
    if (!sel) return;
    try {
      const res = await fetch('/api/models');
      const data = await res.json();
      if (data.status === 'success' && data.models) {
        sel.innerHTML = '';
        data.models.forEach(m => {
          const opt = document.createElement('option');
          opt.value = m.model;
          opt.textContent = m.display_name;
          sel.appendChild(opt);
        });
        loadSettings(); // Restore selected model after list is populated
      }
    } catch (e) { console.warn('Failed to fetch models for settings', e); }
  }

  document.querySelectorAll('input, select').forEach((el) => {
    el.addEventListener('change', saveSettings);
  });

  // Init
  syncModelList();
  loadSettings();
})();
