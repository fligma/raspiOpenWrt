#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import urllib.parse
import hashlib
import re
from http import cookies

current_file = os.path.abspath(__file__)
hashpath=f'{current_file.replace('index.py', '')}hash.file'
if os.path.exists(hashpath):
    with open(hashpath) as f:
        SECRET_HASH = f.readline()
else:
    with open(hashpath, "w") as file:
        file.write("9b8769a4a742959a2d0298c36fb70623f2dfacda8436237df08d8dfd5b37374c") # pass123 default
    with open(hashpath) as file:
        SECRET_HASH = f.readline()

CONFIG_FILE = "/config/index.conf"
DEFAULT_CONFIG = {
    "temp_interval": 2,
    "sys_interval": 3,
    "net_interval": 5,
    "wifi_interval": 5,
    "dev_interval": 5
}

def check_auth(params, cookie):
    """Verify session cookie or passwrd login attempt."""
    submitted_pin = params.get("pin", [""])[0]
    
    if submitted_pin:
        pin_hash = hashlib.sha256(submitted_pin.encode("utf-8")).hexdigest()
        if pin_hash == SECRET_HASH:
            return True, True
            
    if "session" in cookie and cookie["session"].value == SECRET_HASH:
        return True, False
        
    return False, False

def load_config():
    if not os.path.exists("/config"):
        try: os.makedirs("/config", exist_ok=True)
        except: pass

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                conf = json.loads(f.read())
                for k, v in DEFAULT_CONFIG.items():
                    if k not in conf: conf[k] = v
                return conf
        except: pass
    return DEFAULT_CONFIG

def save_config(new_conf):
    if not os.path.exists("/config"):
        try: os.makedirs("/config", exist_ok=True)
        except: pass

    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(json.dumps(new_conf, indent=2))
        return True
    except:
        return False

def cmd_json(cmd):
    try:
        with open(os.devnull, 'w') as devnull:
            res = subprocess.check_output(cmd, shell=True, stderr=devnull)
        return json.loads(res.decode('utf-8'))
    except Exception:
        return {}

def get_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(float(f.read().strip()) / 1000.0, 1)
    except:
        return "N/A"

def get_sys():
    load = "N/A"
    try:
        with open("/proc/loadavg", "r") as f:
            load = " ".join(f.read().strip().split()[0:3])
    except: pass

    mem = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = int(parts[1].replace("kB", "").strip())
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0) + mem.get("Buffers", 0) + mem.get("Cached", 0))
        used = total - avail
        percent = round((used / total) * 100, 1)
        ram = f"{round(used/1024, 1)} MB / {round(total/1024, 1)} MB ({percent}%)"
    except:
        ram = "N/A"
    return {"load": load, "ram": ram}

def get_net():
    data = cmd_json("ubus call network.interface dump")
    if "interface" in data:
        return data["interface"]
    return [{"error": "Failed to read via ubus"}]

def get_wireless():
    data = cmd_json("ubus call network.wireless status")
    if data:
        return data
    return {"error": "Failed to read wireless status via ubus"}

def get_wireless_clients():
    clients = {}
    try:
        with open(os.devnull, "w") as devnull:
            objects = subprocess.check_output(
                ["ubus", "list"], stderr=devnull, timeout=5
            ).decode("utf-8").splitlines()
            for object_name in objects:
                if not object_name.startswith("hostapd."):
                    continue
                response = subprocess.check_output(
                    ["ubus", "call", object_name, "get_clients"],
                    stderr=devnull,
                    timeout=5,
                )
                result = json.loads(response.decode("utf-8"))
                stations = result.get("clients", result)
                if isinstance(stations, dict):
                    for mac in stations:
                        normalized_mac = mac.upper()
                        if re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", normalized_mac):
                            clients[normalized_mac] = object_name
    except Exception:
        pass
    return clients

