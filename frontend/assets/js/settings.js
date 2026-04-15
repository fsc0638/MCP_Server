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
  }

  // Load profile from API
  fetch('/api/auth/me', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(d => {
      if (d.status === 'success' && d.user) {
        populateProfile(d.user);
        // Update sessionStorage for other pages
        sessionStorage.setItem('kway_user', JSON.stringify(d.user));
      } else {
        // Fallback to sessionStorage
        populateProfile(userData);
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
    navItem.classList.add('is-active');

    const content = document.getElementById('settingsContent');
    if (content) content.scrollTop = 0;
  };

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
  window.logout = function () {
    sessionStorage.removeItem('kway_user');
    localStorage.removeItem('kway_chat_session');
    window.location.href = 'index.html';
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
