/* ─── TrustDecay Frontend ─── */

const API     = '';
const POLL_MS = 2500;

let graphData     = null;
let eventsCache   = [];
let relationships = [];
let selectedNode  = null;

// ─── API ────────────────────────────────────────────────────────────────────

async function apiFetch(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

// ─── Toast ──────────────────────────────────────────────────────────────────

function toast(msg, type = 'info', duration = 3500) {
  const stack = document.getElementById('toast-stack');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.animation = 'toast-out 0.2s ease forwards';
    setTimeout(() => el.remove(), 220);
  }, duration);
}

// ─── Tabs ───────────────────────────────────────────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'pane-convergence') renderConvergence();
  });
});

// When "From node" changes, update prop-rel to only show what that node holds
document.getElementById('prop-from').addEventListener('change', () => {
  if (!graphData) return;
  const fromId  = document.getElementById('prop-from').value;
  const srcNode = fromId ? graphData.nodes.find(n => n.node_id === fromId) : null;
  const srcRels = srcNode ? srcNode.trust.map(t => t.relationship_id) : relationships;
  const relSel  = document.getElementById('prop-rel');
  const cur     = relSel.value;
  relSel.innerHTML = `<option value="">Relationship…</option>` +
    srcRels.map(r => `<option value="${r}"${r === cur ? ' selected' : ''}>${r}</option>`).join('');
  hideResult('prop-result');
});

['prop-rel', 'prop-to'].forEach(id =>
  document.getElementById(id).addEventListener('change', () => hideResult('prop-result'))
);
['auth-node', 'auth-rel'].forEach(id =>
  document.getElementById(id).addEventListener('change', () => hideResult('auth-result'))
);

// ─── Health ─────────────────────────────────────────────────────────────────

async function pollHealth() {
  const pill = document.getElementById('health-pill');
  const text = document.getElementById('health-text');
  try {
    await apiFetch('/health');
    pill.className = 'status-pill ok';
    text.textContent = 'Connected';
  } catch {
    pill.className = 'status-pill err';
    text.textContent = 'Disconnected';
  }
}

// ─── Graph Canvas ────────────────────────────────────────────────────────────

const CANVAS  = document.getElementById('graph-canvas');
const CTX     = CANVAS.getContext('2d');
const TOOLTIP = document.getElementById('graph-tooltip');

let nodePositions = {};
let hoveredNode   = null;

function computeLayout(nodes, W, H) {
  const cx = W / 2;
  const cy = H / 2;
  const r  = Math.min(cx, cy) * 0.60;
  const n  = nodes.length;
  const positions = {};
  nodes.forEach((node, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    positions[node.node_id] = {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    };
  });
  return positions;
}

function nodeColors(node) {
  if (node.connectivity === 'OFFLINE')      return { fill: '#fef2f2', stroke: '#fca5a5', text: '#b91c1c' };
  if (node.lifecycle   === 'RECONCILING')   return { fill: '#fffbeb', stroke: '#fde68a', text: '#92400e' };
  return { fill: '#f5f5f5', stroke: '#d4d4d4', text: '#0a0a0a' };
}

function trustSummary(node) {
  const trusted = node.trust.filter(t => t.status === 'TRUSTED').map(t => t.relationship_id);
  const blocked = node.trust.filter(t => t.status === 'BLOCKED').map(t => t.relationship_id);
  return { trusted, blocked };
}

