/* ============================================
   羽依 Yuyi Console — 主应用逻辑
   API 封装 · 日志 · 视图切换 · Toast
   渲染逻辑由 dashboard.js 提供
   ============================================ */

const API_BASE = '/admin/api';
let _currentView = 'dashboard';
let _logFilter = null;

/* ============ API 封装 ============ */

async function api(method, path, data = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (data !== null) opts.body = JSON.stringify(data);

  try {
    const res = await fetch(API_BASE + path, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.error(`API ${method} ${path} failed:`, e);
    throw e;
  }
}

const api_get = (path) => api('GET', path);
const api_post = (path, data) => api('POST', path, data);
const api_put = (path, data) => api('PUT', path, data);

/* ============ Dashboard ============ */

async function refreshDashboard() {
  try {
    const data = await api_get('/dashboard');
    renderDashboard(data);
    showToast('控制面板已刷新', 'success');
  } catch (e) {
    showToast('刷新失败: ' + e.message, 'error');
  }
}

function renderDashboard(data) {
  if (!data) return;

  // 顶部状态栏
  document.getElementById('uptime').textContent = data.uptime || '--';
  document.getElementById('agent-count').textContent = data.online_agents || 0;

  const health = data.health || {};
  const statusMap = {
    healthy: { text: '良好', color: 'var(--c-success)' },
    warning: { text: '注意', color: 'var(--c-warning)' },
    critical: { text: '异常', color: 'var(--c-error)' },
  };
  const hs = statusMap[health.status] || statusMap.healthy;
  const healthEl = document.getElementById('health-status');
  healthEl.textContent = hs.text;
  healthEl.style.color = hs.color;

  document.getElementById('cpu-usage').textContent =
    health.cpu_percent ? `${health.cpu_percent}%` : '--';
  document.getElementById('memory-usage').textContent =
    health.memory_mb ? `${health.memory_mb} MB` : '--';

  // 委托给 dashboard.js 渲染
  if (typeof handleDashboardData === 'function') {
    handleDashboardData(data);
  }

  if (data.modules && typeof renderModules === 'function') {
    renderModules(data.modules);
  }

  if (data.yuyi && typeof renderEmotion === 'function') {
    renderEmotion(data.yuyi);
  }

  if (data.yuyi && typeof renderPersonalityTraits === 'function') {
    renderPersonalityTraits(data.yuyi);
  }
}

/* ============ 模块操作 ============ */

async function toggleModule(name) {
  try {
    const info = await api_get('/modules');
    const mod = (info.modules || []).find(m => m.name === name);
    if (!mod) return;

    if (mod.enabled) {
      const result = await api_post(`/module/${name}/stop`);
      if (result.success) {
        handleModuleAction('module_stop', name, result);
      } else {
        handleModuleError('module_stop', name, result);
      }
    } else {
      const result = await api_post(`/module/${name}/start`);
      if (result.success) {
        handleModuleAction('module_start', name, result);
      } else {
        handleModuleError('module_start', name, result);
      }
    }
    refreshDashboard();
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error');
  }
}

function handleModuleAction(action, moduleName, result) {
  // 时间线记录
  if (typeof YuyiTimeline !== 'undefined') {
    YuyiTimeline.addEvent(action, { module_name: moduleName, message: result.message });
  }

  // 音效反馈
  if (typeof YuyiAudio !== 'undefined') {
    const isStart = action === 'module_start';
    YuyiAudio.play(isStart ? 'success' : 'sleep');
  }

  // 对话式回复
  if (typeof YuyiReplies !== 'undefined') {
    const emotion = isStart ? 'happy' : 'calm';
    const reply = YuyiReplies.getReply(action, emotion, { module: moduleName });
    showToast(reply, 'success');

    if (typeof YuyiAvatar !== 'undefined') {
      YuyiAvatar.speak(reply, emotion);
    }

    if (typeof YuyiState !== 'undefined') {
      YuyiState.update({ emotion, message: reply });
    }
  } else {
    showToast(result.message || '操作成功', 'success');
  }
}

function handleModuleError(action, moduleName, result) {
  if (typeof YuyiTimeline !== 'undefined') {
    YuyiTimeline.addEvent('error', { module_name: moduleName, message: result.error });
  }
  if (typeof YuyiAudio !== 'undefined') {
    YuyiAudio.play('error');
  }
  if (typeof YuyiReplies !== 'undefined') {
    const reply = YuyiReplies.getReply('error_generic', 'worried');
    showToast(reply, 'error');
    if (typeof YuyiAvatar !== 'undefined') {
      YuyiAvatar.speak(reply, 'worried');
    }
  } else {
    showToast('操作失败: ' + (result.error || ''), 'error');
  }
}

async function reloadModule(name) {
  try {
    const result = await api_post(`/module/${name}/reload`);
    if (result.success) {
      if (typeof YuyiTimeline !== 'undefined') {
        YuyiTimeline.addEvent('module_reload', { module_name: name });
      }
      if (typeof YuyiAudio !== 'undefined') {
        YuyiAudio.play('sparkle');
      }
      if (typeof YuyiReplies !== 'undefined') {
        const reply = YuyiReplies.getReply('module_reload', 'happy', { module: name });
        showToast(reply, 'success');
        if (typeof YuyiAvatar !== 'undefined') {
          YuyiAvatar.speak(reply, 'happy');
        }
      } else {
        showToast(result.message, 'success');
      }
    } else {
      handleModuleError('module_reload', name, result);
    }
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error');
  }
}

/* ============ 日志 ============ */

async function refreshLogs() {
  try {
    const params = new URLSearchParams();
    if (_logFilter) params.set('module', _logFilter);
    const data = await api_get(`/logs?${params}`);
    renderLogs(data.logs || []);
  } catch (e) {
    console.error('刷新日志失败:', e);
  }
}

function renderLogs(logs) {
  const container = document.getElementById('logs-container');
  if (!container) return;

  container.innerHTML = '';
  if (logs.length === 0) {
    container.innerHTML = '<div class="loading">暂无日志</div>';
    return;
  }

  logs.slice(-50).forEach(log => {
    const entry = document.createElement('div');
    entry.className = 'log-entry';

    const level = detectLogLevel(log.message);
    const timeStr = new Date().toLocaleTimeString();

    entry.innerHTML = `
      <span class="log-time">${timeStr}</span>
      <span class="log-level ${level}">${level}</span>
      <span class="log-source">[${escapeHtml(log.source)}]</span>
      <span class="log-message">${escapeHtml(log.message)}</span>
    `;
    container.appendChild(entry);
  });

  container.scrollTop = container.scrollHeight;
}

function detectLogLevel(message) {
  const msg = (message || '').toUpperCase();
  if (msg.includes('ERROR') || msg.includes('失败') || msg.includes('错误')) return 'ERROR';
  if (msg.includes('WARN') || msg.includes('警告') || msg.includes('超时')) return 'WARN';
  return 'INFO';
}

function setLogFilter(level) {
  _logFilter = level === 'ALL' ? null : level;
  const buttons = { info: 'log-filter-info', warn: 'log-filter-warn', error: 'log-filter-error' };
  Object.entries(buttons).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('active', key === (_logFilter || '').toLowerCase());
  });
  refreshLogs();
}

