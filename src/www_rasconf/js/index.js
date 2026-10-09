// Main dashboard module: wires the split renderers into a single scheduler that
// behaves like OpenWrt LuCI's auto-refresh (global pause + live countdown).
import {
    updateTemp,
    updateSys,
    updateStorage,
    updateNet,
    updateWifi,
    updateDev,
    updateTraffic,
    disconnectDevice,
} from './render.js';
import { postJson } from './api.js';

const configElement = document.getElementById('rasconf-config');
const CONFIG = configElement ? JSON.parse(configElement.textContent) : {};

// A task is one panel: how often it refreshes and when it is next due.
let tasks = [];
let paused = false;
let tickTimer = null;

function seconds(value, fallback) {
    const n = parseInt(value, 10);
    return isNaN(n) || n < 1 ? fallback : n;
}

function buildTasks() {
    const now = Date.now();
    tasks = [
        { name: 'temp', fn: updateTemp, interval: seconds(CONFIG.temp_interval, 2), next: now },
        { name: 'sys', fn: updateSys, interval: seconds(CONFIG.sys_interval, 3), next: now },
        { name: 'storage', fn: updateStorage, interval: seconds(CONFIG.storage_interval, 10), next: now },
        { name: 'net', fn: updateNet, interval: seconds(CONFIG.net_interval, 5), next: now },
        { name: 'traffic', fn: updateTraffic, interval: seconds(CONFIG.traffic_interval, 5), next: now },
        { name: 'wifi', fn: updateWifi, interval: seconds(CONFIG.wifi_interval, 5), next: now },
        { name: 'dev', fn: updateDev, interval: seconds(CONFIG.dev_interval, 5), next: now },
    ];
}

function secondsToNext() {
    const now = Date.now();
    let soonest = Infinity;
    tasks.forEach(task => { soonest = Math.min(soonest, task.next - now); });
    if (!isFinite(soonest)) return 0;
    return Math.max(0, Math.ceil(soonest / 1000));
}

function paintIndicator() {
    const indicator = document.getElementById('live_indicator');
    const countdown = document.getElementById('refresh_countdown');
    if (paused) {
        if (indicator) { indicator.textContent = '\u25cf Paused'; indicator.classList.add('paused'); }
        if (countdown) countdown.textContent = 'paused';
    } else {
        if (indicator) { indicator.textContent = '\u25cf Live'; indicator.classList.remove('paused'); }
        if (countdown) countdown.textContent = secondsToNext() + 's';
    }
}

function tick() {
    if (paused) { paintIndicator(); return; }
    const now = Date.now();
    tasks.forEach(task => {
        if (now >= task.next) {
            try { task.fn(); } catch (error) { console.error('refresh ' + task.name, error); }
            task.next = now + task.interval * 1000;
        }
    });
    paintIndicator();
}

function togglePause() {
    paused = !paused;
    const button = document.getElementById('refresh_toggle');
    if (button) {
        button.textContent = paused ? 'Resume' : 'Pause';
        button.setAttribute('aria-pressed', String(paused));
        button.classList.toggle('paused', paused);
    }
    if (!paused) {
        // Resume: run everything now, then fall back to the normal cadence.
        const now = Date.now();
        tasks.forEach(task => { task.next = now; });
    }
    paintIndicator();
}

function runOnce() {
    tasks.forEach(task => {
        try { task.fn(); } catch (error) { console.error('init ' + task.name, error); }
    });
}

function fillSettingsForm() {
    const map = {
        cfg_temp: CONFIG.temp_interval,
        cfg_sys: CONFIG.sys_interval,
        cfg_net: CONFIG.net_interval,
        cfg_wifi: CONFIG.wifi_interval,
        cfg_dev: CONFIG.dev_interval,
        cfg_storage: CONFIG.storage_interval,
        cfg_traffic: CONFIG.traffic_interval,
    };
    Object.entries(map).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el) el.value = value;
    });
}

function saveSettings() {
    const newConfig = {
        temp_interval: seconds(document.getElementById('cfg_temp').value, 2),
        sys_interval: seconds(document.getElementById('cfg_sys').value, 3),
        net_interval: seconds(document.getElementById('cfg_net').value, 5),
        wifi_interval: seconds(document.getElementById('cfg_wifi').value, 5),
        dev_interval: seconds(document.getElementById('cfg_dev').value, 5),
        storage_interval: seconds(document.getElementById('cfg_storage').value, 10),
        traffic_interval: seconds(document.getElementById('cfg_traffic').value, 5),
    };

    postJson('save_config', newConfig)
        .then(result => {
            if (result.success) {
                const msg = document.getElementById('save_msg');
                if (msg) msg.classList.add('visible');
                setTimeout(() => location.reload(), 1500);
            } else {
                alert('Error saving: ' + result.error);
            }
        })
        .catch(error => alert('Error saving: ' + error));
}

function init() {
    fillSettingsForm();

    buildTasks();
    runOnce();

    tickTimer = setInterval(tick, 1000);
    paintIndicator();

    const toggleButton = document.getElementById('refresh_toggle');
    if (toggleButton) toggleButton.addEventListener('click', togglePause);
}

document.addEventListener('rasconf:save-settings', saveSettings);
document.addEventListener('click', event => {
    const disconnectButton = event.target.closest('.disconnect-device');
    if (disconnectButton) disconnectDevice(disconnectButton.dataset.deviceMac);
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
