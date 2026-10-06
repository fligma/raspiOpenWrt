document.addEventListener('click', event => {
    const tab = event.target.closest('[data-tab-target]');
    if (tab) {
        const targetId = tab.dataset.tabTarget;
        document.querySelectorAll('.panel').forEach(panel => {
            panel.classList.toggle('active', panel.id === targetId);
        });
        document.querySelectorAll('[data-tab-target]').forEach(item => {
            const isActive = item === tab;
            item.classList.toggle('active', isActive);
            item.setAttribute('aria-selected', String(isActive));
        });
        return;
    }

    if (event.target.closest('[data-action="save-settings"]')) {
        document.dispatchEvent(new CustomEvent('rasconf:save-settings'));
    }
});