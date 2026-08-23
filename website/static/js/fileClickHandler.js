function openFolder() {
    const folderId = this.getAttribute('data-id');
    const folderItem = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[folderId] : null;
    let targetPath;
    if (folderItem && folderItem.path) {
        targetPath = (folderItem.path + '/' + folderId).replaceAll('//', '/');
    } else {
        targetPath = (getCurrentPath() + '/' + folderId).replaceAll('//', '/');
    }
    if (typeof window.hideSyncActivityView === 'function') {
        window.hideSyncActivityView();
    }
    navigateToPath(targetPath);
}

function openFile() {
    const fileName = (this.getAttribute('data-name') || '').toLowerCase();
    let filePath = (this.getAttribute('data-path') + '/' + this.getAttribute('data-id')).replaceAll('//', '/');
    const isMedia = fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi') || fileName.endsWith('.ts') || fileName.endsWith('.ogv');
    const targetUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath, isMedia) : ('/file?path=' + encodeURIComponent(filePath));
    window.open(targetUrl, '_blank');
}

// File More Button Handler Start
function closeMobileBottomSheet() {
    const bs = document.getElementById('gd-bottom-sheet');
    const backdrop = document.getElementById('bottom-sheet-backdrop');
    if (bs) bs.classList.remove('active');
    if (backdrop) backdrop.classList.remove('active');
}

document.addEventListener('DOMContentLoaded', () => {
    const bsBackdrop = document.getElementById('bottom-sheet-backdrop');
    const bsClose = document.getElementById('bs-close-btn');
    if (bsBackdrop) bsBackdrop.addEventListener('click', closeMobileBottomSheet);
    if (bsClose) bsClose.addEventListener('click', closeMobileBottomSheet);
});

