/* ========================================================
   Yuyi Relationship Timeline — 关系成长时间线
   ========================================================
   展示从"陌生"到"亲密"的成长轨迹。
   Phase 2.5: Understanding Layer 核心组件。
   ======================================================== */

const YuyiRelationshipTimeline = (function () {
  "use strict";

  const API_ENDPOINT = "/admin/api/cognitive/relationship-timeline";
  let containerEl = null;
  let currentData = null;

  /* ============ 初始化 ============ */

  function init(containerId) {
    containerEl = document.getElementById(containerId);
    if (!containerEl) {
      console.error("[RelationshipTimeline] Container not found:", containerId);
      return false;
    }
    return true;
  }

  /* ============ 数据加载 ============ */

  async function load(mock = true) {
    try {
      const url = mock ? `${API_ENDPOINT}?mock=true` : API_ENDPOINT;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      currentData = data;
      render();
      return data;
    } catch (e) {
      console.error("[RelationshipTimeline] Load failed:", e);
      return null;
    }
  }

  /* ============ 渲染 ============ */

  function render() {
    if (!containerEl || !currentData) return;

    const data = currentData;
    const timeline = data.timeline || [];
    const currentLevel = data.current_level || "stranger";
    const daysTogether = data.days_together || 1;
    const nextMilestone = data.next_milestone || null;

    containerEl.innerHTML = `
      <div class="relationship-header">
        <div class="relationship-current">
          <span class="relationship-label">当前关系</span>
          <span class="relationship-level">${getLevelLabel(currentLevel)}</span>
        </div>
        <div class="relationship-days">
          <span class="relationship-days-count">${daysTogether}</span>
          <span class="relationship-days-label">天相伴</span>
        </div>
      </div>

      <div class="relationship-timeline">
        ${renderTimeline(timeline)}
      </div>

      ${
        nextMilestone
          ? `<div class="relationship-next">
              <div class="relationship-next-label">下一个里程碑</div>
              <div class="relationship-next-title">${escapeHtml(nextMilestone.title)}</div>
              <div class="relationship-next-progress">
                <div class="relationship-next-bar" style="width: ${(nextMilestone.current_trust / nextMilestone.required_trust) * 100}%"></div>
              </div>
              <div class="relationship-next-trust">
                <span>${nextMilestone.current_trust}</span>
                <span>/</span>
                <span>${nextMilestone.required_trust}</span>
                <span>信任度</span>
              </div>
            </div>`
          : ""
      }
    `;
  }

  function renderTimeline(timeline) {
    if (!timeline || timeline.length === 0) {
      return '<div class="relationship-empty">暂无成长记录</div>';
    }

    return timeline
      .map((item, index) => {
        const achieved = item.achieved;
        const isLast = index === timeline.length - 1;

        return `
        <div class="timeline-item ${achieved ? "achieved" : ""}">
          <div class="timeline-marker">
            <div class="timeline-marker-dot"></div>
            ${!isLast ? '<div class="timeline-marker-line"></div>' : ""}
          </div>
          <div class="timeline-content">
            <div class="timeline-day">Day ${item.day}</div>
            <div class="timeline-title">${escapeHtml(item.title)}</div>
            <div class="timeline-level">${getLevelLabel(item.level)}</div>
          </div>
        </div>
      `;
      })
      .join("");
  }

  /* ============ 辅助函数 ============ */

  function getLevelLabel(level) {
    const labels = {
      stranger: "陌生人",
      acquaintance: "初识",
      friend: "朋友",
      close_friend: "好友",
      partner: "伙伴",
      deep: "深交",
    };
    return labels[level] || level;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  /* ============ 公开 API ============ */

  return {
    init,
    load,
    render,
    getData: () => currentData,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiRelationshipTimeline = YuyiRelationshipTimeline;
}