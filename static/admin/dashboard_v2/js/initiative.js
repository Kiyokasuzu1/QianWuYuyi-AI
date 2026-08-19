/* =============================================================
 * initiative.js —— Initiative 页面渲染
 *
 * 数据源:
 *  - /api/dashboard/v2/initiative/summary
 *  - /api/dashboard/v2/initiative/interests?limit=20&trend=new
 *  - /api/dashboard/v2/initiative/actions?limit=20&status=pending
 *  - /api/dashboard/v2/initiative/filtered?limit=20
 *  - /api/dashboard/v2/initiative/history?limit=50
 *
 * 布局:
 *  - 顶部:Initiative 统计(Interest / Action / Filtered / Trend Mix)
 *  - InterestSignals 列表(可按 trend 过滤)
 *  - PossibleActions 列表(可按 status 过滤)
 *  - Filtered Actions(reason + policy)
 *  - Initiative 变化历史
 * ============================================================= */
(function () {
  "use strict";

  const API = window.YuyiDashboardAPI;

  // ---- DOM helpers ----
  function $(id) { return document.getElementById(id); }
  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = (value === undefined || value === null || value === "") ? "--" : String(value);
  }
  function setHtml(id, html) {
    const el = $(id);
    if (el) el.innerHTML = html;
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function toast(msg) {
    const el = $("yuyiToast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("is-visible");
    setTimeout(() => el.classList.remove("is-visible"), 2400);
  }
  function fmtTime(ts) {
    if (!ts) return "--";
    try {
      return String(ts).replace("T", " ").replace("Z", "").slice(0, 19);
    } catch (e) {
      return String(ts);
    }
  }
  function trendLabel(t) {
    if (t === "new") return "新出现";
    if (t === "rising") return "上升中";
    if (t === "stable") return "稳定";
    if (t === "fading") return "衰退";
    return t || "--";
  }
  function trendClass(t) {
    if (t === "new") return "yit-trend--new";
    if (t === "rising") return "yit-trend--rising";
    if (t === "stable") return "yit-trend--stable";
    if (t === "fading") return "yit-trend--fading";
    return "";
  }
  function statusLabel(s) {
    if (s === "pending") return "候选";
    if (s === "filtered") return "已过滤";
    if (s === "deferred") return "已延后";
    if (s === "discarded") return "已放弃";
    return s || "--";
  }
  function statusClass(s) {
    if (s === "pending") return "yit-status--pending";
    if (s === "filtered") return "yit-status--filtered";
    if (s === "deferred") return "yit-status--deferred";
    if (s === "discarded") return "yit-status--discarded";
    return "";
  }
  function actionTypeLabel(t) {
    if (t === "observe") return "观察";
    if (t === "learn") return "学习";
    if (t === "ask") return "询问";
    if (t === "recommend") return "建议";
    if (t === "remind") return "提醒";
    return t || "--";
  }
  function urgencyLabel(u) {
    if (u === "low") return "低";
    if (u === "normal") return "中";
    if (u === "high") return "高";
    return u || "--";
  }
  function effortLabel(e) {
    if (e === "low") return "轻";
    if (e === "medium") return "中";
    if (e === "high") return "重";
    return e || "--";
  }
  function policyLabel(p) {
    if (p === "low_confidence") return "低置信度";
    if (p === "low_expected_value") return "低预期价值";
    if (p === "repeat_penalty") return "重复抑制";
    if (p === "energy_threshold") return "能量不足";
    if (p === "none") return "无";
    if (p === "unknown") return "未知";
    return p || "--";
  }

  // ---- State ----
  let currentTrend = "";
  let currentActionStatus = "";

  // ---- Summary ----
  async function loadSummary() {
    if (!API || !API.initiativeSummary) return;
    try {
      const r = await API.initiativeSummary();
      const d = r.data || {};
      setText("yit-stat-interest", d.interest_count || 0);
      setText("yit-stat-actions", d.possible_action_count || 0);
      setText("yit-stat-filtered", d.filtered_count || 0);
      setText("yit-last-interest-id", d.last_interest_id || "--");
      setText("yit-last-action-id", d.last_action_id || "--");
      const byTrend = d.by_trend || {};
      setText("yit-trend-new", byTrend.new || 0);
      setText("yit-trend-rising", byTrend.rising || 0);
      setText("yit-trend-stable", byTrend.stable || 0);
      setText("yit-trend-fading", byTrend.fading || 0);
      if (r.fallback) toast("Initiative Summary 不可用");
    } catch (e) {
      // 静默
    }
  }

  // ---- Interests ----
  async function loadInterests() {
    if (!API || !API.initiativeInterests) return;
    try {
      const sel = $("yit-trend-filter");
      const t = (sel && sel.value) || "";
      currentTrend = t || "";
      const r = await API.initiativeInterests(50, currentTrend || null);
      const d = r.data || {};
      const items = d.items || [];
      renderInterests(items);
    } catch (e) {
      // 静默
    }
  }

  function renderInterests(items) {
    const targetId = "yit-interests-list";
    if (!items || !items.length) {
      setHtml(targetId, '<li class="yuyi-list__empty">暂无 InterestSignal</li>');
      return;
    }
    setHtml(
      targetId,
      items.map((it) => {
        const id = esc(it.signal_id || "");
        const cls = trendClass(it.trend);
        const topic = it.topic || "(无主题)";
        const srcCount = (it.source_event_ids || []).length;
        const reflCount = (it.source_reflection_ids || []).length;
        return `<li class="yit-row" data-signal-id="${id}">
          <div class="yit-row__main">
            <span class="yit-trend ${cls}">${esc(trendLabel(it.trend))}</span>
            <span class="yit-row__topic">${esc(topic)}</span>
            <small class="yit-row__meta">
              · strength ${(Number(it.strength) || 0).toFixed(2)}
              · conf ${(Number(it.confidence) || 0).toFixed(2)}
              · sources ${srcCount} evt / ${reflCount} refl
              · ${esc(fmtTime(it.created_at))}
            </small>
            ${it.rationale ? `<p class="yit-row__rationale">${esc(it.rationale)}</p>` : ""}
          </div>
        </li>`;
      }).join("")
    );
  }

  // ---- Actions ----
  async function loadActions() {
    if (!API || !API.initiativeActions) return;
    try {
      const sel = $("yit-action-status-filter");
      const s = (sel && sel.value) || "";
      currentActionStatus = s || "";
      const r = await API.initiativeActions(50, currentActionStatus || null);
      const d = r.data || {};
      const items = d.items || [];
      setText("yit-actions-tag", `${d.total || items.length} 条`);
      renderActions(items);
    } catch (e) {
      // 静默
    }
  }

  function renderActions(items) {
    const targetId = "yit-actions-list";
    if (!items || !items.length) {
      setHtml(targetId, '<li class="yuyi-list__empty">暂无 PossibleAction</li>');
      return;
    }
    setHtml(
      targetId,
      items.map((it) => {
        const id = esc(it.action_id || "");
        const cls = statusClass(it.status);
        const sourceInterest = it.source_interest || "";
        return `<li class="yit-row" data-action-id="${id}">
          <div class="yit-row__main">
            <span class="yit-status ${cls}">${esc(statusLabel(it.status))}</span>
            <span class="yit-row__action-type">${esc(actionTypeLabel(it.action_type))}</span>
            <span class="yit-row__topic">${esc(it.topic || "(无主题)")}</span>
            <small class="yit-row__meta">
              · urgency ${esc(urgencyLabel(it.urgency))}
              · effort ${esc(effortLabel(it.effort_estimate))}
              · ev ${(Number(it.expected_value) || 0).toFixed(2)}
              · conf ${(Number(it.confidence) || 0).toFixed(2)}
              · p ${(Number(it.priority) || 0).toFixed(2)}
              · source ${esc(sourceInterest || "--")}
              · ${esc(fmtTime(it.created_at))}
            </small>
            ${it.rationale ? `<p class="yit-row__rationale">${esc(it.rationale)}</p>` : ""}
          </div>
        </li>`;
      }).join("")
    );
  }

  // ---- Filtered ----
  async function loadFiltered() {
    if (!API || !API.initiativeFiltered) return;
    try {
      const r = await API.initiativeFiltered(50);
      const d = r.data || {};
      const items = d.items || [];
      renderFiltered(items);
    } catch (e) {
      // 静默
    }
  }

  function renderFiltered(items) {
    const targetId = "yit-filtered-list";
    if (!items || !items.length) {
      setHtml(targetId, '<li class="yuyi-list__empty">暂无被过滤的行动</li>');
      return;
    }
    setHtml(
      targetId,
      items.map((it) => {
        const id = esc(it.action_id || "");
        const cls = statusClass(it.status);
        return `<li class="yit-row" data-action-id="${id}">
          <div class="yit-row__main">
            <span class="yit-status ${cls}">${esc(statusLabel(it.status))}</span>
            <span class="yit-row__action-type">${esc(actionTypeLabel(it.action_type))}</span>
            <span class="yit-row__topic">${esc(it.topic || "(无主题)")}</span>
            <small class="yit-row__meta">
              · policy <span class="yit-policy">${esc(policyLabel(it.filter_policy))}</span>
              · ${esc(fmtTime(it.created_at))}
            </small>
            ${it.filter_reason ? `<p class="yit-row__reason">原因:${esc(it.filter_reason)}</p>` : ""}
            ${it.rationale ? `<p class="yit-row__rationale">依据:${esc(it.rationale)}</p>` : ""}
          </div>
        </li>`;
      }).join("")
    );
  }

  // ---- History ----
  async function loadHistory() {
    if (!API || !API.initiativeHistory) return;
    const panel = $("yit-history");
    if (!panel) return;
    setHtml("yit-history", '<li class="yuyi-events__item yuyi-events__item--empty">加载中…</li>');
    try {
      const r = await API.initiativeHistory(50);
      const d = r.data || {};
      const items = d.items || [];
      if (!items.length) {
        setHtml("yit-history", '<li class="yuyi-events__item yuyi-events__item--empty">暂无 Initiative 历史</li>');
        return;
      }
      setHtml(
        "yit-history",
        items.map((it) => {
          const et = it.event_type || "";
          const kind = it.kind || "";
          const subject = it.subject_id || "";
          const topic = it.topic || "";
          const status = it.status || "";
          return `<li class="yit-history__item">
            <div class="yit-history__main">
              <span class="yit-history__kind">${esc(kind)}</span>
              <span class="yit-history__type">${esc(et)}</span>
              <span class="yit-history__subject">${esc(subject)}</span>
            </div>
            <small class="yit-history__meta">
              ${topic ? `<span class="yit-history__topic">${esc(topic)}</span> · ` : ""}
              ${status ? `<span class="yit-history__status">${esc(statusLabel(status))}</span> · ` : ""}
              ${esc(fmtTime(it.timestamp))}
            </small>
          </li>`;
        }).join("")
      );
    } catch (e) {
      setHtml("yit-history", '<li class="yuyi-events__item yuyi-events__item--empty">加载失败</li>');
    }
  }

  // ---- Refresh ----
  async function refresh() {
    await Promise.all([
      loadSummary(),
      loadInterests(),
      loadActions(),
      loadFiltered(),
      loadHistory(),
    ]);
  }

  function init() {
    const root = document.querySelector('[data-page-id="initiative"]');
    if (!root) return;
    refresh();
    const btn = $("yit-refresh");
    if (btn) btn.addEventListener("click", () => { refresh(); toast("已刷新"); });
    const tSel = $("yit-trend-filter");
    if (tSel) tSel.addEventListener("change", () => loadInterests());
    const aSel = $("yit-action-status-filter");
    if (aSel) aSel.addEventListener("change", () => loadActions());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
