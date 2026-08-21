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

// Build Interactive Breadcrumbs with Proper Folder Names (Google Drive style)
function updateBreadcrumbs(breadcrumbs) {
    const container = document.getElementById('breadcrumbs-container');
    if (!container) return;

    const rawPath = getCurrentPath();
    const isTrash = rawPath.startsWith('/trash');
    const isSearch = rawPath.startsWith('/search');

    if (isTrash) {
        container.innerHTML = `<span class="gd-crumb gd-crumb-active" data-path="/trash">Trash</span>`;
        document.title = 'Trash - Google Drive';
        return;
    }

    if (isSearch) {
        const q = rawPath.replace('/search_', '').replace('/search', '');
        const queryDecoded = decodeURIComponent(q);
        container.innerHTML = `
            <span class="gd-crumb gd-crumb-target" data-path="/" onclick="navigateToPath('/')">My Drive</span>
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

        if (i > 0) {
            html += `<span class="gd-crumb-sep">›</span>`;
        }

        if (isLast) {
            html += `<span class="gd-crumb gd-crumb-active gd-crumb-target" data-path="${item.path}" data-id="${item.id}" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</span>`;
        } else {
            html += `<span class="gd-crumb gd-crumb-target" data-path="${item.path}" data-id="${item.id}" onclick="navigateToPath('${item.path}')" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</span>`;
        }
    }

    container.innerHTML = html;

    // Attach Drag & Drop to each breadcrumb item (so user can drop onto ancestor folders)
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

// Main Directory Renderer
function showDirectory(data, breadcrumbs) {
    CURRENT_DIRECTORY_DATA = data;
    const contents = data['contents'] || {};
    DIRECTORY_ITEMS = contents;
    const isTrash = getCurrentPath().startsWith('/trash');

    if (window.CURRENT_PAGE_VIEW !== 'sync') {
        updateBreadcrumbs(breadcrumbs);
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
    let folders = entries.filter(([key, value]) => value.type === 'folder');
    let files = entries.filter(([key, value]) => value.type === 'file');

    folders.sort((a, b) => new Date(b[1].upload_date) - new Date(a[1].upload_date));
    files.sort((a, b) => new Date(b[1].upload_date) - new Date(a[1].upload_date));

    // Handle Empty State
    if (folders.length === 0 && files.length === 0) {
        const emptyHtml = `
            <tr>
                <td colspan="6" style="text-align: center; padding: 60px 20px; color: var(--gd-text-tertiary);">
                    <div style="font-size: 3rem; margin-bottom: 12px;">📁</div>
                    <div style="font-size: 1.1rem; font-weight: 500; color: var(--gd-text-secondary);">No files in this folder</div>
                    <div style="font-size: 0.85rem; margin-top: 6px;">Use "+ New" button or drag & drop files here to upload</div>
                </td>
            </tr>
        `;
        tableBody.innerHTML = emptyHtml;
        gridFolders.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--gd-text-tertiary);">Folder is empty</div>`;
        gridFoldersTitle.style.display = 'none';
        gridFilesTitle.style.display = 'none';
        return;
    }

    gridFoldersTitle.style.display = folders.length ? 'block' : 'none';
    gridFilesTitle.style.display = files.length ? 'block' : 'none';

    let tableHtml = '';
    let menusHtml = '';

    // 1. Render Folders
    for (const [key, item] of folders) {
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const owner = item.owner || 'Admin';

        // Table Row
        tableHtml += `
            <tr draggable="false" data-path="${item.path}" data-id="${item.id}" data-name="${escapeHtml(item.name)}" class="body-tr folder-tr">
                <td class="col-name-td">
                    <div class="td-align file-name-cell">
                        ${badge}
                        <span class="file-name-text" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
                    </div>
                </td>
                <td class="col-type-td"><div class="td-align"><span class="type-pill pill-folder">Folder</span></div></td>
                <td class="col-owner-td"><div class="td-align"><span class="owner-pill">${escapeHtml(owner)}</span></div></td>
                <td class="col-date-td"><div class="td-align date-text">${dateStr}</div></td>
                <td class="col-size-td"><div class="td-align size-text">--</div></td>
                <td class="col-more-td">
                    <div class="td-align td-actions">
                        <a data-id="${item.id}" class="more-btn" title="More actions"><img src="static/assets/more-icon.svg"></a>
                    </div>
                </td>
            </tr>
        `;

        // Grid Folder Chip
        const folderChip = document.createElement('div');
        folderChip.className = 'gd-folder-chip folder-tr';
        folderChip.setAttribute('draggable', 'false');
        folderChip.setAttribute('data-id', item.id);
        folderChip.setAttribute('data-path', item.path);
        folderChip.setAttribute('data-name', item.name);
        folderChip.innerHTML = `
            <div class="gd-folder-chip-left">
                <img class="item-icon-img" src="static/assets/folder-solid-icon.svg">
                <span class="gd-folder-chip-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
            </div>
            <a data-id="${item.id}" class="more-btn" title="More actions"><img src="static/assets/more-icon.svg"></a>
        `;
        gridFolders.appendChild(folderChip);

        // Context / More Menus
        if (isTrash) {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete permanently</div></div>`;
        } else {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="move-${item.id}"><img src="static/assets/folder-solid-icon.svg"> Move to...</div><hr><div id="copy-${item.id}"><img src="static/assets/copy-icon.svg"> Make a copy</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="folder-share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
        }
    }

    // 2. Render Files
    for (const [key, item] of files) {
        const size = convertBytes(item.size);
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const category = item.category || 'File';
        const owner = item.owner || 'Admin';

        // Table Row
        tableHtml += `
            <tr draggable="false" data-path="${item.path}" data-id="${item.id}" data-name="${escapeHtml(item.name)}" class="body-tr file-tr">
                <td class="col-name-td">
                    <div class="td-align file-name-cell">
                        ${badge}
                        <span class="file-name-text" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
                    </div>
                </td>
                <td class="col-type-td"><div class="td-align"><span class="type-pill">${category}</span></div></td>
                <td class="col-owner-td"><div class="td-align"><span class="owner-pill">${escapeHtml(owner)}</span></div></td>
                <td class="col-date-td"><div class="td-align date-text">${dateStr}</div></td>
                <td class="col-size-td"><div class="td-align size-text">${size}</div></td>
                <td class="col-more-td">
                    <div class="td-align td-actions">
                        <a data-id="${item.id}" class="more-btn" title="More actions"><img src="static/assets/more-icon.svg"></a>
                    </div>
                </td>
            </tr>
        `;

        // Grid File Card
        const fileCard = document.createElement('div');
        fileCard.className = 'gd-file-card file-tr';
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
                ${previewInnerHtml}
                <a data-id="${item.id}" class="more-btn gd-file-card-more-btn" title="More actions" onclick="event.stopPropagation();"><img src="static/assets/more-icon.svg"></a>
            </div>
            <div class="gd-file-card-body">
                <div class="gd-file-card-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
                <div class="gd-file-card-meta">
                    <span>${category}</span>
                    <span>${size}</span>
                </div>
            </div>
        `;
        gridFiles.appendChild(fileCard);

        // Context / More Menus
        if (isTrash) {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete permanently</div></div>`;
        } else {
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="preview-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Preview / Open</div><hr><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="move-${item.id}"><img src="static/assets/folder-solid-icon.svg"> Move to...</div><hr><div id="copy-${item.id}"><img src="static/assets/copy-icon.svg"> Make a copy</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
        }
    }

    tableBody.innerHTML = tableHtml;
    const ctxContainer = document.getElementById('context-menus-container');
    if (ctxContainer) ctxContainer.innerHTML = menusHtml;

    // Attach Click and Double Click Events
    if (!isTrash) {
        // Folders
        document.querySelectorAll('.folder-tr').forEach(el => {
            el.ondblclick = openFolder;
            el.onclick = function (e) {
                if (e.target.closest('.more-btn')) return;
                selectItem(this.getAttribute('data-id'));
                openFolder.call(this);
            };
        });

        // Files
        document.querySelectorAll('.file-tr').forEach(el => {
            el.ondblclick = openFilePreview;
            el.onclick = function (e) {
                if (e.target.closest('.more-btn')) return;
                if (window.innerWidth <= 768) {
                    openFilePreview.call(this);
                } else {
                    selectItem(this.getAttribute('data-id'));
                }
            };
        });
    }

    document.querySelectorAll('.more-btn').forEach(div => {
        div.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openMoreButton(div);
        });
    });
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
    if (CURRENT_BREADCRUMBS && CURRENT_BREADCRUMBS.length > 0) {
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
    if (propSizeEl) propSizeEl.innerText = isFolder ? '--' : `${convertBytes(item.size)} (${(item.size || 0).toLocaleString()} bytes)`;
    const propStorageEl = document.getElementById('insp-prop-storage');
    if (propStorageEl) propStorageEl.innerText = isFolder ? '0 bytes (virtual)' : convertBytes(item.size);
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

// In-App File & Media Preview Lightbox
function openFilePreview() {
    const id = this.getAttribute ? this.getAttribute('data-id') : (typeof this.id === 'string' ? this.id : null);
    const item = (typeof DIRECTORY_ITEMS !== 'undefined' && id) ? DIRECTORY_ITEMS[id] : null;
    if (!item) return;

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

    const imageExts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff', '.avif'];
    const videoExts = ['.mp4', '.mkv', '.webm', '.mov', '.avi', '.ts', '.ogv', '.m4v'];
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

    if (holder) {
        holder.innerHTML = '';
        if (isImage) {
            holder.innerHTML = `<img src="${directUrl}" alt="${escapeHtml(item.name)}" style="max-width:90vw; max-height:80vh; object-fit:contain;">`;
        } else if (isVideo) {
            holder.innerHTML = `<video controls autoplay playsinline style="max-width:90vw; max-height:80vh;"><source src="${directUrl}">Your browser does not support video playback.</video>`;
        } else if (isAudio) {
            holder.innerHTML = `
                <div class="gd-preview-audio-wrap">
                    <div class="gd-preview-audio-icon">🎵</div>
                    <div class="gd-preview-audio-title">${escapeHtml(item.name)}</div>
                    <div class="gd-preview-audio-size">${convertBytes(item.size)}</div>
                    <audio controls autoplay style="width: 100%; max-width: 420px;"><source src="${directUrl}">Your browser does not support audio playback.</audio>
                </div>`;
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
        } else {
            // Not previewable in browser -> download directly
            showToast('Downloading ' + item.name + '... ⬇️');
            const dl = document.createElement('a');
            dl.href = directUrl;
            dl.download = item.name;
            dl.target = '_blank';
            document.body.appendChild(dl);
            dl.click();
            document.body.removeChild(dl);
            return;
        }
    }

    if (lightbox) lightbox.classList.add('active');
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
            const directUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath) : `${getRootUrl()}/file?path=${encodeURIComponent(filePath)}`;
            window.open(directUrl, '_blank');
        });
    }

    // Preview Lightbox Close
    const previewClose = document.getElementById('preview-close-btn');
    const previewLightbox = document.getElementById('media-preview-modal');
    function closePreviewLightbox() {
        if (previewLightbox) {
            previewLightbox.classList.remove('active');
            const holder = document.getElementById('preview-content-holder');
            if (holder) holder.innerHTML = '';
        }
    }
    if (previewClose && previewLightbox) {
        previewClose.addEventListener('click', closePreviewLightbox);
    }
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closePreviewLightbox();
        }
    });

    setupDragAndDrop();
    applyViewMode(CURRENT_VIEW_MODE);
    initSyncActivityManager();

    // Initial Auth / Fetch
    const initialPath = getCurrentPath();
    if (initialPath.includes('/share_')) {
        getCurrentDirectory();
    } else {
        if (getPassword() === null) {
            const bg = document.getElementById('bg-blur');
            const login = document.getElementById('get-password');
            if (bg) { bg.style.zIndex = '100'; bg.style.opacity = '1'; }
            if (login) { login.style.zIndex = '101'; login.style.opacity = '1'; }
        } else {
            getCurrentDirectory();
        }
    }
});

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
        try {
            const pwd = getPassword();
            if (!pwd) return;
            const res = await fetch('/api/getSyncStatus', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pwd })
            });
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

