"""Persistence for the dashboard's refresh-interval configuration."""

import json
import os

CONFIG_DIR = "/config"
CONFIG_FILE = "/config/index.conf"

DEFAULT_CONFIG = {
    "temp_interval": 2,
    "sys_interval": 3,
    "net_interval": 5,
    "wifi_interval": 5,
    "dev_interval": 5,
    "storage_interval": 10,
    "traffic_interval": 5,
}


def _ensure_config_dir():
    if not os.path.exists(CONFIG_DIR):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
        except Exception:
            pass


def load_config():
    """Return the saved config, filling in any missing keys from the defaults."""
    _ensure_config_dir()

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                conf = json.loads(f.read())
            for key, value in DEFAULT_CONFIG.items():
                if key not in conf:
                    conf[key] = value
            return conf
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(new_conf):
    """Write the config to disk, returning True on success."""
    _ensure_config_dir()

    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(json.dumps(new_conf, indent=2))
        return True
    except Exception:
        return False
