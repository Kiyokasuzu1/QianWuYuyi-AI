/* ========================================================
   Yuyi Reasoning — 行为原因解释
   ========================================================
   解释羽依当前状态的原因和影响。
   Phase 2.5: Understanding Layer 核心组件。
   ======================================================== */

const YuyiReasoning = (function () {
  "use strict";

  const API_ENDPOINT = "/admin/api/cognitive/explain";
  let containerEl = null;
  let currentData = null;

  /* ============ 初始化 ============ */

  function init(containerId) {
    containerEl = document.getElementById(containerId);
    if (!containerEl) {
      console.error("[Reasoning] Container not found:", containerId);
      return false;
    }
    return true;
  }

  /* ============ 数据加载 ============ */

  async function load(stateType = "emotion", mock = false) {
    try {
      const url = mock
        ? `${API_ENDPOINT}?state=${stateType}&mock=true`
        : `${API_ENDPOINT}?state=${stateType}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      currentData = data;
      render();
      return data;
    } catch (e) {
      console.error("[Reasoning] Load failed:", e);
      return null;
    }
  }

  /* ============ 渲染 ============ */

  function render() {
    if (!containerEl || !currentData) return;

    const data = currentData;
    const stateType = data.state_type || "emotion";
    const currentValue = data.current_value || "未知";

    containerEl.innerHTML = `
      <div class="reasoning-header">
        <div class="reasoning-state">
          <span class="reasoning-state-label">${getStateLabel(stateType)}</span>
          <span class="reasoning-state-value">${escapeHtml(currentValue)}</span>
        </div>
        <button class="reasoning-toggle-btn" onclick="YuyiReasoning.toggle()">
          为什么？
          <svg viewBox="0 0 24 24" width="14" height="14" style="margin-left:4px;">
            <path d="M12 4v16m-8-8h16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </button>
      </div>
      <div class="reasoning-body" id="reasoning-body" style="display:none;">
        <div class="reasoning-section">
          <h4 class="reasoning-section-title">主要因素</h4>
          <div class="reasoning-factors">
            ${renderFactors(data.reasons || [])}
          </div>
        </div>
        <div class="reasoning-section">
          <h4 class="reasoning-section-title">影响</h4>
          <div class="reasoning-impact">
            ${renderImpact(data.impact || {})}
          </div>
        </div>
        ${
          data.suggestions && data.suggestions.length > 0
            ? `<div class="reasoning-section">
                <h4 class="reasoning-section-title">建议</h4>
                <div class="reasoning-suggestions">
                  ${renderSuggestions(data.suggestions)}
                </div>
              </div>`
            : ""
        }
      </div>
    `;
  }

  function renderFactors(reasons) {
    if (!reasons || reasons.length === 0) {
      return '<p class="reasoning-empty">暂无数据</p>';
    }

    return reasons
      .map((r, i) => {
        const weight = r.weight || 3;
        const stars = "★".repeat(weight) + "☆".repeat(5 - weight);
        const typeIcon = getTypeIcon(r.type);

        return `
        <div class="reasoning-factor" style="animation-delay: ${i * 0.1}s">
          <div class="reasoning-factor-stars">${stars}</div>
          <div class="reasoning-factor-content">
            <span class="reasoning-factor-icon">${typeIcon}</span>
            <span class="reasoning-factor-text">${escapeHtml(r.factor)}</span>
          </div>
        </div>
      `;
      })
      .join("");
  }

  function renderImpact(impact) {
    const entries = Object.entries(impact);
    if (entries.length === 0) {
      return '<p class="reasoning-empty">暂无数据</p>';
    }

    return entries
      .map(([key, value]) => {
        const isPositive = value.toString().includes("+");
        const color = isPositive ? "#22c55e" : "#ef4444";

        return `
        <div class="reasoning-impact-item">
          <span class="reasoning-impact-key">${escapeHtml(key)}</span>
          <span class="reasoning-impact-value" style="color:${color}">${escapeHtml(value)}</span>
        </div>
      `;
      })
      .join("");
  }

  function renderSuggestions(suggestions) {
    return suggestions
      .map(
        (s) => `
      <div class="reasoning-suggestion">
        <svg viewBox="0 0 24 24" width="14" height="14" style="margin-right:6px;color:#a5b4fc">
          <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2"/>
          <path d="M12 8v4l2 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        ${escapeHtml(s)}
      </div>
    `
      )
      .join("");
  }

  /* ============ 辅助函数 ============ */

  function getStateLabel(type) {
    const labels = {
      emotion: "当前情绪",
      activity: "当前活动",
      trust: "信任状态",
    };
    return labels[type] || "当前状态";
  }

  function getTypeIcon(type) {
    const icons = {
      interaction: "💬",
      event: "📌",
      growth: "🌱",
      achievement: "✨",
      baseline: "⏳",
      learning: "📚",
    };
    return icons[type] || "•";
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  /* ============ 交互 ============ */

  function toggle() {
    const body = document.getElementById("reasoning-body");
    if (!body) return;

    const isHidden = body.style.display === "none";
    body.style.display = isHidden ? "block" : "none";

    // 添加展开动画
    if (isHidden) {
      body.classList.add("reasoning-body-expanding");
      setTimeout(() => body.classList.remove("reasoning-body-expanding"), 300);
    }
  }

  /* ============ 公开 API ============ */

  return {
    init,
    load,
    render,
    toggle,
    getData: () => currentData,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiReasoning = YuyiReasoning;
}