function openMobileBottomSheet(id) {
    const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
    if (!item) return;

    const isFolder = item.type === 'folder';
    const isTrash = getCurrentPath().includes('/trash');
    const bs = document.getElementById('gd-bottom-sheet');
    const backdrop = document.getElementById('bottom-sheet-backdrop');
    const bsIcon = document.getElementById('bs-icon');
    const bsTitle = document.getElementById('bs-title');
    const bsSubtitle = document.getElementById('bs-subtitle');
    const bsActions = document.getElementById('bs-actions');

    if (!bs || !backdrop) return;

    if (bsIcon) bsIcon.innerText = (typeof getBigIconEmoji === 'function') ? getBigIconEmoji(item) : (isFolder ? '📁' : '📄');
    if (bsTitle) bsTitle.innerText = item.name;
    if (bsSubtitle) {
        bsSubtitle.innerText = isFolder 
            ? 'Folder' 
            : `${(typeof convertBytes === 'function') ? convertBytes(item.size) : ''} • ${item.upload_date || ''}`;
    }

    let actionsHtml = '';
    if (!isTrash) {
        if (!isFolder) {
            actionsHtml += `
                <div class="gd-bs-item" id="bs-act-preview">
                    <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>
                    <span>Preview file</span>
                </div>
                <div class="gd-bs-item" id="bs-act-download">
                    <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z"/></svg>
                    <span>Download</span>
                </div>
            `;
        } else {
            actionsHtml += `
                <div class="gd-bs-item" id="bs-act-open">
                    <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"/></svg>
                    <span>Open folder</span>
                </div>
                <div class="gd-bs-item" id="bs-act-download">
                    <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z"/></svg>
                    <span>Download as ZIP</span>
                </div>
            `;
        }

        actionsHtml += `
            <div class="gd-bs-item" id="bs-act-share">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg>
                <span>Share link</span>
            </div>
            <div class="gd-bs-item" id="bs-act-rename">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
                <span>Rename</span>
            </div>
            <div class="gd-bs-item" id="bs-act-move">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"/></svg>
                <span>Move to...</span>
            </div>
            <div class="gd-bs-item" id="bs-act-copy">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
                <span>Make a copy</span>
            </div>
            <div class="gd-bs-item" id="bs-act-details">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M11 7h2v2h-2zm0 4h2v6h-2zm1-9C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"/></svg>
                <span>Details & info</span>
            </div>
            <div class="gd-bs-item gd-bs-danger" id="bs-act-trash">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                <span>Move to trash</span>
            </div>
        `;
    } else {
        actionsHtml += `
            <div class="gd-bs-item" id="bs-act-restore">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42C8.27 19.99 10.51 21 13 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>
                <span>Restore</span>
            </div>
            <div class="gd-bs-item gd-bs-danger" id="bs-act-delete">
                <svg viewBox="0 0 24 24" class="gd-bs-svg"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                <span>Delete permanently</span>
            </div>
        `;
    }

    bsActions.innerHTML = actionsHtml;

    // Attach Handlers
    const actPreview = document.getElementById('bs-act-preview');
    if (actPreview) {
        actPreview.onclick = () => {
            closeMobileBottomSheet();
            if (typeof openFilePreview === 'function') openFilePreview.call({ getAttribute: () => id });
        };
    }

    const actOpen = document.getElementById('bs-act-open');
    if (actOpen) {
        actOpen.onclick = () => {
            closeMobileBottomSheet();
            navigateToPath((item.path + '/' + item.id).replaceAll('//', '/'));
        };
    }

    const actDownload = document.getElementById('bs-act-download');
    if (actDownload) {
        actDownload.onclick = () => {
            closeMobileBottomSheet();
            const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
            if (isFolder) {
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
        };
    }

    const actShare = document.getElementById('bs-act-share');
    if (actShare) {
        actShare.onclick = () => {
            closeMobileBottomSheet();
            if (isFolder) {
                shareFolder.call({ getAttribute: () => `folder-share-${id}` });
            } else {
                const moreDiv = document.getElementById(`more-option-${id}`);
                shareFile.call({ getAttribute: () => `share-${id}`, parentElement: moreDiv || document.body });
            }
        };
    }

    const actRename = document.getElementById('bs-act-rename');
    if (actRename) {
        actRename.onclick = () => {
            closeMobileBottomSheet();
            const renameBtn = document.getElementById(`rename-${id}`);
            if (renameBtn) renameFileFolder.call(renameBtn);
        };
    }

    const actMove = document.getElementById('bs-act-move');
    if (actMove) {
        actMove.onclick = () => {
            closeMobileBottomSheet();
            openMoveModal(id);
        };
    }

    const actCopy = document.getElementById('bs-act-copy');
    if (actCopy) {
        actCopy.onclick = async () => {
            closeMobileBottomSheet();
            const srcFullPath = (item.path + '/' + item.id).replaceAll('//', '/');
            showToast('Creating copy... ⏳');
            await copyFileFolder(srcFullPath);
        };
    }

    const actDetails = document.getElementById('bs-act-details');
    if (actDetails) {
        actDetails.onclick = () => {
            closeMobileBottomSheet();
            if (typeof selectItem === 'function') selectItem(id);
            const inspector = document.getElementById('gd-inspector');
            if (inspector) {
                inspector.classList.add('mobile-open');
                inspector.classList.remove('hidden');
            }
        };
    }

    const actTrash = document.getElementById('bs-act-trash');
    if (actTrash) {
        actTrash.onclick = () => {
            closeMobileBottomSheet();
            const trashBtn = document.getElementById(`trash-${id}`);
            if (trashBtn) trashFileFolder.call(trashBtn);
        };
    }

    const actRestore = document.getElementById('bs-act-restore');
    if (actRestore) {
        actRestore.onclick = () => {
            closeMobileBottomSheet();
            const restoreBtn = document.getElementById(`restore-${id}`);
            if (restoreBtn) restoreFileFolder.call(restoreBtn);
        };
    }

    const actDelete = document.getElementById('bs-act-delete');
    if (actDelete) {
        actDelete.onclick = () => {
            closeMobileBottomSheet();
            const delBtn = document.getElementById(`delete-${id}`);
            if (delBtn) deleteFileFolder.call(delBtn);
        };
    }

    backdrop.classList.add('active');
    bs.classList.add('active');
}

function closeAllMoreMenus() {
    document.querySelectorAll('.more-options').forEach(el => {
        el.style.opacity = '0';
        el.style.pointerEvents = 'none';
        el.style.zIndex = '-1';
    });
}

function openMoreButton(div) {
    const id = div.getAttribute('data-id');
    if (window.innerWidth <= 768) {
        openMobileBottomSheet(id);
        return;
    }

    closeAllMoreMenus();

    const moreDiv = document.getElementById(`more-option-${id}`);
    if (!moreDiv) return;

    const rect = div.getBoundingClientRect();
    const menuWidth = 200;
    const x = Math.max(10, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 12));
    let y = rect.bottom + 4;
    if (y + 240 > window.innerHeight) {
        y = Math.max(10, rect.top - 240);
    }

    moreDiv.style.position = 'fixed';
    moreDiv.style.left = `${x}px`;
    moreDiv.style.top = `${y}px`;
    moreDiv.style.zIndex = '1000';
    moreDiv.style.opacity = '1';
    moreDiv.style.pointerEvents = 'auto';

    const isTrash = getCurrentPath().includes('/trash');

    const focusInput = moreDiv.querySelector('.more-options-focus');
    if (focusInput) {
        focusInput.focus();
        focusInput.addEventListener('blur', () => {
            setTimeout(() => closeMoreMenu(moreDiv), 180);
        }, { once: true });
    }

    const onDocClick = (e) => {
        if (!moreDiv.contains(e.target) && !div.contains(e.target)) {
            closeMoreMenu(moreDiv);
            document.removeEventListener('click', onDocClick);
        }
    };
    setTimeout(() => {
        document.addEventListener('click', onDocClick);
    }, 10);

    if (!isTrash) {
        const dlZipOpt = moreDiv.querySelector(`#download-zip-opt-${id}`);
        if (dlZipOpt) {
            dlZipOpt.onclick = (e) => {
                e.stopPropagation();
                closeMoreMenu(moreDiv);
                const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
                if (!item) return;
                const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
                showToast(`Preparing ZIP for folder "${item.name}"... 📦`);
                window.location.href = `${getRootUrl()}/downloadZip?path=${encodeURIComponent(filePath)}`;
            };
        }

        const dlOpt = moreDiv.querySelector(`#download-opt-${id}`);
        if (dlOpt) {
            dlOpt.onclick = (e) => {
                e.stopPropagation();
                closeMoreMenu(moreDiv);
                const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
                if (!item) return;
                const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
                const directUrl = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath) : `${getRootUrl()}/file?path=${encodeURIComponent(filePath)}`;
                const a = document.createElement('a');
                a.href = directUrl;
                a.download = item.name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                showToast(`Downloading "${item.name}"... ⬇️`);
            };
        }

        const previewOpt = moreDiv.querySelector(`#preview-opt-${id}`);
        if (previewOpt) {
            previewOpt.onclick = (e) => {
                e.stopPropagation();
                closeMoreMenu(moreDiv);
                if (typeof openFilePreview === 'function') {
                    openFilePreview.call({ getAttribute: () => id });
                }
            };
        }

        const detailsOpt = moreDiv.querySelector(`#details-opt-${id}`);
        if (detailsOpt) {
            detailsOpt.onclick = (e) => {
                e.stopPropagation();
                closeMoreMenu(moreDiv);
                selectItem(id);
                const inspector = document.getElementById('gd-inspector');
                if (inspector) inspector.classList.remove('hidden');
            };
        }

        const renameBtn = moreDiv.querySelector(`#rename-${id}`);
        if (renameBtn) renameBtn.onclick = (e) => { e.stopPropagation(); closeMoreMenu(moreDiv); renameFileFolder.call(renameBtn); };

        const moveBtn = moreDiv.querySelector(`#move-${id}`);
        if (moveBtn) moveBtn.onclick = (e) => { e.stopPropagation(); closeMoreMenu(moreDiv); openMoveModal(id); };

        const copyBtn = moreDiv.querySelector(`#copy-${id}`);
        if (copyBtn) {
            copyBtn.onclick = async (e) => {
                e.stopPropagation();
                closeMoreMenu(moreDiv);
                const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
                if (!item) return;
                const srcFullPath = (item.path + '/' + item.id).replaceAll('//', '/');
                showToast('Creating copy... ⏳');
                await copyFileFolder(srcFullPath);
            };
        }

        const trashBtn = moreDiv.querySelector(`#trash-${id}`);
        if (trashBtn) trashBtn.onclick = (e) => { e.stopPropagation(); closeMoreMenu(moreDiv); trashFileFolder.call(trashBtn); };

        const shareBtn = moreDiv.querySelector(`#share-${id}`);
        if (shareBtn) shareBtn.onclick = (e) => { e.stopPropagation(); shareFile.call(shareBtn); };

        const folderShareBtn = moreDiv.querySelector(`#folder-share-${id}`);
        if (folderShareBtn) folderShareBtn.onclick = (e) => { e.stopPropagation(); shareFolder.call(folderShareBtn); };

        const tagsOpt = moreDiv.querySelector(`#tags-opt-${id}`);
        if (tagsOpt) {
            tagsOpt.onclick = (e) => {
                e.stopPropagation();
                closeMoreMenu(moreDiv);
                if (typeof openManageTagsModal === 'function') openManageTagsModal(id);
            };
        }
    } else {
        const restoreBtn = moreDiv.querySelector(`#restore-${id}`);
        if (restoreBtn) restoreBtn.onclick = (e) => { e.stopPropagation(); closeMoreMenu(moreDiv); restoreFileFolder.call(restoreBtn); };

        const delBtn = moreDiv.querySelector(`#delete-${id}`);
        if (delBtn) delBtn.onclick = (e) => { e.stopPropagation(); closeMoreMenu(moreDiv); deleteFileFolder.call(delBtn); };
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
    const filePath = ((moreDiv ? moreDiv.getAttribute('data-path') : getCurrentPath()) + '/' + id).replaceAll('//', '/');
    let auth = getFolderAuthFromPath();

    if (!auth) {
        const parentFolderPath = (moreDiv ? moreDiv.getAttribute('data-path') : getCurrentPath()) || '/';
        auth = await getFolderShareAuth(parentFolderPath);
    }

    const isMedia = fileName.endsWith('.mp4') || fileName.endsWith('.mkv') || fileName.endsWith('.webm') || fileName.endsWith('.mov') || fileName.endsWith('.avi') || fileName.endsWith('.ts') || fileName.endsWith('.ogv');
    let link = (typeof buildFileUrl === 'function') ? buildFileUrl(filePath, isMedia) : `${getRootUrl()}/file?path=${encodeURIComponent(filePath)}`;
    if (auth && !link.includes('auth=')) {
        link += (link.includes('?') ? '&' : '?') + `auth=${encodeURIComponent(auth)}`;
    }

    copyTextToClipboard(link);
    showToast('File share link copied to clipboard! 📋');
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

// =========================================================
// Move Item Modal Controller
// =========================================================
let CURRENT_MOVE_ITEM = null;
let CURRENT_MOVE_TARGET_PATH = '/';

async function openMoveModal(id) {
    const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
    if (!item) return;

    CURRENT_MOVE_ITEM = item;
    CURRENT_MOVE_TARGET_PATH = '/';

    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('move-item-modal');
    const targetNameEl = document.getElementById('move-target-item-name');

    if (targetNameEl) targetNameEl.innerText = `"${item.name}"`;

    if (bgBlur) {
        bgBlur.style.zIndex = '100';
        bgBlur.style.opacity = '1';
    }
    if (modal) {
        modal.style.zIndex = '101';
        modal.style.opacity = '1';
    }

    await renderMovePickerDirectory('/');
}

function closeMoveModal() {
    const bgBlur = document.getElementById('bg-blur');
    const modal = document.getElementById('move-item-modal');
    if (bgBlur) bgBlur.style.opacity = '0';
    if (modal) modal.style.opacity = '0';
    CURRENT_MOVE_ITEM = null;
}

async function renderMovePickerDirectory(path) {
    CURRENT_MOVE_TARGET_PATH = path || '/';
    const listEl = document.getElementById('move-folder-picker-list');
    const crumbsEl = document.getElementById('move-modal-crumbs');
    if (!listEl) return;

    listEl.innerHTML = '<div class="gd-move-empty-state">Loading folders...</div>';

    // Render breadcrumbs
    if (crumbsEl) {
        const clean = path.replace(/^\/+/, '');
        const parts = clean ? clean.split('/') : [];
        let crumbHtml = `<span class="gd-move-crumb ${path === '/' ? 'active' : ''}" onclick="renderMovePickerDirectory('/')">🏠 My Drive</span>`;
        let acc = '';
        for (let i = 0; i < parts.length; i++) {
            acc += '/' + parts[i];
            const isLast = i === parts.length - 1;
            const folderPart = parts[i];
            crumbHtml += `<span class="gd-move-crumb-sep">›</span><span class="gd-move-crumb ${isLast ? 'active' : ''}" onclick="renderMovePickerDirectory('${acc}')">${folderPart}</span>`;
        }
        crumbsEl.innerHTML = crumbHtml;
    }

    try {
        const json = await postJson('/api/getDirectory', { path: path });
        if (json && json.status === 'ok' && json.data) {
            const contents = json.data.contents || {};
            const folders = Object.entries(contents).filter(([k, v]) => v.type === 'folder');

            // Exclude the item itself if we are moving a folder
            const validFolders = folders.filter(([k, f]) => {
                if (CURRENT_MOVE_ITEM && CURRENT_MOVE_ITEM.type === 'folder' && f.id === CURRENT_MOVE_ITEM.id) {
                    return false;
                }
                return true;
            });

            if (validFolders.length === 0) {
                listEl.innerHTML = '<div class="gd-move-empty-state">No subfolders here.<br>Click "Move Here" to place item in this folder.</div>';
                return;
            }

            let itemsHtml = '';
            validFolders.forEach(([k, folder]) => {
                const folderFullPath = (folder.path + '/' + folder.id).replaceAll('//', '/');
                itemsHtml += `
                    <div class="gd-move-folder-item" data-path="${folderFullPath}" data-id="${folder.id}">
                        <div class="gd-move-folder-left">
                            <span class="gd-move-folder-icon">📁</span>
                            <span class="gd-move-folder-name" title="${escapeHtml(folder.name)}">${escapeHtml(folder.name)}</span>
                        </div>
                        <button class="gd-move-folder-enter-btn" onclick="event.stopPropagation(); renderMovePickerDirectory('${folderFullPath}')">Open ›</button>
                    </div>
                `;
            });
            listEl.innerHTML = itemsHtml;

            // Click row to select folder
            listEl.querySelectorAll('.gd-move-folder-item').forEach(el => {
                el.onclick = function() {
                    listEl.querySelectorAll('.gd-move-folder-item').forEach(i => i.classList.remove('selected'));
                    this.classList.add('selected');
                    CURRENT_MOVE_TARGET_PATH = this.getAttribute('data-path');
                };
                el.ondblclick = function() {
                    const p = this.getAttribute('data-path');
                    renderMovePickerDirectory(p);
                };
            });
        } else {
            listEl.innerHTML = '<div class="gd-move-empty-state">Unable to load folder contents</div>';
        }
    } catch (err) {
        listEl.innerHTML = '<div class="gd-move-empty-state">Error loading folders</div>';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const moveCancel = document.getElementById('move-modal-cancel');
    const moveConfirm = document.getElementById('move-modal-confirm');

    if (moveCancel) moveCancel.onclick = closeMoveModal;

    if (moveConfirm) {
        moveConfirm.onclick = async () => {
            if (!CURRENT_MOVE_ITEM) return;
            const srcFullPath = (CURRENT_MOVE_ITEM.path + '/' + CURRENT_MOVE_ITEM.id).replaceAll('//', '/');
            const destPath = CURRENT_MOVE_TARGET_PATH || '/';

            if (srcFullPath === destPath) {
                showToast('⚠️ Item is already in this folder');
                return;
            }

            closeMoveModal();
            await moveFileFolder(srcFullPath, destPath);
        };
    }
});