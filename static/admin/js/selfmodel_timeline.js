/* ============================================
   羽依 Yuyi Console — Phase 6.5 Step 5.3
   SelfModel Evolution Timeline
   --------------------------------------------
   职责：
   - 通过 /api/admin/selfmodel/evolution_timeline 拉取数据
   - 渲染时间线（history / belief / reflection / audit）
   - 支持来源过滤、时间范围、排序、详情查看
   - 严格只读：仅 GET，不修改任何运行时 / 存储数据
   - 失败/不可用时显示 Offline 状态
   ============================================ */

(function () {
  "use strict";

  // 防止重复初始化
  if (window.YuyiSelfModelTimeline && window.YuyiSelfModelTimeline._initialized) {
    return;
  }

  // ============================================================
  // 配置
  // ============================================================
  var CONFIG = {
    endpoint: "/admin/api/admin/selfmodel/evolution_timeline",
    defaultLimit: 200,
  };

  // 支持的来源
  var SUPPORTED_SOURCES = ["belief", "history", "reflection", "audit"];

  // 排序方向
  var SORT_ASC = "asc";   // 时间正序（旧 → 新）
  var SORT_DESC = "desc"; // 时间倒序（新 → 旧，默认）

  // ============================================================
  // 状态
  // ============================================================
  var state = {
    items: [],
    filtered: [],
    sourceFilter: "all",     // "all" | "belief" | "history" | "reflection" | "audit"
    rangeFilter: "all",      // "all" | "24h" | "7d" | "30d"
    sortOrder: SORT_DESC,    // 默认最近事件优先
    searchQ: "",
    expandedId: null,        // 当前展开的 event id
    isFetching: false,
    lastError: null,
    lastFetchedAt: null,
  };

  // ============================================================
  // API 调用
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

  function safeFetchTimeline(path) {
    return fetchJson(path).then(function (data) {
      return { ok: true, data: data };
    }).catch(function (err) {
      console.warn("[SelfModelTimeline] fetch failed:", err);
      return { ok: false, error: err && err.message ? err.message : String(err) };
    });
  }

  // ============================================================
  // DOM 工具
  // ============================================================
  function $(id) { return document.getElementById(id); }

  function setHTML(id, html) {
    var el = $(id);
    if (el) el.innerHTML = html;
  }

  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtTs(ts) {
    if (!ts) return "--";
    try {
      var d = new Date(ts);
      if (isNaN(d.getTime())) return String(ts);
      var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())
        + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
    } catch (e) {
      return String(ts);
    }
  }

  function fmtRelative(ts) {
    if (!ts) return "";
    try {
      var d = new Date(ts);
      if (isNaN(d.getTime())) return "";
      var diff = Date.now() - d.getTime();
      if (diff < 0) diff = 0;
      if (diff < 60 * 1000) return Math.floor(diff / 1000) + " 秒前";
      if (diff < 3600 * 1000) return Math.floor(diff / 60000) + " 分钟前";
      if (diff < 86400 * 1000) return Math.floor(diff / 3600000) + " 小时前";
      return Math.floor(diff / 86400000) + " 天前";
    } catch (e) {
      return "";
    }
  }

  // ============================================================
  // 加载/离线/空状态
  // ============================================================
  function renderLoading(text) {
    setHTML("smtl-list", '<div class="smtl-loading">' + escapeHtml(text || "加载中") + '</div>');
  }

  function renderOffline(message) {
    setHTML("smtl-list",
      '<div class="smtl-offline">' +
        '<div class="smtl-offline-icon">✦</div>' +
        '<div class="smtl-offline-text">' + escapeHtml(message || "Evolution Timeline 不可用") + '</div>' +
        '<div class="smtl-offline-hint">Runtime 未启动 / SelfModel Bridge 未就绪</div>' +
      '</div>'
    );
  }

  function renderEmpty(message, hint) {
    setHTML("smtl-list",
      '<div class="smtl-empty">' +
        '<div class="smtl-empty-icon">⏳</div>' +
        '<div class="smtl-empty-text">' + escapeHtml(message || "暂无演化事件") + '</div>' +
        '<div class="smtl-empty-hint">' + escapeHtml(hint || "羽依尚无 belief / history / reflection / audit 数据") + '</div>' +
      '</div>'
    );
  }

  // ============================================================
  // 来源映射
  // ============================================================
  function sourceToIcon(source) {
    switch (source) {
      case "belief":     return "✦";
      case "history":    return "▸";
      case "reflection": return "♡";
      case "audit":      return "✎";
      default:           return "·";
    }
  }

  function sourceToLabel(source) {
    switch (source) {
      case "belief":     return "Belief";
      case "history":    return "History";
      case "reflection": return "Reflection";
      case "audit":      return "Audit";
      default:           return source || "Unknown";
    }
  }

  // ============================================================
  // 时间范围过滤
  // ============================================================
  function rangeToSinceTs(range) {
    if (range === "all" || !range) return null;
    var now = Date.now();
    if (range === "24h") return new Date(now - 24 * 3600 * 1000).toISOString();
    if (range === "7d")  return new Date(now - 7 * 86400 * 1000).toISOString();
    if (range === "30d") return new Date(now - 30 * 86400 * 1000).toISOString();
    return null;
  }

  // ============================================================
  // 过滤/排序
  // ============================================================
  function applyFilters() {
    var items = (state.items || []).slice();
    var since = rangeToSinceTs(state.rangeFilter);
    var q = (state.searchQ || "").toLowerCase().trim();

    items = items.filter(function (it) {
      // 来源过滤
      if (state.sourceFilter !== "all" && it.source !== state.sourceFilter) {
        return false;
      }
      // 时间范围过滤（客户端二次过滤；服务端已按 start 过滤）
      if (since && it.timestamp && it.timestamp < since) {
        return false;
      }
      // 关键词搜索
      if (q) {
        var hay = (it.summary || "") + " " +
                  (it.event_type || "") + " " +
                  ((it.metadata && (it.metadata.proposal_id || it.metadata.source_id)) || "");
        if (hay.toLowerCase().indexOf(q) < 0) {
          return false;
        }
      }
      return true;
    });

    // 排序
    items.sort(function (a, b) {
      var ta = a.timestamp || "";
      var tb = b.timestamp || "";
      if (state.sortOrder === SORT_ASC) {
        return ta < tb ? -1 : (ta > tb ? 1 : 0);
      }
      return ta < tb ? 1 : (ta > tb ? -1 : 0);
    });

    state.filtered = items;
  }

  // ============================================================
  // 渲染
  // ============================================================
  function renderStats(data) {
    var bySource = data.by_source || {};
    var total = data.total !== undefined ? data.total : (data.items || []).length;
    var html = "";
    html += renderStatCard("总事件", "value", total, "来自所有来源");
    html += renderStatCard("Belief", "value",
      bySource.belief || 0, "信念变更");
    html += renderStatCard("History", "value",
      bySource.history || 0, "演化历史");
    html += renderStatCard("Reflection", "value",
      bySource.reflection || 0, "自我反思");
    html += renderStatCard("Audit", "value",
      bySource.audit || 0, "审计记录");
    setHTML("smtl-stats", html);
  }

  function renderStatCard(label, kind, value, hint) {
    var valHtml = (kind === "value")
      ? '<div class="smtl-stat-value">' + escapeHtml(value) + '</div>'
      : '<div class="smtl-stat-value smtl-stat-value--small"></div>';
    return '<div class="smtl-stat-card">' +
      '<div class="smtl-stat-label">' + escapeHtml(label) + '</div>' +
      valHtml +
      (hint ? '<div class="smtl-stat-hint">' + escapeHtml(hint) + '</div>' : '') +
    '</div>';
  }

  function renderToolbar() {
    var sourceChips = "";
    sourceChips += '<button class="smtl-chip ' +
      (state.sourceFilter === "all" ? "smtl-chip--active" : "") +
      '" data-source="all">全部</button>';
    SUPPORTED_SOURCES.forEach(function (s) {
      sourceChips += '<button class="smtl-chip smtl-source-chip--' + s + ' ' +
        (state.sourceFilter === s ? "smtl-chip--active" : "") +
        '" data-source="' + s + '">' + sourceToLabel(s) + '</button>';
    });

    var html = "";
    html += '<div class="smtl-toolbar-group">';
    html += '<span class="smtl-toolbar-label">来源</span>';
    html += sourceChips;
    html += '</div>';

    html += '<div class="smtl-toolbar-group">';
    html += '<span class="smtl-toolbar-label">时间</span>';
    html += '<select class="smtl-select" id="smtl-range-select">';
    html += '<option value="all"' + (state.rangeFilter === "all" ? " selected" : "") + '>全部</option>';
    html += '<option value="24h"' + (state.rangeFilter === "24h" ? " selected" : "") + '>最近 24 小时</option>';
    html += '<option value="7d"' + (state.rangeFilter === "7d" ? " selected" : "") + '>最近 7 天</option>';
    html += '<option value="30d"' + (state.rangeFilter === "30d" ? " selected" : "") + '>最近 30 天</option>';
    html += '</select>';
    html += '</div>';

    html += '<div class="smtl-toolbar-group">';
    html += '<span class="smtl-toolbar-label">排序</span>';
    html += '<button class="smtl-sort-toggle" id="smtl-sort-toggle">' +
      (state.sortOrder === SORT_DESC ? "↓ 最新优先" : "↑ 时间正序") +
    '</button>';
    html += '</div>';

    html += '<div class="smtl-toolbar-group" style="flex:1;justify-content:flex-end;">';
    html += '<input type="text" class="smtl-input" id="smtl-search" placeholder="搜索 proposal_id / 描述…" />';
    html += '</div>';

    html += '<div class="smtl-toolbar-group">';
    html += '<span class="smtl-readonly-tag">READ-ONLY</span>';
    html += '</div>';

    setHTML("smtl-toolbar", html);
    bindToolbar();
  }

  function bindToolbar() {
    // 来源 chip
    var chips = document.querySelectorAll("#smtl-toolbar .smtl-chip");
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        var src = chip.dataset.source;
        if (!src) return;
        state.sourceFilter = src;
        state.expandedId = null;
        applyFilters();
        renderToolbar();
        renderList();
      });
    });

    // 时间范围
    var rangeSel = $("smtl-range-select");
    if (rangeSel) {
      rangeSel.addEventListener("change", function () {
        state.rangeFilter = rangeSel.value;
        state.expandedId = null;
        applyFilters();
        renderList();
      });
    }

    // 排序切换
    var sortBtn = $("smtl-sort-toggle");
    if (sortBtn) {
      sortBtn.addEventListener("click", function () {
        state.sortOrder = (state.sortOrder === SORT_DESC) ? SORT_ASC : SORT_DESC;
        state.expandedId = null;
        applyFilters();
        renderToolbar();
        renderList();
      });
    }

    // 搜索
    var searchInput = $("smtl-search");
    if (searchInput) {
      searchInput.addEventListener("input", debounce(function () {
        state.searchQ = searchInput.value;
        state.expandedId = null;
        applyFilters();
        renderList();
      }, 250));
    }
  }

  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments;
      var self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  function renderList() {
    var items = state.filtered || [];

    if (items.length === 0) {
      renderEmpty("暂无匹配的演化事件",
        state.searchQ
          ? "请调整筛选条件或搜索关键词"
          : "羽依尚无 belief / history / reflection / audit 数据");
      return;
    }

    var html = '<div class="smtl-list">';
    items.forEach(function (it) {
      html += renderItem(it);
    });
    html += '</div>';
    setHTML("smtl-list", html);

    bindItemClicks();
  }

  function renderItem(it) {
    var source = it.source || "history";
    var iconCls = "smtl-item-icon--history";
    if (source === "belief")     iconCls = "smtl-item-icon--belief";
    if (source === "reflection") iconCls = "smtl-item-icon--reflection";
    if (source === "audit")      iconCls = "smtl-item-icon--audit";

    var icon = sourceToIcon(source);
    var label = sourceToLabel(source);
    var evt = it.event_type || "";
    var summary = it.summary || it.id || "(无描述)";
    var ts = it.timestamp || "";
    var tsDisp = fmtRelative(ts) || fmtTs(ts) || "--";
    var fullTs = fmtTs(ts);

    var meta = it.metadata || {};
    var proposalId = meta.proposal_id || meta.source_id || "";
    var relatedBeliefs = meta.affected_beliefs || [];
    var pcrInfo = "";

    if (evt && evt.indexOf("pcr") >= 0) {
      pcrInfo = "PCR";
    } else if (source === "audit" && meta.proposal_id) {
      pcrInfo = "PCR audit";
    }

    var metaHtml = "";
    if (proposalId) {
      metaHtml += '<span><span class="smtl-item-meta-key">proposal_id:</span> ' +
        '<span class="smtl-item-meta-value">' + escapeHtml(proposalId) + '</span></span>';
    }
    if (pcrInfo) {
      metaHtml += '<span class="smtl-readonly-tag" style="font-size:9px;padding:1px 6px;">' +
        escapeHtml(pcrInfo) + '</span>';
    }
    if (relatedBeliefs && relatedBeliefs.length) {
      metaHtml += '<span><span class="smtl-item-meta-key">beliefs:</span> ' +
        '<span class="smtl-item-meta-value">' + relatedBeliefs.length + ' 个</span></span>';
    }

    var isExpanded = state.expandedId && it.id === state.expandedId;

    var html = '<div class="smtl-item" data-event-id="' + escapeHtml(it.id || "") + '">';
    html += '<div class="smtl-item-icon ' + iconCls + '">' + icon + '</div>';
    html += '<div class="smtl-item-body">';
    html += '<div class="smtl-item-row1">';
    html += '<span class="smtl-item-source">' + escapeHtml(label) + '</span>';
    if (evt) {
      html += '<span class="smtl-item-event">' + escapeHtml(evt) + '</span>';
    }
    html += '<span class="smtl-item-time" title="' + escapeHtml(fullTs) + '">' +
      escapeHtml(tsDisp) + '</span>';
    html += '</div>';
    html += '<div class="smtl-item-summary">' + escapeHtml(summary) + '</div>';
    if (metaHtml) {
      html += '<div class="smtl-item-meta">' + metaHtml + '</div>';
    }
    html += '<div class="smtl-item-expand-hint">' + (isExpanded ? "点击收起" : "点击查看详情") + '</div>';

    if (isExpanded) {
      html += renderItemDetail(it);
    }

    html += '</div>'; // body
    html += '</div>'; // item
    return html;
  }

  function renderItemDetail(it) {
    var meta = it.metadata || {};
    var html = '<div class="smtl-detail">';
    html += '<div class="smtl-detail-title">✦ 事件详情</div>';

    html += '<div class="smtl-detail-row">' +
      '<span class="smtl-detail-row-key">event_id</span>' +
      '<span class="smtl-detail-row-value">' + escapeHtml(it.id || "--") + '</span>' +
    '</div>';
    html += '<div class="smtl-detail-row">' +
      '<span class="smtl-detail-row-key">source</span>' +
      '<span class="smtl-detail-row-value">' + escapeHtml(it.source || "--") + '</span>' +
    '</div>';
    html += '<div class="smtl-detail-row">' +
      '<span class="smtl-detail-row-key">event_type</span>' +
      '<span class="smtl-detail-row-value">' + escapeHtml(it.event_type || "--") + '</span>' +
    '</div>';
    html += '<div class="smtl-detail-row">' +
      '<span class="smtl-detail-row-key">timestamp</span>' +
      '<span class="smtl-detail-row-value">' + escapeHtml(it.timestamp || "--") + '</span>' +
    '</div>';

    // 链路展示
    var proposalId = meta.proposal_id || meta.source_id || "";
    var affectedBeliefs = meta.affected_beliefs || [];
    var actor = meta.actor || "";
    var sourceType = meta.source_type || "";
    var trigger = meta.trigger_source || "";

    html += '<div class="smtl-detail-title" style="margin-top:6px;">⏳ 追溯链路</div>';
    html += '<div class="smtl-chain">';
    html += '<div class="smtl-chain-step">' +
      '<span class="smtl-chain-step-label">history</span>' +
      '<span class="smtl-chain-step-value">' +
        (it.event_type ? escapeHtml(it.event_type) : '<span class="smtl-chain-step-value--muted">无</span>') +
      '</span>' +
    '</div>';
    html += '<div class="smtl-chain-step">' +
      '<span class="smtl-chain-step-label">↓ proposal</span>' +
      '<span class="smtl-chain-step-value">' +
        (proposalId
          ? escapeHtml(proposalId)
          : '<span class="smtl-chain-step-value--muted">未发现</span>') +
      '</span>' +
    '</div>';
    html += '<div class="smtl-chain-step">' +
      '<span class="smtl-chain-step-label">↓ PCR</span>' +
      '<span class="smtl-chain-step-value">' +
        (it.event_type && it.event_type.indexOf("pcr") >= 0
          ? "applied"
          : (sourceType === "pcr_applied" || trigger
              ? escapeHtml(sourceType || trigger)
              : '<span class="smtl-chain-step-value--muted">无 PCR 信息</span>')) +
      '</span>' +
    '</div>';
    html += '<div class="smtl-chain-step">' +
      '<span class="smtl-chain-step-label">↓ belief / personality</span>' +
      '<span class="smtl-chain-step-value">' +
        (affectedBeliefs && affectedBeliefs.length
          ? affectedBeliefs.length + ' 个 belief: ' + affectedBeliefs.slice(0, 3).map(escapeHtml).join(", ") +
            (affectedBeliefs.length > 3 ? " …" : "")
          : '<span class="smtl-chain-step-value--muted">无直接 belief 变更</span>') +
      '</span>' +
    '</div>';
    html += '</div>';

    // 元数据
    if (meta && Object.keys(meta).length) {
      html += '<div class="smtl-detail-title" style="margin-top:6px;">▸ Metadata</div>';
      html += '<div class="smtl-detail-metadata">' + escapeHtml(JSON.stringify(meta, null, 2)) + '</div>';
    }

    html += '<button class="smtl-detail-close" data-close="1">收起详情</button>';
    html += '</div>';
    return html;
  }

  function bindItemClicks() {
    var list = $("smtl-list");
    if (!list) return;
    list.addEventListener("click", function (e) {
      // 关闭按钮
      if (e.target && e.target.dataset && e.target.dataset.close === "1") {
        e.stopPropagation();
        state.expandedId = null;
        renderList();
        return;
      }
      var item = e.target.closest && e.target.closest(".smtl-item");
      if (!item) return;
      var eid = item.dataset.eventId;
      if (!eid) return;
      if (state.expandedId === eid) {
        state.expandedId = null;
      } else {
        state.expandedId = eid;
      }
      renderList();
    });
  }

  // ============================================================
  // 加载入口
  // ============================================================
  function load(force) {
    if (state.isFetching && !force) return;
    state.isFetching = true;

    renderLoading("正在加载演化时间线");

    // 计算服务端时间范围
    var qs = [];
    qs.push("limit=" + CONFIG.defaultLimit);
    var since = rangeToSinceTs(state.rangeFilter);
    if (since) {
      qs.push("start=" + encodeURIComponent(since));
    }
    // 来源过滤（多选支持）
    if (state.sourceFilter !== "all") {
      qs.push("sources=" + encodeURIComponent(state.sourceFilter));
    }
    var path = CONFIG.endpoint + "?" + qs.join("&");

    safeFetchTimeline(path).then(function (r) {
      state.isFetching = false;
      if (!r.ok || !r.data || r.data.ok === false) {
        var msg = (r.data && r.data.error) || r.error || "未知错误";
        renderOffline("Evolution Timeline 加载失败: " + msg);
        return;
      }
      var data = r.data;
      state.lastFetchedAt = new Date();
      state.lastError = null;
      state.items = data.items || [];
      applyFilters();
      renderStats(data);
      renderToolbar();
      if (state.items.length === 0) {
        renderEmpty("暂无演化事件", "羽依尚无 belief / history / reflection / audit 数据");
      } else {
        renderList();
      }
    });
  }

  // ============================================================
  // 暴露
  // ============================================================
  function init() {
    if (window.YuyiSelfModelTimeline && window.YuyiSelfModelTimeline._initialized) return;
    window.YuyiSelfModelTimeline = {
      _initialized: true,
      load: load,
      _state: state,
      _config: CONFIG,
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
