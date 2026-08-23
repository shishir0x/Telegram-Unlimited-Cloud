// Google Drive Main Application Controller
let CURRENT_DIRECTORY_DATA = {};
let DIRECTORY_ITEMS = {};
let CURRENT_BREADCRUMBS = [];
let SELECTED_ITEM_ID = null;
let CURRENT_VIEW_MODE = localStorage.getItem('gd_view_mode') || 'list'; // 'list' or 'grid'
window.DRAGGED_DRIVE_ITEM = null;

// Google-Grade Lazy Thumbnail Batch Observer (Max 6 concurrent HTTP/MTProto requests)
const THUMB_QUEUE = [];
let THUMB_ACTIVE_COUNT = 0;
const MAX_CONCURRENT_THUMBS = 6;

function processThumbQueue() {
    while (THUMB_ACTIVE_COUNT < MAX_CONCURRENT_THUMBS && THUMB_QUEUE.length > 0) {
        const img = THUMB_QUEUE.shift();
        const dataSrc = img.getAttribute('data-src');
        if (!dataSrc) continue;

        THUMB_ACTIVE_COUNT++;
        img.src = dataSrc;
        img.removeAttribute('data-src');

        img.onload = () => {
            img.classList.add('loaded');
            const placeholder = img.parentElement ? img.parentElement.querySelector('.gd-thumb-shimmer') : null;
            if (placeholder) placeholder.style.display = 'none';
            THUMB_ACTIVE_COUNT = Math.max(0, THUMB_ACTIVE_COUNT - 1);
            processThumbQueue();
        };

        img.onerror = () => {
            img.style.display = 'none';
            const fallback = img.parentElement ? img.parentElement.querySelector('.gd-thumb-fallback') : null;
            const placeholder = img.parentElement ? img.parentElement.querySelector('.gd-thumb-shimmer') : null;
            if (placeholder) placeholder.style.display = 'none';
            if (fallback) fallback.style.display = 'flex';
            THUMB_ACTIVE_COUNT = Math.max(0, THUMB_ACTIVE_COUNT - 1);
            processThumbQueue();
        };
    }
}

const THUMB_OBSERVER = typeof IntersectionObserver !== 'undefined' ? new IntersectionObserver((entries, observer) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const img = entry.target;
            observer.unobserve(img);
            THUMB_QUEUE.push(img);
            processThumbQueue();
        }
    });
}, { rootMargin: '150px 0px' }) : null;

// Helper: Get File Badges & Icons
function getFileBadge(item) {
    if (item.type === 'folder') {
        return `<img class="item-icon-img" src="static/assets/folder-solid-icon.svg">`;
    }
    const ext = (item.extension || '').toLowerCase();
    const cat = item.category || 'Document';

    if (ext === '.pdf' || cat === 'PDF Document') {
        return `<span class="badge-icon badge-pdf">PDF</span>`;
    } else if (cat === 'Image') {
        return `<span class="badge-icon badge-image">IMG</span>`;
    } else if (cat === 'Video') {
        return `<span class="badge-icon badge-video">VID</span>`;
    } else if (cat === 'Audio') {
        return `<span class="badge-icon badge-audio">AUD</span>`;
    } else if (cat === 'Archive') {
        return `<span class="badge-icon badge-zip">ZIP</span>`;
    } else if (cat === 'Source Code') {
        return `<span class="badge-icon badge-code">CODE</span>`;
    } else {
        return `<span class="badge-icon badge-doc">FILE</span>`;
    }
}

function getBigIconEmoji(item) {
    if (item.type === 'folder') return '📁';
    const cat = item.category || 'Document';
    if (cat === 'PDF Document') return '📄';
    if (cat === 'Image') return '🖼️';
    if (cat === 'Video') return '🎬';
    if (cat === 'Audio') return '🎵';
    if (cat === 'Archive') return '📦';
    if (cat === 'Source Code') return '💻';
    return '📄';
}

function formatDate(dateStr) {
    if (!dateStr) return '--';
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return dateStr;
        return d.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
        });
    } catch {
        return dateStr;
    }
}

// Drag & Drop cross-window / tab state helpers
function getDraggedItem(e) {
    if (window.DRAGGED_DRIVE_ITEM) {
        return window.DRAGGED_DRIVE_ITEM;
    }
    if (e && e.dataTransfer) {
        try {
            const jsonStr = e.dataTransfer.getData('application/json');
            if (jsonStr) {
                const parsed = JSON.parse(jsonStr);
                if (parsed && parsed.path) return parsed;
            }
        } catch {}
        try {
            const txt = e.dataTransfer.getData('text/plain');
            if (txt && txt.startsWith('/')) {
                const parts = txt.split('/');
                return { path: txt, id: parts[parts.length - 1], name: 'Item' };
            }
        } catch {}
    }
    try {
        const saved = localStorage.getItem('tg_dragged_item');
        if (saved) {
            const parsed = JSON.parse(saved);
            if (parsed && parsed.path) return parsed;
        }
    } catch {}
    return null;
}

function clearDraggedItem() {
    window.DRAGGED_DRIVE_ITEM = null;
    try {
        localStorage.removeItem('tg_dragged_item');
    } catch {}
}

// Build Interactive Breadcrumbs with Proper Folder Names & Smooth Scrollable Navigation
function updateBreadcrumbs(breadcrumbs) {
    const container = document.getElementById('breadcrumbs-container');
    if (!container) return;

    const rawPath = getCurrentPath();
    const isTrash = rawPath.startsWith('/trash');
    const isSearch = rawPath.startsWith('/search');

    if (isTrash) {
        container.innerHTML = `<span class="gd-crumb gd-crumb-active" data-path="/trash"><span class="gd-crumb-root-icon"><svg viewBox="0 0 24 24"><path d="M15 4V3H9v1H4v2h1v13c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V6h1V4h-5zm2 15H7V6h10v13z"/></svg></span>Trash</span>`;
        document.title = 'Trash - Google Drive';
        return;
    }

    if (isSearch) {
        const q = rawPath.replace('/search_', '').replace('/search', '');
        const queryDecoded = decodeURIComponent(q);
        container.innerHTML = `
            <span class="gd-crumb gd-crumb-target" data-path="/" onclick="navigateToPath('/')">
                <span class="gd-crumb-root-icon"><svg viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg></span>My Drive
            </span>
            <span class="gd-crumb-sep">›</span>
            <span class="gd-crumb gd-crumb-active">Search: "${escapeHtml(queryDecoded)}"</span>
        `;
        document.title = `Search: "${queryDecoded}" - Google Drive`;
        return;
    }

    if (!breadcrumbs || breadcrumbs.length === 0) {
        breadcrumbs = [{ name: 'My Drive', path: '/', id: 'root' }];
    }

    CURRENT_BREADCRUMBS = breadcrumbs;

    // Update document title to the active folder name
    const currentFolder = breadcrumbs[breadcrumbs.length - 1];
    const currentFolderName = currentFolder ? (currentFolder.name || 'My Drive') : 'My Drive';
    document.title = `${currentFolderName} - Google Drive`;

    let html = '';
    for (let i = 0; i < breadcrumbs.length; i++) {
        const item = breadcrumbs[i];
        const isLast = i === breadcrumbs.length - 1;
        const displayName = item.name || (i === 0 ? 'My Drive' : item.id);
        const iconHtml = i === 0 ? `<span class="gd-crumb-root-icon"><svg viewBox="0 0 24 24"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg></span>` : '';

        if (i > 0) {
            html += `<span class="gd-crumb-sep">›</span>`;
        }

        if (isLast) {
            html += `<span class="gd-crumb gd-crumb-active gd-crumb-target" data-path="${item.path}" data-id="${item.id}" title="${escapeHtml(displayName)} (Current)">${iconHtml}${escapeHtml(displayName)}</span>`;
        } else {
            html += `<span class="gd-crumb gd-crumb-target" data-path="${item.path}" data-id="${item.id}" onclick="navigateToPath('${item.path}')" title="Jump to ${escapeHtml(displayName)}">${iconHtml}${escapeHtml(displayName)}</span>`;
        }
    }

    container.innerHTML = html;

    // Smoothly auto-scroll to the end so the deepest child node is always visible immediately
    setTimeout(() => {
        if (container.scrollWidth > container.clientWidth) {
            container.scrollTo({ left: container.scrollWidth, behavior: 'smooth' });
        }
    }, 40);

    // Mouse Wheel Horizontal Scroll Support (for desktop users with standard vertical mouse wheels)
    if (!container._wheelBound) {
        container._wheelBound = true;
        container.addEventListener('wheel', (e) => {
            if (e.deltaY !== 0) {
                e.preventDefault();
                container.scrollLeft += e.deltaY;
            }
        }, { passive: false });

        // Drag to scroll gestures
        let isDown = false;
        let startX;
        let scrollLeft;

        container.addEventListener('mousedown', (e) => {
            if (e.target.closest('.gd-crumb')) return; // Allow clicking crumbs directly
            isDown = true;
            startX = e.pageX - container.offsetLeft;
            scrollLeft = container.scrollLeft;
        });

        container.addEventListener('mouseleave', () => { isDown = false; });
        container.addEventListener('mouseup', () => { isDown = false; });
        container.addEventListener('mousemove', (e) => {
            if (!isDown) return;
            e.preventDefault();
            const x = e.pageX - container.offsetLeft;
            const walk = (x - startX) * 1.5;
            container.scrollLeft = scrollLeft - walk;
        });
    }

    // Attach Drag & Drop to each breadcrumb item (so user can drop files onto ancestor folders)
    container.querySelectorAll('.gd-crumb-target').forEach(crumbEl => {
        const targetPath = crumbEl.getAttribute('data-path');
        const targetId = crumbEl.getAttribute('data-id');
        if (!targetPath) return;

        crumbEl.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            crumbEl.classList.add('gd-crumb-drop-hover');
            e.dataTransfer.dropEffect = 'move';
        });

        crumbEl.addEventListener('dragleave', (e) => {
            crumbEl.classList.remove('gd-crumb-drop-hover');
        });

        crumbEl.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            crumbEl.classList.remove('gd-crumb-drop-hover');

            if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                showToast(`Uploading ${e.dataTransfer.files.length} file(s) into "${crumbEl.innerText.trim()}"...`);
                uploadFilesQueue(e.dataTransfer.files, targetPath);
            }
        });
    });
}

// Copy Current Folder Path to Clipboard
window.copyCurrentFolderPath = function() {
    let displayPath = '/';
    if (window.CURRENT_BREADCRUMBS && window.CURRENT_BREADCRUMBS.length > 0) {
        displayPath = '/' + window.CURRENT_BREADCRUMBS.filter(b => b.path !== '/').map(b => b.name || b.id).join('/');
    } else {
        const raw = getCurrentPath();
        displayPath = raw || '/';
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(displayPath).then(() => {
            showToast(`📋 Copied path: ${displayPath}`);
        }).catch(() => {
            fallbackCopy(displayPath);
        });
    } else {
        fallbackCopy(displayPath);
    }

    function fallbackCopy(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy');
            showToast(`📋 Copied path: ${text}`);
        } catch (err) {
            showToast(`Path: ${text}`);
        }
        document.body.removeChild(ta);
    }
};

// Drag & Drop Helpers for Items (Supports cross-window / browser-to-browser drag & drop)
function handleItemDragStart(e) {
    if (getCurrentPath().startsWith('/trash')) {
        e.preventDefault();
        return;
    }

    const id = this.getAttribute('data-id');
    const item = DIRECTORY_ITEMS[id];
    if (!item) return;

    const fullPath = (item.path + '/' + item.id).replaceAll('//', '/');
    const dragData = {
        id: item.id,
        name: item.name,
        path: fullPath,
        type: item.type,
        source: 'tg-drive'
    };

    window.DRAGGED_DRIVE_ITEM = dragData;

    try {
        const payloadStr = JSON.stringify(dragData);
        e.dataTransfer.setData('application/json', payloadStr);
        e.dataTransfer.setData('text/plain', fullPath);
        localStorage.setItem('tg_dragged_item', payloadStr);
    } catch {}

    e.dataTransfer.effectAllowed = 'move';
    this.classList.add('is-dragging');
}

function handleItemDragEnd(e) {
    this.classList.remove('is-dragging');
    clearDraggedItem();
    document.querySelectorAll('.gd-drop-hover, .gd-crumb-drop-hover, .gd-nav-drop-hover').forEach(el => {
        el.classList.remove('gd-drop-hover', 'gd-crumb-drop-hover', 'gd-nav-drop-hover');
    });
}

function handleFolderDragOver(e) {
    if (getCurrentPath().startsWith('/trash')) {
        e.preventDefault();
        return;
    }
    e.preventDefault();
    e.stopPropagation();
    this.classList.add('gd-drop-hover');
    e.dataTransfer.dropEffect = 'move';
}

function handleFolderDragLeave(e) {
    this.classList.remove('gd-drop-hover');
}

function handleFolderDrop(e) {
    if (getCurrentPath().startsWith('/trash')) {
        e.preventDefault();
        return;
    }
    e.preventDefault();
    e.stopPropagation();
    this.classList.remove('gd-drop-hover');

    const folderId = this.getAttribute('data-id');
    const folderItem = DIRECTORY_ITEMS[folderId];
    if (!folderItem) return;

    const targetFolderPath = (folderItem.path + '/' + folderItem.id).replaceAll('//', '/');
    const draggedItem = getDraggedItem(e);

    if (draggedItem) {
        if (draggedItem.id === folderId) {
            showToast('⚠️ Cannot move a folder into itself');
            clearDraggedItem();
            return;
        }
        if (draggedItem.path === targetFolderPath) {
            clearDraggedItem();
            return;
        }
        moveFileFolder(draggedItem.path, targetFolderPath);
        clearDraggedItem();
    } else if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        showToast(`Uploading ${e.dataTransfer.files.length} file(s) into "${folderItem.name}"...`);
        uploadFilesQueue(e.dataTransfer.files, targetFolderPath);
    }
}