def disconnect_wireless_device(mac):
    mac = mac.upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
        return {"success": False, "error": "Invalid device MAC address"}

    clients = get_wireless_clients()
    access_point = clients.get(mac)
    if not access_point:
        return {"success": False, "error": "Device is not currently connected over Wi-Fi"}

    request = json.dumps({
        "addr": mac,
        "reason": 5,
        "deauth": True,
        "ban_time": 0
    })
    try:
        with open(os.devnull, "w") as devnull:
            subprocess.check_output(
                ["ubus", "call", access_point, "del_client", request],
                stderr=devnull,
                timeout=8,
            )
        return {"success": True, "mac": mac, "interface": access_point}
    except Exception as error:
        return {"success": False, "error": str(error)}

def get_devices():
    devices = {}
    try:
        if os.path.exists("/tmp/dhcp.leases"):
            with open("/tmp/dhcp.leases", "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        mac = parts[1].upper()
                        devices[mac] = {"ip": parts[2], "mac": mac, "hostname": parts[3], "source": "DHCP"}
    except: pass

    try:
        if os.path.exists("/proc/net/arp"):
            with open("/proc/net/arp", "r") as f:
                lines = f.readlines()[1:]
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 6:
                        ip = parts[0]
                        flags = parts[2]
                        mac = parts[3].upper()
                        if flags != "0x0" and mac != "00:00:00:00:00:00":
                            if mac not in devices:
                                devices[mac] = {"ip": ip, "mac": mac, "hostname": "Unknown", "source": "ARP"}
                            devices[mac]["interface"] = parts[5]
    except: pass

    wireless_clients = get_wireless_clients()
    for mac, access_point in wireless_clients.items():
        if mac not in devices:
            devices[mac] = {
                "ip": "N/A",
                "mac": mac,
                "hostname": "Unknown",
                "source": "Wi-Fi",
            }
        devices[mac]["wireless_interface"] = access_point

    return list(devices.values())

method = os.environ.get("REQUEST_METHOD", "GET")
query_string = os.environ.get("QUERY_STRING", "")
get_params = urllib.parse.parse_qs(query_string)

try:
    content_length = int(os.environ.get("CONTENT_LENGTH", 0))
except (ValueError, TypeError):
    content_length = 0

post_data = sys.stdin.read(content_length) if content_length > 0 else ""
post_params = urllib.parse.parse_qs(post_data)

cookie_header = os.environ.get("HTTP_COOKIE", "")
cookie = cookies.SimpleCookie(cookie_header)

action = get_params.get("action", [""])[0] or post_params.get("action", [""])[0]
is_auth, set_new_cookie = check_auth(post_params, cookie)

if action == "logout":
    sys.stdout.write("Set-Cookie: session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT\r\n")
    sys.stdout.write("Location: /cgi-bin/index.py\r\n\r\n")
    sys.exit(0)

if action in ["api", "save_config", "disconnect_device", "restart_service", "restart_interface"]:
    if not is_auth:
        sys.stdout.write("Status: 401 Unauthorized\r\n")
        sys.stdout.write("Content-Type: application/json\r\n\r\n")
        sys.stdout.write(json.dumps({"error": "Unauthorized"}))
        sys.exit(0)

    sys.stdout.write("Content-Type: application/json\r\n\r\n")
    if action == "api":
        data_type = get_params.get("type", ["all"])[0]
        res = {}
        if data_type in ["all", "temp"]: res["temp"] = get_temp()
        if data_type in ["all", "sys"]: res["sys"] = get_sys()
        if data_type in ["all", "net"]: res["net"] = get_net()
        if data_type in ["all", "wifi"]: res["wifi"] = get_wireless()
        if data_type in ["all", "dev"]: res["dev"] = get_devices()
        sys.stdout.write(json.dumps(res))
        
    elif action == "disconnect_device":
        if method != "POST":
            sys.stdout.write(json.dumps({"success": False, "error": "POST required"}))
        else:
            try:
                request = json.loads(post_data)
                result = disconnect_wireless_device(request.get("mac", ""))
                sys.stdout.write(json.dumps(result))
            except Exception as e:
                sys.stdout.write(json.dumps({"success": False, "error": str(e)}))
                
    elif action == "restart_service":
        if method != "POST":
            sys.stdout.write(json.dumps({"success": False, "error": "POST required"}))
        else:
            try:
                request = json.loads(post_data)
                srv = request.get("service", "")
                if srv in ["network", "dnsmasq", "firewall", "uhttpd", "cron", "odhcpd", "system"]:
                    cmd = ["reboot"] if srv == "system" else ["/etc/init.d/" + srv, "restart"]
                    with open(os.devnull, 'w') as devnull:
                        subprocess.Popen(cmd, stdout=devnull, stderr=devnull)
                    sys.stdout.write(json.dumps({"success": True, "message": f"{srv.capitalize()} restart initiated."}))
                else:
                    sys.stdout.write(json.dumps({"success": False, "error": "Invalid service target."}))
            except Exception as e:
                sys.stdout.write(json.dumps({"success": False, "error": str(e)}))
                
    elif action == "restart_interface":
        if method != "POST":
            sys.stdout.write(json.dumps({"success": False, "error": "POST required"}))
        else:
            try:
                request = json.loads(post_data)
                iface = request.get("interface", "")
                if re.match(r"^[a-zA-Z0-9_]+$", iface):
                    with open(os.devnull, 'w') as devnull:
                        subprocess.Popen(["/sbin/ifup", iface], stdout=devnull, stderr=devnull)
                    sys.stdout.write(json.dumps({"success": True, "message": f"Interface {iface} restarted."}))
                else:
                    sys.stdout.write(json.dumps({"success": False, "error": "Invalid interface name."}))
            except Exception as e:
                sys.stdout.write(json.dumps({"success": False, "error": str(e)}))
                
    elif action == "save_config" and method == "POST":
        try:
            new_conf = json.loads(post_data)
            success = save_config(new_conf)
            sys.stdout.write(json.dumps({"success": success}))
        except Exception as e:
            sys.stdout.write(json.dumps({"success": False, "error": str(e)}))
    sys.exit(0)

if set_new_cookie:
    sys.stdout.write(f"Set-Cookie: session={SECRET_HASH}; HttpOnly; Path=/\r\n")

sys.stdout.write("Content-Type: text/html; charset=utf-8\r\n\r\n")

if not is_auth:
    login_html = """<!DOCTYPE html>
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
    sys.stdout.write(login_html)
    sys.exit(0)

config_obj = load_config()

dashboard_html = """<!DOCTYPE html>
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
    <script src="/js/index.js" defer></script>
</head>
<body class="dashboard-page">
    <div class="container">
        <div class="header">
            <h1>Pi Status</h1>
            <div class="header-actions">
                <a href="?action=api&type=all" target="_blank" class="btn" style="margin-right: 15px; background: #2980b9;">JSON API</a>
                <div class="indicator">● Live Tracker</div>
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
                        <div class="metric-value"><span id="val_temp">--</span> °C</div>
                    </div>
                    <div class="card">
                        <h3>System Load & RAM</h3>
                        <div class="metric-section">
                            <div class="metric-label">LOAD AVERAGE (1m 5m 15m)</div>
                            <div id="val_load" class="metric-detail">--</div>
                            <div class="metric-label">MEMORY USAGE</div>
                            <div id="val_ram" class="metric-detail">--</div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Interfaces -->
            <div id="interfaces" class="panel">
                <div class="card">
                    <h3>Network Interfaces</h3>
                    <div id="net_list">Loading interface data...</div>
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
                                <tr><td colspan="4">Loading devices...</td></tr>
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
                        <label>System Load & RAM Update Interval</label>
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
                    <button class="btn" type="button" data-action="save-settings">Save Configuration</button>
                    <div id="save_msg" class="save-message">Settings saved successfully! Reloading...</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Inject command execution script specific to controls -->
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
            msgEl.style.color = 'var(--text-color)';
            
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
</html>"""

sys.stdout.write(dashboard_html.replace("__CONFIG_JSON__", json.dumps(config_obj)))