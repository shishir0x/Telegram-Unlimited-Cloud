function openFolder() {
    let path = (getCurrentPath() + '/' + this.getAttribute('data-id') + '/').replaceAll('//', '/');

    const auth = getFolderAuthFromPath();
    if (auth) {
        path = path + '&auth=' + auth;
    }
    window.location.href = `/?path=${path}`;
}

function openFile() {
    const fileName = this.getAttribute('data-name').toLowerCase();
    let path = '/file?path=' + this.getAttribute('data-path') + '/' + this.getAttribute('data-id');

    if (fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi') || fileName.endsWith('.ts') || fileName.endsWith('.ogv')) {
        path = '/stream?url=' + getRootUrl() + path;
    }

    window.open(path, '_blank');
}

// ==========================================
// Properties / Details Modal Handler
// ==========================================
let CURRENT_DETAILS_ITEM = null;

function openPropertiesModal(item) {
    CURRENT_DETAILS_ITEM = item;
    const isFolder = item.type === 'folder';
    const rootUrl = getRootUrl();
    const filePath = (item.path + '/' + item.id).replace('//', '/');
    const directFileUrl = `${rootUrl}/file?path=${filePath}`;

    document.getElementById('details-title').innerHTML = isFolder ? '📁 Folder Properties' : '📄 File Properties';
    document.getElementById('detail-name').innerText = item.name || '--';
    document.getElementById('detail-type').innerText = item.category ? `${item.category} (${item.mime_type || item.extension || 'application'})` : (isFolder ? 'Folder' : 'File');
    document.getElementById('detail-size').innerText = isFolder ? 'Folder (Multiple items)' : `${convertBytes(item.size)} (${(item.size || 0).toLocaleString()} bytes)`;
    document.getElementById('detail-location').innerText = item.path || '/';
    document.getElementById('detail-owner').innerText = item.owner || 'Admin';
    document.getElementById('detail-date').innerText = item.upload_date || '--';
    document.getElementById('detail-msg-id').innerText = item.file_id ? `#${item.file_id}` : (isFolder ? 'Virtual Directory' : '--');

    const linkInput = document.getElementById('detail-link-input');
    if (isFolder) {
        linkInput.value = `${rootUrl}/?path=${filePath}`;
    } else {
        linkInput.value = directFileUrl;
    }

    document.getElementById('bg-blur').style.zIndex = '4';
    document.getElementById('bg-blur').style.opacity = '0.4';

    const modal = document.getElementById('file-details-modal');
    modal.style.zIndex = '5';
    modal.style.opacity = '1';
}

function closePropertiesModal() {
    document.getElementById('bg-blur').style.opacity = '0';
    setTimeout(() => {
        document.getElementById('bg-blur').style.zIndex = '-1';
    }, 300);

    const modal = document.getElementById('file-details-modal');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.style.zIndex = '-1';
    }, 300);
}