// Skeleton Loader for Instant Navigation Feedback
function showDirectorySkeleton() {
    const tableBody = document.getElementById('directory-data');
    const gridFolders = document.getElementById('grid-folders-data');
    const gridFiles = document.getElementById('grid-files-data');
    const gridFoldersTitle = document.getElementById('grid-folders-title');
    const gridFilesTitle = document.getElementById('grid-files-title');
    const errorState = document.getElementById('directory-error-state');

    if (errorState) errorState.style.display = 'none';

    if (tableBody) {
        let rowsHtml = '';
        for (let i = 0; i < 6; i++) {
            rowsHtml += `
                <tr class="gd-skeleton-row">
                    <td class="col-select-td"><div class="gd-skeleton-box sm"></div></td>
                    <td class="col-name-td">
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <div class="gd-skeleton-box icon"></div>
                            <div class="gd-skeleton-box title" style="width: ${40 + (i % 4) * 15}%;"></div>
                        </div>
                    </td>
                    <td class="col-type-td"><div class="gd-skeleton-box pill"></div></td>
                    <td class="col-owner-td"><div class="gd-skeleton-box pill"></div></td>
                    <td class="col-date-td"><div class="gd-skeleton-box text"></div></td>
                    <td class="col-size-td"><div class="gd-skeleton-box text-sm"></div></td>
                    <td class="col-more-td"></td>
                </tr>
            `;
        }
        tableBody.innerHTML = rowsHtml;
    }

    if (gridFolders && gridFoldersTitle) {
        gridFoldersTitle.style.display = 'block';
        let foldersHtml = '';
        for (let i = 0; i < 4; i++) {
            foldersHtml += `
                <div class="gd-skeleton-folder-card">
                    <div class="gd-skeleton-box folder-icon"></div>
                    <div class="gd-skeleton-box folder-title" style="width: ${50 + (i % 3) * 15}%;"></div>
                </div>
            `;
        }
        gridFolders.innerHTML = foldersHtml;
    }

    if (gridFiles && gridFilesTitle) {
        gridFilesTitle.style.display = 'block';
        let filesHtml = '';
        for (let i = 0; i < 6; i++) {
            filesHtml += `
                <div class="gd-skeleton-file-card">
                    <div class="gd-skeleton-box file-thumb"></div>
                    <div class="gd-skeleton-file-info">
                        <div class="gd-skeleton-box file-title" style="width: ${60 + (i % 3) * 15}%;"></div>
                        <div class="gd-skeleton-box file-sub"></div>
                    </div>
                </div>
            `;
        }
        gridFiles.innerHTML = filesHtml;
    }

    const countsEl = document.getElementById('gd-status-counts');
    if (countsEl) countsEl.innerText = 'Loading folder contents...';
}

function showDirectoryError(message, targetPath) {
    const tableBody = document.getElementById('directory-data');
    const gridFolders = document.getElementById('grid-folders-data');
    const gridFiles = document.getElementById('grid-files-data');
    const gridFoldersTitle = document.getElementById('grid-folders-title');
    const gridFilesTitle = document.getElementById('grid-files-title');
    const errorState = document.getElementById('directory-error-state');

    if (tableBody) tableBody.innerHTML = '';
    if (gridFolders) gridFolders.innerHTML = '';
    if (gridFiles) gridFiles.innerHTML = '';
    if (gridFoldersTitle) gridFoldersTitle.style.display = 'none';
    if (gridFilesTitle) gridFilesTitle.style.display = 'none';

    if (errorState) {
        errorState.style.display = 'flex';
        errorState.innerHTML = `
            <div class="gd-error-card">
                <div class="gd-error-icon">⚠️</div>
                <div class="gd-error-title">Unable to access folder</div>
                <div class="gd-error-desc">${escapeHtml(message || 'The requested folder could not be retrieved.')}</div>
                <div style="display: flex; gap: 10px; justify-content: center; flex-wrap: wrap;">
                    <button class="gd-retry-btn" onclick="if(typeof getCurrentDirectory==='function') getCurrentDirectory();">
                        <svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor;"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
                        <span>Try Again</span>
                    </button>
                    <button class="gd-btn gd-btn-outline" style="border: 1px solid #dadce0; border-radius: 20px; padding: 8px 16px; background: #fff; cursor: pointer; font-size: 0.88rem; font-weight: 500;" onclick="navigateToPath('/')">
                        <span>Go to My Drive</span>
                    </button>
                </div>
            </div>
        `;
    }

    const countsEl = document.getElementById('gd-status-counts');
    if (countsEl) countsEl.innerText = 'Folder access error';
}

function updateStatusBar(foldersCount, filesCount, totalSize) {
    const countsEl = document.getElementById('gd-status-counts');
    const sortEl = document.getElementById('gd-status-sort');
    const viewEl = document.getElementById('gd-status-view');

    if (countsEl) {
        const totalItems = (foldersCount || 0) + (filesCount || 0);
        if (totalItems === 0) {
            countsEl.innerText = 'Empty directory';
        } else {
            const parts = [];
            if (foldersCount > 0) parts.push(`${foldersCount} folder${foldersCount === 1 ? '' : 's'}`);
            if (filesCount > 0) parts.push(`${filesCount} file${filesCount === 1 ? '' : 's'}`);
            let text = parts.join(', ');
            if (totalSize && totalSize > 0) {
                text += ` (${convertBytes(totalSize)})`;
            }
            countsEl.innerText = text;
        }
    }

    if (sortEl && window.CURRENT_SORT) {
        const keyName = window.CURRENT_SORT.key === 'name' ? 'Name' :
                        window.CURRENT_SORT.key === 'date' ? 'Date modified' :
                        window.CURRENT_SORT.key === 'size' ? 'File size' : 'Type';
        const orderName = window.CURRENT_SORT.order === 'asc' ? 'A→Z' : 'Z→A';
        sortEl.innerText = `${keyName} (${orderName})`;
    }

    if (viewEl) {
        viewEl.innerText = (CURRENT_VIEW_MODE === 'grid') ? 'Grid layout' : 'List layout';
    }
}

// Main Directory Renderer
function showDirectory(data, breadcrumbs) {
    CURRENT_DIRECTORY_DATA = data;
    window.CURRENT_BREADCRUMBS = breadcrumbs || window.CURRENT_BREADCRUMBS || [];
    const contents = data ? (data['contents'] || {}) : {};
    DIRECTORY_ITEMS = contents;
    const isTrash = getCurrentPath().startsWith('/trash');

    const errorState = document.getElementById('directory-error-state');
    if (errorState) errorState.style.display = 'none';

    if (window.CURRENT_PAGE_VIEW !== 'sync') {
        updateBreadcrumbs(window.CURRENT_BREADCRUMBS);
    }

    const tableBody = document.getElementById('directory-data');
    const gridFolders = document.getElementById('grid-folders-data');
    const gridFiles = document.getElementById('grid-files-data');
    const gridFoldersTitle = document.getElementById('grid-folders-title');
    const gridFilesTitle = document.getElementById('grid-files-title');

    tableBody.innerHTML = '';
    gridFolders.innerHTML = '';
    gridFiles.innerHTML = '';

    // Clear pending thumbnail queue when navigating to a new folder
    THUMB_QUEUE.length = 0;
    THUMB_ACTIVE_COUNT = 0;

    let entries = Object.entries(contents);

    // Apply Filter Chips (All, Folders, Images, Videos, Audio, PDFs, Code, Archives)
    if (window.CURRENT_FILTER && window.CURRENT_FILTER !== 'all') {
        entries = entries.filter(([key, value]) => {
            if (window.CURRENT_FILTER === 'folder') return value.type === 'folder';
            if (value.type !== 'file') return false;
            const ext = (value.name && value.name.includes('.')) ? value.name.split('.').pop().toLowerCase() : '';
            const cat = (value.category || '').toLowerCase();
            if (window.CURRENT_FILTER === 'image') return ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff', 'avif', 'heic', 'heif'].includes(ext) || cat === 'image';
            if (window.CURRENT_FILTER === 'video') return ['mp4', 'mkv', 'webm', 'mov', 'avi', 'ts', 'ogv', 'm4v', '3gp', 'flv'].includes(ext) || cat === 'video';
            if (window.CURRENT_FILTER === 'audio') return ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'opus', 'wma'].includes(ext) || cat === 'audio';
            if (window.CURRENT_FILTER === 'pdf') return ext === 'pdf' || cat === 'pdf';
            if (window.CURRENT_FILTER === 'code') return ['txt', 'md', 'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'scss', 'json', 'xml', 'csv', 'log', 'sh', 'bat', 'yaml', 'yml', 'c', 'cpp', 'h', 'java', 'rs', 'go', 'sql', 'env'].includes(ext) || cat === 'code';
            if (window.CURRENT_FILTER === 'archive') return ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'iso'].includes(ext) || cat === 'archive';
            return true;
        });
    }

    let folders = entries.filter(([key, value]) => value.type === 'folder');
    let files = entries.filter(([key, value]) => value.type === 'file');

    // Calculate directory stats for status bar
    const totalFilesSize = files.reduce((acc, [k, f]) => acc + (Number(f.size) || 0), 0);
    updateStatusBar(folders.length, files.length, totalFilesSize);

    // Multi-Criteria Sorting Engine (Name, Date Modified, File Size, Type)
    const sortMultiplier = (window.CURRENT_SORT.order === 'desc') ? -1 : 1;
    const sortComparator = (a, b) => {
        const itemA = a[1];
        const itemB = b[1];
        if (window.CURRENT_SORT.key === 'name') {
            return sortMultiplier * itemA.name.localeCompare(itemB.name, undefined, { numeric: true, sensitivity: 'base' });
        } else if (window.CURRENT_SORT.key === 'date') {
            const dateA = new Date(itemA.upload_date || 0).getTime();
            const dateB = new Date(itemB.upload_date || 0).getTime();
            return sortMultiplier * (dateA - dateB);
        } else if (window.CURRENT_SORT.key === 'size') {
            const sizeA = Number(itemA.size) || 0;
            const sizeB = Number(itemB.size) || 0;
            return sortMultiplier * (sizeA - sizeB);
        } else if (window.CURRENT_SORT.key === 'type') {
            const typeA = itemA.type === 'folder' ? 'Folder' : (itemA.category || itemA.name.split('.').pop() || '');
            const typeB = itemB.type === 'folder' ? 'Folder' : (itemB.category || itemB.name.split('.').pop() || '');
            return sortMultiplier * typeA.localeCompare(typeB);
        }
        return 0;
    };

    folders.sort(sortComparator);
    files.sort(sortComparator);

    // Update Header Sort Indicator Arrows
    ['name', 'type', 'date', 'size'].forEach(col => {
        const ind = document.getElementById(`sort-ind-${col}`);
        if (ind) {
            if (window.CURRENT_SORT.key === col) {
                ind.innerText = window.CURRENT_SORT.order === 'asc' ? '▲' : '▼';
            } else {
                ind.innerText = '';
            }
        }
    });

    // Update Sort Dropdown Active Option
    document.querySelectorAll('.gd-sort-option').forEach(opt => {
        const sKey = opt.getAttribute('data-sort');
        const sOrder = opt.getAttribute('data-order');
        if (sKey === window.CURRENT_SORT.key && sOrder === window.CURRENT_SORT.order) {
            opt.classList.add('active');
        } else {
            opt.classList.remove('active');
        }
    });

    // Handle Enhanced Empty State
    if (folders.length === 0 && files.length === 0) {
        const isFiltered = window.CURRENT_FILTER && window.CURRENT_FILTER !== 'all';
        const emptyHtml = `
            <tr>
                <td colspan="7" style="text-align: center; padding: 48px 20px;">
                    <div class="gd-empty-card">
                        <div class="gd-empty-icon-wrap">${isFiltered ? '🔍' : '📂'}</div>
                        <div class="gd-empty-title">${isFiltered ? 'No matching items found' : 'This folder is empty'}</div>
                        <div class="gd-empty-desc">${isFiltered ? 'Try selecting a different filter above to find what you are looking for.' : 'Use "+ New" button or drag & drop files here to upload instantly.'}</div>
                        ${!isFiltered ? `
                        <div class="gd-empty-actions">
                            <button class="gd-retry-btn" onclick="const fi = document.getElementById('fileInput'); if (fi) fi.click();">
                                <svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor;"><path d="M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z"/></svg>
                                <span>Upload Files</span>
                            </button>
                        </div>` : ''}
                    </div>
                </td>
            </tr>
        `;
        tableBody.innerHTML = emptyHtml;
        gridFolders.innerHTML = `
            <div style="grid-column: 1/-1;">
                <div class="gd-empty-card">
                    <div class="gd-empty-icon-wrap">${isFiltered ? '🔍' : '📂'}</div>
                    <div class="gd-empty-title">${isFiltered ? 'No matching items found' : 'This folder is empty'}</div>
                    <div class="gd-empty-desc">${isFiltered ? 'Try selecting a different filter above.' : 'Drag & drop files or click "+ New" to upload.'}</div>
                </div>
            </div>
        `;
        gridFoldersTitle.style.display = 'none';
        gridFilesTitle.style.display = 'none';
        return;
    }

    gridFoldersTitle.style.display = folders.length ? 'block' : 'none';
    gridFilesTitle.style.display = files.length ? 'block' : 'none';

