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
            if (typeof getCurrentDirectory === 'function' && getPassword()) {
                getCurrentDirectory();
            }
        }
    };
}
window.addEventListener('storage', (event) => {
    if (event.key === 'tg_drive_last_sync') {
        if (typeof getCurrentDirectory === 'function' && getPassword()) {
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

    // Update sidebar active highlights immediately
    updateSidebarNavSelection(cleanPath);

    // Fetch and render directory smoothly without full page reload
    if (typeof getCurrentDirectory === 'function') {
        getCurrentDirectory();
    }
}

function updateSidebarNavSelection(path) {
    const isTrash = path.startsWith('/trash');
    const isSearch = path.startsWith('/search');
    const navMyDrive = document.getElementById('nav-my-drive');
    const navComputers = document.getElementById('nav-computers');
    const navTrash = document.getElementById('nav-trash');
    const newBtn = document.getElementById('new-button');

    if (navMyDrive && navTrash) {
        if (isTrash) {
            navMyDrive.className = 'gd-nav-item unselected-item';
            if (navComputers) navComputers.className = 'gd-nav-item unselected-item';
            navTrash.className = 'gd-nav-item selected-item';
            if (newBtn) newBtn.style.display = 'none';
        } else {
            navMyDrive.className = 'gd-nav-item selected-item';
            if (navComputers) navComputers.className = 'gd-nav-item unselected-item';
            navTrash.className = 'gd-nav-item unselected-item';
            if (newBtn) newBtn.style.display = 'inline-flex';
        }
    }
}

// Handle Browser Back / Forward buttons natively
window.addEventListener('popstate', (e) => {
    updateSidebarNavSelection(getCurrentPath());
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

const INPUTS = {}

function validateInput(event) {
    console.log('Validating Input')
    const pattern = /^[a-zA-Z0-9 \-_\\[\]()@#!$%*+={}:;<>,.?/|\\~`]*$/;;
    const input = event.target;
    if (!pattern.test(input.value)) {
        input.value = INPUTS[input.id]
    } else {
        INPUTS[input.id] = input.value
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

function copyTextToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
            alert('Link copied to clipboard!');
        }).catch(function (err) {
            console.error('Could not copy text: ', err);
            fallbackCopyTextToClipboard(text);
        });
    } else {
        fallbackCopyTextToClipboard(text);
    }
}

function fallbackCopyTextToClipboard(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();

    try {
        const successful = document.execCommand('copy');
        if (successful) {
            alert('Link copied to clipboard!');
        } else {
            alert('Failed to copy the link.');
        }
    } catch (err) {
        console.error('Fallback: Oops, unable to copy', err);
    }

    document.body.removeChild(textArea);
}

function getPassword() {
    return localStorage.getItem('password')
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
    toast.innerHTML = message;
    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.add('show');
    });

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}