/* =============================================================
 * api.js —— Dashboard V2 统一 API 客户端
 *
 * 职责:
 *  - 封装 /api/dashboard/v2/* 的 fetch 调用
 *  - 统一解析 ok / fallback / error 三种响应
 *  - 失败时抛出可读错误(供 UI toast 显示)
 *  - 不引入第三方库
 * ============================================================= */
(function (global) {
  "use strict";

  const API_BASE = "/api/dashboard/v2";

  /**
   * 统一 fetch 封装。
   * @param {string} path - 例如 "/overview"
   * @param {object} [opts]
   * @returns {Promise<{ok: boolean, data: any, fallback: boolean, fallback_reason?: string, error?: any}>}
   */
  async function request(path, opts) {
    const url = API_BASE + path;
    const init = Object.assign(
      {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      },
      opts || {}
    );
    let resp;
    try {
      resp = await fetch(url, init);
    } catch (e) {
      throw new Error("network_error: " + (e && e.message ? e.message : e));
    }
    let body = null;
    try {
      body = await resp.json();
    } catch (e) {
      throw new Error("invalid_json: status=" + resp.status);
    }
    if (!body || typeof body !== "object") {
      throw new Error("invalid_response");
    }
    return body;
  }

  function unwrap(body) {
    if (!body || body.ok !== true) {
      const err = (body && body.error) || { code: "unknown", message: "unknown" };
      const e = new Error(err.message || "request_failed");
      e.code = err.code;
      e.details = err.details;
      throw e;
    }
    return {
      data: body.data,
      fallback: !!body.fallback,
      fallback_reason: body.fallback_reason,
      timestamp: body.timestamp,
      schema_version: body.schema_version,
    };
  }

  const api = {
    base: API_BASE,

    /** GET /api/dashboard/v2/overview */
    async overview() {
      const body = await request("/overview");
      return unwrap(body);
    },

    /** GET /api/dashboard/v2/snapshot */
    async snapshot() {
      const body = await request("/snapshot");
      return unwrap(body);
    },

    /** GET /api/dashboard/v2/events?limit=20 */
    async events(limit) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const body = await request("/events?limit=" + n);
      return unwrap(body);
    },

    /** GET /api/dashboard/v2/health */
    async health() {
      const body = await request("/health");
      return unwrap(body);
    },

    /** GET /api/dashboard/v2/ws/info */
    async wsInfo() {
      const body = await request("/ws/info");
      return unwrap(body);
    },

    // ---- Runtime 子接口 ----
    async runtimeStatus() {
      const body = await request("/runtime/status");
      return unwrap(body);
    },
    async runtimeTasks() {
      const body = await request("/runtime/tasks");
      return unwrap(body);
    },
    async runtimeTicks(limit) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const body = await request("/runtime/ticks?limit=" + n);
      return unwrap(body);
    },
    async runtimeEvents(limit) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const body = await request("/runtime/events?limit=" + n);
      return unwrap(body);
    },
    /**
     * Step 8.4.6: GET /api/dashboard/v2/runtime/live2d
     * 返回 envelope:{ok, data, trace, confidence, fallback, fallback_reason, timestamp}
     * 注意:此接口返回的是 envelope 本身,data 字段是纯业务数据,trace 在 data 外
     */
    async runtimeLive2d() {
      const body = await request("/runtime/live2d");
      return body;
    },

    // ---- Phase 7.0: Runtime Center ----
    /** GET /api/dashboard/v2/runtime/lifecycle —— Runtime 生命周期状态 */
    async runtimeLifecycle() {
      const body = await request("/runtime/lifecycle");
      return unwrap(body);
    },
    /** GET /api/dashboard/v2/runtime/traces?limit=N —— 请求链路追踪 */
    async runtimeTraces(limit) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const body = await request("/runtime/traces?limit=" + n);
      return unwrap(body);
    },
    /** GET /api/dashboard/v2/runtime/services —— 4 服务状态总览 */
    async runtimeServices() {
      const body = await request("/runtime/services");
      return unwrap(body);
    },

    // ---- SelfModel 子接口 ----
    async selfmodelIdentity() {
      const body = await request("/selfmodel/identity");
      return unwrap(body);
    },
    async selfmodelTraits() {
      const body = await request("/selfmodel/traits");
      return unwrap(body);
    },
    async selfmodelCapabilities() {
      const body = await request("/selfmodel/capabilities");
      return unwrap(body);
    },
    async selfmodelBeliefs(limit) {
      const n = Math.max(1, Math.min(200, limit || 50));
      const body = await request("/selfmodel/beliefs?limit=" + n);
      return unwrap(body);
    },
    async selfmodelTimeline(limit) {
      const n = Math.max(1, Math.min(200, limit || 50));
      const body = await request("/selfmodel/timeline?limit=" + n);
      return unwrap(body);
    },
    async selfmodelHealth() {
      const body = await request("/selfmodel/health");
      return unwrap(body);
    },

    // ---- Memory 子接口 ----
    async memorySummary() {
      const body = await request("/memory/summary");
      return unwrap(body);
    },
    async memoryRecent(limit) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const body = await request("/memory/recent?limit=" + n);
      return unwrap(body);
    },
    async memoryImportant(limit) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const body = await request("/memory/important?limit=" + n);
      return unwrap(body);
    },
    async memoryTimeline(range) {
      const r = range || "7d";
      const body = await request("/memory/timeline?range=" + encodeURIComponent(r));
      return unwrap(body);
    },
    async memoryDetail(memoryId) {
      const body = await request("/memory/" + encodeURIComponent(memoryId));
      return unwrap(body);
    },

    // ---- Reflection 子接口 ----
    async reflectionSummary() {
      const body = await request("/reflection/summary");
      return unwrap(body);
    },
    async reflectionList(limit, type) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const t = type ? "&type=" + encodeURIComponent(type) : "";
      const body = await request("/reflection/list?limit=" + n + t);
      return unwrap(body);
    },
    async reflectionDetail(reflectionId) {
      const body = await request("/reflection/" + encodeURIComponent(reflectionId));
      return unwrap(body);
    },
    async reflectionInsights(reflectionId) {
      const body = await request("/reflection/" + encodeURIComponent(reflectionId) + "/insights");
      return unwrap(body);
    },
    async reflectionEvidence(reflectionId) {
      const body = await request("/reflection/" + encodeURIComponent(reflectionId) + "/evidence");
      return unwrap(body);
    },

    // ---- Goal 子接口 ----
    async goalSummary() {
      const body = await request("/goal/summary");
      return unwrap(body);
    },
    async goalCurrent() {
      const body = await request("/goal/current");
      return unwrap(body);
    },
    async goalList(limit, status) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const s = status ? "&status=" + encodeURIComponent(status) : "";
      const body = await request("/goal/list?limit=" + n + s);
      return unwrap(body);
    },
    async goalDetail(goalId) {
      const body = await request("/goal/" + encodeURIComponent(goalId));
      return unwrap(body);
    },
    async goalHistory(limit) {
      const n = Math.max(1, Math.min(200, limit || 50));
      const body = await request("/goal/history?limit=" + n);
      return unwrap(body);
    },

    // ---- Initiative 子接口 ----
    async initiativeSummary() {
      const body = await request("/initiative/summary");
      return unwrap(body);
    },
    async initiativeInterests(limit, trend) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const t = trend ? "&trend=" + encodeURIComponent(trend) : "";
      const body = await request("/initiative/interests?limit=" + n + t);
      return unwrap(body);
    },
    async initiativeActions(limit, status) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const s = status ? "&status=" + encodeURIComponent(status) : "";
      const body = await request("/initiative/actions?limit=" + n + s);
      return unwrap(body);
    },
    async initiativeFiltered(limit) {
      const n = Math.max(1, Math.min(200, limit || 20));
      const body = await request("/initiative/filtered?limit=" + n);
      return unwrap(body);
    },
    async initiativeHistory(limit) {
      const n = Math.max(1, Math.min(200, limit || 50));
      const body = await request("/initiative/history?limit=" + n);
      return unwrap(body);
    },

    // ---- LifeGraph 子接口 ----
    async lifeGraphOverview(nodeLimit, edgeLimit, types) {
      const n = Math.max(1, Math.min(500, nodeLimit || 200));
      const e = Math.max(1, Math.min(500, edgeLimit || 400));
      const t = (types && types.length) ? "&types=" + encodeURIComponent(types.join(",")) : "";
      const body = await request("/life-graph/overview?node_limit=" + n + "&edge_limit=" + e + t);
      return unwrap(body);
    },
    async lifeGraphTimeline(limit) {
      const n = Math.max(1, Math.min(500, limit || 100));
      const body = await request("/life-graph/timeline?limit=" + n);
      return unwrap(body);
    },
    async lifeGraphPath(fromId, toId, maxDepth) {
      const d = Math.max(1, Math.min(16, maxDepth || 8));
      const body = await request(
        "/life-graph/path?from_id=" + encodeURIComponent(fromId || "") +
        "&to_id=" + encodeURIComponent(toId || "") +
        "&max_depth=" + d
      );
      return unwrap(body);
    },

    // ---- Step 8.4.2 ----
    async lifeGraphNode(nodeId) {
      const body = await request("/life-graph/node/" + encodeURIComponent(nodeId || ""));
      return unwrap(body);
    },
    async lifeGraphNodeNeighbors(nodeId, depth) {
      const d = Math.max(1, Math.min(3, depth || 1));
      const body = await request(
        "/life-graph/node/" + encodeURIComponent(nodeId || "") + "/neighbors?depth=" + d
      );
      return unwrap(body);
    },
    async lifeGraphWhy(nodeId) {
      const body = await request("/life-graph/why/" + encodeURIComponent(nodeId || ""));
      return unwrap(body);
    },

    // ---- LifeState 子接口 (Step 8.4.1) ----
    /**
     * GET /api/dashboard/v2/life-state/summary
     * 返回 envelope:{ok, data, trace, confidence, fallback, fallback_reason, timestamp}
     * 注意:此接口返回的是 envelope 本身,data 字段是纯业务数据,trace 在 data 外
     */
    async lifeStateSummary() {
      const body = await request("/life-state/summary");
      // 此接口不通过 ok_response 包装,直接返回 envelope
      return body;
    },

    // ---- LifeTimeline 子接口 (Step 8.4.3) ----
    /**
     * GET /api/dashboard/v2/life-timeline?range=24h|7d|30d|90d|all
     * 返回 7 泳道成长时间线
     */
    async lifeTimeline(range) {
      const r = range || "all";
      const body = await request("/life-timeline?range=" + encodeURIComponent(r));
      return body;
    },

    /**
     * GET /api/dashboard/v2/life-timeline/milestones?limit=50
     * 返回成长里程碑列表
     */
    async lifeTimelineMilestones(limit) {
      const n = Math.max(1, Math.min(200, limit || 50));
      const body = await request("/life-timeline/milestones?limit=" + n);
      return body;
    },

    /**
     * GET /api/dashboard/v2/life-timeline/causality?start_time=...&end_time=...
     * 返回时间范围内的因果边
     */
    async lifeTimelineCausality(startTime, endTime) {
      const params = [];
      if (startTime) params.push("start_time=" + encodeURIComponent(startTime));
      if (endTime) params.push("end_time=" + encodeURIComponent(endTime));
      const qs = params.length ? "?" + params.join("&") : "";
      const body = await request("/life-timeline/causality" + qs);
      return body;
    },

    /**
     * GET /api/dashboard/v2/life-timeline/lanes
     * 返回七泳道定义
     */
    async lifeTimelineLanes() {
      const body = await request("/life-timeline/lanes");
      return body;
    },

    // ---- EventStream 子接口 (Step 8.4.4) ----
    /**
     * GET /api/dashboard/v2/event-stream?types=&keyword=&since=&limit=
     * 返回统一生命事件流
     * 返回 envelope: {ok, data: {items, available_types, total, ...}, trace, confidence, fallback, fallback_reason, timestamp}
     */
    async eventStream(opts) {
      const o = opts || {};
      const params = [];
      if (o.types) params.push("types=" + encodeURIComponent(o.types));
      if (o.keyword) params.push("keyword=" + encodeURIComponent(o.keyword));
      if (o.since) params.push("since=" + encodeURIComponent(String(o.since)));
      const n = Math.max(1, Math.min(200, o.limit || 50));
      params.push("limit=" + n);
      const qs = params.length ? "?" + params.join("&") : "";
      const body = await request("/event-stream" + qs);
      return body;
    },

    /**
     * GET /api/dashboard/v2/event-stream/types
     * 返回可用事件类型列表
     */
    async eventStreamTypes() {
      const body = await request("/event-stream/types");
      return body;
    },

    /**
     * GET /api/dashboard/v2/event-stream/health
     * 返回 EventStream 健康状态
     */
    async eventStreamHealth() {
      const body = await request("/event-stream/health");
      return body;
    },

    /**
     * GET /api/dashboard/v2/event-stream/ws-info
     * 返回 WebSocket 频道元信息
     */
    async eventStreamWsInfo() {
      const body = await request("/event-stream/ws-info");
      return body;
    },

    // ---- Step 8.4.7: Audit (POST security chain) ----
    /**
     * GET /api/dashboard/v2/audit/logs?limit=50&offset=0
     * 返回审计日志(只读 envelope)
     */
    async auditLogs(opts) {
      const o = opts || {};
      const params = [];
      if (o.who) params.push("who=" + encodeURIComponent(o.who));
      if (o.action) params.push("action=" + encodeURIComponent(o.action));
      if (o.result) params.push("result=" + encodeURIComponent(o.result));
      const n = Math.max(1, Math.min(200, o.limit || 50));
      params.push("limit=" + n);
      const off = Math.max(0, o.offset || 0);
      if (off) params.push("offset=" + off);
      const qs = params.length ? "?" + params.join("&") : "";
      const body = await request("/audit/logs" + qs);
      return body;
    },

    // ---- Trace & Confidence (Step 8.4.5) ----
    /**
     * 从 envelope 中提取 trace 元数据(统一解析)。
     * 永远返回对象,缺失字段用空值填充。
     * @param {object} envelope
     * @returns {{sources: Array, evidence_count: number, generated_at: string|null, confidence: number, fallback: boolean, fallback_reason: string|null, timestamp: string|null, ok: boolean}}
     */
    extractTrace(envelope) {
      const e = envelope && typeof envelope === "object" ? envelope : {};
      const tr = (e.trace && typeof e.trace === "object") ? e.trace : {};
      return {
        sources: Array.isArray(tr.sources) ? tr.sources : [],
        evidence_count: typeof tr.evidence_count === "number" ? tr.evidence_count : 0,
        generated_at: tr.generated_at || null,
        confidence: typeof e.confidence === "number" ? e.confidence : 0.0,
        fallback: e.fallback === true,
        fallback_reason: e.fallback_reason || null,
        timestamp: e.timestamp || null,
        ok: e.ok === true,
      };
    },

    /**
     * 把 0~1 的 confidence 转成百分比字符串,如 0.92 → "92%"
     * @param {number} c
     * @returns {string}
     */
    formatConfidence(c) {
      if (typeof c !== "number" || isNaN(c)) return "--";
      const pct = Math.round(c * 100);
      return pct + "%";
    },

    /**
     * 全局 confidence toggle 状态(localStorage 持久化)
     * 默认关闭,开启时 TracePanel 才会显示
     */
    getConfidenceToggle() {
      try {
        const v = localStorage.getItem("yuyi.dashboard.confidence_enabled");
        return v === "1" || v === "true";
      } catch (e) { return false; }
    },
    setConfidenceToggle(enabled) {
      try {
        localStorage.setItem("yuyi.dashboard.confidence_enabled", enabled ? "1" : "0");
      } catch (e) { /* 静默 */ }
    },
  };

  global.YuyiDashboardAPI = api;
})(window);
