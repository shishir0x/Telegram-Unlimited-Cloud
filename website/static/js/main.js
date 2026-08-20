// Google Drive Main Application Controller
let CURRENT_DIRECTORY_DATA = {};
let DIRECTORY_ITEMS = {};
let CURRENT_BREADCRUMBS = [];
let SELECTED_ITEM_ID = null;
let CURRENT_VIEW_MODE = localStorage.getItem('gd_view_mode') || 'list'; // 'list' or 'grid'
window.DRAGGED_DRIVE_ITEM = null;

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

            const draggedItem = getDraggedItem(e);
            if (draggedItem) {
                const srcPath = draggedItem.path;
                if (srcPath === targetPath) return;
                moveFileFolder(srcPath, targetPath);
                clearDraggedItem();
            } else if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                showToast(`Uploading ${e.dataTransfer.files.length} file(s) into "${crumbEl.innerText.trim()}"...`);
                uploadFilesQueue(e.dataTransfer.files, targetPath);
            }
        });
    });
}

// Drag & Drop Helpers for Items (Supports cross-window / browser-to-browser drag & drop)
function handleItemDragStart(e) {
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
    e.preventDefault();
    e.stopPropagation();
    this.classList.add('gd-drop-hover');
    e.dataTransfer.dropEffect = 'move';
}

function handleFolderDragLeave(e) {
    this.classList.remove('gd-drop-hover');
}

function handleFolderDrop(e) {
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

    updateBreadcrumbs(breadcrumbs);

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
    let menusHtml = '';

    // 1. Render Folders
    for (const [key, item] of folders) {
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const owner = item.owner || 'Admin';

        // Table Row
        tableHtml += `
            <tr draggable="${!isTrash}" data-path="${item.path}" data-id="${item.id}" data-name="${escapeHtml(item.name)}" class="body-tr folder-tr">
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
        folderChip.setAttribute('draggable', !isTrash);
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
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> File details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="folder-share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
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
            <tr draggable="${!isTrash}" data-path="${item.path}" data-id="${item.id}" data-name="${escapeHtml(item.name)}" class="body-tr file-tr">
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
        fileCard.setAttribute('draggable', !isTrash);
        fileCard.setAttribute('data-id', item.id);
        fileCard.setAttribute('data-path', item.path);
        fileCard.setAttribute('data-name', item.name);
        fileCard.innerHTML = `
            <div class="gd-file-card-preview">
                <span style="font-size: 2.2rem;">${getBigIconEmoji(item)}</span>
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
            menusHtml += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${escapeHtml(item.name)}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="preview-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Preview / Open</div><hr><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> File details</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Move to trash</div><hr><div id="share-${item.id}"><img src="static/assets/share-icon.svg"> Share link</div></div>`;
        }
    }

    tableBody.innerHTML = tableHtml;
    const ctxContainer = document.getElementById('context-menus-container');
    if (ctxContainer) ctxContainer.innerHTML = menusHtml;

    // Attach Click and Double Click & Drag Events
    if (!isTrash) {
        // Folders
        document.querySelectorAll('.folder-tr').forEach(el => {
            el.ondblclick = openFolder;
            el.onclick = function (e) {
                if (e.target.closest('.more-btn')) return;
                if (window.innerWidth <= 768) {
                    openFolder.call(this);
                } else {
                    selectItem(this.getAttribute('data-id'));
                }
            };

            // Draggable item
            el.addEventListener('dragstart', handleItemDragStart);
            el.addEventListener('dragend', handleItemDragEnd);

            // Drop target folder
            el.addEventListener('dragover', handleFolderDragOver);
            el.addEventListener('dragleave', handleFolderDragLeave);
            el.addEventListener('drop', handleFolderDrop);
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

            // Draggable item
            el.addEventListener('dragstart', handleItemDragStart);
            el.addEventListener('dragend', handleItemDragEnd);
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
    const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
    const directUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath) : `${rootUrl}/file?path=${encodeURIComponent(filePath)}`;

    // Build human-readable location path
    let readableLocation = 'My Drive';
    if (CURRENT_BREADCRUMBS && CURRENT_BREADCRUMBS.length > 0) {
        readableLocation = CURRENT_BREADCRUMBS.map(c => c.name).join(' / ');
    }

    const headerTitle = document.getElementById('insp-header-title');
    if (headerTitle) headerTitle.innerText = isFolder ? 'Folder Details' : 'File Details';

    document.getElementById('insp-filename').innerText = item.name;
    document.getElementById('insp-big-icon').innerText = getBigIconEmoji(item);
    document.getElementById('insp-prop-type').innerText = item.category || (isFolder ? 'Folder' : 'File');
    document.getElementById('insp-prop-size').innerText = isFolder ? '--' : `${convertBytes(item.size)} (${(item.size || 0).toLocaleString()} bytes)`;
    document.getElementById('insp-prop-storage').innerText = isFolder ? '0 bytes (virtual)' : convertBytes(item.size);
    document.getElementById('insp-prop-location').innerText = readableLocation;
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

        // Only handle generic viewport drop if not dropped on a specific folder or nav target
        if (e.target.closest('.folder-tr') || e.target.closest('.gd-crumb-target') || e.target.closest('#nav-my-drive') || e.target.closest('#nav-trash')) {
            return;
        }

        e.preventDefault();

        const draggedItem = getDraggedItem(e);
        if (draggedItem) {
            const currentPath = getCurrentPath();
            if (draggedItem.path !== currentPath) {
                moveFileFolder(draggedItem.path, currentPath);
            }
            clearDraggedItem();
        } else if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
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

            const draggedItem = getDraggedItem(e);
            if (draggedItem) {
                moveFileFolder(draggedItem.path, '/');
                clearDraggedItem();
            } else if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
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
            inspector.classList.toggle('hidden');
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