/* ============ 操作 ============ */

async function apiAction(action) {
  try {
    let reply = '';
    let emotion = 'calm';

    if (action === 'reload_config') {
      reply = YuyiReplies ? YuyiReplies.getReply('config_save', 'happy') : '配置已重载';
      emotion = 'happy';
    } else if (action === 'save_backup') {
      reply = YuyiReplies ? YuyiReplies.getReply('config_save', 'happy') : '备份已保存';
      emotion = 'happy';
    } else if (action === 'system_check') {
      reply = YuyiReplies ? YuyiReplies.getReply('health_check', 'calm') : '系统检查完成';
      emotion = 'calm';
    } else if (action === 'clear_cache') {
      reply = '缓存已清理';
      emotion = 'calm';
    }

    const data = await api_post(`/action/${action}`);
    if (data.success) {
      if (reply) {
        showToast(reply, 'success');
        if (typeof YuyiAvatar !== 'undefined') {
          YuyiAvatar.speak(reply, emotion);
        }
        if (typeof YuyiTimeline !== 'undefined') {
          const timelineAction = action === 'reload_config' ? 'config_change' :
                                 action === 'save_backup' ? 'config_save' :
                                 action === 'system_check' ? 'health_check' : 'state_change';
          YuyiTimeline.addEvent(timelineAction, { message: reply });
        }
      } else {
        showToast(data.message || '操作成功', 'success');
      }

      if (action === 'reload_config') refreshDashboard();
      if (action === 'system_check' && data.health) {
        showToast(`健康分: ${data.health.score || '--'} 分`, 'info');
      }

      if (typeof YuyiAudio !== 'undefined') {
        YuyiAudio.play('success');
      }
    } else {
      handleModuleError(action, 'system', data);
    }
  } catch (e) {
    showToast('操作失败: ' + e.message, 'error');
  }
}

