/* ============================================================
   K WAY AgentPortal — Settings Page Script
   Architecture: Apple progressive-enhancement pattern
   ============================================================ */

(function () {
  'use strict';

  /* ── User data ────────────────────────────────────────────── */
  const userData = JSON.parse(
    sessionStorage.getItem('kway_user') ||
    '{"name":"林 志遠","initials":"林","email":"user@kway.com.tw"}'
  );

  /* ── Init user display ────────────────────────────────────── */
  const topbarAvatar  = document.getElementById('topbarAvatar');
  const profileAvatar = document.getElementById('profileAvatar');
  const profileName   = document.getElementById('profileName');
  const profileEmail  = document.getElementById('profileEmail');

  if (topbarAvatar)  topbarAvatar.textContent  = userData.initials || userData.name[0];
  if (profileAvatar) profileAvatar.textContent = userData.initials || userData.name[0];
  if (profileName)   profileName.textContent   = userData.name;
  if (profileEmail)  profileEmail.textContent  = userData.email || 'user@kway.com.tw';

  /* ── Theme Palette Picker ─────────────────────────────────── */
  var THEME_KEY = 'kway_theme';

  function _applyTheme(themeId) {
    var root = document.documentElement;
    if (themeId) {
      root.setAttribute('data-theme', themeId);
      localStorage.setItem(THEME_KEY, themeId);
    } else {
      root.removeAttribute('data-theme');
      localStorage.removeItem(THEME_KEY);
    }
  }

  function _syncPaletteUI(activeId) {
    document.querySelectorAll('.theme-palette-card').forEach(function (card) {
      var id = card.getAttribute('data-theme-id');
      var isActive = (id === (activeId || ''));
      card.classList.toggle('is-active', isActive);
      card.setAttribute('aria-checked', isActive ? 'true' : 'false');
    });
  }

  var grid = document.getElementById('themePaletteGrid');
  if (grid) {
    // Init: reflect saved theme
    var savedTheme = localStorage.getItem(THEME_KEY) || '';
    _syncPaletteUI(savedTheme);

    // Click handler
    grid.addEventListener('click', function (e) {
      var card = e.target.closest('.theme-palette-card');
      if (!card) return;
      var themeId = card.getAttribute('data-theme-id');
      _applyTheme(themeId);
      _syncPaletteUI(themeId);
      showToast('主題已套用：' + (card.querySelector('.theme-palette-name').textContent || 'K WAY 標準'), 'success');
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
