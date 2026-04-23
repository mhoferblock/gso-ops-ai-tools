/* ── GSO Ops AI Tools — App Logic ─────────────────────────────────────────── */

// ── Config & State ────────────────────────────────────────────────────────────
const BASE = '';

const state = {
  user: null,
  token: null,
  ssoMode: false,
  serverConfig: { sso_mode: false, ai_enabled: false },
  currentSection: 'home',
  tools: [],
  chatMessages: [],
  lastChatId: 0,
  chatPollingTimer: null,
  spotlightIndex: 0,
  spotlightTools: [],
  spotlightTimer: null,
  chatHistory: [],  // for AOL AI conversation context
};

// Tile accent colors — cycles through by index
const TILE_COLORS = [
  '#6366F1','#8B5CF6','#06B6D4','#10B981',
  '#F59E0B','#EF4444','#3B82F6','#EC4899',
  '#0EA5E9','#84CC16','#F97316','#A78BFA',
];

// ── Utilities ─────────────────────────────────────────────────────────────────
function initials(name = '') {
  return name.split(' ').map(w => w[0] || '').join('').slice(0, 2).toUpperCase() || '??';
}

function avatarColor(name = '') {
  const i = [...name].reduce((a, c) => a + c.charCodeAt(0), 0) % TILE_COLORS.length;
  return TILE_COLORS[i];
}

function tileColor(index) {
  return TILE_COLORS[index % TILE_COLORS.length];
}

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr.replace(' ', 'T') + (dateStr.includes('T') ? '' : 'Z'));
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60)    return 'just now';
  if (secs < 3600)  return `${Math.floor(secs/60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs/3600)}h ago`;
  return `${Math.floor(secs/86400)}d ago`;
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr.replace(' ', 'T') + 'Z');
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch { return dateStr; }
}

function esc(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toast(msg, duration = 3000) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), duration);
}

function $(id) { return document.getElementById(id); }

// ── API Client ────────────────────────────────────────────────────────────────
const api = {
  async req(endpoint, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...opts.headers };
    // Use X-App-Token instead of Authorization — Databricks Apps proxy strips
    // the standard Authorization header before forwarding requests to the app.
    if (state.token) headers['X-App-Token'] = state.token;
    const res = await fetch(BASE + endpoint, { ...opts, headers });
    if (res.status === 401 && state.serverConfig.sso_mode && !opts._retried) {
      // Token expired — refresh via SSO and retry once
      await attemptSSOLogin();
      return api.req(endpoint, { ...opts, _retried: true });
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  },
  get: (url) => api.req(url),
  post: (url, body) => api.req(url, { method: 'POST', body: JSON.stringify(body) }),
  put:  (url, body) => api.req(url, { method: 'PUT',  body: JSON.stringify(body) }),
  del:  (url)        => api.req(url, { method: 'DELETE' }),
};

// ── Auth ──────────────────────────────────────────────────────────────────────
function loadAuth() {
  const token = localStorage.getItem('gso_token');
  const user  = localStorage.getItem('gso_user');
  if (token && user) {
    state.token = token;
    state.user  = JSON.parse(user);
    renderNav();
  }
}

/**
 * Try Databricks SSO auto-login.
 * On Databricks Apps, X-Forwarded-User is injected server-side — the backend
 * reads it and returns a token transparently.  Returns true if SSO succeeded.
 * Returns false silently on local dev (403 from backend).
 */
async function attemptSSOLogin() {
  try {
    const data = await api.post('/api/auth/sso', {});
    if (data.token && data.user) {
      state.token   = data.token;
      state.user    = data.user;
      state.ssoMode = true;
      localStorage.setItem('gso_token', data.token);
      localStorage.setItem('gso_user',  JSON.stringify(data.user));
      renderNav();
      return true;
    }
  } catch (_) {
    // Not on Databricks — fall through to manual login
  }
  return false;
}

async function login(username, displayName, email) {
  const data = await api.post('/api/auth/login', { username, display_name: displayName, email });
  state.token = data.token;
  state.user  = data.user;
  localStorage.setItem('gso_token', data.token);
  localStorage.setItem('gso_user', JSON.stringify(data.user));
  renderNav();
  closeModal('login-modal');
  toast(`Welcome, ${data.user.display_name}! 👋`);
  checkWinner();
}

function logout() {
  state.token   = null;
  state.user    = null;
  state.ssoMode = false;
  localStorage.removeItem('gso_token');
  localStorage.removeItem('gso_user');
  renderNav();
  navigate('home');
  // On Databricks, SSO will silently re-authenticate on next page load
  if (state.ssoMode) {
    toast('Signed out. Refresh to sign back in via Databricks SSO.');
  } else {
    toast('Signed out. See you soon!');
  }
}

// ── Nav ───────────────────────────────────────────────────────────────────────
function renderNav() {
  const navRight = $('nav-right');
  if (!navRight) return;
  if (state.user) {
    const ssoChip = state.ssoMode
      ? `<span style="font-size:11px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
                      color:rgba(255,255,255,.75);padding:2px 8px;border-radius:10px;letter-spacing:.3px">
           🔐 SSO
         </span>`
      : '';
    navRight.innerHTML = `
      ${ssoChip}
      <div class="avatar" style="background:${avatarColor(state.user.display_name)}"
           onclick="navigate('profile-me')" title="${esc(state.user.display_name)}">
        ${esc(initials(state.user.display_name))}
      </div>
      <span style="color:rgba(255,255,255,.8);font-size:13px;cursor:pointer"
            onclick="navigate('profile-me')">${esc(state.user.display_name.split(' ')[0])}</span>
      <button class="btn-nav-login" style="background:transparent;border:1px solid rgba(255,255,255,.2)"
              onclick="logout()">Sign out</button>
    `;
  } else {
    navRight.innerHTML = `
      <button class="btn-nav-login" onclick="openModal('login-modal')">Sign in</button>
    `;
  }
}

