/* ========================================================
   Yuyi Timeline — 状态时间线
   ========================================================
   记录羽依的重要活动节点，形成一个"生活轨迹"。
   用户可以看到：羽依今天做了什么、处理了哪些任务、
   状态如何变化，让 AI 助手更有"生活感"。
   ======================================================== */

const YuyiTimeline = (function () {
  "use strict";

  let timeline = [];
  let listeners = [];
  const MAX_EVENTS = 100;

  /* ============ 事件类型定义 ============ */

  const EVENT_TYPES = {
    system_start: { icon: "start", color: "#10b981", label: "系统启动" },
    system_stop: { icon: "stop", color: "#f87171", label: "系统关闭" },
    module_start: { icon: "play", color: "#10b981", label: "模块启动" },
    module_stop: { icon: "pause", color: "#f87171", label: "模块停止" },
    module_reload: { icon: "refresh", color: "#8b5cf6", label: "模块重载" },
    health_check: { icon: "check", color: "#3b82f6", label: "健康检查" },
    config_change: { icon: "settings", color: "#8b5cf6", label: "配置变更" },
    config_save: { icon: "save", color: "#10b981", label: "配置保存" },
    config_rollback: { icon: "undo", color: "#f59e0b", label: "配置回滚" },
    emotion_change: { icon: "heart", color: "#ec4899", label: "情绪变化" },
    task_complete: { icon: "done", color: "#10b981", label: "任务完成" },
    task_start: { icon: "play", color: "#3b82f6", label: "任务开始" },
    error: { icon: "alert", color: "#f87171", label: "发生错误" },
    memory_save: { icon: "bookmark", color: "#ec4899", label: "记忆保存" },
    skill_use: { icon: "sparkle", color: "#f59e0b", label: "技能使用" },
    user_interaction: { icon: "user", color: "#6366f1", label: "用户交互" },
    state_change: { icon: "circle", color: "#6b7280", label: "状态变化" },
  };

  /* ============ 添加事件 ============ */

  function addEvent(type, data) {
    const eventDef = EVENT_TYPES[type];
    if (!eventDef) {
      console.warn(`[YuyiTimeline] 未知事件类型: ${type}`);
      return null;
    }

    const event = {
      id: generateId(),
      type,
      data: data || {},
      time: Date.now(),
      timeStr: formatTime(new Date()),
      ...eventDef,
    };

    timeline.push(event);
    if (timeline.length > MAX_EVENTS) {
      timeline.shift();
    }

    notifyListeners("add", event);
    return event;
  }

  /* ============ 查询事件 ============ */

  function getEvents(count) {
    return timeline.slice(-count || 20);
  }

  function getEventsByType(type) {
    return timeline.filter(e => e.type === type);
  }

  function getTodayEvents() {
    const today = new Date();
    return timeline.filter(e => {
      const d = new Date(e.time);
      return d.toDateString() === today.toDateString();
    });
  }

  function getStatistics() {
    const stats = {
      total: timeline.length,
      today: getTodayEvents().length,
      types: {},
      firstEvent: timeline[0]?.timeStr || null,
      lastEvent: timeline[timeline.length - 1]?.timeStr || null,
    };

    timeline.forEach(e => {
      stats.types[e.type] = (stats.types[e.type] || 0) + 1;
    });

    return stats;
  }

  /* ============ 订阅机制 ============ */

  function subscribe(callback) {
    listeners.push(callback);
    return () => {
      listeners = listeners.filter(cb => cb !== callback);
    };
  }

  function notifyListeners(event, data) {
    listeners.forEach(cb => {
      try { cb(event, data); } catch (e) { console.error(e); }
    });
  }

  /* ============ 渲染到 DOM ============ */

  function render(containerId, options = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const events = getEvents(options.count || 15);
    if (events.length === 0) {
      container.innerHTML = `
        <div class="timeline-empty">
          <div class="timeline-empty-icon">✦</div>
          <div class="timeline-empty-text">还没有活动记录哦~</div>
          <div class="timeline-empty-hint">开始操作后，这里会记录羽依的活动轨迹</div>
        </div>
      `;
      return;
    }

    const groups = groupByTime(events);
    let html = '';

    Object.keys(groups).forEach(timeLabel => {
      html += `<div class="timeline-group">
        <div class="timeline-time-label">${timeLabel}</div>
        <div class="timeline-events">`;

      groups[timeLabel].forEach(event => {
        html += renderEventItem(event);
      });

      html += '</div></div>';
    });

    container.innerHTML = `<div class="timeline-container">${html}</div>`;
  }

  function renderEventItem(event) {
    const data = event.data || {};
    const detail = data.module_name || data.message || data.task || '';
    const emoji = getEventEmoji(event.type);

    return `
      <div class="timeline-item" style="--event-color: ${event.color}">
        <div class="timeline-dot"></div>
        <div class="timeline-content">
          <div class="timeline-header">
            <span class="timeline-emoji">${emoji}</span>
            <span class="timeline-label">${event.label}</span>
            <span class="timeline-time">${formatTime(new Date(event.time), true)}</span>
          </div>
          ${detail ? `<div class="timeline-detail">${escapeHtml(detail)}</div>` : ''}
        </div>
      </div>
    `;
  }

  function groupByTime(events) {
    const groups = {};
    const now = Date.now();
    const oneHour = 3600000;

    events.forEach(event => {
      let label;
      const diff = now - event.time;

      if (diff < 60000) {
        label = "刚刚";
      } else if (diff < oneHour) {
        label = `${Math.floor(diff / 60000)} 分钟前`;
      } else if (diff < oneHour * 24) {
        label = `${Math.floor(diff / oneHour)} 小时前`;
      } else {
        label = formatTime(new Date(event.time), true);
      }

      if (!groups[label]) groups[label] = [];
      groups[label].push(event);
    });

    return groups;
  }

  function getEventEmoji(type) {
    const emojis = {
      system_start: '🌟',
      system_stop: '💤',
      module_start: '▶️',
      module_stop: '⏸️',
      module_reload: '🔄',
      health_check: '✅',
      config_change: '⚙️',
      config_save: '💾',
      config_rollback: '↩️',
      emotion_change: '💗',
      task_complete: '🎉',
      task_start: '🚀',
      error: '⚠️',
      memory_save: '🔖',
      skill_use: '✨',
      user_interaction: '👤',
      state_change: '🔸',
    };
    return emojis[type] || '•';
  }

  /* ============ 工具函数 ============ */

  function generateId() {
    return `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  function formatTime(date, withSeconds = false) {
    const h = String(date.getHours()).padStart(2, '0');
    const m = String(date.getMinutes()).padStart(2, '0');
    if (withSeconds) {
      const s = String(date.getSeconds()).padStart(2, '0');
      return `${h}:${m}:${s}`;
    }
    return `${h}:${m}`;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  /* ============ 初始化事件追踪 ============ */

  function setupAutoTracking() {
    if (typeof YuyiState === "undefined") return;

    YuyiState.subscribe("change", (changed, state) => {
      if (changed.emotion) {
        addEvent("emotion_change", {
          from: changed.emotion.from,
          to: changed.emotion.to,
        });
      }
      if (changed.activity) {
        addEvent("state_change", {
          from: changed.activity.from,
          to: changed.activity.to,
        });
      }
    });
  }

  /* ============ 公开 API ============ */

  return {
    addEvent,
    getEvents,
    getEventsByType,
    getTodayEvents,
    getStatistics,
    subscribe,
    render,
    setupAutoTracking,
    EVENT_TYPES,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiTimeline = YuyiTimeline;
}