function getItemProvenance(item) {
    const rawPath = item.human_path || item.display_path || item.path || '/';
    let cleanPath = (rawPath || '/').replaceAll('//', '/');
    if (cleanPath !== '/' && cleanPath.endsWith('/')) {
        cleanPath = cleanPath.slice(0, -1);
    }
    
    // Determine parent folder for display
    let parentPath = item.display_path || '/';
    if (!item.display_path && item.human_path) {
        const parts = item.human_path.split('/').filter(Boolean);
        if (parts.length > 1) {
            parentPath = '/' + parts.slice(0, -1).join('/');
        } else {
            parentPath = '/';
        }
    }

    const lower = (cleanPath + ' ' + (item.device || '') + ' ' + (item.name || '')).toLowerCase();
    const isMobile = lower.includes('oneplus') || lower.includes('mobile') || lower.includes('phone') || lower.includes('android') || lower.includes('shared storage');
    const isPC = lower.includes('computer') || lower.includes('c_drive') || lower.includes('d_drive') || lower.includes('windows') || lower.includes('desktop') || lower.includes('pc');

    let badge = '';
    if (isMobile) {
        badge = `<span class="gd-device-pill gd-device-mobile" title="Phone Backup"><svg viewBox="0 0 24 24" style="width:11px;height:11px;fill:currentColor;"><path d="M17 1.01L7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14z"/></svg> Phone</span>`;
    } else if (isPC) {
        badge = `<span class="gd-device-pill gd-device-pc" title="Computer / PC Backup"><svg viewBox="0 0 24 24" style="width:11px;height:11px;fill:currentColor;"><path d="M20 18c1.1 0 1.99-.9 1.99-2L22 6c0-1.1-.9-2-2-2H4c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2H0v2h24v-2h-4zM4 6h16v10H4V6z"/></svg> PC</span>`;
    }

    return {
        parentPath: parentPath,
        fullPath: cleanPath,
        badge: badge
    };
}

    let tableHtml = '';
    let menusHtml = '';

    // 1. Render Folders
    for (const [key, item] of folders) {
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const owner = item.owner || 'Admin';
        const tags = Array.isArray(item.tags) ? item.tags : [];
        const folderHasSize = typeof item.size === 'number' && item.size > 0;
        const folderItemCount = (item.file_count || 0);
        const folderSize = folderHasSize 
            ? convertBytes(item.size) 
            : (folderItemCount > 0 ? `${folderItemCount} item${folderItemCount === 1 ? '' : 's'}` : '0 B');
        const folderTooltip = folderHasSize
            ? `${(item.size || 0).toLocaleString()} bytes (${folderItemCount} file${folderItemCount === 1 ? '' : 's'})`
            : `${folderItemCount} file${folderItemCount === 1 ? '' : 's'}`;

        const isSearch = getCurrentPath().startsWith('/search_');
        const prov = getItemProvenance(item);

        const tagsHtml = tags.length ? tags.map(t => `<span class="gd-tag-badge" onclick="event.stopPropagation(); navigateToPath('/tags/${encodeURIComponent(t)}');">🏷️ ${escapeHtml(t)}</span>`).join('') : '';

        // Table Row
        tableHtml += `
            <tr draggable="false" data-path="${item.path}" data-id="${item.id}" data-name="${escapeHtml(item.name)}" class="body-tr folder-tr ${window.SELECTED_ITEMS.has(item.id) ? 'is-selected' : ''}">
                <td class="col-select-td" onclick="event.stopPropagation();">
                    <input type="checkbox" class="gd-checkbox item-select-checkbox" data-id="${item.id}" ${window.SELECTED_ITEMS.has(item.id) ? 'checked' : ''} />
                </td>
                <td class="col-name-td">
                    <div class="td-align file-name-cell" style="${isSearch ? 'flex-direction: column; align-items: flex-start; justify-content: center; gap: 2px;' : ''}">
                        <div style="display: flex; align-items: center; gap: 10px; width: 100%;">
                            ${badge}
                            <span class="file-name-text" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
                            ${tagsHtml}
                        </div>
                        ${isSearch ? `
                        <div class="gd-search-path-subline" style="margin-left: 58px;">
                            ${prov.badge}
                            <span class="gd-search-path-text" title="Stored in: ${escapeHtml(prov.parentPath)}">📁 ${escapeHtml(prov.parentPath)}</span>
                        </div>` : ''}
                    </div>
                </td>
                <td class="col-type-td"><div class="td-align"><span class="type-pill pill-folder">Folder</span></div></td>
                <td class="col-owner-td"><div class="td-align"><span class="owner-pill">${escapeHtml(owner)}</span></div></td>
                <td class="col-date-td"><div class="td-align date-text">${dateStr}</div></td>
                <td class="col-size-td"><div class="td-align size-text" title="${folderTooltip}">${folderSize}</div></td>
                <td class="col-more-td">
                    <div class="td-align td-actions">
                        <a data-id="${item.id}" class="more-btn" title="More actions"><img src="static/assets/more-icon.svg"></a>
                    </div>
                </td>
            </tr>
        `;

        // Grid Folder Chip
        const folderChip = document.createElement('div');
        folderChip.className = `gd-folder-chip folder-tr ${isSearch ? 'is-search-mode' : ''} ${window.SELECTED_ITEMS.has(item.id) ? 'is-selected' : ''}`;
        folderChip.setAttribute('draggable', 'false');
        folderChip.setAttribute('data-id', item.id);
        folderChip.setAttribute('data-path', item.path);
        folderChip.setAttribute('data-name', item.name);
        folderChip.innerHTML = `
            <div class="gd-card-select-btn ${window.SELECTED_ITEMS.has(item.id) ? 'checked' : ''}" data-id="${item.id}" title="Select">
                <svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
            </div>
            <div class="gd-folder-chip-left">
                <img class="item-icon-img" src="static/assets/folder-solid-icon.svg">
                <div style="min-width:0;display:flex;flex-direction:column;gap:2px;overflow:hidden;flex:1;">
                    <span class="gd-folder-chip-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
                    <span class="gd-folder-chip-size" title="${folderTooltip}">${folderSize}</span>
                    ${isSearch ? `
                    <div class="gd-search-path-subline" style="margin-top:0;">
                        ${prov.badge}
                        <span class="gd-search-path-text" title="Stored in: ${escapeHtml(prov.parentPath)}">${escapeHtml(prov.parentPath)}</span>
                    </div>` : ''}
                </div>
            </div>
            <a data-id="${item.id}" class="more-btn" title="More actions"><img src="static/assets/more-icon.svg"></a>
        `;
        gridFolders.appendChild(folderChip);

        // Context / More Menus
        if (isTrash) {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete permanently</div></div>`;
        } else {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="download-zip-opt-${item.id}"><svg style="width:15px;height:15px;margin-right:8px;vertical-align:-2px;fill:currentColor;" viewBox="0 0 24 24"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z"/></svg> Download as ZIP</div><hr><div id="tags-opt-${item.id}"><svg style="width:15px;height:15px;margin-right:8px;vertical-align:-2px;fill:currentColor;" viewBox="0 0 24 24"><path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.42l9 9c.36.36.86.58 1.41.58.55 0 1.05-.22 1.41-.59l7-7c.37-.36.59-.86.59-1.41 0-.55-.23-1.06-.59-1.42zM5.5 7C4.67 7 4 6.33 4 5.5S4.67 4 5.5 4 7 4.67 7 5.5 6.33 7 5.5 7z"/></svg> Manage Tags</div><hr><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="move-${item.id}"><img src="static/assets/folder-solid-icon.svg"> Move to...</div><hr><div id="copy-${item.id}"><img src="static/assets/copy-icon.svg"> Make a copy</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="folder-share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
        }
    }

    // 2. Render Files
    for (const [key, item] of files) {
        const size = convertBytes(item.size);
        const sizeTooltip = `${(item.size || 0).toLocaleString()} bytes`;
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const category = item.category || 'File';
        const owner = item.owner || 'Admin';
        const tags = Array.isArray(item.tags) ? item.tags : [];

        const isSearch = getCurrentPath().startsWith('/search_');
        const prov = getItemProvenance(item);

        const tagsHtml = tags.length ? tags.map(t => `<span class="gd-tag-badge" onclick="event.stopPropagation(); navigateToPath('/tags/${encodeURIComponent(t)}');">🏷️ ${escapeHtml(t)}</span>`).join('') : '';

        // Table Row
        tableHtml += `
            <tr draggable="false" data-path="${item.path}" data-id="${item.id}" data-name="${escapeHtml(item.name)}" class="body-tr file-tr ${window.SELECTED_ITEMS.has(item.id) ? 'is-selected' : ''}">
                <td class="col-select-td" onclick="event.stopPropagation();">
                    <input type="checkbox" class="gd-checkbox item-select-checkbox" data-id="${item.id}" ${window.SELECTED_ITEMS.has(item.id) ? 'checked' : ''} />
                </td>
                <td class="col-name-td">
                    <div class="td-align file-name-cell" style="${isSearch ? 'flex-direction: column; align-items: flex-start; justify-content: center; gap: 2px;' : ''}">
                        <div style="display: flex; align-items: center; gap: 10px; width: 100%;">
                            ${badge}
                            <span class="file-name-text" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
                            ${tagsHtml}
                        </div>
                        ${isSearch ? `
                        <div class="gd-search-path-subline" style="margin-left: 58px;">
                            ${prov.badge}
                            <span class="gd-search-path-text" title="Stored in: ${escapeHtml(prov.parentPath)}">📁 ${escapeHtml(prov.parentPath)}</span>
                        </div>` : ''}
                    </div>
                </td>
                <td class="col-type-td"><div class="td-align"><span class="type-pill">${category}</span></div></td>
                <td class="col-owner-td"><div class="td-align"><span class="owner-pill">${escapeHtml(owner)}</span></div></td>
                <td class="col-date-td"><div class="td-align date-text">${dateStr}</div></td>
                <td class="col-size-td"><div class="td-align size-text" title="${sizeTooltip}">${size}</div></td>
                <td class="col-more-td">
                    <div class="td-align td-actions">
                        <a data-id="${item.id}" class="more-btn" title="More actions"><img src="static/assets/more-icon.svg"></a>
                    </div>
                </td>
            </tr>
        `;

        // Grid File Card
        const fileCard = document.createElement('div');
        fileCard.className = `gd-file-card file-tr ${window.SELECTED_ITEMS.has(item.id) ? 'is-selected' : ''}`;
        fileCard.setAttribute('draggable', 'false');
        fileCard.setAttribute('data-id', item.id);
        fileCard.setAttribute('data-path', item.path);
        fileCard.setAttribute('data-name', item.name);

        const ext = (item.name && item.name.includes('.')) ? item.name.split('.').pop().toLowerCase() : '';
        const isMedia = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'heic', 'tiff', 'mp4', 'mkv', 'webm', 'mov', 'avi', '3gp'].includes(ext);
        const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
        const authParam = new URLSearchParams(window.location.search).get('auth');
        const pwd = localStorage.getItem('password') || '';
        const thumbUrl = `/thumbnail?path=${encodeURIComponent(filePath)}${authParam ? `&auth=${encodeURIComponent(authParam)}` : ''}${pwd ? `&password=${encodeURIComponent(pwd)}` : ''}`;

        let previewInnerHtml = '';
        if (isMedia) {
            previewInnerHtml = `
                <div class="gd-thumb-shimmer"></div>
                <img class="gd-file-card-thumb" src="${thumbUrl}" loading="lazy" alt="${escapeHtml(item.name)}" 
                    onload="this.classList.add('loaded'); if (this.previousElementSibling) this.previousElementSibling.style.display='none';" 
                    onerror="this.style.display='none'; if (this.previousElementSibling) this.previousElementSibling.style.display='none'; if (this.nextElementSibling) this.nextElementSibling.style.display='flex';" />
                <div class="gd-thumb-fallback" style="display: none;">
                    <span style="font-size: 2.2rem;">${getBigIconEmoji(item)}</span>
                </div>
            `;
        } else {
            previewInnerHtml = `
                <div class="gd-ext-badge-card badge-${escapeHtml(ext || 'file')}">
                    <span class="gd-ext-icon">${getBigIconEmoji(item)}</span>
                    <span class="gd-ext-label">${escapeHtml(ext.toUpperCase() || 'FILE')}</span>
                </div>
            `;
        }

        fileCard.innerHTML = `
            <div class="gd-file-card-preview">
                <div class="gd-card-select-btn ${window.SELECTED_ITEMS.has(item.id) ? 'checked' : ''}" data-id="${item.id}" title="Select">
                    <svg viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>
                </div>
                ${previewInnerHtml}
                <a data-id="${item.id}" class="more-btn gd-file-card-more-btn" title="More actions" onclick="event.stopPropagation();"><img src="static/assets/more-icon.svg"></a>
            </div>
            <div class="gd-file-card-body">
                <div class="gd-file-card-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
                <div class="gd-file-card-meta">
                    <span>${category}</span>
                    <span>${size}</span>
                </div>
                ${isSearch ? `
                <div class="gd-search-path-subline" style="margin-top: 4px;">
                    ${prov.badge}
                    <span class="gd-search-path-text" title="Stored in: ${escapeHtml(prov.parentPath)}">${escapeHtml(prov.parentPath)}</span>
                </div>` : ''}
            </div>
        `;
        gridFiles.appendChild(fileCard);

        // Context / More Menus
        if (isTrash) {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete permanently</div></div>`;
        } else {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="download-opt-${item.id}"><svg style="width:15px;height:15px;margin-right:8px;vertical-align:-2px;fill:currentColor;" viewBox="0 0 24 24"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z"/></svg> Download</div><hr><div id="preview-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Preview / Open</div><hr><div id="tags-opt-${item.id}"><svg style="width:15px;height:15px;margin-right:8px;vertical-align:-2px;fill:currentColor;" viewBox="0 0 24 24"><path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.42l9 9c.36.36.86.58 1.41.58.55 0 1.05-.22 1.41-.59l7-7c.37-.36.59-.86.59-1.41 0-.55-.23-1.06-.59-1.42zM5.5 7C4.67 7 4 6.33 4 5.5S4.67 4 5.5 4 7 4.67 7 5.5 6.33 7 5.5 7z"/></svg> Manage Tags</div><hr><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="move-${item.id}"><img src="static/assets/folder-solid-icon.svg"> Move to...</div><hr><div id="copy-${item.id}"><img src="static/assets/copy-icon.svg"> Make a copy</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
        }
    }

    tableBody.innerHTML = tableHtml;
    const ctxContainer = document.getElementById('context-menus-container');
    if (ctxContainer) ctxContainer.innerHTML = menusHtml;


    // Attach Selection, Click, Double Click, and Right-Click Context Menu Events
    document.querySelectorAll('.item-select-checkbox').forEach(cb => {
        cb.onchange = function (e) {
            e.stopPropagation();
            toggleItemSelection(this.getAttribute('data-id'));
        };
    });

    document.querySelectorAll('.gd-card-select-btn').forEach(btn => {
        btn.onclick = function (e) {
            e.stopPropagation();
            toggleItemSelection(this.getAttribute('data-id'));
        };
    });

    // Folders Event Attachment
    document.querySelectorAll('.folder-tr').forEach(el => {
        const id = el.getAttribute('data-id');
        el.ondblclick = openFolder;
        el.onclick = function (e) {
            if (e.target.closest('.more-btn') || e.target.closest('.col-select-td') || e.target.closest('.gd-card-select-btn')) return;
            if (e.shiftKey && window.LAST_SELECTED_ID) {
                rangeSelectItems(window.LAST_SELECTED_ID, id);
            } else {
                window.LAST_SELECTED_ID = id;
                selectItem(id);
                if (!isTrash) openFolder.call(this);
            }
        };
        el.oncontextmenu = function (e) {
            e.preventDefault();
            e.stopPropagation();
            selectItem(id);
            openContextMenuAt(id, e.clientX, e.clientY);
        };
    });

    // Files Event Attachment
    document.querySelectorAll('.file-tr').forEach(el => {
        const id = el.getAttribute('data-id');
        el.ondblclick = openFilePreview;
        el.onclick = function (e) {
            if (e.target.closest('.more-btn') || e.target.closest('.col-select-td') || e.target.closest('.gd-card-select-btn')) return;
            if (e.shiftKey && window.LAST_SELECTED_ID) {
                rangeSelectItems(window.LAST_SELECTED_ID, id);
            } else {
                window.LAST_SELECTED_ID = id;
                selectItem(id);
                if (!isTrash) openFilePreview.call(this);
            }
        };
        el.oncontextmenu = function (e) {
            e.preventDefault();
            e.stopPropagation();
            selectItem(id);
            openContextMenuAt(id, e.clientX, e.clientY);
        };
    });

    document.querySelectorAll('.more-btn').forEach(div => {
        div.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openMoreButton(div);
        });
    });

    updateBulkActionBar();
}