document.addEventListener('DOMContentLoaded', () => {
    const closeBtn = document.getElementById('details-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closePropertiesModal);

    const copyBtn = document.getElementById('detail-copy-link-btn');
    if (copyBtn) {
        copyBtn.addEventListener('click', () => {
            const input = document.getElementById('detail-link-input');
            copyTextToClipboard(input.value);
            const originalText = copyBtn.innerText;
            copyBtn.innerText = 'Copied! ✅';
            setTimeout(() => { copyBtn.innerText = originalText; }, 2000);
        });
    }

    const openBtn = document.getElementById('detail-open-btn');
    if (openBtn) {
        openBtn.addEventListener('click', () => {
            if (!CURRENT_DETAILS_ITEM) return;
            const item = CURRENT_DETAILS_ITEM;
            if (item.type === 'folder') {
                window.location.href = `/?path=${(item.path + '/' + item.id).replace('//', '/')}`;
            } else {
                let path = '/file?path=' + item.path + '/' + item.id;
                const fileName = (item.name || '').toLowerCase();
                if (fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi')) {
                    path = '/stream?url=' + getRootUrl() + path;
                }
                window.open(path, '_blank');
            }
        });
    }

    const dlBtn = document.getElementById('detail-download-btn');
    if (dlBtn) {
        dlBtn.addEventListener('click', () => {
            if (!CURRENT_DETAILS_ITEM) return;
            const item = CURRENT_DETAILS_ITEM;
            if (item.type === 'folder') {
                alert('Folder direct download as zip will be supported soon. You can open and download individual files.');
            } else {
                const path = '/file?path=' + item.path + '/' + item.id;
                window.open(path, '_blank');
            }
        });
    }
});

// File More Button Handler Start
function openMoreButton(div) {
    const id = div.getAttribute('data-id');
    const moreDiv = document.getElementById(`more-option-${id}`);
    if (!moreDiv) return;

    const rect = div.getBoundingClientRect();
    const x = rect.left + window.scrollX - 40;
    const y = rect.top + window.scrollY;

    moreDiv.style.zIndex = 2;
    moreDiv.style.opacity = 1;
    moreDiv.style.left = `${x}px`;
    moreDiv.style.top = `${y}px`;

    const isTrash = getCurrentPath().includes('/trash');

    const focusInput = moreDiv.querySelector('.more-options-focus');
    if (focusInput) {
        focusInput.focus();
        focusInput.addEventListener('blur', closeMoreBtnFocus);
        focusInput.addEventListener('focusout', closeMoreBtnFocus);
    }

    if (!isTrash) {
        const detailsOpt = moreDiv.querySelector(`#details-opt-${id}`);
        if (detailsOpt) {
            detailsOpt.onclick = () => {
                closeMoreMenu(moreDiv);
                const item = DIRECTORY_ITEMS[id];
                if (item) openPropertiesModal(item);
            };
        }

        const renameBtn = moreDiv.querySelector(`#rename-${id}`);
        if (renameBtn) renameBtn.onclick = renameFileFolder;

        const trashBtn = moreDiv.querySelector(`#trash-${id}`);
        if (trashBtn) trashBtn.onclick = trashFileFolder;

        const shareBtn = moreDiv.querySelector(`#share-${id}`);
        if (shareBtn) shareBtn.onclick = shareFile;

        const folderShareBtn = moreDiv.querySelector(`#folder-share-${id}`);
        if (folderShareBtn) folderShareBtn.onclick = shareFolder;
    } else {
        const restoreBtn = moreDiv.querySelector(`#restore-${id}`);
        if (restoreBtn) restoreBtn.onclick = restoreFileFolder;

        const delBtn = moreDiv.querySelector(`#delete-${id}`);
        if (delBtn) delBtn.onclick = deleteFileFolder;
    }
}

function closeMoreMenu(moreDiv) {
    if (!moreDiv) return;
    moreDiv.style.opacity = '0';
    setTimeout(() => {
        moreDiv.style.zIndex = '-1';
    }, 300);
}

function closeMoreBtnFocus() {
    closeMoreMenu(this.parentElement);
}

// Rename File Folder Start
function renameFileFolder() {
    const id = this.getAttribute('id').split('-')[1];
    document.getElementById('rename-name').value = this.parentElement.getAttribute('data-name');
    document.getElementById('bg-blur').style.zIndex = '2';
    document.getElementById('bg-blur').style.opacity = '0.1';

    document.getElementById('rename-file-folder').style.zIndex = '3';
    document.getElementById('rename-file-folder').style.opacity = '1';
    document.getElementById('rename-file-folder').setAttribute('data-id', id);
    setTimeout(() => {
        document.getElementById('rename-name').focus();
    }, 300);
}

document.getElementById('rename-cancel').addEventListener('click', () => {
    document.getElementById('rename-name').value = '';
    document.getElementById('bg-blur').style.opacity = '0';
    setTimeout(() => {
        document.getElementById('bg-blur').style.zIndex = '-1';
    }, 300);
    document.getElementById('rename-file-folder').style.opacity = '0';
    setTimeout(() => {
        document.getElementById('rename-file-folder').style.zIndex = '-1';
    }, 300);
});

document.getElementById('rename-create').addEventListener('click', async () => {
    const name = document.getElementById('rename-name').value;
    if (name === '') {
        alert('Name cannot be empty');
        return;
    }

    const id = document.getElementById('rename-file-folder').getAttribute('data-id');
    const path = document.getElementById(`more-option-${id}`).getAttribute('data-path') + '/' + id;

    const data = {
        'name': name,
        'path': path
    };

    const response = await postJson('/api/renameFileFolder', data);
    if (response.status === 'ok') {
        alert('File/Folder Renamed Successfully');
        window.location.reload();
    } else {
        alert('Failed to rename file/folder');
        window.location.reload();
    }
});

// Trash & Restore
async function trashFileFolder() {
    const id = this.getAttribute('id').split('-')[1];
    const path = document.getElementById(`more-option-${id}`).getAttribute('data-path') + '/' + id;
    const data = {
        'path': path,
        'trash': true
    };
    const response = await postJson('/api/trashFileFolder', data);

    if (response.status === 'ok') {
        alert('File/Folder Sent to Trash Successfully');
        window.location.reload();
    } else {
        alert('Failed to Send File/Folder to Trash');
        window.location.reload();
    }
}

async function restoreFileFolder() {
    const id = this.getAttribute('id').split('-')[1];
    const path = this.getAttribute('data-path') + '/' + id;
    const data = {
        'path': path,
        'trash': false
    };
    const response = await postJson('/api/trashFileFolder', data);

    if (response.status === 'ok') {
        alert('File/Folder Restored Successfully');
        window.location.reload();
    } else {
        alert('Failed to Restore File/Folder');
        window.location.reload();
    }
}

async function deleteFileFolder() {
    const id = this.getAttribute('id').split('-')[1];
    const path = this.getAttribute('data-path') + '/' + id;
    const data = {
        'path': path
    };
    const response = await postJson('/api/deleteFileFolder', data);

    if (response.status === 'ok') {
        alert('File/Folder Deleted Successfully');
        window.location.reload();
    } else {
        alert('Failed to Delete File/Folder');
        window.location.reload();
    }
}

async function shareFile() {
    const fileName = this.parentElement.getAttribute('data-name').toLowerCase();
    const id = this.getAttribute('id').split('-')[1];
    const path = document.getElementById(`more-option-${id}`).getAttribute('data-path') + '/' + id;
    const root_url = getRootUrl();

    let link;
    if (fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi') || fileName.endsWith('.ts') || fileName.endsWith('.ogv')) {
        link = `${root_url}/stream?url=${root_url}/file?path=${path}`;
    } else {
        link = `${root_url}/file?path=${path}`;
    }

    copyTextToClipboard(link);
}

async function shareFolder() {
    const id = this.getAttribute('id').split('-')[2];
    let path = document.getElementById(`more-option-${id}`).getAttribute('data-path') + '/' + id;
    const root_url = getRootUrl();

    const auth = await getFolderShareAuth(path);
    path = path.slice(1);

    let link = `${root_url}/?path=/share_${path}&auth=${auth}`;
    copyTextToClipboard(link);
}