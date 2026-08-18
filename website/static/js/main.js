let CURRENT_DIRECTORY_DATA = {};
let DIRECTORY_ITEMS = {};

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

function formatDate(dateStr) {
    if (!dateStr) return '--';
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return dateStr;
        return d.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch {
        return dateStr;
    }
}

function showDirectory(data) {
    CURRENT_DIRECTORY_DATA = data;
    const contents = data['contents'] || {};
    DIRECTORY_ITEMS = contents;
    document.getElementById('directory-data').innerHTML = '';
    const isTrash = getCurrentPath().startsWith('/trash');

    let html = '';

    let entries = Object.entries(contents);
    let folders = entries.filter(([key, value]) => value.type === 'folder');
    let files = entries.filter(([key, value]) => value.type === 'file');

    folders.sort((a, b) => new Date(b[1].upload_date) - new Date(a[1].upload_date));
    files.sort((a, b) => new Date(b[1].upload_date) - new Date(a[1].upload_date));

    if (folders.length === 0 && files.length === 0) {
        document.getElementById('directory-data').innerHTML = `
            <tr>
                <td colspan="6" style="text-align: center; padding: 40px; color: #70757a;">
                    <div style="font-size: 1.1rem; font-weight: 500;">📁 This folder is empty</div>
                    <div style="font-size: 0.85rem; margin-top: 5px;">Upload files or sync from your device to see them here</div>
                </td>
            </tr>
        `;
        return;
    }

    // Render Folders
    for (const [key, item] of folders) {
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const owner = item.owner || 'Me';

        html += `
            <tr data-path="${item.path}" data-id="${item.id}" data-name="${item.name}" class="body-tr folder-tr">
                <td>
                    <div class="td-align">
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
                        <button class="info-quick-btn" data-id="${item.id}" title="Properties">ℹ️</button>
                        <a data-id="${item.id}" class="more-btn"><img src="static/assets/more-icon.svg" class="rotate-90"></a>
                    </div>
                </td>
            </tr>
        `;

        if (isTrash) {
            html += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete</div></div>`;
        } else {
            html += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Properties</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Trash</div><hr><div id="folder-share-${item.id}"><img src="static/assets/share-icon.svg"> Share</div></div>`;
        }
    }

    // Render Files
    for (const [key, item] of files) {
        const size = convertBytes(item.size);
        const badge = getFileBadge(item);
        const dateStr = formatDate(item.upload_date);
        const category = item.category || 'File';
        const owner = item.owner || 'Me';

        html += `
            <tr data-path="${item.path}" data-id="${item.id}" data-name="${item.name}" class="body-tr file-tr">
                <td>
                    <div class="td-align">
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
                        <button class="info-quick-btn" data-id="${item.id}" title="Properties">ℹ️</button>
                        <a data-id="${item.id}" class="more-btn"><img src="static/assets/more-icon.svg" class="rotate-90"></a>
                    </div>
                </td>
            </tr>
        `;

        if (isTrash) {
            html += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="restore-${item.id}" data-path="${item.path}"><img src="static/assets/load-icon.svg"> Restore</div><hr><div id="delete-${item.id}" data-path="${item.path}"><img src="static/assets/trash-icon.svg"> Delete</div></div>`;
        } else {
            html += `<div data-path="${item.path}" id="more-option-${item.id}" data-name="${item.name}" class="more-options"><input class="more-options-focus" readonly="readonly" style="height:0;width:0;border:none;position:absolute"><div id="details-opt-${item.id}"><img src="static/assets/info-icon-small.svg"> Properties</div><hr><div id="rename-${item.id}"><img src="static/assets/pencil-icon.svg"> Rename</div><hr><div id="trash-${item.id}"><img src="static/assets/trash-icon.svg"> Trash</div><hr><div id="share-${item.id}"><img src="static/assets/share-icon.svg"> Share</div></div>`;
        }
    }

    document.getElementById('directory-data').innerHTML = html;

    if (!isTrash) {
        document.querySelectorAll('.folder-tr').forEach(div => {
            div.ondblclick = openFolder;
        });
        document.querySelectorAll('.file-tr').forEach(div => {
            div.ondblclick = openFile;
        });
    }

    document.querySelectorAll('.more-btn').forEach(div => {
        div.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            openMoreButton(div);
        });
    });

    document.querySelectorAll('.info-quick-btn').forEach(btn => {
        btn.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            const id = btn.getAttribute('data-id');
            const item = DIRECTORY_ITEMS[id];
            if (item) {
                openPropertiesModal(item);
            }
        });
    });
}

document.getElementById('search-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const query = document.getElementById('file-search').value;
    if (query === '') {
        alert('Search field is empty');
        return;
    }
    const path = '/?path=/search_' + encodeURI(query);
    window.location = path;
});

// Loading Main Page
document.addEventListener('DOMContentLoaded', function () {
    const inputs = ['new-folder-name', 'rename-name', 'file-search'];
    for (let i = 0; i < inputs.length; i++) {
        const el = document.getElementById(inputs[i]);
        if (el) el.addEventListener('input', validateInput);
    }

    if (getCurrentPath().includes('/share_')) {
        getCurrentDirectory();
    } else {
        if (getPassword() === null) {
            document.getElementById('bg-blur').style.zIndex = '2';
            document.getElementById('bg-blur').style.opacity = '0.1';

            document.getElementById('get-password').style.zIndex = '3';
            document.getElementById('get-password').style.opacity = '1';
        } else {
            getCurrentDirectory();
        }
    }
});