/* ============ 视图切换 ============ */

function switchView(view) {
  _currentView = view;

  document.querySelectorAll('.nav-item').forEach(item => {
    item.classList.toggle('active', item.dataset.view === view);
  });

  if (view === 'dashboard' || view === 'modules') {
    showDashboardView();
  } else if (view === 'config') {
    showConfigView();
  } else if (view === 'audit') {
    showAuditView();
  } else if (view === 'cognitive') {
    showCognitiveView();
  } else if (view === 'autonomous') {
    showAutonomousView();
  } else if (view === 'logs') {
    showLogsView();
  }
}

function showDashboardView() {
  document.getElementById('welcome-section').style.display = '';
  document.getElementById('modules-section').style.display = '';
  document.querySelector('.bottom-grid').style.display = '';

  document.querySelectorAll('.config-view, .audit-view, .cognitive-view').forEach(el => {
    el.style.display = 'none';
    el.classList.remove('active');
  });
}

function showConfigView() {
  hideDashboardView();
  let view = document.querySelector('.config-view');
  if (!view) {
    view = createConfigView();
    document.querySelector('.content').appendChild(view);
  }
  view.style.display = '';
  view.classList.add('active');
  loadConfigEditor();
}

function showAuditView() {
  hideDashboardView();
  let view = document.querySelector('.audit-view');
  if (!view) {
    view = createAuditView();
    document.querySelector('.content').appendChild(view);
  }
  view.style.display = '';
  view.classList.add('active');
  loadAuditLogs();
}

function showCognitiveView() {
  hideDashboardView();
  let view = document.querySelector('.cognitive-view');
  if (!view) {
    view = createCognitiveView();
    document.querySelector('.content').appendChild(view);
  }
  view.style.display = '';
  view.classList.add('active');
  // 加载认知数据
  if (typeof YuyiCognitive !== 'undefined') {
    YuyiCognitive.loadAll();
  }
  // 初始化状态原因解释（Phase 2.5）
  if (typeof YuyiReasoning !== 'undefined') {
    const reasoningContainerId = 'reasoning-container';
    if (YuyiReasoning.init(reasoningContainerId)) {
      YuyiReasoning.load('emotion', true); // 默认加载情绪状态解释
    }
  }
  // 初始化认知雷达（Phase 2.5）
  if (typeof YuyiRadar !== 'undefined') {
    const radarCanvasId = 'radar-canvas';
    if (YuyiRadar.init(radarCanvasId)) {
      YuyiRadar.load(true); // 默认使用 mock 数据
    }
  }
  // 初始化记忆关系网络（Phase 2.5）
  if (typeof YuyiMemoryGraph !== 'undefined') {
    const canvasId = 'memory-graph-canvas';
    if (YuyiMemoryGraph.init(canvasId)) {
      YuyiMemoryGraph.load(true); // 默认使用 mock 数据
    }
  }
  // 初始化关系成长时间线（Phase 2.5）
  if (typeof YuyiRelationshipTimeline !== 'undefined') {
    const timelineContainerId = 'relationship-timeline-container';
    if (YuyiRelationshipTimeline.init(timelineContainerId)) {
      YuyiRelationshipTimeline.load(true); // 默认使用 mock 数据
    }
  }
}

