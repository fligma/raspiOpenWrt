#!/usr/bin/env python3
"""Rasconf dashboard CGI entry point.

Handles PIN authentication, routes API/action requests, and serves the login and
dashboard pages. Data gathering and page templates live in sibling modules that
are imported here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json
import subprocess
import urllib.parse
import re
from http import cookies

import auth
import store
import sysinfo
import netinfo
import pages

DEFAULT_HASH = "9b8769a4a742959a2d0298c36fb70623f2dfacda8436237df08d8dfd5b37374c"  # pass123
HASH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hash.file")

SECRET_HASH = auth.load_secret_hash(HASH_FILE, DEFAULT_HASH)


def handle_api(get_params):
    data_type = get_params.get("type", ["all"])[0]
    res = {}
    if data_type in ["all", "temp"]:
        res["temp"] = sysinfo.get_temp()
    if data_type in ["all", "sys"]:
        res["sys"] = sysinfo.get_sys()
        res["cpu"] = sysinfo.get_cpu()
    if data_type in ["all", "storage"]:
        res["storage"] = sysinfo.get_storage()
    if data_type in ["all", "traffic"]:
        res["traffic"] = sysinfo.get_traffic()
    if data_type in ["all", "net"]:
        res["net"] = netinfo.get_net()
    if data_type in ["all", "wifi"]:
        res["wifi"] = netinfo.get_wireless()
    if data_type in ["all", "dev"]:
        res["dev"] = netinfo.get_devices()
    return res


def handle_disconnect(post_data):
    request = json.loads(post_data)
    return netinfo.disconnect_wireless_device(request.get("mac", ""))


VALID_SERVICES = ["network", "dnsmasq", "firewall", "uhttpd", "cron", "odhcpd", "system"]


def handle_restart_service(post_data):
    request = json.loads(post_data)
    srv = request.get("service", "")
    if srv not in VALID_SERVICES:
        return {"success": False, "error": "Invalid service target."}
    cmd = ["reboot"] if srv == "system" else ["/etc/init.d/" + srv, "restart"]
    with open(os.devnull, 'w') as devnull:
        subprocess.Popen(cmd, stdout=devnull, stderr=devnull)
    return {"success": True, "message": f"{srv.capitalize()} restart initiated."}


def handle_restart_interface(post_data):
    request = json.loads(post_data)
    iface = request.get("interface", "")
    if not re.match(r"^[a-zA-Z0-9_]+$", iface):
        return {"success": False, "error": "Invalid interface name."}
    with open(os.devnull, 'w') as devnull:
        subprocess.Popen(["/sbin/ifup", iface], stdout=devnull, stderr=devnull)
    return {"success": True, "message": f"Interface {iface} restarted."}


def handle_save_config(post_data):
    new_conf = json.loads(post_data)
    return {"success": store.save_config(new_conf)}


def read_request():
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
    return method, get_params, post_data, post_params, cookie, action


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    method, get_params, post_data, post_params, cookie, action = read_request()

    is_auth, set_new_cookie = auth.check_auth(post_params, cookie, SECRET_HASH)

    if action == "logout":
        sys.stdout.write("Set-Cookie: session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT\r\n")
        sys.stdout.write("Location: /cgi-bin/index.py\r\n\r\n")
        return

    json_actions = {
        "api": lambda: handle_api(get_params),
        "disconnect_device": lambda: handle_disconnect(post_data),
        "restart_service": lambda: handle_restart_service(post_data),
        "restart_interface": lambda: handle_restart_interface(post_data),
        "save_config": lambda: handle_save_config(post_data),
    }

    if action in json_actions:
        if not is_auth:
            sys.stdout.write("Status: 401 Unauthorized\r\n")
            sys.stdout.write("Content-Type: application/json\r\n\r\n")
            sys.stdout.write(json.dumps({"error": "Unauthorized"}))
            return

        sys.stdout.write("Content-Type: application/json\r\n\r\n")
        try:
            if action == "api":
                result = handle_api(get_params)
            elif method != "POST":
                # All non-read actions must arrive as POST.
                result = {"success": False, "error": "POST required"}
            else:
                result = json_actions[action]()
        except Exception as error:
            result = {"success": False, "error": str(error)}
        sys.stdout.write(json.dumps(result))
        return

    if set_new_cookie:
        sys.stdout.write(f"Set-Cookie: session={SECRET_HASH}; HttpOnly; Path=/\r\n")

    sys.stdout.write("Content-Type: text/html; charset=utf-8\r\n\r\n")

    if not is_auth:
        sys.stdout.write(pages.login_html())
        return

    config_json = json.dumps(store.load_config())
    sys.stdout.write(pages.dashboard_html(config_json))


if __name__ == "__main__":
    main()