// Global Storage Stats Updater for Sidebar Widget
function updateSidebarStorageStats(stats) {
    if (!stats) return;
    const countEl = document.getElementById('storage-total-count');
    const sizeEl = document.getElementById('storage-total-size');
    const fillEl = document.getElementById('storage-progress-fill');
    const totalFiles = stats.total_files || 0;
    const totalBytes = stats.total_bytes || 0;

    if (countEl) {
        countEl.innerHTML = `<strong>${totalFiles.toLocaleString()} Files Uploaded</strong>`;
    }
    if (sizeEl) {
        sizeEl.innerText = `${convertBytes(totalBytes)} • Unlimited Cloud`;
    }
    if (fillEl) {
        fillEl.style.width = '100%';
    }
}

// Select Item and Populate Inspector Panel (Only opens when explicitly pressed)
function selectItem(id) {
    SELECTED_ITEM_ID = id;
    const item = DIRECTORY_ITEMS[id];
    if (!item) return;

    // Highlight row
    document.querySelectorAll('.body-tr, .gd-folder-chip, .gd-file-card').forEach(el => {
        if (el.getAttribute('data-id') === id) {
            el.classList.add('selected');
        } else {
            el.classList.remove('selected');
        }
    });

    // Populate Inspector
    const isFolder = item.type === 'folder';
    const rootUrl = getRootUrl();
    const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
    const directUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath) : `${rootUrl}/file?path=${encodeURIComponent(filePath)}`;

    // Build human-readable location path
    let readableLocation = 'My Drive';
    const isSearch = getCurrentPath().startsWith('/search_');
    if (isSearch && (item.display_path || item.human_path)) {
        const prov = (typeof getItemProvenance === 'function') ? getItemProvenance(item) : { parentPath: item.display_path || '/' };
        const devPrefix = item.device ? `[${item.device}] ` : '';
        readableLocation = `${devPrefix}${prov.parentPath}`;
    } else if (CURRENT_BREADCRUMBS && CURRENT_BREADCRUMBS.length > 0) {
        readableLocation = CURRENT_BREADCRUMBS.map(c => c.name).join(' / ');
    }

    const headerTitle = document.getElementById('insp-header-title');
    if (headerTitle) headerTitle.innerText = isFolder ? 'Folder Details' : 'File Details';

    const filenameEl = document.getElementById('insp-filename');
    if (filenameEl) filenameEl.innerText = item.name;
    const bigIconEl = document.getElementById('insp-big-icon');
    if (bigIconEl) bigIconEl.innerText = getBigIconEmoji(item);
    const propTypeEl = document.getElementById('insp-prop-type');
    if (propTypeEl) propTypeEl.innerText = item.category || (isFolder ? 'Folder' : 'File');
    const propSizeEl = document.getElementById('insp-prop-size');
    if (propSizeEl) {
        if (isFolder) {
            const fSize = item.size || 0;
            const fCount = item.file_count || 0;
            propSizeEl.innerText = `${convertBytes(fSize)} (${fSize.toLocaleString()} bytes • ${fCount} file${fCount === 1 ? '' : 's'})`;
        } else {
            propSizeEl.innerText = `${convertBytes(item.size)} (${(item.size || 0).toLocaleString()} bytes)`;
        }
    }
    const propStorageEl = document.getElementById('insp-prop-storage');
    if (propStorageEl) {
        propStorageEl.innerText = isFolder 
            ? ((item.size || 0) > 0 ? convertBytes(item.size) : '0 bytes (virtual)') 
            : convertBytes(item.size);
    }
    const propLocationEl = document.getElementById('insp-prop-location');
    if (propLocationEl) propLocationEl.innerText = readableLocation;
    const propOwnerEl = document.getElementById('insp-prop-owner');
    if (propOwnerEl) propOwnerEl.innerText = item.owner || 'Admin (You)';
    const propDateEl = document.getElementById('insp-prop-date');
    if (propDateEl) propDateEl.innerText = item.upload_date || '--';
    const propMsgIdEl = document.getElementById('insp-prop-msg-id');
    if (propMsgIdEl) propMsgIdEl.innerText = item.file_id ? `#${item.file_id}` : (isFolder ? 'Virtual' : '--');

    const linkInput = document.getElementById('insp-link-input');
    if (linkInput) {
        linkInput.value = isFolder ? `${rootUrl}/?path=${filePath}` : directUrl;
    }
}

// Switch between List View and Grid View
function applyViewMode(mode) {
    CURRENT_VIEW_MODE = mode;
    localStorage.setItem('gd_view_mode', mode);

    const listContainer = document.getElementById('list-view-container');
    const gridContainer = document.getElementById('grid-view-container');
    const listBtn = document.getElementById('toggle-list-view');
    const gridBtn = document.getElementById('toggle-grid-view');

    if (window.CURRENT_PAGE_VIEW === 'sync') {
        if (listBtn && gridBtn) {
            if (mode === 'grid') {
                listBtn.removeAttribute('active');
                gridBtn.setAttribute('active', '');
            } else {
                gridBtn.removeAttribute('active');
                listBtn.setAttribute('active', '');
            }
        }
        return;
    }

    if (mode === 'grid') {
        if (listContainer) listContainer.style.display = 'none';
        if (gridContainer) gridContainer.style.display = 'block';
        if (listBtn) listBtn.removeAttribute('active');
        if (gridBtn) gridBtn.setAttribute('active', '');
    } else {
        if (listContainer) listContainer.style.display = 'block';
        if (gridContainer) gridContainer.style.display = 'none';
        if (gridBtn) gridBtn.removeAttribute('active');
        if (listBtn) listBtn.setAttribute('active', '');
    }
}

// Copy text from in-app code/text preview
function copyPreviewText() {
    const codeEl = document.getElementById('preview-text-code');
    if (codeEl && codeEl.innerText) {
        copyTextToClipboard(codeEl.innerText);
        showToast('Text content copied to clipboard! 📋');
    }
}

// Multi-Select & Bulk Actions State Management
window.SELECTED_ITEMS = new Map();
window.LAST_SELECTED_ID = null;
window.CURRENT_SORT = { key: 'name', order: 'asc' };
window.CURRENT_FILTER = 'all';

function setDirectorySort(key, order) {
    if (order) {
        window.CURRENT_SORT = { key, order };
    } else {
        if (window.CURRENT_SORT.key === key) {
            window.CURRENT_SORT.order = window.CURRENT_SORT.order === 'asc' ? 'desc' : 'asc';
        } else {
            window.CURRENT_SORT = { key, order: (key === 'name' ? 'asc' : 'desc') };
        }
    }
    if (typeof CURRENT_DIRECTORY_DATA !== 'undefined' && CURRENT_DIRECTORY_DATA) {
        showDirectory(CURRENT_DIRECTORY_DATA, window.CURRENT_BREADCRUMBS || []);
    }
}

function setDirectoryFilter(filterType) {
    window.CURRENT_FILTER = filterType;
    document.querySelectorAll('.gd-filter-chip').forEach(chip => {
        if (chip.getAttribute('data-filter') === filterType) {
            chip.classList.add('active');
        } else {
            chip.classList.remove('active');
        }
    });
    if (typeof CURRENT_DIRECTORY_DATA !== 'undefined' && CURRENT_DIRECTORY_DATA) {
        showDirectory(CURRENT_DIRECTORY_DATA, window.CURRENT_BREADCRUMBS || []);
    }
}

function rangeSelectItems(startId, endId) {
    if (!DIRECTORY_ITEMS) return;
    const allIds = Object.keys(DIRECTORY_ITEMS);
    const idx1 = allIds.indexOf(startId);
    const idx2 = allIds.indexOf(endId);
    if (idx1 === -1 || idx2 === -1) return;

    const [minIdx, maxIdx] = [Math.min(idx1, idx2), Math.max(idx1, idx2)];
    for (let i = minIdx; i <= maxIdx; i++) {
        const id = allIds[i];
        window.SELECTED_ITEMS.set(id, DIRECTORY_ITEMS[id]);
    }
    updateBulkActionBar();
}

function openContextMenuAt(id, clientX, clientY) {
    if (window.innerWidth <= 768) {
        if (typeof openMobileBottomSheet === 'function') openMobileBottomSheet(id);
        return;
    }

    if (typeof closeAllMoreMenus === 'function') closeAllMoreMenus();

    const moreDiv = document.getElementById(`more-option-${id}`);
    if (!moreDiv) return;

    const menuWidth = 200;
    const menuHeight = 240;
    const x = Math.max(10, Math.min(clientX, window.innerWidth - menuWidth - 12));
    const y = Math.max(10, Math.min(clientY, window.innerHeight - menuHeight - 12));

    moreDiv.style.position = 'fixed';
    moreDiv.style.left = `${x}px`;
    moreDiv.style.top = `${y}px`;
    moreDiv.style.zIndex = '1000';
    moreDiv.style.opacity = '1';
    moreDiv.style.pointerEvents = 'auto';

    const onDocClick = (e) => {
        if (!moreDiv.contains(e.target)) {
            if (typeof closeMoreMenu === 'function') closeMoreMenu(moreDiv);
            document.removeEventListener('click', onDocClick);
        }
    };
    setTimeout(() => {
        document.addEventListener('click', onDocClick);
    }, 10);
}

function updateBulkActionBar() {
    const bar = document.getElementById('bulk-actions-bar');
    const label = document.getElementById('bulk-count-label');
    const headerCheck = document.getElementById('header-select-all');
    const count = window.SELECTED_ITEMS.size;

    if (label) {
        label.innerText = `${count} selected`;
    }

    if (bar) {
        if (count > 0) {
            bar.classList.add('active');
        } else {
            bar.classList.remove('active');
        }
    }

    // Sync header checkbox
    if (headerCheck) {
        const totalItems = Object.keys(DIRECTORY_ITEMS || {}).length;
        headerCheck.checked = totalItems > 0 && count === totalItems;
        headerCheck.indeterminate = count > 0 && count < totalItems;
    }

    // Update row & card classes
    document.querySelectorAll('.body-tr, .gd-folder-chip, .gd-file-card').forEach(el => {
        const id = el.getAttribute('data-id');
        const isChecked = window.SELECTED_ITEMS.has(id);
        if (isChecked) {
            el.classList.add('is-selected');
        } else {
            el.classList.remove('is-selected');
        }
        const rowCb = el.querySelector('.item-select-checkbox');
        if (rowCb) rowCb.checked = isChecked;
        const cardBtn = el.querySelector('.gd-card-select-btn');
        if (cardBtn) {
            if (isChecked) cardBtn.classList.add('checked');
            else cardBtn.classList.remove('checked');
        }
    });
}

function toggleItemSelection(id) {
    const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
    if (!item) return;

    if (window.SELECTED_ITEMS.has(id)) {
        window.SELECTED_ITEMS.delete(id);
    } else {
        window.SELECTED_ITEMS.set(id, item);
    }
    updateBulkActionBar();
}

function selectAllItems() {
    if (!DIRECTORY_ITEMS) return;
    for (const [id, item] of Object.entries(DIRECTORY_ITEMS)) {
        window.SELECTED_ITEMS.set(id, item);
    }
    updateBulkActionBar();
}

function deselectAllItems() {
    window.SELECTED_ITEMS.clear();
    updateBulkActionBar();
}

async function bulkDownloadSelected() {
    if (window.SELECTED_ITEMS.size === 0) return;
    const items = Array.from(window.SELECTED_ITEMS.values());

    if (items.length === 1 && items[0].type === 'file') {
        const item = items[0];
        const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
        const directUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath) : `${getRootUrl()}/file?path=${encodeURIComponent(filePath)}`;
        const a = document.createElement('a');
        a.href = directUrl;
        a.download = item.name;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        showToast(`Downloading "${item.name}"... ⬇️`);
        return;
    }

    if (items.length === 1 && items[0].type === 'folder') {
        const folder = items[0];
        const folderPath = (folder.path + '/' + folder.id).replaceAll('//', '/');
        showToast(`Preparing ZIP archive for "${folder.name}"... 📦`);
        window.location.href = `${getRootUrl()}/downloadZip?path=${encodeURIComponent(folderPath)}`;
        return;
    }

    // Multiple files and/or folders selected: Download unified ZIP
    const paths = items.map(item => (item.path + '/' + item.id).replaceAll('//', '/'));
    showToast(`Preparing ZIP archive for ${items.length} selected item(s)... 📦`);
    
    try {
        const res = await postJson('/api/downloadZip', { paths });
        if (res && res.status === 'ok' && res.download_url) {
            window.location.href = res.download_url;
        } else {
            const pathsParam = encodeURIComponent(paths.join(','));
            window.location.href = `${getRootUrl()}/downloadZip?paths=${pathsParam}`;
        }
    } catch (e) {
        const pathsParam = encodeURIComponent(paths.join(','));
        window.location.href = `${getRootUrl()}/downloadZip?paths=${pathsParam}`;
    }
}

