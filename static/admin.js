/* ════════════════════════════════════════════════
   API 2 Cursor — Admin Panel JS
   ════════════════════════════════════════════════ */
const API = '';
let authKey = '';
let _currentPage = 'overview';
let _relayProfiles = {};
let _editingMappingName = null;
let _editingRelayName = null;
let _currentLogId = null;   // { date, id }

// ─── 工具 ────────────────────────────────────────
function togglePwd(id) {
  const el = document.getElementById(id);
  el.type = el.type === 'password' ? 'text' : 'password';
}

function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function fmtNum(n) {
  if (n == null || n === '') return '—';
  if (n >= 1e9) return (n/1e9).toFixed(1)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return String(n);
}

function fmtTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return Math.floor(diff/60000) + '分钟前';
    if (diff < 86400000) return Math.floor(diff/3600000) + '小时前';
    return d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  } catch { return iso; }
}

function toast(msg, ok = true) {
  const area = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + (ok ? 'toast-ok' : 'toast-err');
  el.textContent = ok ? '✓  ' + msg : '✕  ' + msg;
  area.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

async function apiFetch(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (authKey) headers['Authorization'] = 'Bearer ' + authKey;
  const res = await fetch(API + path, { ...opts, headers });
  const ct = res.headers.get('content-type') || '';
  if (!ct.includes('application/json')) {
    const text = await res.text();
    if (!res.ok) throw new Error('HTTP ' + res.status + ': ' + text.slice(0, 120));
    throw new Error('服务器返回了非 JSON 响应');
  }
  const data = await res.json();
  if (!res.ok) {
    const e = data.error;
    const msg = (typeof e === 'object' && e !== null)
      ? (e.message || JSON.stringify(e))
      : (e || data.message || 'HTTP ' + res.status);
    throw new Error(msg);
  }
  return data;
}

// ─── 登录 ────────────────────────────────────────
async function doLogin() {
  const key = document.getElementById('loginKey').value.trim();
  if (!key) { toast('请输入密钥', false); return; }
  try {
    const r = await apiFetch('/api/admin/login', { method: 'POST', body: JSON.stringify({ key }) });
    if (r.ok) {
      authKey = key;
      sessionStorage.setItem('_ak', key);
      document.getElementById('login').style.display = 'none';
      document.getElementById('app').classList.add('visible');
      initDashboard();
    }
  } catch { toast('密钥无效', false); }
}

function doLogout() {
  authKey = '';
  sessionStorage.removeItem('_ak');
  document.getElementById('app').classList.remove('visible');
  document.getElementById('login').style.display = 'flex';
}

// ─── 导航 ────────────────────────────────────────
const PAGE_TITLES = {
  overview: '仪表盘',
  relays: '中转站管理',
  mappings: '模型映射',
  settings: '全局设置',
  logs: '对话日志',
  livelog: '实时日志',
};

function navigate(page) {
  if (_currentPage === page) return;
  document.getElementById('page-' + _currentPage).style.display = 'none';
  document.querySelector(`.nav-item[data-page="${_currentPage}"]`).classList.remove('active');

  _currentPage = page;
  document.getElementById('page-' + page).style.display = '';
  document.querySelector(`.nav-item[data-page="${page}"]`).classList.add('active');
  document.getElementById('topbarTitle').textContent = PAGE_TITLES[page] || page;

  if (page === 'overview') loadStats();
  if (page === 'relays') loadRelays();
  if (page === 'mappings') loadMappings();
  if (page === 'settings') loadSettings();
  if (page === 'logs') loadLogs();
  if (page === 'livelog') startLivelog();
  else stopLivelog();
}

// ─── 初始化 ──────────────────────────────────────
async function initDashboard() {
  checkHealth();
  await loadRelaysMeta();   // 先拿中转站，供侧边栏和弹窗用
  loadStats();              // 首页统计
}

async function checkHealth() {
  const pill = document.getElementById('statusPill');
  try {
    const r = await fetch(API + '/health');
    const d = await r.json();
    if (d.status === 'ok') {
      pill.textContent = '在线';
      pill.className = 'status-pill ok';
    } else {
      pill.textContent = '异常';
      pill.className = 'status-pill err';
    }
  } catch {
    pill.textContent = '离线';
    pill.className = 'status-pill err';
  }
}

// ─── 统计 ────────────────────────────────────────
async function loadStats() {
  try {
    const data = await apiFetch('/api/admin/stats');
    const models = data.models || {};
    const keys = Object.keys(models);

    // 汇总数字
    let totalReq = 0, totalIn = 0, totalOut = 0;
    for (const s of Object.values(models)) {
      totalReq += s.request_count || 0;
      totalIn  += s.input_tokens  || 0;
      totalOut += s.output_tokens || 0;
    }
    document.getElementById('statRequests').textContent = fmtNum(totalReq);
    document.getElementById('statInput').textContent    = fmtNum(totalIn);
    document.getElementById('statOutput').textContent   = fmtNum(totalOut);

    const uptime = data.uptime_seconds || 0;
    const h = Math.floor(uptime / 3600);
    const m = Math.floor((uptime % 3600) / 60);
    const s = uptime % 60;
    document.getElementById('statUptime').textContent = h > 0 ? h + 'h ' + m + 'm' : m + 'm ' + s + 's';
    document.getElementById('statUptimeSub').textContent = h > 0 ? '小时分钟' : '分钟秒';

    const el = document.getElementById('statsTable');
    if (!keys.length) {
      el.innerHTML = '<div class="empty"><div class="empty-icon">📭</div>暂无统计数据</div>';
      return;
    }
    keys.sort((a, b) => models[b].request_count - models[a].request_count);
    let html = '<table class="data-table"><thead><tr><th>模型</th><th>请求数</th><th>输入 Tokens</th><th>输出 Tokens</th><th>合计</th></tr></thead><tbody>';
    for (const name of keys) {
      const s = models[name];
      html += `<tr>
        <td style="font-family:monospace;font-size:12px">${esc(name)}</td>
        <td>${s.request_count}</td>
        <td>${(s.input_tokens||0).toLocaleString()}</td>
        <td>${(s.output_tokens||0).toLocaleString()}</td>
        <td style="font-weight:600">${(s.total_tokens||0).toLocaleString()}</td>
      </tr>`;
    }
    html += '</tbody></table>';
    el.innerHTML = html;
  } catch (e) {
    document.getElementById('statsTable').innerHTML = '<div class="empty">加载失败</div>';
  }
}

// ─── 全局设置 ─────────────────────────────────────
async function loadSettings() {
  try {
    const s = await apiFetch('/api/admin/settings');
    document.getElementById('targetUrl').value    = s.proxy_target_url || '';
    document.getElementById('proxyKey').value     = s.proxy_api_key || '';
    document.getElementById('upstreamProxy').value = s.upstream_proxy || '';
    document.getElementById('debugMode').value    = s.debug_mode || 'off';
    document.getElementById('envUrl').textContent = s.env_target_url ? '环境变量: ' + s.env_target_url : '';
    document.getElementById('envKey').textContent = s.env_api_key ? '环境变量: (已配置)' : '';
  } catch (e) { toast('加载设置失败: ' + e.message, false); }
}

async function saveSettings() {
  try {
    await apiFetch('/api/admin/settings', {
      method: 'PUT',
      body: JSON.stringify({
        proxy_target_url: document.getElementById('targetUrl').value.trim(),
        proxy_api_key:    document.getElementById('proxyKey').value.trim(),
        upstream_proxy:   document.getElementById('upstreamProxy').value.trim(),
        debug_mode:       document.getElementById('debugMode').value,
      }),
    });
    toast('设置已保存');
  } catch (e) { toast('保存失败: ' + e.message, false); }
}

// ─── 中转站：元数据（侧边栏 + 弹窗用） ───────────
async function loadRelaysMeta() {
  try {
    const data = await apiFetch('/api/admin/relays');
    _relayProfiles = data.relay_profiles || {};
    _updateSidebarRelay(data.active_relay || '');
    _rebuildRelaySelect(data.active_relay || '');
    _rebuildRelayProfileSelect();
  } catch { /* silent */ }
}

function _updateSidebarRelay(active) {
  const dot   = document.getElementById('relayDot');
  const label = document.getElementById('relayLabel');
  if (active && _relayProfiles[active]) {
    dot.classList.add('active');
    label.textContent = active;
  } else {
    dot.classList.remove('active');
    label.textContent = '未激活中转站';
  }
}

function _rebuildRelaySelect(active) {
  const sel = document.getElementById('activeRelaySelect');
  sel.innerHTML = '<option value="">不使用（回退到全局设置地址）</option>' +
    Object.keys(_relayProfiles).map(n =>
      `<option value="${esc(n)}"${n===active?' selected':''}>${esc(n)} — ${esc(_relayProfiles[n].base_url||'')}</option>`
    ).join('');
  _updateActiveRelayHint(active);
}

function _rebuildRelayProfileSelect() {
  const sel = document.getElementById('mRelayProfile');
  const names = Object.keys(_relayProfiles);
  sel.innerHTML = names.length
    ? names.map(n => `<option value="${esc(n)}">${esc(n)} — ${esc(_relayProfiles[n].base_url||'')}</option>`).join('')
    : '<option value="">（暂无中转站）</option>';
}

function _updateActiveRelayHint(active) {
  const hint = document.getElementById('activeRelayHint');
  if (!hint) return;
  const r = _relayProfiles[active];
  hint.textContent = r ? '当前使用: ' + r.name + ' → ' + r.base_url : '当前未激活中转站，将回退到全局设置地址';
}

function onActiveRelayChange() {
  const val = document.getElementById('activeRelaySelect').value;
  const r = _relayProfiles[val];
  const hint = document.getElementById('activeRelayHint');
  hint.textContent = r ? '切换后使用: ' + r.name + ' → ' + r.base_url : '切换后回退到全局设置地址';
}

async function saveActiveRelay() {
  const active = document.getElementById('activeRelaySelect').value;
  const btn = document.getElementById('saveActiveRelayBtn');
  btn.disabled = true;
  try {
    await apiFetch('/api/admin/settings', {
      method: 'PUT', body: JSON.stringify({ active_relay: active }),
    });
    _updateSidebarRelay(active);
    _updateActiveRelayHint(active);
    toast(active ? '已切换到: ' + active : '已取消激活中转站');
  } catch (e) { toast('切换失败: ' + e.message, false); }
  finally { btn.disabled = false; }
}

// ─── 中转站：页面列表 ─────────────────────────────
async function loadRelays() {
  const el = document.getElementById('relayList');
  try {
    const data = await apiFetch('/api/admin/relays');
    _relayProfiles = data.relay_profiles || {};
    const active = data.active_relay || '';
    _rebuildRelaySelect(active);
    _rebuildRelayProfileSelect();
    _updateSidebarRelay(active);

    const names = Object.keys(_relayProfiles);
    if (!names.length) {
      el.innerHTML = '<div class="empty"><div class="empty-icon">🔗</div>暂无中转站，点击「+ 添加中转站」开始</div>';
      return;
    }
    el.innerHTML = '<div class="relay-list">' + names.map(name => {
      const r = _relayProfiles[name];
      const isActive = name === active;
      return `<div class="relay-item${isActive?' relay-item-active':''}">
        <div class="relay-top">
          <div class="relay-info">
            <span class="relay-name">${esc(name)}</span>
            ${isActive ? '<span class="tag tag-active">激活</span>' : ''}
            <span class="relay-url">${esc(r.base_url||'（地址未填写）')}</span>
          </div>
          <div class="relay-actions">
            ${!isActive ? `<button class="btn btn-ghost btn-sm" onclick="quickActivateRelay('${esc(name)}')">激活</button>` : ''}
            <button class="btn btn-ghost btn-sm" onclick="openRelayModal('${esc(name)}')">编辑</button>
            <button class="btn btn-red btn-sm" onclick="deleteRelay('${esc(name)}')">删除</button>
          </div>
        </div>
      </div>`;
    }).join('') + '</div>';
  } catch (e) {
    el.innerHTML = '<div class="empty">加载失败</div>';
    toast('加载中转站失败: ' + e.message, false);
  }
}

async function quickActivateRelay(name) {
  try {
    await apiFetch('/api/admin/settings', { method: 'PUT', body: JSON.stringify({ active_relay: name }) });
    toast('已激活: ' + name);
    loadRelays();
  } catch (e) { toast('激活失败: ' + e.message, false); }
}

// ─── 中转站弹窗 ───────────────────────────────────
function openRelayModal(name) {
  _editingRelayName = name || null;
  document.getElementById('relayModalTitle').textContent = name ? '编辑中转站' : '添加中转站';
  const nameInput = document.getElementById('rName');
  if (name && _relayProfiles[name]) {
    nameInput.value = name;
    nameInput.disabled = true;
    document.getElementById('rUrl').value = _relayProfiles[name].base_url || '';
    document.getElementById('rKey').value = '';
  } else {
    nameInput.value = '';
    nameInput.disabled = false;
    document.getElementById('rUrl').value = '';
    document.getElementById('rKey').value = '';
  }
  document.getElementById('relayModal').classList.add('active');
}
function closeRelayModal() {
  document.getElementById('relayModal').classList.remove('active');
  _editingRelayName = null;
}
async function saveRelay() {
  const name    = document.getElementById('rName').value.trim();
  const base_url= document.getElementById('rUrl').value.trim();
  const api_key = document.getElementById('rKey').value.trim();
  if (!name)     { toast('请填写中转站名称', false); return; }
  if (!base_url) { toast('请填写中转站地址', false); return; }
  try {
    if (_editingRelayName) {
      await apiFetch('/api/admin/relays/' + encodeURIComponent(_editingRelayName), {
        method: 'PUT', body: JSON.stringify({ name: _editingRelayName, base_url, api_key }),
      });
      toast('中转站已更新');
    } else {
      await apiFetch('/api/admin/relays', { method: 'POST', body: JSON.stringify({ name, base_url, api_key }) });
      toast('中转站已添加');
    }
    closeRelayModal();
    loadRelays();
  } catch (e) { toast('操作失败: ' + e.message, false); }
}
async function deleteRelay(name) {
  if (!confirm('确定删除中转站「' + name + '」？\n若有模型绑定此中转站将无法删除。')) return;
  try {
    await apiFetch('/api/admin/relays/' + encodeURIComponent(name), { method: 'DELETE' });
    toast('已删除');
    loadRelays();
  } catch (e) { toast('删除失败: ' + e.message, false); }
}

// ─── 模型映射 ─────────────────────────────────────
async function loadMappings() {
  const el = document.getElementById('mappingList');
  try {
    const mappings = await apiFetch('/api/admin/mappings');
    const keys = Object.keys(mappings);
    if (!keys.length) {
      el.innerHTML = '<div class="empty"><div class="empty-icon">🗺</div>暂无模型映射</div>';
      return;
    }
    el.innerHTML = '<div class="mapping-list">' + keys.map(name => {
      const m = mappings[name];
      const backend = m.backend || 'auto';
      const tagClass = {anthropic:'tag-anthropic',openai:'tag-openai',responses:'tag-responses',gemini:'tag-gemini'}[backend] || 'tag-auto';
      const tagLabel = backend === 'auto' ? '自动' : backend;
      const hasRelay = !!m.relay_profile;
      const hasOverride = !hasRelay && (m.target_url || m.api_key);
      return `<div class="mapping-item">
        <div class="mapping-top">
          <span class="mapping-name">${esc(name)}</span>
          <span class="mapping-arrow">→</span>
          <span class="mapping-upstream">${esc(m.upstream_model||name)}</span>
          <div class="mapping-meta">
            <span class="tag ${tagClass}">${tagLabel}</span>
            ${hasRelay    ? `<span class="tag tag-relay">🔗 ${esc(m.relay_profile)}</span>` : ''}
            ${hasOverride ? '<span class="tag tag-override">自定义地址</span>' : ''}
            ${m.custom_instructions ? '<span class="tag tag-instructions">自定义指令</span>' : ''}
            ${m.body_modifications && Object.keys(m.body_modifications).length ? '<span class="tag tag-mods">Body修改</span>' : ''}
            ${m.header_modifications && Object.keys(m.header_modifications).length ? '<span class="tag tag-mods">Header修改</span>' : ''}
          </div>
          <div class="mapping-actions">
            <button class="btn btn-ghost btn-sm" onclick="openEditModal('${esc(name)}')">编辑</button>
            <button class="btn btn-red btn-sm" onclick="deleteMapping('${esc(name)}')">删除</button>
          </div>
        </div>
      </div>`;
    }).join('') + '</div>';
  } catch (e) {
    el.innerHTML = '<div class="empty">加载失败</div>';
    toast('加载映射失败: ' + e.message, false);
  }
}

function onRelaySourceChange() {
  const val = document.getElementById('mRelaySource').value;
  document.getElementById('relayProfileRow').style.display = val === 'profile' ? '' : 'none';
  document.getElementById('customRelayRow').style.display  = val === 'custom'  ? '' : 'none';
}

function openAddModal() {
  _editingMappingName = null;
  document.getElementById('modalTitle').textContent = '添加模型映射';
  ['mName','mUpstream','mUrl','mKey','mInstructions','mBodyMods','mHeaderMods'].forEach(id => {
    document.getElementById(id).value = '';
  });
  document.getElementById('mName').disabled = false;
  document.getElementById('mBackend').value = 'auto';
  document.getElementById('mRelaySource').value = 'global';
  document.getElementById('mInsPosition').value = 'prepend';
  _rebuildRelayProfileSelect();
  onRelaySourceChange();
  document.getElementById('mappingModal').classList.add('active');
}

async function openEditModal(name) {
  _editingMappingName = name;
  document.getElementById('modalTitle').textContent = '编辑模型映射';
  try {
    const mappings = await apiFetch('/api/admin/mappings');
    const m = mappings[name];
    if (!m) { toast('映射未找到', false); return; }
    _rebuildRelayProfileSelect();
    document.getElementById('mName').value = name;
    document.getElementById('mName').disabled = false;
    document.getElementById('mUpstream').value = m.upstream_model || '';
    document.getElementById('mBackend').value  = m.backend || 'auto';
    if (m.relay_profile) {
      document.getElementById('mRelaySource').value  = 'profile';
      document.getElementById('mRelayProfile').value = m.relay_profile;
      document.getElementById('mUrl').value = '';
      document.getElementById('mKey').value = '';
    } else if (m.target_url || m.api_key) {
      document.getElementById('mRelaySource').value = 'custom';
      document.getElementById('mUrl').value = m.target_url || '';
      document.getElementById('mKey').value = m.api_key || '';
    } else {
      document.getElementById('mRelaySource').value = 'global';
      document.getElementById('mUrl').value = '';
      document.getElementById('mKey').value = '';
    }
    onRelaySourceChange();
    document.getElementById('mInstructions').value = m.custom_instructions || '';
    document.getElementById('mInsPosition').value  = m.instructions_position || 'prepend';
    document.getElementById('mBodyMods').value   = m.body_modifications   && Object.keys(m.body_modifications).length   ? JSON.stringify(m.body_modifications,null,2) : '';
    document.getElementById('mHeaderMods').value = m.header_modifications && Object.keys(m.header_modifications).length ? JSON.stringify(m.header_modifications,null,2) : '';
    document.getElementById('mappingModal').classList.add('active');
  } catch (e) { toast('错误: ' + e.message, false); }
}

function closeMappingModal() {
  document.getElementById('mappingModal').classList.remove('active');
  _editingMappingName = null;
}

async function saveMapping() {
  const name     = document.getElementById('mName').value.trim();
  const upstream = document.getElementById('mUpstream').value.trim();
  if (!name)     { toast('请填写 Cursor 模型名', false); return; }
  if (!upstream) { toast('请填写上游模型名', false); return; }

  let bodyMods = {}, headerMods = {};
  const bStr = document.getElementById('mBodyMods').value.trim();
  const hStr = document.getElementById('mHeaderMods').value.trim();
  if (bStr) { try { bodyMods = JSON.parse(bStr); } catch { toast('Body 修改格式错误', false); return; } }
  if (hStr) { try { headerMods = JSON.parse(hStr); } catch { toast('Header 修改格式错误', false); return; } }

  const src = document.getElementById('mRelaySource').value;
  let relay_profile = '', target_url = '', api_key = '';
  if (src === 'profile') {
    relay_profile = document.getElementById('mRelayProfile').value;
    if (!relay_profile) { toast('请选择中转站', false); return; }
  } else if (src === 'custom') {
    target_url = document.getElementById('mUrl').value.trim();
    api_key    = document.getElementById('mKey').value.trim();
  }

  const payload = {
    name, upstream_model: upstream,
    backend: document.getElementById('mBackend').value,
    relay_profile, target_url, api_key,
    custom_instructions:  document.getElementById('mInstructions').value,
    instructions_position:document.getElementById('mInsPosition').value,
    body_modifications:   bodyMods,
    header_modifications: headerMods,
  };

  try {
    if (_editingMappingName) {
      await apiFetch('/api/admin/mappings/' + encodeURIComponent(_editingMappingName), { method:'PUT', body:JSON.stringify(payload) });
      toast('映射已更新');
    } else {
      await apiFetch('/api/admin/mappings', { method:'POST', body:JSON.stringify(payload) });
      toast('映射已添加');
    }
    closeMappingModal();
    loadMappings();
  } catch (e) { toast('操作失败: ' + e.message, false); }
}

async function deleteMapping(name) {
  if (!confirm('确定删除映射「' + name + '」？')) return;
  try {
    await apiFetch('/api/admin/mappings/' + encodeURIComponent(name), { method:'DELETE' });
    toast('已删除');
    loadMappings();
  } catch (e) { toast('删除失败: ' + e.message, false); }
}

// ─── 对话日志 ─────────────────────────────────────
async function loadLogs() {
  const el = document.getElementById('logFileList');
  el.innerHTML = '<div class="empty" style="padding:24px">加载中…</div>';
  try {
    const data = await apiFetch('/api/admin/logs');
    const days = data.days || [];
    if (!days.length) {
      el.innerHTML = '<div class="empty" style="padding:24px"><div class="empty-icon">📂</div>暂无日志文件<br><span style="font-size:11px">需开启「详细日志」模式</span></div>';
      return;
    }
    let html = '';
    for (const day of days) {
      html += `<div class="log-date-group">
        <div class="log-date-label">${esc(day.date)}</div>`;
      for (const f of day.files) {
        const routeTag = f.route ? `<span class="tag tag-${f.route==='chat'?'chat':f.route==='messages'?'messages':'responses'}" style="font-size:10px;padding:1px 5px">${esc(f.route)}</span>` : '';
        html += `<div class="log-file-item" id="lfi-${esc(f.id)}" onclick="loadLogDetail('${esc(f.date)}','${esc(f.id)}')">
          <div class="log-file-name">${esc(f.conversation_id.slice(0,28)+(f.conversation_id.length>28?'…':''))}</div>
          <div class="log-file-meta">
            ${routeTag}
            ${f.last_client_model ? `<span style="font-size:11px;color:var(--muted);font-family:monospace">${esc(f.last_client_model.slice(0,20))}</span>` : ''}
            <span class="log-file-time">${fmtTime(f.updated_at)}</span>
            ${f.turn_count ? `<span style="font-size:10px;color:var(--muted)">${f.turn_count}轮</span>` : ''}
          </div>
        </div>`;
      }
      html += '</div>';
    }
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = '<div class="empty" style="padding:24px">加载失败</div>';
    toast('加载日志失败: ' + e.message, false);
  }
}

async function loadLogDetail(date, id) {
  _currentLogId = { date, id };
  // 高亮选中
  document.querySelectorAll('.log-file-item').forEach(el => el.classList.remove('selected'));
  const item = document.getElementById('lfi-' + id);
  if (item) item.classList.add('selected');

  const header = document.getElementById('logDetailHeader');
  const body   = document.getElementById('logDetailBody');
  header.style.display = '';
  document.getElementById('logDetailTitle').textContent = id;
  body.innerHTML = '<div class="log-empty"><div class="log-empty-icon">⏳</div>加载中…</div>';

  try {
    const doc = await apiFetch(`/api/admin/logs/${encodeURIComponent(date)}/${encodeURIComponent(id)}`);
    const turns = doc.turns || [];
    if (!turns.length) {
      body.innerHTML = '<div class="log-empty"><div class="log-empty-icon">📭</div>暂无记录</div>';
      return;
    }
    body.innerHTML = turns.map((t, i) => renderTurn(t, i)).join('');
  } catch (e) {
    body.innerHTML = `<div class="log-empty"><div class="log-empty-icon">⚠️</div>加载失败: ${esc(e.message)}</div>`;
  }
}

function renderTurn(t, idx) {
  const bkTag = `<span class="tag tag-${t.backend==='anthropic'?'anthropic':t.backend==='openai'?'openai':t.backend==='responses'?'responses':t.backend==='gemini'?'gemini':'auto'}">${esc(t.backend||'?')}</span>`;
  const streamTag = t.stream ? '<span class="tag tag-relay">流式</span>' : '';
  const errBadge = t.error ? '<span class="tag tag-error">Error</span>' : '';

  const sections = [];

  if (t.upstream_request) {
    sections.push(renderSection('上游请求 (upstream_request)', t.upstream_request));
  }
  if (t.upstream_response) {
    sections.push(renderSection('上游响应 (upstream_response)', t.upstream_response));
  }
  if (t.client_response) {
    sections.push(renderSection('客户端响应 (client_response)', t.client_response));
  }
  if (t.error) {
    sections.push(`<div class="log-section-label">错误</div><div class="log-error-block">${esc(JSON.stringify(t.error,null,2))}</div>`);
  }
  if (t.stream_trace && t.stream_trace.summary && Object.keys(t.stream_trace.summary).length) {
    sections.push(renderSection('流式摘要 (stream_trace.summary)', t.stream_trace.summary));
  }

  return `<div class="log-turn">
    <div class="log-turn-header" onclick="toggleTurn(${idx})">
      <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px">
        <span style="font-size:12px;font-weight:700;color:var(--text2)">Turn ${idx+1}</span>
        ${bkTag}${streamTag}${errBadge}
        <span style="font-size:11px;color:var(--muted);font-family:monospace">${esc(t.client_model||'')}</span>
        <span style="font-size:11px;color:var(--muted)">${fmtTime(t.started_at)}</span>
        ${t.usage ? `<span style="font-size:11px;color:var(--muted)">in:${t.usage.input_tokens||t.usage.prompt_tokens||0} out:${t.usage.output_tokens||t.usage.completion_tokens||0}</span>` : ''}
      </div>
      <span class="log-turn-toggle" id="turn-toggle-${idx}">▼</span>
    </div>
    <div class="log-turn-body" id="turn-body-${idx}">
      ${sections.join('')}
    </div>
  </div>`;
}

function renderSection(label, data) {
  const id = 'sec-' + Math.random().toString(36).slice(2);
  const json = JSON.stringify(data, null, 2);
  const big = json.length > 600;
  return `<div>
    <div class="log-section-label">${esc(label)}</div>
    <div class="log-json${big?' collapsed':''}" id="${id}">${esc(json)}</div>
    ${big ? `<span class="log-expand-btn" onclick="expandSection('${id}',this)">展开全部 ↓</span>` : ''}
  </div>`;
}

function expandSection(id, btn) {
  const el = document.getElementById(id);
  el.classList.remove('collapsed');
  btn.remove();
}

function toggleTurn(idx) {
  const body   = document.getElementById('turn-body-' + idx);
  const toggle = document.getElementById('turn-toggle-' + idx);
  const hidden = body.style.display === 'none';
  body.style.display   = hidden ? '' : 'none';
  toggle.textContent   = hidden ? '▼' : '▶';
}

async function deleteCurrentLog() {
  if (!_currentLogId) return;
  if (!confirm('确定删除这条对话日志？')) return;
  try {
    await apiFetch(`/api/admin/logs/${encodeURIComponent(_currentLogId.date)}/${encodeURIComponent(_currentLogId.id)}`, { method:'DELETE' });
    toast('日志已删除');
    _currentLogId = null;
    document.getElementById('logDetailHeader').style.display = 'none';
    document.getElementById('logDetailBody').innerHTML = '<div class="log-empty"><div class="log-empty-icon">📂</div>选择左侧对话查看详情</div>';
    loadLogs();
  } catch (e) { toast('删除失败: ' + e.message, false); }
}

// ─── 实时日志 ─────────────────────────────────────
let _livelogTimer = null;
let _livelogSince = 0;
let _livelogAllEntries = [];   // 所有已收到的条目（用于前端过滤）
let _livelogFilterLevel = '';

function startLivelog() {
  _livelogSince = 0;
  _livelogAllEntries = [];
  _livelogFilterLevel = document.getElementById('livelogFilter')?.value || '';
  document.getElementById('livelogBody').innerHTML = '<div style="color:var(--muted)">连接中…</div>';
  _pollLivelog();
}

function stopLivelog() {
  if (_livelogTimer) { clearTimeout(_livelogTimer); _livelogTimer = null; }
}

async function _pollLivelog() {
  _livelogTimer = null;
  if (_currentPage !== 'livelog') return;
  const paused = document.getElementById('livelogPause')?.checked;
  if (!paused) {
    try {
      const data = await apiFetch('/api/admin/live-logs?since=' + _livelogSince);
      if (data.logs && data.logs.length) {
        _livelogAllEntries.push(...data.logs);
        _livelogSince = data.total;
        _renderLivelog(data.logs);
      } else if (_livelogAllEntries.length === 0) {
        document.getElementById('livelogBody').innerHTML = '<div style="color:var(--muted)">等待日志输出…</div>';
      }
    } catch { /* ignore */ }
  }
  _livelogTimer = setTimeout(_pollLivelog, 1500);
}

function _renderLivelog(entries) {
  const body = document.getElementById('livelogBody');
  const filter = _livelogFilterLevel;
  const autoScroll = document.getElementById('livelogAutoScroll')?.checked;

  // 首次加载：清空占位
  if (body.children.length === 1 && body.firstElementChild?.style?.color?.includes('muted') ||
      body.innerHTML.includes('等待') || body.innerHTML.includes('连接')) {
    body.innerHTML = '';
  }

  for (const e of entries) {
    if (filter && e.level !== filter) continue;
    const line = document.createElement('div');
    line.className = 'llline llline-' + e.level.toLowerCase();
    line.innerHTML =
      `<span class="ll-ts">${esc(e.ts)}</span>` +
      `<span class="ll-lvl ll-${e.level.toLowerCase()}">${esc(e.level)}</span>` +
      `<span class="ll-logger">${esc(e.logger)}</span>` +
      `<span class="ll-msg">${esc(e.msg)}</span>`;
    body.appendChild(line);
  }

  // 最多保留 2000 行 DOM
  while (body.children.length > 2000) body.removeChild(body.firstChild);

  if (autoScroll) body.scrollTop = body.scrollHeight;
}

function applyLivelogFilter() {
  _livelogFilterLevel = document.getElementById('livelogFilter').value;
  // 重新用已缓存条目渲染
  const body = document.getElementById('livelogBody');
  body.innerHTML = '';
  const filtered = _livelogFilterLevel
    ? _livelogAllEntries.filter(e => e.level === _livelogFilterLevel)
    : _livelogAllEntries;
  _renderLivelog(filtered);
}

function clearLivelog() {
  _livelogAllEntries = [];
  _livelogSince = 0;
  document.getElementById('livelogBody').innerHTML = '<div style="color:var(--muted)">已清空，等待新日志…</div>';
}

// ─── 初始化入口 ───────────────────────────────────
(function init() {
  const saved = sessionStorage.getItem('_ak');
  if (saved) {
    authKey = saved;
    document.getElementById('login').style.display = 'none';
    document.getElementById('app').classList.add('visible');
    initDashboard();
  }
})();

// 弹窗关闭
document.getElementById('mappingModal').addEventListener('click', function(e) { if(e.target===this) closeMappingModal(); });
document.getElementById('relayModal').addEventListener('click',   function(e) { if(e.target===this) closeRelayModal(); });
document.addEventListener('keydown', e => { if(e.key==='Escape'){ closeMappingModal(); closeRelayModal(); } });
