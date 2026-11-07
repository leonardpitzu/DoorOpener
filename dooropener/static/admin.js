// DoorOpener admin panel. The server passes its settings as data- attributes on
// <body>; BASE is the path the panel is served under: '' on DoorOpener's own port,
// the ingress path inside Home Assistant's sidebar.
const BASE = document.body.dataset.base || '';
const CSRF_TOKEN = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

let allLogs = [];
let allUsers = [];
let filteredLogs = [];
let isAuthenticated = false;

// XSS-safe escaping utilities
const _escDiv = document.createElement('div');
function escapeHtml(str) {
    if (!str) return '';
    _escDiv.textContent = str;
    return _escDiv.innerHTML;
}
function escapeAttr(str) {
    if (!str) return '';
    return str.replace(/[&"'<>]/g, c => ({'&':'&amp;','"':'&quot;',"'":'&#39;','<':'&lt;','>':'&gt;'}[c]));
}

async function login() {
    const password = document.getElementById('adminPassword').value;
    const rememberMe = document.getElementById('rememberMe').checked;
    const errorDiv = document.getElementById('loginError');

    try {
        const res = await fetch(BASE + '/admin/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
            credentials: 'same-origin',
            cache: 'no-store',
            body: JSON.stringify({ password, remember_me: rememberMe })
        });
        const data = await res.json();
        if (data.status === 'success') {
            // Reload the page so the server renders the full dashboard HTML
            window.location.reload();
        } else {
            errorDiv.textContent = data.message || 'Invalid admin password';
            errorDiv.style.display = 'block';
        }
    } catch (error) {
        errorDiv.textContent = 'Error connecting to server';
        errorDiv.style.display = 'block';
    }
}

// Check if already authenticated on page load
async function checkAuthentication() {
    const dash = document.getElementById('dashboard');
    if (dash && dash.style.display !== 'none') {
        // Server already rendered the dashboard — we're authenticated
        isAuthenticated = true;
        const form = document.getElementById('loginForm');  // absent inside Home Assistant
        if (form) form.style.display = 'none';
        await Promise.all([loadLogs(), loadUsers()]);
    }
}

function logout() {
    fetch(BASE + '/admin/logout', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': CSRF_TOKEN,
        },
        credentials: 'same-origin',
        cache: 'no-store'
    })
    .then(response => response.json())
    .then(data => {
        isAuthenticated = false;
        document.getElementById('loginForm').style.display = 'block';
        document.getElementById('dashboard').style.display = 'none';
        document.getElementById('adminPassword').value = '';
        document.getElementById('rememberMe').checked = false;
    })
    .catch(error => {
        // Handle logout error silently
        isAuthenticated = false;
        document.getElementById('loginForm').style.display = 'block';
        document.getElementById('dashboard').style.display = 'none';
    });
}

// Allow Enter key to login
document.addEventListener('DOMContentLoaded', function() {
    // Check authentication on page load
    checkAuthentication();
    // Apply compact preference
    applyCompactFromStorage();

    _bind('adminPassword', 'keypress', (e) => { if (e.key === 'Enter') login(); });
});

let _loading = false;
async function loadLogs() {
    if (!isAuthenticated || _loading) return;
    _loading = true;
    try {
        const res = await fetch(BASE + '/admin/logs', { credentials: 'same-origin', cache: 'no-store' });
        const data = await res.json();
        allLogs = data.logs || [];
        populateUserFilter();
        filterLogs();
    } catch (e) {
        document.getElementById('logTableBody').innerHTML = 
            '<tr><td colspan="5" class="no-logs">Error loading logs</td></tr>';
    } finally {
        _loading = false;
    }
}

// --- User Management ---
async function loadUsers() {
    try {
        const res = await fetch(BASE + '/admin/users', { credentials: 'same-origin', cache: 'no-store' });
        const data = await res.json();
        allUsers = data.users || [];
        renderUsers(allUsers);
    } catch (e) {
        const tbody = document.getElementById('usersTableBody');
        if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="no-logs">Error loading users</td></tr>';
    }
}