async function bulkDeleteSelected() {
    if (window.SELECTED_ITEMS.size === 0) return;
    const count = window.SELECTED_ITEMS.size;
    const isTrash = getCurrentPath().includes('/trash');
    const msg = isTrash 
        ? `Are you sure you want to permanently delete these ${count} item(s) from Telegram storage? This cannot be undone.`
        : `Move ${count} item(s) to trash?`;

    if (!confirm(msg)) return;

    const paths = Array.from(window.SELECTED_ITEMS.values()).map(item => {
        return (item.path + '/' + item.id).replaceAll('//', '/');
    });

    if (isTrash) {
        const res = await postJson('/api/bulkDelete', { paths });
        if (res && res.status === 'ok') {
            showToast(`Permanently deleted ${count} item(s) 🗑️`);
            deselectAllItems();
            getCurrentDirectory();
        } else {
            alert('Failed to delete selected items.');
        }
    } else {
        const res = await postJson('/api/bulkTrash', { paths, trash: true });
        if (res && res.status === 'ok') {
            showToast(`Moved ${count} item(s) to trash 🗑️`);
            deselectAllItems();
            getCurrentDirectory();
        } else {
            alert('Failed to trash selected items.');
        }
    }
}

// In-App File & Media Preview Lightbox
function openFilePreview() {
    const id = this.getAttribute ? this.getAttribute('data-id') : (typeof this.id === 'string' ? this.id : null);
    const item = (typeof DIRECTORY_ITEMS !== 'undefined' && id) ? DIRECTORY_ITEMS[id] : null;
    if (!item) return;

    if (item.type === 'folder') {
        openFolder.call(this);
        return;
    }

    const fileName = item.name.toLowerCase();
    const path = (item.path + '/' + item.id).replaceAll('//', '/');
    const directUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(path) : `${getRootUrl()}/file?path=${encodeURIComponent(path)}`;

    const lightbox = document.getElementById('media-preview-modal');
    const title = document.getElementById('preview-filename');
    const holder = document.getElementById('preview-content-holder');
    const dlBtn = document.getElementById('preview-download-btn');

    if (title) title.innerText = item.name;
    if (dlBtn) dlBtn.onclick = () => {
        const dl = document.createElement('a');
        dl.href = directUrl;
        dl.download = item.name;
        dl.target = '_blank';
        document.body.appendChild(dl);
        dl.click();
        document.body.removeChild(dl);
    };

    const imageExts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff', '.avif', '.heic', '.heif'];
    const videoExts = ['.mp4', '.mkv', '.webm', '.mov', '.avi', '.ts', '.ogv', '.m4v', '.3gp', '.flv'];
    const audioExts = ['.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.opus', '.wma'];
    const codeTextExts = [
        '.txt', '.md', '.markdown', '.py', '.js', '.ts', '.jsx', '.tsx',
        '.html', '.htm', '.css', '.scss', '.json', '.xml', '.csv', '.log',
        '.sh', '.bat', '.cmd', '.yaml', '.yml', '.c', '.cpp', '.h', '.hpp',
        '.java', '.rs', '.go', '.sql', '.ini', '.env', '.cfg', '.conf', '.toml',
        '.dockerfile', '.gitattributes', '.gitignore'
    ];

    const isImage = imageExts.some(ext => fileName.endsWith(ext));
    const isVideo = videoExts.some(ext => fileName.endsWith(ext));
    const isAudio = audioExts.some(ext => fileName.endsWith(ext));
    const isPdf = fileName.endsWith('.pdf');
    const isText = codeTextExts.some(ext => fileName.endsWith(ext));

    if (isImage || isVideo || isAudio || isPdf || isText) {
        if (holder) {
            holder.innerHTML = '';
            if (isImage) {
                holder.innerHTML = `
                    <div class="gd-preview-image-container">
                        <div class="gd-preview-controls-bar">
                            <button class="gd-preview-tool-btn" id="img-zoom-out" title="Zoom Out">🔍−</button>
                            <button class="gd-preview-tool-btn" id="img-zoom-reset" title="Reset Zoom">100%</button>
                            <button class="gd-preview-tool-btn" id="img-zoom-in" title="Zoom In">🔍+</button>
                            <button class="gd-preview-tool-btn" id="img-rotate" title="Rotate 90°">🔄 Rotate</button>
                        </div>
                        <div class="gd-preview-img-wrapper" id="img-preview-wrapper">
                            <img id="preview-active-img" src="${directUrl}" alt="${escapeHtml(item.name)}" 
                                style="max-width:88vw; max-height:76vh; object-fit:contain; transition: transform 0.2s ease;"
                                onerror="this.style.display='none'; this.parentElement.nextElementSibling.style.display='block';" />
                        </div>
                    </div>
                    <div style="display:none; text-align:center; color:#fff; padding:30px;">
                        <div style="font-size:2.5rem; margin-bottom:10px;">⚠️</div>
                        <p>Image preview unavailable. Click below to download directly.</p>
                        <a href="${directUrl}" download="${escapeHtml(item.name)}" class="gd-primary-btn" style="margin-top:12px; display:inline-block;">Download Image</a>
                    </div>`;

                let currentZoom = 1.0;
                let currentRot = 0;
                const activeImg = document.getElementById('preview-active-img');
                const btnZoomIn = document.getElementById('img-zoom-in');
                const btnZoomOut = document.getElementById('img-zoom-out');
                const btnZoomReset = document.getElementById('img-zoom-reset');
                const btnRotate = document.getElementById('img-rotate');

                function updateTransform() {
                    if (activeImg) {
                        activeImg.style.transform = `scale(${currentZoom}) rotate(${currentRot}deg)`;
                    }
                }

                if (btnZoomIn) btnZoomIn.onclick = () => { currentZoom = Math.min(3.0, currentZoom + 0.25); updateTransform(); };
                if (btnZoomOut) btnZoomOut.onclick = () => { currentZoom = Math.max(0.4, currentZoom - 0.25); updateTransform(); };
                if (btnZoomReset) btnZoomReset.onclick = () => { currentZoom = 1.0; currentRot = 0; updateTransform(); };
                if (btnRotate) btnRotate.onclick = () => { currentRot = (currentRot + 90) % 360; updateTransform(); };

            } else if (isVideo) {
                holder.innerHTML = `
                    <div class="gd-preview-video-container">
                        <div class="gd-preview-controls-bar">
                            <span class="gd-preview-bar-label">Speed:</span>
                            <button class="gd-preview-speed-btn" data-speed="0.75">0.75x</button>
                            <button class="gd-preview-speed-btn active" data-speed="1.0">1.0x</button>
                            <button class="gd-preview-speed-btn" data-speed="1.25">1.25x</button>
                            <button class="gd-preview-speed-btn" data-speed="1.5">1.5x</button>
                            <button class="gd-preview-speed-btn" data-speed="2.0">2.0x</button>
                            <button class="gd-preview-tool-btn" id="video-pip-btn" title="Picture in Picture">📺 PiP</button>
                        </div>
                        <video id="preview-active-video" controls autoplay playsinline style="max-width:88vw; max-height:76vh; border-radius: 8px;">
                            <source src="${directUrl}">
                            Your browser does not support video playback.
                        </video>
                    </div>`;

                const videoEl = document.getElementById('preview-active-video');
                const savedVol = localStorage.getItem('tgdrive_video_vol');
                if (videoEl && savedVol !== null) {
                    videoEl.volume = parseFloat(savedVol);
                }

                if (videoEl) {
                    videoEl.onvolumechange = () => {
                        localStorage.setItem('tgdrive_video_vol', videoEl.volume);
                    };
                }

                document.querySelectorAll('.gd-preview-speed-btn').forEach(btn => {
                    btn.onclick = () => {
                        const spd = parseFloat(btn.getAttribute('data-speed'));
                        if (videoEl) videoEl.playbackRate = spd;
                        document.querySelectorAll('.gd-preview-speed-btn').forEach(b => b.classList.remove('active'));
                        btn.classList.add('active');
                    };
                });

                const pipBtn = document.getElementById('video-pip-btn');
                if (pipBtn && videoEl) {
                    if (document.pictureInPictureEnabled) {
                        pipBtn.onclick = async () => {
                            try {
                                if (document.pictureInPictureElement) {
                                    await document.exitPictureInPicture();
                                } else {
                                    await videoEl.requestPictureInPicture();
                                }
                            } catch (e) {
                                console.warn('PiP error:', e);
                            }
                        };
                    } else {
                        pipBtn.style.display = 'none';
                    }
                }

            } else if (isAudio) {
                holder.innerHTML = `
                    <div class="gd-preview-audio-wrap">
                        <div class="gd-preview-audio-icon">🎵</div>
                        <div class="gd-preview-audio-title">${escapeHtml(item.name)}</div>
                        <div class="gd-preview-audio-size">${convertBytes(item.size)}</div>
                        <audio id="preview-active-audio" controls autoplay style="width: 100%; max-width: 420px; margin-top: 16px;"><source src="${directUrl}">Your browser does not support audio playback.</audio>
                    </div>`;
                const audioEl = document.getElementById('preview-active-audio');
                const savedVol = localStorage.getItem('tgdrive_audio_vol');
                if (audioEl && savedVol !== null) {
                    audioEl.volume = parseFloat(savedVol);
                }
                if (audioEl) {
                    audioEl.onvolumechange = () => {
                        localStorage.setItem('tgdrive_audio_vol', audioEl.volume);
                    };
                }

            } else if (isPdf) {
                holder.innerHTML = `<iframe src="${directUrl}" class="gd-preview-pdf-frame"></iframe>`;
            } else if (isText) {
                holder.innerHTML = `
                    <div class="gd-preview-text-wrap">
                        <div class="gd-preview-text-header">
                            <span>${escapeHtml(item.name)}</span>
                            <button class="gd-btn-copy-code" onclick="copyPreviewText()">Copy Content</button>
                        </div>
                        <pre class="gd-preview-code"><code id="preview-text-code">Loading content...</code></pre>
                    </div>`;
                fetch(directUrl, { credentials: 'same-origin' })
                    .then(res => {
                        if (!res.ok) throw new Error('HTTP ' + res.status);
                        return res.text();
                    })
                    .then(text => {
                        const codeEl = document.getElementById('preview-text-code');
                        if (codeEl) codeEl.innerText = text;
                    })
                    .catch(err => {
                        const codeEl = document.getElementById('preview-text-code');
                        if (codeEl) codeEl.innerText = 'Unable to load text preview: ' + err.message;
                    });
            }
        }
        if (lightbox) lightbox.classList.add('active');
    } else {
        // Universal direct download fallback for non-previewable files (zip, exe, iso, etc.)
        showToast('Downloading ' + item.name + '... ⬇️');
        const dl = document.createElement('a');
        dl.href = directUrl;
        dl.download = item.name;
        dl.target = '_blank';
        document.body.appendChild(dl);
        dl.click();
        document.body.removeChild(dl);
    }
}

// Drag & Drop Upload Zone (Explorer to Browser & Sidebar drop targets)
function setupDragAndDrop() {
    const dropOverlay = document.getElementById('drop-overlay');
    let dragCounter = 0;

    window.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCounter++;
        // Show drop overlay when dragging external files or cross-window items
        if (dropOverlay) {
            dropOverlay.classList.add('active');
        }
    });

    window.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (dropOverlay) {
            dropOverlay.classList.add('active');
        }
    });

    window.addEventListener('dragleave', (e) => {
        dragCounter--;
        if (dragCounter <= 0 && dropOverlay) {
            dragCounter = 0;
            dropOverlay.classList.remove('active');
        }
    });

    window.addEventListener('drop', (e) => {
        dragCounter = 0;
        if (dropOverlay) dropOverlay.classList.remove('active');

        if (getCurrentPath().startsWith('/trash')) {
            e.preventDefault();
            return;
        }

        // Only handle generic viewport drop if not dropped on a specific folder or nav target
        if (e.target.closest('.folder-tr') || e.target.closest('.gd-crumb-target') || e.target.closest('#nav-my-drive') || e.target.closest('#nav-trash')) {
            return;
        }

        e.preventDefault();

        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            uploadFilesQueue(e.dataTransfer.files, getCurrentPath());
        }
    });

    // Sidebar Navigation Drop Targets
    const navMyDrive = document.getElementById('nav-my-drive');
    const navTrash = document.getElementById('nav-trash');

    if (navMyDrive) {
        navMyDrive.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            navMyDrive.classList.add('gd-nav-drop-hover');
            e.dataTransfer.dropEffect = 'move';
        });

        navMyDrive.addEventListener('dragleave', () => {
            navMyDrive.classList.remove('gd-nav-drop-hover');
        });

        navMyDrive.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            navMyDrive.classList.remove('gd-nav-drop-hover');

            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                uploadFilesQueue(e.dataTransfer.files, '/');
            }
        });
    }

    if (navTrash) {
        navTrash.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            navTrash.classList.add('gd-nav-drop-hover');
            e.dataTransfer.dropEffect = 'move';
        });

        navTrash.addEventListener('dragleave', () => {
            navTrash.classList.remove('gd-nav-drop-hover');
        });

        navTrash.addEventListener('drop', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            navTrash.classList.remove('gd-nav-drop-hover');

            const draggedItem = getDraggedItem(e);
            if (draggedItem) {
                const data = {
                    'path': draggedItem.path,
                    'trash': true
                };
                const res = await postJson('/api/trashFileFolder', data);
                if (res.status === 'ok') {
                    showToast('Moved item to Trash 🗑️');
                    broadcastDriveChange('TRASH', { path: data.path });
                    getCurrentDirectory();
                } else {
                    alert('Failed to send item to Trash');
                }
                clearDraggedItem();
            }
        });
    }
}

