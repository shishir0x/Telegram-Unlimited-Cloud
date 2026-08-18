// Google Drive Main Application Controller
let CURRENT_DIRECTORY_DATA = {};
let DIRECTORY_ITEMS = {};
let SELECTED_ITEM_ID = null;
let CURRENT_VIEW_MODE = localStorage.getItem('gd_view_mode') || 'list'; // 'list' or 'grid'

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

// Build Interactive Breadcrumbs
function updateBreadcrumbs() {
    const container = document.getElementById('breadcrumbs-container');
    if (!container) return;

    const rawPath = getCurrentPath();
    if (rawPath === '/' || rawPath === '' || rawPath === 'redirect') {
        container.innerHTML = `<span class="gd-crumb gd-crumb-active" data-path="/">My Drive</span>`;
        return;
    }

    if (rawPath.startsWith('/trash')) {
        container.innerHTML = `<span class="gd-crumb gd-crumb-active">Trash</span>`;
        return;
    }

    if (rawPath.startsWith('/search')) {
        const q = rawPath.replace('/search_', '');
        container.innerHTML = `
            <span class="gd-crumb" onclick="window.location.href='/?path=/'">My Drive</span>
            <span class="gd-crumb-sep">›</span>
            <span class="gd-crumb gd-crumb-active">Search: "${decodeURIComponent(q)}"</span>
        `;
        return;
    }

    const clean = rawPath.replace('/share_', '').replace(/^\/+|\/+$/g, '');
    const parts = clean.split('/').filter(Boolean);

    let html = `<span class="gd-crumb" onclick="window.location.href='/?path=/'">My Drive</span>`;
    let accumulatedPath = '';

    for (let i = 0; i < parts.length; i++) {
        accumulatedPath += '/' + parts[i];
        const isLast = i === parts.length - 1;
        const displayName = parts[i];

        html += `<span class="gd-crumb-sep">›</span>`;
        if (isLast) {
            html += `<span class="gd-crumb gd-crumb-active">${displayName}</span>`;
        } else {
            const targetUrl = `/?path=${accumulatedPath}`;
            html += `<span class="gd-crumb" onclick="window.location.href='${targetUrl}'">${displayName}</span>`;
        }
    }

    container.innerHTML = html;
}