function createCognitiveView() {
  const view = document.createElement('section');
  view.className = 'section glass-card cognitive-view';
  view.innerHTML = `
    <div class="section-header">
      <h2>羽依认知控制台</h2>
      <div class="section-actions">
        <button class="icon-btn-sm" onclick="if(typeof YuyiCognitive!=='undefined')YuyiCognitive.loadAll()" title="刷新">
          <svg viewBox="0 0 24 24" width="14" height="14"><path d="M23 4v6h-6M1 20v-6h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
      </div>
    </div>

    <!-- 心智状态 -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">🧠 心智状态</h3>
      <div class="mind-grid">
        <div class="mind-chart-wrap">
          <canvas id="emotion-chart"></canvas>
        </div>
        <div class="mind-gauges">
          <div id="attention-display"></div>
          <div id="thinking-display"></div>
        </div>
      </div>
    </div>

    <!-- 状态原因解释（Phase 2.5） -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">💡 状态原因解释</h3>
      <div id="reasoning-container" class="reasoning-container"></div>
    </div>

    <!-- 认知雷达（Phase 2.5） -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">⚡ 认知雷达</h3>
      <div class="radar-container">
        <canvas id="radar-canvas" width="500" height="400"></canvas>
      </div>
    </div>

    <!-- 记忆关系网络（Phase 2.5） -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">🌐 记忆关系网络</h3>
      <div class="memory-graph-container">
        <canvas id="memory-graph-canvas" width="800" height="400"></canvas>
        <div id="memory-graph-detail" class="memory-graph-detail"></div>
      </div>
    </div>

    <!-- 记忆与关系 -->
    <div class="cognitive-two-col">
      <div class="cognitive-section">
        <h3 class="cognitive-section-title">💭 记忆空间</h3>
        <div id="memory-identity"></div>
        <div id="memory-stats"></div>
        <div id="memory-events" class="cognitive-scroll"></div>
      </div>
      <div class="cognitive-section">
        <h3 class="cognitive-section-title">💕 关系层</h3>
        <div class="relation-gauges-row">
          <div id="trust-gauge"></div>
          <div id="familiarity-gauge"></div>
        </div>
        <div id="interaction-stats"></div>
        <!-- 关系成长时间线（Phase 2.5） -->
        <div id="relationship-timeline-container" class="relationship-timeline-container"></div>
        <div id="milestones-list" class="cognitive-scroll"></div>
      </div>
    </div>

    <!-- 成长系统 -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">🌱 成长系统</h3>
      <div id="growth-stage"></div>
      <div class="growth-two-col">
        <div id="growth-records"></div>
        <div id="growth-proposals"></div>
      </div>
    </div>
  `;
  return view;
}

function showLogsView() {
  showDashboardView();
  setTimeout(() => {
    const logsSection = document.querySelector('.logs-section');
    if (logsSection) logsSection.scrollIntoView({ behavior: 'smooth' });
  }, 100);
}

function showAutonomousView() {
  hideDashboardView();
  let view = document.querySelector('.autonomous-view');
  if (!view) {
    view = createAutonomousView();
    document.querySelector('.content').appendChild(view);
  }
  view.style.display = '';
  view.classList.add('active');
  if (typeof YuyiAutonomous !== 'undefined') {
    YuyiAutonomous.loadAll();
  }
}