// App Initialization
document.addEventListener('DOMContentLoaded', () => {
    const searchForm = document.getElementById('search-form');
    const searchInput = document.getElementById('file-search');
    const searchClear = document.getElementById('search-clear-btn');

    if (searchForm && searchInput) {
        searchForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const q = searchInput.value.trim();
            if (!q) return;
            navigateToPath(`/search_${encodeURIComponent(q)}`);
        });

        searchInput.addEventListener('input', () => {
            if (searchClear) {
                searchClear.style.display = searchInput.value ? 'flex' : 'none';
            }
        });

        if (searchClear) {
            searchClear.addEventListener('click', () => {
                searchInput.value = '';
                searchClear.style.display = 'none';
                navigateToPath('/');
            });
        }
    }

    // View Toggles
    const listBtn = document.getElementById('toggle-list-view');
    const gridBtn = document.getElementById('toggle-grid-view');
    const infoBtn = document.getElementById('toggle-info-pane');
    const closeInspBtn = document.getElementById('close-inspector-btn');
    const inspector = document.getElementById('gd-inspector');

    if (listBtn) listBtn.addEventListener('click', () => applyViewMode('list'));
    if (gridBtn) gridBtn.addEventListener('click', () => applyViewMode('grid'));

    if (infoBtn && inspector) {
        infoBtn.addEventListener('click', () => {
            const isOpening = inspector.classList.contains('hidden');
            inspector.classList.toggle('hidden');
            if (isOpening && !SELECTED_ITEM_ID && typeof DIRECTORY_ITEMS !== 'undefined') {
                const keys = Object.keys(DIRECTORY_ITEMS);
                if (keys.length > 0) {
                    selectItem(keys[0]);
                }
            }
        });
    }

    if (closeInspBtn && inspector) {
        closeInspBtn.addEventListener('click', () => {
            inspector.classList.add('hidden');
            inspector.classList.remove('mobile-open');
        });
    }

    // Inspector Copy Link
    const inspCopyBtn = document.getElementById('insp-copy-btn');
    const inspLinkInput = document.getElementById('insp-link-input');
    if (inspCopyBtn && inspLinkInput) {
        inspCopyBtn.addEventListener('click', () => {
            copyTextToClipboard(inspLinkInput.value);
            showToast('Link copied to clipboard! 📋');
        });
    }

    // Inspector Action Buttons
    const inspOpenBtn = document.getElementById('insp-open-btn');
    const inspDlBtn = document.getElementById('insp-download-btn');

    if (inspOpenBtn) {
        inspOpenBtn.addEventListener('click', () => {
            if (!SELECTED_ITEM_ID) return;
            const item = DIRECTORY_ITEMS[SELECTED_ITEM_ID];
            if (!item) return;
            if (item.type === 'folder') {
                const folderPath = (item.path + '/' + item.id).replaceAll('//', '/');
                navigateToPath(folderPath);
            } else {
                openFilePreview.call({ getAttribute: () => SELECTED_ITEM_ID });
            }
        });
    }

    if (inspDlBtn) {
        inspDlBtn.addEventListener('click', () => {
            if (!SELECTED_ITEM_ID) return;
            const item = DIRECTORY_ITEMS[SELECTED_ITEM_ID];
            if (!item) return;
            const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
            if (item.type === 'folder') {
                showToast(`Preparing ZIP for folder "${item.name}"... 📦`);
                window.location.href = `${getRootUrl()}/downloadZip?path=${encodeURIComponent(filePath)}`;
            } else {
                const directUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath) : `${getRootUrl()}/file?path=${encodeURIComponent(filePath)}`;
                const a = document.createElement('a');
                a.href = directUrl;
                a.download = item.name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                showToast(`Downloading "${item.name}"... ⬇️`);
            }
        });
    }

    // Table Header Sort Clicks
    ['name', 'type', 'date', 'size'].forEach(col => {
        const th = document.getElementById(`th-sort-${col}`);
        if (th) {
            th.addEventListener('click', (e) => {
                e.stopPropagation();
                setDirectorySort(col);
            });
        }
    });

    // Sort Dropdown Button & Options
    const sortMenuBtn = document.getElementById('sort-menu-btn');
    const sortDropdown = document.getElementById('sort-dropdown-menu');
    if (sortMenuBtn && sortDropdown) {
        sortMenuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            sortDropdown.classList.toggle('active');
        });

        document.addEventListener('click', (e) => {
            if (!sortDropdown.contains(e.target) && e.target !== sortMenuBtn) {
                sortDropdown.classList.remove('active');
            }
        });

        document.querySelectorAll('.gd-sort-option').forEach(opt => {
            opt.addEventListener('click', () => {
                const sKey = opt.getAttribute('data-sort');
                const sOrder = opt.getAttribute('data-order');
                setDirectorySort(sKey, sOrder);
                sortDropdown.classList.remove('active');
            });
        });
    }

    // Filter Chips
    document.querySelectorAll('.gd-filter-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            const f = chip.getAttribute('data-filter');
            setDirectoryFilter(f);
        });
    });

    // Google Drive Keyboard Shortcuts
    document.addEventListener('keydown', (e) => {
        const isInput = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable;
        
        // Command Palette (Ctrl+K or Cmd+K)
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            toggleCommandPalette();
            return;
        }

        if (isInput) return;

        if (e.key === 'Delete' || e.key === 'Backspace') {
            if (window.SELECTED_ITEMS && window.SELECTED_ITEMS.size > 0) {
                e.preventDefault();
                bulkDeleteSelected();
            }
        } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
            e.preventDefault();
            selectAllItems();
        } else if (e.key === 'F2') {
            if (window.SELECTED_ITEMS && window.SELECTED_ITEMS.size === 1) {
                e.preventDefault();
                const singleItem = Array.from(window.SELECTED_ITEMS.values())[0];
                const renameBtn = document.getElementById(`rename-${singleItem.id}`);
                if (renameBtn) renameFileFolder.call(renameBtn);
            }
        } else if (e.key === 'Enter') {
            if (window.SELECTED_ITEMS && window.SELECTED_ITEMS.size === 1) {
                e.preventDefault();
                const singleItem = Array.from(window.SELECTED_ITEMS.values())[0];
                if (singleItem.type === 'folder') {
                    navigateToPath((singleItem.path + '/' + singleItem.id).replaceAll('//', '/'));
                } else {
                    openFilePreview.call({ getAttribute: () => singleItem.id });
                }
            }
        } else if (e.key === '/') {
            e.preventDefault();
            const searchInput = document.getElementById('file-search');
            if (searchInput) searchInput.focus();
        } else if (e.key === '?' || (e.shiftKey && e.key === '?')) {
            e.preventDefault();
            openKeyboardShortcutsModal();
        } else if (e.key.toLowerCase() === 'v' && !e.ctrlKey && !e.metaKey && !e.altKey) {
            e.preventDefault();
            applyViewMode(CURRENT_VIEW_MODE === 'grid' ? 'list' : 'grid');
        } else if (e.key.toLowerCase() === 'i' && !e.ctrlKey && !e.metaKey && !e.altKey) {
            e.preventDefault();
            const infoBtn = document.getElementById('toggle-info-pane');
            if (infoBtn) infoBtn.click();
        } else if (e.shiftKey && e.key.toLowerCase() === 'n') {
            e.preventDefault();
            const newFolderBtn = document.getElementById('new-folder-btn');
            if (newFolderBtn) newFolderBtn.click();
        } else if (e.shiftKey && e.key.toLowerCase() === 'u') {
            e.preventDefault();
            const fileInput = document.getElementById('file-input');
            if (fileInput) fileInput.click();
        }
    });

    // Preview Lightbox Close (Button + Escape Key + Backdrop click)
    const previewClose = document.getElementById('preview-close-btn');
    const previewLightbox = document.getElementById('media-preview-modal');
    function closePreviewLightbox() {
        if (previewLightbox) {
            previewLightbox.classList.remove('active');
            const holder = document.getElementById('preview-content-holder');
            if (holder) holder.innerHTML = '';
        }
    }
    if (previewClose) {
        previewClose.addEventListener('click', closePreviewLightbox);
    }
    if (previewLightbox) {
        previewLightbox.addEventListener('click', (e) => {
            if (e.target === previewLightbox || e.target.id === 'preview-content-holder') {
                closePreviewLightbox();
            }
        });
    }
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closePreviewLightbox();
            closeCommandPalette();
            closeKeyboardShortcutsModal();
            closeManageTagsModal();
            deselectAllItems();
        }
    });

    // Header Select-All & Bulk Action Toolbar Bindings
    const headerCheck = document.getElementById('header-select-all');
    if (headerCheck) {
        headerCheck.addEventListener('change', (e) => {
            if (e.target.checked) {
                selectAllItems();
            } else {
                deselectAllItems();
            }
        });
    }

    const bulkDeselectBtn = document.getElementById('bulk-deselect-btn');
    if (bulkDeselectBtn) bulkDeselectBtn.addEventListener('click', deselectAllItems);

    const bulkSelectAllBtn = document.getElementById('bulk-select-all-btn');
    if (bulkSelectAllBtn) bulkSelectAllBtn.addEventListener('click', selectAllItems);

    const bulkDownloadBtn = document.getElementById('bulk-download-btn');
    if (bulkDownloadBtn) bulkDownloadBtn.addEventListener('click', bulkDownloadSelected);

    const bulkDeleteBtn = document.getElementById('bulk-delete-btn');
    if (bulkDeleteBtn) bulkDeleteBtn.addEventListener('click', bulkDeleteSelected);

    // Tags Modal Handlers
    const addTagBtn = document.getElementById('add-tag-submit-btn');
    const newTagInput = document.getElementById('new-tag-input');
    const manageTagsClose = document.getElementById('manage-tags-close');

    if (addTagBtn) addTagBtn.addEventListener('click', addTagToCurrentItem);
    if (manageTagsClose) manageTagsClose.addEventListener('click', closeManageTagsModal);
    if (newTagInput) {
        newTagInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addTagToCurrentItem();
            }
        });
    }

    // Keyboard Shortcuts Modal Close
    const shortcutsClose = document.getElementById('shortcuts-modal-close');
    if (shortcutsClose) shortcutsClose.addEventListener('click', closeKeyboardShortcutsModal);

    // Storage Section Click -> Opens Storage Breakdown Modal
    const storageSection = document.querySelector('.gd-storage-section');
    if (storageSection) {
        storageSection.style.cursor = 'pointer';
        storageSection.addEventListener('click', () => {
            window.openStorageBreakdownModal();
        });
    }

    const sbCloseBtn = document.getElementById('storage-breakdown-close');
    if (sbCloseBtn) sbCloseBtn.addEventListener('click', window.closeStorageBreakdownModal);

    setupDragAndDrop();
    applyViewMode(CURRENT_VIEW_MODE);
    initSyncActivityManager();
    initCommandPalette();

    // Initial fetch — server enforces auth via session cookie; 401 triggers login modal
    getCurrentDirectory();
});

// ==========================================
// Tag Management & Global Shortcuts
// ==========================================

let CURRENT_TAG_ITEM_ID = null;

window.openManageTagsModal = function(id) {
    const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
    if (!item) return;
    CURRENT_TAG_ITEM_ID = id;

    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('manage-tags-modal');
    const tagInput = document.getElementById('new-tag-input');

    if (tagInput) tagInput.value = '';
    renderModalTags();

    if (bgBlur) {
        bgBlur.style.zIndex = '100';
        bgBlur.style.opacity = '1';
    }
    if (modal) {
        modal.style.zIndex = '101';
        modal.style.opacity = '1';
    }
    if (tagInput) setTimeout(() => tagInput.focus(), 150);
};

window.closeManageTagsModal = function() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('manage-tags-modal');
    if (bgBlur) bgBlur.style.opacity = '0';
    if (modal) modal.style.opacity = '0';
    setTimeout(() => {
        if (bgBlur) bgBlur.style.zIndex = '-1';
        if (modal) modal.style.zIndex = '-1';
    }, 200);
    CURRENT_TAG_ITEM_ID = null;
};

function renderModalTags() {
    const tagsListEl = document.getElementById('modal-tags-list');
    if (!tagsListEl || !CURRENT_TAG_ITEM_ID) return;
    const item = DIRECTORY_ITEMS[CURRENT_TAG_ITEM_ID];
    if (!item) return;

    const tags = Array.isArray(item.tags) ? item.tags : [];
    if (tags.length === 0) {
        tagsListEl.innerHTML = '<div style="color: var(--gd-text-secondary); font-size: 0.88rem;">No tags assigned yet.</div>';
        return;
    }

    tagsListEl.innerHTML = tags.map(tag => `
        <span class="gd-tag-chip" style="margin-right: 6px; margin-bottom: 6px; display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; background: rgba(66, 133, 244, 0.12); color: #4285f4; border-radius: 12px; font-size: 0.82rem; font-weight: 500;">
            <span>#${escapeHtml(tag)}</span>
            <button onclick="removeTagFromCurrentItem('${escapeHtml(tag)}')" style="background: none; border: none; cursor: pointer; color: inherit; font-size: 1rem; line-height: 1; padding: 0 2px;">&times;</button>
        </span>
    `).join('');
}

window.addTagToCurrentItem = async function() {
    const tagInput = document.getElementById('new-tag-input');
    if (!tagInput || !CURRENT_TAG_ITEM_ID) return;
    const tag = tagInput.value.trim().replace(/^#+/, '');
    if (!tag) return;

    const item = DIRECTORY_ITEMS[CURRENT_TAG_ITEM_ID];
    if (!item) return;
    const itemFullPath = (item.path + '/' + item.id).replaceAll('//', '/');

    try {
        const res = await postJson('/api/tagFileFolder', {
            path: itemFullPath,
            action: 'add',
            tag: tag
        });
        if (res && res.status === 'ok') {
            if (!Array.isArray(item.tags)) item.tags = [];
            if (!item.tags.includes(tag)) item.tags.push(tag);
            tagInput.value = '';
            renderModalTags();
            showToast(`Added tag #${tag} 🏷️`);
            if (typeof broadcastDriveChange === 'function') {
                broadcastDriveChange('TAG_ADD', { path: itemFullPath, tag });
            }
            if (typeof getCurrentDirectory === 'function') {
                getCurrentDirectory();
            }
        } else {
            showToast(res.status || 'Failed to add tag');
        }
    } catch (e) {
        showToast('Error adding tag');
    }
};

window.removeTagFromCurrentItem = async function(tag) {
    if (!CURRENT_TAG_ITEM_ID) return;
    const item = DIRECTORY_ITEMS[CURRENT_TAG_ITEM_ID];
    if (!item) return;
    const itemFullPath = (item.path + '/' + item.id).replaceAll('//', '/');

    try {
        const res = await postJson('/api/tagFileFolder', {
            path: itemFullPath,
            action: 'remove',
            tag: tag
        });
        if (res && res.status === 'ok') {
            if (Array.isArray(item.tags)) {
                item.tags = item.tags.filter(t => t !== tag);
            }
            renderModalTags();
            showToast(`Removed tag #${tag}`);
            if (typeof broadcastDriveChange === 'function') {
                broadcastDriveChange('TAG_REMOVE', { path: itemFullPath, tag });
            }
            if (typeof getCurrentDirectory === 'function') {
                getCurrentDirectory();
            }
        }
    } catch (e) {
        showToast('Error removing tag');
    }
};

window.openKeyboardShortcutsModal = function() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('keyboard-shortcuts-modal');
    if (bgBlur) {
        bgBlur.style.zIndex = '100';
        bgBlur.style.opacity = '1';
    }
    if (modal) {
        modal.style.zIndex = '101';
        modal.style.opacity = '1';
    }
};

