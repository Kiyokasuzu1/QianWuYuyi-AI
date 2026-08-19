/* =============================================================
 * goal.js —— Goal 页面渲染
 *
 * 数据源:
 *  - /api/dashboard/v2/goal/summary
 *  - /api/dashboard/v2/goal/current
 *  - /api/dashboard/v2/goal/list?limit=20&status=active
 *  - /api/dashboard/v2/goal/<id>
 *  - /api/dashboard/v2/goal/history?limit=50
 *
 * 布局:
 *  - 顶部:Goal 统计(Total / Active / Paused / Completed)
 *  - 当前活跃目标卡片
 *  - 左侧:Goal 列表(可按状态过滤)
 *  - 中间:Goal 详情
 *  - 右侧:Goal 变化历史
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
  function statusLabel(s) {
    if (s === "candidate") return "候选";
    if (s === "active") return "活跃";
    if (s === "paused") return "暂停";
    if (s === "completed") return "完成";
    if (s === "abandoned") return "放弃";
    return s || "--";
  }
  function statusClass(s) {
    if (s === "active") return "ygl-status--active";
    if (s === "paused") return "ygl-status--paused";
    if (s === "completed") return "ygl-status--completed";
    if (s === "abandoned") return "ygl-status--abandoned";
    if (s === "candidate") return "ygl-status--candidate";
    return "";
  }
  function typeLabel(t) {
    if (t === "personal_growth") return "个人成长";
    if (t === "learning") return "学习";
    if (t === "creative") return "创造";
    if (t === "relationship") return "关系";
    if (t === "exploration") return "探索";
    return t || "--";
  }

  // ---- State ----
  let currentGoalId = "";
  let currentStatus = "";

  // ---- Summary ----
  async function loadSummary() {
    if (!API || !API.goalSummary) return;
    try {
      const r = await API.goalSummary();
      const d = r.data || {};
      setText("ygl-stat-total", d.total_count || 0);
      setText("ygl-stat-active", d.active_count || 0);
      const byStatus = d.by_status || {};
      setText("ygl-stat-paused", byStatus.paused || 0);
      setText("ygl-stat-completed", byStatus.completed || 0);
      setText("ygl-last-id", d.last_goal_id || "--");
      setText("ygl-last-at", d.last_goal_at ? fmtTime(d.last_goal_at) : "--");
      if (r.fallback) toast("Goal Summary 不可用");
    } catch (e) {
      // 静默
    }
  }

  // ---- Current ----
  async function loadCurrent() {
    if (!API || !API.goalCurrent) return;
    const panel = $("ygl-current");
    if (!panel) return;
    setHtml("ygl-current", '<p class="yuyi-empty">加载中…</p>');
    try {
      const r = await API.goalCurrent();
      const d = r.data || {};
      const cur = d.current;
      if (!cur) {
        const reason = d.fallback_reason || "no_active_goal";
        setHtml("ygl-current", `<p class="yuyi-empty">暂无活跃目标(原因:${esc(reason)})</p>`);
        return;
      }
      const cls = statusClass(cur.status);
      setHtml(
        "ygl-current",
        `<div class="ygl-current__head">
          <span class="ygl-status ${cls}">${esc(statusLabel(cur.status))}</span>
          <span class="ygl-current__type">${esc(typeLabel(cur.goal_type))}</span>
        </div>
        <h3 class="ygl-current__title">${esc(cur.title || "(无标题)")}</h3>
        ${cur.description ? `<p class="ygl-current__desc">${esc(cur.description)}</p>` : ""}
        <dl class="yuyi-stat">
          <div><dt>priority</dt><dd>${(Number(cur.priority) || 0).toFixed(2)}</dd></div>
          <div><dt>importance</dt><dd>${(Number(cur.importance) || 0).toFixed(2)}</dd></div>
          <div><dt>confidence</dt><dd>${(Number(cur.confidence) || 0).toFixed(2)}</dd></div>
          <div><dt>created_at</dt><dd>${esc(fmtTime(cur.created_at))}</dd></div>
        </dl>
        ${cur.reason ? `<p class="ygl-current__reason"><small>原因:${esc(cur.reason)}</small></p>` : ""}`
      );
    } catch (e) {
      setHtml("ygl-current", '<p class="yuyi-empty">加载失败</p>');
    }
  }

  // ---- List ----
  async function loadList() {
    if (!API || !API.goalList) return;
    try {
      const sel = $("ygl-status-filter");
      const s = (sel && sel.value) || "";
      currentStatus = s || "";
      const r = await API.goalList(50, currentStatus || null);
      const d = r.data || {};
      const items = d.items || [];
      renderList(items);
    } catch (e) {
      // 静默
    }
  }

  function renderList(items) {
    const targetId = "ygl-list";
    if (!items || !items.length) {
      setHtml(targetId, '<li class="yuyi-list__empty">暂无 Goal 记录</li>');
      return;
    }
    setHtml(
      targetId,
      items.map((it) => {
        const id = esc(it.goal_id || "");
        const cls = statusClass(it.status);
        const title = it.title || "(无标题)";
        return `<li class="ygl-row" data-goal-id="${id}">
          <div class="ygl-row__main">
            <span class="ygl-row__title">${esc(title)}</span>
            <small class="ygl-row__meta">
              <span class="ygl-status ${cls}">${esc(statusLabel(it.status))}</span>
              · ${esc(typeLabel(it.goal_type))}
              · p ${(Number(it.priority) || 0).toFixed(2)}
              · conf ${(Number(it.confidence) || 0).toFixed(2)}
              · ${esc(fmtTime(it.created_at))}
            </small>
          </div>
        </li>`;
      }).join("")
    );
    // 点击查看详情
    document.querySelectorAll(`#${targetId} .ygl-row`).forEach((el) => {
      el.addEventListener("click", () => {
        const gid = el.getAttribute("data-goal-id");
        if (gid) {
          currentGoalId = gid;
          loadDetail(gid);
          highlight(gid);
        }
      });
    });
  }

  function highlight(gid) {
    document.querySelectorAll("#ygl-list .ygl-row").forEach((el) => {
      el.classList.toggle("is-active", el.getAttribute("data-goal-id") === gid);
    });
  }

  // ---- Detail ----
  async function loadDetail(gid) {
    if (!API || !API.goalDetail) return;
    const panel = $("ygl-detail");
    if (!panel) return;
    setHtml("ygl-detail", '<p class="yuyi-empty">加载中…</p>');
    try {
      const r = await API.goalDetail(gid);
      const d = r.data || {};
      const g = d.goal;
      if (!g) {
        setHtml("ygl-detail", '<p class="yuyi-empty">未找到该 Goal</p>');
        return;
      }
      const cls = statusClass(g.status);
      const plan = g.plan;
      const planHtml = plan ? `
        <div class="ygl-detail__section">
          <h4>Plan</h4>
          <dl class="yuyi-stat">
            <div><dt>plan_id</dt><dd>${esc(plan.plan_id || "--")}</dd></div>
            <div><dt>step_count</dt><dd>${plan.step_count || 0}</dd></div>
            <div><dt>progress</dt><dd>${(Number(plan.progress) || 0).toFixed(2)}</dd></div>
            <div><dt>title</dt><dd>${esc(plan.title || "--")}</dd></div>
          </dl>
        </div>` : "";
      setHtml(
        "ygl-detail",
        `<div class="ygl-detail__head">
          <span class="ygl-status ${cls}">${esc(statusLabel(g.status))}</span>
          <span class="ygl-detail__id">${esc(g.goal_id || "")}</span>
        </div>
        <h3 class="ygl-detail__title">${esc(g.title || "(无标题)")}</h3>
        ${g.description ? `<p class="ygl-detail__desc">${esc(g.description)}</p>` : ""}
        <dl class="yuyi-stat">
          <div><dt>type</dt><dd>${esc(typeLabel(g.goal_type))}</dd></div>
          <div><dt>priority</dt><dd>${(Number(g.priority) || 0).toFixed(2)}</dd></div>
          <div><dt>importance</dt><dd>${(Number(g.importance) || 0).toFixed(2)}</dd></div>
          <div><dt>confidence</dt><dd>${(Number(g.confidence) || 0).toFixed(2)}</dd></div>
          <div><dt>created_at</dt><dd>${esc(fmtTime(g.created_at))}</dd></div>
          <div><dt>updated_at</dt><dd>${esc(fmtTime(g.updated_at))}</dd></div>
          <div><dt>source_events</dt><dd>${g.source_event_count || (g.source_event_ids ? g.source_event_ids.length : 0)}</dd></div>
          <div><dt>source_desires</dt><dd>${g.source_desire_count || (g.source_desire_ids ? g.source_desire_ids.length : 0)}</dd></div>
        </dl>
        ${g.reason ? `<div class="ygl-detail__section">
          <h4>Reason</h4>
          <p>${esc(g.reason)}</p>
        </div>` : ""}
        ${g.source_event_ids && g.source_event_ids.length ? `<div class="ygl-detail__section">
          <h4>Source Events</h4>
          <ul class="ygl-tag-list">${g.source_event_ids.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
        </div>` : ""}
        ${planHtml}`
      );
    } catch (e) {
      setHtml("ygl-detail", '<p class="yuyi-empty">加载失败</p>');
    }
  }

  // ---- History ----
  async function loadHistory() {
    if (!API || !API.goalHistory) return;
    const panel = $("ygl-history");
    if (!panel) return;
    setHtml("ygl-history", '<li class="yuyi-events__item yuyi-events__item--empty">加载中…</li>');
    try {
      const r = await API.goalHistory(50);
      const d = r.data || {};
      const items = d.items || [];
      if (!items.length) {
        setHtml("ygl-history", '<li class="yuyi-events__item yuyi-events__item--empty">暂无 Goal 历史</li>');
        return;
      }
      setHtml(
        "ygl-history",
        items.map((it) => {
          const et = it.event_type || "";
          const goalId = it.goal_id || "";
          const life = it.lifecycle || "";
          const reason = it.reason || "";
          return `<li class="ygl-history__item">
            <div class="ygl-history__main">
              <span class="ygl-history__type">${esc(et)}</span>
              <span class="ygl-history__goal">${esc(goalId)}</span>
            </div>
            <small class="ygl-history__meta">
              ${life ? `<span class="ygl-history__lifecycle">${esc(life)}</span> · ` : ""}
              ${esc(fmtTime(it.timestamp))}
            </small>
            ${reason ? `<p class="ygl-history__reason">${esc(reason)}</p>` : ""}
          </li>`;
        }).join("")
      );
    } catch (e) {
      setHtml("ygl-history", '<li class="yuyi-events__item yuyi-events__item--empty">加载失败</li>');
    }
  }

  // ---- Refresh ----
  async function refresh() {
    await Promise.all([loadSummary(), loadCurrent(), loadList(), loadHistory()]);
  }

  function init() {
    const root = document.querySelector('[data-page-id="goal"]');
    if (!root) return;
    refresh();
    const btn = $("ygl-refresh");
    if (btn) btn.addEventListener("click", () => { refresh(); toast("已刷新"); });
    const sel = $("ygl-status-filter");
    if (sel) sel.addEventListener("change", () => loadList());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
