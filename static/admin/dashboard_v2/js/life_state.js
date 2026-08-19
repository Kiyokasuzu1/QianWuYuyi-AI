/* =============================================================
 * life_state.js —— Overview 内嵌 LifeStateSummary 渲染
 *
 * 数据源:
 *  - GET /api/dashboard/v2/life-state/summary
 *
 * 关键设计:
 *  - Provider 不生成 headline,前端根据结构化 data 渲染可读文本
 *  - 默认简洁展示,点击"可信度"按钮展开 trace
 *  - 纯只读,无写操作
 * ============================================================= */
(function (global) {
  "use strict";

  const API = global.YuyiDashboardAPI;

  // ---- DOM helpers ----
  function $(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const el = $(id);
    if (!el) return;
    el.textContent = value === undefined || value === null || value === "" ? "--" : String(value);
  }

  function setHtml(id, html) {
    const el = $(id);
    if (!el) return;
    el.innerHTML = html;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtTs(s) {
    if (!s) return "--";
    try {
      return String(s).replace("T", " ").replace("Z", "").slice(0, 19);
    } catch (e) {
      return "--";
    }
  }

  // ---- 渲染 ----
  function renderFocusCard(data) {
    const focus = data && data.current_focus ? data.current_focus : {};
    const topic = focus.topic || "暂无明显焦点";
    const trend = focus.trend || "stable";
    const score = (focus.score || 0).toFixed(2);
    const reason = focus.selection_reason || "no_interests";

    setText("yuyiFocusTopic", topic);
    setText("yuyiFocusScore", score);
    setText("yuyiFocusTrend", trend);
    setText("yuyiFocusReason", reason);

    // 焦点卡右侧标签(色调)
    const tagEl = $("yuyiFocusTag");
    if (tagEl) {
      tagEl.classList.remove("yuyi-tag--rising", "yuyi-tag--stable", "yuyi-tag--fading", "yuyi-tag--new");
      if (trend === "rising") tagEl.classList.add("yuyi-tag--rising");
      else if (trend === "fading") tagEl.classList.add("yuyi-tag--fading");
      else if (trend === "new") tagEl.classList.add("yuyi-tag--new");
      else tagEl.classList.add("yuyi-tag--stable");
    }
  }

  function renderEmotionCard(data) {
    const em = data && data.emotion ? data.emotion : {};
    const name = em.name || "--";
    setText("yuyiEmotionName", name);
    setText("yuyiEmotionIntensity", (em.intensity || 0).toFixed(2));
    setText("yuyiEmotionValence", (em.valence || 0).toFixed(2));
    setText("yuyiEmotionArousal", (em.arousal || 0).toFixed(2));
    setText("yuyiEmotionSource", em.source || "none");

    // 因果证据条数
    const c = em.causality || {};
    const eventCount = (c.event_ids || []).length;
    const memCount = (c.memory_ids || []).length;
    setText("yuyiEmotionCausality", `事件 ${eventCount} · 记忆 ${memCount}`);
  }

  function renderGrowthCard(data) {
    const g = data && data.growth ? data.growth : {};
    setText("yuyiGrowthStage", g.stage || "--");
    setText("yuyiGrowthScore", (g.stage_score || 0).toFixed(2));
    const m = g.last_milestone;
    if (m && m.summary) {
      setText("yuyiGrowthMilestone", m.summary);
      setText("yuyiGrowthMilestoneTime", fmtTs(m.timestamp));
    } else {
      setText("yuyiGrowthMilestone", "暂无里程碑");
      setText("yuyiGrowthMilestoneTime", "--");
    }
  }

  function renderActiveGoalsCard(data) {
    const list = (data && data.active_goals) || [];
    const wrap = $("yuyiActiveGoals");
    if (!wrap) return;
    if (!list.length) {
      wrap.innerHTML = '<p class="yuyi-empty">暂无活跃目标</p>';
      return;
    }
    wrap.innerHTML = list
      .map((g) => {
        const progress = ((g.progress || 0) * 100).toFixed(0);
        return `<li class="yuyi-list__row">
          <span class="yuyi-list__title">${esc(g.title || g.id)}</span>
          <span class="yuyi-list__tag">${esc(g.status || "active")}</span>
          <span class="yuyi-list__metric">${progress}%</span>
        </li>`;
      })
      .join("");
  }

  function renderRecentMemoriesCard(data) {
    const list = (data && data.recent_memories) || [];
    const wrap = $("yuyiRecentMemories");
    if (!wrap) return;
    if (!list.length) {
      wrap.innerHTML = '<p class="yuyi-empty">暂无近期记忆</p>';
      return;
    }
    wrap.innerHTML = list
      .map((m) => {
        return `<li class="yuyi-list__row">
          <span class="yuyi-list__title" title="${esc(m.summary || "")}">${esc((m.summary || "").slice(0, 30))}</span>
          <span class="yuyi-list__metric">imp ${(m.importance || 0).toFixed(2)}</span>
          <span class="yuyi-list__time">${fmtTs(m.timestamp)}</span>
        </li>`;
      })
      .join("");
  }

  function renderRecentReflectionsCard(data) {
    const list = (data && data.recent_reflections) || [];
    const wrap = $("yuyiRecentReflections");
    if (!wrap) return;
    if (!list.length) {
      wrap.innerHTML = '<p class="yuyi-empty">暂无近期反思</p>';
      return;
    }
    wrap.innerHTML = list
      .map((r) => {
        return `<li class="yuyi-list__row">
          <span class="yuyi-list__title" title="${esc(r.insight || "")}">${esc((r.topic || r.id || "").slice(0, 30))}</span>
          <span class="yuyi-list__time">${fmtTs(r.timestamp)}</span>
        </li>`;
      })
      .join("");
  }

  function renderInterestsCard(data) {
    const list = (data && data.interests) || [];
    const wrap = $("yuyiInterestsList");
    if (!wrap) return;
    if (!list.length) {
      wrap.innerHTML = '<p class="yuyi-empty">暂无兴趣</p>';
      return;
    }
    wrap.innerHTML = list
      .map((i) => {
        const strength = (i.strength || 0).toFixed(2);
        return `<li class="yuyi-list__row">
          <span class="yuyi-list__title">${esc(i.topic || "")}</span>
          <span class="yuyi-list__tag yuyi-tag--${esc(i.trend || "stable")}">${esc(i.trend || "stable")}</span>
          <span class="yuyi-list__metric">${strength}</span>
        </li>`;
      })
      .join("");
  }

  function renderRisksCard(data) {
    const list = (data && data.risks) || [];
    const wrap = $("yuyiRisksList");
    if (!wrap) return;
    if (!list.length) {
      wrap.innerHTML = '<p class="yuyi-empty yuyi-empty--ok">当前无风险信号</p>';
      return;
    }
    wrap.innerHTML = list
      .map((r) => {
        return `<li class="yuyi-list__row yuyi-list__row--risk yuyi-list__row--${esc(r.level || "low")}">
          <span class="yuyi-list__tag yuyi-tag--risk-${esc(r.level || "low")}">${esc((r.level || "low").toUpperCase())}</span>
          <span class="yuyi-list__title" title="${esc(r.reason || "")}">${esc(r.reason || "")}</span>
        </li>`;
      })
      .join("");
  }

  function renderStatsCard(data) {
    const s = (data && data.stats) || {};
    setText("yuyiStatInterest", s.interest_count || 0);
    setText("yuyiStatMemory", s.memory_count || 0);
    setText("yuyiStatGoal", s.active_goal_count || 0);
    setText("yuyiStatReflection", s.reflection_count_7d || 0);
  }

  // ---- 可信度 trace 渲染 ----
  function renderTrace(envelope) {
    const wrap = $("yuyiTracePanel");
    if (!wrap) return;
    const trace = (envelope && envelope.trace) || {};
    const sources = trace.sources || [];
    if (!sources.length) {
      wrap.innerHTML = '<p class="yuyi-empty">无证据来源</p>';
      return;
    }
    const rows = sources
      .map((s) => {
        const okTag = s.ok ? '<span class="yuyi-tag yuyi-tag--stable">OK</span>' : '<span class="yuyi-tag yuyi-tag--fading">FAIL</span>';
        const dur = (s.duration_ms || 0) + "ms";
        return `<li class="yuyi-trace__row">
          <span class="yuyi-trace__provider">${esc(s.provider || "")}</span>
          <span class="yuyi-trace__method">.${esc(s.method || "")}</span>
          ${okTag}
          <span class="yuyi-trace__dur">${esc(dur)}</span>
        </li>`;
      })
      .join("");
    const evCount = trace.evidence_count || 0;
    const gen = trace.generated_at || "--";
    wrap.innerHTML = `
      <header class="yuyi-trace__header">
        <span>证据来源: <strong>${evCount}</strong></span>
        <span>生成于: ${esc(gen)}</span>
      </header>
      <ul class="yuyi-trace__list">${rows}</ul>
    `;
  }

  // ---- 顶层渲染 ----
  function renderConfidenceBadge(confidence) {
    const el = $("yuyiConfidenceBadge");
    if (!el) return;
    const c = Math.max(0, Math.min(1, confidence || 0));
    el.textContent = "可信度 " + (c * 100).toFixed(0) + "%";
    el.classList.remove("yuyi-confidence--high", "yuyi-confidence--mid", "yuyi-confidence--low");
    if (c >= 0.8) el.classList.add("yuyi-confidence--high");
    else if (c >= 0.4) el.classList.add("yuyi-confidence--mid");
    else el.classList.add("yuyi-confidence--low");
  }

  function renderFallback(reason) {
    const el = $("yuyiLifeStateFallback");
    if (!el) return;
    if (reason) {
      el.style.display = "";
      el.textContent = "数据不可用: " + reason;
    } else {
      el.style.display = "none";
    }
  }

  async function loadAll() {
    if (!API || typeof API.lifeStateSummary !== "function") {
      console.warn("YuyiDashboardAPI.lifeStateSummary 不可用");
      return;
    }
    try {
      const r = await API.lifeStateSummary();
      // 新 envelope 结构:{ok, data, trace, confidence, fallback, fallback_reason, timestamp}
      const envelope = r && r.data ? r.data : r;
      if (!envelope || typeof envelope !== "object") return;
      const data = envelope.data || {};
      renderFocusCard(data);
      renderEmotionCard(data);
      renderGrowthCard(data);
      renderActiveGoalsCard(data);
      renderRecentMemoriesCard(data);
      renderRecentReflectionsCard(data);
      renderInterestsCard(data);
      renderRisksCard(data);
      renderStatsCard(data);
      renderTrace(envelope);
      renderConfidenceBadge(envelope.confidence);
      renderFallback(envelope.fallback ? envelope.fallback_reason : null);
    } catch (e) {
      console.warn("LifeState loadAll 失败:", e);
      renderFallback("request_failed: " + (e && e.message ? e.message : e));
    }
  }

  // 暴露
  global.YuyiLifeState = {
    loadAll: loadAll,
    render: function () { return loadAll(); },
  };
})(window);
