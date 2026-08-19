/* =============================================================
 * trace_panel.js —— Dashboard V2 统一 TracePanel 组件
 *
 * 职责:
 *  - 渲染数据来源 / 证据数量 / 生成时间 / 可信度 / fallback 原因
 *  - 复用于:Overview / LifeGraph / Timeline / EventStream /
 *    Memory / Reflection / Goal / Initiative 等所有页面
 *  - 默认隐藏,通过全局 confidence toggle 开启显示
 *  - 不引入第三方库
 * ============================================================= */
(function (global) {
  "use strict";

  const $ = (id) => document.getElementById(id);

  /**
   * 构造一个 TracePanel 渲染器。
   * @param {HTMLElement|string} root 容器或容器 id
   * @param {object} [opts]  { title?: string, defaultExpanded?: boolean }
   */
  function TracePanel(root, opts) {
    this.root = typeof root === "string" ? $(root) : root;
    this.opts = Object.assign(
      { title: "🪶 数据来源 & 可信度", defaultExpanded: false },
      opts || {}
    );
    this._current = null;
    this._render();
  }

  TracePanel.prototype._render = function () {
    if (!this.root) return;
    this.root.innerHTML = [
      '<div class="yuyi-tp">',
      '  <div class="yuyi-tp-head" data-role="head">',
      '    <span class="yuyi-tp-title">' + this.opts.title + "</span>",
      '    <span class="yuyi-tp-summary" data-role="summary">--</span>',
      '  </div>',
      '  <div class="yuyi-tp-body" data-role="body" hidden>',
      '    <div class="yuyi-tp-row"><span class="yuyi-tp-label">可信度</span><span class="yuyi-tp-value" data-role="confidence">--</span></div>',
      '    <div class="yuyi-tp-row"><span class="yuyi-tp-label">证据数量</span><span class="yuyi-tp-value" data-role="evidence">--</span></div>',
      '    <div class="yuyi-tp-row"><span class="yuyi-tp-label">生成时间</span><span class="yuyi-tp-value" data-role="generated">--</span></div>',
      '    <div class="yuyi-tp-row"><span class="yuyi-tp-label">fallback</span><span class="yuyi-tp-value" data-role="fallback">--</span></div>',
      '    <div class="yuyi-tp-row"><span class="yuyi-tp-label">fallback 原因</span><span class="yuyi-tp-value" data-role="reason">--</span></div>',
      '    <div class="yuyi-tp-section">',
      '      <div class="yuyi-tp-section-title">数据来源</div>',
      '      <ul class="yuyi-tp-sources" data-role="sources"></ul>',
      '    </div>',
      '  </div>',
      "</div>",
    ].join("");

    // 绑定展开/收起
    const head = this.root.querySelector('[data-role="head"]');
    if (head) {
      head.addEventListener("click", () => this.toggle());
    }
  };

  TracePanel.prototype.toggle = function () {
    const body = this.root && this.root.querySelector('[data-role="body"]');
    if (!body) return;
    body.hidden = !body.hidden;
  };

  TracePanel.prototype.set = function (envelope) {
    this._current = envelope;
    const api = global.YuyiDashboardAPI;
    let trace = { sources: [], evidence_count: 0, generated_at: null, confidence: 0, fallback: false, fallback_reason: null };
    if (api && typeof api.extractTrace === "function") {
      trace = api.extractTrace(envelope);
    } else if (envelope && typeof envelope === "object") {
      const tr = envelope.trace || {};
      trace.sources = Array.isArray(tr.sources) ? tr.sources : [];
      trace.evidence_count = typeof tr.evidence_count === "number" ? tr.evidence_count : 0;
      trace.generated_at = tr.generated_at || null;
      trace.confidence = typeof envelope.confidence === "number" ? envelope.confidence : 0;
      trace.fallback = envelope.fallback === true;
      trace.fallback_reason = envelope.fallback_reason || null;
    }
    this._renderSummary(trace);
    this._renderDetail(trace);
  };

  TracePanel.prototype._renderSummary = function (trace) {
    const sumEl = this.root && this.root.querySelector('[data-role="summary"]');
    if (!sumEl) return;
    const conf = trace.confidence;
    const api = global.YuyiDashboardAPI;
    const confText = api && typeof api.formatConfidence === "function"
      ? api.formatConfidence(conf)
      : (typeof conf === "number" ? Math.round(conf * 100) + "%" : "--");
    const dotClass = conf >= 0.8 ? "ok" : (conf >= 0.5 ? "warn" : "err");
    const fallbackTag = trace.fallback ? ' <span class="yuyi-tp-tag-fallback">fallback</span>' : "";
    sumEl.innerHTML = [
      '<span class="yuyi-tp-dot yuyi-tp-dot-' + dotClass + '"></span>',
      "<span>可信度 " + confText + "</span>",
      fallbackTag,
    ].join(" ");
  };

  TracePanel.prototype._renderDetail = function (trace) {
    const api = global.YuyiDashboardAPI;
    const conf = trace.confidence;
    const confText = api && typeof api.formatConfidence === "function"
      ? api.formatConfidence(conf)
      : (typeof conf === "number" ? Math.round(conf * 100) + "%" : "--");
    const setText = (role, val) => {
      const el = this.root && this.root.querySelector('[data-role="' + role + '"]');
      if (el) el.textContent = val;
    };
    setText("confidence", confText);
    setText("evidence", String(trace.evidence_count));
    setText("generated", trace.generated_at || "--");
    setText("fallback", trace.fallback ? "是" : "否");
    setText("reason", trace.fallback_reason || "--");

    const ul = this.root && this.root.querySelector('[data-role="sources"]');
    if (ul) {
      const sources = Array.isArray(trace.sources) ? trace.sources : [];
      if (sources.length === 0) {
        ul.innerHTML = '<li class="yuyi-tp-source-empty">无数据来源</li>';
      } else {
        const items = sources.map((s) => {
          const ok = s.ok === true;
          const dur = typeof s.duration_ms === "number" ? " · " + s.duration_ms + "ms" : "";
          const err = s.error ? " · <span class=\"yuyi-tp-err\">" + s.escapeHTML(s.error) + "</span>" : "";
          return (
            '<li class="yuyi-tp-source ' + (ok ? "ok" : "err") + '">' +
            '<span class="yuyi-tp-source-pm">' + s.escapeHTML(s.provider || "?") + '</span>' +
            '<span class="yuyi-tp-source-dot">' + (ok ? "●" : "✕") + "</span>" +
            '<span class="yuyi-tp-source-method">' + s.escapeHTML(s.method || "?") + "</span>" +
            dur + err +
            "</li>"
          );
        });
        ul.innerHTML = items.join("");
      }
    }
  };

  // 简易 HTML escape(避免 XSS)
  if (typeof String.prototype.escapeHTML !== "function") {
    String.prototype.escapeHTML = function () {
      return String(this)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    };
  }

  /**
   * 全局 confidence toggle 按钮创建器(供任意页面调用)。
   * @param {HTMLElement|string} root
   */
  function mountConfidenceToggle(root) {
    const el = typeof root === "string" ? $(root) : root;
    if (!el) return null;
    const api = global.YuyiDashboardAPI;
    const update = () => {
      const on = api && api.getConfidenceToggle ? api.getConfidenceToggle() : false;
      el.innerHTML = on
        ? '<label class="yuyi-tp-toggle"><input type="checkbox" checked /> 显示数据来源</label>'
        : '<label class="yuyi-tp-toggle"><input type="checkbox" /> 显示数据来源</label>';
      const cb = el.querySelector("input[type=checkbox]");
      if (cb) {
        cb.addEventListener("change", () => {
          if (api && api.setConfidenceToggle) api.setConfidenceToggle(cb.checked);
          // 触发全局事件,让其他组件响应
          try {
            global.dispatchEvent(new CustomEvent("yuyi:confidence-toggle", { detail: { enabled: cb.checked } }));
          } catch (e) { /* 静默 */ }
        });
      }
    };
    update();
    // 监听 storage 变化(支持跨 tab 同步)
    try {
      global.addEventListener("storage", (e) => {
        if (e.key === "yuyi.dashboard.confidence_enabled") update();
      });
    } catch (e) { /* 静默 */ }
    return { refresh: update };
  }

  global.YuyiTracePanel = TracePanel;
  global.YuyiConfidenceToggle = { mount: mountConfidenceToggle };
})(window);
