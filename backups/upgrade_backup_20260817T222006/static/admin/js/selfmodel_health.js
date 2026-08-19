/* ============================================
   羽依 Yuyi Console — Phase 6.5 Step 5.4
   SelfModel Health Dashboard + Retention Dashboard
   --------------------------------------------
   职责：
   - 通过 /api/admin/selfmodel/health 拉取健康报告
   - 渲染 Health Overview（overall_status / check time / issues）
   - 渲染 Issue List（按 severity 过滤）
   - 渲染 Persistence 状态（文件 / snapshot）
   - 通过 /api/admin/selfmodel/retention 拉取配额与容量
   - 渲染当前容量（Beliefs / History / Reflection）
   - 渲染 Quota 进度条
   - 调用 POST /api/admin/selfmodel/retention/dry_run 模拟整理
   - 严格只读：除 dry_run 之外不允许任何写入接口
   ============================================ */

(function () {
  "use strict";

  // 防止重复初始化
  if (window.YuyiSelfModelHealth && window.YuyiSelfModelHealth._initialized) {
    return;
  }

  // ============================================================
  // 配置
  // ============================================================
  var CONFIG = {
    healthEndpoint: "/admin/api/admin/selfmodel/health",
    retentionEndpoint: "/admin/api/admin/selfmodel/retention",
    dryRunEndpoint: "/admin/api/admin/selfmodel/retention/dry_run",
  };

  // 严重程度选项
  var SEVERITIES = ["all", "critical", "warning", "info"];
  // 颜色映射
  var STATUS_COLOR = {
    healthy:   "healthy",
    warning:   "warning",
    degraded:  "degraded",
    critical:  "critical",
    unknown:   "unknown",
  };
  var SEVERITY_ICON = {
    critical: "⚠",
    error:    "✕",
    warning:  "!",
    info:     "i",
  };
  var SEVERITY_COLOR = {
    critical: "critical",
    error:    "critical",
    warning:  "warning",
    info:     "info",
  };

  // ============================================================
  // 状态
  // ============================================================
  var state = {
    severityFilter: "all",
    isFetchingHealth: false,
    isFetchingRetention: false,
    isRunningDryRun: false,
    lastHealth: null,
    lastRetention: null,
    lastDryRun: null,
    healthError: null,
    retentionError: null,
    dryRunError: null,
    lastFetchedAt: null,
  };

  // ============================================================
  // API 工具
  // ============================================================
  function fetchJson(path) {
    return fetch("/admin/api" + path, {
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

  function postJson(path, body) {
    return fetch("/admin/api" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : JSON.stringify({ dry_run: true }),
      cache: "no-store",
    }).then(function (res) {
      if (!res.ok) {
        throw new Error("HTTP " + res.status);
      }
      return res.json();
    });
  }

  function safeGet(path) {
    return fetchJson(path).then(function (data) {
      return { ok: true, data: data };
    }).catch(function (err) {
      console.warn("[SelfModelHealth] GET " + path + " failed:", err);
      return { ok: false, error: err && err.message ? err.message : String(err) };
    });
  }

  function safePost(path, body) {
    return postJson(path, body).then(function (data) {
      return { ok: true, data: data };
    }).catch(function (err) {
      console.warn("[SelfModelHealth] POST " + path + " failed:", err);
      return { ok: false, error: err && err.message ? err.message : String(err) };
    });
  }

  // ============================================================
  // DOM 工具
  // ============================================================
  function $(id) { return document.getElementById(id); }
  function setHTML(id, html) { var el = $(id); if (el) el.innerHTML = html; }

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
        + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
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

  function statusToLabel(s) {
    switch (s) {
      case "healthy":  return "健康";
      case "warning":  return "注意";
      case "degraded": return "降级";
      case "critical": return "严重";
      default:         return "未知";
    }
  }

  function statusToIcon(s) {
    switch (s) {
      case "healthy":  return "♡";
      case "warning":  return "!";
      case "degraded": return "△";
      case "critical": return "✕";
      default:         return "?";
    }
  }

  // ============================================================
  // Loading / Offline / Empty
  // ============================================================
  function renderLoading(id, text) {
    setHTML(id, '<div class="smhealth-loading">' + escapeHtml(text || "加载中") + '</div>');
  }

  function renderOffline(id, message) {
    setHTML(id,
      '<div class="smhealth-offline">' +
        '<div class="smhealth-offline-icon">✦</div>' +
        '<div class="smhealth-offline-text">' + escapeHtml(message || "不可用") + '</div>' +
        '<div class="smhealth-offline-hint">Runtime 未启动 / SelfModel Bridge 未就绪</div>' +
      '</div>'
    );
  }

  // ============================================================
  // 状态卡（runtime 状态指示）
  // ============================================================
  function renderRuntimeBadge() {
    var dot = $("smh-runtime-dot");
    var text = $("smh-runtime-text");
    if (!dot || !text) return;

    var available = state.lastHealth && state.lastHealth.available;
    var status = "unknown";
    if (state.lastHealth && state.lastHealth.report) {
      status = state.lastHealth.report.overall_status || "unknown";
    } else if (state.lastRetention && state.lastRetention.available) {
      status = "healthy";
    }
    var color = STATUS_COLOR[status] || "unknown";
    dot.className = "yuyi-status-dot smh-status--" + color;
    text.textContent = statusToLabel(status);
  }

  // ============================================================
  // Health Overview 渲染
  // ============================================================
  function renderHealthOverview() {
    var id = "smh-health-overview";
    if (!state.lastHealth) {
      renderLoading(id, "正在加载健康状态");
      return;
    }
    if (state.healthError) {
      renderOffline(id, "Health 加载失败: " + state.healthError);
      return;
    }
    if (!state.lastHealth.available || !state.lastHealth.report) {
      renderOffline(id, "Health 不可用");
      return;
    }
    var r = state.lastHealth.report;
    var status = r.overall_status || "unknown";
    var color = STATUS_COLOR[status] || "unknown";
    var issues = r.issues || [];
    var issueCount = issues.length;
    var criticalCount = issues.filter(function (it) {
      var sv = (it.severity || "").toLowerCase();
      return sv === "critical" || sv === "error";
    }).length;

    var html = "";
    html += '<div class="smhealth-overview-card smhealth-overview-card--' + color + '">';
    html += '  <div class="smhealth-overview-label">健康状态</div>';
    html += '  <div class="smhealth-overview-value smhealth-overview-value--' + color + '">';
    html += '    <span class="smhealth-overview-icon smhealth-overview-icon--' + color + '">' + statusToIcon(status) + '</span>';
    html += escapeHtml(statusToLabel(status));
    html += '  </div>';
    html += '  <div class="smhealth-overview-hint">' + escapeHtml(status) + '</div>';
    html += '</div>';

    html += '<div class="smhealth-overview-card">';
    html += '  <div class="smhealth-overview-label">检查时间</div>';
    html += '  <div class="smhealth-overview-value" style="font-size:16px;">' + escapeHtml(fmtTs(r.timestamp)) + '</div>';
    html += '  <div class="smhealth-overview-hint">' + escapeHtml(fmtRelative(r.timestamp) || "刚刚") + '</div>';
    html += '</div>';

    html += '<div class="smhealth-overview-card">';
    html += '  <div class="smhealth-overview-label">问题数量</div>';
    html += '  <div class="smhealth-overview-value">' + escapeHtml(issueCount) + '</div>';
    html += '  <div class="smhealth-overview-hint">' + (criticalCount > 0
        ? '其中 ' + escapeHtml(criticalCount) + ' 个严重'
        : '无严重问题') + '</div>';
    html += '</div>';

    html += '<div class="smhealth-overview-card">';
    html += '  <div class="smhealth-overview-label">数据规模</div>';
    html += '  <div class="smhealth-overview-value" style="font-size:18px;">' +
      escapeHtml((r.beliefs_count || 0)) + ' / ' +
      escapeHtml((r.history_count || 0)) + ' / ' +
      escapeHtml((r.reflections_count || 0)) +
    '</div>';
    html += '  <div class="smhealth-overview-hint">beliefs / history / reflections</div>';
    html += '</div>';

    setHTML(id, html);
  }

  // ============================================================
  // Issue 列表（severity 过滤）
  // ============================================================
  function renderIssuesToolbar() {
    var id = "smh-issues-toolbar";
    var html = "";
    html += '<div class="smhealth-toolbar-group">';
    html += '  <span class="smhealth-toolbar-label">严重程度</span>';
    SEVERITIES.forEach(function (sv) {
      var label = sv === "all" ? "全部"
        : sv === "critical" ? "严重"
        : sv === "warning" ? "警告"
        : "信息";
      html += '<button class="smhealth-chip ' +
        (state.severityFilter === sv ? "smhealth-chip--active" : "") +
        '" data-severity="' + escapeHtml(sv) + '">' + escapeHtml(label) + '</button>';
    });
    html += '</div>';

    html += '<div class="smhealth-toolbar-group" style="flex:1;justify-content:flex-end;">';
    html += '  <span class="smhealth-readonly-tag">READ-ONLY</span>';
    html += '</div>';

    setHTML(id, html);
    bindIssuesToolbar();
  }

  function bindIssuesToolbar() {
    var chips = document.querySelectorAll("#smh-issues-toolbar .smhealth-chip");
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        var sv = chip.dataset.severity;
        if (!sv) return;
        state.severityFilter = sv;
        renderIssuesToolbar();
        renderIssues();
      });
    });
  }

  function renderIssues() {
    var id = "smh-issues";
    if (!state.lastHealth) {
      renderLoading(id, "正在加载问题列表");
      return;
    }
    if (!state.lastHealth.available || !state.lastHealth.report) {
      renderOffline(id, "Issues 不可用");
      return;
    }
    var issues = state.lastHealth.report.issues || [];
    var filtered = issues.filter(function (it) {
      if (state.severityFilter === "all") return true;
      var sv = (it.severity || "").toLowerCase();
      if (state.severityFilter === "critical") {
        return sv === "critical" || sv === "error";
      }
      return sv === state.severityFilter;
    });

    if (filtered.length === 0) {
      setHTML(id,
        '<div class="smhealth-empty">' +
          '<div class="smhealth-empty-icon">♡</div>' +
          '<div class="smhealth-empty-text">没有匹配的问题</div>' +
          '<div class="smhealth-empty-hint">' +
            (state.severityFilter === "all"
              ? "SelfModel 当前没有任何健康问题"
              : "当前严重程度下未发现问题") +
          '</div>' +
        '</div>'
      );
      return;
    }

    var html = '<div class="smhealth-issues">';
    filtered.forEach(function (it) {
      html += renderIssueItem(it);
    });
    html += '</div>';
    setHTML(id, html);
  }

  function renderIssueItem(it) {
    var sev = (it.severity || "info").toLowerCase();
    var colorKey = SEVERITY_COLOR[sev] || "info";
    var icon = SEVERITY_ICON[sev] || "·";
    var label = sev.toUpperCase();

    var html = '<div class="smhealth-issue smhealth-issue--' + colorKey + '">';
    html += '<div class="smhealth-issue-icon smhealth-issue-icon--' + colorKey + '">' + icon + '</div>';
    html += '<div class="smhealth-issue-body">';
    html += '  <div class="smhealth-issue-row1">';
    html += '    <span class="smhealth-issue-severity smhealth-issue-severity--' + colorKey + '">' + escapeHtml(label) + '</span>';
    if (it.code) {
      html += '    <span class="smhealth-issue-code">' + escapeHtml(it.code) + '</span>';
    }
    if (it.location) {
      html += '    <span class="smhealth-issue-component">' + escapeHtml(it.location) + '</span>';
    }
    if (it.timestamp) {
      html += '    <span class="smhealth-issue-time" title="' + escapeHtml(it.timestamp) + '">' +
        escapeHtml(fmtRelative(it.timestamp) || fmtTs(it.timestamp)) + '</span>';
    }
    html += '  </div>';
    html += '  <div class="smhealth-issue-message">' + escapeHtml(it.message || "(无描述)") + '</div>';
    html += '</div>';
    html += '</div>';
    return html;
  }

  // ============================================================
  // Persistence 状态
  // ============================================================
  function renderPersistence() {
    var id = "smh-persistence";
    if (!state.lastHealth) {
      renderLoading(id, "正在加载 Persistence 状态");
      return;
    }
    if (!state.lastHealth.available || !state.lastHealth.report) {
      renderOffline(id, "Persistence 状态不可用");
      return;
    }
    var r = state.lastHealth.report;
    var filesMissing = r.files_missing || [];
    var filesCorrupted = r.files_corrupted || [];
    var fileSizes = r.file_sizes || {};
    var dataDir = r.data_dir || "";

    // 三个核心文件：beliefs / history / reflection
    var knownFiles = [
      { key: "beliefs",     label: "SelfBelief 文件",     icon: "✦" },
      { key: "history",     label: "SelfHistory 文件",    icon: "▸" },
      { key: "reflections", label: "SelfReflection 文件", icon: "♡" },
    ];
    // snapshot 不一定存在
    var snapshotKey = "snapshots";
    var snapshotFiles = Object.keys(fileSizes).filter(function (k) {
      return k.toLowerCase().indexOf("snap") >= 0;
    });

    var html = '<div class="smhealth-persistence-grid">';

    knownFiles.forEach(function (f) {
      var size = fileSizes[f.key];
      var missing = filesMissing.some(function (m) { return String(m).toLowerCase().indexOf(f.key) >= 0; });
      var corrupt = filesCorrupted.some(function (c) {
        var name = (c && c.file) ? String(c.file).toLowerCase() : String(c).toLowerCase();
        return name.indexOf(f.key) >= 0;
      });
      var pillCls = "unknown";
      var pillTxt = "未知";
      if (missing)        { pillCls = "missing"; pillTxt = "缺失"; }
      else if (corrupt)   { pillCls = "corrupt"; pillTxt = "损坏"; }
      else if (size !== undefined && size !== null) { pillCls = "ok"; pillTxt = "正常"; }
      else if (Object.prototype.hasOwnProperty.call(fileSizes, f.key) === false) {
        pillCls = "unknown"; pillTxt = "未检查";
      }

      html += '<div class="smhealth-persistence-card">';
      html += '  <div class="smhealth-persistence-card-head">';
      html += '    <span class="smhealth-persistence-card-name">' + f.icon + ' ' + escapeHtml(f.label) + '</span>';
      html += '    <span class="smhealth-persistence-pill smhealth-persistence-pill--' + pillCls + '">' + pillTxt + '</span>';
      html += '  </div>';
      html += '  <div class="smhealth-persistence-card-meta">';
      if (size !== undefined && size !== null) {
        html += 'size: ' + escapeHtml(size) + ' bytes';
      } else if (missing) {
        html += '文件未找到';
      } else {
        html += '未发现该文件';
      }
      html += '  </div>';
      html += '</div>';
    });

    // snapshot 状态
    var snapSize = fileSizes[snapshotKey];
    if (snapshotFiles.length > 0) {
      var sSize = snapshotFiles.reduce(function (acc, k) { return acc + (fileSizes[k] || 0); }, 0);
      html += '<div class="smhealth-persistence-card">';
      html += '  <div class="smhealth-persistence-card-head">';
      html += '    <span class="smhealth-persistence-card-name">⏳ Snapshot</span>';
      html += '    <span class="smhealth-persistence-pill smhealth-persistence-pill--ok">已挂载</span>';
      html += '  </div>';
      html += '  <div class="smhealth-persistence-card-meta">';
      html += 'files: ' + escapeHtml(snapshotFiles.length) + '，total: ' + escapeHtml(sSize) + ' bytes';
      html += '  </div>';
      html += '</div>';
    } else {
      html += '<div class="smhealth-persistence-card">';
      html += '  <div class="smhealth-persistence-card-head">';
      html += '    <span class="smhealth-persistence-card-name">⏳ Snapshot</span>';
      html += '    <span class="smhealth-persistence-pill smhealth-persistence-pill--unknown">未挂载</span>';
      html += '  </div>';
      html += '  <div class="smhealth-persistence-card-meta">未提供 snapshot 文件</div>';
      html += '</div>';
    }

    // data dir 信息
    if (dataDir) {
      html += '<div class="smhealth-persistence-card" style="grid-column:1/-1;">';
      html += '  <div class="smhealth-persistence-card-head">';
      html += '    <span class="smhealth-persistence-card-name">▦ data_dir</span>';
      html += '  </div>';
      html += '  <div class="smhealth-persistence-card-meta">' + escapeHtml(dataDir) + '</div>';
      html += '</div>';
    }

    html += '</div>';
    setHTML(id, html);
  }

  // ============================================================
  // Retention 容量
  // ============================================================
  function renderCapacity() {
    var id = "smh-capacity";
    if (!state.lastRetention) {
      renderLoading(id, "正在加载容量数据");
      return;
    }
    if (state.retentionError) {
      renderOffline(id, "Retention 加载失败: " + state.retentionError);
      return;
    }
    if (!state.lastRetention.available) {
      renderOffline(id, "Retention 不可用");
      return;
    }
    var counts = state.lastRetention.current_counts || {};

    // 推导 archived：
    // - adapter 没有持久化 archive 状态；这里全部以 0 表示
    // - 仅 total / active 是从 current_counts 读取
    var beliefsTotal = counts.beliefs_total || 0;
    var beliefsActive = counts.beliefs_active || 0;
    var beliefsInactive = counts.beliefs_inactive || 0;
    var beliefsArchived = 0;
    var historyTotal = counts.history_total || 0;
    var historyArchived = 0;
    var reflectionsTotal = counts.reflections_total || 0;
    var reflectionsArchived = 0;

    var html = '<div class="smhealth-capacity-grid">';

    // Beliefs
    html += '<div class="smhealth-capacity-card smhealth-capacity-card--beliefs">';
    html += '  <div class="smhealth-capacity-head">';
    html += '    <span class="smhealth-capacity-title"><span class="smhealth-capacity-icon smhealth-capacity-icon--beliefs">✦</span> Beliefs</span>';
    html += '  </div>';
    html += '  <div class="smhealth-capacity-total">' + escapeHtml(beliefsTotal) + '</div>';
    html += '  <div class="smhealth-capacity-rows">';
    html += '    <div class="smhealth-capacity-row"><span class="smhealth-capacity-row-key">active</span><span class="smhealth-capacity-row-value">' + escapeHtml(beliefsActive) + '</span></div>';
    html += '    <div class="smhealth-capacity-row"><span class="smhealth-capacity-row-key">inactive</span><span class="smhealth-capacity-row-value">' + escapeHtml(beliefsInactive) + '</span></div>';
    html += '    <div class="smhealth-capacity-row"><span class="smhealth-capacity-row-key">archived</span><span class="smhealth-capacity-row-value">' + escapeHtml(beliefsArchived) + '</span></div>';
    html += '  </div>';
    html += '</div>';

    // History
    html += '<div class="smhealth-capacity-card smhealth-capacity-card--history">';
    html += '  <div class="smhealth-capacity-head">';
    html += '    <span class="smhealth-capacity-title"><span class="smhealth-capacity-icon smhealth-capacity-icon--history">▸</span> History</span>';
    html += '  </div>';
    html += '  <div class="smhealth-capacity-total">' + escapeHtml(historyTotal) + '</div>';
    html += '  <div class="smhealth-capacity-rows">';
    html += '    <div class="smhealth-capacity-row"><span class="smhealth-capacity-row-key">total</span><span class="smhealth-capacity-row-value">' + escapeHtml(historyTotal) + '</span></div>';
    html += '    <div class="smhealth-capacity-row"><span class="smhealth-capacity-row-key">archived</span><span class="smhealth-capacity-row-value">' + escapeHtml(historyArchived) + '</span></div>';
    html += '  </div>';
    html += '</div>';

    // Reflection
    html += '<div class="smhealth-capacity-card smhealth-capacity-card--reflections">';
    html += '  <div class="smhealth-capacity-head">';
    html += '    <span class="smhealth-capacity-title"><span class="smhealth-capacity-icon smhealth-capacity-icon--reflections">♡</span> Reflection</span>';
    html += '  </div>';
    html += '  <div class="smhealth-capacity-total">' + escapeHtml(reflectionsTotal) + '</div>';
    html += '  <div class="smhealth-capacity-rows">';
    html += '    <div class="smhealth-capacity-row"><span class="smhealth-capacity-row-key">total</span><span class="smhealth-capacity-row-value">' + escapeHtml(reflectionsTotal) + '</span></div>';
    html += '    <div class="smhealth-capacity-row"><span class="smhealth-capacity-row-key">archived</span><span class="smhealth-capacity-row-value">' + escapeHtml(reflectionsArchived) + '</span></div>';
    html += '  </div>';
    html += '</div>';

    html += '</div>';
    setHTML(id, html);

    // 同步渲染 quota
    renderQuota();
  }

  // ============================================================
  // Quota 进度条
  // ============================================================
  function renderQuota() {
    var id = "smh-quota";
    if (!state.lastRetention || !state.lastRetention.available) {
      renderLoading(id, "正在加载配额");
      return;
    }
    var counts = state.lastRetention.current_counts || {};
    var th = state.lastRetention.thresholds || {};
    var items = [
      { key: "max_active_beliefs",   label: "Active Beliefs",     used: counts.beliefs_active || 0 },
      { key: "max_in_memory_events", label: "History Events",     used: counts.history_total || 0 },
      { key: "recent_window_days",   label: "Reflection Window",  used: counts.reflections_total || 0, window: true },
    ];

    var html = '<div class="smhealth-quota-list">';
    items.forEach(function (it) {
      var max = th[it.key];
      var used = it.used;
      var pct = 0;
      var fillCls = "ok";
      if (it.window) {
        // recent_window_days：进度条以 1 个窗口为参考
        // 当前 reflections vs 假定 1 个窗口最多 N 条（这里用 window 本身作为标签）
        pct = 0;
        html += '<div class="smhealth-quota-row">';
        html += '  <div class="smhealth-quota-head">';
        html += '    <span class="smhealth-quota-name">' + escapeHtml(it.label) + '</span>';
        html += '    <span class="smhealth-quota-usage">' + escapeHtml(used) + ' 条 · 窗口 ' + escapeHtml(max) + ' 天</span>';
        html += '  </div>';
        html += '  <div class="smhealth-quota-bar"><div class="smhealth-quota-fill smhealth-quota-fill--ok" style="width:' + (used > 0 ? 30 : 5) + '%;"></div></div>';
        html += '</div>';
        return;
      }
      if (max && max > 0) {
        pct = Math.min(100, Math.round((used / max) * 100));
        if (pct >= 100)      fillCls = "over";
        else if (pct >= 80)  fillCls = "warn";
        else                 fillCls = "ok";
      }
      html += '<div class="smhealth-quota-row">';
      html += '  <div class="smhealth-quota-head">';
      html += '    <span class="smhealth-quota-name">' + escapeHtml(it.label) + '</span>';
      html += '    <span class="smhealth-quota-usage">' + escapeHtml(used) + ' / ' + escapeHtml(max || "--") + ' (' + escapeHtml(pct) + '%)</span>';
      html += '  </div>';
      html += '  <div class="smhealth-quota-bar">';
      html += '    <div class="smhealth-quota-fill smhealth-quota-fill--' + fillCls + '" style="width:' + Math.max(2, pct) + '%;"></div>';
      html += '  </div>';
      html += '</div>';
    });
    html += '</div>';

    // 额外展示其他阈值（不画进度条）
    var extraKeys = ["min_confidence_for_active", "importance_low_below", "high_value_min_confidence"];
    var hasExtra = extraKeys.some(function (k) { return th[k] !== undefined; });
    if (hasExtra) {
      html += '<div class="smhealth-block" style="margin-top:8px;background:rgba(255,255,255,0.4);">';
      html += '  <div class="smhealth-block-title">▦ 阈值详情</div>';
      extraKeys.forEach(function (k) {
        if (th[k] !== undefined) {
          var name = ({
            min_confidence_for_active: "min_confidence_for_active",
            importance_low_below: "importance_low_below",
            high_value_min_confidence: "high_value_min_confidence",
          })[k] || k;
          html += '<div style="font-size:11px;color:var(--c-text-muted);font-family:JetBrains Mono,Consolas,monospace;">' +
            escapeHtml(name) + ' = ' + escapeHtml(th[k]) + '</div>';
        }
      });
      html += '</div>';
    }

    setHTML(id, html);
  }

  // ============================================================
  // Retention Dry Run
  // ============================================================
  function renderDryRunResult() {
    var id = "smh-dryrun-result";
    if (!state.lastDryRun) return; // 不主动渲染，等用户点击
    if (state.dryRunError) {
      setHTML(id,
        '<div class="smhealth-dryrun-result">' +
          '<div class="smhealth-dryrun-result-head">' +
            '✕ Dry Run 失败: ' + escapeHtml(state.dryRunError) +
          '</div>' +
        '</div>'
      );
      return;
    }
    var d = state.lastDryRun;
    var r = d.report || {};
    var summary = d.summary || {};
    var beliefs = summary.beliefs || {};
    var history = summary.history || {};
    var reflections = summary.reflections || {};
    var applied = !!d.applied;
    var pillCls = applied ? "" : "smhealth-dryrun-pill--applied-false";
    var pillTxt = applied ? "applied" : "dry-run only";

    var html = '<div class="smhealth-dryrun-result">';
    html += '  <div class="smhealth-dryrun-result-head">';
    html += '    <span>✦ Dry Run 报告</span>';
    html += '    <span class="smhealth-dryrun-pill ' + pillCls + '">' + escapeHtml(pillTxt) + '</span>';
    html += '  </div>';
    html += '  <div class="smhealth-dryrun-stats">';
    html += '    <div class="smhealth-dryrun-stat">';
    html += '      <div class="smhealth-dryrun-stat-label">deactivate beliefs</div>';
    html += '      <div class="smhealth-dryrun-stat-value ' + (beliefs.deactivated ? "" : "smhealth-dryrun-stat-value--zero") + '">' +
      escapeHtml(beliefs.deactivated || 0) + '</div>';
    html += '    </div>';
    html += '    <div class="smhealth-dryrun-stat">';
    html += '      <div class="smhealth-dryrun-stat-label">archive history</div>';
    html += '      <div class="smhealth-dryrun-stat-value ' + (history.archived ? "" : "smhealth-dryrun-stat-value--zero") + '">' +
      escapeHtml(history.archived || 0) + '</div>';
    html += '    </div>';
    html += '    <div class="smhealth-dryrun-stat">';
    html += '      <div class="smhealth-dryrun-stat-label">archive reflections</div>';
    html += '      <div class="smhealth-dryrun-stat-value ' + (reflections.archived ? "" : "smhealth-dryrun-stat-value--zero") + '">' +
      escapeHtml(reflections.archived || 0) + '</div>';
    html += '    </div>';
    html += '  </div>';
    html += '  <div style="font-size:11px;color:var(--c-text-muted);">';
    html += '    预计不会修改任何数据。详情见 <code style="font-family:JetBrains Mono,Consolas,monospace;">report</code>。';
    html += '  </div>';
    html += '</div>';

    setHTML(id, html);
  }

  function runDryRun() {
    if (state.isRunningDryRun) return;
    state.isRunningDryRun = true;
    state.dryRunError = null;
    state.lastDryRun = null;

    var btn = $("smh-dryrun-btn");
    if (btn) btn.disabled = true;

    setHTML("smh-dryrun-result",
      '<div class="smhealth-loading">正在模拟整理（dry_run）</div>'
    );

    // 服务端强制 dry_run=True，前端仅传 dry_run=true 作为冗余信号
    safePost("/admin/selfmodel/retention/dry_run", { dry_run: true }).then(function (r) {
      state.isRunningDryRun = false;
      if (btn) btn.disabled = false;
      if (!r.ok || !r.data || r.data.ok === false) {
        state.dryRunError = (r.data && r.data.error) || r.error || "未知错误";
        renderDryRunResult();
        return;
      }
      state.lastDryRun = r.data;
      renderDryRunResult();
    });
  }

  function bindDryRun() {
    var btn = $("smh-dryrun-btn");
    if (btn && !btn._smhBound) {
      btn.addEventListener("click", runDryRun);
      btn._smhBound = true;
    }
  }

  // ============================================================
  // 数据加载
  // ============================================================
  function fetchHealth(force) {
    if (state.isFetchingHealth && !force) return Promise.resolve();
    state.isFetchingHealth = true;
    state.healthError = null;
    renderRuntimeBadge();

    return safeGet("/admin/selfmodel/health").then(function (r) {
      state.isFetchingHealth = false;
      if (!r.ok || !r.data || r.data.ok === false) {
        state.healthError = (r.data && r.data.error) || r.error || "未知错误";
        state.lastHealth = r.data || null;
      } else {
        state.lastHealth = r.data;
      }
      renderHealthOverview();
      renderIssuesToolbar();
      renderIssues();
      renderPersistence();
      renderRuntimeBadge();
    });
  }

  function fetchRetention(force) {
    if (state.isFetchingRetention && !force) return Promise.resolve();
    state.isFetchingRetention = true;
    state.retentionError = null;

    return safeGet("/admin/selfmodel/retention").then(function (r) {
      state.isFetchingRetention = false;
      if (!r.ok || !r.data || r.data.ok === false) {
        state.retentionError = (r.data && r.data.error) || r.error || "未知错误";
        state.lastRetention = r.data || null;
      } else {
        state.lastRetention = r.data;
      }
      renderCapacity();
    });
  }

  function load(force) {
    state.lastFetchedAt = new Date();
    renderHealthOverview();
    renderIssues();
    renderPersistence();
    renderCapacity();
    renderRuntimeBadge();
    bindDryRun();

    return Promise.all([fetchHealth(force), fetchRetention(force)]);
  }

  // ============================================================
  // 初始化与暴露
  // ============================================================
  function init() {
    if (window.YuyiSelfModelHealth && window.YuyiSelfModelHealth._initialized) return;
    window.YuyiSelfModelHealth = {
      _initialized: true,
      load: load,
      runDryRun: runDryRun,
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