function renderGraph() {
  if (!graphData) return;

  const wrap = CANVAS.parentElement;
  const dpr  = window.devicePixelRatio || 1;
  const W    = wrap.clientWidth;
  const H    = wrap.clientHeight;
  if (CANVAS.width !== W * dpr || CANVAS.height !== H * dpr) {
    CANVAS.width       = W * dpr;
    CANVAS.height      = H * dpr;
    CANVAS.style.width  = W + 'px';
    CANVAS.style.height = H + 'px';
  }
  CTX.setTransform(dpr, 0, 0, dpr, 0, 0);
  CTX.clearRect(0, 0, W, H);

  const nodes = graphData.nodes;
  nodePositions = computeLayout(nodes, W, H);

  // Authority lines
  nodes.forEach(node => {
    node.trust.forEach(tr => {
      if (tr.source_node === 'AUTHORITY') {
        drawLine(W / 2, H / 2, nodePositions[node.node_id], '#d4d4d4', [4, 5]);
      }
    });
  });

  // Propagation edges
  const drawn = new Set();
  nodes.forEach(node => {
    node.trust.forEach(tr => {
      if (tr.source_node !== 'AUTHORITY' && tr.source_node !== node.node_id) {
        const key = `${tr.source_node}→${node.node_id}`;
        if (!drawn.has(key)) {
          drawn.add(key);
          const color = tr.status === 'BLOCKED' ? '#fca5a5' : '#a3a3a3';
          drawCurvedLine(nodePositions[tr.source_node], nodePositions[node.node_id], color);
        }
      }
    });
  });

  // Authority node
  drawAuthority(W / 2, H / 2);

  // Nodes
  nodes.forEach(node => {
    const pos = nodePositions[node.node_id];
    if (pos) drawNode(node, pos.x, pos.y, node.node_id === hoveredNode);
  });
}

function drawLine(ax, ay, to, color, dash = []) {
  if (!to) return;
  CTX.save();
  CTX.beginPath();
  CTX.setLineDash(dash);
  CTX.strokeStyle = color;
  CTX.lineWidth = 1;
  CTX.moveTo(ax, ay);
  CTX.lineTo(to.x, to.y);
  CTX.stroke();
  CTX.restore();
}

function drawCurvedLine(from, to, color) {
  if (!from || !to) return;
  CTX.save();
  CTX.beginPath();
  CTX.setLineDash([3, 5]);
  CTX.strokeStyle = color;
  CTX.lineWidth = 1.2;
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2 - 22;
  CTX.moveTo(from.x, from.y);
  CTX.quadraticCurveTo(mx, my, to.x, to.y);
  CTX.stroke();
  CTX.restore();
}

function drawAuthority(cx, cy) {
  const s = 17;
  CTX.save();
  CTX.beginPath();
  CTX.moveTo(cx, cy - s);
  CTX.lineTo(cx + s, cy);
  CTX.lineTo(cx, cy + s);
  CTX.lineTo(cx - s, cy);
  CTX.closePath();
  CTX.fillStyle = '#f0f0f0';
  CTX.strokeStyle = '#aaa';
  CTX.lineWidth = 1.5;
  CTX.fill();
  CTX.stroke();
  CTX.fillStyle = '#555';
  CTX.font = '600 8px Inter, sans-serif';
  CTX.textAlign = 'center';
  CTX.textBaseline = 'middle';
  CTX.fillText('AUTH', cx, cy);
  CTX.restore();
}

function drawNode(node, x, y, hovered) {
  const R   = 24;
  const col = nodeColors(node);
  const { trusted, blocked } = trustSummary(node);

  CTX.save();

  if (hovered) {
    CTX.beginPath();
    CTX.arc(x, y, R + 7, 0, 2 * Math.PI);
    CTX.strokeStyle = 'rgba(0,0,0,0.08)';
    CTX.lineWidth = 1;
    CTX.stroke();
  }

  CTX.beginPath();
  CTX.arc(x, y, R, 0, 2 * Math.PI);
  CTX.fillStyle = col.fill;
  CTX.fill();
  CTX.strokeStyle = col.stroke;
  CTX.lineWidth = hovered ? 2 : 1.5;
  CTX.stroke();

  CTX.fillStyle = col.text;
  CTX.font = '700 14px JetBrains Mono, monospace';
  CTX.textAlign = 'center';
  CTX.textBaseline = 'middle';
  CTX.fillText(node.node_id, x, y - 1);

  // Trust dots
  const dots = [
    ...trusted.map(() => '#16a34a'),
    ...blocked.map(() => '#ef4444'),
  ];
  const dotR   = 3;
  const spacing = dotR * 2 + 3;
  const startX  = x - ((dots.length - 1) * spacing) / 2;
  dots.forEach((color, i) => {
    CTX.beginPath();
    CTX.arc(startX + i * spacing, y + R + 8, dotR, 0, 2 * Math.PI);
    CTX.fillStyle = color;
    CTX.fill();
  });

  // Reconciling badge
  if (node.lifecycle === 'RECONCILING') {
    CTX.beginPath();
    CTX.arc(x + R - 5, y - R + 5, 5, 0, 2 * Math.PI);
    CTX.fillStyle = '#f59e0b';
    CTX.fill();
  }

  CTX.restore();
}

