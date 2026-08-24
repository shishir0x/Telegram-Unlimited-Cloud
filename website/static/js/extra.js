// BroadcastChannel for real-time cross-tab / cross-window sync
const DRIVE_SYNC_CHANNEL = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('tg_drive_sync') : null;

function broadcastDriveChange(action = 'REFRESH', details = {}) {
    try {
        if (DRIVE_SYNC_CHANNEL) {
            DRIVE_SYNC_CHANNEL.postMessage({ type: action, details, timestamp: Date.now() });
        }
        localStorage.setItem('tg_drive_last_sync', JSON.stringify({ action, details, timestamp: Date.now() }));
    } catch (e) {
        console.warn('Sync broadcast error:', e);
    }
}

// Listen for cross-window / cross-tab changes
if (DRIVE_SYNC_CHANNEL) {
    DRIVE_SYNC_CHANNEL.onmessage = (event) => {
        if (event.data && (event.data.type === 'REFRESH' || event.data.type === 'MOVE' || event.data.type === 'UPLOAD')) {
            if (typeof getCurrentDirectory === 'function') {
                getCurrentDirectory();
            }
        }
    };
}
window.addEventListener('storage', (event) => {
    if (event.key === 'tg_drive_last_sync') {
        if (typeof getCurrentDirectory === 'function') {
            getCurrentDirectory();
        }
    }
});

function getCurrentPath() {
    try {
        const url = new URL(window.location.href);
        let path = url.searchParams.get('path');
        if (path === null) {
            return '/';
        }
        // Clean trailing spaces and any malformed query substrings
        if (path.includes('&auth=')) {
            path = path.split('&auth=')[0];
        }
        path = path.trim();
        return path || '/';
    } catch {
        return '/';
    }
}

function getFolderAuthFromPath() {
    try {
        const url = new URL(window.location.href);
        const auth = url.searchParams.get('auth');
        return auth || null;
    } catch {
        return null;
    }
}

function buildFileUrl(filePath, isStream = false) {
    const rootUrl = getRootUrl();
    let cleanPath = (filePath || '').replaceAll('//', '/');
    if (cleanPath.startsWith('/share_')) {
        cleanPath = '/' + cleanPath.replace('/share_', '');
    }
    let url = `${rootUrl}/file?path=${encodeURIComponent(cleanPath)}`;

    const auth = getFolderAuthFromPath();
    if (auth) {
        url += `&auth=${encodeURIComponent(auth)}`;
    }

    if (isStream) {
        return `${rootUrl}/stream?url=${encodeURIComponent(url)}`;
    }
    return url;
}

// Navigation Request Tracking for Race Condition Prevention
window.NAV_REQUEST_ID = 0;

function getParentPath(currentPath) {
    if (!currentPath || currentPath === '/' || currentPath === '/recent' || currentPath === '/trash' || currentPath.startsWith('/tags/')) {
        return '/';
    }
    let clean = currentPath.replaceAll('//', '/');
    if (clean.endsWith('/') && clean !== '/') {
        clean = clean.slice(0, -1);
    }
    const lastSlash = clean.lastIndexOf('/');
    if (lastSlash <= 0) {
        return '/';
    }
    return clean.slice(0, lastSlash);
}

// Single Page Application (SPA) smooth router
function navigateToPath(targetPath, pushState = true) {
    if (!targetPath) targetPath = '/';
    let cleanPath = targetPath.replaceAll('//', '/');
    if (cleanPath !== '/' && cleanPath.endsWith('/')) {
        cleanPath = cleanPath.slice(0, -1);
    }

    const auth = getFolderAuthFromPath();
    const url = new URL(window.location.href);
    url.searchParams.set('path', cleanPath);
    if (auth && cleanPath.startsWith('/share')) {
        url.searchParams.set('auth', auth);
    } else if (!cleanPath.startsWith('/share')) {
        url.searchParams.delete('auth');
    }

    if (pushState) {
        window.history.pushState({ path: cleanPath }, '', url.toString());
    }

    // Reset views when navigating
    if (cleanPath === '/shared_links' || cleanPath.startsWith('/shared_links')) {
        if (typeof window.hideSyncActivityView === 'function') {
            window.hideSyncActivityView();
        }
        if (typeof window.showSharedLinksView === 'function') {
            window.showSharedLinksView(false);
            return;
        }
    } else {
        if (typeof window.hideSharedLinksView === 'function') {
            window.hideSharedLinksView();
        }
        if (typeof window.hideSyncActivityView === 'function') {
            window.hideSyncActivityView();
        }
    }

    // Update sidebar active highlights immediately
    updateSidebarNavSelection(cleanPath);

    // Immediately show skeleton loader to prevent showing stale content during fetch
    if (typeof showDirectorySkeleton === 'function') {
        showDirectorySkeleton();
    }

    // Fetch and render directory smoothly without full page reload
    if (typeof getCurrentDirectory === 'function') {
        getCurrentDirectory();
    }
}

