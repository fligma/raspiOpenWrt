#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import urllib.parse
import hashlib
from http import cookies

# Generate new hash via CLI: python3 -c "import hashlib; print(hashlib.sha256(b'YOUR_PIN').hexdigest())"
SECRET_HASH = "4791512d9f517bff255c49e11cbc678909181958b7b1d09335fc3c8246c24323"

CONFIG_FILE = "/etc/rasconf.conf"
DEFAULT_CONFIG = {
    "temp_interval": 2,
    "sys_interval": 3,
    "net_interval": 5,
    "wifi_interval": 5,
    "dev_interval": 5
}

def check_auth(params, cookie):
    """Verify session cookie or PIN login attempt."""
    submitted_pin = params.get("pin", [""])[0]
    
    if submitted_pin:
        pin_hash = hashlib.sha256(submitted_pin.encode("utf-8")).hexdigest()
        if pin_hash == SECRET_HASH:
            return True, True
            
    if "session" in cookie and cookie["session"].value == SECRET_HASH:
        return True, False
        
    return False, False

def load_config():
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

if action in ["api", "save_config"]:
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

if not is_auth
    login_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>rasconf - PIN Required</title>
    <style>
        body { background: #121212; color: #E0E0E0; font-family: 'Segoe UI', Roboto, Arial, sans-serif; margin: 0; padding: 0; display: flex; height: 100vh; align-items: center; justify-content: center; }
        .login-card { background: #212121; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); width: 100%; max-width: 360px; border-top: 4px solid #C51A4A; text-align: center; }
        .login-card h2 { margin-top: 0; color: white; font-size: 20px; margin-bottom: 20px; }
        .login-card input[type="password"] { width: 100%; padding: 12px; background: #1A1A1A; border: 1px solid #444; color: white; border-radius: 6px; box-sizing: border-box; font-size: 16px; margin-bottom: 20px; text-align: center; }
        .btn { background: #C51A4A; color: white; border: none; padding: 12px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; width: 100%; text-transform: uppercase; letter-spacing: 1px; transition: 0.2s; }
        .btn:hover { background: #A0153C; }
    </style>
</head>
<body>
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

dashboard_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Raspberry Pi Status</title>
    <style>
        body {{ background: #121212; color: #E0E0E0; font-family: 'Segoe UI', Roboto, Arial, sans-serif; margin: 0; padding: 20px; }}
        .container {{ max-width: 900px; margin: 0 auto; background: #212121; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); overflow: hidden; }}
        .header {{ background: #C51A4A; color: white; padding: 20px; display: flex; justify-content: space-between; align-items: center; }}
        .header h1 {{ margin: 0; font-size: 22px; font-weight: 600; letter-spacing: 0.5px; }}
        .header-actions {{ display: flex; align-items: center; gap: 15px; }}
        .indicator {{ font-size: 12px; opacity: 0.9; font-weight: bold; }}
        .logout-btn {{ background: rgba(0,0,0,0.3); color: white; border: 1px solid rgba(255,255,255,0.3); padding: 5px 12px; border-radius: 4px; text-decoration: none; font-size: 12px; font-weight: bold; transition: 0.2s; }}
        .logout-btn:hover {{ background: rgba(0,0,0,0.6); }}
        
        .tabs {{ display: flex; background: #2A2A2A; border-bottom: 1px solid #333; }}
        .tab {{ flex: 1; padding: 15px; text-align: center; cursor: pointer; color: #AAA; transition: 0.2s; font-weight: 500; font-size: 14px; text-transform: uppercase; }}
        .tab:hover {{ background: #333; color: white; }}
        .tab.active {{ background: #333; color: #C51A4A; border-bottom: 3px solid #C51A4A; }}
        
        .content {{ padding: 20px; }}
        .panel {{ display: none; }}
        .panel.active {{ display: block; }}
        
        .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
        .card {{ background: #2E2E2E; padding: 20px; border-radius: 8px; margin-bottom: 15px; border-left: 4px solid #C51A4A; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }}
        .card h3 {{ margin-top: 0; margin-bottom: 15px; color: white; font-size: 16px; border-bottom: 1px solid #3C3C3C; padding-bottom: 8px; }}
        
        table {{ width: 100%; border-collapse: collapse; margin-top: 5px; }}
        table th, table td {{ padding: 12px; text-align: left; border-bottom: 1px solid #3C3C3C; font-size: 14px; }}
        table th {{ color: #888; font-size: 12px; text-transform: uppercase; font-weight: 600; }}
        
        .form-group {{ margin-bottom: 15px; }}
        .form-group label {{ display: block; margin-bottom: 5px; color: #BBB; font-size: 14px; }}
        .form-group input {{ width: 100%; padding: 10px; background: #1A1A1A; border: 1px solid #444; color: white; border-radius: 5px; box-sizing: border-box; }}
        .btn {{ background: #C51A4A; color: white; border: none; padding: 12px 24px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: bold; transition: 0.2s; width: 100%; text-transform: uppercase; letter-spacing: 1px; }}
        .btn:hover {{ background: #A0153C; }}
        
        pre {{ background: #1A1A1A; padding: 15px; border-radius: 6px; overflow-x: auto; font-size: 12px; color: #AAA; border: 1px solid #333; }}
        details summary {{ cursor: pointer; font-size: 12px; color: #C51A4A; margin-top: 8px; font-weight: bold; outline: none; }}
        .status-up {{ color: #4CAF50; font-weight: bold; font-size: 12px; padding: 2px 6px; background: rgba(76, 175, 80, 0.1); border-radius: 4px; }}
        .status-down {{ color: #F44336; font-weight: bold; font-size: 12px; padding: 2px 6px; background: rgba(244, 67, 54, 0.1); border-radius: 4px; }}
        .pill {{ background: #1A1A1A; padding: 4px 8px; border-radius: 4px; font-size: 12px; color: #AAA; border: 1px solid #444; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Raspberry Pi 3B+ Status</h1>
            <div class="header-actions">
                <div class="indicator">● Live Tracker</div>
                <a href="?action=logout" class="logout-btn">Logout</a>
            </div>
        </div>
        <div class="tabs">
            <div class="tab active" onclick="switchTab(event, 'dashboard')">Dashboard</div>
            <div class="tab" onclick="switchTab(event, 'interfaces')">Interfaces</div>
            <div class="tab" onclick="switchTab(event, 'wireless')">Wireless</div>
            <div class="tab" onclick="switchTab(event, 'devices')">Devices</div>
            <div class="tab" onclick="switchTab(event, 'settings')">Settings</div>
        </div>
        
        <div class="content">
            <!-- Dashboard -->
            <div id="dashboard" class="panel active">
                <div class="grid-2">
                    <div class="card">
                        <h3>CPU Temperature</h3>
                        <div style="font-size: 38px; font-weight: 300; margin: 15px 0;"><span id="val_temp">--</span> °C</div>
                    </div>
                    <div class="card">
                        <h3>System Load & RAM</h3>
                        <div style="margin-top: 15px;">
                            <div style="color: #888; font-size: 12px; font-weight: bold;">LOAD AVERAGE (1m 5m 15m)</div>
                            <div id="val_load" style="font-size: 20px; font-weight: 300; margin-bottom: 15px;">--</div>
                            <div style="color: #888; font-size: 12px; font-weight: bold;">MEMORY USAGE</div>
                            <div id="val_ram" style="font-size: 20px; font-weight: 300;">--</div>
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
                    <div style="overflow-x: auto;">
                        <table>
                            <thead>
                                <tr><th>Hostname</th><th>IP Address</th><th>MAC Address</th><th>Interface / Source</th></tr>
                            </thead>
                            <tbody id="dev_list">
                                <tr><td colspan="4">Loading devices...</td></tr>
                            </tbody>
                        </table>
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
                    <button class="btn" onclick="saveSettings()">Save Configuration</button>
                    <div id="save_msg" style="margin-top: 15px; color: #4CAF50; display: none; text-align: center; font-weight: bold;">Settings saved successfully! Reloading...</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const CONFIG = {json.dumps(config_obj)};
        let intervals = {{}};

        function switchTab(e, tabId) {{
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            e.target.classList.add('active');
        }}

        function fetchData(type, callback) {{
            fetch('?action=api&type=' + type)
                .then(r => r.json())
                .then(data => callback(data))
                .catch(err => console.error('Error fetching ' + type, err));
        }}

        function updateTemp() {{
            fetchData('temp', d => {{ if(d.temp) document.getElementById('val_temp').innerText = d.temp; }});
        }}

        function updateSys() {{
            fetchData('sys', d => {{
                if(d.sys) {{
                    document.getElementById('val_load').innerText = d.sys.load;
                    document.getElementById('val_ram').innerText = d.sys.ram;
                }}
            }});
        }}

        function updateNet() {{
            fetchData('net', d => {{
                let html = '';
                if(Array.isArray(d.net)) {{
                    d.net.forEach(iface => {{
                        let upStr = iface.up ? '<span class="status-up">UP</span>' : '<span class="status-down">DOWN</span>';
                        let ipInfo = iface['ipv4-address'] && iface['ipv4-address'].length > 0 
                            ? iface['ipv4-address'].map(ip => ip.address + '/' + ip.mask).join(', ') 
                            : 'No IPv4 Address';
                        
                        html += `
                        <div style="border-bottom: 1px solid #3C3C3C; padding: 15px 0;">
                            <div style="font-weight: 600; font-size: 16px; margin-bottom: 5px;">${{iface.interface}} ${{upStr}}</div>
                            <div style="color: #AAA; font-size: 13px;">
                                <span class="pill">Device: ${{iface.device || 'N/A'}}</span> 
                                <span style="margin-left: 10px;">IPv4: ${{ipInfo}}</span>
                            </div>
                            <details>
                                <summary>► View Full Interface Data</summary>
                                <pre>${{JSON.stringify(iface, null, 2)}}</pre>
                            </details>
                        </div>`;
                    }});
                }} else {{ 
                    html = '<pre>' + JSON.stringify(d.net, null, 2) + '</pre>'; 
                }}
                document.getElementById('net_list').innerHTML = html;
            }});
        }}

        function updateWifi() {{
            fetchData('wifi', d => {{
                let html = '';
                if(d.wifi && Object.keys(d.wifi).length > 0 && !d.wifi.error) {{
                    for(let radio in d.wifi) {{
                        let rData = d.wifi[radio];
                        let upStr = rData.up ? '<span class="status-up">UP</span>' : '<span class="status-down">DOWN</span>';
                        html += `
                        <div style="border-bottom: 1px solid #3C3C3C; padding: 15px 0;">
                            <div style="font-weight: 600; font-size: 16px; margin-bottom: 5px;">${{radio}} ${{upStr}}</div>
                            <details>
                                <summary>► View Full Radio Data</summary>
                                <pre>${{JSON.stringify(rData, null, 2)}}</pre>
                            </details>
                        </div>`;
                    }}
                }} else {{
                    html = '<div style="color:#888;">No wireless interfaces found or device offline.</div><pre>' + JSON.stringify(d.wifi, null, 2) + '</pre>';
                }}
                document.getElementById('wifi_list').innerHTML = html;
            }});
        }}

        function updateDev() {{
            fetchData('dev', d => {{
                let html = '';
                if(d.dev && d.dev.length > 0) {{
                    d.dev.forEach(device => {{
                        let iface = device.interface ? device.interface : device.source;
                        html += `<tr>
                            <td style="font-weight: 500;">${{device.hostname}}</td>
                            <td>${{device.ip}}</td>
                            <td style="font-family: monospace; color: #AAA;">${{device.mac}}</td>
                            <td><span class="pill">${{iface}}</span></td>
                        </tr>`;
                    }});
                }} else {{
                    html = '<tr><td colspan="4" style="text-align:center; color:#888; padding: 20px;">No connected devices found.</td></tr>';
                }}
                document.getElementById('dev_list').innerHTML = html;
            }});
        }}

        function init() {{
            document.getElementById('cfg_temp').value = CONFIG.temp_interval;
            document.getElementById('cfg_sys').value = CONFIG.sys_interval;
            document.getElementById('cfg_net').value = CONFIG.net_interval;
            document.getElementById('cfg_wifi').value = CONFIG.wifi_interval;
            document.getElementById('cfg_dev').value = CONFIG.dev_interval;
            
            updateTemp(); updateSys(); updateNet(); updateWifi(); updateDev();
            
            intervals.temp = setInterval(updateTemp, CONFIG.temp_interval * 1000);
            intervals.sys = setInterval(updateSys, CONFIG.sys_interval * 1000);
            intervals.net = setInterval(updateNet, CONFIG.net_interval * 1000);
            intervals.wifi = setInterval(updateWifi, CONFIG.wifi_interval * 1000);
            intervals.dev = setInterval(updateDev, CONFIG.dev_interval * 1000);
        }}

        function saveSettings() {{
            let newConf = {{
                temp_interval: parseInt(document.getElementById('cfg_temp').value) || 2,
                sys_interval: parseInt(document.getElementById('cfg_sys').value) || 3,
                net_interval: parseInt(document.getElementById('cfg_net').value) || 5,
                wifi_interval: parseInt(document.getElementById('cfg_wifi').value) || 5,
                dev_interval: parseInt(document.getElementById('cfg_dev').value) || 5
            }};
            
            fetch('?action=save_config', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(newConf)
            }})
            .then(r => r.json())
            .then(res => {{
                if(res.success) {{
                    document.getElementById('save_msg').style.display = 'block';
                    setTimeout(() => location.reload(), 1500);
                }} else {{
                    alert("Error saving: " + res.error);
                }}
            }});
        }}

        window.onload = init;
    </script>
</body>
</html>"""

sys.stdout.write(dashboard_html)
lock';
                    setTimeout(() => location.reload(), 1500);
                }} else {{
                    alert("Error saving: " + res.error);
                }}
            }});
        }}

        window.onload = init;
    </script>
</body>
</html>"""

sys.stdout.write(dashboard_html)