window.closeKeyboardShortcutsModal = function() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('keyboard-shortcuts-modal');
    if (bgBlur) bgBlur.style.opacity = '0';
    if (modal) modal.style.opacity = '0';
    setTimeout(() => {
        if (bgBlur) bgBlur.style.zIndex = '-1';
        if (modal) modal.style.zIndex = '-1';
    }, 200);
};

// ==========================================
// Command Palette (Ctrl+K) Controller
// ==========================================

let CMD_PALETTE_ITEMS = [];
let CMD_PALETTE_ACTIVE_IDX = 0;

window.openStorageBreakdownModal = async function() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('storage-breakdown-modal');
    if (!modal) return;

    if (bgBlur) {
        bgBlur.style.zIndex = '100';
        bgBlur.style.opacity = '1';
    }
    modal.style.zIndex = '101';
    modal.style.opacity = '1';

    // Calculate Storage Breakdown
    let totalBytes = 0;
    let totalFiles = 0;
    let totalFolders = 0;

    const breakdown = {
        video: { label: 'Videos', size: 0, count: 0, color: '#ea4335', icon: '🎬' },
        image: { label: 'Images', size: 0, count: 0, color: '#fbbc05', icon: '🖼️' },
        audio: { label: 'Audio', size: 0, count: 0, color: '#34a853', icon: '🎵' },
        doc: { label: 'Documents', size: 0, count: 0, color: '#4285f4', icon: '📄' },
        code: { label: 'Code & Text', size: 0, count: 0, color: '#a142f4', icon: '💻' },
        archive: { label: 'Archives', size: 0, count: 0, color: '#ff6d01', icon: '📦' },
        other: { label: 'Other Files', size: 0, count: 0, color: '#5f6368', icon: '📁' }
    };

    const videoExts = ['mp4', 'mkv', 'webm', 'mov', 'avi', 'ts', 'ogv', 'm4v', '3gp', 'flv'];
    const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff', 'avif', 'heic', 'heif'];
    const audioExts = ['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'opus', 'wma'];
    const docExts = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'epub', 'odt', 'ods', 'odp'];
    const codeExts = ['txt', 'md', 'py', 'js', 'ts', 'jsx', 'tsx', 'html', 'css', 'scss', 'json', 'xml', 'csv', 'log', 'sh', 'bat', 'yaml', 'yml', 'c', 'cpp', 'h', 'java', 'rs', 'go', 'sql', 'env'];
    const archiveExts = ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'iso'];

    const items = (typeof DIRECTORY_ITEMS !== 'undefined') ? Object.values(DIRECTORY_ITEMS) : [];
    items.forEach(item => {
        if (item.type === 'folder') {
            totalFolders++;
            return;
        }
        totalFiles++;
        const size = Number(item.size) || 0;
        totalBytes += size;
        const ext = (item.name && item.name.includes('.')) ? item.name.split('.').pop().toLowerCase() : '';

        if (videoExts.includes(ext)) {
            breakdown.video.size += size; breakdown.video.count++;
        } else if (imageExts.includes(ext)) {
            breakdown.image.size += size; breakdown.image.count++;
        } else if (audioExts.includes(ext)) {
            breakdown.audio.size += size; breakdown.audio.count++;
        } else if (docExts.includes(ext)) {
            breakdown.doc.size += size; breakdown.doc.count++;
        } else if (codeExts.includes(ext)) {
            breakdown.code.size += size; breakdown.code.count++;
        } else if (archiveExts.includes(ext)) {
            breakdown.archive.size += size; breakdown.archive.count++;
        } else {
            breakdown.other.size += size; breakdown.other.count++;
        }
    });

    const totalSizeEl = document.getElementById('sb-total-size');
    const totalFilesEl = document.getElementById('sb-total-files');
    if (totalSizeEl) totalSizeEl.textContent = convertBytes(totalBytes);
    if (totalFilesEl) totalFilesEl.textContent = `${totalFiles.toLocaleString()} files across ${totalFolders.toLocaleString()} folders in view`;

    // Render stacked bar percentages
    for (const [key, data] of Object.entries(breakdown)) {
        const barEl = document.getElementById(`sb-bar-${key}`);
        if (barEl) {
            const pct = totalBytes > 0 ? ((data.size / totalBytes) * 100).toFixed(1) : 0;
            barEl.style.width = `${pct}%`;
        }
    }

    // Render categories breakdown list
    const listEl = document.getElementById('sb-categories-list');
    if (listEl) {
        listEl.innerHTML = Object.entries(breakdown).map(([key, data]) => {
            const pct = totalBytes > 0 ? ((data.size / totalBytes) * 100).toFixed(1) : 0;
            return `
                <div class="gd-sb-cat-row">
                    <div class="gd-sb-cat-icon" style="background: ${data.color}22; color: ${data.color};">${data.icon}</div>
                    <div class="gd-sb-cat-info">
                        <div class="gd-sb-cat-title-row">
                            <span class="gd-sb-cat-name">${data.label}</span>
                            <span class="gd-sb-cat-size">${convertBytes(data.size)} (${pct}%)</span>
                        </div>
                        <div class="gd-sb-cat-subtext">${data.count.toLocaleString()} item(s)</div>
                    </div>
                </div>
            `;
        }).join('');
    }
};

window.closeStorageBreakdownModal = function() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('storage-breakdown-modal');
    if (bgBlur) bgBlur.style.opacity = '0';
    if (modal) modal.style.opacity = '0';
    setTimeout(() => {
        if (bgBlur) bgBlur.style.zIndex = '-1';
        if (modal) modal.style.zIndex = '-1';
    }, 200);
};

function getStaticCommands() {
    return [
        { id: 'nav-my-drive', label: 'Go to My Drive', icon: '🏠', action: () => navigateToPath('/') },
        { id: 'nav-recent', label: 'Go to Recent Files', icon: '🕒', action: () => navigateToPath('/recent') },
        { id: 'nav-trash', label: 'Go to Trash', icon: '🗑️', action: () => navigateToPath('/trash') },
        { id: 'nav-sync', label: 'View Live Sync Activity', icon: '⚡', action: () => window.showSyncActivityView() },
        { id: 'nav-storage', label: 'View Cloud Storage Breakdown', icon: '📊', action: () => window.openStorageBreakdownModal() },
        { id: 'act-new-folder', label: 'Create New Folder', icon: '📁', action: () => { const b = document.getElementById('new-folder-btn'); if (b) b.click(); } },
        { id: 'act-upload-file', label: 'Upload Local File', icon: '⬆️', action: () => { const b = document.getElementById('file-input'); if (b) b.click(); } },
        { id: 'act-upload-url', label: 'Upload from URL', icon: '🌐', action: () => { const b = document.getElementById('url-upload'); if (b) b.click(); } },
        { id: 'act-toggle-view', label: 'Toggle List / Grid View', icon: '🔲', action: () => applyViewMode(CURRENT_VIEW_MODE === 'grid' ? 'list' : 'grid') },
        { id: 'act-toggle-insp', label: 'Toggle Details Inspector', icon: 'ℹ️', action: () => { const b = document.getElementById('toggle-info-pane'); if (b) b.click(); } },
        { id: 'act-shortcuts', label: 'Show Keyboard Shortcuts', icon: '⌨️', action: () => openKeyboardShortcutsModal() }
    ];
}

window.toggleCommandPalette = function() {
    const modal = document.getElementById('cmd-palette-modal');
    if (!modal) return;
    if (modal.classList.contains('active')) {
        closeCommandPalette();
    } else {
        openCommandPalette();
    }
};

window.openCommandPalette = function() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('cmd-palette-modal');
    const input = document.getElementById('cmd-palette-input');
    if (!modal) return;

    if (bgBlur) {
        bgBlur.style.zIndex = '100';
        bgBlur.style.opacity = '1';
    }
    modal.classList.add('active');
    if (input) {
        input.value = '';
        setTimeout(() => input.focus(), 80);
    }
    renderCommandPaletteResults('');
};

window.closeCommandPalette = function() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('cmd-palette-modal');
    if (modal) modal.classList.remove('active');
    if (bgBlur) {
        bgBlur.style.opacity = '0';
        setTimeout(() => { bgBlur.style.zIndex = '-1'; }, 200);
    }
};

function renderCommandPaletteResults(query) {
    const resultsContainer = document.getElementById('cmd-palette-results');
    if (!resultsContainer) return;

    const q = (query || '').toLowerCase().trim();
    const staticCmds = getStaticCommands();
    let matches = [];

    // 1. Filter system commands
    staticCmds.forEach(cmd => {
        if (!q || cmd.label.toLowerCase().includes(q)) {
            matches.push(cmd);
        }
    });

    // 2. Add matching items from current directory
    if (typeof DIRECTORY_ITEMS !== 'undefined' && DIRECTORY_ITEMS) {
        Object.entries(DIRECTORY_ITEMS).forEach(([id, item]) => {
            if (!q || item.name.toLowerCase().includes(q)) {
                matches.push({
                    id: `item-${id}`,
                    label: item.name,
                    icon: item.type === 'folder' ? '📁' : '📄',
                    subtext: item.type === 'folder' ? ((item.size && item.size > 0) ? `Folder • ${convertBytes(item.size)}` : 'Folder') : convertBytes(item.size),
                    action: () => {
                        if (item.type === 'folder') {
                            navigateToPath((item.path + '/' + item.id).replaceAll('//', '/'));
                        } else {
                            openFilePreview.call({ getAttribute: () => item.id });
                        }
                    }
                });
            }
        });
    }

    CMD_PALETTE_ITEMS = matches;
    CMD_PALETTE_ACTIVE_IDX = 0;

    if (matches.length === 0) {
        resultsContainer.innerHTML = `<div class="gd-cmd-empty">No matching commands or files found. Press Enter to search everywhere for "${escapeHtml(q)}".</div>`;
        return;
    }

    resultsContainer.innerHTML = matches.map((item, idx) => `
        <div class="gd-cmd-item ${idx === 0 ? 'active' : ''}" data-idx="${idx}">
            <span class="gd-cmd-icon">${item.icon}</span>
            <div class="gd-cmd-info">
                <span class="gd-cmd-title">${escapeHtml(item.label)}</span>
                ${item.subtext ? `<span class="gd-cmd-subtext">${escapeHtml(item.subtext)}</span>` : ''}
            </div>
        </div>
    `).join('');

    resultsContainer.querySelectorAll('.gd-cmd-item').forEach(el => {
        el.onclick = () => {
            const idx = parseInt(el.getAttribute('data-idx'), 10);
            executeCommandPaletteItem(idx);
        };
    });
}

function executeCommandPaletteItem(idx) {
    if (CMD_PALETTE_ITEMS[idx] && typeof CMD_PALETTE_ITEMS[idx].action === 'function') {
        closeCommandPalette();
        CMD_PALETTE_ITEMS[idx].action();
    } else {
        const input = document.getElementById('cmd-palette-input');
        const q = input ? input.value.trim() : '';
        if (q) {
            closeCommandPalette();
            navigateToPath(`/search_${encodeURIComponent(q)}`);
        }
    }
}

function initCommandPalette() {
    const input = document.getElementById('cmd-palette-input');
    const escBadge = document.querySelector('.gd-cmd-esc-badge');

    if (escBadge) escBadge.onclick = closeCommandPalette;

    if (input) {
        input.addEventListener('input', (e) => {
            renderCommandPaletteResults(e.target.value);
        });

        input.addEventListener('keydown', (e) => {
            const resultsContainer = document.getElementById('cmd-palette-results');
            const items = resultsContainer ? resultsContainer.querySelectorAll('.gd-cmd-item') : [];

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (items.length > 0) {
                    items[CMD_PALETTE_ACTIVE_IDX]?.classList.remove('active');
                    CMD_PALETTE_ACTIVE_IDX = (CMD_PALETTE_ACTIVE_IDX + 1) % items.length;
                    items[CMD_PALETTE_ACTIVE_IDX]?.classList.add('active');
                    items[CMD_PALETTE_ACTIVE_IDX]?.scrollIntoView({ block: 'nearest' });
                }
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (items.length > 0) {
                    items[CMD_PALETTE_ACTIVE_IDX]?.classList.remove('active');
                    CMD_PALETTE_ACTIVE_IDX = (CMD_PALETTE_ACTIVE_IDX - 1 + items.length) % items.length;
                    items[CMD_PALETTE_ACTIVE_IDX]?.classList.add('active');
                    items[CMD_PALETTE_ACTIVE_IDX]?.scrollIntoView({ block: 'nearest' });
                }
            } else if (e.key === 'Enter') {
                e.preventDefault();
                executeCommandPaletteItem(CMD_PALETTE_ACTIVE_IDX);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closeCommandPalette();
            }
        });
    }
}

// ==========================================
// Live Sync Manager Telemetry & Native Activity UI
// ==========================================

