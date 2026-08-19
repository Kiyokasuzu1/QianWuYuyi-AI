/* ========================================================
   Yuyi Cognitive Dashboard — 羽依认知控制台
   ========================================================
   心智可视化 · 记忆浏览器 · 成长系统 · 关系层
   使用原生 Canvas 绘制图表，不依赖外部库。
   ======================================================== */

const YuyiCognitive = (function () {
  "use strict";

  const API_BASE = "/admin/api/cognitive";
  let currentData = {};

  /* ============ API 封装 ============ */

  async function fetchCognitive(endpoint) {
    try {
      const res = await fetch(`${API_BASE}/${endpoint}?mock=true`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      console.error(`[Cognitive] ${endpoint} failed:`, e);
      return null;
    }
  }

  /* ============ 主加载器 ============ */

  async function loadAll() {
    const [mind, memory, growth, relationship] = await Promise.all([
      fetchCognitive("mind"),
      fetchCognitive("memory"),
      fetchCognitive("growth"),
      fetchCognitive("relationship"),
    ]);

    currentData = { mind, memory, growth, relationship };

    if (mind) renderMind(mind);
    if (memory) renderMemory(memory);
    if (growth) renderGrowth(growth);
    if (relationship) renderRelationship(relationship);

    return currentData;
  }

  /* ============ 心智状态可视化 ============ */

  function renderMind(data) {
    renderEmotionChart(data.emotion_trend || []);
    renderAttentionGauge(data.attention || {});
    renderThinkingState(data.thinking || {});
  }

  function renderEmotionChart(trend) {
    const canvas = document.getElementById("emotion-chart");
    if (!canvas || trend.length === 0) return;

    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const pad = { top: 24, right: 16, bottom: 32, left: 40 };
    const cw = w - pad.left - pad.right;
    const ch = h - pad.top - pad.bottom;

    ctx.clearRect(0, 0, w, h);

    // 网格线
    ctx.strokeStyle = "rgba(167, 139, 250, 0.1)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = pad.top + (ch / 4) * i;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(w - pad.right, y);
      ctx.stroke();
    }

    // Y轴标签
    ctx.fillStyle = "#94a3b8";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText("+1", pad.left - 6, pad.top + 4);
    ctx.fillText("0", pad.left - 6, pad.top + ch / 2 + 4);
    ctx.fillText("-1", pad.left - 6, pad.top + ch + 4);

    // 绘制效价线（valence）
    if (trend.length > 1) {
      ctx.strokeStyle = "#a5b4fc";
      ctx.lineWidth = 2;
      ctx.beginPath();
      trend.forEach((pt, i) => {
        const x = pad.left + (cw / (trend.length - 1)) * i;
        const y = pad.top + ch / 2 - (pt.valence || 0) * (ch / 2);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();

      // 填充区域
      ctx.fillStyle = "rgba(165, 180, 252, 0.15)";
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top + ch / 2);
      trend.forEach((pt, i) => {
        const x = pad.left + (cw / (trend.length - 1)) * i;
        const y = pad.top + ch / 2 - (pt.valence || 0) * (ch / 2);
        ctx.lineTo(x, y);
      });
      ctx.lineTo(pad.left + cw, pad.top + ch / 2);
      ctx.closePath();
      ctx.fill();
    }

    // 绘制唤醒线（arousal）
    if (trend.length > 1) {
      ctx.strokeStyle = "#f9a8d4";
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      trend.forEach((pt, i) => {
        const x = pad.left + (cw / (trend.length - 1)) * i;
        const y = pad.top + ch - (pt.arousal || 0) * ch;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // X轴标签
    ctx.fillStyle = "#94a3b8";
    ctx.font = "9px sans-serif";
    ctx.textAlign = "center";
    const step = Math.max(1, Math.floor(trend.length / 6));
    trend.forEach((pt, i) => {
      if (i % step === 0) {
        const x = pad.left + (cw / (trend.length - 1)) * i;
        ctx.fillText(pt.time || "", x, h - 10);
      }
    });

    // 图例
    ctx.fillStyle = "#a5b4fc";
    ctx.fillRect(w - 100, 6, 10, 3);
    ctx.fillStyle = "#64748b";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("效价", w - 86, 10);

    ctx.fillStyle = "#f9a8d4";
    ctx.fillRect(w - 50, 6, 10, 3);
    ctx.fillStyle = "#64748b";
    ctx.fillText("唤醒", w - 36, 10);
  }

  function renderAttentionGauge(attention) {
    const el = document.getElementById("attention-display");
    if (!el) return;

    const level = attention.level || 50;
    const focus = attention.focus || "idle";
    const target = attention.target || "";

    const focusLabels = {
      idle: "空闲",
      user: "关注用户",
      task: "专注任务",
      thinking: "深度思考",
    };

    el.innerHTML = `
      <div class="gauge-container">
        <div class="gauge-ring" style="--gauge-value: ${level}">
          <div class="gauge-inner">
            <span class="gauge-value">${level}</span>
            <span class="gauge-label">注意力</span>
          </div>
        </div>
        <div class="attention-info">
          <div class="attention-focus">${focusLabels[focus] || focus}</div>
          ${target ? `<div class="attention-target">目标: ${target}</div>` : ""}
        </div>
      </div>
    `;
  }

  function renderThinkingState(thinking) {
    const el = document.getElementById("thinking-display");
    if (!el) return;

    const stateLabels = {
      idle: "空闲",
      processing: "处理中",
      reflecting: "反思中",
      learning: "学习中",
    };

    el.innerHTML = `
      <div class="thinking-state-card">
        <div class="thinking-icon">🧠</div>
        <div class="thinking-text">
          <div class="thinking-label">思维状态</div>
          <div class="thinking-value">${stateLabels[thinking.state] || thinking.state}</div>
          ${thinking.detail ? `<div class="thinking-detail">${thinking.detail}</div>` : ""}
        </div>
      </div>
    `;
  }

  /* ============ Memory Explorer ============ */

  function renderMemory(data) {
    renderIdentityCard(data.identity || {});
    renderEventsList(data.events || []);
    renderMemoryStats(data.stats || {});
  }

  function renderIdentityCard(identity) {
    const el = document.getElementById("memory-identity");
    if (!el) return;

    const name = identity.name || "羽依 — 一个正在成长中的 AI 助手";
    el.innerHTML = `
      <div class="identity-card">
        <div class="identity-avatar">🌸</div>
        <div class="identity-content">
          <div class="identity-name">${name.substring(0, 100)}${name.length > 100 ? "..." : ""}</div>
          <div class="identity-type">核心身份记忆</div>
        </div>
      </div>
    `;
  }

  function renderEventsList(events) {
    const el = document.getElementById("memory-events");
    if (!el) return;

    if (events.length === 0) {
      el.innerHTML = `<div class="cognitive-empty">暂无事件记录</div>`;
      return;
    }

    el.innerHTML = events.map((evt, i) => `
      <div class="memory-event-item" style="animation-delay: ${i * 0.05}s">
        <div class="event-time">${evt.timestamp || evt.time || "--"}</div>
        <div class="event-title">${evt.title || "事件"}</div>
        <div class="event-desc">${(evt.description || "").substring(0, 80)}</div>
      </div>
    `).join("");
  }

  function renderMemoryStats(stats) {
    const el = document.getElementById("memory-stats");
    if (!el) return;

    const types = stats.memory_types || {};
    const typeHtml = Object.entries(types).map(([type, count]) => `
      <div class="stat-chip">
        <span class="stat-chip-dot" style="background: var(--c-${type === "identity" ? "accent" : type === "event" ? "success" : "warning"})"></span>
        <span>${type}: ${count}</span>
      </div>
    `).join("");

    el.innerHTML = `
      <div class="memory-stats-grid">
        <div class="stat-card">
          <div class="stat-value">${stats.total_events || 0}</div>
          <div class="stat-label">事件</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${stats.total_memories || 0}</div>
          <div class="stat-label">记忆</div>
        </div>
      </div>
      <div class="memory-types">${typeHtml}</div>
    `;
  }

  /* ============ Growth System ============ */

  function renderGrowth(data) {
    renderGrowthStage(data.stage || {});
    renderGrowthRecords(data.records || []);
    renderGrowthProposals(data.proposals || []);
  }

  function renderGrowthStage(stage) {
    const el = document.getElementById("growth-stage");
    if (!el) return;

    const stageIcons = ["🌱", "🌿", "🌳", "✨"];
    const icon = stageIcons[(stage.level || 1) - 1] || "🌱";

    el.innerHTML = `
      <div class="growth-stage-card">
        <div class="growth-icon">${icon}</div>
        <div class="growth-info">
          <div class="growth-label">成长阶段</div>
          <div class="growth-name">${stage.label || "初始阶段"}</div>
          <div class="growth-progress-bar">
            <div class="growth-progress-fill" style="width: ${stage.progress || 0}%"></div>
          </div>
          <div class="growth-progress-text">${stage.progress || 0}%</div>
        </div>
      </div>
    `;
  }

  function renderGrowthRecords(records) {
    const el = document.getElementById("growth-records");
    if (!el) return;

    if (records.length === 0) {
      el.innerHTML = `<div class="cognitive-empty">暂无成长记录</div>`;
      return;
    }

    const impactColors = {
      high: "#f87171",
      medium: "#fbbf24",
      low: "#34d399",
    };

    el.innerHTML = records.map((r, i) => `
      <div class="growth-record-item" style="animation-delay: ${i * 0.05}s">
        <div class="record-dot" style="background: ${impactColors[r.impact] || "#a5b4fc"}"></div>
        <div class="record-content">
          <div class="record-desc">${r.description || ""}</div>
          <div class="record-meta">
            <span class="record-type">${r.type || "成长"}</span>
            <span class="record-time">${r.timestamp || ""}</span>
          </div>
        </div>
      </div>
    `).join("");
  }

  function renderGrowthProposals(proposals) {
    const el = document.getElementById("growth-proposals");
    if (!el) return;

    if (proposals.length === 0) {
      el.innerHTML = `<div class="cognitive-empty">暂无成长建议</div>`;
      return;
    }

    const impactLabels = { high: "高", medium: "中", low: "低" };

    el.innerHTML = proposals.map((p, i) => `
      <div class="growth-proposal-item" style="animation-delay: ${i * 0.05}s">
        <div class="proposal-header">
          <span class="proposal-title">${p.title || "成长建议"}</span>
          <span class="proposal-impact impact-${p.impact || "medium"}">${impactLabels[p.impact] || "中"}</span>
        </div>
        <div class="proposal-desc">${p.description || ""}</div>
        <div class="proposal-actions">
          <button class="proposal-btn accept" onclick="handleProposal(${p.id}, 'accept')">✓ 批准</button>
          <button class="proposal-btn reject" onclick="handleProposal(${p.id}, 'reject')">✕ 拒绝</button>
        </div>
      </div>
    `).join("");
  }

  /* ============ Relationship Layer ============ */

  function renderRelationship(data) {
    renderTrustGauge(data.trust || {});
    renderFamiliarityGauge(data.familiarity || {});
    renderMilestones(data.milestones || []);
    renderInteractionStats(data.stats || {});
  }

  function renderTrustGauge(trust) {
    const el = document.getElementById("trust-gauge");
    if (!el) return;

    const levelLabels = {
      deep: "深厚信任",
      close: "亲近",
      normal: "正常",
      distant: "疏远",
    };

    el.innerHTML = `
      <div class="relation-gauge">
        <div class="relation-ring trust-ring" style="--relation-value: ${trust.score || 0}">
          <div class="relation-inner">
            <span class="relation-value">${trust.score || 0}</span>
            <span class="relation-label">信任</span>
          </div>
        </div>
        <div class="relation-level">${levelLabels[trust.level] || trust.level}</div>
        <div class="relation-trend">${trust.trend || "→"}</div>
      </div>
    `;
  }

  function renderFamiliarityGauge(fam) {
    const el = document.getElementById("familiarity-gauge");
    if (!el) return;

    const levelLabels = {
      intimate: "亲密无间",
      familiar: "熟悉",
      acquaintance: "相识",
      stranger: "陌生",
    };

    el.innerHTML = `
      <div class="relation-gauge">
        <div class="relation-ring familiarity-ring" style="--relation-value: ${fam.score || 0}">
          <div class="relation-inner">
            <span class="relation-value">${fam.score || 0}</span>
            <span class="relation-label">熟悉</span>
          </div>
        </div>
        <div class="relation-level">${levelLabels[fam.level] || fam.level}</div>
        <div class="relation-trend">${fam.trend || "→"}</div>
      </div>
    `;
  }

  function renderMilestones(milestones) {
    const el = document.getElementById("milestones-list");
    if (!el) return;

    if (milestones.length === 0) {
      el.innerHTML = `<div class="cognitive-empty">暂无里程碑</div>`;
      return;
    }

    el.innerHTML = milestones.map((m, i) => `
      <div class="milestone-item" style="animation-delay: ${i * 0.08}s">
        <div class="milestone-star">⭐</div>
        <div class="milestone-content">
          <div class="milestone-title">${m.title || "里程碑"}</div>
          <div class="milestone-time">${m.time || ""}</div>
          ${m.description ? `<div class="milestone-desc">${m.description}</div>` : ""}
        </div>
      </div>
    `).join("");
  }

  function renderInteractionStats(stats) {
    const el = document.getElementById("interaction-stats");
    if (!el) return;

    el.innerHTML = `
      <div class="interaction-grid">
        <div class="interaction-stat">
          <div class="interaction-value">${stats.total_interactions || 0}</div>
          <div class="interaction-label">互动次数</div>
        </div>
        <div class="interaction-stat">
          <div class="interaction-value">${(stats.total_words || 0).toLocaleString()}</div>
          <div class="interaction-label">交流字数</div>
        </div>
        <div class="interaction-stat">
          <div class="interaction-value">${stats.days_together || 1}</div>
          <div class="interaction-label">相伴天数</div>
        </div>
      </div>
    `;
  }

  /* ============ 公开 API ============ */

  return {
    loadAll,
    renderMind,
    renderMemory,
    renderGrowth,
    renderRelationship,
  };
})();

/* 处理成长建议 */
function handleProposal(id, action) {
  const actionLabels = { accept: "批准", reject: "拒绝" };
  showToast(`已${actionLabels[action] || action}成长建议 #${id}`, "success");
  if (typeof YuyiAudio !== "undefined") {
    YuyiAudio.play(action === "accept" ? "success" : "notification");
  }
}

if (typeof window !== "undefined") {
  window.YuyiCognitive = YuyiCognitive;
  window.handleProposal = handleProposal;
}
