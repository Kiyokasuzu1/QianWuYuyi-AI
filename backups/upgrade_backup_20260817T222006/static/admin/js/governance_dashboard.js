/* ============================================
   羽依 Yuyi Console — Phase 5.3 Admin Governance Dashboard
   --------------------------------------------
   职责：
   - 通过 Phase 5.3 GovernanceProvider API 拉取治理快照
   - 渲染 Personality / Memory / Growth 三个治理面板
   - 提供提交 Proposal 的 POST 操作（受控）
   - API 失败时显示 "Offline" 状态而不抛出错误
   - 严格遵循：
       1. 只读视图通过 GET 接口
       2. 受控修改通过 POST 提交 Proposal，不直接改 Authority
   ============================================ */

(function () {
  "use strict";

  // 防止重复初始化
  if (window.YuyiGovernanceDashboard && window.YuyiGovernanceDashboard._initialized) {
    return;
  }

  // ============================================================
  // 配置
  // ============================================================
  var CONFIG = {
    refreshIntervalMs: 15000,       // 治理面板刷新间隔 15s
    endpoints: {
      personality: "/admin/api/admin/governance/personality",
      memory:      "/admin/api/admin/governance/memory",
      growth:      "/admin/api/admin/governance/growth",
      proposals:   "/admin/api/admin/governance/proposals",
      proposalDetail: "/admin/api/admin/governance/proposal/",
      proposePersonality: "/admin/api/admin/governance/personality/propose",
      proposeMemory:      "/admin/api/admin/governance/memory/propose",
      reviewGrowth:       "/admin/api/admin/governance/growth/review",
    },
  };

  var STATUS_LABELS = {
    pending:  { text: "待审批", cls: "gov-status--pending" },
    approved: { text: "已通过", cls: "gov-status--approved" },
    rejected: { text: "已拒绝", cls: "gov-status--rejected" },
    applied:  { text: "已应用", cls: "gov-status--applied" },
    cancelled: { text: "已取消", cls: "gov-status--cancelled" },
  };

  var lastError = null;
  var lastFetchedAt = null;
  var refreshTimer = null;
  var isFetching = false;
  var lastProposals = [];

  // ============================================================
  // API 工具
  // ============================================================
  function fetchJson(path, options) {
    var opts = options || {};
    // 修复：去掉重复的 /admin/api 前缀，避免拼接出 /admin/api/admin/api/...
    return fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (errData) {
          throw new Error(errData && errData.error ? errData.error : "HTTP " + res.status);
        });
      }
      return res.json();
    });
  }

  function safeFetch(label, path) {
    return fetchJson(path).then(function (data) {
      return { ok: true, label: label, data: data };
    }).catch(function (err) {
      console.warn("[GovernanceDashboard] " + label + " fetch failed:", err);
      return { ok: false, label: label, error: err && err.message ? err.message : String(err) };
    });
  }

  function postJson(label, path, body) {
    return fetchJson(path, { method: "POST", body: body }).then(function (data) {
      return { ok: data && data.success !== false, label: label, data: data };
    }).catch(function (err) {
      console.warn("[GovernanceDashboard] " + label + " post failed:", err);
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

  function fmtNumber(v) {
    if (v === null || v === undefined) return "--";
    var n = Number(v);
    if (isNaN(n)) return "--";
    return String(n);
  }

  function fmtPercent(v) {
    if (v === null || v === undefined) return "--";
    var n = Number(v);
    if (isNaN(n)) return "--";
    if (n >= 0 && n <= 1) return Math.round(n * 100) + "%";
    return Math.round(n) + "%";
  }

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  function showToast(msg, kind) {
    // 复用 toast 组件
    try {
      if (window.YuyiToast && typeof window.YuyiToast.show === "function") {
        window.YuyiToast.show(msg, kind || "info");
        return;
      }
    } catch (e) { /* ignore */ }
    // fallback：console + alert
    if (kind === "error") {
      console.error("[GovernanceDashboard]", msg);
    } else {
      console.log("[GovernanceDashboard]", msg);
    }
  }

  // ============================================================
  // 渲染：Personality 治理面板
  // ============================================================
  function renderPersonality(data) {
    var container = $("gov-personality-content");
    if (!container) return;

    if (!data || !data.ok) {
      container.innerHTML = '<div class="rd-offline-note">Personality 治理不可用</div>';
      return;
    }

    var body = data.data || {};
    if (body.available === false) {
      container.innerHTML = '<div class="rd-offline-note">Personality Resolver 未就绪</div>';
      return;
    }

    var traits = (body.data && body.data.traits) || {};
    var state = (body.data && body.data.state) || {};
    var recentProposals = (body.data && body.data.recent_proposals) || [];

    var traitKeys = Object.keys(traits);
    var traitHtml = traitKeys.length === 0
      ? '<div class="rd-recent-empty">暂无 trait 数据</div>'
      : traitKeys.slice(0, 8).map(function (k) {
          var v = traits[k] && traits[k].value;
          return (
            '<div class="gov-trait-item">' +
              '<span class="gov-trait-name">' + escapeHtml(k) + '</span>' +
              '<span class="gov-trait-value">' + fmtPercent(v) + '</span>' +
            '</div>'
          );
        }).join("");

    var stateHtml = Object.keys(state).length === 0
      ? '<div class="rd-recent-empty">GrowthState 不可用</div>'
      : Object.keys(state).slice(0, 5).map(function (k) {
          return (
            '<div class="gov-trait-item">' +
              '<span class="gov-trait-name">' + escapeHtml(k) + '</span>' +
              '<span class="gov-trait-value">' + fmtNumber(state[k]) + '</span>' +
            '</div>'
          );
        }).join("");

    var recentHtml = recentProposals.length === 0
      ? '<div class="rd-recent-empty">暂无历史建议</div>'
      : recentProposals.slice(0, 3).map(function (p) {
          var dims = Object.keys(p.affected_dimensions || {});
          return (
            '<div class="gov-recent-item">' +
              '<span class="gov-recent-id">' + escapeHtml(p.proposal_id) + '</span>' +
              '<span class="gov-recent-dim">' + escapeHtml(dims.join(", ") || "--") + '</span>' +
              '<span class="gov-status ' + (STATUS_LABELS[p.status] ? STATUS_LABELS[p.status].cls : "") + '">' +
                (STATUS_LABELS[p.status] ? STATUS_LABELS[p.status].text : escapeHtml(p.status || "")) +
              '</span>' +
            '</div>'
          );
        }).join("");

    container.innerHTML =
      '<div class="gov-panel-grid">' +
        '<div class="gov-panel-col">' +
          '<div class="gov-col-title">当前 Traits</div>' +
          traitHtml +
        '</div>' +
        '<div class="gov-panel-col">' +
          '<div class="gov-col-title">GrowthState 关联</div>' +
          stateHtml +
        '</div>' +
        '<div class="gov-panel-col">' +
          '<div class="gov-col-title">最近人格相关建议</div>' +
          recentHtml +
        '</div>' +
      '</div>';
  }

  // ============================================================
  // 渲染：Memory 治理面板
  // ============================================================
  function renderMemory(data) {
    var container = $("gov-memory-content");
    if (!container) return;

    if (!data || !data.ok) {
      container.innerHTML = '<div class="rd-offline-note">Memory 治理不可用</div>';
      return;
    }

    var body = data.data || {};
    if (body.available === false) {
      container.innerHTML = '<div class="rd-offline-note">MemoryStore 未就绪</div>';
      return;
    }

    var inner = body.data || {};
    var total = inner.total_count || 0;
    var important = inner.important_count || 0;
    var byType = inner.by_type || {};
    var recent = inner.recent || [];
    var sourceEventIds = inner.source_event_ids || [];

    var typeHtml = Object.keys(byType).length === 0
      ? '<div class="rd-recent-empty">暂无类型数据</div>'
      : Object.keys(byType).map(function (k) {
          return (
            '<div class="gov-trait-item">' +
              '<span class="gov-trait-name">' + escapeHtml(k) + '</span>' +
              '<span class="gov-trait-value">' + fmtNumber(byType[k]) + '</span>' +
            '</div>'
          );
        }).join("");

    var recentHtml = recent.length === 0
      ? '<div class="rd-recent-empty">暂无最近记忆</div>'
      : recent.slice(0, 5).map(function (m) {
          var content = m.content || m.text || JSON.stringify(m);
          if (typeof content === "string" && content.length > 50) {
            content = content.substring(0, 50) + "...";
          }
          var mid = m.id || m.memory_id || "";
          return (
            '<div class="gov-mem-item" data-memory-id="' + escapeHtml(mid) + '">' +
              '<span class="gov-mem-id">' + escapeHtml(mid) + '</span>' +
              '<span class="gov-mem-content">' + escapeHtml(content) + '</span>' +
            '</div>'
          );
        }).join("");

    var sourceHtml = sourceEventIds.length === 0
      ? '<div class="rd-recent-empty">暂无来源事件</div>'
      : sourceEventIds.slice(0, 5).map(function (sid) {
          return '<div class="gov-trait-item"><span class="gov-trait-name">' + escapeHtml(sid) + '</span></div>';
        }).join("");

    container.innerHTML =
      '<div class="gov-panel-grid">' +
        '<div class="gov-panel-col">' +
          '<div class="gov-col-title">总记忆 / 重要</div>' +
          '<div class="gov-trait-item"><span class="gov-trait-name">总记忆数</span><span class="gov-trait-value">' + fmtNumber(total) + '</span></div>' +
          '<div class="gov-trait-item"><span class="gov-trait-name">重要记忆</span><span class="gov-trait-value">' + fmtNumber(important) + '</span></div>' +
        '</div>' +
        '<div class="gov-panel-col">' +
          '<div class="gov-col-title">类型分布</div>' +
          typeHtml +
        '</div>' +
        '<div class="gov-panel-col">' +
          '<div class="gov-col-title">最近记忆 (点击记忆 ID 自动填入)</div>' +
          recentHtml +
        '</div>' +
        '<div class="gov-panel-col">' +
          '<div class="gov-col-title">来源事件</div>' +
          sourceHtml +
        '</div>' +
      '</div>';

    // 绑定 memory 点击 → 自动填入 memory_id
    var memItems = container.querySelectorAll(".gov-mem-item");
    for (var i = 0; i < memItems.length; i++) {
      memItems[i].addEventListener("click", function () {
        var mid = this.getAttribute("data-memory-id");
        var input = $("gov-memory-id-input");
        if (input && mid) {
          input.value = mid;
          showToast("已填入 memory_id: " + mid, "info");
        }
      });
    }
  }

  // ============================================================
  // 渲染：Growth Proposal 面板
  // ============================================================
  function renderGrowth(data) {
    var summaryEl = $("gov-growth-summary");
    var listEl = $("gov-proposals-list");
    if (!summaryEl || !listEl) return;

    if (!data || !data.ok) {
      summaryEl.innerHTML = '<div class="rd-offline-note">Growth 治理不可用</div>';
      listEl.innerHTML = "";
      return;
    }

    var body = data.data || {};
    if (body.available === false) {
      summaryEl.innerHTML = '<div class="rd-offline-note">GrowthState 未就绪</div>';
      listEl.innerHTML = "";
      return;
    }

    var inner = body.data || {};
    var metrics = inner.metrics || {};
    var pending = inner.pending || [];
    var approved = inner.approved || [];
    var rejected = inner.rejected || [];
    var applied = inner.applied || [];
    var byType = inner.by_type || {};
    var total = inner.total || (pending.length + approved.length + rejected.length + applied.length);

    // 摘要统计
    summaryEl.innerHTML =
      '<div class="gov-summary-grid">' +
        '<div class="gov-summary-cell">' +
          '<div class="gov-cell-label">总 Proposal</div>' +
          '<div class="gov-cell-value">' + fmtNumber(total) + '</div>' +
        '</div>' +
        '<div class="gov-summary-cell gov-summary-cell--pending">' +
          '<div class="gov-cell-label">待审批</div>' +
          '<div class="gov-cell-value">' + fmtNumber(pending.length) + '</div>' +
        '</div>' +
        '<div class="gov-summary-cell gov-summary-cell--approved">' +
          '<div class="gov-cell-label">已通过</div>' +
          '<div class="gov-cell-value">' + fmtNumber(approved.length) + '</div>' +
        '</div>' +
        '<div class="gov-summary-cell gov-summary-cell--rejected">' +
          '<div class="gov-cell-label">已拒绝</div>' +
          '<div class="gov-cell-value">' + fmtNumber(rejected.length) + '</div>' +
        '</div>' +
      '</div>';

    // Proposal 列表
    var items = pending.concat(applied).concat(approved).concat(rejected);
    lastProposals = items;

    if (items.length === 0) {
      listEl.innerHTML = '<div class="rd-recent-empty">暂无 Proposal</div>';
      return;
    }

    var listHtml = items.slice(0, 10).map(function (p) {
      var statusInfo = STATUS_LABELS[p.status] || { text: p.status || "", cls: "" };
      var dims = p.affected_dimensions || {};
      var dimKeys = Object.keys(dims);
      var dimStr = dimKeys.length === 0
        ? (p.proposal_type === "identity" ? "记忆处理" : "--")
        : dimKeys.map(function (k) {
            var d = dims[k];
            return escapeHtml(k) + " (Δ " + (typeof d === "number" ? d.toFixed(4) : d) + ")";
          }).join(", ");
      var ts = p.timestamp || "";
      var reason = p.reason || "";
      var reviewer = p.reviewer_id || "";
      var reviewedAt = p.reviewed_at || "";
      var reviewInfo = reviewer
        ? '<div class="gov-prop-reviewer">审查人: ' + escapeHtml(reviewer) + (reviewedAt ? ' · ' + escapeHtml(reviewedAt) : '') + '</div>'
        : '';

      var actionBtns = '';
      if (p.status === "pending") {
        actionBtns =
          '<div class="gov-prop-actions">' +
            '<button class="gov-btn gov-btn--small gov-btn--ok" data-proposal-id="' + escapeHtml(p.proposal_id) + '" data-action="approve">通过</button>' +
            '<button class="gov-btn gov-btn--small gov-btn--bad" data-proposal-id="' + escapeHtml(p.proposal_id) + '" data-action="reject">拒绝</button>' +
          '</div>';
      }

      return (
        '<div class="gov-prop-card ' + (p.is_governance ? 'gov-prop-card--gov' : '') + '">' +
          '<div class="gov-prop-header">' +
            '<span class="gov-prop-id">' + escapeHtml(p.proposal_id) + '</span>' +
            '<span class="gov-status ' + statusInfo.cls + '">' + escapeHtml(statusInfo.text) + '</span>' +
          '</div>' +
          '<div class="gov-prop-dim">' + dimStr + '</div>' +
          (reason ? '<div class="gov-prop-reason">原因: ' + escapeHtml(reason) + '</div>' : '') +
          (ts ? '<div class="gov-prop-time">创建: ' + escapeHtml(ts) + '</div>' : '') +
          reviewInfo +
          actionBtns +
        '</div>'
      );
    }).join("");

    listEl.innerHTML = listHtml;

    // 绑定审查按钮
    var btns = listEl.querySelectorAll(".gov-prop-actions .gov-btn");
    for (var j = 0; j < btns.length; j++) {
      btns[j].addEventListener("click", function () {
        var pid = this.getAttribute("data-proposal-id");
        var act = this.getAttribute("data-action");
        if (pid && act) {
          onReviewProposal(pid, act);
        }
      });
    }
  }

  // ============================================================
  // 操作：提交人格变更建议
  // ============================================================
  function onProposePersonality() {
    var traitEl = $("gov-personality-trait-input");
    var deltaEl = $("gov-personality-delta-input");
    var reasonEl = $("gov-personality-reason-input");
    if (!traitEl || !deltaEl) return;

    var trait = (traitEl.value || "").trim();
    var deltaStr = (deltaEl.value || "").trim();
    var reason = reasonEl ? (reasonEl.value || "").trim() : "";

    if (!trait) {
      showToast("请填写特质名", "error");
      return;
    }
    if (!deltaStr) {
      showToast("请填写调整量", "error");
      return;
    }
    var delta = Number(deltaStr);
    if (isNaN(delta)) {
      showToast("调整量必须是数字", "error");
      return;
    }

    postJson(
      "propose-personality",
      CONFIG.endpoints.proposePersonality,
      { trait: trait, delta: delta, reason: reason, confidence: 0.5, priority: "medium" }
    ).then(function (res) {
      if (res && res.ok) {
        showToast("人格变更建议已提交: " + (res.data && res.data.proposal_id ? res.data.proposal_id : ""), "success");
        if (traitEl) traitEl.value = "";
        if (deltaEl) deltaEl.value = "";
        if (reasonEl) reasonEl.value = "";
        refreshAll();
      } else {
        showToast("提交失败: " + (res && res.error ? res.error : "未知错误"), "error");
      }
    });
  }

  // ============================================================
  // 操作：提交记忆处理申请
  // ============================================================
  function onProposeMemory() {
    var idEl = $("gov-memory-id-input");
    var actEl = $("gov-memory-action-input");
    var targetEl = $("gov-memory-target-input");
    var reasonEl = $("gov-memory-reason-input");
    if (!idEl || !actEl) return;

    var mid = (idEl.value || "").trim();
    var act = actEl.value;
    var reason = reasonEl ? (reasonEl.value || "").trim() : "";
    var target = targetEl ? (targetEl.value || "").trim() : "";

    if (!mid) {
      showToast("请填写 memory_id", "error");
      return;
    }
    if (act === "merge" && !target) {
      showToast("merge 操作必须填写目标 memory_id", "error");
      return;
    }

    var body = { memory_id: mid, action: act, reason: reason };
    if (act === "merge" && target) {
      body.target_memory_id = target;
    }

    postJson("propose-memory", CONFIG.endpoints.proposeMemory, body).then(function (res) {
      if (res && res.ok) {
        showToast("记忆处理申请已提交: " + (res.data && res.data.proposal_id ? res.data.proposal_id : ""), "success");
        if (idEl) idEl.value = "";
        if (reasonEl) reasonEl.value = "";
        if (targetEl) targetEl.value = "";
        refreshAll();
      } else {
        showToast("提交失败: " + (res && res.error ? res.error : "未知错误"), "error");
      }
    });
  }

  // ============================================================
  // 操作：审查 Proposal
  // ============================================================
  function onReviewProposal(proposalId, action) {
    if (!proposalId || !action) return;
    var label = action === "approve" ? "通过" : "拒绝";
    var reason = window.prompt("请输入审查原因（" + label + "）:", "");
    if (reason === null) return; // 用户取消

    postJson("review-growth", CONFIG.endpoints.reviewGrowth, {
      proposal_id: proposalId,
      action: action,
      reason: reason || "",
    }).then(function (res) {
      if (res && res.ok) {
        showToast("Proposal " + action + " 成功: " + proposalId, "success");
        refreshAll();
      } else {
        showToast("审查失败: " + (res && res.error ? res.error : "未知错误"), "error");
      }
    });
  }

  // ============================================================
  // 全局 Offline 状态
  // ============================================================
  function renderGlobalOffline() {
    setHTML("gov-personality-content", '<div class="rd-offline-note">Offline</div>');
    setHTML("gov-memory-content",      '<div class="rd-offline-note">Offline</div>');
    setHTML("gov-growth-summary",      '<div class="rd-offline-note">Offline</div>');
    setHTML("gov-proposals-list",      '<div class="rd-offline-note">Offline</div>');
    setText("gov-last-updated", "Offline");
  }

  // ============================================================
  // 主刷新流程
  // ============================================================
  function refreshAll() {
    if (isFetching) return;
    isFetching = true;

    var p1 = safeFetch("gov-personality", CONFIG.endpoints.personality);
    var p2 = safeFetch("gov-memory",      CONFIG.endpoints.memory);
    var p3 = safeFetch("gov-growth",      CONFIG.endpoints.growth);

    Promise.all([p1, p2, p3]).then(function (results) {
      isFetching = false;

      var personality = results[0];
      var memory      = results[1];
      var growth      = results[2];

      var anyOk = results.some(function (r) { return r.ok; });
      if (!anyOk) {
        lastError = "All endpoints failed";
        renderGlobalOffline();
        return;
      }

      renderPersonality(personality);
      renderMemory(memory);
      renderGrowth(growth);

      lastError = null;
      lastFetchedAt = new Date();
      setText("gov-last-updated", "更新于 " + lastFetchedAt.toLocaleTimeString());
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
  var YuyiGovernanceDashboard = {
    _initialized: true,
    refresh: refreshAll,
    start: startAutoRefresh,
    stop: stopAutoRefresh,
    config: CONFIG,
    proposePersonality: onProposePersonality,
    proposeMemory: onProposeMemory,
    reviewProposal: onReviewProposal,
    getLastProposals: function () { return lastProposals; },
    isOnline: function () { return lastError === null && lastFetchedAt !== null; },
  };

  // ============================================================
  // DOMContentLoaded 初始化
  // ============================================================
  function init() {
    var refreshBtn = $("gov-refresh-btn");
    if (refreshBtn) {
      refreshBtn.addEventListener("click", refreshAll);
    }
    var propPersonalityBtn = $("gov-personality-propose-btn");
    if (propPersonalityBtn) {
      propPersonalityBtn.addEventListener("click", onProposePersonality);
    }
    var propMemoryBtn = $("gov-memory-propose-btn");
    if (propMemoryBtn) {
      propMemoryBtn.addEventListener("click", onProposeMemory);
    }

    refreshAll();
    startAutoRefresh();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.YuyiGovernanceDashboard = YuyiGovernanceDashboard;
})();