function createAutonomousView() {
  const view = document.createElement('section');
  view.className = 'section glass-card autonomous-view';
  view.innerHTML = `
    <div class="section-header">
      <h2>羽依自主智能控制台</h2>
      <div class="section-actions">
        <button class="icon-btn-sm" onclick="if(typeof YuyiAutonomous!=='undefined')YuyiAutonomous.loadAll()" title="刷新">
          <svg viewBox="0 0 24 24" width="14" height="14"><path d="M23 4v6h-6M1 20v-6h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
      </div>
    </div>

    <!-- 自主智能概览 -->
    <div class="autonomous-overview">
      <div class="overview-card">
        <div class="overview-title">目标系统</div>
        <div class="overview-value" id="goal-count">0</div>
        <div class="overview-label">活跃目标</div>
      </div>
      <div class="overview-card">
        <div class="overview-title">反思系统</div>
        <div class="overview-value" id="reflection-count">0</div>
        <div class="overview-label">今日反思</div>
      </div>
      <div class="overview-card">
        <div class="overview-title">主动行为</div>
        <div class="overview-value" id="proactive-count">0</div>
        <div class="overview-label">已执行</div>
      </div>
      <div class="overview-card">
        <div class="overview-title">成长方案</div>
        <div class="overview-value" id="growth-pending-count">0</div>
        <div class="overview-label">待审批</div>
      </div>
    </div>

    <!-- 目标系统 -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">🎯 自主目标</h3>
      <div id="autonomous-goals"></div>
    </div>

    <!-- 反思系统 -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">🪞 自我反思</h3>
      <div id="autonomous-reflections"></div>
    </div>

    <!-- 主动行为 -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">🚀 主动行为引擎</h3>
      <div id="autonomous-proactive"></div>
    </div>

    <!-- 梦境模拟 -->
    <div class="cognitive-section">
      <h3 class="cognitive-section-title">💭 情景模拟</h3>
      <div id="autonomous-dream"></div>
    </div>
  `;
  return view;
}

function hideDashboardView() {
  document.getElementById('welcome-section').style.display = 'none';
  document.getElementById('modules-section').style.display = 'none';
  const bottomGrid = document.querySelector('.bottom-grid');
  if (bottomGrid) bottomGrid.style.display = 'none';
  document.querySelectorAll('.cognitive-view').forEach(el => {
    el.style.display = 'none';
    el.classList.remove('active');
  });
}

/* ============ 配置视图 ============ */

function createConfigView() {
  const view = document.createElement('section');
  view.className = 'section glass-card config-view';
  view.innerHTML = `
    <div class="section-header">
      <h2>配置管理</h2>
    </div>
    <p style="color:var(--c-text-light);font-size:13px;margin-bottom:12px;">
      编辑 config.yaml，修改后点击保存。支持回滚到历史版本。
    </p>
    <textarea id="config-editor" class="config-editor" spellcheck="false"></textarea>
    <div class="config-toolbar">
      <button class="action-btn" onclick="saveConfig()">
        <div class="action-icon" style="background:linear-gradient(135deg,#dcfce7,#bbf7d0)">
          <svg viewBox="0 0 24 24" width="16" height="16"><polyline points="20,6 9,17 4,12" fill="none" stroke="#22c55e" stroke-width="2" stroke-linecap="round"/></svg>
        </div>
        <span>保存配置</span>
      </button>
      <button class="action-btn" onclick="reloadConfigEditor()">
        <div class="action-icon" style="background:linear-gradient(135deg,#dbeafe,#bfdbfe)">
          <svg viewBox="0 0 24 24" width="16" height="16"><path d="M23 4v6h-6M1 20v-6h6" fill="none" stroke="#3b82f6" stroke-width="2" stroke-linecap="round"/></svg>
        </div>
        <span>重新加载</span>
      </button>
    </div>
    <div style="margin-top:20px;">
      <div class="section-header"><h3 style="font-size:14px;">历史备份</h3></div>
      <div id="config-backups" style="display:flex;gap:8px;flex-wrap:wrap;"></div>
    </div>
  `;
  return view;
}

async function loadConfigEditor() {
  try {
    const data = await api_get('/config');
    const config = data.config || {};
    document.getElementById('config-editor').value = JSON.stringify(config, null, 2);

    const history = await api_get('/config/history');
    const backups = history.backups || [];
    const container = document.getElementById('config-backups');
    if (container) {
      container.innerHTML = '';
      backups.slice(0, 10).forEach(b => {
        const btn = document.createElement('button');
        btn.className = 'chip-btn';
        btn.textContent = `${b.modified} (${b.size}B)`;
        btn.onclick = () => rollbackConfig(b.filename);
        container.appendChild(btn);
      });
    }
  } catch (e) {
    showToast('加载配置失败', 'error');
  }
}

