/* ============================================
   羽依自主智能面板 — Yuyi Autonomous Dashboard
   Phase 3: 目标、反思、主动行为、模拟
   ============================================ */

const YuyiAutonomous = (function() {
  const API_BASE = '/admin/api';

  async function api_get(path) {
    const res = await fetch(API_BASE + path);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }

  function renderGoalCard(goal) {
    const statusColor = {
      created: 'var(--c-text-muted)',
      active: 'var(--c-success)',
      in_progress: 'var(--c-accent)',
      paused: 'var(--c-warning)',
      completed: '#22c55e',
      abandoned: '#ef4444',
      failed: '#ef4444',
    }[goal.status] || 'var(--c-text-muted)';

    return `
      <div class="cognitive-card" style="margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
          <span style="font-weight:600;font-size:14px;">${escapeHtml(goal.description)}</span>
          <span style="font-size:11px;color:${statusColor};font-weight:600;">${goal.status}</span>
        </div>
        <div style="display:flex;gap:12px;font-size:12px;color:var(--c-text-light);">
          <span>类型: ${goal.goal_type}</span>
          <span>优先级: ${goal.priority}/10</span>
          <span>进度: ${Math.round((goal.progress || 0) * 100)}%</span>
        </div>
        <div class="progress-bar" style="margin-top:6px;height:4px;background:rgba(0,0,0,0.05);border-radius:2px;overflow:hidden;">
          <div style="width:${Math.round((goal.progress || 0) * 100)}%;height:100%;background:linear-gradient(90deg,var(--c-accent),var(--c-accent-light));border-radius:2px;transition:width 0.5s ease;"></div>
        </div>
      </div>
    `;
  }

  function renderReflectionCard(reflection) {
    const typeLabel = {
      daily: '每日反思',
      event_based: '事件反思',
      milestone: '里程碑反思',
      periodic: '定期反思',
      manual: '手动反思',
    }[reflection.reflection_type] || reflection.reflection_type;

    const insights = (reflection.insights || []).map(i => `<div style="font-size:12px;color:var(--c-text-light);margin:2px 0;">• ${escapeHtml(i)}</div>`).join('');
    const improvements = (reflection.improvements || []).map(i => `<div style="font-size:12px;color:var(--c-accent);margin:2px 0;">→ ${escapeHtml(i)}</div>`).join('');

    return `
      <div class="cognitive-card" style="margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
          <span style="font-weight:600;font-size:14px;">${typeLabel}</span>
          <span style="font-size:11px;color:var(--c-text-muted);">${formatTime(reflection.completed_at || reflection.started_at)}</span>
        </div>
        ${insights ? `<div style="margin:4px 0;">${insights}</div>` : ''}
        ${improvements ? `<div style="margin:4px 0;">${improvements}</div>` : ''}
      </div>
    `;
  }

  function renderActionCard(action) {
    const typeLabel = {
      greeting: '问候',
      reminder: '提醒',
      care: '关怀',
      share: '分享',
      suggestion: '建议',
      checkin: '状态询问',
    }[action.action_type] || action.action_type;

    const outcomeColor = {
      executed: '#22c55e',
      rejected: '#ef4444',
      deferred: '#f59e0b',
      skipped: 'var(--c-text-muted)',
    }[action.outcome] || 'var(--c-text-muted)';

    return `
      <div class="cognitive-card" style="margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
          <span style="font-weight:600;font-size:14px;">${typeLabel}</span>
          <span style="font-size:11px;color:${outcomeColor};font-weight:600;">${action.outcome || 'pending'}</span>
        </div>
        <div style="font-size:13px;color:var(--c-text);margin-bottom:4px;">${escapeHtml(action.content)}</div>
        <div style="display:flex;gap:12px;font-size:11px;color:var(--c-text-light);">
          <span>必要性: ${Math.round((action.necessity_score || 0) * 100)}%</span>
          <span>打扰风险: ${Math.round((action.disturbance_risk || 0) * 100)}%</span>
          <span>信心: ${Math.round((action.confidence || 0) * 100)}%</span>
        </div>
        ${action.outcome_reason ? `<div style="font-size:11px;color:var(--c-text-muted);margin-top:4px;">${escapeHtml(action.outcome_reason)}</div>` : ''}
      </div>
    `;
  }

  function renderSimulationCard(sim) {
    const outcomes = (sim.outcomes || []).map(o => `
      <div style="display:flex;justify-content:space-between;font-size:12px;margin:2px 0;">
        <span style="color:var(--c-text-light);">${escapeHtml(o.metric)}</span>
        <span>${escapeHtml(String(o.before))} → <span style="color:var(--c-accent);font-weight:600;">${escapeHtml(String(o.after))}</span></span>
      </div>
    `).join('');

    const riskColor = { low: '#22c55e', medium: '#f59e0b', high: '#ef4444' }[sim.risk_level] || 'var(--c-text-muted)';

    return `
      <div class="cognitive-card" style="margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
          <span style="font-weight:600;font-size:14px;">${escapeHtml(sim.description || '模拟')}</span>
          <span style="font-size:11px;color:${riskColor};font-weight:600;">风险: ${sim.risk_level || 'low'}</span>
        </div>
        ${outcomes ? `<div style="margin:4px 0;">${outcomes}</div>` : ''}
        ${sim.recommendation ? `<div style="font-size:12px;color:var(--c-accent);margin-top:4px;">💡 ${escapeHtml(sim.recommendation)}</div>` : ''}
      </div>
    `;
  }

  function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function formatTime(ts) {
    if (!ts) return '--';
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  async function loadGoals() {
    try {
      const data = await api_get('/autonomous/goals?mock=true');
      const container = document.getElementById('autonomous-goals');
      if (!container) return;
      const goals = data.goals || [];
      if (goals.length === 0) {
        container.innerHTML = '<div class="loading" style="color:var(--c-text-muted);">暂无目标</div>';
      } else {
        container.innerHTML = goals.map(renderGoalCard).join('');
      }
      const stats = data.stats || {};
      document.getElementById('goal-count').textContent = stats.active_goals || 0;
    } catch (e) {
      console.error('loadGoals failed:', e);
    }
  }

  async function loadReflections() {
    try {
      const data = await api_get('/autonomous/reflections?mock=true');
      const container = document.getElementById('autonomous-reflections');
      if (!container) return;
      const reflections = data.reflections || [];
      if (reflections.length === 0) {
        container.innerHTML = '<div class="loading" style="color:var(--c-text-muted);">暂无反思记录</div>';
      } else {
        container.innerHTML = reflections.map(renderReflectionCard).join('');
      }
      const stats = data.stats || {};
      document.getElementById('reflection-count').textContent = stats.today_reflections || 0;
    } catch (e) {
      console.error('loadReflections failed:', e);
    }
  }

  async function loadProactive() {
    try {
      const data = await api_get('/autonomous/proactive?mock=true');
      const container = document.getElementById('autonomous-proactive');
      if (!container) return;
      const actions = data.actions || [];
      if (actions.length === 0) {
        container.innerHTML = '<div class="loading" style="color:var(--c-text-muted);">暂无主动行为</div>';
      } else {
        container.innerHTML = actions.map(renderActionCard).join('');
      }
      const stats = data.stats || {};
      document.getElementById('proactive-count').textContent = stats.executed || 0;
    } catch (e) {
      console.error('loadProactive failed:', e);
    }
  }

  async function loadDream() {
    try {
      const data = await api_get('/autonomous/dream?mock=true');
      const container = document.getElementById('autonomous-dream');
      if (!container) return;
      const simulations = data.simulations || [];
      if (simulations.length === 0) {
        container.innerHTML = '<div class="loading" style="color:var(--c-text-muted);">暂无模拟记录</div>';
      } else {
        container.innerHTML = simulations.map(renderSimulationCard).join('');
      }
    } catch (e) {
      console.error('loadDream failed:', e);
    }
  }

  async function loadGrowthPending() {
    try {
      const data = await api_get('/autonomous/growth-pending?mock=true');
      const stats = data.stats || {};
      document.getElementById('growth-pending-count').textContent = stats.pending || 0;
    } catch (e) {
      console.error('loadGrowthPending failed:', e);
    }
  }

  async function loadAll() {
    await Promise.all([
      loadGoals(),
      loadReflections(),
      loadProactive(),
      loadDream(),
      loadGrowthPending(),
    ]);
  }

  return {
    loadAll,
    loadGoals,
    loadReflections,
    loadProactive,
    loadDream,
    loadGrowthPending,
  };
})();
