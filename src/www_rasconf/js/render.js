// Per-panel render functions. Each pulls its slice of the API and paints it.
import { fetchData, postJson } from './api.js';
import {
    setText,
    setBar,
    humanBytes,
    ratePerSecond,
    replacePreservingDetails,
} from './format.js';

// Kept between traffic polls so we can derive RX/TX rates from the counters.
let lastTraffic = null;

export function updateTemp() {
    fetchData('temp', data => {
        if (data.temp) setText('val_temp', data.temp);
    });
}

export function updateSys() {
    fetchData('sys', data => {
        if (data.sys) {
            setText('val_load', data.sys.load);
            setText('val_ram', data.sys.ram);
            setBar('bar_ram', data.sys.ram_percent);
            setText('val_hostname', data.sys.hostname);
            setText('val_uptime', data.sys.uptime);
            setText('val_cores', data.sys.cores);
        }
        renderCpu(data.cpu);
    });
}

function renderCpu(cpu) {
    setText('val_cpu_overall', cpu ? cpu.overall + '%' : '--%');
    const grid = document.getElementById('cpu_cores');
    if (!grid) return;
    if (!cpu || !Array.isArray(cpu.cores) || cpu.cores.length === 0) {
        grid.innerHTML = '<div class="muted-message">Per-core data unavailable.</div>';
        return;
    }
    grid.innerHTML = cpu.cores.map((usage, index) => {
        const level = usage > 85 ? 'high' : usage > 55 ? 'mid' : '';
        return `
        <div class="core-item">
            <div class="core-head">
                <span class="core-name">Core ${index}</span>
                <span class="core-pct">${usage}%</span>
            </div>
            <div class="bar core-bar"><span class="bar-fill ${level}" style="width:${Math.max(0, Math.min(100, usage))}%"></span></div>
        </div>`;
    }).join('');
}

export function updateStorage() {
    fetchData('storage', data => {
        const s = data.storage;
        if (!s || s.error) return;
        setText(
            'val_storage',
            humanBytes(s.used) + ' used of ' + humanBytes(s.total) + ' (' + s.percent + '%)'
        );
        setBar('bar_storage', s.percent);
    });
}

export function updateNet() {
    fetchData('net', data => {
        let html = '';
        if (Array.isArray(data.net)) {
            data.net.forEach((iface, index) => {
                const statusClass = iface.up ? 'status-up' : 'status-down';
                const statusText = iface.up ? 'UP' : 'DOWN';
                const ipInfo = iface['ipv4-address'] && iface['ipv4-address'].length > 0
                    ? iface['ipv4-address'].map(ip => ip.address + '/' + ip.mask).join(', ')
                    : 'No IPv4 Address';

                html += `
                <div class="data-row">
                    <div class="data-row-title">${iface.interface} <span class="${statusClass}">${statusText}</span></div>
                    <div class="data-row-details">
                        <span class="pill">Device: ${iface.device || 'N/A'}</span>
                        <span class="data-ip">IPv4: ${ipInfo}</span>
                    </div>
                    <details data-detail-key="interface-${index}">
                        <summary>► View Full Interface Data</summary>
                        <pre>${JSON.stringify(iface, null, 2)}</pre>
                    </details>
                </div>`;
            });
        } else {
            html = '<pre>' + JSON.stringify(data.net, null, 2) + '</pre>';
        }
        replacePreservingDetails(document.getElementById('net_list'), html);
    });
}

export function updateWifi() {
    fetchData('wifi', data => {
        let html = '';
        if (data.wifi && Object.keys(data.wifi).length > 0 && !data.wifi.error) {
            Object.entries(data.wifi).forEach(([radio, radioData], index) => {
                const statusClass = radioData.up ? 'status-up' : 'status-down';
                const statusText = radioData.up ? 'UP' : 'DOWN';
                html += `
                <div class="data-row">
                    <div class="data-row-title">${radio} <span class="${statusClass}">${statusText}</span></div>
                    <details data-detail-key="radio-${index}">
                        <summary>► View Full Radio Data</summary>
                        <pre>${JSON.stringify(radioData, null, 2)}</pre>
                    </details>
                </div>`;
            });
        } else {
            html = '<div class="muted-message">No wireless interfaces found or device offline.</div><pre>' + JSON.stringify(data.wifi, null, 2) + '</pre>';
        }
        replacePreservingDetails(document.getElementById('wifi_list'), html);
    });
}

export function updateDev() {
    fetchData('dev', data => {
        let html = '';
        if (data.dev && data.dev.length > 0) {
            data.dev.forEach(device => {
                const iface = device.interface ? device.interface : device.source;
                const disconnectAction = device.wireless_interface
                    ? `<button class="disconnect-device" type="button" data-device-mac="${device.mac}" aria-label="Disconnect ${device.hostname} from Wi-Fi">Disconnect Wi-Fi</button>`
                    : '<span class="muted-message">Wired / not on Wi-Fi</span>';
                html += `<tr>
                    <td class="device-name">${device.hostname}</td>
                    <td>${device.ip}</td>
                    <td class="device-mac">${device.mac}</td>
                    <td><span class="pill">${device.wireless_interface || iface}</span></td>
                    <td>${disconnectAction}</td>
                </tr>`;
            });
        } else {
            html = '<tr><td class="empty-row" colspan="5">No connected devices found.</td></tr>';
        }
        const body = document.getElementById('dev_list');
        if (body) body.innerHTML = html;
    });
}

export function updateTraffic() {
    fetchData('traffic', data => {
        const t = data.traffic;
        const body = document.getElementById('traffic_list');
        if (!t || t.error || !body) return;

        const now = Date.now();
        let html = '';
        if (lastTraffic) {
            t.interfaces.forEach(iface => {
                const prev = lastTraffic.interfaces[iface.name];
                const elapsed = now - lastTraffic.time;
                const rxRate = ratePerSecond(iface.rx, prev && prev.rx, elapsed);
                const txRate = ratePerSecond(iface.tx, prev && prev.tx, elapsed);
                html += `<tr>
                    <td class="device-name">${iface.name}</td>
                    <td>${humanBytes(iface.rx)}</td>
                    <td>${humanBytes(rxRate)}/s</td>
                    <td>${humanBytes(iface.tx)}</td>
                    <td>${humanBytes(txRate)}/s</td>
                </tr>`;
            });
        } else {
            html = '<tr><td colspan="5" class="empty-row">Collecting traffic baseline...</td></tr>';
        }
        body.innerHTML = html || '<tr><td colspan="5" class="empty-row">No interfaces found.</td></tr>';

        const totals = document.getElementById('traffic_totals');
        if (totals) totals.innerText =
            'Total RX: ' + humanBytes(t.rx) + '    |    Total TX: ' + humanBytes(t.tx);

        const snapshot = { time: now, interfaces: {} };
        t.interfaces.forEach(iface => {
            snapshot.interfaces[iface.name] = { rx: iface.rx, tx: iface.tx };
        });
        lastTraffic = snapshot;
    });
}

export function disconnectDevice(mac) {
    if (!confirm(`Disconnect Wi-Fi device ${mac}? It may reconnect if the device retries.`)) return;

    postJson('disconnect_device', { mac })
        .then(result => {
            if (!result.success) {
                alert('Unable to disconnect device: ' + result.error);
                return;
            }
            updateDev();
        })
        .catch(error => alert('Unable to disconnect device: ' + error));
}
