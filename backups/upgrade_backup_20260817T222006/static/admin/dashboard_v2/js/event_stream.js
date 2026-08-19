/* =============================================================
 * event_stream.js —— Step 8.4.4 Event Stream (统一生命事件流)
 *
 * 职责:
 *  - 渲染 Event Stream 页面
 *  - 顶部过滤栏:[事件类型] [关键词] [时间范围] [数量]
 *  - 主体:实时事件列表
 *  - 事件卡片:时间 / 事件类型 / summary / source
 *  - 展开:payload JSON / trace / confidence / evidence_count
 *  - 优先 WebSocket,失败 5s polling(用户无感切换)
 *
 * 数据源:
 *  GET  /api/dashboard/v2/event-stream?types=&keyword=&since=&limit=
 *  GET  /api/dashboard/v2/event-stream/types
 *  GET  /api/dashboard/v2/event-stream/health
 *  WS   /api/dashboard/v2/ws (channel=dashboard.event_stream)
 *  Fallback: 5s polling
 *
 * 风格:沿用 Yuyi 玻璃拟态 + 柔色,无第三方库
 * ============================================================= */
(function (global) {
  "use strict";

  // ---- 配置 ----
  const DEFAULT_LIMIT = 50;
  const MAX_LIMIT = 200;
  const POLL_INTERVAL_MS = 5000;
  const WS_CHANNEL = "dashboard.event_stream";

  // 事件类型(优先级视觉:按 7 域分组上色)
  const DOMAIN_COLORS = {
    memory:     { bg: "#bde0fe", text: "#2b4c7e" },
    reflection: { bg: "#cdb4db", text: "#4a3473" },
    emotion:    { bg: "#ffc8dd", text: "#7e3b58" },
    growth:     { bg: "#a0c4ff", text: "#2a4a8a" },
    goal:       { bg: "#caffbf", text: "#2e6a36" },
    desire:     { bg: "#fdffb6", text: "#7a7320" },
    initiative: { bg: "#ffd6a5", text: "#7e5320" },
    self_model: { bg: "#bdb2ff", text: "#3e3489" },
    lifecycle:  { bg: "#e2e2f0", text: "#3a3a52" },
    other:      { bg: "#e2e2f0", text: "#3a3a52" },
  };

  // ---- 模块状态 ----
  let currentTypes = "";        // 逗号分隔
  let currentKeyword = "";
  let currentSince = 0;         // epoch 秒
  let currentLimit = DEFAULT_LIMIT;
  let currentTypesOptions = []; // 已知事件类型
  let items = [];               // 内存中的事件列表
  let pollTimer = null;
  let wsHooked = false;
  let lastFetchTs = 0;
  let fallbackActive = false;
  let seenIds = Object.create(null);

  // ---- 工具 ----
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function fmtTs(iso) {
    if (!iso) return "--";
    try {
      const s = String(iso);
      return s.length >= 19 ? s.slice(5, 19).replace("T", " ") : s;
    } catch (e) { return String(iso); }
  }
  function domainOf(eventType) {
    const et = String(eventType || "");
    const m = et.match(/^integration\.([a-z_]+?)\./);
    if (!m) return "other";
    return m[1] || "other";
  }
  function colorOf(eventType) {
    const d = domainOf(eventType);
    return DOMAIN_COLORS[d] || DOMAIN_COLORS.other;
  }
  function badgeOf(eventType) {
    const d = domainOf(eventType);
    return d;
  }
  function shortType(et) {
    if (!et) return "";
    return et.replace(/^integration\./, "");
  }
  function safeJSON(s) {
    try {
      return JSON.stringify(s, null, 2);
    } catch (e) {
      try { return String(s); } catch (_) { return ""; }
    }
  }

  // ---- 入口 ----
  async function initEventStream() {
    try {
      const root = $("yuyi-event-stream-root");
      if (!root) return;
      renderShell(root);
      bindEvents();
      hookWS();
      await loadTypes();
      await load();
      startPolling();
    } catch (e) {
      console.warn("event_stream.init 异常:", e);
    }
  }

  function renderShell(root) {
    root.innerHTML = [
      '<div class="yuyi-event-stream">',
      '  <div class="yuyi-es-header">',
      '    <h3 class="yuyi-es-title">🪶 Event Stream · 生命事件流</h3>',
      '    <div class="yuyi-es-health" id="yuyi-es-health"><span class="yuyi-es-status-dot"></span><span class="yuyi-es-status-text">--</span></div>',
      '  </div>',
      '  <div class="yuyi-es-toolbar">',
      '    <div class="yuyi-es-field">',
      '      <label class="yuyi-es-label">事件类型</label>',
      '      <select id="yuyi-es-types" class="yuyi-es-select" multiple size="4"></select>',
      '      <input type="text" id="yuyi-es-types-text" class="yuyi-es-input" placeholder="memory.*,goal.created 或留空显示全部" />',
      '    </div>',
      '    <div class="yuyi-es-field">',
      '      <label class="yuyi-es-label">关键词</label>',
      '      <input type="text" id="yuyi-es-keyword" class="yuyi-es-input" placeholder="搜索 event_type / summary / topic / title" />',
      '    </div>',
      '    <div class="yuyi-es-field">',
      '      <label class="yuyi-es-label">时间范围</label>',
      '      <select id="yuyi-es-since" class="yuyi-es-select">',
      '        <option value="0">全部</option>',
      '        <option value="3600">最近 1h</option>',
      '        <option value="86400">最近 24h</option>',
      '        <option value="604800">最近 7d</option>',
      '        <option value="2592000">最近 30d</option>',
      '      </select>',
      '    </div>',
      '    <div class="yuyi-es-field">',
      '      <label class="yuyi-es-label">数量</label>',
      '      <select id="yuyi-es-limit" class="yuyi-es-select">',
      '        <option value="20">20</option>',
      '        <option value="50" selected>50</option>',
      '        <option value="100">100</option>',
      '        <option value="200">200</option>',
      '      </select>',
      '    </div>',
      '    <div class="yuyi-es-field yuyi-es-field-btn">',
      '      <button type="button" id="yuyi-es-apply" class="yuyi-es-btn">应用</button>',
      '      <button type="button" id="yuyi-es-clear" class="yuyi-es-btn yuyi-es-btn-ghost">清空</button>',
      '    </div>',
      '  </div>',
      '  <div class="yuyi-es-list" id="yuyi-es-list">',
      '    <div class="yuyi-es-empty">正在加载事件流...</div>',
      '  </div>',
      '</div>',
    ].join("");
  }

  function bindEvents() {
    const apply = $("yuyi-es-apply");
    if (apply) apply.addEventListener("click", onApply);
    const clear = $("yuyi-es-clear");
    if (clear) clear.addEventListener("click", onClear);
    const limit = $("yuyi-es-limit");
    if (limit) limit.addEventListener("change", function () {
      currentLimit = Math.max(1, Math.min(MAX_LIMIT, parseInt(this.value, 10) || DEFAULT_LIMIT));
      onApply();
    });
  }

  async function loadTypes() {
    if (!global.YuyiDashboardAPI) return;
    // 由于 api.js 中没有暴露 listTypes,我们直接用 envelope
    try {
      const resp = await fetch("/api/dashboard/v2/event-stream/types", {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      });
      if (!resp.ok) return;
      const body = await resp.json();
      const types = (body && body.data && body.data.types) || [];
      currentTypesOptions = types;
      renderTypesList();
    } catch (e) {
      // 静默
    }
  }

  function renderTypesList() {
    const sel = $("yuyi-es-types");
    if (!sel) return;
    sel.innerHTML = currentTypesOptions.map(t =>
      '<option value="' + esc(t) + '">' + esc(t) + '</option>'
    ).join("");
  }

  function onApply() {
    // 优先:多选框;其次:输入框(逗号分隔)
    const sel = $("yuyi-es-types");
    const text = $("yuyi-es-types-text");
    let types = "";
    if (sel && sel.selectedOptions && sel.selectedOptions.length > 0) {
      const arr = [];
      for (let i = 0; i < sel.selectedOptions.length; i++) {
        arr.push(sel.selectedOptions[i].value);
      }
      types = arr.join(",");
    } else if (text) {
      types = (text.value || "").trim();
    }
    currentTypes = types;
    const kw = $("yuyi-es-keyword");
    if (kw) currentKeyword = (kw.value || "").trim();
    const sinceSel = $("yuyi-es-since");
    if (sinceSel) {
      const sec = parseInt(sinceSel.value, 10) || 0;
      currentSince = sec > 0 ? Math.floor(Date.now() / 1000) - sec : 0;
    }
    items = [];
    seenIds = Object.create(null);
    load();
  }

  function onClear() {
    currentTypes = "";
    currentKeyword = "";
    currentSince = 0;
    const sel = $("yuyi-es-types");
    if (sel) { for (let i = 0; i < sel.options.length; i++) sel.options[i].selected = false; }
    const text = $("yuyi-es-types-text");
    if (text) text.value = "";
    const kw = $("yuyi-es-keyword");
    if (kw) kw.value = "";
    const since = $("yuyi-es-since");
    if (since) since.value = "0";
    items = [];
    seenIds = Object.create(null);
    load();
  }

  async function load() {
    if (!global.YuyiDashboardAPI) return;
    const params = [];
    if (currentTypes) params.push("types=" + encodeURIComponent(currentTypes));
    if (currentKeyword) params.push("keyword=" + encodeURIComponent(currentKeyword));
    if (currentSince > 0) params.push("since=" + currentSince);
    params.push("limit=" + currentLimit);
    const qs = "?" + params.join("&");
    try {
      const body = await fetch("/api/dashboard/v2/event-stream" + qs, {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      }).then(r => r.json());
      if (!body || !body.data) {
        renderError("加载失败");
        return;
      }
      const data = body.data;
      if (data.fallback && data.fallback_reason) {
        console.debug("event_stream fallback:", data.fallback_reason);
      }
      // 替换 items
      items = Array.isArray(data.items) ? data.items : [];
      // 更新已知类型
      if (Array.isArray(data.available_types)) {
        const newTypes = data.available_types.filter(t => currentTypesOptions.indexOf(t) < 0);
        if (newTypes.length > 0) {
          currentTypesOptions = currentTypesOptions.concat(newTypes);
          renderTypesList();
        }
      }
      lastFetchTs = Date.now();
      renderList();
      updateHealthBadge("ok");
    } catch (e) {
      renderError("网络错误: " + (e && e.message ? e.message : e));
      updateHealthBadge("error");
    }
  }

  // ---- Polling fallback ----
  function startPolling() {
    stopPolling();
    pollTimer = setInterval(function () {
      // WS 正常时不轮询
      if (!fallbackActive) return;
      load();
    }, POLL_INTERVAL_MS);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // ---- WebSocket 钩子 ----
  function hookWS() {
    if (wsHooked) return;
    wsHooked = true;
    const ws = global.YuyiDashboardWS;
    if (!ws) return;
    try {
      // 监听 dashboard.event_stream 频道 / 通用 events 类型
      ws.subscribe(function (payload) {
        try {
          if (!payload || typeof payload !== "object") return;
          if (payload.type === "events" && Array.isArray(payload.events)) {
            // 来自 polling 的 events
            ingestEvents(payload.events);
            return;
          }
          if (payload.type === "event" && payload.data) {
            // 来自 WebSocket 的单事件
            ingestEvents([payload.data]);
            return;
          }
          if (payload.channel === WS_CHANNEL && Array.isArray(payload.events)) {
            ingestEvents(payload.events);
          }
        } catch (e) { /* 静默 */ }
      });
      // 标记 fallback
      if (typeof ws._fallbackPoll !== "undefined") {
        fallbackActive = !!ws._fallbackPoll;
      }
      // 启动连接
      if (typeof ws.connect === "function") ws.connect();
    } catch (e) {
      fallbackActive = true;
    }
  }

  function ingestEvents(events) {
    let added = 0;
    for (let i = 0; i < events.length; i++) {
      const ev = events[i];
      if (!ev || !ev.event_id) continue;
      if (seenIds[ev.event_id]) continue;
      // 应用过滤(types / keyword / since)
      if (!passesFilter(ev)) continue;
      seenIds[ev.event_id] = 1;
      items.unshift(ev);  // 新的插到顶部
      added++;
    }
    // 限制 items 数量
    if (items.length > currentLimit) {
      items = items.slice(0, currentLimit);
    }
    if (added > 0) renderList();
  }

  function passesFilter(ev) {
    if (currentTypes) {
      const types = currentTypes.split(",").map(s => s.trim()).filter(Boolean);
      const et = String(ev.event_type || "");
      let hit = false;
      for (let i = 0; i < types.length; i++) {
        const f = types[i];
        if (!f) continue;
        if (f === et) { hit = true; break; }
        if (f.indexOf("*") >= 0) {
          const prefix = f.split("*")[0];
          if (et.indexOf(prefix) === 0) { hit = true; break; }
        }
      }
      if (!hit) return false;
    }
    if (currentKeyword) {
      const kw = currentKeyword.toLowerCase();
      const hay = [
        ev.event_type || "",
        ev.summary || "",
        (ev.payload && ev.payload.topic) || "",
        (ev.payload && ev.payload.title) || "",
        (ev.payload && ev.payload.content) || "",
        (ev.payload && ev.payload.text) || "",
      ].join(" ").toLowerCase();
      if (hay.indexOf(kw) < 0) return false;
    }
    if (currentSince > 0) {
      const ts = parseFloat(ev.timestamp_ts || ev.timestamp || 0) || 0;
      if (ts > 0 && ts < currentSince) return false;
    }
    return true;
  }

  // ---- 渲染 ----
  function renderList() {
    const root = $("yuyi-es-list");
    if (!root) return;
    if (!items || items.length === 0) {
      root.innerHTML = '<div class="yuyi-es-empty">暂无事件。等待新事件或调整过滤条件。</div>';
      return;
    }
    root.innerHTML = items.map(renderCard).join("");
    bindCardToggles();
  }

  function renderCard(ev) {
    if (!ev) return "";
    const color = colorOf(ev.event_type);
    const badge = badgeOf(ev.event_type);
    const st = shortType(ev.event_type);
    const ts = ev.timestamp_iso || ev.timestamp || "";
    const summary = esc(ev.summary || "(无 summary)");
    const eid = esc(ev.event_id || "");
    const source = esc(ev.source || "--");
    const trace = ev.trace || {};
    const conf = (ev.confidence === undefined || ev.confidence === null) ? "0" : String(ev.confidence);
    const traceable = !!ev.traceable;
    const evCount = (trace.evidence_count === undefined ? 0 : trace.evidence_count);
    const refs = (trace.source_refs || []).slice(0, 4).map(esc).join(", ");
    const payloadJSON = safeJSON(ev.payload || {});
    const refHtml = refs ? (' · 证据: ' + refs) : "";

    return [
      '<div class="yuyi-es-card" data-eid="' + eid + '">',
      '  <div class="yuyi-es-card-head">',
      '    <span class="yuyi-es-badge" style="background:' + color.bg + ';color:' + color.text + ';">' + esc(badge) + '</span>',
      '    <span class="yuyi-es-type">' + esc(st) + '</span>',
      '    <span class="yuyi-es-time">' + esc(fmtTs(ts)) + '</span>',
      '    <span class="yuyi-es-source">source: ' + esc(source) + '</span>',
      '    <span class="yuyi-es-toggle" data-action="toggle">展开 ▾</span>',
      '  </div>',
      '  <div class="yuyi-es-card-summary">' + summary + '</div>',
      '  <div class="yuyi-es-card-trace">',
      '    <span class="yuyi-es-chip">conf: ' + esc(conf) + '</span>',
      '    <span class="yuyi-es-chip">evidence: ' + esc(String(evCount)) + '</span>',
      '    <span class="yuyi-es-chip ' + (traceable ? 'yuyi-es-chip-on' : 'yuyi-es-chip-off') + '">' + (traceable ? "traceable" : "no-trace") + '</span>',
      (refs ? '<span class="yuyi-es-chip">' + esc(refs) + '</span>' : ''),
      '  </div>',
      '  <div class="yuyi-es-card-detail" data-detail hidden>',
      '    <div class="yuyi-es-card-detail-section">',
      '      <div class="yuyi-es-card-detail-title">payload</div>',
      '      <pre class="yuyi-es-card-json">' + esc(payloadJSON) + '</pre>',
      '    </div>',
      '    <div class="yuyi-es-card-detail-section">',
      '      <div class="yuyi-es-card-detail-title">事件 id</div>',
      '      <div class="yuyi-es-card-monospace">' + eid + '</div>',
      '    </div>',
      '    <div class="yuyi-es-card-detail-section">',
      '      <div class="yuyi-es-card-detail-title">跳转</div>',
      '      <button type="button" class="yuyi-es-btn yuyi-es-btn-ghost" data-action="jump-graph">LifeGraph 搜索此 id</button>',
      '      <button type="button" class="yuyi-es-btn yuyi-es-btn-ghost" data-action="jump-timeline">LifeTimeline 跳转</button>',
      '    </div>',
      '  </div>',
      '</div>',
    ].join("");
  }

  function renderError(msg) {
    const root = $("yuyi-es-list");
    if (!root) return;
    root.innerHTML = '<div class="yuyi-es-error">' + esc(msg) + '</div>';
  }

  function bindCardToggles() {
    const root = $("yuyi-es-list");
    if (!root) return;
    root.querySelectorAll("[data-action='toggle']").forEach(function (el) {
      el.addEventListener("click", function () {
        const card = el.closest(".yuyi-es-card");
        if (!card) return;
        const detail = card.querySelector("[data-detail]");
        if (!detail) return;
        const open = !detail.hasAttribute("hidden");
        if (open) {
          detail.setAttribute("hidden", "");
          el.textContent = "展开 ▾";
        } else {
          detail.removeAttribute("hidden");
          el.textContent = "收起 ▴";
        }
      });
    });
    root.querySelectorAll("[data-action='jump-graph']").forEach(function (el) {
      el.addEventListener("click", function () {
        const card = el.closest(".yuyi-es-card");
        const eid = card ? card.getAttribute("data-eid") : "";
        if (eid) {
          const url = "/admin/dashboard_v2/index.html?view=life_graph&focus=" + encodeURIComponent(eid);
          window.location.href = url;
        }
      });
    });
    root.querySelectorAll("[data-action='jump-timeline']").forEach(function (el) {
      el.addEventListener("click", function () {
        const card = el.closest(".yuyi-es-card");
        const eid = card ? card.getAttribute("data-eid") : "";
        if (eid) {
          const url = "/admin/dashboard_v2/index.html?view=life_timeline&focus=" + encodeURIComponent(eid);
          window.location.href = url;
        }
      });
    });
  }

  function updateHealthBadge(status) {
    const dot = document.querySelector("#yuyi-es-health .yuyi-es-status-dot");
    const txt = document.querySelector("#yuyi-es-health .yuyi-es-status-text");
    if (!dot || !txt) return;
    dot.className = "yuyi-es-status-dot";
    if (status === "ok") {
      dot.classList.add("yuyi-es-dot-ok");
      txt.textContent = "EventStream · " + (fallbackActive ? "polling" : "live");
    } else if (status === "error") {
      dot.classList.add("yuyi-es-dot-err");
      txt.textContent = "EventStream · error";
    } else {
      dot.classList.add("yuyi-es-dot-idle");
      txt.textContent = "EventStream · idle";
    }
  }

  global.YuyiEventStream = {
    init: initEventStream,
    refresh: load,
    setFallback: function (v) { fallbackActive = !!v; },
  };
})(window);
