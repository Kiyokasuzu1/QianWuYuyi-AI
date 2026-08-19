/* ========================================================
   Yuyi Growth Preview — 成长影响预览弹窗
   ========================================================
   在批准成长建议前，预览对人格/行为/情绪的影响。
   Phase 2.5: Understanding Layer 核心组件。
   ======================================================== */

const YuyiGrowthPreview = (function () {
  "use strict";

  const API_ENDPOINT = "/admin/api/cognitive/proposal";
  let modalEl = null;
  let currentProposalId = null;
  let currentData = null;
  let onConfirmCallback = null;

  /* ============ 创建模态框 ============ */

  function createModal() {
    if (modalEl) return;

    modalEl = document.createElement("div");
    modalEl.id = "growth-preview-modal";
    modalEl.className = "growth-preview-modal";
    modalEl.innerHTML = `
      <div class="growth-preview-backdrop" onclick="YuyiGrowthPreview.close()"></div>
      <div class="growth-preview-content">
        <div class="growth-preview-header">
          <h3 class="growth-preview-title">成长预测</h3>
          <button class="growth-preview-close" onclick="YuyiGrowthPreview.close()">×</button>
        </div>
        <div class="growth-preview-body" id="growth-preview-body">
          <div class="growth-preview-loading">加载中...</div>
        </div>
        <div class="growth-preview-footer">
          <button class="growth-preview-btn growth-preview-btn-cancel" onclick="YuyiGrowthPreview.close()">取消</button>
          <button class="growth-preview-btn growth-preview-btn-confirm" onclick="YuyiGrowthPreview.confirm()">确认成长</button>
        </div>
      </div>
    `;

    document.body.appendChild(modalEl);
  }

  /* ============ 打开预览 ============ */

  async function open(proposalId, onConfirm) {
    createModal();
    currentProposalId = proposalId;
    onConfirmCallback = onConfirm;

    modalEl.classList.add("active");
    document.body.style.overflow = "hidden";

    const bodyEl = document.getElementById("growth-preview-body");
    bodyEl.innerHTML = '<div class="growth-preview-loading">加载中...</div>';

    try {
      const url = `${API_ENDPOINT}/${proposalId}/preview?mock=true`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      currentData = data;
      renderPreview();
    } catch (e) {
      console.error("[GrowthPreview] Load failed:", e);
      bodyEl.innerHTML = '<div class="growth-preview-error">加载失败</div>';
    }
  }

  /* ============ 渲染预览 ============ */

  function renderPreview() {
    const bodyEl = document.getElementById("growth-preview-body");
    if (!bodyEl || !currentData) return;

    const data = currentData;
    const before = data.before || {};
    const after = data.after || {};
    const effects = data.effects || [];

    bodyEl.innerHTML = `
      <div class="growth-preview-description">
        ${escapeHtml(data.description || "成长建议")}
      </div>

      <div class="growth-preview-section">
        <h4 class="growth-preview-section-title">人格变化</h4>
        <div class="growth-preview-changes">
          ${renderChanges(before, after)}
        </div>
      </div>

      ${
        effects.length > 0
          ? `<div class="growth-preview-section">
              <h4 class="growth-preview-section-title">行为变化</h4>
              <div class="growth-preview-effects">
                ${renderEffects(effects)}
              </div>
            </div>`
          : ""
      }

      <div class="growth-preview-confidence">
        <span class="growth-preview-confidence-label">预测置信度</span>
        <span class="growth-preview-confidence-value">${Math.round((data.confidence || 0.7) * 100)}%</span>
      </div>
    `;
  }

  function renderChanges(before, after) {
    const keys = Object.keys({ ...before, ...after });
    if (keys.length === 0) {
      return '<div class="growth-preview-empty">暂无数据</div>';
    }

    return keys
      .map((key) => {
        const beforeVal = before[key] || 0;
        const afterVal = after[key] || 0;
        const delta = afterVal - beforeVal;
        const deltaPercent = Math.round(delta * 100);
        const isPositive = delta > 0;

        return `
        <div class="growth-preview-change-item">
          <div class="growth-preview-change-key">${escapeHtml(key)}</div>
          <div class="growth-preview-change-values">
            <div class="growth-preview-change-before">
              <span class="growth-preview-change-label">当前</span>
              <span class="growth-preview-change-number">${Math.round(beforeVal * 100)}%</span>
            </div>
            <div class="growth-preview-change-arrow ${isPositive ? "positive" : "negative"}">
              ${isPositive ? "↑" : "↓"}
            </div>
            <div class="growth-preview-change-after">
              <span class="growth-preview-change-label">预测</span>
              <span class="growth-preview-change-number">${Math.round(afterVal * 100)}%</span>
            </div>
          </div>
          <div class="growth-preview-change-delta ${isPositive ? "positive" : "negative"}">
            ${isPositive ? "+" : ""}${deltaPercent}%
          </div>
        </div>
      `;
      })
      .join("");
  }

  function renderEffects(effects) {
    return effects
      .map(
        (effect) => `
      <div class="growth-preview-effect-item">
        <svg viewBox="0 0 24 24" width="14" height="14" style="margin-right:8px;color:#22c55e">
          <polyline points="20,6 9,17 4,12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        ${escapeHtml(effect)}
      </div>
    `
      )
      .join("");
  }

  /* ============ 关闭模态框 ============ */

  function close() {
    if (!modalEl) return;

    modalEl.classList.remove("active");
    document.body.style.overflow = "";
    currentProposalId = null;
    currentData = null;
    onConfirmCallback = null;
  }

  /* ============ 确认成长 ============ */

  function confirm() {
    if (onConfirmCallback && currentProposalId) {
      onConfirmCallback(currentProposalId);
    }
    close();
  }

  /* ============ 辅助函数 ============ */

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  /* ============ 公开 API ============ */

  return {
    open,
    close,
    confirm,
    getData: () => currentData,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiGrowthPreview = YuyiGrowthPreview;
}