function hitTestNode(mx, my) {
  for (const [id, pos] of Object.entries(nodePositions)) {
    if (Math.hypot(mx - pos.x, my - pos.y) <= 30) return id;
  }
  return null;
}

CANVAS.addEventListener('mousemove', e => {
  const r  = CANVAS.getBoundingClientRect();
  const mx = e.clientX - r.left;
  const my = e.clientY - r.top;
  const hit = hitTestNode(mx, my);
  hoveredNode = hit;
  CANVAS.style.cursor = hit ? 'pointer' : 'default';

  if (hit && graphData) {
    const node = graphData.nodes.find(n => n.node_id === hit);
    if (node) {
      const { trusted, blocked } = trustSummary(node);
      TOOLTIP.innerHTML =
        `<strong>${node.node_id}</strong>\n` +
        `${node.connectivity} · ${node.lifecycle}\n` +
        (trusted.length ? `Trusted: ${trusted.join(', ')}\n` : '') +
        (blocked.length ? `Blocked: ${blocked.join(', ')}\n` : '') +
        `Epoch: ${node.last_reconciled_epoch}`;
      TOOLTIP.style.left = (mx + 14) + 'px';
      TOOLTIP.style.top  = (my - 10) + 'px';
      TOOLTIP.classList.remove('hidden');
    }
  } else {
    TOOLTIP.classList.add('hidden');
  }
  renderGraph();
});

CANVAS.addEventListener('mouseleave', () => {
  hoveredNode = null;
  TOOLTIP.classList.add('hidden');
  renderGraph();
});

CANVAS.addEventListener('click', e => {
  const r  = CANVAS.getBoundingClientRect();
  const hit = hitTestNode(e.clientX - r.left, e.clientY - r.top);
  if (hit && graphData) openDrawer(hit);
});

// ─── Node Cards ──────────────────────────────────────────────────────────────

function renderNodeCards() {
  if (!graphData) return;
  const grid = document.getElementById('node-grid');
  grid.innerHTML = '';
  graphData.nodes.forEach(node => {
    const { trusted, blocked } = trustSummary(node);
    const avatarClass = node.connectivity === 'OFFLINE' ? 'offline' :
                        node.lifecycle   === 'RECONCILING' ? 'reconciling' : 'online';
    const connPill = `<span class="pill pill-${node.connectivity.toLowerCase()}">${node.connectivity}</span>`;
    const lcPill   = `<span class="pill pill-${node.lifecycle.toLowerCase()}">${node.lifecycle}</span>`;
    const trustPills = [
      ...trusted.map(r => `<span class="pill pill-trusted">${r}</span>`),
      ...blocked.map(r => `<span class="pill pill-blocked">${r} blocked</span>`),
    ].join('');

    const card = document.createElement('div');
    card.className = 'node-card';
    card.innerHTML = `
      <div class="node-card-header">
        <div class="node-avatar ${avatarClass}">${node.node_id}</div>
        <div class="node-name">Node ${node.node_id}</div>
      </div>
      <div class="node-meta">
        <div class="node-pill-row">${connPill} ${lcPill}</div>
        <div class="node-pill-row">${trustPills || '<span class="node-epoch" style="font-style:italic">no trust cached</span>'}</div>
        <div class="node-epoch">epoch ${node.last_reconciled_epoch}</div>
      </div>`;
    card.addEventListener('click', () => openDrawer(node.node_id));
    grid.appendChild(card);
  });
}

// ─── Node Drawer ──────────────────────────────────────────────────────────────

