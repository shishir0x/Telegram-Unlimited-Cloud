function openFolder() {
    const folderId = this.getAttribute('data-id');
    const folderItem = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[folderId] : null;
    let targetPath;
    if (folderItem && folderItem.path) {
        targetPath = (folderItem.path + '/' + folderId).replaceAll('//', '/');
    } else {
        targetPath = (getCurrentPath() + '/' + folderId).replaceAll('//', '/');
    }
    navigateToPath(targetPath);
}

function openFile() {
    const fileName = (this.getAttribute('data-name') || '').toLowerCase();
    let filePath = this.getAttribute('data-path') + '/' + this.getAttribute('data-id');
    let path = '/file?path=' + filePath;

    const auth = getFolderAuthFromPath();
    if (auth) {
        path += '&auth=' + encodeURIComponent(auth);
    }

    if (fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi') || fileName.endsWith('.ts') || fileName.endsWith('.ogv')) {
        path = '/stream?url=' + encodeURIComponent(getRootUrl() + path);
    }

    window.open(path, '_blank');
}

// File More Button Handler Start
function openMoreButton(div) {
    const id = div.getAttribute('data-id');
    const moreDiv = document.getElementById(`more-option-${id}`);
    if (!moreDiv) return;

    const rect = div.getBoundingClientRect();
    const x = Math.min(rect.left + window.scrollX - 120, window.innerWidth - 220);
    const y = rect.top + window.scrollY + 20;

    moreDiv.style.zIndex = 200;
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
                selectItem(id);
                const inspector = document.getElementById('gd-inspector');
                if (inspector) inspector.classList.remove('hidden');
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
    }, 200);
}

function closeMoreBtnFocus() {
    closeMoreMenu(this.parentElement);
}

// Rename File Folder Start
function renameFileFolder() {
    const id = this.getAttribute('id').split('-')[1];
    document.getElementById('rename-name').value = this.parentElement.getAttribute('data-name');

    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('rename-file-folder');

    bgBlur.style.zIndex = '100';
    bgBlur.style.opacity = '1';

    modal.style.zIndex = '101';
    modal.style.opacity = '1';
    modal.setAttribute('data-id', id);

    setTimeout(() => {
        document.getElementById('rename-name').focus();
    }, 200);
}

document.addEventListener('DOMContentLoaded', () => {
    const renameCancel = document.getElementById('rename-cancel');
    const renameCreate = document.getElementById('rename-create');
    const renameInput = document.getElementById('rename-name');
    const bgBlur = document.getElementById('bg-blur');
    const renameModal = document.getElementById('rename-file-folder');

    function closeRenameModal() {
        if (renameInput) renameInput.value = '';
        if (bgBlur) bgBlur.style.opacity = '0';
        if (renameModal) renameModal.style.opacity = '0';
        setTimeout(() => {
            if (bgBlur) bgBlur.style.zIndex = '-1';
            if (renameModal) renameModal.style.zIndex = '-1';
        }, 200);
    }

    if (renameCancel) {
        renameCancel.addEventListener('click', closeRenameModal);
    }

    async function handleRenameSubmit() {
        const name = renameInput.value.trim();
        if (name === '') {
            alert('Name cannot be empty');
            return;
        }

        const id = renameModal.getAttribute('data-id');
        const moreDiv = document.getElementById(`more-option-${id}`);
        const path = (moreDiv ? moreDiv.getAttribute('data-path') : getCurrentPath()) + '/' + id;

        const data = {
            'name': name,
            'path': path.replaceAll('//', '/')
        };

        const response = await postJson('/api/renameFileFolder', data);
        if (response.status === 'ok') {
            closeRenameModal();
            showToast(`Renamed to "${name}" ✏️`);
            broadcastDriveChange('RENAME', { name, path: data.path });
            getCurrentDirectory();
        } else {
            alert('Failed to rename file/folder: ' + (response.status || 'Error'));
        }
    }

    if (renameCreate) {
        renameCreate.addEventListener('click', handleRenameSubmit);
    }

    if (renameInput) {
        renameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleRenameSubmit();
            } else if (e.key === 'Escape') {
                closeRenameModal();
            }
        });
    }
});

// Trash & Restore
async function trashFileFolder() {
    const id = this.getAttribute('id').split('-')[1];
    const moreDiv = document.getElementById(`more-option-${id}`);
    const path = (moreDiv ? moreDiv.getAttribute('data-path') : getCurrentPath()) + '/' + id;
    const data = {
        'path': path.replaceAll('//', '/'),
        'trash': true
    };
    const response = await postJson('/api/trashFileFolder', data);

    if (response.status === 'ok') {
        showToast('Moved item to Trash 🗑️');
        broadcastDriveChange('TRASH', { path: data.path });
        getCurrentDirectory();
    } else {
        alert('Failed to Send File/Folder to Trash');
    }
}

async function restoreFileFolder() {
    const id = this.getAttribute('id').split('-')[1];
    const path = this.getAttribute('data-path') + '/' + id;
    const data = {
        'path': path.replaceAll('//', '/'),
        'trash': false
    };
    const response = await postJson('/api/trashFileFolder', data);

    if (response.status === 'ok') {
        showToast('Restored item from Trash 🔄');
        broadcastDriveChange('RESTORE', { path: data.path });
        getCurrentDirectory();
    } else {
        alert('Failed to Restore File/Folder');
    }
}

async function deleteFileFolder() {
    if (!confirm('Are you sure you want to permanently delete this item? This action cannot be undone.')) {
        return;
    }
    const id = this.getAttribute('id').split('-')[1];
    const path = this.getAttribute('data-path') + '/' + id;
    const data = {
        'path': path.replaceAll('//', '/')
    };
    const response = await postJson('/api/deleteFileFolder', data);

    if (response.status === 'ok') {
        showToast('Item permanently deleted 🗑️');
        broadcastDriveChange('DELETE', { path: data.path });
        getCurrentDirectory();
    } else {
        alert('Failed to Delete File/Folder');
    }
}

async function shareFile() {
    const fileName = (this.parentElement.getAttribute('data-name') || '').toLowerCase();
    const id = this.getAttribute('id').split('-')[1];
    const moreDiv = document.getElementById(`more-option-${id}`);
    const path = ((moreDiv ? moreDiv.getAttribute('data-path') : getCurrentPath()) + '/' + id).replaceAll('//', '/');
    const root_url = getRootUrl();
    const auth = getFolderAuthFromPath();

    let fileUrl = `${root_url}/file?path=${encodeURIComponent(path)}`;
    if (auth) {
        fileUrl += `&auth=${encodeURIComponent(auth)}`;
    }

    let link;
    if (fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi') || fileName.endsWith('.ts') || fileName.endsWith('.ogv')) {
        link = `${root_url}/stream?url=${encodeURIComponent(fileUrl)}`;
    } else {
        link = fileUrl;
    }

    copyTextToClipboard(link);
    showToast('File link copied to clipboard! 📋');
}

async function shareFolder() {
    const id = this.getAttribute('id').split('-')[2];
    const moreDiv = document.getElementById(`more-option-${id}`);
    let path = ((moreDiv ? moreDiv.getAttribute('data-path') : getCurrentPath()) + '/' + id).replaceAll('//', '/');
    const root_url = getRootUrl();

    const auth = await getFolderShareAuth(path);
    if (!auth) return;
    path = path.replace(/^\/+/, '');

    let link = `${root_url}/?path=/share_${path}&auth=${auth}`;
    copyTextToClipboard(link);
    showToast('Folder share link copied to clipboard! 📋');
}