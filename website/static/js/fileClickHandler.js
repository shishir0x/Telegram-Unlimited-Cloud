function openFolder() {
    let path = (getCurrentPath() + '/' + this.getAttribute('data-id') + '/').replaceAll('//', '/');

    const auth = getFolderAuthFromPath();
    if (auth) {
        path = path + '&auth=' + auth;
    }
    window.location.href = `/?path=${path}`;
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
    const bgBlur = document.getElementById('bg-blur');
    const renameModal = document.getElementById('rename-file-folder');

    if (renameCancel) {
        renameCancel.addEventListener('click', () => {
            document.getElementById('rename-name').value = '';
            bgBlur.style.opacity = '0';
            renameModal.style.opacity = '0';
            setTimeout(() => {
                bgBlur.style.zIndex = '-1';
                renameModal.style.zIndex = '-1';
            }, 200);
        });
    }

    if (renameCreate) {
        renameCreate.addEventListener('click', async () => {
            const name = document.getElementById('rename-name').value;
            if (name === '') {
                alert('Name cannot be empty');
                return;
            }

            const id = renameModal.getAttribute('data-id');
            const path = document.getElementById(`more-option-${id}`).getAttribute('data-path') + '/' + id;

            const data = {
                'name': name,
                'path': path
            };

            const response = await postJson('/api/renameFileFolder', data);
            if (response.status === 'ok') {
                window.location.reload();
            } else {
                alert('Failed to rename file/folder: ' + (response.status || 'Error'));
                window.location.reload();
            }
        });
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
        window.location.reload();
    } else {
        alert('Failed to Delete File/Folder');
        window.location.reload();
    }
}

async function shareFile() {
    const fileName = (this.parentElement.getAttribute('data-name') || '').toLowerCase();
    const id = this.getAttribute('id').split('-')[1];
    const path = document.getElementById(`more-option-${id}`).getAttribute('data-path') + '/' + id;
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
    alert('Link copied to clipboard! 📋');
}

async function shareFolder() {
    const id = this.getAttribute('id').split('-')[2];
    let path = document.getElementById(`more-option-${id}`).getAttribute('data-path') + '/' + id;
    const root_url = getRootUrl();

    const auth = await getFolderShareAuth(path);
    path = path.slice(1);

    let link = `${root_url}/?path=/share_${path}&auth=${auth}`;
    copyTextToClipboard(link);
    alert('Folder share link copied to clipboard! 📋');
}