// Switch to Native Sync View in UI
window.showSyncActivityView = function() {
    window.CURRENT_PAGE_VIEW = 'sync';
    const listViewContainer = document.getElementById('list-view-container');
    const gridViewContainer = document.getElementById('grid-view-container');
    const syncViewContainer = document.getElementById('sync-view-container');
    const breadcrumbsContainer = document.getElementById('breadcrumbs-container');
    const navMyDrive = document.getElementById('nav-my-drive');
    const navSyncActivity = document.getElementById('nav-sync-activity');
    const navTrash = document.getElementById('nav-trash');

    if (listViewContainer) listViewContainer.style.display = 'none';
    if (gridViewContainer) gridViewContainer.style.display = 'none';
    if (syncViewContainer) syncViewContainer.style.display = 'flex';

    // Update sidebar selection
    if (navMyDrive) navMyDrive.className = 'gd-nav-item unselected-item';
    if (navTrash) navTrash.className = 'gd-nav-item unselected-item';
    if (navSyncActivity) navSyncActivity.className = 'gd-nav-item selected-item';

    // Update breadcrumb
    if (breadcrumbsContainer) {
        breadcrumbsContainer.innerHTML = `
            <span class="gd-crumb gd-crumb-target" id="crumb-root-back">My Drive</span>
            <span class="gd-crumb-separator">&gt;</span>
            <span class="gd-crumb gd-crumb-current">⚡ Live Sync Activity</span>
        `;
        const rootCrumb = document.getElementById('crumb-root-back');
        if (rootCrumb) {
            rootCrumb.addEventListener('click', (e) => {
                e.preventDefault();
                window.hideSyncActivityView();
                if (typeof updateDirectoryData === 'function') updateDirectoryData('/');
            });
        }
    }

    if (typeof pollSyncStatus === 'function') pollSyncStatus();
};

window.hideSyncActivityView = function() {
    window.CURRENT_PAGE_VIEW = 'drive';
    const listViewContainer = document.getElementById('list-view-container');
    const gridViewContainer = document.getElementById('grid-view-container');
    const syncViewContainer = document.getElementById('sync-view-container');
    const navMyDrive = document.getElementById('nav-my-drive');
    const navSyncActivity = document.getElementById('nav-sync-activity');

    if (syncViewContainer) syncViewContainer.style.display = 'none';
    if (CURRENT_VIEW_MODE === 'grid') {
        if (gridViewContainer) gridViewContainer.style.display = 'block';
        if (listViewContainer) listViewContainer.style.display = 'none';
    } else {
        if (listViewContainer) listViewContainer.style.display = 'block';
        if (gridViewContainer) gridViewContainer.style.display = 'none';
    }
    if (navSyncActivity) navSyncActivity.className = 'gd-nav-item unselected-item';
    if (navMyDrive) navMyDrive.className = 'gd-nav-item selected-item';
};

function initSyncActivityManager() {
    const syncPill = document.getElementById('gd-sync-status-pill');
    const syncPillText = document.getElementById('sync-pill-text');
    const navSyncActivity = document.getElementById('nav-sync-activity');
    const navMyDrive = document.getElementById('nav-my-drive');
    const navTrash = document.getElementById('nav-trash');

    if (syncPill) syncPill.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); window.showSyncActivityView(); });
    if (navSyncActivity) navSyncActivity.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); window.showSyncActivityView(); });

    // Handle My Drive navigation switching away from sync view
    if (navMyDrive) navMyDrive.addEventListener('click', () => window.hideSyncActivityView());
    if (navTrash) navTrash.addEventListener('click', () => window.hideSyncActivityView());

    async function pollSyncStatus() {
        if (!window.IS_AUTHENTICATED) return; // Only poll once authenticated
        try {
            const res = await fetch('/api/getSyncStatus', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({})
            });
            if (res.status === 401) {
                window.IS_AUTHENTICATED = false;
                return;
            }
            if (res.ok) {
                const json = await res.json();
                if (json.status === 'ok' && json.data) {
                    renderSyncStatus(json.data);
                }
            }
        } catch (e) {
            // Ignore background polling errors
        }
    }

    function renderSyncStatus(data) {
        const state = data.state || 'idle';
        const isBusy = (state === 'scanning' || state === 'syncing_folders' || state === 'syncing_files');

        // Update Pill in header
        if (syncPill && syncPillText) {
            if (isBusy) {
                syncPill.classList.add('active');
                if (state === 'syncing_folders') {
                    syncPillText.innerText = `Sync: Folders (${data.folders_created || 0}/${data.folders_total || 0})`;
                } else if (state === 'syncing_files') {
                    syncPillText.innerText = `Syncing: ${data.current_index || 0}/${data.total_items || 0}`;
                } else {
                    syncPillText.innerText = 'Sync: Scanning...';
                }
            } else {
                syncPill.classList.remove('active');
                syncPillText.innerText = (state === 'completed') ? 'Sync: Complete' : 'Sync: Idle';
            }
        }
        // Update Sidebar Storage Widget with live stats
        if (data.drive_stats && typeof updateSidebarStorageStats === 'function') {
            updateSidebarStorageStats(data.drive_stats);
        }

        // Update Native UI Dashboard Elements
        const uiStateText = document.getElementById('sync-view-state-text');
        const uiSource = document.getElementById('sync-view-source-text');
        const uiSpeed = document.getElementById('sync-view-speed');

        const uiActiveName = document.getElementById('sync-view-active-name');
        const uiActivePath = document.getElementById('sync-view-active-path');
        const uiActiveSize = document.getElementById('sync-view-active-size');

        const uiFill = document.getElementById('sync-view-progress-fill');
        const uiCounts = document.getElementById('sync-view-progress-counts');
        const uiPercent = document.getElementById('sync-view-progress-percent');

        const pendingBody = document.getElementById('sync-pending-table-body');
        const pendingBadge = document.getElementById('sync-pending-badge');
        const completedBody = document.getElementById('sync-completed-table-body');
        const completedBadge = document.getElementById('sync-completed-badge');
        const terminalLogs = document.getElementById('sync-view-terminal-logs');

        // State & Target info
        if (uiStateText) uiStateText.innerText = state.replace('_', ' ').toUpperCase();
        if (uiSource && data.source) uiSource.innerText = data.source;
        if (uiSpeed) uiSpeed.innerText = data.speed_str || (isBusy ? 'Transferring...' : '0.00 KB/s');

        // Active file card
        if (uiActiveName) uiActiveName.innerText = data.current_item || (isBusy ? 'Processing cloud stream...' : 'No active transfer');
        if (uiActivePath) uiActivePath.innerText = data.current_path || '/';
        if (uiActiveSize) uiActiveSize.innerText = data.current_size || (data.current_item ? 'In-Memory Stream' : '--');

        // Progress Calculation
        let pct = 0;
        let countText = '0 / 0 files (0 remaining)';
        if (state === 'syncing_folders' && data.folders_total > 0) {
            pct = Math.round((data.folders_created / data.folders_total) * 100);
            countText = `${data.folders_created} / ${data.folders_total} folders verified`;
        } else if (state === 'syncing_files' && data.total_items > 0) {
            pct = Math.round((data.current_index / data.total_items) * 100);
            countText = `${data.current_index} / ${data.total_items} files (${data.remaining_items || 0} remaining)`;
        } else if (state === 'completed') {
            pct = 100;
            countText = `🎉 All ${data.files_uploaded || 0} files successfully uploaded (${data.files_skipped || 0} up-to-date skipped)`;
        }

        if (uiFill) uiFill.style.width = `${pct}%`;
        if (uiCounts) uiCounts.innerText = countText;
        if (uiPercent) uiPercent.innerText = `${pct}%`;

        // Render Left Table: ⏳ To Be Uploaded (Queue)
        if (pendingBody) {
            let pendingList = (data.pending_queue && data.pending_queue.length > 0) ? [...data.pending_queue] : [];
            
            if (pendingList.length === 0 && data.current_item) {
                pendingList.push({
                    name: data.current_item,
                    path: data.current_path || (data.source ? `/${data.source}/` : '/'),
                    size: data.current_size || '⚡ Transferring'
                });
            }

            // If queue list is short but sync is actively running with remaining items
            if (pendingList.length < 15 && isBusy && data.remaining_items > pendingList.length) {
                const startIdx = (data.current_index || 0) + pendingList.length + 1;
                const fillCount = Math.min(15 - pendingList.length, data.remaining_items - pendingList.length);
                for (let i = 0; i < fillCount; i++) {
                    const nextNum = startIdx + i;
                    pendingList.push({
                        name: `[Stream Item #${nextNum} of ${data.total_items || '...'}]`,
                        path: data.current_path || (data.source ? `/${data.source}/` : '/'),
                        size: 'Queued'
                    });
                }
            }

            if (pendingBadge) pendingBadge.innerText = data.remaining_items !== undefined ? data.remaining_items : pendingList.length;

            if (pendingList.length > 0) {
                pendingBody.innerHTML = pendingList.map((item, idx) => `
                    <tr>
                        <td class="gd-sync-table-idx">${idx + 1}</td>
                        <td class="gd-sync-table-fname" title="${escapeHtml(item.name || '')}">
                            <span>${idx === 0 && isBusy ? '⚡' : '📄'}</span>
                            <span class="file-name-text">${escapeHtml(item.name || '')}</span>
                        </td>
                        <td>
                            <span class="gd-sync-table-path" title="${escapeHtml(item.path || '/')}">${escapeHtml(item.path || '/')}</span>
                        </td>
                        <td style="text-align: right;">
                            <span class="gd-sync-status-tag ${idx === 0 && isBusy ? 'tag-uploading' : 'tag-pending'}">${escapeHtml(item.size || 'Queued')}</span>
                        </td>
                    </tr>
                `).join('');
            } else if (isBusy && data.remaining_items > 0) {
                pendingBody.innerHTML = `
                    <tr class="empty-row">
                        <td colspan="4">⚡ Processing remaining ${data.remaining_items} items over persistent MTP stream...</td>
                    </tr>
                `;
            } else {
                pendingBody.innerHTML = `
                    <tr class="empty-row">
                        <td colspan="4">${state === 'completed' ? '✅ All files transferred! Queue is empty.' : 'No pending files in queue'}</td>
                    </tr>
                `;
            }
        }

        // Render Right Table: ✅ Uploaded to Cloud (History)
        if (completedBody) {
            let completedList = data.completed_list || [];
            
            // If empty, parse from live logs array
            if (completedList.length === 0 && data.logs && data.logs.length > 0) {
                completedList = [];
                for (let i = data.logs.length - 1; i >= 0; i--) {
                    const log = data.logs[i];
                    const msg = log.msg || '';
                    if (msg.includes(']: ') && msg.includes(' (')) {
                        try {
                            const fpart = msg.split(']: ')[1];
                            const fname = fpart.substring(0, fpart.lastIndexOf(' (')).trim();
                            const fsize = fpart.substring(fpart.lastIndexOf(' (') + 2, fpart.lastIndexOf(')')).trim();
                            completedList.push({
                                name: fname,
                                path: data.current_path || (data.source ? `/${data.source}/` : '/'),
                                size: fsize,
                                time: log.time || 'Synced'
                            });
                        } catch (e) {}
                    }
                }
            }

            if (completedBadge) completedBadge.innerText = data.current_index !== undefined ? data.current_index : completedList.length;

            if (completedList.length > 0) {
                completedBody.innerHTML = completedList.map((item, idx) => `
                    <tr>
                        <td class="gd-sync-table-idx">${idx + 1}</td>
                        <td class="gd-sync-table-fname" title="${escapeHtml(item.name || '')}">
                            <span>✅</span>
                            <span class="file-name-text">${escapeHtml(item.name || '')}</span>
                        </td>
                        <td>
                            <span class="gd-sync-table-path" title="${escapeHtml(item.path || '/')}">${escapeHtml(item.path || '/')}</span>
                        </td>
                        <td style="text-align: right;">
                            <span class="gd-sync-status-tag tag-synced">${escapeHtml(item.size ? item.size + ' • ' + (item.time || '') : (item.time || 'Synced'))}</span>
                        </td>
                    </tr>
                `).join('');
            } else {
                completedBody.innerHTML = `
                    <tr class="empty-row">
                        <td colspan="4">Uploaded files will appear here in real-time</td>
                    </tr>
                `;
            }
        }

        // Render Live Logs
        if (terminalLogs && data.logs && data.logs.length > 0) {
            terminalLogs.innerHTML = data.logs.map(l => `
                <div class="gd-sync-terminal-line">
                    <span class="log-time">[${l.time}]</span> ${escapeHtml(l.msg)}
                </div>
            `).join('');
            terminalLogs.scrollTop = terminalLogs.scrollHeight;
        }
    }

    setInterval(pollSyncStatus, 2000);
}

// Google Drive Keyboard Navigation & Shortcuts
window.addEventListener('keydown', (e) => {
    // Do not intercept if user is typing in form inputs, textareas, search boxes, or modals
    const tag = e.target.tagName ? e.target.tagName.toLowerCase() : '';
    const isEditable = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;
    if (isEditable) return;

    // Check if any modal is active
    const activeModal = document.querySelector('.gd-modal[style*="opacity: 1"], .gd-dialog[style*="opacity: 1"], #bg-blur[style*="opacity: 1"]');
    if (activeModal && activeModal.style.opacity !== '0' && activeModal.style.zIndex !== '-1') return;

    // 1. Backspace / Alt+Left: Navigate to Parent Folder
    if (e.key === 'Backspace' || (e.altKey && e.key === 'ArrowLeft')) {
        const curPath = typeof getCurrentPath === 'function' ? getCurrentPath() : '/';
        if (curPath && curPath !== '/' && !curPath.startsWith('/trash') && !curPath.startsWith('/recent')) {
            e.preventDefault();
            const parent = typeof getParentPath === 'function' ? getParentPath(curPath) : '/';
            if (typeof navigateToPath === 'function') {
                navigateToPath(parent);
            }
        }
    }

    // 2. F5 or Ctrl+R / Cmd+R: Smooth Directory Refresh
    if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && (e.key === 'r' || e.key === 'R'))) {
        e.preventDefault();
        if (typeof refreshCurrentDirectory === 'function') {
            refreshCurrentDirectory();
        }
    }

    // 3. Ctrl+A / Cmd+A: Select All Directory Items
    if ((e.ctrlKey || e.metaKey) && (e.key === 'a' || e.key === 'A')) {
        e.preventDefault();
        if (typeof selectAllItems === 'function') {
            selectAllItems();
        }
    }

    // 4. Escape: Deselect all / close inspector
    if (e.key === 'Escape') {
        if (typeof deselectAllItems === 'function') {
            deselectAllItems();
        }
        const inspector = document.getElementById('gd-inspector');
        if (inspector && !inspector.classList.contains('hidden')) {
            inspector.classList.add('hidden');
        }
    }
});


