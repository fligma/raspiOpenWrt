// Small DOM + formatting helpers shared across the dashboard renderers.

export function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.innerText = value;
}

export function setBar(id, percent) {
    const el = document.getElementById(id);
    if (el) el.style.width = Math.max(0, Math.min(100, Number(percent) || 0)) + '%';
}

export function humanBytes(n) {
    n = Number(n) || 0;
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
        n /= 1024;
        i += 1;
    }
    return (i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + units[i];
}

export function ratePerSecond(current, previous, elapsedMs) {
    if (previous == null || elapsedMs <= 0) return 0;
    return Math.max(0, (current - previous) / (elapsedMs / 1000));
}

// Swap a container's markup while keeping any <details> blocks the user opened.
export function replacePreservingDetails(element, html) {
    const expandedKeys = new Set(
        Array.from(element.querySelectorAll('details[open]'))
            .map(details => details.dataset.detailKey)
    );
    element.innerHTML = html;
    element.querySelectorAll('details[data-detail-key]').forEach(details => {
        details.open = expandedKeys.has(details.dataset.detailKey);
    });
}