// Main Directory Renderer
function showDirectory(data) {
    CURRENT_DIRECTORY_DATA = data;
    const contents = data['contents'] || {};
    DIRECTORY_ITEMS = contents;
    const isTrash = getCurrentPath().startsWith('/trash');

    updateBreadcrumbs();

    const tableBody = document.getElementById('directory-data');
    const gridFolders = document.getElementById('grid-folders-data');
    const gridFiles = document.getElementById('grid-files-data');
    const gridFoldersTitle = document.getElementById('grid-folders-title');
    const gridFilesTitle = document.getElementById('grid-files-title');

    tableBody.innerHTML = '';
    gridFolders.innerHTML = '';
    gridFiles.innerHTML = '';

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

    // 1. Render Folders
    for (const [key, item] of folders) {
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const owner = item.owner || 'Admin';

        // Table Row
        tableHtml += `
            <tr data-path="${item.path}" data-id="${item.id}" data-name="${item.name}" class="body-tr folder-tr">
                <td>
                    <div class="td-align file-name-cell">
                        ${badge}
                        <span class="file-name-text" title="${item.name}">${item.name}</span>
                    </div>
                </td>
                <td><div class="td-align"><span class="type-pill pill-folder">Folder</span></div></td>
                <td><div class="td-align"><span class="owner-pill">${owner}</span></div></td>
                <td><div class="td-align date-text">${dateStr}</div></td>
                <td><div class="td-align size-text">--</div></td>
                <td>
                    <div class="td-align td-actions">
                        <a data-id="${item.id}" class="more-btn"><img src="static/assets/more-icon.svg"></a>
                    </div>
                </td>
            </tr>
        `;

        // Grid Folder Chip
        const folderChip = document.createElement('div');
        folderChip.className = 'gd-folder-chip folder-tr';
        folderChip.setAttribute('data-id', item.id);
        folderChip.setAttribute('data-path', item.path);
        folderChip.setAttribute('data-name', item.name);
        folderChip.innerHTML = `
            <div class="gd-folder-chip-left">
                <img class="item-icon-img" src="static/assets/folder-solid-icon.svg">
                <span class="gd-folder-chip-name" title="${item.name}">${item.name}</span>
            </div>
            <a data-id="${item.id}" class="more-btn"><img src="static/assets/more-icon.svg"></a>
        `;
        gridFolders.appendChild(folderChip);

        // Context / More Menus
        if (isTrash) {
            tableHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete</div></div>`;
        } else {
            tableHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> File details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="folder-share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
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
            <tr data-path="${item.path}" data-id="${item.id}" data-name="${item.name}" class="body-tr file-tr">
                <td>
                    <div class="td-align file-name-cell">
                        ${badge}
                        <span class="file-name-text" title="${item.name}">${item.name}</span>
                    </div>
                </td>
                <td><div class="td-align"><span class="type-pill">${category}</span></div></td>
                <td><div class="td-align"><span class="owner-pill">${owner}</span></div></td>
                <td><div class="td-align date-text">${dateStr}</div></td>
                <td><div class="td-align size-text">${size}</div></td>
                <td>
                    <div class="td-align td-actions">
                        <a data-id="${item.id}" class="more-btn"><img src="static/assets/more-icon.svg"></a>
                    </div>
                </td>
            </tr>
        `;

        // Grid File Card
        const fileCard = document.createElement('div');
        fileCard.className = 'gd-file-card file-tr';
        fileCard.setAttribute('data-id', item.id);
        fileCard.setAttribute('data-path', item.path);
        fileCard.setAttribute('data-name', item.name);
        fileCard.innerHTML = `
            <div class="gd-file-card-preview">
                <span style="font-size: 2.2rem;">${getBigIconEmoji(item)}</span>
            </div>
            <div class="gd-file-card-body">
                <div class="gd-file-card-name" title="${item.name}">${item.name}</div>
                <div class="gd-file-card-meta">
                    <span>${category}</span>
                    <span>${size}</span>
                </div>
            </div>
        `;
        gridFiles.appendChild(fileCard);

        // Context / More Menus
        if (isTrash) {
            tableHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete</div></div>`;
        } else {
            tableHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> File details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
        }
    }

    tableBody.innerHTML = tableHtml;

    // Attach Click and Double Click Events
    if (!isTrash) {
        document.querySelectorAll('.folder-tr').forEach(el => {
            el.ondblclick = openFolder;
            el.onclick = function (e) {
                if (e.target.closest('.more-btn')) return;
                selectItem(this.getAttribute('data-id'));
            };
        });

        document.querySelectorAll('.file-tr').forEach(el => {
            el.ondblclick = openFilePreview;
            el.onclick = function (e) {
                if (e.target.closest('.more-btn')) return;
                selectItem(this.getAttribute('data-id'));
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

    // Auto-select first item if available to populate inspector
    if (files.length > 0) {
        selectItem(files[0][1].id);
    } else if (folders.length > 0) {
        selectItem(folders[0][1].id);
    }
}

// Select Item and Populate Inspector Panel
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
    const filePath = (item.path + '/' + item.id).replace('//', '/');
    const directUrl = `${rootUrl}/file?path=${filePath}`;

    document.getElementById('insp-filename').innerText = item.name;
    document.getElementById('insp-big-icon').innerText = getBigIconEmoji(item);
    document.getElementById('insp-prop-type').innerText = item.category || (isFolder ? 'Folder' : 'File');
    document.getElementById('insp-prop-size').innerText = isFolder ? '--' : `${convertBytes(item.size)} (${(item.size || 0).toLocaleString()} bytes)`;
    document.getElementById('insp-prop-storage').innerText = isFolder ? '0 bytes (virtual)' : convertBytes(item.size);
    document.getElementById('insp-prop-location').innerText = item.path || 'My Drive';
    document.getElementById('insp-prop-owner').innerText = item.owner || 'Admin (You)';
    document.getElementById('insp-prop-date').innerText = item.upload_date || '--';
    document.getElementById('insp-prop-msg-id').innerText = item.file_id ? `#${item.file_id}` : (isFolder ? 'Virtual' : '--');

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

    if (mode === 'grid') {
        listContainer.style.display = 'none';
        gridContainer.style.display = 'block';
        listBtn.removeAttribute('active');
        gridBtn.setAttribute('active', '');
    } else {
        listContainer.style.display = 'block';
        gridContainer.style.display = 'none';
        gridBtn.removeAttribute('active');
        listBtn.setAttribute('active', '');
    }
}

// In-App Media Preview Lightbox
function openFilePreview() {
    const id = this.getAttribute('data-id');
    const item = DIRECTORY_ITEMS[id];
    if (!item) return;

    const fileName = item.name.toLowerCase();
    const path = (item.path + '/' + item.id).replace('//', '/');
    const rootUrl = getRootUrl();
    const directUrl = `${rootUrl}/file?path=${path}`;
    const streamUrl = `${rootUrl}/stream?url=${encodeURIComponent(directUrl)}`;

    const lightbox = document.getElementById('media-preview-modal');
    const title = document.getElementById('preview-filename');
    const holder = document.getElementById('preview-content-holder');
    const dlBtn = document.getElementById('preview-download-btn');

    title.innerText = item.name;
    dlBtn.onclick = () => window.open(directUrl, '_blank');

    holder.innerHTML = '';

    if (fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi')) {
        holder.innerHTML = `<video controls autoplay style="max-width: 90vw; max-height: 80vh;"><source src="${streamUrl}" type="video/mp4">Your browser does not support video playback.</video>`;
    } else if (fileName.endsWith('.mp3') || fileName.endsWith('.wav') || fileName.endsWith('.ogg') || fileName.endsWith('.flac') || fileName.endsWith('.m4a')) {
        holder.innerHTML = `<audio controls autoplay style="width: 400px;"><source src="${directUrl}">Your browser does not support audio playback.</audio>`;
    } else if (fileName.endsWith('.jpg') || fileName.endsWith('.jpeg') || fileName.endsWith('.png') || fileName.endsWith('.gif') || fileName.endsWith('.webp') || fileName.endsWith('.svg')) {
        holder.innerHTML = `<img src="${directUrl}" alt="${item.name}">`;
    } else if (fileName.endsWith('.pdf')) {
        holder.innerHTML = `<iframe src="${directUrl}"></iframe>`;
    } else {
        // Fallback open directly in browser tab
        window.open(directUrl, '_blank');
        return;
    }

    lightbox.classList.add('active');
}

// Drag & Drop Upload Zone
function setupDragAndDrop() {
    const viewport = document.getElementById('content-viewport');
    const dropOverlay = document.getElementById('drop-overlay');

    if (!viewport || !dropOverlay) return;

    window.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropOverlay.classList.add('active');
    });

    window.addEventListener('dragleave', (e) => {
        if (e.relatedTarget === null || e.clientX === 0 || e.clientY === 0) {
            dropOverlay.classList.remove('active');
        }
    });

    window.addEventListener('drop', (e) => {
        e.preventDefault();
        dropOverlay.classList.remove('active');

        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            const fileInput = document.getElementById('fileInput');
            fileInput.files = e.dataTransfer.files;
            const event = new Event('change');
            fileInput.dispatchEvent(event);
        }
    });
}

