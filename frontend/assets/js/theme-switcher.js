/**
 * theme-switcher.js
 * MCP Agent Console — UIUXPRO 六色主題切換器
 *
 * 用法：
 *   ThemeSwitcher.set('pearl')      // 直接切換
 *   ThemeSwitcher.get()             // 取得目前主題 id
 *   ThemeSwitcher.themes            // 所有主題定義
 *   ThemeSwitcher.init()            // 頁面初始化（自動呼叫）
 *
 * 主題儲存於 localStorage key: 'kway_theme'
 */

const ThemeSwitcher = (() => {
  const STORAGE_KEY = 'kway_theme';
  const DEFAULT_THEME = null; // null = 使用 :root 預設（K WAY Blue）

  /** 八套主題定義（由淺到深）*/
  const themes = [
    {
      id:    'pearl',
      label: '珍珠晨光',
      desc:  '暖奶白 × 珊瑚橙 × 薄荷青',
      dark:  false,
      swatch: { bg: '#FAF8F5', primary: '#E06B52', secondary: '#4ECDC4' },
    },
    {
      id:    'sand',
      label: '暖沙午後',
      desc:  '沙棕暖調 × 赤陶橘 × 鼠尾草綠',
      dark:  false,
      swatch: { bg: '#F5EDE0', primary: '#C97B4B', secondary: '#5BAA9F' },
    },
    {
      id:    'mint',
      label: '薄荷清風',
      desc:  '薄荷冰綠 × 翡翠青 × 暖橙紅',
      dark:  false,
      swatch: { bg: '#EEF7F6', primary: '#2A9D8F', secondary: '#E76F51' },
    },
    {
      id:    'lavender',
      label: '薰衣草霧',
      desc:  '淡紫灰白 × 紫羅蘭 × 活力橙',
      dark:  false,
      swatch: { bg: '#F0EDF8', primary: '#7C5CBF', secondary: '#FF7043' },
    },
    {
      id:    'twilight',
      label: '暮色靛藍',
      desc:  '深靛紫夜 × 電光紫 × 珊瑚橙',
      dark:  true,
      swatch: { bg: '#1E1B3A', primary: '#7B6EFF', secondary: '#FF7F5C' },
    },
    {
      id:    'midnight',
      label: '子夜深邃',
      desc:  '純粹黑夜 × 深空紫 × 電光青',
      dark:  true,
      swatch: { bg: '#0D0D1A', primary: '#6B4EFF', secondary: '#4ECDC4' },
    },
    {
      id:    'ocean',
      label: '晴湖青韻',
      desc:  '淺灰藍底 × 青湖藍 × 琥珀橙',
      dark:  false,
      swatch: { bg: '#F2F4F7', primary: '#1a9aaa', secondary: '#f5a623' },
    },
    {
      id:    'slate',
      label: '石板青苔',
      desc:  '霧灰白底 × 鼠尾草綠 × 靛藍 × 深石板側欄',
      dark:  false,
      swatch: { bg: '#F0F1F3', primary: '#72b2a0', secondary: '#5b5db8' },
    },
  ];

  /** 套用主題到 <html> */
  function set(themeId) {
    const root = document.documentElement;
    if (themeId) {
      root.setAttribute('data-theme', themeId);
      localStorage.setItem(STORAGE_KEY, themeId);
    } else {
      root.removeAttribute('data-theme');
      localStorage.removeItem(STORAGE_KEY);
    }
    // 發出自訂事件，讓其他模組響應
    window.dispatchEvent(new CustomEvent('kway:theme-change', { detail: { theme: themeId } }));
  }

  /** 取得目前主題 id（null = 預設） */
  function get() {
    return document.documentElement.getAttribute('data-theme') || null;
  }

  /** 初始化：從 localStorage 讀取並套用 */
  function init() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) set(saved);
  }

  // 自動初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return { themes, set, get, init };
})();
