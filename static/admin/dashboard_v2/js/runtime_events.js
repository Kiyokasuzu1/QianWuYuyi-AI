/* =============================================================
 * runtime_events.js —— Phase 7.1 Runtime Event Stream 前端客户端
 *
 * 策略(优先级递减):
 *   1) SSE /events/stream EventSource 实时推送
 *   2) 不支持 SSE → /events/recent 3s 轮询降级
 *   3) /events/stream 返回 JSON envelope(fallback=true) → 也降级轮询
 *
 * 特性:
 *   - Last-Event-ID: EventSource 自带,浏览器断线重连时自动续传
 *   - 在线观察者数量展示
 *   - 事件列表最多保留 150 条 DOM 节点(保护内存)
 *   - 页面不可见时暂停轮询,可见时恢复
 * ============================================================= */
(function () {
  "use strict";

  const SSE_URL = "/api/dashboard/v2/runtime/events/stream";
  const RECENT_URL = "/api/dashboard/v2/runtime/events/recent?limit=50";
  const POLL_INTERVAL_MS = 3000;
  const MAX_ITEMS = 150;
  const MAX_DISPLAY_PREVIEW = 180;
  const MAX_DISPLAY_DURATION_S = 5 * 60 * 1000;  // 展示时 duration 超过 5 分钟不显示精确值

  let eventSource = null;
  let pollTimer = null;
  let lastEventId = null;
  let seenIds = new Set();       // 防止轮询降级时重复插入
  let isStreaming = false;       // 当前是否处于 SSE 模式
  let observerCount = 0;         // 当前订阅者数(最近一次获取)

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function setTag(text, cls) {
    const tag = $("yroc-events-tag");
    if (!tag) return;
    tag.textContent = text;
    tag.className = "yuyi-card__tag" + (cls ? (" " + cls) : "");
  }

  // ------------------------------------------------------------
  // 事件图标映射(event_type → icon)
  // event_type 是 runtime.xxx,只比较 xxx 段
  // ------------------------------------------------------------
  function iconFor(shortType) {
    if (!shortType) return "▫";
    // runtime.* 事件
    if (/message\.received/.test(shortType)) return "📩";
    if (/pipeline\.started/.test(shortType)) return "▶";
    if (/pipeline\.finished/.test(shortType)) return "✅";
    if (/pipeline\.error/.test(shortType)) return "❌";
    if (/token_optimization/.test(shortType)) return "⚡";
    if (/runtime_path/.test(shortType)) return "🧠";
    if (/orchestrator_fallback/.test(shortType)) return "🔧";
    if (/response\.sent/.test(shortType)) return "💬";
    if (/stage\./.test(shortType)) return "⚙";
    // cognitive.* 事件 (Phase 7.2)
    if (/memory\.retrieved/.test(shortType)) return "🧠";
    if (/personality\.resolved/.test(shortType)) return "👤";
    if (/emotion\.updated/.test(shortType)) return "😊";
    if (/growth\.evaluated/.test(shortType)) return "🌱";
    if (/self_model\.loaded/.test(shortType)) return "🪞";
    if (/response\.path_decided/.test(shortType)) return "🛣️";
    return "•";
  }

  function classForLevel(level) {
    if (!level) return "yroc-evt--info";
    if (level === "error") return "yroc-evt--err";
    if (level === "warn") return "yroc-evt--warn";
    return "yroc-evt--info";
  }

  // ------------------------------------------------------------
  // 事件项渲染
  // ------------------------------------------------------------
  function renderItem(ev) {
    const item = document.createElement("li");
    item.className = "yroc-evt " + classForLevel(ev.level);
    item.dataset.eventId = ev.event_id || "";

    const etype = ev.event_type || "";
    // 去掉 runtime. 或 cognitive. 前缀
    const shortType = etype.indexOf("runtime.") === 0 ? etype.slice("runtime.".length)
                    : etype.indexOf("cognitive.") === 0 ? etype.slice("cognitive.".length)
                    : etype;
    const icon = iconFor(shortType);

    // Phase 7.2: cognitive 事件的 subsystem 颜色
    const cogSubsystem = (ev.data && typeof ev.data === "object" && ev.data.subsystem) ? ev.data.subsystem : "";
    if (cogSubsystem) {
      item.classList.add("yroc-evt--cog-" + cogSubsystem);
    }

    // 时间: HH:MM:SS.mmm
    let timeStr = "";
    if (ev.timestamp) {
      try {
        const d = new Date(Number(ev.timestamp) * 1000);
        function pad(n, w) { n = String(n); while (n.length < (w || 2)) n = "0" + n; return n; }
        timeStr = pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
        const ms = d.getMilliseconds();
        if (ms) timeStr += "." + pad(ms, 3);
      } catch (e) { /* 静默 */ }
    }

    // 头部
    const head = document.createElement("div");
    head.className = "yroc-evt__head";
    head.innerHTML =
      '<span class="yroc-evt__icon">' + icon + '</span>' +
      '<span class="yroc-evt__type" title="' + esc(etype) + '">' + esc(shortType) + '</span>' +
      '<span class="yroc-evt__time">' + esc(timeStr) + '</span>';
    if (ev.data && typeof ev.data.duration_ms === "number" && ev.data.duration_ms > 0 && ev.data.duration_ms < MAX_DISPLAY_DURATION_S) {
      const dur = document.createElement("span");
      dur.className = "yroc-evt__dur";
      dur.textContent = ev.data.duration_ms + "ms";
      head.appendChild(dur);
    }
    if (observerCount > 0) {
      // 只有头部首次更新时才写到 tag,这里不重复写
    }
    item.appendChild(head);

    // trace / session
    if (ev.trace_id || ev.stage) {
      const meta = document.createElement("div");
      meta.className = "yroc-evt__meta";
      const parts = [];
      if (ev.stage) parts.push("stage: " + esc(ev.stage));
      if (ev.trace_id) parts.push("trace: <code>" + esc(ev.trace_id).slice(0, 16) + "</code>");
      meta.innerHTML = parts.join(" · ");
      item.appendChild(meta);
    }

    // payload data 中常用字段: preview / reply_preview / error 等
    const lines = [];
    if (ev.data && typeof ev.data === "object") {
      const d = ev.data;
      // Phase 7.2: cognitive 事件 payload 在 d.data 里(CognitiveEvent.to_dict() 嵌套)
      const cogPayload = (d.event_type && typeof d.event_type === "string" && d.event_type.indexOf("cognitive.") === 0 && d.data && typeof d.data === "object") ? d.data : null;

      if (cogPayload) {
        // === cognitive.memory.retrieved ===
        if (typeof cogPayload.memory_count === "number") {
          lines.push("找到 " + cogPayload.memory_count + " 条记忆");
        }
        if (typeof cogPayload.max_score === "number" && cogPayload.max_score > 0) {
          lines.push("最高得分 " + cogPayload.max_score.toFixed(2));
        }
        if (cogPayload.sources && typeof cogPayload.sources === "object") {
          const srcParts = [];
          for (const k in cogPayload.sources) {
            if (cogPayload.sources[k] > 0) srcParts.push(cogPayload.sources[k] + " " + k);
          }
          if (srcParts.length) lines.push("来源: " + esc(srcParts.join(" + ")));
        }
        if (typeof cogPayload.query_length === "number" && cogPayload.query_length > 0) {
          lines.push("关键词长度 " + cogPayload.query_length);
        }
        // === cognitive.personality.resolved ===
        if (Array.isArray(cogPayload.traits_used) && cogPayload.traits_used.length > 0) {
          lines.push("激活特质: " + esc(cogPayload.traits_used.join(" / ")));
        }
        if (typeof cogPayload.traits_count === "number" && cogPayload.traits_count > 0) {
          lines.push("已加载 " + cogPayload.traits_count + " 个特质");
        }
        if (typeof cogPayload.tension_count === "number" && cogPayload.tension_count > 0) {
          lines.push("<span style='color: var(--yuyi-degraded)'>⚠ " + cogPayload.tension_count + " 个冲突</span>");
        }
        // === cognitive.emotion.updated ===
        if (typeof cogPayload.state === "string" && cogPayload.state) {
          const stateLabel = cogPayload.state_cn ? cogPayload.state_cn : cogPayload.state;
          lines.push("当前情绪: " + esc(stateLabel));
        }
        if (typeof cogPayload.intensity === "number") {
          lines.push("强度 " + (cogPayload.intensity * 100).toFixed(0) + "%");
        }
        if (typeof cogPayload.trend === "string" && cogPayload.trend) {
          lines.push("趋势: " + esc(cogPayload.trend));
        }
        // === cognitive.response.path_decided ===
        if (typeof cogPayload.path === "string" && cogPayload.path) {
          lines.push("生成路径: " + esc(cogPayload.path));
        }
        if (typeof cogPayload.reply_length_chars === "number" && cogPayload.reply_length_chars > 0) {
          lines.push("回复 " + cogPayload.reply_length_chars + " 字");
        }
        if (typeof cogPayload.error_code === "string" && cogPayload.error_code) {
          lines.push("<span style='color: var(--yuyi-critical)'>⚠ " + esc(cogPayload.error_code) + "</span>");
        }
      }

      // === runtime.* 事件原有字段 ===
      if (typeof d.preview === "string" && d.preview) {
        lines.push("📝 " + esc(d.preview).slice(0, MAX_DISPLAY_PREVIEW));
      }
      if (typeof d.reply_preview === "string" && d.reply_preview) {
        lines.push("💬 " + esc(d.reply_preview).slice(0, MAX_DISPLAY_PREVIEW));
      }
      if (typeof d.error === "string" && d.error) {
        lines.push("⚠ " + esc(d.error).slice(0, MAX_DISPLAY_PREVIEW));
      }
      if (d.unexpected_exception === true) {
        lines.push("<span style='color: var(--yuyi-critical)'>未知异常兜底</span>");
      }
      // runtime_path / orchestrator_fallback 指标
      if (typeof d.runtime_succeeded !== "undefined" || typeof d.runtime_attempted !== "undefined") {
        const ok = d.runtime_succeeded ? "✓" : "✗";
        lines.push("runtime_succeeded=" + ok);
      }
      if (typeof d.reply_source === "string" && d.reply_source) {
        lines.push("来源: " + esc(d.reply_source));
      }
      if (typeof d.length_chars === "number" && d.length_chars > 0) {
        lines.push("字数: " + d.length_chars);
      }
    }
    if (lines.length) {
      const body = document.createElement("div");
      body.className = "yroc-evt__body";
      body.innerHTML = lines.join("<br>");
      item.appendChild(body);
    }

    return item;
  }

  function pushEventToTop(ev) {
    if (!ev) return;
    const eid = ev.event_id || "";
    if (eid && seenIds.has(eid)) return;
    if (eid) {
      seenIds.add(eid);
      lastEventId = eid;
      if (seenIds.size > MAX_ITEMS * 2) {
        // 裁剪 seenIds 到 MAX_ITEMS
        const arr = Array.from(seenIds);
        seenIds = new Set(arr.slice(-MAX_ITEMS));
      }
    }

    const list = $("yroc-events-list");
    if (!list) return;

    // 移除占位 li
    const empties = list.querySelectorAll(".yuyi-list__empty");
    for (let i = 0; i < empties.length; i++) empties[i].parentNode && empties[i].parentNode.removeChild(empties[i]);

    // 插入顶部
    const item = renderItem(ev);
    if (list.firstChild) list.insertBefore(item, list.firstChild);
    else list.appendChild(item);

    // 裁剪节点数
    while (list.children.length > MAX_ITEMS) {
      list.removeChild(list.lastChild);
    }
  }

  function applyRecentBatch(events) {
    if (!Array.isArray(events)) return;
    // 轮询返回的是"旧 → 新",按顺序逐个插入,最后一条就在顶部(更准确是反过来,新→旧)
    const sorted = events.slice().sort(function (a, b) {
      const ta = Number(a && a.timestamp) || 0;
      const tb = Number(b && b.timestamp) || 0;
      return tb - ta; // 新 → 旧
    });
    for (let i = sorted.length - 1; i >= 0; i--) {
      pushEventToTop(sorted[i]);
    }
  }

  // ------------------------------------------------------------
  // SSE 模式
  // ------------------------------------------------------------
  function startSSE() {
    if (typeof EventSource === "undefined") {
      startPolling();
      return;
    }
    stopSSE();
    stopPolling();

    setTag("SSE 连接中…", "is-degraded");
    let firstMessageReceived = false;
    try {
      eventSource = new EventSource(SSE_URL, { withCredentials: false });
    } catch (e) {
      setTag("EventSource 不可用", "is-critical");
      startPolling();
      return;
    }

    eventSource.addEventListener("runtime", function (evt) {
      try {
        const data = JSON.parse(evt.data);
        firstMessageReceived = true;
        setTag("SSE · 实时", "is-online");
        isStreaming = true;
        pushEventToTop(data);
        if (evt && evt.lastEventId) {
          lastEventId = evt.lastEventId;
        }
      } catch (err) {
        /* 静默 */
      }
    });

    // message 事件兜底(某些情况下服务器没带 event: runtime 标签)
    eventSource.addEventListener("message", function (evt) {
      try {
        const data = JSON.parse(evt.data);
        // 如果数据包含 fallback=true → 服务器端 EventQueue 不可用,降级轮询
        if (data && data.ok === true && data.fallback === true) {
          stopSSE();
          startPolling();
          return;
        }
        firstMessageReceived = true;
        setTag("SSE · 实时", "is-online");
        isStreaming = true;
        pushEventToTop(data);
        if (evt && evt.lastEventId) lastEventId = evt.lastEventId;
      } catch (err) { /* 静默 */ }
    });

    eventSource.addEventListener("open", function () {
      if (!firstMessageReceived) {
        setTag("SSE 已连接 · 等待事件…", "is-degraded");
      }
    });

    eventSource.addEventListener("error", function () {
      // EventSource 会自动重连,我们只更新状态
      setTag("SSE 断线 · 自动重连中…", "is-degraded");
      // 如果连续 15s 没恢复,降级到轮询(保护服务器)
      setTimeout(function () {
        if (eventSource && eventSource.readyState === EventSource.CONNECTING) {
          stopSSE();
          startPolling();
        }
      }, 15000);
    });
  }

  function stopSSE() {
    if (eventSource) {
      try { eventSource.close(); } catch (e) { /* 静默 */ }
      eventSource = null;
    }
    isStreaming = false;
  }

  // ------------------------------------------------------------
  // 轮询降级模式
  // ------------------------------------------------------------
  function pollOnce() {
    const url = lastEventId ? (RECENT_URL + "&since_id=" + encodeURIComponent(lastEventId)) : RECENT_URL;
    fetch(url, { cache: "no-store" })
      .then(function (resp) { return resp.json(); })
      .then(function (envelope) {
        setTag("轮询模式 · " + POLL_INTERVAL_MS / 1000 + "s", "is-degraded");
        if (!envelope || envelope.ok !== true) return;
        const data = envelope.data || {};
        if (Array.isArray(data.events)) {
          applyRecentBatch(data.events);
        }
        if (typeof data.subscriber_count === "number") {
          observerCount = data.subscriber_count;
        }
        // 如果服务器标记 fallback=true 但仍然返回了 events,下次继续;也不升级到 SSE
      })
      .catch(function () {
        setTag("轮询失败", "is-critical");
      });
  }

  function startPolling() {
    stopSSE();
    stopPolling();
    pollOnce();
    pollTimer = setInterval(pollOnce, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ------------------------------------------------------------
  // 页面可见性(节省资源)
  // ------------------------------------------------------------
  let wasStreaming = false;
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (eventSource) { wasStreaming = true; stopSSE(); }
      stopPolling();
    } else {
      if (wasStreaming) startSSE();
      else if (!eventSource && !pollTimer) startSSE();
    }
  });

  // ------------------------------------------------------------
  // 初始化(仅 runtime-center 页面存在时执行)
  // ------------------------------------------------------------
  function init() {
    if (!document.querySelector('[data-page-id="runtime-center"]')) return;
    // 初始先加载一次 recent(保证进入页面立刻有东西看)
    pollOnce();
    // 然后启动 SSE
    setTimeout(startSSE, 300);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