// Search Form
document.addEventListener('DOMContentLoaded', () => {
    const searchForm = document.getElementById('search-form');
    const searchInput = document.getElementById('file-search');
    const searchClear = document.getElementById('search-clear-btn');

    if (searchForm && searchInput) {
        searchForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const q = searchInput.value.trim();
            if (!q) return;
            window.location = `/?path=/search_${encodeURIComponent(q)}`;
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
                window.location = '/?path=/';
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
            inspector.classList.toggle('hidden');
        });
    }

    if (closeInspBtn && inspector) {
        closeInspBtn.addEventListener('click', () => {
            inspector.classList.add('hidden');
        });
    }

    // Inspector Copy Link
    const inspCopyBtn = document.getElementById('insp-copy-btn');
    const inspLinkInput = document.getElementById('insp-link-input');
    if (inspCopyBtn && inspLinkInput) {
        inspCopyBtn.addEventListener('click', () => {
            copyTextToClipboard(inspLinkInput.value);
            inspCopyBtn.innerText = 'Copied! ✅';
            setTimeout(() => { inspCopyBtn.innerText = 'Copy'; }, 2000);
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
                window.location.href = `/?path=${(item.path + '/' + item.id).replace('//', '/')}`;
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
            const directUrl = `${getRootUrl()}/file?path=${(item.path + '/' + item.id).replace('//', '/')}`;
            window.open(directUrl, '_blank');
        });
    }

    // Preview Lightbox Close
    const previewClose = document.getElementById('preview-close-btn');
    const previewLightbox = document.getElementById('media-preview-modal');
    if (previewClose && previewLightbox) {
        previewClose.addEventListener('click', () => {
            previewLightbox.classList.remove('active');
            document.getElementById('preview-content-holder').innerHTML = '';
        });
    }

    setupDragAndDrop();
    applyViewMode(CURRENT_VIEW_MODE);

    // Initial Auth / Fetch
    if (getCurrentPath().includes('/share_')) {
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
