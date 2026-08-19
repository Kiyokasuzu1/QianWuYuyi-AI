/* ========================================================
   Yuyi State Manager — 统一状态模型
   ========================================================
   集中管理羽依的所有状态（情绪、能量、活动、焦点等）。
   所有组件（背景、角色、UI）通过订阅状态变化来响应更新。
   未来扩展更简单，不需要到处改代码。
   ======================================================== */

const YuyiState = (function () {
  "use strict";

  const listeners = new Map();
  const stateHistory = [];
  const MAX_HISTORY = 50;

  const state = {
    emotion: "smile",
    energy: 50,
    activity: "idle",
    focus: "",
    message: "正在为您待命~",
    current_task: "",
    last_update: null,
  };

  function getState() {
    return { ...state };
  }

  function update(newState) {
    const keys = Object.keys(newState);
    const changed = {};

    keys.forEach(key => {
      if (state[key] !== newState[key]) {
        changed[key] = {
          from: state[key],
          to: newState[key],
        };
        state[key] = newState[key];
      }
    });

    if (Object.keys(changed).length > 0) {
      state.last_update = Date.now();
      pushHistory(changed);
      notifyListeners("change", changed);
      Object.keys(changed).forEach(key => {
        notifyListeners(`change:${key}`, {
          key,
          from: changed[key].from,
          to: changed[key].to,
        });
      });
    }

    return changed;
  }

  function subscribe(event, callback) {
    if (!listeners.has(event)) {
      listeners.set(event, new Set());
    }
    listeners.get(event).add(callback);
    return () => unsubscribe(event, callback);
  }

  function unsubscribe(event, callback) {
    if (listeners.has(event)) {
      listeners.get(event).delete(callback);
    }
  }

  function notifyListeners(event, data) {
    if (listeners.has(event)) {
      listeners.get(event).forEach(cb => {
        try { cb(data, { ...state }); } catch (e) { console.error(e); }
      });
    }
  }

  function pushHistory(changed) {
    stateHistory.push({
      time: Date.now(),
      changes: { ...changed },
      snapshot: { ...state },
    });
    if (stateHistory.length > MAX_HISTORY) {
      stateHistory.shift();
    }
  }

  function getHistory(count) {
    return stateHistory.slice(-count || MAX_HISTORY);
  }

  function getEmotionLabel() {
    const labels = {
      smile: "平静",
      happy: "开心",
      excited: "兴奋",
      sad: "伤心",
      angry: "生气",
      surprised: "惊讶",
      fearful: "害怕",
      worried: "担心",
      dazed: "困惑",
      sleepy: "困倦",
      curious: "好奇",
    };
    return labels[state.emotion] || "平静";
  }

  function initialize(initialState) {
    if (initialState) {
      update(initialState);
    }
  }

  return {
    initialize,
    getState,
    update,
    subscribe,
    unsubscribe,
    getHistory,
    getEmotionLabel,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiState = YuyiState;
}