// ── Router ────────────────────────────────────────────────────────────────────
function navigate(section, param) {
  // Stop spotlight timer when leaving home
  if (section !== 'home' && state.spotlightTimer) {
    clearInterval(state.spotlightTimer);
    state.spotlightTimer = null;
  }
  // Stop chat polling when leaving chat
  if (section !== 'chat' && state.chatPollingTimer) {
    clearInterval(state.chatPollingTimer);
    state.chatPollingTimer = null;
  }

  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));

  state.currentSection = section;

  const el = $(section) || $('home');
  if (el) el.classList.add('active');

  const navLink = document.querySelector(`.nav-link[data-section="${section}"]`);
  if (navLink) navLink.classList.add('active');

  // Section loaders
  if (section === 'home')          loadHome();
  if (section === 'discover')      loadDiscover();
  if (section === 'my-tools')      loadMyTools();
  if (section === 'leaderboard')   loadLeaderboard();
  if (section === 'chat')          loadChat();
  if (section === 'best-practices') loadBestPractices();
  if (section === 'board')         loadBoard();
  if (section === 'profile-me')    loadProfileMe();
  if (section === 'profile-view')  loadProfileView(param);
}

// ── Tile renderer ─────────────────────────────────────────────────────────────
function renderToolTile(tool, colorIndex) {
  const color = tileColor(colorIndex ?? 0);
  const voted = tool.user_voted;
  const tags  = (tool.tags || []).slice(0, 3);
  return `
    <div class="tool-tile" data-id="${tool.id}">
      <div class="tile-stripe" style="background:${color}"></div>
      <div class="tile-body">
        <div class="tile-owner">
          <div class="avatar avatar-sm" style="background:${avatarColor(tool.owner_name || '')}">
            ${esc(initials(tool.owner_name || ''))}
          </div>
          <span class="tile-owner-name"
                onclick="viewUserProfile(event,'${esc(tool.username)}')"
                style="cursor:pointer;color:${color}">
            ${esc(tool.owner_name || tool.username || '')}
          </span>
        </div>
        <div class="tile-name">${esc(tool.name)}</div>
        <div class="tile-desc">${esc(tool.description || tool.summary || '')}</div>
        ${tags.length ? `<div class="tile-tags">${tags.map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div>` : ''}
        <div class="tile-actions">
          <div class="tile-meta">
            <div class="tile-meta-item">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
              ${tool.click_count || 0}
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="btn-vote ${voted ? 'voted' : ''}"
                    onclick="voteTool(event,${tool.id},this)">
              ♥ <span class="vote-count">${tool.vote_count || 0}</span>
            </button>
            <button class="btn-open" onclick="openTool(event,${tool.id},'${esc(tool.url)}')">
              Open →
            </button>
          </div>
        </div>
      </div>
    </div>`;
}

// ── Home ──────────────────────────────────────────────────────────────────────
async function loadHome() {
  // Stats
  try {
    const stats = await api.get('/api/stats');
    $('stat-tools').textContent  = stats.total_tools;
    $('stat-users').textContent  = stats.total_users;
    $('stat-votes').textContent  = stats.total_votes;
    $('stat-clicks').textContent = stats.total_clicks;
  } catch {}

  // Spotlight
  try {
    state.spotlightTools = await api.get('/api/tools/featured');
    state.spotlightIndex = 0;
    renderSpotlight();
    if (state.spotlightTools.length > 1) {
      state.spotlightTimer = setInterval(() => {
        state.spotlightIndex = (state.spotlightIndex + 1) % state.spotlightTools.length;
        renderSpotlight();
      }, 5000);
    }
  } catch {}

  // Recent tools
  try {
    const tools = await api.get('/api/tools?sort=newest');
    const el = $('home-tools-grid');
    if (tools.length === 0) {
      el.innerHTML = `<div class="empty"><h3>No tools yet</h3><p>Be the first to submit one!</p></div>`;
    } else {
      el.innerHTML = tools.slice(0, 6).map((t, i) => renderToolTile(t, i)).join('');
    }
  } catch {}

  // Feed
  loadFeed();
}

function renderSpotlight() {
  const tools = state.spotlightTools;
  if (!tools.length) return;
  const t = tools[state.spotlightIndex];
  const color = tileColor(state.spotlightIndex);

  $('spotlight-name').textContent  = t.name;
  $('spotlight-owner').textContent = `by ${t.owner_name || t.username}`;
  $('spotlight-desc').textContent  = t.summary || t.description || '';
  $('spotlight-open').onclick = () => openTool(null, t.id, t.url);
  $('spotlight-open').style.background = color;
  $('spotlight-stripe').style.background = color;

  // Dots
  const dots = $('spotlight-dots');
  dots.innerHTML = tools.map((_, i) =>
    `<div class="dot ${i === state.spotlightIndex ? 'active' : ''}"
          onclick="goSpotlight(${i})"></div>`).join('');
}

function goSpotlight(i) {
  state.spotlightIndex = i;
  renderSpotlight();
  clearInterval(state.spotlightTimer);
  state.spotlightTimer = setInterval(() => {
    state.spotlightIndex = (state.spotlightIndex + 1) % state.spotlightTools.length;
    renderSpotlight();
  }, 5000);
}

async function loadFeed() {
  try {
    const items = await api.get('/api/feed?limit=15');
    const el = $('activity-feed');
    if (!items.length) {
      el.innerHTML = `<div class="feed-item"><div class="feed-text" style="color:var(--text-muted)">Activity will appear here as tools are added and voted on.</div></div>`;
      return;
    }
    el.innerHTML = items.map(item => `
      <div class="feed-item">
        <div class="feed-icon ${item.event_type}">
          ${item.event_type === 'tool_added' ? '🔧' : '♥'}
        </div>
        <div class="feed-text">${esc(item.message)}</div>
        <div class="feed-time">${timeAgo(item.created_at)}</div>
      </div>`).join('');
  } catch {}
}

// ── Discover ──────────────────────────────────────────────────────────────────
async function loadDiscover(sort, search) {
  const sortVal   = sort   || $('discover-sort')?.value   || 'newest';
  const searchVal = search || $('discover-search')?.value || '';
  let url = `/api/tools?sort=${sortVal}`;
  if (searchVal) url += `&search=${encodeURIComponent(searchVal)}`;
  try {
    const tools = await api.get(url);
    const el = $('discover-grid');
    if (!tools.length) {
      el.innerHTML = `<div class="empty"><h3>No tools found</h3><p>Try a different search term.</p></div>`;
      return;
    }
    el.innerHTML = tools.map((t, i) => renderToolTile(t, i)).join('');
  } catch (e) {
    toast('Failed to load tools: ' + e.message);
  }
}

// ── My Tools ──────────────────────────────────────────────────────────────────
async function loadMyTools() {
  if (!state.user) {
    $('my-tools-grid').innerHTML = `
      <div class="empty">
        <h3>Sign in to manage your tools</h3>
        <p>Login to upload and manage your AI tools.</p>
        <button class="btn-primary" style="margin-top:16px;width:auto;padding:10px 24px"
                onclick="openModal('login-modal')">Sign in</button>
      </div>`;
    return;
  }
  try {
    const tools = await api.get(`/api/tools?owner=${state.user.username}`);
    const grid = $('my-tools-grid');
    const uploadTile = `
      <div class="upload-tile" onclick="openModal('submit-modal')">
        <div class="icon">+</div>
        <h3>Submit Your AI Tool</h3>
        <p>Drop a link and let Claude write the description for you</p>
      </div>`;
    if (!tools.length) {
      grid.innerHTML = uploadTile + `
        <div class="empty" style="grid-column:1/-1">
          <h3>No tools yet</h3>
          <p>Submit your first AI tool above.</p>
        </div>`;
    } else {
      grid.innerHTML = uploadTile + tools.map((t, i) => `
        <div style="position:relative">
          ${renderToolTile(t, i)}
          <button onclick="deleteTool(${t.id})"
                  style="position:absolute;top:12px;right:12px;background:rgba(0,0,0,.6);
                         color:#fff;border:none;border-radius:50%;width:26px;height:26px;
                         cursor:pointer;font-size:14px;display:flex;align-items:center;justify-content:center">
            ×
          </button>
        </div>`).join('');
    }
  } catch (e) {
    toast('Error loading tools: ' + e.message);
  }
}

async function deleteTool(id) {
  if (!confirm('Delete this tool?')) return;
  try {
    await api.del(`/api/tools/${id}`);
    toast('Tool deleted.');
    loadMyTools();
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

// ── Submit Tool ───────────────────────────────────────────────────────────────
async function autoSummarize() {
  const url = $('submit-url').value.trim();
  if (!url) return toast('Enter a URL first');
  const btn = $('summarize-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Analyzing...';
  try {
    const data = await api.post('/api/summarize', { url });
    $('submit-name').value        = data.name || '';
    $('submit-desc').value        = data.description || data.summary || '';
    $('submit-summary').value     = data.summary || '';
    $('submit-tags').value        = (data.tags || []).join(', ');
    toast('Description generated!');
  } catch (e) {
    toast('Summarize failed: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Auto-Summarize with AI ✨';
  }
}

async function submitTool(e) {
  e.preventDefault();
  if (!state.user) return openModal('login-modal');
  const name    = $('submit-name').value.trim();
  const url     = $('submit-url').value.trim();
  const desc    = $('submit-desc').value.trim();
  const summary = $('submit-summary').value.trim();
  const tags    = $('submit-tags').value.split(',').map(t => t.trim()).filter(Boolean);
  if (!name || !url) return toast('Name and URL are required');
  const btn = $('submit-btn');
  btn.disabled = true;
  btn.textContent = 'Submitting...';
  try {
    await api.post('/api/tools', { name, url, description: desc, summary, tags });
    toast('Tool submitted! 🎉');
    closeModal('submit-modal');
    e.target.reset();
    loadMyTools();
    loadFeed();
  } catch (err) {
    toast('Error: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Submit Tool';
  }
}

// ── Vote & Click ──────────────────────────────────────────────────────────────
async function voteTool(e, toolId, btn) {
  e.stopPropagation();
  if (!state.user) { openModal('login-modal'); return; }
  try {
    const data = await api.post(`/api/tools/${toolId}/vote`, {});
    const countEl = btn.querySelector('.vote-count');
    if (countEl) countEl.textContent = data.vote_count;
    btn.classList.toggle('voted', data.voted);
    toast(data.voted ? 'Voted! ♥' : 'Vote removed');
  } catch (e) {
    toast(e.message);
  }
}

async function openTool(e, toolId, url) {
  if (e) e.stopPropagation();
  // Track click
  api.post(`/api/tools/${toolId}/click`, {}).catch(() => {});
  // Open tool in new tab
  window.open(url, '_blank', 'noopener');
  // Refresh click count in all visible tiles
  setTimeout(() => {
    if (state.currentSection === 'home')        loadHome();
    if (state.currentSection === 'discover')    loadDiscover();
    if (state.currentSection === 'leaderboard') loadLeaderboard();
  }, 300);
}

// ── Leaderboard ───────────────────────────────────────────────────────────────
async function loadLeaderboard() {
  try {
    const data = await api.get('/api/leaderboard');
    renderLBTab('most-used',   data.most_used,  'click_count', 'clicks');
    renderLBTab('most-voted',  data.most_voted, 'vote_count',  'votes');
    renderLBWeekly(data.weekly_history);
  } catch (e) {
    toast('Failed to load leaderboard: ' + e.message);
  }
}

function renderLBTab(elId, items, countKey, label) {
  const el = $(elId);
  if (!items.length) { el.innerHTML = `<div class="empty"><p>No data yet.</p></div>`; return; }
  el.innerHTML = items.map((item, i) => {
    const rankClass = i === 0 ? 'gold' : i === 1 ? 'silver' : i === 2 ? 'bronze' : '';
    const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : (i + 1);
    return `
      <div class="lb-row" onclick="openTool(null,${item.id},'${esc(item.url)}')">
        <div class="lb-rank ${rankClass}">${medal}</div>
        <div>
          <div class="lb-name">${esc(item.name)}</div>
          <div class="lb-owner">by ${esc(item.owner_name)}</div>
        </div>
        <div>
          <div class="lb-count">${item[countKey] || 0}</div>
          <div class="lb-label">${label}</div>
        </div>
      </div>`;
  }).join('');
}

function renderLBWeekly(history) {
  const el = $('weekly-history');
  if (!history.length) { el.innerHTML = `<div class="empty"><p>No weekly winners yet.</p></div>`; return; }
  el.innerHTML = history.map((w, i) => `
    <div class="lb-row">
      <div class="lb-rank ${i === 0 ? 'gold' : ''}">${i === 0 ? '🏆' : i + 1}</div>
      <div>
        <div class="lb-name">${esc(w.tool_name)}</div>
        <div class="lb-owner">by ${esc(w.owner_name)} · week of ${formatDate(w.week_start)}</div>
      </div>
      <div>
        <div class="lb-count">${w.votes_at_time}</div>
        <div class="lb-label">votes</div>
      </div>
    </div>`).join('');
}

function switchLBTab(tab) {
  document.querySelectorAll('.lb-tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.lb-tab-content').forEach(c => c.style.display = 'none');
  document.querySelector(`.lb-tab-btn[data-tab="${tab}"]`).classList.add('active');
  const el = $(tab);
  if (el) el.style.display = 'block';
}

// ── AOL Chat ──────────────────────────────────────────────────────────────────
async function loadChat() {
  state.chatMessages = [];
  state.lastChatId   = 0;
  try {
    const msgs = await api.get('/api/chat');
    state.chatMessages = msgs;
    state.lastChatId   = msgs.length ? msgs[msgs.length - 1].id : 0;
    renderChatMessages();
    // Render buddy list
    const users = await api.get('/api/users');
    renderBuddyList(users);
  } catch (e) {
    console.error('Chat load error:', e);
  }
  // Poll every 4 seconds
  state.chatPollingTimer = setInterval(pollChat, 4000);
}

async function pollChat() {
  if (state.currentSection !== 'chat') {
    clearInterval(state.chatPollingTimer);
    return;
  }
  try {
    const msgs = await api.get(`/api/chat?since=${state.lastChatId}`);
    if (msgs.length) {
      state.chatMessages.push(...msgs);
      state.lastChatId = msgs[msgs.length - 1].id;
      renderChatMessages();
    }
  } catch {}
}

function renderChatMessages() {
  const el = $('aol-messages');
  if (!el) return;
  el.innerHTML = state.chatMessages.map(m => {
    const senderClass = m.is_bot ? 'bot' : m.user_id === state.user?.id ? 'user' : 'other';
    const displayName = m.display_name || m.username;
    return `
      <div class="aol-msg">
        <div class="aol-msg-sender ${senderClass}">
          ${esc(displayName)}
          <span class="aol-msg-time">${timeAgo(m.created_at)}</span>
        </div>
        <div class="aol-msg-text">${esc(m.message)}</div>
      </div>`;
  }).join('');
  el.scrollTop = el.scrollHeight;
}

function renderBuddyList(users) {
  const el = $('buddy-list');
  if (!el) return;
  el.innerHTML = `
    <div class="aol-buddy" style="background:#FFFF99">
      <div class="aol-buddy-dot" style="background:#8B0000"></div>
      <span style="font-weight:bold;color:#8B0000">AOL AI</span>
    </div>
    ${users.map(u => `
      <div class="aol-buddy" onclick="viewUserProfile(null,'${esc(u.username)}')">
        <div class="aol-buddy-dot"></div>
        <span>${esc(u.display_name)}</span>
      </div>`).join('')}
  `;
}

async function sendChat() {
  if (!state.user) return openModal('login-modal');
  const input = $('chat-input');
  const msg   = input.value.trim();
  if (!msg) return;
  input.value = '';
  const isAsk = msg.startsWith('@AOL_AI') || msg.startsWith('?') || msg.toLowerCase().includes('hey aol');
  try {
    await api.post('/api/chat', { message: msg });
    // Optimistically add user message
    state.chatMessages.push({
      id: Date.now(),
      user_id: state.user.id,
      username: state.user.username,
      display_name: state.user.display_name,
      message: msg,
      is_bot: 0,
      created_at: new Date().toISOString(),
    });
    renderChatMessages();
    state.lastChatId = 0; // force full reload on next poll to sync IDs

    if (isAsk) {
      // Ask Claude
      state.chatHistory.push({ role: 'user', content: msg });
      const clean = msg.replace(/^@AOL_AI\s*/, '').replace(/^\?\s*/, '');
      const data = await api.post('/api/ask', {
        message: clean,
        history: state.chatHistory.slice(-8),
      });
      state.chatHistory.push({ role: 'assistant', content: data.response });
      // Force refresh to show bot reply from DB
      const fresh = await api.get('/api/chat');
      state.chatMessages = fresh;
      state.lastChatId = fresh.length ? fresh[fresh.length - 1].id : 0;
      renderChatMessages();
    }
  } catch (e) {
    toast('Send failed: ' + e.message);
  }
}

function chatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
}

// ── Best Practices ────────────────────────────────────────────────────────────
async function loadBestPractices() {
  try {
    const practices = await api.get('/api/best-practices');
    const el = $('practices-list');
    if (!practices.length) {
      el.innerHTML = `<div class="empty"><h3>No posts yet</h3><p>Share your first best practice above.</p></div>`;
      return;
    }
    el.innerHTML = practices.map(p => `
      <div class="practice-card">
        <div class="practice-title">${esc(p.title)}</div>
        <div class="practice-author">by ${esc(p.author_name)} · ${formatDate(p.created_at)}</div>
        <div class="practice-content">${esc(p.content)}</div>
      </div>`).join('');
  } catch (e) {
    toast('Error loading practices: ' + e.message);
  }
}

async function submitPractice(e) {
  e.preventDefault();
  if (!state.user) return openModal('login-modal');
  const title   = $('practice-title').value.trim();
  const content = $('practice-content').value.trim();
  if (!title || !content) return toast('Title and content required');
  try {
    await api.post('/api/best-practices', { title, content });
    toast('Practice posted! 🚀');
    e.target.reset();
    loadBestPractices();
  } catch (err) {
    toast('Error: ' + err.message);
  }
}

// ── Profile — Me ──────────────────────────────────────────────────────────────
async function loadProfileMe() {
  if (!state.user) {
    $('profile-me-content').innerHTML = `
      <div class="empty">
        <h3>Sign in to view your profile</h3>
        <button class="btn-primary" style="margin-top:16px;width:auto;padding:10px 24px"
                onclick="openModal('login-modal')">Sign in</button>
      </div>`;
    return;
  }
  try {
    const u = await api.get(`/api/users/${state.user.username}`);
    renderProfile(u, true);
  } catch {}
}

function renderProfile(u, isMe = false) {
  const el = isMe ? $('profile-me-content') : $('profile-view-content');
  const color = avatarColor(u.display_name);
  el.innerHTML = `
    <div class="profile-header" id="profile-header-${isMe?'me':'view'}">
      <div class="avatar avatar-lg" style="background:${color};flex-shrink:0">
        ${esc(initials(u.display_name))}
      </div>
      <div class="profile-info" style="flex:1">
        <div class="view-mode-fields">
          <h2>${esc(u.display_name)}</h2>
          <div class="username">@${esc(u.username)}</div>
          ${u.bio ? `<div class="profile-bio">${esc(u.bio)}</div>` : ''}
          ${u.favorite_ai_project ? `<div class="profile-fav">⚡ Favorite project: <span>${esc(u.favorite_ai_project)}</span></div>` : ''}
          ${isMe ? `<button class="btn-secondary" style="margin-top:16px;width:auto" onclick="toggleEditProfile()">Edit Profile</button>` : ''}
        </div>
        ${isMe ? `
          <div class="edit-mode-fields">
            <div class="form-group">
              <label class="form-label">Display name</label>
              <input id="edit-display-name" class="form-input" value="${esc(u.display_name)}">
            </div>
            <div class="form-group">
              <label class="form-label">Bio</label>
              <textarea id="edit-bio" class="form-textarea" style="min-height:80px">${esc(u.bio||'')}</textarea>
            </div>
            <div class="form-group">
              <label class="form-label">Favorite AI project</label>
              <input id="edit-fav" class="form-input" value="${esc(u.favorite_ai_project||'')}">
            </div>
            <div class="btn-row">
              <button class="btn-primary" onclick="saveProfile()" style="width:auto">Save</button>
              <button class="btn-secondary" onclick="toggleEditProfile()" style="width:auto">Cancel</button>
            </div>
          </div>` : ''}
      </div>
    </div>
    <div class="section-header">
      <h2 class="section-title">Tools by ${esc(u.display_name.split(' ')[0])}</h2>
      <span class="pill pill-info">${u.tools?.length || 0} tools</span>
    </div>
    <div class="tools-grid" id="${isMe?'profile-me':'profile-view'}-tools">
      ${(u.tools||[]).length
        ? (u.tools||[]).map((t,i)=>renderToolTile(t,i)).join('')
        : `<div class="empty" style="grid-column:1/-1"><h3>No tools yet</h3></div>`}
    </div>`;
}

function toggleEditProfile() {
  const header = document.getElementById('profile-header-me');
  if (!header) return;
  header.classList.toggle('edit-mode');
}

async function saveProfile() {
  const displayName = $('edit-display-name').value.trim();
  const bio         = $('edit-bio').value.trim();
  const fav         = $('edit-fav').value.trim();
  try {
    const updated = await api.put('/api/auth/me', {
      display_name: displayName, bio, favorite_ai_project: fav
    });
    state.user = updated;
    localStorage.setItem('gso_user', JSON.stringify(updated));
    renderNav();
    toast('Profile updated!');
    loadProfileMe();
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

// ── Profile — View ────────────────────────────────────────────────────────────
async function loadProfileView(username) {
  if (!username) return;
  try {
    const u = await api.get(`/api/users/${username}`);
    renderProfile(u, false);
  } catch (e) {
    $('profile-view-content').innerHTML = `<div class="empty"><h3>User not found</h3></div>`;
  }
}

function viewUserProfile(e, username) {
  if (e) e.stopPropagation();
  if (state.user && username === state.user.username) return navigate('profile-me');
  navigate('profile-view', username);
}

// ── Weekly Winner Modal ───────────────────────────────────────────────────────
let winnerCountdownTimer = null;

async function checkWinner() {
  if (!state.user) return;
  // Only show on Mondays (0 = Sunday, 1 = Monday)
  const today = new Date();
  // For demo, always show if winner not seen. Remove the Monday check to always demo it.
  // In production, uncomment: if (today.getDay() !== 1) return;
  try {
    const winner = await api.get('/api/winner');
    if (!winner || winner.user_has_seen) return;
    showWinnerModal(winner);
  } catch {}
}

function showWinnerModal(winner) {
  $('winner-tool-name').textContent  = winner.tool_name   || 'Unknown Tool';
  $('winner-owner-name').textContent = `by ${winner.owner_name || 'Unknown'}`;
  $('winner-tool-desc').textContent  = winner.tool_summary || '';
  $('winner-votes').textContent      = winner.votes_at_time || 0;
  $('winner-week').textContent       = formatDate(winner.week_start);
  $('winner-modal-overlay').classList.remove('hidden');

  // Fire confetti!
  fireConfetti();

  // 10-second countdown
  let seconds = 10;
  const bar     = $('countdown-bar');
  const countEl = $('winner-countdown-num');
  if (bar) bar.style.width = '100%';
  if (countEl) countEl.textContent = seconds;

  clearInterval(winnerCountdownTimer);
  winnerCountdownTimer = setInterval(() => {
    seconds--;
    if (bar) bar.style.width = `${(seconds / 10) * 100}%`;
    if (countEl) countEl.textContent = seconds;
    if (seconds <= 0) {
      clearInterval(winnerCountdownTimer);
      dismissWinner(winner.id);
    }
  }, 1000);
}

async function dismissWinner(winnerId) {
  $('winner-modal-overlay').classList.add('hidden');
  clearInterval(winnerCountdownTimer);
  if (winnerId && state.user) {
    api.post(`/api/winner/${winnerId}/seen`, {}).catch(() => {});
  }
}

function fireConfetti() {
  if (typeof confetti === 'undefined') return;
  const end = Date.now() + 6000;
  const colors = ['#6366F1', '#8B5CF6', '#F59E0B', '#10B981', '#EF4444', '#fff'];
  (function frame() {
    confetti({ particleCount: 3, angle: 60,  spread: 55, origin: { x: 0 }, colors });
    confetti({ particleCount: 3, angle: 120, spread: 55, origin: { x: 1 }, colors });
    if (Date.now() < end) requestAnimationFrame(frame);
  })();
}

// ── Modals ────────────────────────────────────────────────────────────────────
function openModal(id) {
  $(id).classList.remove('hidden');
}
function closeModal(id) {
  $(id).classList.add('hidden');
}

// Close modals when clicking overlay
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay') &&
      !e.target.id.startsWith('winner')) {
    e.target.classList.add('hidden');
  }
});

// ── Login form ────────────────────────────────────────────────────────────────
async function handleLogin(e) {
  e.preventDefault();
  const username    = $('login-username').value.trim();
  const displayName = $('login-display').value.trim();
  const email       = $('login-email').value.trim();
  if (!username || !displayName) return toast('Username and display name required');
  try {
    await login(username, displayName, email);
  } catch (err) {
    toast('Login failed: ' + err.message);
  }
}

// ── Tab switcher ──────────────────────────────────────────────────────────────
function switchTab(groupId, tab) {
  const group = $(groupId);
  if (!group) return;
  group.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  group.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
  const activeTab = group.querySelector(`[data-tab="${tab}"]`);
  if (activeTab) activeTab.classList.add('active');
  const tabContent = $(tab);
  if (tabContent) tabContent.style.display = 'block';
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  loadAuth();

  // Fetch server config (SSO mode flag, AI enabled flag)
  try {
    const cfg = await api.get('/api/config');
    state.serverConfig = cfg;
  } catch (_) {
    state.serverConfig = { sso_mode: false, ai_enabled: false };
  }

  // On Databricks: always refresh token via SSO on every page load.
  // This ensures tokens are always valid even after app restarts/redeploys.
  if (state.serverConfig.sso_mode) {
    await attemptSSOLogin();
  }

  navigate('home');
  if (state.user) {
    setTimeout(checkWinner, 1500);
  }
  initWalkthrough();
}

document.addEventListener('DOMContentLoaded', init);

// ── Board ─────────────────────────────────────────────────────────────────────
const BOARD_EMOJIS = [
  '😀','😎','🤔','😍','🥳','🙌','👏','🤝',
  '🚀','💡','🔥','⭐','✅','❌','📌','📊',
  '💻','🤖','🧠','⚡','💪','🎯','📅','🗓️',
  '❤️','💯','👍','🎉','🏆','✨','💎','🌟',
];

const boardState = {
  selectedColor: '#FFEF5E',
  selectedEmoji: '',
  isDrawMode: false,
  isEraser: false,
  penColor: '#000000',
  isDrawing: false,
  lastX: 0, lastY: 0,
  dragNote: null,
  dragOffsetX: 0, dragOffsetY: 0,
  _canvasInited: false,
  _dragInited: false,
};

async function loadBoard() {
  const grid = $('emoji-picker');
  if (grid && !grid.children.length) {
    grid.innerHTML = BOARD_EMOJIS.map(e =>
      `<button class="emoji-btn" onclick="selectEmoji('${e}',this)">${e}</button>`
    ).join('');
  }
  try {
    const notes = await api.get('/api/board');
    const board  = $('corkboard');
    const hint   = $('board-empty-hint');
    board.querySelectorAll('.board-note').forEach(n => n.remove());
    if (notes.length > 0) {
      if (hint) hint.style.display = 'none';
      notes.forEach(renderBoardNote);
    } else {
      if (hint) hint.style.display = 'block';
    }
  } catch (e) { console.error('Board load error:', e); }

  if (!boardState._canvasInited) { initEditorCanvas(); boardState._canvasInited = true; }
  if (!boardState._dragInited)   { initBoardDrag();    boardState._dragInited   = true; }
}

function renderBoardNote(note) {
  const board = $('corkboard');
  if (!board) return;
  const el = document.createElement('div');
  el.className = 'board-note';
  el.dataset.id = note.id;
  el.style.left       = `${Math.max(0, note.x_pos)}px`;
  el.style.top        = `${Math.max(0, note.y_pos)}px`;
  el.style.background = note.color || '#FFEF5E';
  el.style.transform  = `rotate(${note.rotation || 0}deg)`;
  el.style.zIndex     = 10;
  const canDelete = note.is_mine && state.user;
  const daysLeft = note.expires_at
    ? Math.max(0, Math.ceil((new Date(note.expires_at.replace(' ','T')+'Z') - Date.now()) / 86400000))
    : 7;
  el.innerHTML = `
    <div class="note-pin">
      <div class="note-pin-head"></div>
      <div class="note-pin-shaft"></div>
    </div>
    ${note.drawing_data ? `<img class="note-drawing-img" src="${note.drawing_data}" alt="">` : ''}
    ${note.emoji        ? `<span class="note-emoji-big">${note.emoji}</span>` : ''}
    ${note.message      ? `<div class="note-message-text">${esc(note.message)}</div>` : ''}
    <div class="note-footer">
      <span class="note-author-tag">${esc(note.author_name)}</span>
      <span class="note-age-tag">${daysLeft}d left</span>
      ${canDelete
        ? `<button class="note-delete-btn" title="Remove"
                   onclick="deleteBoardNote(event,${note.id},this.closest('.board-note'))">×</button>`
        : ''}
    </div>
    <div class="note-expiry-bar" style="width:${Math.round((daysLeft/7)*100)}%"></div>
  `;
  el.addEventListener('mousedown', (e) => startNoteDrag(e, el));
  board.appendChild(el);
}

function initBoardDrag() {
  document.addEventListener('mousemove', (e) => {
    if (!boardState.dragNote) return;
    const board = $('corkboard');
    if (!board) return;
    const rect = board.getBoundingClientRect();
    const x = e.clientX - rect.left - boardState.dragOffsetX;
    const y = e.clientY - rect.top  - boardState.dragOffsetY;
    const maxX = board.clientWidth  - boardState.dragNote.offsetWidth  - 2;
    const maxY = board.clientHeight - boardState.dragNote.offsetHeight - 2;
    const cx = Math.max(0, Math.min(maxX, x));
    const cy = Math.max(0, Math.min(maxY, y));
    boardState.dragNote.style.left = `${cx}px`;
    boardState.dragNote.style.top  = `${cy}px`;
    boardState.dragNote._pendingX  = cx;
    boardState.dragNote._pendingY  = cy;
  });
  document.addEventListener('mouseup', async () => {
    if (!boardState.dragNote) return;
    const n = boardState.dragNote;
    n.classList.remove('dragging');
    n.style.transform = `rotate(${n._origRotation || 0}deg)`;
    const nid = n.dataset.id;
    const x = n._pendingX ?? parseFloat(n.style.left);
    const y = n._pendingY ?? parseFloat(n.style.top);
    boardState.dragNote = null;
    if (nid && state.user) {
      api.put(`/api/board/${nid}/position`, { x_pos: x, y_pos: y }).catch(() => {});
    }
  });
}

function startNoteDrag(e, el) {
  if (e.button !== 0) return;
  if (e.target.classList.contains('note-delete-btn')) return;
  boardState.dragNote   = el;
  const rect = el.getBoundingClientRect();
  boardState.dragOffsetX = e.clientX - rect.left;
  boardState.dragOffsetY = e.clientY - rect.top;
  el._origRotation = parseFloat(el.style.transform?.match(/rotate\(([^)]+)deg\)/)?.[1] || 0);
  el.classList.add('dragging');
  e.preventDefault();
}

function initEditorCanvas() {
  const canvas = $('editor-canvas');
  if (!canvas) return;
  canvas.addEventListener('mousedown', canvasMouseDown);
  canvas.addEventListener('mousemove', canvasMouseMove);
  canvas.addEventListener('mouseup',   () => { boardState.isDrawing = false; });
  canvas.addEventListener('mouseleave',() => { boardState.isDrawing = false; });
  canvas.addEventListener('touchstart', e => { e.preventDefault(); canvasMouseDown(e.touches[0]); }, { passive: false });
  canvas.addEventListener('touchmove',  e => { e.preventDefault(); canvasMouseMove(e.touches[0]); }, { passive: false });
  canvas.addEventListener('touchend',   () => { boardState.isDrawing = false; });
}

function canvasMouseDown(e) {
  boardState.isDrawing = true;
  const canvas = $('editor-canvas');
  const rect   = canvas.getBoundingClientRect();
  boardState.lastX = (e.clientX - rect.left) * (canvas.width  / rect.width);
  boardState.lastY = (e.clientY - rect.top)  * (canvas.height / rect.height);
}

function canvasMouseMove(e) {
  if (!boardState.isDrawing) return;
  const canvas = $('editor-canvas');
  const ctx    = canvas.getContext('2d');
  const rect   = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) * (canvas.width  / rect.width);
  const y = (e.clientY - rect.top)  * (canvas.height / rect.height);
  ctx.beginPath();
  ctx.moveTo(boardState.lastX, boardState.lastY);
  ctx.lineTo(x, y);
  if (boardState.isEraser) {
    ctx.lineWidth = 20;
    ctx.globalCompositeOperation = 'destination-out';
    ctx.strokeStyle = 'rgba(0,0,0,1)';
  } else {
    ctx.lineWidth = 3;
    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = boardState.penColor;
  }
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.stroke();
  boardState.lastX = x; boardState.lastY = y;
}

function toggleDrawMode() {
  boardState.isDrawMode = !boardState.isDrawMode;
  boardState.isEraser   = false;
  $('editor-canvas')?.classList.toggle('draw-active', boardState.isDrawMode);
  $('editor-surface')?.classList.toggle('draw-mode',   boardState.isDrawMode);
  $('draw-btn')?.classList.toggle('active',  boardState.isDrawMode);
  $('eraser-btn')?.classList.remove('active');
  if ($('pen-color-section')) $('pen-color-section').style.display = boardState.isDrawMode ? 'block' : 'none';
}

function toggleEraser() {
  if (!boardState.isDrawMode) {
    boardState.isDrawMode = true;
    $('draw-btn')?.classList.add('active');
    $('editor-canvas')?.classList.add('draw-active');
    $('editor-surface')?.classList.add('draw-mode');
    if ($('pen-color-section')) $('pen-color-section').style.display = 'block';
  }
  boardState.isEraser = !boardState.isEraser;
  $('eraser-btn')?.classList.toggle('active', boardState.isEraser);
  $('draw-btn')?.classList.toggle('active', !boardState.isEraser);
}

function clearDrawing() {
  const c = $('editor-canvas');
  if (c) c.getContext('2d').clearRect(0, 0, c.width, c.height);
}

function setNoteColor(color, el) {
  boardState.selectedColor = color;
  document.querySelectorAll('.note-color-swatch').forEach(s => s.classList.remove('active'));
  el.classList.add('active');
  if ($('editor-surface')) $('editor-surface').style.background = color;
  const topNote = document.querySelector('.tray-stack-note.top-note');
  if (topNote) topNote.style.background = color;
}

function setPenColor(color, el) {
  boardState.penColor = color;
  boardState.isEraser = false;
  document.querySelectorAll('.pen-color-swatch').forEach(s => s.classList.remove('active'));
  el.classList.add('active');
  $('eraser-btn')?.classList.remove('active');
}

function selectEmoji(emoji, btn) {
  boardState.selectedEmoji = (boardState.selectedEmoji === emoji) ? '' : emoji;
  document.querySelectorAll('.emoji-btn').forEach(b => b.classList.remove('selected'));
  if (boardState.selectedEmoji) btn.classList.add('selected');
  if ($('selected-emoji'))      $('selected-emoji').textContent      = boardState.selectedEmoji;
  if ($('editor-emoji-display'))$('editor-emoji-display').textContent = boardState.selectedEmoji;
}

function resetEditor() {
  boardState.selectedColor = '#FFEF5E';
  boardState.selectedEmoji = '';
  boardState.isDrawMode    = false;
  boardState.isEraser      = false;
  if ($('editor-surface')) {
    $('editor-surface').style.background = '#FFEF5E';
    $('editor-surface').classList.remove('draw-mode');
  }
  if ($('note-text-input'))     $('note-text-input').value = '';
  if ($('editor-canvas'))       $('editor-canvas').classList.remove('draw-active');
  clearDrawing();
  $('draw-btn')?.classList.remove('active');
  $('eraser-btn')?.classList.remove('active');
  document.querySelectorAll('.note-color-swatch').forEach((s,i) => s.classList.toggle('active', i===0));
  document.querySelectorAll('.emoji-btn').forEach(b => b.classList.remove('selected'));
  if ($('selected-emoji'))       $('selected-emoji').textContent       = '';
  if ($('editor-emoji-display')) $('editor-emoji-display').textContent = '';
  const topNote = document.querySelector('.tray-stack-note.top-note');
  if (topNote) topNote.style.background = '#FFEF5E';
  if ($('pen-color-section')) $('pen-color-section').style.display = 'none';
  toast('Fresh note ready!');
}

async function pinNoteToBoard() {
  if (!state.user) { openModal('login-modal'); return; }
  const message = $('note-text-input')?.value.trim() || '';
  const canvas  = $('editor-canvas');
  let drawingData = '';
  if (canvas) {
    const ctx = canvas.getContext('2d');
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    if (imageData.data.some((v, i) => i % 4 === 3 && v > 0)) {
      drawingData = canvas.toDataURL('image/png');
    }
  }
  if (!message && !drawingData && !boardState.selectedEmoji) {
    toast('Add a message, draw something, or pick an emoji first!');
    return;
  }
  const board  = $('corkboard');
  const noteW = 175, noteH = 195;
  const maxX  = Math.max(50, (board?.clientWidth  || 800) - noteW - 30);
  const maxY  = Math.max(50, (board?.clientHeight || 560) - noteH - 30);
  const x     = 20 + Math.random() * maxX;
  const y     = 20 + Math.random() * maxY;
  const rotation = (Math.random() * 10 - 5);
  const btn = $('pin-btn');
  if (btn) { btn.disabled = true; btn.textContent = '📌 Pinning…'; }
  try {
    const note = await api.post('/api/board', {
      message, color: boardState.selectedColor, emoji: boardState.selectedEmoji,
      drawing_data: drawingData, pen_color: boardState.penColor,
      x_pos: x, y_pos: y, rotation,
    });
    renderBoardNote(note);
    if ($('board-empty-hint')) $('board-empty-hint').style.display = 'none';
    toast('📌 Note pinned!');
    resetEditor();
    if (typeof confetti !== 'undefined') {
      confetti({ particleCount: 30, spread: 55, origin: { y: 0.7 },
                 colors: ['#FFEF5E','#FF8DA1','#A8F5A0','#B8E0FF','#E8B4F8'] });
    }
  } catch (e) {
    toast('Could not pin: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '📌 Pin to Board!'; }
  }
}

async function deleteBoardNote(e, noteId, el) {
  e.stopPropagation();
  if (!state.user) return;
  try {
    await api.del(`/api/board/${noteId}`);
    el.style.transition = 'opacity .3s, transform .3s';
    el.style.opacity    = '0';
    el.style.transform  = 'scale(0.8) rotate(15deg)';
    setTimeout(() => {
      el.remove();
      const board = $('corkboard');
      if (board && !board.querySelector('.board-note')) {
        if ($('board-empty-hint')) $('board-empty-hint').style.display = 'block';
      }
    }, 300);
    toast('Note removed.');
  } catch (err) { toast(err.message); }
}


// ── First-Time Walkthrough ────────────────────────────────────────────────
const WT_KEY      = 'gso_walkthrough_v1';
const WT_TOTAL    = 8;
let   _wtStep     = 1;

function initWalkthrough() {
  if (localStorage.getItem(WT_KEY)) return;
  // Short delay so the page renders first
  setTimeout(() => {
    const overlay = document.getElementById('walkthrough-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    _wtStep = 1;
    _renderWtStep();
  }, 600);
}

function _renderWtStep() {
  // Activate correct step
  document.querySelectorAll('.wt-step').forEach(el => {
    el.classList.toggle('active', parseInt(el.dataset.step) === _wtStep);
  });

  // Dots
  const dotsEl = document.getElementById('wt-dots');
  if (dotsEl) {
    dotsEl.innerHTML = Array.from({length: WT_TOTAL}, (_, i) =>
      `<div class="wt-dot${i + 1 === _wtStep ? ' active' : ''}" onclick="walkthroughGoTo(${i + 1})"></div>`
    ).join('');
  }

  // Back button
  const backBtn = document.getElementById('wt-back');
  if (backBtn) backBtn.style.display = _wtStep > 1 ? 'inline-flex' : 'none';

  // Next button label
  const nextBtn = document.getElementById('wt-next');
  if (nextBtn) {
    if (_wtStep === WT_TOTAL) {
      nextBtn.textContent = "Let's Go! 🚀";
      nextBtn.classList.add('finish');
    } else {
      nextBtn.textContent = 'Next →';
      nextBtn.classList.remove('finish');
    }
  }

  // Animate final icon on last step
  if (_wtStep === WT_TOTAL) {
    const icon = document.getElementById('wt-final-icon');
    if (icon) {
      icon.style.animation = 'none';
      requestAnimationFrame(() => {
        icon.style.animation = 'wt-slide-up 0.4s cubic-bezier(0.34,1.56,0.64,1)';
      });
      if (typeof confetti === 'function') {
        confetti({ particleCount: 60, spread: 80, origin: { y: 0.7 }, zIndex: 10000 });
      }
    }
  }
}

function walkthroughNext() {
  if (_wtStep >= WT_TOTAL) {
    completeWalkthrough();
  } else {
    _wtStep++;
    _renderWtStep();
  }
}

function walkthroughBack() {
  if (_wtStep > 1) { _wtStep--; _renderWtStep(); }
}

function walkthroughGoTo(step) {
  _wtStep = step;
  _renderWtStep();
}

function skipWalkthrough()    { completeWalkthrough(); }

function completeWalkthrough() {
  localStorage.setItem(WT_KEY, '1');
  const overlay = document.getElementById('walkthrough-overlay');
  if (!overlay) return;
  overlay.style.transition = 'opacity .3s';
  overlay.style.opacity    = '0';
  setTimeout(() => { overlay.style.display = 'none'; overlay.style.opacity = '1'; }, 300);
  // Prompt sign-in if not logged in
  if (!state.user) {
    setTimeout(() => openModal('login-modal'), 400);
  }
}