function renderUsers(users) {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;
    if (!users.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="no-logs">No users found</td></tr>';
        return;
    }
    const rows = users.map(u => {
        const activeStr = u.active ? 'Yes' : 'No';
        const created = u.created_at ? new Date(u.created_at).toLocaleString() : '-';
        const lastUsed = u.last_used_at ? new Date(u.last_used_at).toLocaleString() : '-';
        const timesUsed = u.times_used || 0;
        const src = u.source || 'store';
        const actionsHtml = `
            <button class="refresh-btn" data-action="edit" data-user="${escapeAttr(u.username)}" style="padding:6px 10px; background: linear-gradient(135deg, #5e35b1, #4527a0);">Edit</button>
            <button class="refresh-btn" data-action="toggle" data-user="${escapeAttr(u.username)}" data-active="${u.active ? 'false' : 'true'}" style="padding:6px 10px; background: linear-gradient(135deg, #00897b, #00695c);">${u.active ? 'Deactivate' : 'Activate'}</button>
            <button class="refresh-btn" data-action="delete" data-user="${escapeAttr(u.username)}" style="padding:6px 10px; background: linear-gradient(135deg, #e53935, #c62828);">Delete</button>
        `;
        return `
            <tr>
                <td data-label="Username">${escapeHtml(u.username)}</td>
                <td data-label="Active">${activeStr}</td>
                <td data-label="Access">${escapeHtml(describeAccess(u.access))}</td>
                <td data-label="Source">${src}</td>
                <td data-label="Created">${created}</td>
                <td data-label="Last Used">${lastUsed}</td>
                <td data-label="Times Used">${timesUsed}</td>
                <td data-label="Actions" style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">${actionsHtml}</td>
            </tr>
        `;}).join('');
    tbody.innerHTML = rows;
}

// --- Sexy Modal Framework ---
function showModal(title, fieldsHtml, onSubmit) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.35); display:flex; align-items:center; justify-content:center; z-index:9999;';
    const card = document.createElement('div');
    card.style.cssText = 'min-width:320px; max-width:92vw; background: linear-gradient(135deg, rgba(255,255,255,0.5), rgba(255,255,255,0.25)); border:1px solid rgba(255,255,255,0.5); border-radius:16px; backdrop-filter: blur(14px); box-shadow: 0 12px 40px rgba(0,0,0,0.25); padding:20px;';
    card.innerHTML = `
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;">
            <h3 style="margin:0; color:rgba(0,0,0,0.85);">${title}</h3>
            <button id="modalCloseBtn" style="border:none; background:transparent; font-size:20px; cursor:pointer; color:rgba(0,0,0,0.6)">✕</button>
        </div>
        <div class="modal-body">${fieldsHtml}</div>
        <div style="display:flex; gap:8px; justify-content:flex-end; margin-top:14px;">
            <button id="modalCancel" class="refresh-btn" style="background: linear-gradient(135deg, #9e9e9e, #757575);">Cancel</button>
            <button id="modalOk" class="refresh-btn">OK</button>
        </div>
    `;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    function close() { try { document.body.removeChild(overlay); } catch(e) {} }
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    card.querySelector('#modalCloseBtn').addEventListener('click', close);
    card.querySelector('#modalCancel').addEventListener('click', close);
    card.querySelector('#modalOk').addEventListener('click', async () => {
        try { await onSubmit(card); close(); } catch (e) { /* keep modal open on error */ }
    });
    return card;
}

// A PIN input with a generator next to it: pick a length, and the server
// fills in a PIN that no user has and nobody would guess first.
function pinField(id, label, placeholder) {
    const lengths = [4, 5, 6, 7, 8]
        .map(n => `<option value="${n}"${n === 6 ? ' selected' : ''}>${n} digits</option>`)
        .join('');
    return `
        <label class="field-label" for="${id}">${label}</label>
        <div class="pin-row">
            <input id="${id}" type="password" inputmode="numeric" pattern="[0-9]*" autocomplete="off" placeholder="${placeholder}">
            <select id="${id}_len" aria-label="PIN length">${lengths}</select>
            <button type="button" id="${id}_gen" class="refresh-btn generate-btn">🎲 Generate</button>
        </div>
    `;
}

