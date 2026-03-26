/**
 * ui.js — DOM helpers and visual feedback.
 */

export function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.innerText = message;
    
    let container = document.getElementById("toast-container");
    if (!container) {
        container = document.createElement("div");
        container.id = "toast-container";
        document.body.appendChild(container);
    }
    
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 500);
    }, 3000);
}

export function formatPrice(p) {
    return Number(p).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function switchTab(tabContainerId, activeTabId) {
    const container = document.getElementById(tabContainerId);
    if (!container) return;
    
    // 1. Update Content Panes
    const contents = container.querySelectorAll('.bottom-content, .settings-content');
    contents.forEach(c => c.classList.remove('active'));
    
    const activeContent = document.getElementById(activeTabId);
    if (activeContent) activeContent.classList.add('active');

    // 2. Update Tab Buttons (Assuming they are in a sibling container or same container)
    const tabs = container.querySelectorAll('.bottom-tab, .settings-tab');
    tabs.forEach(t => {
        const idMatch = t.getAttribute('onclick')?.includes(activeTabId.replace('tab-', ''));
        if (idMatch) t.classList.add('active');
        else t.classList.remove('active');
    });
}

export function updateElementText(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}
