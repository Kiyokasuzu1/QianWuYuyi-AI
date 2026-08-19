/* ========================================================
   Yuyi Remote Companion — 羽依远程陪伴面板
   ========================================================
   屏幕查看 · 鼠标/键盘/文本控制 · 状态指示 · 自动刷新
   接入已实现但未启用的 /admin/api/screen/* 与 /admin/api/control/*
   风格参考 yuyi-cognitive.js / yuyi-autonomous.js（IIFE 模式）
   ======================================================== */

const YuyiRemote = (function () {
  "use strict";

  const API_BASE = "/admin/api";
  const AUTO_REFRESH_INTERVAL = 30000; // 自动刷新间隔：30 秒

  let _initialized = false;        // 是否已初始化（事件绑定）
  let _autoRefreshTimer = null;    // 自动刷新定时器
  let _autoRefreshOn = false;      // 自动刷新开关
  let _actionLog = [];             // 操作记录
  let _lastStatus = null;          // 最近一次状态

  /* ============ API 封装 ============ */

  async function apiGet(path) {
    const res = await fetch(API_BASE + path);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  async function apiPost(path, data) {
    const res = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  /* ============ 初始化（事件绑定，幂等） ============ */

  function init() {
    if (_initialized) return;
    _initialized = true;

    // 刷新截图按钮
    const refreshBtn = document.getElementById("remote-refresh-btn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", () => captureScreen());
    }

    // 自动刷新开关
    const autoCheckbox = document.getElementById("remote-auto-refresh");
    if (autoCheckbox) {
      autoCheckbox.addEventListener("change", (e) => {
        toggleAutoRefresh(e.target.checked);
      });
    }

    // 鼠标点击
    const clickBtn = document.getElementById("remote-click-btn");
    if (clickBtn) {
      clickBtn.addEventListener("click", () => sendClick());
    }

    // 文本输入
    const typeBtn = document.getElementById("remote-type-btn");
    if (typeBtn) {
      typeBtn.addEventListener("click", () => sendType());
    }

    // 按键
    const keyBtn = document.getElementById("remote-key-btn");
    if (keyBtn) {
      keyBtn.addEventListener("click", () => sendKey());
    }

    // 点击截图获取坐标（轻量交互：点击图片回填坐标）
    const screenImg = document.getElementById("remote-screen-image");
    if (screenImg) {
      screenImg.addEventListener("click", (e) => {
        const rect = screenImg.getBoundingClientRect();
        const x = Math.round(e.clientX - rect.left);
        const y = Math.round(e.clientY - rect.top);
        const xInput = document.getElementById("remote-click-x");
        const yInput = document.getElementById("remote-click-y");
        if (xInput) xInput.value = x;
        if (yInput) yInput.value = y;
      });
    }
  }

  /* ============ 主加载器 ============ */

  async function load() {
    await Promise.all([loadStatus(), loadScreenContext()]);
  }

  /* ============ 状态加载 ============ */

  async function loadStatus() {
    try {
      const data = await apiGet("/control/status");
      _lastStatus = data;
      renderStatus(data);
    } catch (e) {
      console.error("[Remote] loadStatus failed:", e);
      renderStatus(null);
    }
  }

  function renderStatus(data) {
    const items = [
      { id: "remote-status-screen", ok: data ? data.screen_enabled : false, label: "屏幕模块" },
      { id: "remote-status-control", ok: data ? data.control_enabled : false, label: "控制模块" },
      { id: "remote-status-agent", ok: data ? data.agent_online : false, label: "代理在线" },
    ];
    items.forEach((item) => {
      const el = document.getElementById(item.id);
      if (!el) return;
      const dot = el.querySelector(".remote-dot");
      const val = el.querySelector(".remote-status-value");
      if (dot) {
        dot.classList.toggle("online", !!item.ok);
        dot.classList.toggle("offline", !item.ok);
      }
      if (val) {
        val.textContent = item.ok ? "在线" : "离线";
        val.style.color = item.ok ? "var(--c-success, #22c55e)" : "var(--c-text-muted, #94a3b8)";
      }
    });
  }

  /* ============ 屏幕上下文（OCR + 描述） ============ */

  async function loadScreenContext() {
    try {
      const data = await apiGet("/screen/context");
      renderScreenContext(data);
    } catch (e) {
      console.error("[Remote] loadScreenContext failed:", e);
      renderScreenContext(null);
    }
  }

  function renderScreenContext(data) {
    const ocrEl = document.getElementById("remote-ocr-text");
    if (!ocrEl) return;

    if (!data || data.available === false) {
      ocrEl.textContent = data && data.error ? data.error : "屏幕模块未启用";
      return;
    }

    const text = data.text || data.description || "";
    ocrEl.textContent = text || "暂无内容";
  }

  /* ============ 主动截图 ============ */

  async function captureScreen() {
    const wrap = document.getElementById("remote-screen-wrap");
    const placeholder = document.getElementById("remote-screen-placeholder");
    const img = document.getElementById("remote-screen-image");
    const meta = document.getElementById("remote-screen-meta");
    const ocrEl = document.getElementById("remote-ocr-text");
    const btn = document.getElementById("remote-refresh-btn");

    if (btn) btn.disabled = true;
    if (placeholder) {
      placeholder.style.display = "";
      placeholder.querySelector(".remote-placeholder-text").textContent = "羽依正在截图~";
      placeholder.querySelector(".remote-placeholder-hint").textContent = "请稍等片刻...";
    }
    if (img) img.style.display = "none";

    try {
      const data = await apiPost("/screen/capture", {});

      if (data && data.success) {
        const base64 = data.image_base64 || "";
        const format = data.format || "jpeg";
        if (base64 && img) {
          img.src = `data:image/${format};base64,${base64}`;
          img.style.display = "";
          if (placeholder) placeholder.style.display = "none";
        }
        if (meta) {
          const sizeKB = data.image_size ? Math.round(data.image_size / 1024) : "--";
          meta.textContent = `已更新 · ${data.timestamp || ""} · ${sizeKB} KB`;
        }
        if (ocrEl) {
          ocrEl.textContent = data.ocr_text || "（未识别到文字）";
        }
        if (typeof showToast === "function") showToast("屏幕已刷新", "success");
        // 截图成功后顺便刷新状态（代理在线性可能变化）
        loadStatus();
      } else {
        const err = (data && data.error) || "截图失败";
        if (placeholder) {
          placeholder.querySelector(".remote-placeholder-text").textContent = "截图失败";
          placeholder.querySelector(".remote-placeholder-hint").textContent = err;
        }
        if (typeof showToast === "function") showToast("截图失败: " + err, "error");
      }
    } catch (e) {
      console.error("[Remote] captureScreen failed:", e);
      if (placeholder) {
        placeholder.querySelector(".remote-placeholder-text").textContent = "截图失败";
        placeholder.querySelector(".remote-placeholder-hint").textContent = e.message;
      }
      if (typeof showToast === "function") showToast("截图失败: " + e.message, "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ============ 自动刷新 ============ */

  function toggleAutoRefresh(on) {
    _autoRefreshOn = on;
    if (_autoRefreshTimer) {
      clearInterval(_autoRefreshTimer);
      _autoRefreshTimer = null;
    }
    if (on) {
      // 立即刷新一次，再启动定时
      captureScreen();
      _autoRefreshTimer = setInterval(() => {
        // 仅在当前视图为 remote 时才真正请求，避免后台浪费
        if (typeof _currentView !== "undefined" && _currentView === "remote") {
          captureScreen();
        }
      }, AUTO_REFRESH_INTERVAL);
      if (typeof showToast === "function") showToast("已开启自动刷新（每 30 秒）", "info");
    } else {
      if (typeof showToast === "function") showToast("已关闭自动刷新", "info");
    }
  }

  /* ============ 鼠标点击 ============ */

  async function sendClick() {
    const xInput = document.getElementById("remote-click-x");
    const yInput = document.getElementById("remote-click-y");
    const btnSel = document.getElementById("remote-click-button");
    const x = xInput ? Number(xInput.value) : NaN;
    const y = yInput ? Number(yInput.value) : NaN;
    const button = btnSel ? btnSel.value : "left";

    if (Number.isNaN(x) || Number.isNaN(y)) {
      if (typeof showToast === "function") showToast("请输入有效的 X / Y 坐标", "error");
      return;
    }

    const btn = document.getElementById("remote-click-btn");
    if (btn) btn.disabled = true;
    try {
      const result = await apiPost("/control/click", { x, y, button });
      const ok = result && (result.success !== false);
      addLog(ok ? `点击 (${x}, ${y}) ${button}` : `点击失败: ${result.error || "未知"}`, ok);
      if (typeof showToast === "function") {
        showToast(ok ? `已点击 (${x}, ${y})` : "点击失败", ok ? "success" : "error");
      }
    } catch (e) {
      addLog(`点击失败: ${e.message}`, false);
      if (typeof showToast === "function") showToast("点击失败: " + e.message, "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ============ 文本输入 ============ */

  async function sendType() {
    const ta = document.getElementById("remote-type-text");
    const text = ta ? ta.value : "";
    if (!text) {
      if (typeof showToast === "function") showToast("请输入文字", "error");
      return;
    }

    const btn = document.getElementById("remote-type-btn");
    if (btn) btn.disabled = true;
    try {
      const result = await apiPost("/control/type", { text });
      const ok = result && (result.success !== false);
      addLog(ok ? `输入文字：${text.slice(0, 30)}${text.length > 30 ? "..." : ""}` : `输入失败: ${result.error || "未知"}`, ok);
      if (typeof showToast === "function") {
        showToast(ok ? "文字已发送" : "输入失败", ok ? "success" : "error");
      }
      if (ok && ta) ta.value = "";
    } catch (e) {
      addLog(`输入失败: ${e.message}`, false);
      if (typeof showToast === "function") showToast("输入失败: " + e.message, "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ============ 按键 ============ */

  async function sendKey() {
    const input = document.getElementById("remote-key-input");
    const key = input ? input.value.trim() : "";
    if (!key) {
      if (typeof showToast === "function") showToast("请输入按键名", "error");
      return;
    }

    const btn = document.getElementById("remote-key-btn");
    if (btn) btn.disabled = true;
    try {
      const result = await apiPost("/control/key", { key });
      const ok = result && (result.success !== false);
      addLog(ok ? `按键：${key}` : `按键失败: ${result.error || "未知"}`, ok);
      if (typeof showToast === "function") {
        showToast(ok ? `已发送按键 ${key}` : "按键失败", ok ? "success" : "error");
      }
      if (ok && input) input.value = "";
    } catch (e) {
      addLog(`按键失败: ${e.message}`, false);
      if (typeof showToast === "function") showToast("按键失败: " + e.message, "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ============ 操作记录 ============ */

  function addLog(message, ok) {
    _actionLog.unshift({
      time: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
      message,
      ok,
    });
    if (_actionLog.length > 20) _actionLog.length = 20;
    renderLog();
  }

  function renderLog() {
    const list = document.getElementById("remote-log-list");
    if (!list) return;
    if (_actionLog.length === 0) {
      list.innerHTML = '<div class="remote-log-empty">还没有操作记录哦~</div>';
      return;
    }
    list.innerHTML = _actionLog.map((entry) => `
      <div class="remote-log-entry ${entry.ok ? "ok" : "fail"}">
        <span class="remote-log-time">${entry.time}</span>
        <span class="remote-log-dot"></span>
        <span class="remote-log-msg">${escapeHtml(entry.message)}</span>
      </div>
    `).join("");
  }

  /* ============ 工具 ============ */

  function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = String(str);
    return div.innerHTML;
  }

  /* ============ 对外接口 ============ */

  return {
    init,
    load,
    loadStatus,
    loadScreenContext,
    captureScreen,
    sendClick,
    sendType,
    sendKey,
    toggleAutoRefresh,
  };
})();