function updateSidebarNavSelection(path) {
    const isTrash = path && path.startsWith('/trash');
    const isRecent = path && (path === '/recent' || path.startsWith('/recent'));
    const isSharedLinks = path && (path === '/shared_links' || path.startsWith('/shared_links')) || (window.CURRENT_PAGE_VIEW === 'shared_links');
    const isSync = (window.CURRENT_PAGE_VIEW === 'sync');
    const navMyDrive = document.getElementById('nav-my-drive');
    const navRecent = document.getElementById('nav-recent');
    const navSharedLinks = document.getElementById('nav-shared-links');
    const navSyncActivity = document.getElementById('nav-sync-activity');
    const navTrash = document.getElementById('nav-trash');
    const newBtn = document.getElementById('new-button');

    const allNavs = [navMyDrive, navRecent, navSharedLinks, navSyncActivity, navTrash];
    allNavs.forEach(n => {
        if (n) n.className = 'gd-nav-item unselected-item';
    });

    if (isSharedLinks) {
        if (navSharedLinks) navSharedLinks.className = 'gd-nav-item selected-item';
        if (newBtn) newBtn.style.display = 'inline-flex';
    } else if (isSync) {
        if (navSyncActivity) navSyncActivity.className = 'gd-nav-item selected-item';
        if (newBtn) newBtn.style.display = 'inline-flex';
    } else if (isTrash) {
        if (navTrash) navTrash.className = 'gd-nav-item selected-item';
        if (newBtn) newBtn.style.display = 'none';
    } else if (isRecent) {
        if (navRecent) navRecent.className = 'gd-nav-item selected-item';
        if (newBtn) newBtn.style.display = 'inline-flex';
    } else {
        if (navMyDrive) navMyDrive.className = 'gd-nav-item selected-item';
        if (newBtn) newBtn.style.display = 'inline-flex';
    }
}


// Handle Browser Back / Forward buttons natively
window.addEventListener('popstate', (e) => {
    updateSidebarNavSelection(getCurrentPath());
    if (typeof showDirectorySkeleton === 'function') {
        showDirectorySkeleton();
    }
    if (typeof getCurrentDirectory === 'function') {
        getCurrentDirectory();
    }
});

// Sidebar section active states are handled in sidebar.js on DOMContentLoaded

function convertBytes(bytes) {
    const kilobyte = 1024;
    const megabyte = kilobyte * 1024;
    const gigabyte = megabyte * 1024;

    if (bytes >= gigabyte) {
        return (bytes / gigabyte).toFixed(2) + ' GB';
    } else if (bytes >= megabyte) {
        return (bytes / megabyte).toFixed(2) + ' MB';
    } else if (bytes >= kilobyte) {
        return (bytes / kilobyte).toFixed(2) + ' KB';
    } else {
        return bytes + ' bytes';
    }
}

function getRootUrl() {
    const url = new URL(window.location.href);
    const protocol = url.protocol; // Get the protocol, e.g., "https:"
    const hostname = url.hostname; // Get the hostname, e.g., "sub.example.com" or "192.168.1.1"
    const port = url.port; // Get the port, e.g., "8080"

    const rootUrl = `${protocol}//${hostname}${port ? ':' + port : ''}`;

    return rootUrl;
}

async function copyTextToClipboard(text) {
    if (!text) return false;
    if (navigator.clipboard && window.isSecureContext && navigator.clipboard.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (err) {
            console.warn('Navigator clipboard failed, using fallback:', err);
        }
    }
    return fallbackCopyTextToClipboard(text);
}

function fallbackCopyTextToClipboard(text) {
    let successful = false;
    try {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.top = '0';
        textArea.style.left = '0';
        textArea.style.width = '2em';
        textArea.style.height = '2em';
        textArea.style.padding = '0';
        textArea.style.border = 'none';
        textArea.style.outline = 'none';
        textArea.style.boxShadow = 'none';
        textArea.style.background = 'transparent';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        successful = document.execCommand('copy');
        document.body.removeChild(textArea);
    } catch (err) {
        console.error('Fallback clipboard copy failed:', err);
        successful = false;
    }
    return successful;
}


function getRandomId() {
    const length = 6;
    const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let result = '';
    for (let i = 0; i < length; i++) {
        result += characters.charAt(Math.floor(Math.random() * characters.length));
    }
    return result;
}
const getRandomID = getRandomId;
window.getRandomId = getRandomId;
window.getRandomID = getRandomId;

function removeSlash(text) {
    let charactersToRemove = "[/]+"; // Define the characters to remove inside square brackets
    let trimmedStr = text.replace(new RegExp(`^${charactersToRemove}|${charactersToRemove}$`, 'g'), '');
    return trimmedStr;
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function showToast(message, duration = 3000) {
    let container = document.getElementById('gd-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'gd-toast-container';
        container.className = 'gd-toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = 'gd-toast';
    if (typeof message === 'string' && !message.includes('<')) {
        toast.textContent = message;
    } else {
        toast.innerHTML = String(message || '');
    }
    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.add('show');
    });

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}