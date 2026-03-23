/* ================================================================
   Crassus 2.5 — Dashboard Application
   ================================================================ */

(function () {
    'use strict';

    // ------------------------------------------------------------------
    // Template JSON payloads for TradingView
    // ------------------------------------------------------------------
    const templateMap = {
        stockBuy: `{
  "ticker": "{{ticker}}",
  "side": "buy",
  "strategy": "bollinger_mean_reversion",
  "mode": "stock",
  "price": "{{close}}",
  "volume": "{{volume}}",
  "time": "{{timenow}}"
}`,
        stockSell: `{
  "ticker": "{{ticker}}",
  "side": "sell",
  "strategy": "bollinger_mean_reversion",
  "mode": "stock",
  "price": "{{close}}",
  "volume": "{{volume}}",
  "time": "{{timenow}}"
}`,
        optionsBuy: `{
  "ticker": "{{ticker}}",
  "side": "buy",
  "strategy": "lorentzian_classification",
  "mode": "options",
  "price": "{{close}}",
  "volume": "{{volume}}",
  "time": "{{timenow}}"
}`,
        optionsSell: `{
  "ticker": "{{ticker}}",
  "side": "sell",
  "strategy": "lorentzian_classification",
  "mode": "options",
  "price": "{{close}}",
  "volume": "{{volume}}",
  "time": "{{timenow}}"
}`
    };

    let configData = {};
    let _pendingSave = false;

    // ------------------------------------------------------------------
    // Utility: HTML-safe escaping (prevents XSS)
    // ------------------------------------------------------------------
    function esc(value) {
        const str = String(value ?? '');
        const div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    // ------------------------------------------------------------------
    // Formatting helpers (all return escaped strings)
    // ------------------------------------------------------------------
    function fmt(value) {
        if (value === null || value === undefined || value === '') return '-';
        const n = Number(value);
        if (Number.isNaN(n)) return esc(String(value));
        return esc(n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    }

    function fmtDollar(value) {
        if (value === null || value === undefined || value === '') return '-';
        return '$' + fmt(value);
    }

    function fmtPct(value) {
        if (value === null || value === undefined || value === '') return '-';
        return fmt(value) + '%';
    }

    function plClass(value) {
        if (Number(value) > 0) return 'positive';
        if (Number(value) < 0) return 'negative';
        return '';
    }

    function formatDate(value) {
        if (!value) return '-';
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return esc(String(value));
        return esc(d.toLocaleString());
    }

    // ------------------------------------------------------------------
    // Toast notifications
    // ------------------------------------------------------------------
    function showToast(message, type) {
        const toast = document.getElementById('toast');
        toast.textContent = message;
        toast.className = 'toast ' + (type === 'error' ? 'toast-error' : 'toast-success');
        toast.classList.add('show');
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => toast.classList.remove('show'), 3500);
    }

    // ------------------------------------------------------------------
    // Confirm dialog
    // ------------------------------------------------------------------
    function confirmAction(title, message) {
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay';
            overlay.innerHTML = `
                <div class="modal-box">
                    <h3>${esc(title)}</h3>
                    <p>${esc(message)}</p>
                    <div class="modal-actions">
                        <button class="btn btn-secondary" data-action="cancel">Cancel</button>
                        <button class="btn btn-danger" data-action="confirm">Confirm</button>
                    </div>
                </div>`;
            document.body.appendChild(overlay);
            overlay.querySelector('[data-action="cancel"]').addEventListener('click', () => {
                overlay.remove();
                resolve(false);
            });
            overlay.querySelector('[data-action="confirm"]').addEventListener('click', () => {
                overlay.remove();
                resolve(true);
            });
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) { overlay.remove(); resolve(false); }
            });
        });
    }

    // ------------------------------------------------------------------
    // Status helpers
    // ------------------------------------------------------------------
    function statusClass(status) {
        const s = String(status || '').toLowerCase();
        if (['filled', 'ok', 'forwarded'].includes(s)) return 'status-filled';
        if (['new', 'accepted', 'pending_new', 'partially_filled', 'stored_only'].includes(s)) return 'status-new';
        if (['canceled', 'expired', 'replaced'].includes(s)) return 'status-canceled';
        if (['rejected', 'error', 'forward_error', 'parse_error'].includes(s)) return 'status-error-inline';
        return 'status-canceled';
    }

    function pillClass(ok, warn) {
        if (ok === true) return 'status-success';
        if (warn) return 'status-warning';
        return 'status-error';
    }

    // ------------------------------------------------------------------
    // Tab navigation
    // ------------------------------------------------------------------
    function initTabs() {
        const tabs = document.querySelectorAll('.tab-btn');
        const panels = document.querySelectorAll('.tab-panel');

        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const target = tab.dataset.tab;
                tabs.forEach(t => t.classList.remove('active'));
                panels.forEach(p => p.classList.remove('active'));
                tab.classList.add('active');
                document.getElementById('panel-' + target).classList.add('active');
            });
        });
    }

    // ------------------------------------------------------------------
    // Clipboard
    // ------------------------------------------------------------------
    function copyToClipboard(elementId) {
        const node = document.getElementById(elementId);
        const text = node.textContent || node.innerText || '';
        navigator.clipboard.writeText(text).then(
            () => showToast('Copied to clipboard', 'success'),
            () => showToast('Clipboard copy failed', 'error')
        );
    }

    // ------------------------------------------------------------------
    // Paper mode toggle
    // ------------------------------------------------------------------
    function togglePaperMode() {
        const toggle = document.getElementById('setupPaperToggle');
        toggle.classList.toggle('active');
        document.getElementById('setupPaperLabel').textContent =
            toggle.classList.contains('active') ? 'ON' : 'OFF';
    }

    // ------------------------------------------------------------------
    // Template tab switcher
    // ------------------------------------------------------------------
    function setTemplate(key, tab) {
        document.querySelectorAll('#templateTabs .template-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('webhookTemplate').textContent = templateMap[key];
    }

    // ------------------------------------------------------------------
    // API: Webhook Info
    // ------------------------------------------------------------------
    function loadWebhookInfo() {
        fetch('/api/webhook/info')
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'ok') throw new Error(data.message || 'Could not load webhook info');
                document.getElementById('webhookUrl').textContent = data.local_url;
                document.getElementById('webhookFullUrl').textContent = data.full_url;
                document.getElementById('webhookToken').textContent = data.auth_token;

                const target = document.getElementById('webhookForwardTarget');
                const pill = document.getElementById('webhookForwardStatus');

                if (data.forward_target === 'none') {
                    target.textContent = 'Store only — dashboard fallback mode';
                    pill.textContent = 'Store Only';
                    pill.className = 'status-pill status-warning';
                } else {
                    target.textContent = data.forward_target.toUpperCase() + ' \u2192 ' + data.forward_url;
                    pill.textContent = 'Forwarding: ' + data.forward_target;
                    pill.className = 'status-pill status-success';
                }
            })
            .catch(err => {
                const pill = document.getElementById('webhookForwardStatus');
                pill.textContent = 'Unavailable';
                pill.className = 'status-pill status-error';
                document.getElementById('webhookForwardTarget').textContent = err.message;
            });
    }

    // ------------------------------------------------------------------
    // API: Generate Token
    // ------------------------------------------------------------------
    function generateToken() {
        fetch('/api/webhook/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') throw new Error(data.message || 'Could not generate token');
            showToast('New webhook token generated', 'success');
            loadWebhookInfo();
        })
        .catch(err => showToast(err.message, 'error'));
    }

    // ------------------------------------------------------------------
    // API: Webhook Activity
    // ------------------------------------------------------------------
    function renderLatestSnapshot(event) {
        const empty = document.getElementById('latestSnapshotEmpty');
        const content = document.getElementById('latestSnapshotContent');

        if (!event) {
            empty.classList.remove('hidden');
            content.classList.add('hidden');
            return;
        }

        empty.classList.add('hidden');
        content.classList.remove('hidden');

        const parsed = event.parsed || {};
        const forward = event.forward || {};

        const cards = [
            ['Ticker', parsed.ticker || '-'],
            ['Side', parsed.side || '-'],
            ['Mode', parsed.mode || '-'],
            ['Strategy', parsed.strategy || '-'],
            ['Price', parsed.price !== undefined ? fmtDollar(parsed.price) : '-'],
            ['Volume', parsed.volume != null ? fmt(parsed.volume) : '-'],
            ['Alert Time', parsed.time || '-'],
            ['Received', formatDate(event.received_at)],
        ];

        document.getElementById('latestSummary').innerHTML = cards.map(([label, value]) => `
            <div class="signal-item">
                <div class="label">${esc(label)}</div>
                <div class="value">${esc(value)}</div>
            </div>
        `).join('');

        const parseOk = !event.parse_error;
        const parsePill = document.getElementById('latestParseStatus');
        parsePill.textContent = parseOk ? 'Parsed' : 'Parse Error';
        parsePill.className = 'status-pill ' + (parseOk ? 'status-success' : 'status-error');

        let forwardText = 'Stored';
        let forwardOk = forward.ok === true;
        let forwardWarn = false;
        if ((forward.target || '') === 'none') {
            forwardText = 'Stored Only';
            forwardWarn = true;
        } else if (forward.status_code) {
            forwardText = 'Forward ' + forward.status_code;
        } else if (forward.error) {
            forwardText = 'Forward Error';
        }

        const forwardPill = document.getElementById('latestForwardPill');
        forwardPill.textContent = forwardText;
        forwardPill.className = 'status-pill ' + pillClass(forwardOk, forwardWarn);

        document.getElementById('latestPayload').textContent = JSON.stringify(event.payload || {}, null, 2);
        document.getElementById('latestForward').textContent = JSON.stringify(forward || {}, null, 2);
    }

    function renderActiveWebhooks(items) {
        const container = document.getElementById('activeWebhooks');
        document.getElementById('activeCount').textContent = String(items.length || 0);

        if (!items.length) {
            container.innerHTML = '<div class="empty-state">No active webhooks yet.</div>';
            return;
        }

        container.innerHTML = items.map(item => `
            <div class="sidebar-item">
                <div class="top">
                    <div class="title">${esc(item.ticker)} ${esc(String(item.side || '').toUpperCase())}</div>
                    <span class="count-pill">${esc(item.count)} alerts</span>
                </div>
                <div class="sidebar-meta">
                    <span>${esc(item.strategy)}</span>
                    <span>${esc(item.mode)}</span>
                    <span>Last ${esc(formatDate(item.last_seen))}</span>
                    <span>Status ${esc(item.last_status)}</span>
                </div>
            </div>
        `).join('');
    }

    function renderRecentAlerts(events) {
        const container = document.getElementById('recentAlerts');
        document.getElementById('recentCount').textContent = String(events.length || 0);

        if (!events.length) {
            container.innerHTML = '<div class="empty-state">Recent alerts will appear here.</div>';
            return;
        }

        let html = '<div class="table-wrap"><table><thead><tr>' +
            '<th>Received</th><th>Ticker</th><th>Side</th><th>Mode</th><th>Strategy</th><th>Forward</th>' +
            '</tr></thead><tbody>';

        for (const event of events) {
            const p = event.parsed || {};
            const fw = event.forward || {};
            const fwLabel = fw.status_code
                ? String(fw.status_code)
                : (fw.target === 'none' ? 'stored' : (fw.error ? 'error' : 'stored'));

            html += `<tr>
                <td>${esc(formatDate(event.received_at))}</td>
                <td><strong>${esc(p.ticker || '-')}</strong></td>
                <td>${esc((p.side || '-').toUpperCase())}</td>
                <td>${esc(p.mode || '-')}</td>
                <td>${esc(p.strategy || event.parse_error || '-')}</td>
                <td><span class="status ${statusClass(fw.ok ? 'ok' : (event.parse_error ? 'error' : fwLabel))}">${esc(fwLabel)}</span></td>
            </tr>`;
        }

        html += '</tbody></table></div>';
        container.innerHTML = html;
    }

    function loadWebhookActivity() {
        fetch('/api/webhook/activity')
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'ok') throw new Error(data.message || 'Could not load webhook activity');
                renderLatestSnapshot(data.latest_event);
                renderActiveWebhooks(data.active_webhooks || []);
                renderRecentAlerts(data.recent_events || []);
            })
            .catch(err => {
                document.getElementById('activeWebhooks').innerHTML =
                    '<div class="empty-state">' + esc(err.message) + '</div>';
            });
    }

    // ------------------------------------------------------------------
    // API: Test Webhook
    // ------------------------------------------------------------------
    function testWebhook() {
        const btn = document.getElementById('webhookTestBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Sending...';

        fetch('/api/webhook/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') throw new Error(data.message || 'Test webhook failed');
            const result = data.forward || data.response_body || {};
            if (data.response_code && data.response_code < 400) {
                showToast('Test webhook sent to the shared function endpoint', 'success');
            } else if (result.ok) {
                showToast('Test webhook stored and forwarded', 'success');
            } else if (result.target === 'none') {
                showToast('Test webhook stored (dashboard fallback mode)', 'success');
            } else {
                showToast('Test webhook stored. Forwarding needs attention.', 'error');
            }
            loadWebhookActivity();
        })
        .catch(err => showToast(err.message, 'error'))
        .finally(() => {
            btn.disabled = false;
            btn.textContent = 'Send Test Alert';
        });
    }

    // ------------------------------------------------------------------
    // API: Clear Webhooks (with confirmation)
    // ------------------------------------------------------------------
    async function clearWebhooks() {
        const confirmed = await confirmAction(
            'Clear Webhook Snapshots',
            'This will permanently delete all stored webhook snapshots. This cannot be undone.'
        );
        if (!confirmed) return;

        fetch('/api/webhook/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') throw new Error(data.message || 'Could not clear snapshots');
            showToast('Webhook snapshots cleared', 'success');
            loadWebhookActivity();
        })
        .catch(err => showToast(err.message, 'error'));
    }

    // ------------------------------------------------------------------
    // API: Broker Status
    // ------------------------------------------------------------------
    function loadBrokerStatus() {
        fetch('/api/credentials/check')
            .then(r => r.json())
            .then(data => {
                const statusBox = document.getElementById('brokerStatus');
                const badge = document.getElementById('tradingBadge');

                if (data.status === 'ok') {
                    statusBox.innerHTML = '<span class="status-pill status-success">Connected</span> Alpaca account ' + esc(data.account_id);
                    badge.textContent = data.paper ? 'Paper Trading' : 'Live Trading';
                    badge.className = 'badge ' + (data.paper ? 'badge-paper' : 'badge-live');
                } else if (data.status === 'missing') {
                    statusBox.textContent = 'No Alpaca credentials configured. Webhook monitoring is still active.';
                    badge.textContent = 'Webhook Ready';
                    badge.className = 'badge badge-neutral';
                } else {
                    statusBox.textContent = data.message || 'Broker credentials present but not verified.';
                    badge.textContent = 'Broker Attention';
                    badge.className = 'badge badge-neutral';
                }
            })
            .catch(() => {
                document.getElementById('brokerStatus').textContent = 'Could not check broker status.';
            });
    }

    // ------------------------------------------------------------------
    // API: Save Credentials
    // ------------------------------------------------------------------
    function submitCredentials() {
        const btn = document.getElementById('setupConnectBtn');
        const apiKey = document.getElementById('setupApiKey').value.trim();
        const secretKey = document.getElementById('setupSecretKey').value.trim();
        const paper = document.getElementById('setupPaperToggle').classList.contains('active');

        if (!apiKey || !secretKey) {
            showToast('Both API key and secret key are required.', 'error');
            return;
        }

        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Verifying...';

        fetch('/api/credentials/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey, secret_key: secretKey, paper }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') throw new Error(data.message || 'Could not save credentials');
            showToast('Broker credentials saved and verified', 'success');
            loadBrokerStatus();
            loadPortfolio();
            loadPositions();
            loadOrders();
        })
        .catch(err => showToast(err.message, 'error'))
        .finally(() => {
            btn.disabled = false;
            btn.textContent = 'Save & Verify';
        });
    }

    // ------------------------------------------------------------------
    // API: Portfolio
    // ------------------------------------------------------------------
    function loadPortfolio() {
        fetch('/api/portfolio')
            .then(r => r.json())
            .then(data => {
                const grid = document.getElementById('portfolioGrid');
                if (data.status !== 'ok') {
                    grid.innerHTML = '<div class="empty-state">' + esc(data.message || 'Broker snapshot unavailable.') + '</div>';
                    return;
                }
                const p = data.portfolio;
                grid.innerHTML = `
                    <div class="stat-box"><div class="label">Equity</div><div class="value">${fmtDollar(p.equity)}</div></div>
                    <div class="stat-box"><div class="label">Buying Power</div><div class="value">${fmtDollar(p.buying_power)}</div></div>
                    <div class="stat-box"><div class="label">Cash</div><div class="value">${fmtDollar(p.cash)}</div></div>
                    <div class="stat-box"><div class="label">Portfolio Value</div><div class="value">${fmtDollar(p.portfolio_value)}</div></div>
                    <div class="stat-box"><div class="label">Daily P&amp;L</div><div class="value ${plClass(p.profit_loss)}">${fmtDollar(p.profit_loss)}</div></div>
                    <div class="stat-box"><div class="label">Daily P&amp;L %</div><div class="value ${plClass(p.profit_loss_pct)}">${fmtPct(p.profit_loss_pct)}</div></div>
                `;
            })
            .catch(() => {
                document.getElementById('portfolioGrid').innerHTML = '<div class="empty-state">Failed to load broker snapshot.</div>';
            });
    }

    // ------------------------------------------------------------------
    // API: Positions
    // ------------------------------------------------------------------
    function loadPositions() {
        fetch('/api/positions')
            .then(r => r.json())
            .then(data => {
                const el = document.getElementById('positionsTable');
                if (data.status !== 'ok') {
                    el.innerHTML = '<div class="empty-state">' + esc(data.message || 'No positions available.') + '</div>';
                    return;
                }
                if (!data.positions.length) {
                    el.innerHTML = '<div class="empty-state">No open positions.</div>';
                    return;
                }
                let html = '<div class="table-wrap"><table><thead><tr>' +
                    '<th>Symbol</th><th>Qty</th><th>Avg Entry</th><th>Current</th><th>Market Value</th><th>Unrealized P&amp;L</th>' +
                    '</tr></thead><tbody>';
                for (const pos of data.positions) {
                    html += `<tr>
                        <td><strong>${esc(pos.symbol)}</strong></td>
                        <td>${esc(pos.qty)}</td>
                        <td>${fmtDollar(pos.avg_entry)}</td>
                        <td>${fmtDollar(pos.current_price)}</td>
                        <td>${fmtDollar(pos.market_value)}</td>
                        <td class="${plClass(pos.unrealized_pl)}">${fmtDollar(pos.unrealized_pl)} (${fmtPct(pos.unrealized_pl_pct)})</td>
                    </tr>`;
                }
                html += '</tbody></table></div>';
                el.innerHTML = html;
            })
            .catch(() => {
                document.getElementById('positionsTable').innerHTML = '<div class="empty-state">Failed to load positions.</div>';
            });
    }

    // ------------------------------------------------------------------
    // API: Orders
    // ------------------------------------------------------------------
    function loadOrders() {
        fetch('/api/orders')
            .then(r => r.json())
            .then(data => {
                const el = document.getElementById('ordersTable');
                if (data.status !== 'ok') {
                    el.innerHTML = '<div class="empty-state">' + esc(data.message || 'No orders available.') + '</div>';
                    return;
                }
                if (!data.orders.length) {
                    el.innerHTML = '<div class="empty-state">No recent orders.</div>';
                    return;
                }
                let html = '<div class="table-wrap"><table><thead><tr>' +
                    '<th>Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Status</th><th>Fill</th><th>Time</th>' +
                    '</tr></thead><tbody>';
                for (const o of data.orders) {
                    html += `<tr>
                        <td><strong>${esc(o.symbol)}</strong></td>
                        <td>${esc(String(o.side || '').toUpperCase())}</td>
                        <td>${esc(o.type || '-')}</td>
                        <td>${esc(o.qty || '-')}</td>
                        <td><span class="status ${statusClass(o.status)}">${esc(o.status || '-')}</span></td>
                        <td>${o.filled_price != null ? fmtDollar(o.filled_price) : '-'}</td>
                        <td>${esc(o.submitted_at || '-')}</td>
                    </tr>`;
                }
                html += '</tbody></table></div>';
                el.innerHTML = html;
            })
            .catch(() => {
                document.getElementById('ordersTable').innerHTML = '<div class="empty-state">Failed to load recent orders.</div>';
            });
    }

    // ------------------------------------------------------------------
    // API: Config
    // ------------------------------------------------------------------
    function loadConfig() {
        fetch('/api/config')
            .then(r => r.json())
            .then(data => {
                if (data.status !== 'ok') throw new Error(data.message || 'Could not load config');
                configData = data.config;
                renderConfig(configData);
            })
            .catch(err => {
                document.getElementById('configEditor').innerHTML = '<div class="empty-state">' + esc(err.message) + '</div>';
            });
    }

    function renderConfig(config) {
        const groups = {};
        for (const [key, meta] of Object.entries(config)) {
            const group = meta.group;
            if (!groups[group]) groups[group] = [];
            groups[group].push({ key, ...meta });
        }

        let html = '';
        for (const [groupName, params] of Object.entries(groups)) {
            const groupId = groupName.replace(/[^a-zA-Z0-9]/g, '_');
            html += `<div class="config-group" data-group="${esc(groupName)}">
                <div class="config-group-header" onclick="window._toggleConfigGroup(this)">
                    <h3>${esc(groupName)}</h3>
                    <span class="chevron">\u25BC</span>
                </div>
                <div class="config-group-body" id="group-${esc(groupId)}">`;

            for (const param of params) {
                if (param.type === 'bool') {
                    const active = String(param.value || '').toLowerCase() === 'true';
                    html += `<div class="config-row" data-config-label="${esc(param.label.toLowerCase())} ${esc(param.description.toLowerCase())}">
                        <div class="config-label">
                            ${esc(param.label)}
                            <div class="desc">${esc(param.description)}</div>
                        </div>
                        <div class="toggle-wrap">
                            <span>${active ? 'ON' : 'OFF'}</span>
                            <div class="toggle ${active ? 'active' : ''}" data-key="${esc(param.key)}"
                                 role="switch" aria-checked="${active}" aria-label="${esc(param.label)}"
                                 tabindex="0" onclick="window._toggleConfigBool(this)"
                                 onkeydown="if(event.key===' '||event.key==='Enter'){event.preventDefault();window._toggleConfigBool(this)}"></div>
                        </div>
                    </div>`;
                } else {
                    html += `<div class="config-row" data-config-label="${esc(param.label.toLowerCase())} ${esc(param.description.toLowerCase())}">
                        <div class="config-label">
                            ${esc(param.label)}
                            <div class="desc">${esc(param.description)}</div>
                        </div>
                        <input class="input config-input" type="text" data-key="${esc(param.key)}"
                               value="${esc(param.value)}" placeholder="${esc(param.default)}"
                               aria-label="${esc(param.label)}">
                    </div>`;
                }
            }
            html += '</div></div>';
        }

        document.getElementById('configEditor').innerHTML = html;
    }

    // Exposed to onclick handlers
    window._toggleConfigBool = function (el) {
        el.classList.toggle('active');
        const isActive = el.classList.contains('active');
        el.setAttribute('aria-checked', isActive);
        el.previousElementSibling.textContent = isActive ? 'ON' : 'OFF';
    };

    window._toggleConfigGroup = function (header) {
        header.classList.toggle('collapsed');
        const body = header.nextElementSibling;
        body.classList.toggle('collapsed');
    };

    function filterConfig(query) {
        const q = query.toLowerCase().trim();
        document.querySelectorAll('.config-row').forEach(row => {
            const label = row.dataset.configLabel || '';
            row.style.display = (!q || label.includes(q)) ? '' : 'none';
        });
        document.querySelectorAll('.config-group').forEach(group => {
            const visibleRows = group.querySelectorAll('.config-row:not([style*="display: none"])');
            group.style.display = (!q || visibleRows.length > 0) ? '' : 'none';
        });
    }

    function saveConfig() {
        if (_pendingSave) return;
        _pendingSave = true;

        const updates = {};
        document.querySelectorAll('.config-input').forEach(input => {
            updates[input.dataset.key] = input.value;
        });
        document.querySelectorAll('.toggle[data-key]').forEach(toggle => {
            updates[toggle.dataset.key] = toggle.classList.contains('active') ? 'true' : 'false';
        });

        const btn = document.getElementById('saveConfigBtn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Saving...';

        fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates),
        })
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') throw new Error(data.message || 'Could not save settings');
            if (data.azure_error) {
                showToast('Saved locally. Azure sync needs attention.', 'error');
            } else {
                showToast('Settings saved', 'success');
            }
            loadWebhookInfo();
            loadWebhookActivity();
            loadBrokerStatus();
        })
        .catch(err => showToast(err.message, 'error'))
        .finally(() => {
            _pendingSave = false;
            btn.disabled = false;
            btn.textContent = 'Save Settings';
        });
    }

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------
    function init() {
        initTabs();

        document.getElementById('webhookTemplate').textContent = templateMap.stockBuy;

        // Wire up event handlers
        document.getElementById('setupSecretKey').addEventListener('keydown', e => {
            if (e.key === 'Enter') submitCredentials();
        });

        const searchInput = document.getElementById('configSearch');
        if (searchInput) {
            searchInput.addEventListener('input', () => filterConfig(searchInput.value));
        }

        // Load all data
        loadWebhookInfo();
        loadWebhookActivity();
        loadBrokerStatus();
        loadPortfolio();
        loadPositions();
        loadOrders();
        loadConfig();

        // Polling intervals
        setInterval(loadWebhookActivity, 5000);
        setInterval(() => {
            loadPortfolio();
            loadPositions();
            loadOrders();
        }, 30000);
    }

    // Expose functions needed by onclick handlers in HTML
    window.copyToClipboard = copyToClipboard;
    window.togglePaperMode = togglePaperMode;
    window.setTemplate = setTemplate;
    window.generateToken = generateToken;
    window.testWebhook = testWebhook;
    window.clearWebhooks = clearWebhooks;
    window.submitCredentials = submitCredentials;
    window.saveConfig = saveConfig;

    // Boot
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
