/* ============================================
   羽依 Yuyi Console — Phase 6.5 SelfModel Dashboard
   --------------------------------------------
   职责：
   - 通过 /api/admin/selfmodel/* API 拉取 SelfModel 视图
   - 渲染 Overview / Identity / Beliefs / Why 四个子视图
   - 严格只读：仅 GET 与 POST /retention/dry_run
   - 失败/不可用时显示 Offline 状态而非抛错
   ============================================ */

(function () {
  "use strict";

  // 防止重复初始化
  if (window.YuyiSelfModelDashboard && window.YuyiSelfModelDashboard._initialized) {
    return;
  }

  // ============================================================
  // 配置
  // ============================================================
  var CONFIG = {
    endpoints: {
      status:        "/admin/api/admin/selfmodel/status",
      identity:      "/admin/api/admin/selfmodel/identity",
      beliefs:       "/admin/api/admin/selfmodel/beliefs",
      beliefWhy:     "/admin/api/admin/selfmodel/belief",        // /<id>/why
      history:       "/admin/api/admin/selfmodel/history",
      reflections:   "/admin/api/admin/selfmodel/reflections",
      timeline:      "/admin/api/admin/selfmodel/evolution_timeline",
      pcrRelated:    "/admin/api/admin/selfmodel/pcr",            // /<id>/related
      health:        "/admin/api/admin/selfmodel/health",
      retention:     "/admin/api/admin/selfmodel/retention",
      retentionDry:  "/admin/api/admin/selfmodel/retention/dry_run",
    },
    tabs: ["overview", "identity", "beliefs"],
    defaultTab: "overview",
  };

  // ============================================================
  // 状态
  // ============================================================
  var state = {
    activeTab: CONFIG.defaultTab,
    beliefsCache: null,
    beliefsCacheAt: 0,
    currentWhyBeliefId: null,
    lastError: null,
    lastFetchedAt: null,
    isFetching: false,
  };

  var BELIEF_CACHE_TTL_MS = 30 * 1000;

  // ============================================================
  // API 调用
  // ============================================================
  function fetchJson(path, opts) {
    opts = opts || {};
    // 修复：去掉重复的 /admin/api 前缀，避免拼接出 /admin/api/admin/api/...
    return fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    }).then(function (res) {
      if (!res.ok) {
        throw new Error("HTTP " + res.status);
      }
      return res.json();
    });
  }

  function safeFetch(label, path, opts) {
    return fetchJson(path, opts).then(function (data) {
      return { ok: true, label: label, data: data };
    }).catch(function (err) {
      console.warn("[SelfModelDashboard] " + label + " failed:", err);
      return { ok: false, label: label, error: err && err.message ? err.message : String(err) };
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

  function fmtPercent(v) {
    if (v === null || v === undefined) return "--";
    var n = Number(v);
    if (isNaN(n)) return "--";
    return Math.round(n * 100) + "%";
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
  // Offline / Loading 通用渲染
  // ============================================================
  function renderOffline(containerId, message) {
    setHTML(containerId,
      '<div class="sm-offline">' +
        '<div class="sm-offline-icon">✦</div>' +
        '<div class="sm-offline-text">' + escapeHtml(message || "SelfModel 不可用") + '</div>' +
        '<div class="sm-offline-hint">Runtime 未启动 / SelfModelBootstrap 未就绪</div>' +
      '</div>'
    );
  }

  function renderLoading(containerId, text) {
    setHTML(containerId, '<div class="sm-loading">' + escapeHtml(text || "加载中") + '</div>');
  }

  // ============================================================
  // 状态指示（health / available）
  // ============================================================
  function statusToPillClass(status) {
    if (!status) return "sm-status-pill--unknown";
    var s = String(status).toLowerCase();
    if (s === "healthy" || s === "ok" || s === "available" || s === "good") return "sm-status-pill--ok";
    if (s === "warning" || s === "warn" || s === "degraded") return "sm-status-pill--warn";
    if (s === "critical" || s === "error" || s === "bad" || s === "unavailable") return "sm-status-pill--bad";
    return "sm-status-pill--unknown";
  }

  // ============================================================
  // Tabs 切换
  // ============================================================
  function activateTab(tab) {
    if (CONFIG.tabs.indexOf(tab) < 0) tab = CONFIG.defaultTab;
    state.activeTab = tab;

    document.querySelectorAll(".sm-tab").forEach(function (el) {
      el.classList.toggle("sm-tab--active", el.dataset.tab === tab);
    });
    document.querySelectorAll(".sm-subview").forEach(function (el) {
      el.style.display = el.id === "sm-view-" + tab ? "" : "none";
    });
  }

  // ============================================================
  // Overview
  // ============================================================
  function renderOverview(status, identity, health, retention, timeline) {
    var bridgeOk = status && status.ok !== false && status.available !== false;
    if (!bridgeOk) {
      renderOffline("sm-view-overview", "SelfModel 视图未就绪");
      return;
    }

    var bs = (status && status.bootstrap) || {};
    var ids = (identity && identity.data) || identity || {};
    var hr = (health && health.report) || {};
    var hc = (health && health.summary && health.summary.counts) || {};
    var rt = (retention && retention.current_counts) || {};
    var rtThr = (retention && retention.thresholds) || {};
    var recent = (timeline && timeline.items) || [];

    // Health 状态色
    var healthClass = statusToPillClass(hr.overall_status);
    var healthText = hr.overall_status ? hr.overall_status : "unknown";

    var html = "";
    // Bootstrap 小卡
    html += '<div class="sm-stat-grid">';
    html += renderStatCard("Beliefs", "value", (hc.beliefs !== undefined ? hc.beliefs : "--"),
      "信念总数", "");
    html += renderStatCard("History", "value", (hc.history !== undefined ? hc.history : "--"),
      "演化历史", "");
    html += renderStatCard("Reflections", "value", (hc.reflections !== undefined ? hc.reflections : "--"),
      "自我反思", "");
    html += renderStatCard("Active", "value", (rt.beliefs_active !== undefined ? rt.beliefs_active : "--"),
      "活跃信念", "");
    html += "</div>";

    // 健康 + Retention
    html += '<div class="sm-stat-grid" style="margin-top:14px;">';
    html += '<div class="sm-stat-card">' +
      '<div class="sm-stat-label">SelfModel 健康度</div>' +
      '<div class="sm-stat-value sm-stat-value--small"><span class="sm-status-pill ' + healthClass + '"><span class="sm-status-dot"></span>' + escapeHtml(healthText) + '</span></div>' +
      '<div class="sm-stat-hint">问题数：' + ((hr.issues && hr.issues.length) || 0) + '</div>' +
    '</div>';
    html += '<div class="sm-stat-card">' +
      '<div class="sm-stat-label">Persistence 状态</div>' +
      '<div class="sm-stat-value sm-stat-value--small">' +
        (bs.persistence_attached === true
          ? '<span class="sm-status-pill sm-status-pill--ok"><span class="sm-status-dot"></span>已挂载</span>'
          : '<span class="sm-status-pill sm-status-pill--bad"><span class="sm-status-dot"></span>未挂载</span>') +
      '</div>' +
      '<div class="sm-stat-hint">data_dir: ' + escapeHtml(bs.data_dir || "--") + '</div>' +
    '</div>';
    html += '<div class="sm-stat-card">' +
      '<div class="sm-stat-label">Bootstrap 加载</div>' +
      '<div class="sm-stat-value sm-stat-value--small">' +
        (bs.last_load_counts && (bs.last_load_counts.beliefs !== undefined)
          ? 'beliefs ' + bs.last_load_counts.beliefs
          : '--') +
      '</div>' +
      '<div class="sm-stat-hint">history ' + ((bs.last_load_counts && bs.last_load_counts.history) || 0) +
        ' · reflection ' + ((bs.last_load_counts && bs.last_load_counts.reflections) || 0) +
      '</div>' +
    '</div>';
    html += '<div class="sm-stat-card">' +
      '<div class="sm-stat-label">Retention 配额</div>' +
      '<div class="sm-stat-value sm-stat-value--small">' +
        ((rt.beliefs_total !== undefined && rtThr.max_active_beliefs)
          ? rt.beliefs_active + ' / ' + rtThr.max_active_beliefs
          : '--') +
      '</div>' +
      '<div class="sm-stat-hint">活跃 / 上限</div>' +
    '</div>';
    html += "</div>";

    // 最近演化事件
    html += '<div style="margin-top:18px;">';
    html += '<div class="sm-why-block-title">最近演化事件</div>';
    if (!recent.length) {
      html += '<div class="sm-offline" style="padding:20px;">' +
        '<div class="sm-offline-text">暂无演化记录</div>' +
        '<div class="sm-offline-hint">羽依尚无 belief / history / reflection 数据</div>' +
      '</div>';
    } else {
      html += '<div class="sm-timeline">';
      recent.slice(0, 6).forEach(function (it) {
        html += renderTimelineItem(it);
      });
      html += "</div>";
    }
    html += "</div>";

    setHTML("sm-view-overview", html);
  }

  function renderStatCard(label, kind, value, hint, extra) {
    var valHtml = (kind === "value")
      ? '<div class="sm-stat-value">' + escapeHtml(value) + '</div>'
      : '<div class="sm-stat-value sm-stat-value--small">' + (value || "") + '</div>';
    return '<div class="sm-stat-card">' +
      '<div class="sm-stat-label">' + escapeHtml(label) + '</div>' +
      valHtml +
      (hint ? '<div class="sm-stat-hint">' + escapeHtml(hint) + '</div>' : '') +
      (extra || "") +
    '</div>';
  }

  function renderTimelineItem(it) {
    var src = it.source || "history";
    var iconCls = "sm-timeline-icon--" + src;
    var icon = src === "belief" ? "✦" : (src === "reflection" ? "♡" : (src === "audit" ? "✎" : "▸"));
    return '<div class="sm-timeline-item">' +
      '<div class="sm-timeline-icon ' + iconCls + '">' + icon + '</div>' +
      '<div class="sm-timeline-body">' +
        '<div class="sm-timeline-row">' +
          '<span class="sm-timeline-source">' + escapeHtml(src) + '</span>' +
          '<span class="sm-timeline-time" title="' + escapeHtml(it.timestamp || "") + '">' +
            escapeHtml(fmtRelative(it.timestamp) || fmtTs(it.timestamp)) +
          '</span>' +
        '</div>' +
        '<div class="sm-timeline-summary">' + escapeHtml(it.summary || it.event_id || "") + '</div>' +
      '</div>' +
    '</div>';
  }

  // ============================================================
  // Identity
  // ============================================================
  function renderIdentity(identity) {
    if (!identity || identity.available === false) {
      var msg = (identity && identity.identity_name) ? identity.identity_name : "Identity 不可用";
      setHTML("sm-view-identity",
        '<div class="sm-offline">' +
          '<div class="sm-offline-icon">✦</div>' +
          '<div class="sm-offline-text">' + escapeHtml(msg) + '</div>' +
          '<div class="sm-offline-hint">SelfModel Identity 未初始化</div>' +
        '</div>'
      );
      return;
    }

    var name = identity.identity_name || "浅雾羽依";
    var core = identity.core_identity || identity.identity_role || "AI 助手";
    var persona = identity.current_personality || identity.personality || "温柔 / 理性";
    var narrative = identity.narrative || identity.growth_narrative || "";
    var sul = identity.self_understanding_level;

    var html = "";
    html += '<div class="sm-identity-card">';
    html += '<div class="sm-identity-avatar">羽</div>';
    html += '<div class="sm-identity-body">';
    html += '<div class="sm-identity-name">' + escapeHtml(name);
    if (identity.available === true) {
      html += ' <span class="sm-status-pill sm-status-pill--ok"><span class="sm-status-dot"></span>active</span>';
    }
    html += '</div>';
    html += '<div class="sm-identity-core">' + escapeHtml(core) + ' · 当前人格：' + escapeHtml(persona) + '</div>';
    if (narrative) {
      html += '<div class="sm-identity-narrative">' + escapeHtml(narrative) + '</div>';
    }
    if (sul !== undefined && sul !== null) {
      var pct = Math.round(Number(sul) * 100);
      html += '<div class="sm-identity-meter">' +
        '<span>自我理解水平</span>' +
        '<div class="sm-identity-meter-bar"><div class="sm-identity-meter-fill" style="width:' + pct + '%"></div></div>' +
        '<span>' + pct + '%</span>' +
      '</div>';
    }
    html += '</div></div>';

    setHTML("sm-view-identity", html);
  }

  // ============================================================
  // Beliefs
  // ============================================================
  function ensureBeliefsListContainer() {
    return $("sm-view-beliefs");
  }

  function renderBeliefs(beliefsData) {
    var container = ensureBeliefsListContainer();
    if (!container) return;
    if (!beliefsData || beliefsData.available === false) {
      renderOffline("sm-view-beliefs", "Beliefs 不可用");
      return;
    }

    var items = beliefsData.items || [];
    var total = beliefsData.total !== undefined ? beliefsData.total : items.length;
    var active = beliefsData.active !== undefined ? beliefsData.active : 0;
    var inactive = beliefsData.inactive !== undefined ? beliefsData.inactive : 0;
    var byDomain = beliefsData.by_domain || {};

    // 渲染 toolbar + 列表
    var html = "";
    html += '<div class="sm-briefs-toolbar">';
    html += '<select class="sm-briefs-filter" id="sm-beliefs-domain-filter">' +
      '<option value="">全部领域 (' + total + ')</option>';
    Object.keys(byDomain).forEach(function (d) {
      html += '<option value="' + escapeHtml(d) + '">' + escapeHtml(d) + ' (' + byDomain[d] + ')</option>';
    });
    html += '</select>';
    html += '<select class="sm-briefs-filter" id="sm-beliefs-active-filter">' +
      '<option value="all">全部 (' + total + ')</option>' +
      '<option value="active">仅 active (' + active + ')</option>' +
      '<option value="inactive">仅 inactive (' + inactive + ')</option>' +
      '</select>';
    html += '<input type="text" class="sm-briefs-search" id="sm-beliefs-search" placeholder="搜索 belief 内容…" />';
    html += '<span class="sm-stat-hint" id="sm-beliefs-count">显示 ' + items.length + ' / ' + total + '</span>';
    html += '</div>';

    html += '<div class="sm-briefs-list" id="sm-beliefs-list">';
    if (items.length === 0) {
      html += '<div class="sm-offline" style="padding:24px;">' +
        '<div class="sm-offline-text">暂无 belief</div>' +
        '<div class="sm-offline-hint">羽依尚无 SelfBelief 数据</div>' +
      '</div>';
    } else {
      items.forEach(function (it) {
        html += renderBeliefCard(it);
      });
    }
    html += '</div>';

    setHTML("sm-view-beliefs", html);
    bindBeliefsToolbar();
  }

  function renderBeliefCard(b) {
    var domain = b.domain || "value";
    var domCls = "sm-brief-domain--" + domain;
    var active = b.active !== false;
    var cardCls = "sm-brief-card" + (active ? "" : " sm-brief-card--inactive");
    var conf = Number(b.confidence || 0);
    var pct = Math.max(0, Math.min(100, Math.round(conf * 100)));
    var sources = (b.sources || []);
    var evCount = b.evidence_count !== undefined ? b.evidence_count : sources.length;

    return '<div class="' + cardCls + '" data-belief-id="' + escapeHtml(b.belief_id) + '">' +
      '<div class="sm-brief-row1">' +
        '<span class="sm-brief-domain ' + domCls + '">' + escapeHtml(domain) + '</span>' +
        '<span class="sm-brief-confidence">' +
          '<span class="sm-brief-confidence-bar"><span class="sm-brief-confidence-fill" style="width:' + pct + '%"></span></span>' +
          fmtPercent(conf) +
        '</span>' +
      '</div>' +
      '<div class="sm-brief-content">' + escapeHtml(b.content || "") + '</div>' +
      '<div class="sm-brief-meta">' +
        '<span><span class="sm-brief-active-dot' + (active ? '' : ' sm-brief-active-dot--inactive') + '"></span>' + (active ? 'active' : 'inactive') + '</span>' +
        '<span>v' + (b.version || 1) + '</span>' +
        '<span>来源 ' + evCount + ' 个</span>' +
        (b.first_seen ? '<span>first: ' + escapeHtml(fmtTs(b.first_seen)) + '</span>' : '') +
        (b.last_confirmed ? '<span>last: ' + escapeHtml(fmtRelative(b.last_confirmed)) + '</span>' : '') +
      '</div>' +
    '</div>';
  }

  function bindBeliefsToolbar() {
    var dEl = $("sm-beliefs-domain-filter");
    var aEl = $("sm-beliefs-active-filter");
    var sEl = $("sm-beliefs-search");
    if (dEl) dEl.addEventListener("change", reloadBeliefsWithFilters);
    if (aEl) aEl.addEventListener("change", reloadBeliefsWithFilters);
    if (sEl) sEl.addEventListener("input", debounce(reloadBeliefsWithFilters, 300));

    var list = $("sm-beliefs-list");
    if (list) {
      list.addEventListener("click", function (e) {
        var card = e.target.closest && e.target.closest(".sm-brief-card");
        if (!card) return;
        var bid = card.dataset.beliefId;
        if (bid) showWhy(bid);
      });
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

  function reloadBeliefsWithFilters() {
    var domain = ($("sm-beliefs-domain-filter") || {}).value || null;
    var active = ($("sm-beliefs-active-filter") || {}).value || "all";
    var q = (($("sm-beliefs-search") || {}).value || "").toLowerCase().trim();

    var includeInactive = active !== "active";
    fetchAndRenderBeliefs({
      domain: domain,
      include_inactive: includeInactive ? "true" : "false",
    }, q);
  }

  function fetchAndRenderBeliefs(params, searchQ) {
    var qs = [];
    if (params && params.domain) qs.push("domain=" + encodeURIComponent(params.domain));
    if (params && params.include_inactive) qs.push("include_inactive=" + params.include_inactive);
    qs.push("limit=200");
    var path = CONFIG.endpoints.beliefs + "?" + qs.join("&");

    renderLoading("sm-view-beliefs", "正在加载 beliefs");
    safeFetch("beliefs", path).then(function (r) {
      if (!r.ok) {
        renderOffline("sm-view-beliefs", "Beliefs 加载失败");
        return;
      }
      var data = r.data;
      // 客户端搜索过滤
      if (searchQ) {
        data = Object.assign({}, data, {
          items: (data.items || []).filter(function (it) {
            return String(it.content || "").toLowerCase().indexOf(searchQ) >= 0;
          }),
          total: (data.items || []).filter(function (it) {
            return String(it.content || "").toLowerCase().indexOf(searchQ) >= 0;
          }).length,
        });
      }
      state.beliefsCache = data;
      state.beliefsCacheAt = Date.now();
      renderBeliefs(data);
    });
  }

  // ============================================================
  // Why 详情
  // ============================================================
  function showWhy(beliefId) {
    if (!beliefId) return;
    state.currentWhyBeliefId = beliefId;
    activateTab("beliefs");

    var container = $("sm-view-beliefs");
    if (!container) return;

    renderLoading("sm-view-beliefs", "正在追溯 belief 起源");

    safeFetch("beliefWhy", CONFIG.endpoints.beliefWhy + "/" + encodeURIComponent(beliefId) + "/why")
      .then(function (r) {
        if (!r.ok) {
          renderOffline("sm-view-beliefs", "Why 追溯失败");
          return;
        }
        renderWhyPanel(r.data, beliefId);
      });
  }

  function renderWhyPanel(data, beliefId) {
    if (!data || data.available === false) {
      var err = (data && data.error) || "Why 不可用";
      setHTML("sm-view-beliefs",
        '<button class="sm-why-back" id="sm-why-back-btn">← 返回 Beliefs</button>' +
        '<div class="sm-offline">' +
          '<div class="sm-offline-icon">✦</div>' +
          '<div class="sm-offline-text">' + escapeHtml(err) + '</div>' +
          '<div class="sm-offline-hint">该 belief 无可追溯数据</div>' +
        '</div>'
      );
      bindWhyBack();
      return;
    }

    var belief = data.belief || {};
    var origin = data.origin || {};
    var pcr = data.pcr_link || {};
    var summary = pcr.summary || {};
    var answer = data.answer || "";

    var sources = origin.sources || [];
    var historyEvents = (pcr.history_events || []);

    var html = "";
    html += '<button class="sm-why-back" id="sm-why-back-btn">← 返回 Beliefs</button>';

    // 标题
    html += '<div class="sm-why-block">';
    html += '<div class="sm-why-block-title">Belief</div>';
    html += '<div class="sm-why-block-content">' +
      '<div style="font-size:15px;font-weight:600;margin-bottom:4px;">' + escapeHtml(belief.content || beliefId) + '</div>' +
      '<div class="sm-brief-meta">' +
        '<span class="sm-brief-domain sm-brief-domain--' + escapeHtml(belief.domain || "value") + '">' + escapeHtml(belief.domain || "value") + '</span>' +
        '<span>confidence: ' + fmtPercent(belief.confidence) + '</span>' +
        (belief.belief_id ? '<span>id: ' + escapeHtml(belief.belief_id) + '</span>' : '') +
      '</div>' +
    '</div>';

    // 链路
    html += '<div class="sm-why-block">';
    html += '<div class="sm-why-block-title">追溯链路（belief ← sources ← proposal ← PCR ← history）</div>';
    html += '<div class="sm-why-flow">';
    html += '<div class="sm-why-step">' +
      '<span class="sm-why-step-arrow">▸</span>' +
      '<div class="sm-why-step-body">' +
        '<div class="sm-why-step-label">来源（sources）</div>' +
        '<div class="sm-why-step-value">' +
          (sources.length
            ? '<div class="sm-why-source-list">' +
                sources.map(function (s) { return '<div class="sm-why-source-item">#' + escapeHtml(s) + '</div>'; }).join("") +
              '</div>'
            : '<div class="sm-why-step-value--muted">无 sources</div>') +
        '</div>' +
      '</div>' +
    '</div>';
    html += '<div class="sm-why-step">' +
      '<span class="sm-why-step-arrow">↓</span>' +
      '<div class="sm-why-step-body">' +
        '<div class="sm-why-step-label">Proposal（来自）</div>' +
        '<div class="sm-why-step-value ' + (sources.length ? '' : 'sm-why-step-value--muted') + '">' +
          (sources.length
            ? sources.map(function (s) { return 'proposal_id = <code>' + escapeHtml(s) + '</code>'; }).join(" · ")
            : '未发现关联 proposal') +
        '</div>' +
      '</div>' +
    '</div>';
    html += '<div class="sm-why-step">' +
      '<span class="sm-why-step-arrow">↓</span>' +
      '<div class="sm-why-step-body">' +
        '<div class="sm-why-step-label">PCR 应用记录</div>' +
        '<div class="sm-why-step-value ' + (historyEvents.length ? '' : 'sm-why-step-value--muted') + '">' +
          (historyEvents.length
            ? historyEvents.length + ' 条相关 history 事件'
            : '无 PCR history 记录') +
        '</div>' +
      '</div>' +
    '</div>';
    html += '<div class="sm-why-step">' +
      '<span class="sm-why-step-arrow">↓</span>' +
      '<div class="sm-why-step-body">' +
        '<div class="sm-why-step-label">最终变化</div>' +
        '<div class="sm-why-step-value ' + (answer ? '' : 'sm-why-step-value--muted') + '">' +
          (answer
            ? escapeHtml(answer)
            : '尚无 self_explanation 文本') +
        '</div>' +
      '</div>' +
    '</div>';
    html += '</div>'; // flow
    html += '</div>';

    // 摘要
    if (answer) {
      html += '<div class="sm-why-summary">' + escapeHtml(answer) + '</div>';
    }

    // 关键统计
    html += '<div class="sm-why-block">';
    html += '<div class="sm-why-block-title">关联统计</div>';
    html += '<div class="sm-brief-meta">' +
      '<span>linked beliefs: ' + (summary.belief_count !== undefined ? summary.belief_count : 0) + '</span>' +
      '<span>history: ' + (summary.history_count !== undefined ? summary.history_count : 0) + '</span>' +
      '<span>reflections: ' + (summary.reflection_count !== undefined ? summary.reflection_count : 0) + '</span>' +
      '<span>audit: ' + (summary.audit_count !== undefined ? summary.audit_count : 0) + '</span>' +
    '</div>';
    html += '</div>';

    setHTML("sm-view-beliefs", html);
    bindWhyBack();
  }

  function bindWhyBack() {
    var btn = $("sm-why-back-btn");
    if (btn) {
      btn.addEventListener("click", function () {
        state.currentWhyBeliefId = null;
        if (state.beliefsCache) {
          renderBeliefs(state.beliefsCache);
        } else {
          reloadBeliefsWithFilters();
        }
      });
    }
  }

  // ============================================================
  // 渲染入口
  // ============================================================
  function render(force) {
    if (state.isFetching) return;
    state.isFetching = true;

    // 加载状态
    renderLoading("sm-view-overview", "正在加载 SelfModel 状态");
    renderLoading("sm-view-identity", "正在加载 Identity");
    renderLoading("sm-view-beliefs", "正在加载 Beliefs");

    var pStatus = safeFetch("status", CONFIG.endpoints.status);
    var pIdentity = safeFetch("identity", CONFIG.endpoints.identity);
    var pHealth = safeFetch("health", CONFIG.endpoints.health);
    var pRetention = safeFetch("retention", CONFIG.endpoints.retention);
    var pTimeline = safeFetch("timeline", CONFIG.endpoints.timeline + "?limit=10");

    Promise.all([pStatus, pIdentity, pHealth, pRetention, pTimeline]).then(function (rs) {
      state.isFetching = false;
      state.lastFetchedAt = new Date();
      state.lastError = null;

      // rs 顺序：[status, identity, health, retention, timeline]
      renderOverview(rs[0].data, rs[1].data, rs[2].data, rs[3].data, rs[4].data);
      renderIdentity(rs[1].data);

      // Beliefs 列表（独立拉取，可单独刷新）
      fetchAndRenderBeliefs({ include_inactive: "true" }, null);
    });
  }

  // ============================================================
  // 对外暴露
  // ============================================================
  function init() {
    if (window.YuyiSelfModelDashboard && window.YuyiSelfModelDashboard._initialized) return;

    // 绑定 tab 切换
    document.querySelectorAll(".sm-tab").forEach(function (el) {
      el.addEventListener("click", function () {
        var tab = el.dataset.tab;
        if (!tab) return;
        activateTab(tab);
      });
    });

    // 绑定 refresh 按钮（如果存在）
    var refreshBtn = $("sm-refresh-btn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () { render(true); });
    }

    window.YuyiSelfModelDashboard = {
      _initialized: true,
      render: render,
      showWhy: showWhy,
      reloadBeliefs: reloadBeliefsWithFilters,
      activateTab: activateTab,
      // 暴露给 test_only 钩子
      _fetchBeliefs: fetchAndRenderBeliefs,
      _state: state,
    };
  }

  // 自动初始化
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