function openDrawer(nodeId) {
  selectedNode = nodeId;
  if (!graphData) return;
  const node = graphData.nodes.find(n => n.node_id === nodeId);
  if (!node) return;

  document.getElementById('drawer-node-id').textContent = `Node ${nodeId}`;
  document.getElementById('drawer-overlay').classList.remove('hidden');
  document.getElementById('node-drawer').classList.remove('hidden');

  document.getElementById('drawer-body').innerHTML = `
    <div class="drawer-block">
      <div class="drawer-block-title">State</div>
      <div class="drawer-stat-grid">
        <div class="drawer-stat">
          <div class="drawer-stat-label">Connectivity</div>
          <div class="drawer-stat-value" style="color:${node.connectivity === 'OFFLINE' ? '#b91c1c' : '#0a0a0a'}">${node.connectivity}</div>
        </div>
        <div class="drawer-stat">
          <div class="drawer-stat-label">Lifecycle</div>
          <div class="drawer-stat-value" style="color:${node.lifecycle === 'RECONCILING' ? '#92400e' : '#0a0a0a'}">${node.lifecycle}</div>
        </div>
        <div class="drawer-stat">
          <div class="drawer-stat-label">Last Epoch</div>
          <div class="drawer-stat-value">${node.last_reconciled_epoch}</div>
        </div>
        <div class="drawer-stat">
          <div class="drawer-stat-label">Trust Entries</div>
          <div class="drawer-stat-value">${node.trust.length}</div>
        </div>
      </div>
    </div>

    <div class="drawer-block">
      <div class="drawer-block-title">Cached Trust</div>
      ${node.trust.length ? `
      <div class="drawer-trust-list">
        ${node.trust.map(tr => `
          <div class="trust-row">
            <div>
              <div class="trust-row-rel">${tr.relationship_id}</div>
              <div class="trust-row-meta">via ${tr.source_node === 'AUTHORITY' ? 'Authority' : `node ${tr.source_node}`} · epoch ${tr.epoch}</div>
            </div>
            <span class="pill ${tr.status === 'TRUSTED' ? 'pill-trusted' : 'pill-blocked'}">${tr.status}</span>
          </div>`).join('')}
      </div>` : `<div class="empty-state" style="padding:12px 0 0">No trust cached</div>`}
    </div>

    <div class="drawer-block">
      <div class="drawer-block-title">Quick Actions</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn-action btn-warn" onclick="quickDisconnect('${nodeId}')">Disconnect</button>
        <button class="btn-action btn-ok"   onclick="quickReconnect('${nodeId}')">Reconnect</button>
      </div>
    </div>`;
}

function closeDrawer() {
  selectedNode = null;
  document.getElementById('drawer-overlay').classList.add('hidden');
  document.getElementById('node-drawer').classList.add('hidden');
}
document.getElementById('drawer-close').addEventListener('click', closeDrawer);
document.getElementById('drawer-overlay').addEventListener('click', closeDrawer);

async function quickDisconnect(id) {
  try { await apiFetch(`/nodes/${id}/disconnect`, { method: 'POST' }); toast(`Node ${id} disconnected`, 'info'); closeDrawer(); await refresh(); }
  catch (e) { toast(e.message, 'error'); }
}
async function quickReconnect(id) {
  try { await apiFetch(`/nodes/${id}/reconnect`, { method: 'POST' }); toast(`Node ${id} reconnected`, 'success'); closeDrawer(); await refresh(); }
  catch (e) { toast(e.message, 'error'); }
}

// ─── Populate Selects ────────────────────────────────────────────────────────

function populateSelects() {
  if (!graphData) return;
  const nodeIds = graphData.nodes.map(n => n.node_id);
  const relSet  = new Set();
  graphData.nodes.forEach(n => n.trust.forEach(t => relSet.add(t.relationship_id)));
  relationships = [...relSet].sort();

  const fillNodes = (id, placeholder = 'Select node…') => {
    const sel = document.getElementById(id);
    const cur = sel.value;
    sel.innerHTML = `<option value="">${placeholder}</option>` +
      nodeIds.map(n => `<option value="${n}"${n === cur ? ' selected' : ''}>${n}</option>`).join('');
  };
  const fillRels = (id, rels = relationships, placeholder = 'Select relationship…') => {
    const sel = document.getElementById(id);
    const cur = sel.value;
    sel.innerHTML = `<option value="">${placeholder}</option>` +
      rels.map(r => `<option value="${r}"${r === cur ? ' selected' : ''}>${r}</option>`).join('');
  };

  fillNodes('disconnect-node');
  fillNodes('reconnect-node');
  fillNodes('auth-node');
  fillNodes('prop-from', 'From node…');
  fillNodes('prop-to',   'To node…');
  fillRels('revoke-rel');
  fillRels('auth-rel');

  // prop-rel: show only what the selected source node actually holds
  const fromNode = document.getElementById('prop-from').value;
  const srcNode  = fromNode ? graphData.nodes.find(n => n.node_id === fromNode) : null;
  const srcRels  = srcNode ? srcNode.trust.map(t => t.relationship_id) : relationships;
  fillRels('prop-rel', srcRels, 'Relationship…');

  const conv = document.getElementById('convergence-rel');
  const convCur = conv.value;
  conv.innerHTML = `<option value="">Latest revoked</option>` +
    relationships.map(r => `<option value="${r}"${r === convCur ? ' selected' : ''}>${r}</option>`).join('');
}

