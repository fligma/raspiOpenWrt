"""HTML page templates for the dashboard. Each function returns a string so the
caller (index.py) stays responsible for HTTP headers and stdout."""


def login_html():
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Rasconf - PIN Required</title>
    <link rel="icon" href="/assets/favicon.ico" sizes="any">
    <link rel="apple-touch-icon" sizes="180x180" href="/assets/apple-touch-icon.png">
    <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16x16.png">
    <link rel="manifest" href="/assets/site.webmanifest">
    <link rel="stylesheet" href="/css/main.css">
    <link rel="stylesheet" href="/css/index.css">
</head>
<body class="login-page">
    <div class="login-card">
        <h2>Raspberry Pi Access</h2>
        <form method="POST" action="">
            <input type="password" name="pin" placeholder="Enter Security PIN" required autofocus>
            <button class="btn" type="submit">Unlock Dashboard</button>
        </form>
    </div>
</body>
</html>"""


def dashboard_html(config_json):
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Rasconf - Dashboard</title>
    <link rel="icon" href="/assets/favicon.ico" sizes="any">
    <link rel="apple-touch-icon" sizes="180x180" href="/assets/apple-touch-icon.png">
    <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16x16.png">
    <link rel="manifest" href="/assets/site.webmanifest">
    <link rel="stylesheet" href="/css/main.css">
    <link rel="stylesheet" href="/css/index.css">
    <script src="/js/main.js" defer></script>
    <script type="module" src="/js/index.js"></script>
</head>
<body class="dashboard-page">
    <div class="container">
        <div class="header">
            <h1>Pi Status</h1>
            <div class="header-actions">
                <a href="?action=api&type=all" target="_blank" class="btn api-btn">JSON API</a>
                <div class="refresh-control">
                    <span id="live_indicator" class="indicator">\u25cf Live</span>
                    <span id="refresh_countdown" class="refresh-countdown">--</span>
                    <button id="refresh_toggle" class="btn-mini" type="button" aria-pressed="false">Pause</button>
                </div>
                <a href="?action=logout" class="logout-btn">Logout</a>
            </div>
        </div>
        <div class="tabs">
            <div class="tab active" data-tab-target="dashboard" role="tab" aria-selected="true">Dashboard</div>
            <div class="tab" data-tab-target="interfaces" role="tab" aria-selected="false">Interfaces</div>
            <div class="tab" data-tab-target="wireless" role="tab" aria-selected="false">Wireless</div>
            <div class="tab" data-tab-target="devices" role="tab" aria-selected="false">Devices</div>
            <div class="tab" data-tab-target="controls" role="tab" aria-selected="false">System Controls</div>
            <div class="tab" data-tab-target="settings" role="tab" aria-selected="false">Settings</div>
        </div>

        <div class="content">
            <!-- Dashboard -->
            <div id="dashboard" class="panel active">
                <div class="grid-2">
                    <div class="card">
                        <h3>CPU Temperature</h3>
                        <div class="metric-value"><span id="val_temp">--</span> \u00b0C</div>
                    </div>
                    <div class="card">
                        <h3>System Load &amp; Memory</h3>
                        <div class="metric-section">
                            <div class="metric-label">LOAD AVERAGE (1m 5m 15m)</div>
                            <div id="val_load" class="metric-detail">--</div>
                            <div class="metric-label">MEMORY USAGE</div>
                            <div id="val_ram" class="metric-detail">--</div>
                            <div class="bar"><span id="bar_ram" class="bar-fill"></span></div>
                        </div>
                    </div>
                    <div class="card">
                        <h3>Host &amp; Uptime</h3>
                        <div class="stat-grid">
                            <div><div class="metric-label">HOSTNAME</div><div id="val_hostname" class="stat-value">--</div></div>
                            <div><div class="metric-label">UPTIME</div><div id="val_uptime" class="stat-value">--</div></div>
                            <div><div class="metric-label">CPU CORES</div><div id="val_cores" class="stat-value">--</div></div>
                        </div>
                    </div>
                    <div class="card">
                        <h3>Storage (root)</h3>
                        <div class="metric-section">
                            <div id="val_storage" class="metric-detail">--</div>
                            <div class="bar"><span id="bar_storage" class="bar-fill"></span></div>
                        </div>
                    </div>
                    <div class="card card-wide">
                        <h3>CPU Usage <span id="val_cpu_overall" class="cpu-overall">--%</span></h3>
                        <div id="cpu_cores" class="core-grid"></div>
                    </div>
                </div>
            </div>

            <!-- Interfaces -->
            <div id="interfaces" class="panel">
                <div class="card">
                    <h3>Network Interfaces</h3>
                    <div id="net_list">Loading interface data...</div>
                </div>
                <div class="card">
                    <h3>Network Traffic</h3>
                    <div class="device-table-wrap">
                        <table>
                            <thead>
                                <tr><th>Interface</th><th>Total RX</th><th>RX Rate</th><th>Total TX</th><th>TX Rate</th></tr>
                            </thead>
                            <tbody id="traffic_list">
                                <tr><td colspan="5" class="empty-row">Loading traffic data...</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <div id="traffic_totals" class="muted-message" style="margin-top:10px;"></div>
                </div>
            </div>

            <!-- Wireless -->
            <div id="wireless" class="panel">
                <div class="card">
                    <h3>Wireless Status</h3>
                    <div id="wifi_list">Loading wireless data...</div>
                </div>
            </div>

            <!-- Devices -->
            <div id="devices" class="panel">
                <div class="card">
                    <h3>Connected Devices (DHCP / ARP)</h3>
                    <div class="device-table-wrap">
                        <table>
                            <thead>
                                <tr><th>Hostname</th><th>IP Address</th><th>MAC Address</th><th>Interface / Source</th><th>Action</th></tr>
                            </thead>
                            <tbody id="dev_list">
                                <tr><td colspan="5">Loading devices...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- System Controls -->
            <div id="controls" class="panel">
                <div class="grid-2">
                    <div class="card">
                        <h3>Service Controls</h3>
                        <p style="margin-bottom: 15px; font-size: 0.9em; color: #a0a0a0;">Restart core system services. Note that this may temporarily interrupt your connection.</p>
                        <div style="display: grid; gap: 10px; grid-template-columns: 1fr 1fr;">
                            <button class="btn cmd-btn" data-type="service" data-target="network">Restart Network</button>
                            <button class="btn cmd-btn" data-type="service" data-target="dnsmasq">Restart DNS/DHCP</button>
                            <button class="btn cmd-btn" data-type="service" data-target="firewall">Restart Firewall</button>
                            <button class="btn cmd-btn" style="background: #e74c3c;" data-type="service" data-target="system">Reboot System</button>
                        </div>
                    </div>
                    <div class="card">
                        <h3>Interface Controls</h3>
                        <p style="margin-bottom: 15px; font-size: 0.9em; color: #a0a0a0;">Bring up/restart specific network interfaces.</p>
                        <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                            <button class="btn cmd-btn" data-type="interface" data-target="lan">Restart LAN</button>
                            <button class="btn cmd-btn" data-type="interface" data-target="wan">Restart WAN</button>
                        </div>
                        <div class="form-group" style="margin-top: 15px; border-top: 1px solid #333; padding-top: 15px;">
                            <label>Restart Custom Interface</label>
                            <div style="display: flex; gap: 10px;">
                                <input type="text" id="custom_iface" placeholder="e.g. wg0">
                                <button class="btn" onclick="restartCustomIface()">Restart</button>
                            </div>
                        </div>
                        <div id="cmd_msg" class="save-message" style="margin-top:10px;"></div>
                    </div>
                </div>
            </div>

            <!-- Settings -->
            <div id="settings" class="panel">
                <div class="card">
                    <h3>Refresh Intervals (Seconds)</h3>
                    <div class="form-group">
                        <label>CPU Temperature Update Interval</label>
                        <input type="number" id="cfg_temp" min="1" step="1">
                    </div>
                    <div class="form-group">
                        <label>System Load &amp; RAM Update Interval</label>
                        <input type="number" id="cfg_sys" min="1" step="1">
                    </div>
                    <div class="form-group">
                        <label>Network Interfaces Update Interval</label>
                        <input type="number" id="cfg_net" min="1" step="1">
                    </div>
                    <div class="form-group">
                        <label>Wireless Update Interval</label>
                        <input type="number" id="cfg_wifi" min="1" step="1">
                    </div>
                    <div class="form-group">
                        <label>Connected Devices Update Interval</label>
                        <input type="number" id="cfg_dev" min="1" step="1">
                    </div>
                    <div class="form-group">
                        <label>Storage Update Interval</label>
                        <input type="number" id="cfg_storage" min="1" step="1">
                    </div>
                    <div class="form-group">
                        <label>Network Traffic Update Interval</label>
                        <input type="number" id="cfg_traffic" min="1" step="1">
                    </div>
                    <button class="btn" type="button" data-action="save-settings">Save Configuration</button>
                    <div id="save_msg" class="save-message">Settings saved successfully! Reloading...</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Command execution script specific to the controls tab -->
    <script>
        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('.cmd-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const type = e.target.dataset.type;
                    const target = e.target.dataset.target;
                    if(confirm(`Are you sure you want to restart ${target}?`)) {
                        executeCmd(type, target);
                    }
                });
            });
        });

        function restartCustomIface() {
            const iface = document.getElementById('custom_iface').value.trim();
            if(iface && confirm(`Are you sure you want to restart interface ${iface}?`)) {
                executeCmd('interface', iface);
            }
        }

        async function executeCmd(type, target) {
            const msgEl = document.getElementById('cmd_msg');
            msgEl.style.display = 'block';
            msgEl.textContent = 'Executing...';
            msgEl.style.color = 'var(--color-text)';

            const action = type === 'service' ? 'restart_service' : 'restart_interface';
            const payload = type === 'service' ? { service: target } : { interface: target };

            try {
                const res = await fetch(`?action=${action}`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    msgEl.textContent = data.message;
                    msgEl.style.color = '#2ecc71';
                } else {
                    msgEl.textContent = 'Error: ' + data.error;
                    msgEl.style.color = '#e74c3c';
                }
            } catch(e) {
                msgEl.textContent = 'Request failed.';
                msgEl.style.color = '#e74c3c';
            }
            setTimeout(() => { msgEl.style.display = 'none'; }, 5000);
        }
    </script>
    <script type="application/json" id="rasconf-config">__CONFIG_JSON__</script>
</body>
</html>""".replace("__CONFIG_JSON__", config_json)
