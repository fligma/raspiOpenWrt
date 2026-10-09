"""Network-facing data: interface status, wireless radios, connected clients,
and the Wi-Fi deauth action. Uses ubus and hostapd, plus DHCP/ARP tables."""

import json
import os
import re
import subprocess

MAC_RE = r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}"


def cmd_json(cmd):
    """Run a shell command and parse its stdout as JSON, or {} on failure."""
    try:
        with open(os.devnull, 'w') as devnull:
            res = subprocess.check_output(cmd, shell=True, stderr=devnull)
        return json.loads(res.decode('utf-8'))
    except Exception:
        return {}


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
    """Map MAC -> hostapd ubus object for every associated station."""
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
                        if re.fullmatch(MAC_RE, normalized_mac):
                            clients[normalized_mac] = object_name
    except Exception:
        pass
    return clients


def disconnect_wireless_device(mac):
    """Deauthenticate a station from the access point it is associated with."""
    mac = mac.upper()
    if not re.fullmatch(MAC_RE, mac):
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
    """Merge DHCP leases, the ARP table, and Wi-Fi clients into one device list."""
    devices = {}
    try:
        if os.path.exists("/tmp/dhcp.leases"):
            with open("/tmp/dhcp.leases", "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        mac = parts[1].upper()
                        devices[mac] = {"ip": parts[2], "mac": mac, "hostname": parts[3], "source": "DHCP"}
    except Exception:
        pass

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
    except Exception:
        pass

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