// ─── Events ──────────────────────────────────────────────────────────────────

function fmtTs(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  return isNaN(d) ? ts.slice(11, 19) : d.toLocaleTimeString('en-US', { hour12: false });
}

function renderEvents() {
  const log   = document.getElementById('event-log');
  const count = document.getElementById('event-count');
  count.textContent = `${eventsCache.length} events`;

  if (!eventsCache.length) {
    log.innerHTML = '<div class="empty-state">Waiting for events…</div>';
    return;
  }
  const KW = /(alice|bob|A|B|C|D|REVOKED|BLOCKED|TRUSTED|READY|OFFLINE|ONLINE|RECONCILING)/g;
  log.innerHTML = [...eventsCache].reverse().slice(0, 80).map(ev =>
    `<div class="event-entry">
      <span class="event-ts">${fmtTs(ev.ts)}</span>
      <span class="event-msg">${ev.message.replace(KW, '<span class="hl">$1</span>')}</span>
    </div>`
  ).join('');
}

// ─── Convergence ─────────────────────────────────────────────────────────────

async function renderConvergence() {
  const relId = document.getElementById('convergence-rel').value || undefined;
  const body  = document.getElementById('convergence-body');
  try {
    const url  = relId ? `/convergence?relationship_id=${encodeURIComponent(relId)}` : '/convergence';
    const data = await apiFetch(url);
    const allOk = data.all_converged;
    body.innerHTML = `
      <div class="convergence-meta">
        <span class="convergence-rel-info">${data.relationship_id} · auth epoch ${data.authority_epoch} · ${data.authority_status}</span>
        <span class="convergence-status ${allOk ? 'all-converged' : 'not-converged'}">${allOk ? '✓ Converged' : '⚠ Diverged'}</span>
      </div>
      <table class="convergence-table">
        <thead>
          <tr><th>Node</th><th>Status</th><th>Epoch</th><th></th></tr>
        </thead>
        <tbody>
          ${data.nodes.map(n => `
            <tr>
              <td class="conv-node">${n.node_id}</td>
              <td class="${n.converged ? 'conv-ok' : 'conv-fail'}">${n.local_status ?? '—'}</td>
              <td class="${n.converged ? 'conv-ok' : 'conv-fail'}">${n.local_epoch ?? '—'}</td>
              <td class="conv-icon ${n.converged ? 'conv-ok' : 'conv-fail'}">${n.converged ? '✓' : '✗'}</td>
            </tr>`).join('')}
        </tbody>
      </table>`;
  } catch {
    body.innerHTML = '<div class="empty-state">No data yet</div>';
  }
}
document.getElementById('convergence-rel').addEventListener('change', renderConvergence);

// ─── Operations ──────────────────────────────────────────────────────────────

document.getElementById('btn-reset').addEventListener('click', async () => {
  if (!confirm('Reset demo to seed state?')) return;
  try {
    await apiFetch('/demo/reset', { method: 'POST' });
    toast('Reset to seed graph', 'success');
    hideResult('prop-result');
    hideResult('auth-result');
    await refresh();
  }
  catch (e) { toast(e.message, 'error'); }
});

