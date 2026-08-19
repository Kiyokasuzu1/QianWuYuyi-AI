/* ============================================
   羽依 Yuyi Console — Phase 5.2 Runtime Dashboard
   --------------------------------------------
   职责：
   - 通过 Phase 5.1 RuntimeProvider API 拉取 RuntimeCore 状态
   - 渲染 Runtime Status / Authority / Personality / Emotion / Memory 卡片
   - 提供自动刷新与手动刷新
   - API 失败时显示 "Runtime Offline" 而非抛出错误
   - 严格只读：不创建任何 Runtime 实例
   ============================================ */

(function () {
  "use strict";

  // 防止重复初始化
  if (window.YuyiRuntimeDashboard && window.YuyiRuntimeDashboard._initialized) {
    return;
  }

  // ============================================================
  // 配置
  // ============================================================
  var CONFIG = {
    refreshIntervalMs: 10000,        // 默认 10 秒自动刷新
    endpoints: {
      authority:    "/admin/api/admin/authority/status",
      personality:  "/admin/api/admin/personality/status",
      emotion:      "/admin/api/admin/emotion/status",
      growth:       "/admin/api/admin/growth/status",
      memory:       "/admin/api/admin/memory/summary",
    },
  };

  // Authority 组件 → 中文标签映射
  var AUTHORITY_LABELS = {
    memory_store:         "MemoryStore",
    vector_memory:        "VectorMemory",
    emotion_manager:      "EmotionManager",
    personality_resolver: "Personality",
    self_model_store:     "SelfModelStore",
    growth_state:         "GrowthState",
  };

  // 状态值
  var lastError = null;
  var lastFetchedAt = null;
  var refreshTimer = null;
  var isFetching = false;

  // ============================================================
  // API 调用封装（仅 GET，失败不抛错）
  // ============================================================
  function fetchJson(path) {
    // 修复：去掉重复的 /admin/api 前缀，避免拼接出 /admin/api/admin/api/...
    return fetch(path, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    }).then(function (res) {
      if (!res.ok) {
        throw new Error("HTTP " + res.status);
      }
      return res.json();
    });
  }

  function safeFetch(label, path) {
    return fetchJson(path).then(function (data) {
      return { ok: true, label: label, data: data };
    }).catch(function (err) {
      console.warn("[RuntimeDashboard] " + label + " fetch failed:", err);
      return { ok: false, label: label, error: err && err.message ? err.message : String(err) };
    });
  }

  // ============================================================
  // DOM 工具
  // ============================================================
  function $(id) { return document.getElementById(id); }

  function setText(id, text) {
    var el = $(id);
    if (el) el.textContent = text;
  }

  function setHTML(id, html) {
    var el = $(id);
    if (el) el.innerHTML = html;
  }

  function setAttr(id, attr, value) {
    var el = $(id);
    if (el) el.setAttribute(attr, value);
  }

  function fmtPercent(v) {
    if (v === null || v === undefined) return "--";
    var n = Number(v);
    if (isNaN(n)) return "--";
    return Math.round(n * 100) + "%";
  }

  function fmtNumber(v) {
    if (v === null || v === undefined) return "--";
    var n = Number(v);
    if (isNaN(n)) return "--";
    return String(n);
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  // ============================================================
  // 渲染：Runtime 状态总览
  // ============================================================
  function renderRuntimeStatus(data) {
    var statusEl = $("rd-runtime-status");
    if (!statusEl) return;

    if (!data || !data.ok) {
      setText("rd-runtime-status-text", "OFFLINE");
      setAttr("rd-runtime-status-text", "class", "rd-status-pill rd-status-pill--error");
      setText("rd-runtime-initialized", "--");
      setText("rd-runtime-running", "--");
      return;
    }

    var body = data.data || {};
    var online = body.online === true;
    var runtime = body.runtime || {};

    setText("rd-runtime-status-text", online ? "ONLINE" : "OFFLINE");
    setAttr(
      "rd-runtime-status-text",
      "class",
      "rd-status-pill " + (online ? "rd-status-pill--ok" : "rd-status-pill--error")
    );

    setText("rd-runtime-initialized", runtime.initialized ? "是" : "否");
    setText("rd-runtime-running", runtime.is_running ? "运行中" : "未运行");
  }

  // ============================================================
  // 渲染：Authority 状态
  // ============================================================
  function renderAuthority(data) {
    var container = $("rd-authority-grid");
    if (!container) return;

    if (!data || !data.ok) {
      container.innerHTML = '<div class="rd-offline-note">Authority 不可用</div>';
      return;
    }

    var body = data.data || {};
    var authority = body.authority || {};

    var items = Object.keys(AUTHORITY_LABELS).map(function (key) {
      var ready = authority[key] === true;
      var label = AUTHORITY_LABELS[key];
      var cls = ready
        ? "rd-auth-pill rd-auth-pill--ok"
        : "rd-auth-pill rd-auth-pill--missing";
      var mark = ready ? "✓" : "✗";
      return (
        '<div class="rd-auth-item">' +
        '<span class="' + cls + '">' + mark + ' ' + escapeHtml(label) + '</span>' +
        '</div>'
      );
    });

    container.innerHTML = items.join("");
  }

  // ============================================================
  // 渲染：Personality 面板
  // ============================================================
  function renderPersonality(data) {
    var container = $("rd-personality-content");
    if (!container) return;

    if (!data || !data.ok) {
      container.innerHTML = '<div class="rd-offline-note">Personality 不可用</div>';
      return;
    }

    var body = data.data || {};
    if (body.available === false) {
      container.innerHTML = '<div class="rd-offline-note">Personality Resolver 未就绪</div>';
      return;
    }

    var current = body.current || {};
    var state = body.state || {};
    var selfModel = body.self_model || {};

    // 当前人格名称
    var personaName = current.name || current.persona || current.label || "未命名人格";
    var traitCount = 0;
    if (current.traits && typeof current.traits === "object") {
      traitCount = Object.keys(current.traits).length;
    } else if (current.personality && typeof current.personality === "object") {
      traitCount = Object.keys(current.personality).length;
    } else if (typeof current.trait_count === "number") {
      traitCount = current.trait_count;
    }

    // Resolver 状态
    var resolverState = "就绪";
    if (current.status) {
      resolverState = String(current.status);
    } else if (body.available) {
      resolverState = "Active";
    }

    // Growth 关联
    var growthLinked = "";
    if (selfModel && selfModel.linked_to_growth) {
      growthLinked = "已关联";
    } else if (state && Object.keys(state).length > 0) {
      growthLinked = "已绑定";
    } else {
      growthLinked = "未关联";
    }

    container.innerHTML =
      '<div class="rd-personality-grid">' +
        '<div class="rd-personality-cell">' +
          '<div class="rd-cell-label">当前人格</div>' +
          '<div class="rd-cell-value">' + escapeHtml(personaName) + '</div>' +
        '</div>' +
        '<div class="rd-personality-cell">' +
          '<div class="rd-cell-label">Resolver 状态</div>' +
          '<div class="rd-cell-value">' + escapeHtml(resolverState) + '</div>' +
        '</div>' +
        '<div class="rd-personality-cell">' +
          '<div class="rd-cell-label">Trait 数量</div>' +
          '<div class="rd-cell-value">' + fmtNumber(traitCount) + '</div>' +
        '</div>' +
        '<div class="rd-personality-cell">' +
          '<div class="rd-cell-label">Growth 关联</div>' +
          '<div class="rd-cell-value">' + escapeHtml(growthLinked) + '</div>' +
        '</div>' +
      '</div>';
  }

  // ============================================================
  // 渲染：Growth 指标
  // ============================================================
  function renderGrowth(growthData) {
    var container = $("rd-growth-metrics");
    if (!container) return;

    if (!growthData || !growthData.ok) {
      container.innerHTML = '<div class="rd-offline-note">Growth 不可用</div>';
      return;
    }

    var body = growthData.data || {};
    if (body.available === false) {
      container.innerHTML = '<div class="rd-offline-note">GrowthState 未就绪</div>';
      return;
    }

    var metrics = body.metrics || {};

    var fields = [
      { key: "total_growth",   label: "total_growth",   isPercent: true },
      { key: "maturity",       label: "maturity",       isPercent: true },
      { key: "self_awareness", label: "self_awareness", isPercent: true },
      { key: "empathy",        label: "empathy",        isPercent: true },
      { key: "stability",      label: "stability",      isPercent: true },
    ];

    var rows = fields.map(function (f) {
      var raw = metrics[f.key];
      var display = "--";
      var colorCls = "rd-metric--unknown";

      if (raw !== null && raw !== undefined) {
        var n = Number(raw);
        if (!isNaN(n)) {
          if (f.isPercent) {
            // 兼容 0-1 与 0-100 两种格式
            if (n >= 0 && n <= 1) {
              display = Math.round(n * 100) + "%";
            } else {
              display = Math.round(n) + "%";
            }
            if (n >= 0.7) colorCls = "rd-metric--ok";
            else if (n >= 0.4) colorCls = "rd-metric--warn";
            else colorCls = "rd-metric--bad";
          } else {
            display = String(n);
          }
        }
      }

      return (
        '<div class="rd-metric ' + colorCls + '">' +
          '<div class="rd-metric-label">' + escapeHtml(f.label) + '</div>' +
          '<div class="rd-metric-value">' + escapeHtml(display) + '</div>' +
        '</div>'
      );
    });

    container.innerHTML = rows.join("");
  }

  // ============================================================
  // 渲染：Emotion 面板
  // ============================================================
  function renderEmotion(data) {
    var container = $("rd-emotion-content");
    if (!container) return;

    if (!data || !data.ok) {
      container.innerHTML = '<div class="rd-offline-note">Emotion 不可用</div>';
      return;
    }

    var body = data.data || {};
    if (body.available === false) {
      container.innerHTML = '<div class="rd-offline-note">EmotionManager 未就绪</div>';
      return;
    }

    var current = body.current || "--";
    var intensity = Number(body.intensity || 0);
    if (isNaN(intensity)) intensity = 0;
    var recent = Array.isArray(body.recent) ? body.recent : [];

    var intensityPct = Math.round(Math.max(0, Math.min(1, intensity)) * 100);
    var intensityColor =
      intensityPct >= 70 ? "#f472b6" :
      intensityPct >= 40 ? "#a78bfa" :
      "#cbd5e1";

    var recentHtml = recent.length === 0
      ? '<div class="rd-recent-empty">暂无最近变化</div>'
      : recent.slice(0, 5).map(function (r) {
          var ts = r.timestamp || r.time || r.created_at || "";
          var emotion = r.emotion || r.dominant || r.current || r.label || "";
          var intensity2 = r.intensity;
          var line = escapeHtml(ts) + " · " + escapeHtml(emotion);
          if (intensity2 !== undefined && intensity2 !== null) {
            line += " (" + Math.round(Number(intensity2) * 100) + "%)";
          }
          return '<div class="rd-recent-item">' + line + '</div>';
        }).join("");

    container.innerHTML =
      '<div class="rd-emotion-grid">' +
        '<div class="rd-emotion-cell">' +
          '<div class="rd-cell-label">当前情绪</div>' +
          '<div class="rd-emotion-value">' + escapeHtml(current) + '</div>' +
        '</div>' +
        '<div class="rd-emotion-cell">' +
          '<div class="rd-cell-label">强度</div>' +
          '<div class="rd-emotion-bar-wrap">' +
            '<div class="rd-emotion-bar" style="width:' + intensityPct + '%;background:' + intensityColor + ';"></div>' +
          '</div>' +
          '<div class="rd-cell-sub">' + intensityPct + '%</div>' +
        '</div>' +
      '</div>' +
      '<div class="rd-recent-list">' +
        '<div class="rd-recent-title">最近变化</div>' +
        recentHtml +
      '</div>';
  }

  // ============================================================
  // 渲染：Memory 概览
  // ============================================================
  function renderMemory(data) {
    var container = $("rd-memory-content");
    if (!container) return;

    if (!data || !data.ok) {
      container.innerHTML = '<div class="rd-offline-note">Memory 不可用</div>';
      return;
    }

    var body = data.data || {};
    if (body.available === false) {
      container.innerHTML = '<div class="rd-offline-note">MemoryStore 未就绪</div>';
      return;
    }

    var total = body.total_count || 0;
    var important = body.important_count || 0;
    var userId = body.user_id || "--";
    var recent = Array.isArray(body.recent) ? body.recent : [];

    var recentHtml = recent.length === 0
      ? '<div class="rd-recent-empty">暂无最近记忆</div>'
      : recent.slice(0, 5).map(function (m) {
          var content = m.content || m.text || m.summary || JSON.stringify(m);
          if (typeof content === "string" && content.length > 60) {
            content = content.substring(0, 60) + "...";
          }
          var imp = m.importance;
          var impLabel = "";
          if (imp !== undefined && imp !== null) {
            var inum = Number(imp);
            if (!isNaN(inum)) {
              impLabel = " · 重要度 " + (inum >= 0 && inum <= 1 ? Math.round(inum * 100) + "%" : inum);
            }
          }
          return '<div class="rd-memory-item">' + escapeHtml(content) + impLabel + '</div>';
        }).join("");

    container.innerHTML =
      '<div class="rd-memory-grid">' +
        '<div class="rd-memory-cell">' +
          '<div class="rd-cell-label">总记忆数</div>' +
          '<div class="rd-memory-value">' + fmtNumber(total) + '</div>' +
        '</div>' +
        '<div class="rd-memory-cell">' +
          '<div class="rd-cell-label">重要记忆</div>' +
          '<div class="rd-memory-value">' + fmtNumber(important) + '</div>' +
        '</div>' +
        '<div class="rd-memory-cell">' +
          '<div class="rd-cell-label">当前 user_id</div>' +
          '<div class="rd-memory-value rd-memory-value--small">' + escapeHtml(userId) + '</div>' +
        '</div>' +
      '</div>' +
      '<div class="rd-recent-list">' +
        '<div class="rd-recent-title">最近 5 条</div>' +
        recentHtml +
      '</div>';
  }

  // ============================================================
  // 全局 Offline 状态指示
  // ============================================================
  function renderGlobalOffline() {
    setText("rd-runtime-status-text", "OFFLINE");
    setAttr("rd-runtime-status-text", "class", "rd-status-pill rd-status-pill--error");
    setText("rd-runtime-initialized", "--");
    setText("rd-runtime-running", "--");

    setHTML("rd-authority-grid", '<div class="rd-offline-note">Runtime Offline</div>');
    setHTML("rd-personality-content", '<div class="rd-offline-note">Runtime Offline</div>');
    setHTML("rd-growth-metrics", '<div class="rd-offline-note">Runtime Offline</div>');
    setHTML("rd-emotion-content", '<div class="rd-offline-note">Runtime Offline</div>');
    setHTML("rd-memory-content", '<div class="rd-offline-note">Runtime Offline</div>');

    setText("rd-last-updated", "Runtime Offline");
  }

  // ============================================================
  // 主刷新流程（并行请求所有 Phase 5.1 API）
  // ============================================================
  function refreshAll() {
    if (isFetching) return;
    isFetching = true;

    var p1 = safeFetch("authority",   CONFIG.endpoints.authority);
    var p2 = safeFetch("personality", CONFIG.endpoints.personality);
    var p3 = safeFetch("emotion",     CONFIG.endpoints.emotion);
    var p4 = safeFetch("growth",      CONFIG.endpoints.growth);
    var p5 = safeFetch("memory",      CONFIG.endpoints.memory);

    Promise.all([p1, p2, p3, p4, p5]).then(function (results) {
      isFetching = false;

      var authority   = results[0];
      var personality = results[1];
      var emotion     = results[2];
      var growth      = results[3];
      var memory      = results[4];

      // 至少有一个 OK 才算在线
      var anyOk = results.some(function (r) { return r.ok; });

      if (!anyOk) {
        lastError = "All endpoints failed";
        renderGlobalOffline();
        return;
      }

      // Runtime Status 派生：authority.online 优先
      if (authority && authority.ok && authority.data) {
        renderRuntimeStatus(authority);
      } else {
        setText("rd-runtime-status-text", "OFFLINE");
        setAttr("rd-runtime-status-text", "class", "rd-status-pill rd-status-pill--error");
        setText("rd-runtime-initialized", "--");
        setText("rd-runtime-running", "--");
      }

      renderAuthority(authority);
      renderPersonality(personality);
      renderGrowth(growth);
      renderEmotion(emotion);
      renderMemory(memory);

      lastError = null;
      lastFetchedAt = new Date();
      setText("rd-last-updated", "更新于 " + lastFetchedAt.toLocaleTimeString());
    });
  }

  // ============================================================
  // 自动刷新
  // ============================================================
  function startAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
    refreshTimer = setInterval(refreshAll, CONFIG.refreshIntervalMs);
  }

  function stopAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  // ============================================================
  // 公开接口
  // ============================================================
  var YuyiRuntimeDashboard = {
    _initialized: true,
    refresh: refreshAll,
    start: startAutoRefresh,
    stop: stopAutoRefresh,
    config: CONFIG,
    isOnline: function () { return lastError === null && lastFetchedAt !== null; },
    getLastFetchedAt: function () { return lastFetchedAt; },
  };

  // ============================================================
  // DOMContentLoaded 初始化
  // ============================================================
  function init() {
    // 绑定手动刷新按钮
    var btn = $("rd-refresh-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        refreshAll();
      });
    }

    // 立即拉一次，然后开启自动刷新
    refreshAll();
    startAutoRefresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    // 脚本晚于 DOMContentLoaded 加载时直接初始化
    init();
  }

  // 暴露到全局
  window.YuyiRuntimeDashboard = YuyiRuntimeDashboard;
})();
