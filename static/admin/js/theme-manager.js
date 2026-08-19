/* ========================================================
   Yuyi Theme Manager
   ========================================================
   主题加载、切换、CSS 变量动态注入。
   预留未来多主题扩展（夜晚模式、专注模式等）。
   ======================================================== */

const YuyiThemeManager = (function () {
  "use strict";

  const THEME_BASE_PATH = "themes/";
  const DEFAULT_THEME = "yuyi-default";
  const STORAGE_KEY = "yuyi_console_theme";

  let currentTheme = null;
  let listeners = [];

  /* ---------- 主题加载 ---------- */

  async function loadTheme(name) {
    const themeName = name || DEFAULT_THEME;
    try {
      const resp = await fetch(`${THEME_BASE_PATH}${themeName}.json`, {
        cache: "no-cache",
      });
      if (!resp.ok) throw new Error(`Theme ${themeName} not found`);
      const themeData = await resp.json();
      applyTheme(themeData);
      currentTheme = themeData;
      saveThemePreference(themeName);
      notifyListeners("load", themeData);
      return themeData;
    } catch (err) {
      console.warn("[YuyiTheme] 加载主题失败，使用默认变量:", err.message);
      return null;
    }
  }

  /* ---------- 应用主题到 CSS 变量 ---------- */

  function applyTheme(theme) {
    const root = document.documentElement;

    if (theme.background && theme.background.gradient) {
      root.style.setProperty("--bg-page", theme.background.gradient);
    }

    if (theme.glass && theme.glass.card) {
      const card = theme.glass.card;
      if (card.background) root.style.setProperty("--glass-2", card.background);
      if (card.border) root.style.setProperty("--glass-border", card.border);
      if (card.blur) root.style.setProperty("--glass-blur", `blur(${card.blur})`);
    }

    if (theme.states) {
      Object.entries(theme.states).forEach(([key, val]) => {
        if (val.color) root.style.setProperty(`--state-${key}`, val.color);
        if (val.soft) root.style.setProperty(`--state-${key}-soft`, val.soft);
        if (val.glow) root.style.setProperty(`--state-${key}-glow`, val.glow);
      });
    }

    if (theme.text) {
      if (theme.text.primary) root.style.setProperty("--text-primary", theme.text.primary);
      if (theme.text.secondary) root.style.setProperty("--text-secondary", theme.text.secondary);
      if (theme.text.muted) root.style.setProperty("--text-muted", theme.text.muted);
    }
  }

  /* ---------- 情绪 → 主题覆盖 ---------- */

  function applyMoodOverride(mood) {
    if (!currentTheme || !currentTheme.character) return;
    const override = currentTheme.character.moodOverrides[mood];
    if (!override) return;

    const scene = document.querySelector(".yuyi-scene");
    if (scene) {
      if (override.hue !== undefined) {
        scene.style.filter = `hue-rotate(${override.hue}deg)`;
      }
    }
  }

  /* ---------- 偏好持久化 ---------- */

  function saveThemePreference(name) {
    try {
      localStorage.setItem(STORAGE_KEY, name);
    } catch (e) { /* ignore */ }
  }

  function loadSavedThemeName() {
    try {
      return localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME;
    } catch (e) {
      return DEFAULT_THEME;
    }
  }

  /* ---------- 事件系统 ---------- */

  function on(event, callback) {
    listeners.push({ event, callback });
  }

  function notifyListeners(event, data) {
    listeners.forEach(l => {
      if (l.event === event) {
        try { l.callback(data); } catch (e) { console.error(e); }
      }
    });
  }

  /* ---------- 公开 API ---------- */

  function getCurrentTheme() {
    return currentTheme;
  }

  function getStateColor(stateName) {
    if (currentTheme && currentTheme.states && currentTheme.states[stateName]) {
      return currentTheme.states[stateName];
    }
    return { color: "#888", soft: "#eee", glow: "rgba(0,0,0,0.1)" };
  }

  /* ---------- 初始化 ---------- */

  async function init() {
    const savedName = loadSavedThemeName();
    await loadTheme(savedName);
    return currentTheme;
  }

  return {
    init,
    loadTheme,
    getCurrentTheme,
    getStateColor,
    applyMoodOverride,
    on,
    DEFAULT_THEME,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiThemeManager = YuyiThemeManager;
}