function bindPinGenerator(card, id) {
    const input = card.querySelector('#' + id);
    const length = card.querySelector('#' + id + '_len');
    const generate = card.querySelector('#' + id + '_gen');
    const save = card.querySelector('#modalOk');
    generate.addEventListener('click', async () => {
        // Saving waits for the PIN, so it can never send a half-filled field.
        generate.disabled = save.disabled = true;
        try {
            const res = await fetch(BASE + '/admin/generate-pin?length=' + encodeURIComponent(length.value), {
                credentials: 'same-origin',
                cache: 'no-store',
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                showToast('Could not generate a PIN: ' + (data.message || res.status), 'error');
                return;
            }
            input.value = data.pin;
            input.type = 'text';  // the admin has to read it to pass it on
            input.focus();
        } catch (e) {
            showToast('Could not generate a PIN', 'error');
        } finally {
            generate.disabled = save.disabled = false;
        }
    });
}

function openAddUser() {
    const fields = `
        <label class="field-label" for="um_username">Username</label>
        <input id="um_username" placeholder="letters, numbers, _-." autocomplete="off" style="width:100%">
        ${pinField('um_pin', 'PIN (4-8 digits)', 'type one, or generate')}
        ${accessFields(null)}
    `;
    const card = showModal('Add User', fields, async (card) => {
        const username = card.querySelector('#um_username').value.trim();
        const pin = card.querySelector('#um_pin').value.trim();
        if (!username) { showToast('Username required', 'error'); throw new Error('v'); }
        if (!/^[-_.A-Za-z0-9]{1,32}$/.test(username)) { showToast('Invalid username', 'error'); throw new Error('v'); }
        if (!/^\d{4,8}$/.test(pin)) { showToast('PIN must be 4-8 digits', 'error'); throw new Error('v'); }
        await addUser(username, pin, readAccess(card));
    });
    bindPinGenerator(card, 'um_pin');
}

async function addUser(username, pin, access) {
    try {
        const res = await fetch(BASE + '/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
            credentials: 'same-origin',
            cache: 'no-store',
            body: JSON.stringify({ username, pin, active: true, access }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast('Create failed: ' + (err.message || res.status), 'error');
            return;
        }
        showToast('User created', 'success');
        await loadUsers();
    } catch (e) {
        showToast('Create failed', 'error');
    }
}

function openEditUser(username) {
    const user = allUsers.find((u) => u.username === username) || {};
    const fields = `
        <div style="color: rgba(0,0,0,0.7); margin-bottom:6px">Editing <b>${escapeHtml(username)}</b></div>
        ${pinField('um_newpin', 'New PIN (optional)', 'leave blank to keep current')}
        ${accessFields(user.access)}
    `;
    const card = showModal('Edit User', fields, async (card) => {
        const pin = card.querySelector('#um_newpin').value.trim();
        if (pin && !/^\d{4,8}$/.test(pin)) { showToast('PIN must be 4-8 digits', 'error'); throw new Error('v'); }
        await editUser(username, pin || undefined, readAccess(card));
    });
    bindPinGenerator(card, 'um_newpin');
}

async function editUser(username, pin, access) {
    try {
        const body = { access };
        if (pin !== undefined) body.pin = pin;
        const res = await fetch(BASE + '/admin/users/' + encodeURIComponent(username), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
            credentials: 'same-origin',
            cache: 'no-store',
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast('Update failed: ' + (err.message || res.status), 'error');
            return;
        }
        showToast('User updated', 'success');
        await loadUsers();
    } catch (e) {
        showToast('Update failed', 'error');
    }
}

async function toggleActive(username, active) {
    try {
        const res = await fetch(BASE + '/admin/users/' + encodeURIComponent(username), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
            credentials: 'same-origin',
            cache: 'no-store',
            body: JSON.stringify({ active }),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast('Toggle failed: ' + (err.message || res.status), 'error');
            return;
        }
        showToast('User ' + (active ? 'activated' : 'deactivated'), 'success');
        await loadUsers();
    } catch (e) {
        showToast('Toggle failed', 'error');
    }
}

async function deleteUser(username) {
    if (!confirm('Delete user ' + username + '?')) return;
    try {
        const res = await fetch(BASE + '/admin/users/' + encodeURIComponent(username), {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': CSRF_TOKEN },
            credentials: 'same-origin',
            cache: 'no-store',
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast('Delete failed: ' + (err.message || res.status), 'error');
            return;
        }
        showToast('User deleted', 'success');
        await loadUsers();
    } catch (e) {
        showToast('Delete failed', 'error');
    }
}

// --- Access rules: when a PIN works (guests, cleaners, couriers) ---
// Dates and hours are the door's local time; the server checks them.
function accessFields(rules) {
    rules = rules || {};
    const days = rules.days || [0, 1, 2, 3, 4, 5, 6];
    const hours = rules.hours || ['', ''];
    const limited = Object.values(rules).some(Boolean);
    const dayBoxes = DAY_NAMES.map((name, i) =>
        `<label><input type="checkbox" value="${i}"${days.includes(i) ? ' checked' : ''}>${name}</label>`).join('');
    return `
        <details class="access"${limited ? ' open' : ''}>
            <summary>Limit when this PIN works</summary>
            <div class="access-row">
                <div><label class="field-label" for="ax_from">Valid from</label>
                    <input id="ax_from" type="datetime-local" value="${escapeAttr(rules.valid_from || '')}"></div>
                <div><label class="field-label" for="ax_until">Valid until</label>
                    <input id="ax_until" type="datetime-local" value="${escapeAttr(rules.valid_until || '')}"></div>
            </div>
            <div class="field-label">Days</div>
            <div class="days" id="ax_days">${dayBoxes}</div>
            <div class="field-label">Hours <span class="hint">(empty: all day; 22:00 to 06:00 runs overnight)</span></div>
            <div class="hours">
                <input id="ax_hours_from" type="time" value="${escapeAttr(hours[0])}" aria-label="From">
                <span>to</span>
                <input id="ax_hours_until" type="time" value="${escapeAttr(hours[1])}" aria-label="Until">
            </div>
            <label class="one-time"><input id="ax_once" type="checkbox"${rules.one_time ? ' checked' : ''}>
                One-time PIN: deactivated after the first opening</label>
            <div class="hint">Times are the door's local time.</div>
        </details>`;
}

function readAccess(card) {
    const value = (id) => card.querySelector('#' + id).value;
    const days = [...card.querySelectorAll('#ax_days input:checked')].map((box) => Number(box.value));
    const from = value('ax_hours_from');
    const until = value('ax_hours_until');
    if (!days.length) { showToast('Pick at least one day', 'error'); throw new Error('v'); }
    if (!from !== !until) { showToast('Set both hours, or neither', 'error'); throw new Error('v'); }
    const rules = {
        valid_from: value('ax_from') || null,
        valid_until: value('ax_until') || null,
        days: days.length === 7 ? null : days,
        hours: from ? [from, until] : null,
        one_time: card.querySelector('#ax_once').checked,
    };
    return Object.values(rules).some(Boolean) ? rules : null;
}

function describeAccess(rules) {
    if (!rules) return 'Always';
    const when = (s) => new Date(s).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    const parts = [];
    if (rules.valid_from) parts.push('from ' + when(rules.valid_from));
    if (rules.valid_until) parts.push('until ' + when(rules.valid_until));
    if (rules.days) parts.push(rules.days.map((d) => DAY_NAMES[d]).join(' '));
    if (rules.hours) parts.push(rules.hours.join('-'));
    if (rules.one_time) parts.push('one-time');
    if (rules.valid_until && new Date(rules.valid_until) < new Date()) parts.push('(expired)');
    return parts.join(' · ');
}

function openClearAll(btnId) {
    const fields = `
        <div style="color: rgba(0,0,0,0.75); margin-bottom:8px">This will permanently remove all log entries.</div>
        <div style="font-size: 0.92em; color: rgba(0,0,0,0.6)">Are you sure?</div>
    `;
    showModal('Clear All Logs', fields, async () => {
        await clearLogs('all', btnId);
    });
}

async function clearLogs(mode, btnId) {
    try {
        const restore = setButtonBusy(btnId, mode === 'all' ? 'Clearing…' : 'Removing…');
        const res = await fetch(BASE + '/admin/logs/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
            credentials: 'same-origin',
            cache: 'no-store',
            body: JSON.stringify({ mode })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast('Clear logs failed: ' + (data.message || res.status), 'error');
            if (typeof restore === 'function') restore();
            return;
        }
        const removed = data.removed ?? 0;
        showToast((mode === 'all' ? 'All logs cleared' : `Removed ${removed} test entries`), 'success');
        await loadLogs();
        if (typeof restore === 'function') restore();
    } catch (e) {
        showToast('Clear logs failed', 'error');
    }
}

function populateUserFilter() {
    const userSelect = document.getElementById('filterUser');
    const users = [...new Set(allLogs.map(log => log.user).filter(user => user))];

    // Clear existing options except "All users"
    userSelect.innerHTML = '<option value="">All users</option>';

    users.sort().forEach(user => {
        const option = document.createElement('option');
        option.value = user;
        option.textContent = user;
        userSelect.appendChild(option);
    });
}

function filterLogs() {
    const userFilter = document.getElementById('filterUser').value;
    const statusFilter = document.getElementById('filterStatus').value;

    filteredLogs = allLogs.filter(log => {
        const userMatch = !userFilter || log.user === userFilter;
        const statusMatch = !statusFilter || log.status === statusFilter;
        return userMatch && statusMatch;
    });

    sortLogs();
}

function sortLogs() {
    const sortBy = document.getElementById('sortBy').value;

    filteredLogs.sort((a, b) => {
        switch(sortBy) {
            case 'time':
                // Sort by raw timestamp string for ISO format
                return b.timestamp.localeCompare(a.timestamp);
            case 'time_asc':
                return a.timestamp.localeCompare(b.timestamp);
            case 'user':
                return (a.user || 'UNKNOWN').localeCompare(b.user || 'UNKNOWN');
            case 'status':
                return a.status.localeCompare(b.status);
            default:
                return 0;
        }
    });

    renderLogs();
}

function renderLogs() {
    const tbody = document.getElementById('logTableBody');
    const cardList = document.getElementById('logCards');

    if (filteredLogs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="no-logs">No logs found</td></tr>';
        if (cardList) cardList.innerHTML = '<div class="log-card"><div class="card-body"><div class="card-row"><span class="label">Info</span><span class="value">No logs found</span></div></div></div>';
        return;
    }

    const rowsHtml = filteredLogs.map(log => {
        const statusClass = log.status === 'SUCCESS' ? 'status-success' : 'status-failure';

        // Parse ISO timestamp properly
        let timestamp = log.timestamp;
        try {
            const date = new Date(log.timestamp);
            if (!isNaN(date.getTime())) {
                timestamp = date.toLocaleString();
            }
        } catch (e) {
            // Keep original timestamp if parsing fails
        }

        return `
            <tr>
                <td data-label="Timestamp">${escapeHtml(timestamp)}</td>
                <td data-label="IP Address">${escapeHtml(log.ip)}</td>
                <td data-label="User">${escapeHtml(log.user || 'UNKNOWN')}</td>
                <td class="${statusClass}" data-label="Status">${escapeHtml(log.status)}</td>
                <td data-label="Details">${escapeHtml(log.details || '-')}</td>
            </tr>
        `;
    }).join('');
    tbody.innerHTML = rowsHtml;

    if (cardList) {
        const cardsHtml = filteredLogs.map(log => {
            const statusClass = log.status === 'SUCCESS' ? 'status-success' : 'status-failure';
            let timestamp = log.timestamp;
            try {
                const date = new Date(log.timestamp);
                if (!isNaN(date.getTime())) {
                    timestamp = date.toLocaleString();
                }
            } catch (e) {}
            return `
                <div class="log-card">
                    <div class="card-body">
                        <div class="card-row"><span class="label">IP Address</span><span class="value">${escapeHtml(log.ip)}</span></div>
                        <div class="card-row"><span class="label">User</span><span class="value">${escapeHtml(log.user || 'UNKNOWN')}</span></div>
                        <div class="card-row"><span class="label">Details</span><span class="value">${escapeHtml(log.details || '-')}</span></div>
                    </div>
                </div>
            `;
        }).join('');
        cardList.innerHTML = cardsHtml;
    }
}

function toggleCompact(ev) {
    const enabled = !!ev.target.checked;
    const dash = document.getElementById('dashboard');
    dash.classList.toggle('compact', enabled);
    try { localStorage.setItem('admin_compact', enabled ? '1' : '0'); } catch (e) {}
}

function applyCompactFromStorage() {
    let enabled = false;
    try { enabled = localStorage.getItem('admin_compact') === '1'; } catch (e) {}
    const checkbox = document.getElementById('compactToggle');
    if (checkbox) checkbox.checked = enabled;
    const dash = document.getElementById('dashboard');
    dash.classList.toggle('compact', enabled);
}

// Tabs logic
function switchTab(tab) {
    const logsTab = document.getElementById('logsTab');
    const usersTab = document.getElementById('usersTab');
    const btnLogs = document.getElementById('tabLogsBtn');
    const btnUsers = document.getElementById('tabUsersBtn');
    if (tab === 'users') {
        logsTab.style.display = 'none';
        usersTab.style.display = 'block';
        btnUsers.classList.add('active');
        btnLogs.classList.remove('active');
        loadUsers();
    } else {
        usersTab.style.display = 'none';
        logsTab.style.display = 'block';
        btnLogs.classList.add('active');
        btnUsers.classList.remove('active');
        loadLogs();
    }
}

// Button busy helper
function setButtonBusy(id, busyLabel) {
    if (!id) return null;
    const el = document.getElementById(id);
    if (!el) return null;
    const orig = el.dataset.origLabel || el.textContent;
    el.dataset.origLabel = orig;
    el.disabled = true;
    el.style.opacity = '0.7';
    el.textContent = busyLabel || 'Working…';
    return function restore() {
        el.disabled = false;
        el.style.opacity = '';
        el.textContent = el.dataset.origLabel || orig;
    };
}

// Toast helper
function showToast(message, type = 'success', persistent = false) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.textContent = message;
    container.appendChild(t);
    setTimeout(() => t.classList.add('show'), 10);
    if (!persistent) {
        setTimeout(() => {
            t.classList.remove('show');
            setTimeout(() => t.remove(), 500);
        }, 3500);
    }
}

// --- Bind all event listeners (replaces inline handlers) ---
// Helper: bind only if element exists (dashboard elements may not be rendered)
function _bind(id, event, fn) {
    const el = document.getElementById(id);
    if (el) el.addEventListener(event, fn);
}
_bind('loginBtn', 'click', login);
_bind('logoutBtn', 'click', logout);
_bind('tabLogsBtn', 'click', () => switchTab('logs'));
_bind('tabUsersBtn', 'click', () => switchTab('users'));
_bind('sortBy', 'change', sortLogs);
_bind('filterUser', 'change', filterLogs);
_bind('filterStatus', 'change', filterLogs);
_bind('compactToggle', 'change', (e) => toggleCompact(e));
_bind('refreshLogsBtn', 'click', loadLogs);
_bind('btnClearAll', 'click', () => openClearAll('btnClearAll'));
_bind('addUserBtn', 'click', openAddUser);

// Event delegation for dynamically rendered user-action buttons
(function() {
    const tbody = document.getElementById('usersTableBody');
    if (tbody) {
        tbody.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action]');
            if (!btn) return;
            const action = btn.dataset.action;
            const user = btn.dataset.user;
            if (action === 'edit') openEditUser(user);
            else if (action === 'toggle') toggleActive(user, btn.dataset.active === 'true');
            else if (action === 'delete') deleteUser(user);
        });
    }
})();