document.getElementById('btn-revoke').addEventListener('click', async () => {
  const rel = document.getElementById('revoke-rel').value;
  if (!rel) { toast('Select a relationship', 'info'); return; }
  try {
    const r = await apiFetch(`/trust/${rel}/revoke`, { method: 'POST' });
    toast(`Revoked "${rel}" · epoch ${r.epoch} · blocked: ${r.blocked_nodes.join(', ') || 'none'}`, 'success', 5000);
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
});

document.getElementById('btn-disconnect').addEventListener('click', async () => {
  const node = document.getElementById('disconnect-node').value;
  if (!node) { toast('Select a node', 'info'); return; }
  try { await apiFetch(`/nodes/${node}/disconnect`, { method: 'POST' }); toast(`Node ${node} offline`, 'info'); await refresh(); }
  catch (e) { toast(e.message, 'error'); }
});

document.getElementById('btn-reconnect').addEventListener('click', async () => {
  const node = document.getElementById('reconnect-node').value;
  if (!node) { toast('Select a node', 'info'); return; }
  try {
    const r = await apiFetch(`/nodes/${node}/reconnect`, { method: 'POST' });
    toast(`Node ${node} reconciled · ${r.reconciled_relationships ?? ''} relationships updated`, 'success');
    await refresh();
  } catch (e) { toast(e.message, 'error'); }
});

document.getElementById('btn-authorize').addEventListener('click', async () => {
  const node   = document.getElementById('auth-node').value;
  const rel    = document.getElementById('auth-rel').value;
  const result = document.getElementById('auth-result');
  if (!node || !rel) { toast('Select node and relationship', 'info'); return; }
  try {
    const r = await apiFetch(`/nodes/${node}/authorize`, { method: 'POST', body: JSON.stringify({ relationship_id: rel }) });
    result.className = `auth-result ${r.decision === 'ALLOW' ? 'allow' : 'deny'}`;
    result.textContent = `${r.decision} — ${r.reason}`;
    result.classList.remove('hidden');
  } catch (e) { toast(e.message, 'error'); }
});

document.getElementById('btn-propagate').addEventListener('click', async () => {
  const from   = document.getElementById('prop-from').value;
  const rel    = document.getElementById('prop-rel').value;
  const to     = document.getElementById('prop-to').value;
  const result = document.getElementById('prop-result');

  if (!from || !rel || !to) {
    showResult(result, 'deny', 'Select source node, relationship, and destination node');
    return;
  }
  if (from === to) {
    showResult(result, 'deny', 'Source and destination must be different nodes');
    return;
  }
  // Check source node holds this relationship
  const srcNode = graphData?.nodes?.find(n => n.node_id === from);
  const hasTrust = srcNode?.trust?.some(t => t.relationship_id === rel);
  if (!hasTrust) {
    showResult(result, 'deny', `Node ${from} does not hold trust for "${rel}" — cannot propagate`);
    return;
  }
  try {
    await apiFetch(`/nodes/${from}/propagate`, {
      method: 'POST',
      body: JSON.stringify({ relationship_id: rel, to_node: to }),
    });
    showResult(result, 'allow', `"${rel}" propagated from node ${from} → node ${to}`);
    toast(`Propagated "${rel}" from ${from} → ${to}`, 'success');
    await refresh();
  } catch (e) {
    showResult(result, 'deny', e.message);
    toast(e.message, 'error');
  }
});

function showResult(el, type, msg) {
  el.className = `auth-result ${type}`;
  el.textContent = msg;
  el.classList.remove('hidden');
}

function hideResult(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}

// ─── Epoch badge ─────────────────────────────────────────────────────────────

function updateEpochBadge() {
  const max = graphData?.nodes?.reduce((m, n) =>
    Math.max(m, n.last_reconciled_epoch, ...n.trust.map(t => t.epoch)), 0) ?? 0;
  document.getElementById('epoch-badge').textContent = `epoch ${max}`;
}

// ─── Refresh ─────────────────────────────────────────────────────────────────

async function refresh() {
  try {
    const [graph, events] = await Promise.all([apiFetch('/graph'), apiFetch('/events')]);
    graphData   = graph;
    eventsCache = events.events;

    renderGraph();
    renderNodeCards();
    renderEvents();
    populateSelects();
    updateEpochBadge();

    const activeTab = document.querySelector('.tab-btn.active')?.dataset?.tab;
    if (activeTab === 'pane-convergence') await renderConvergence();

    if (selectedNode) openDrawer(selectedNode);
  } catch (e) {
    console.error('refresh failed', e);
  }
}

window.addEventListener('resize', renderGraph);

async function init() {
  await pollHealth();
  await refresh();
  await renderConvergence();
  setInterval(refresh, POLL_MS);
  setInterval(pollHealth, 5000);
}

init();