function reloadConfigEditor() {
  loadConfigEditor();
  showToast('配置已重新加载', 'success');
}

async function saveConfig() {
  try {
    const raw = document.getElementById('config-editor').value;
    const config = JSON.parse(raw);
    const result = await api_put('/config', { config, operator: 'admin' });
    if (result.success) showToast('配置已保存', 'success');
    else showToast('保存失败: ' + (result.errors?.join(', ') || ''), 'error');
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}

async function rollbackConfig(filename) {
  if (!confirm(`确定回滚到 ${filename} 吗？`)) return;
  try {
    const result = await api_post('/config/rollback', { filename, operator: 'admin' });
    if (result.success) {
      showToast('已回滚', 'success');
      loadConfigEditor();
    } else {
      showToast('回滚失败', 'error');
    }
  } catch (e) {
    showToast('回滚失败: ' + e.message, 'error');
  }
}

/* ============ 审计视图 ============ */

function createAuditView() {
  const view = document.createElement('section');
  view.className = 'section glass-card audit-view';
  view.innerHTML = `
    <div class="section-header">
      <h2>操作日志</h2>
      <div class="section-actions">
        <input id="audit-filter" class="chip-btn" style="min-width:180px;" placeholder="按事件/操作者/目标过滤">
      </div>
    </div>
    <div id="audit-summary" style="display:flex;gap:16px;margin-bottom:16px;"></div>
    <div style="overflow-x:auto;">
      <table class="audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>事件</th>
            <th>操作者</th>
            <th>类型</th>
            <th>目标</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody id="audit-tbody">
          <tr><td colspan="6" style="text-align:center;padding:20px;color:var(--c-text-muted);">加载中...</td></tr>
        </tbody>
      </table>
    </div>
  `;

  const filter = view.querySelector('#audit-filter');
  filter.addEventListener('keypress', e => {
    if (e.key === 'Enter') loadAuditLogs(filter.value);
  });

  return view;
}

async function loadAuditLogs(filter = '') {
  try {
    const params = new URLSearchParams();
    if (filter) {
      const parts = filter.split('|').map(s => s.trim());
      if (parts[0]) params.set('event_type', parts[0]);
    }
    const data = await api_get(`/audit?${params}`);

    const summary = data.summary || {};
    const summaryDiv = document.getElementById('audit-summary');
    if (summaryDiv) {
      summaryDiv.innerHTML = `
        <div class="glass-card" style="flex:1;padding:12px;">
          <div style="font-size:12px;color:var(--c-text-muted);">总操作</div>
          <div style="font-size:20px;font-weight:700;color:var(--c-accent);">${summary.total || 0}</div>
        </div>
        <div class="glass-card" style="flex:1;padding:12px;">
          <div style="font-size:12px;color:var(--c-text-muted);">成功</div>
          <div style="font-size:20px;font-weight:700;color:#22c55e;">${(summary.by_result?.success || 0)}</div>
        </div>
        <div class="glass-card" style="flex:1;padding:12px;">
          <div style="font-size:12px;color:var(--c-text-muted);">失败</div>
          <div style="font-size:20px;font-weight:700;color:#ef4444;">${(summary.by_result?.failure || 0)}</div>
        </div>
      `;
    }

    const tbody = document.getElementById('audit-tbody');
    if (tbody) {
      const logs = data.logs || [];
      if (logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--c-text-muted);">暂无记录</td></tr>';
      } else {
        tbody.innerHTML = logs.map(log => `
          <tr>
            <td style="color:var(--c-text-muted);">${log.timestamp?.substring(11, 19) || '--'}</td>
            <td><span class="chip-btn" style="font-size:11px;padding:2px 8px;">${log.event_type}</span></td>
            <td>${log.operator}</td>
            <td style="color:var(--c-text-muted);font-size:12px;">${log.operator_type || '--'}</td>
            <td>${log.target || '--'}</td>
            <td style="color:${log.result === 'success' ? '#22c55e' : '#ef4444'};font-weight:600;">
              ${log.result === 'success' ? '成功' : '失败'}
            </td>
          </tr>
        `).join('');
      }
    }
  } catch (e) {
    showToast('加载审计日志失败', 'error');
  }
}

