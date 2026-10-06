const configElement = document.getElementById('rasconf-config');
const CONFIG = configElement ? JSON.parse(configElement.textContent) : {};
const intervals = {};

function fetchData(type, callback) {
    fetch('?action=api&type=' + type)
        .then(response => response.json())
        .then(data => callback(data))
        .catch(error => console.error('Error fetching ' + type, error));
}

function updateTemp() {
    fetchData('temp', data => {
        if (data.temp) document.getElementById('val_temp').innerText = data.temp;
    });
}

function updateSys() {
    fetchData('sys', data => {
        if (data.sys) {
            document.getElementById('val_load').innerText = data.sys.load;
            document.getElementById('val_ram').innerText = data.sys.ram;
        }
    });
}

function replacePreservingDetails(element, html) {
    const expandedKeys = new Set(
        Array.from(element.querySelectorAll('details[open]'))
            .map(details => details.dataset.detailKey)
    );
    element.innerHTML = html;
    element.querySelectorAll('details[data-detail-key]').forEach(details => {
        details.open = expandedKeys.has(details.dataset.detailKey);
    });
}

function updateNet() {
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

function updateWifi() {
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

function updateDev() {
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
        document.getElementById('dev_list').innerHTML = html;
    });
}

function disconnectDevice(mac) {
    if (!confirm(`Disconnect Wi-Fi device ${mac}? It may reconnect if the device retries.`)) return;

    fetch('?action=disconnect_device', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({mac})
    })
        .then(response => response.json())
        .then(result => {
            if (!result.success) {
                alert('Unable to disconnect device: ' + result.error);
                return;
            }
            updateDev();
        })
        .catch(error => alert('Unable to disconnect device: ' + error));
}

function init() {
    document.getElementById('cfg_temp').value = CONFIG.temp_interval;
    document.getElementById('cfg_sys').value = CONFIG.sys_interval;
    document.getElementById('cfg_net').value = CONFIG.net_interval;
    document.getElementById('cfg_wifi').value = CONFIG.wifi_interval;
    document.getElementById('cfg_dev').value = CONFIG.dev_interval;

    updateTemp();
    updateSys();
    updateNet();
    updateWifi();
    updateDev();

    intervals.temp = setInterval(updateTemp, CONFIG.temp_interval * 1000);
    intervals.sys = setInterval(updateSys, CONFIG.sys_interval * 1000);
    intervals.net = setInterval(updateNet, CONFIG.net_interval * 1000);
    intervals.wifi = setInterval(updateWifi, CONFIG.wifi_interval * 1000);
    intervals.dev = setInterval(updateDev, CONFIG.dev_interval * 1000);
}

function saveSettings() {
    const newConfig = {
        temp_interval: parseInt(document.getElementById('cfg_temp').value, 10) || 2,
        sys_interval: parseInt(document.getElementById('cfg_sys').value, 10) || 3,
        net_interval: parseInt(document.getElementById('cfg_net').value, 10) || 5,
        wifi_interval: parseInt(document.getElementById('cfg_wifi').value, 10) || 5,
        dev_interval: parseInt(document.getElementById('cfg_dev').value, 10) || 5
    };

    fetch('?action=save_config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(newConfig)
    })
        .then(response => response.json())
        .then(result => {
            if (result.success) {
                document.getElementById('save_msg').classList.add('visible');
                setTimeout(() => location.reload(), 1500);
            } else {
                alert('Error saving: ' + result.error);
            }
        });
}

document.addEventListener('rasconf:save-settings', saveSettings);
document.addEventListener('click', event => {
    const disconnectButton = event.target.closest('.disconnect-device');
    if (disconnectButton) disconnectDevice(disconnectButton.dataset.deviceMac);
});
window.addEventListener('DOMContentLoaded', init);