/* ============ 人格视图 ============ */

/* ============ Toast ============ */

function showToast(message, type = 'info') {
  if (typeof YuyiToast !== 'undefined') {
    YuyiToast.show(message, { type });
  } else {
    console.log(`[${type}] ${message}`);
  }
}

/* ============ 工具 ============ */

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

/* ============ 初始化 ============ */

document.addEventListener('DOMContentLoaded', async () => {
  // 初始化主题管理器
  if (typeof YuyiThemeManager !== 'undefined') {
    try {
      await YuyiThemeManager.init();
    } catch (e) {
      console.warn('Theme init failed:', e.message);
    }
  }

  // 初始化背景粒子引擎
  if (typeof YuyiBackground !== 'undefined') {
    try {
      YuyiBackground.init();
    } catch (e) {
      console.warn('Background init failed:', e.message);
    }
  }

  // 初始化音效系统
  if (typeof YuyiAudio !== 'undefined') {
    YuyiAudio.init();
    YuyiAudio.setEnabled(true);
  }

  // 初始化状态管理器（默认 sleepy 匹配角色形象）
  if (typeof YuyiState !== 'undefined') {
    YuyiState.initialize({
      emotion: 'sleepy',
      energy: 50,
      activity: 'idle',
      message: '正在为您待命~',
    });
  }

  // 初始化 Avatar 接口（使用 SVG 适配器）
  if (typeof YuyiAvatar !== 'undefined') {
    YuyiAvatar.createSVGAdapter();
  }

  // 初始化角色控制器（默认 sleepy 匹配角色形象）
  if (typeof YuyiCharacter !== 'undefined') {
    YuyiCharacter.init({ expression: 'sleepy', energy: 50, message: '正在为您待命~' });
  }

  // 初始化时间线
  if (typeof YuyiTimeline !== 'undefined') {
    YuyiTimeline.setupAutoTracking();
    YuyiTimeline.addEvent('system_start', { message: '羽依控制中枢启动' });
    YuyiTimeline.render('timeline-container');
    updateTimelineCount();

    YuyiTimeline.subscribe((event, data) => {
      if (event === 'add') {
        setTimeout(() => {
          YuyiTimeline.render('timeline-container');
          updateTimelineCount();
        }, 100);
      }
    });
  }

  // 状态管理器 → 角色控制器联动
  if (typeof YuyiState !== 'undefined' && typeof YuyiCharacter !== 'undefined') {
    YuyiState.subscribe('change:emotion', (data) => {
      if (typeof YuyiAvatar !== 'undefined') {
        YuyiAvatar.setEmotion(data.to);
      }
    });
    YuyiState.subscribe('change:energy', (data) => {
      if (typeof YuyiAvatar !== 'undefined') {
        YuyiAvatar.setEnergy(data.to);
      }
    });
    YuyiState.subscribe('change:message', (data) => {
      if (typeof YuyiAvatar !== 'undefined') {
        YuyiAvatar.setMessage(data.to);
      }
    });
  }

  // 初始加载
  refreshDashboard();
  refreshLogs();

  // 定时刷新 Dashboard
  setInterval(() => {
    if (_currentView === 'dashboard' || _currentView === 'modules') {
      refreshDashboard();
    }
  }, 10000);

  // 日志每 5 秒刷新
  setInterval(refreshLogs, 5000);

  // 欢迎音效和回复
  if (typeof YuyiAudio !== 'undefined') {
    YuyiAudio.play('wake');
  }
  if (typeof YuyiReplies !== 'undefined') {
    const reply = YuyiReplies.getReply('greeting', 'happy');
    showToast(reply, 'success');
    if (typeof YuyiAvatar !== 'undefined') {
      YuyiAvatar.speak(reply, 'happy');
    }
  }
});

function updateTimelineCount() {
  if (typeof YuyiTimeline === 'undefined') return;
  const stats = YuyiTimeline.getStatistics();
  const el = document.getElementById('timeline-count');
  if (el) {
    el.textContent = `${stats.today} 今日记录`;
  }
}
