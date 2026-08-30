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
    if (typeof window.hideSharedLinksView === 'function') {
        window.hideSharedLinksView();
    }
    navigateToPath(targetPath);
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
        if (isFolder) {
            const fSize = (typeof convertBytes === 'function' && typeof item.size === 'number' && item.size > 0) ? convertBytes(item.size) : 'Folder';
            const fCount = item.file_count ? ` • ${item.file_count} item${item.file_count === 1 ? '' : 's'}` : '';
            bsSubtitle.innerText = `${fSize}${fCount}`;
        } else {
            bsSubtitle.innerText = `${(typeof convertBytes === 'function') ? convertBytes(item.size) : ''} • ${item.upload_date || ''}`;
        }
    }

    let actionsHtml = '';
    if (!isTrash) {
        if (!isFolder) {
            const isZip = item.name && item.name.toLowerCase().endsWith('.zip');
            const archiveBsHtml = isZip ? `
                <div class="gd-bs-item" id="bs-act-archive">
                    <svg viewBox="0 0 24 24" class="gd-bs-svg" style="fill:none;stroke:currentColor;stroke-width:2;"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
                    <span>Browse Archive</span>
                </div>
            ` : '';
            actionsHtml += `
                ${archiveBsHtml}
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
    const actArchive = document.getElementById('bs-act-archive');
    if (actArchive) {
        actArchive.onclick = () => {
            closeMobileBottomSheet();
            const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
            if (window.ARCHIVE_MANAGER && typeof window.ARCHIVE_MANAGER.open === 'function') {
                window.ARCHIVE_MANAGER.open(filePath, item.name);
            }
        };
    }

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

function bindMoreMenuActions(moreDiv, id) {
    if (!moreDiv || moreDiv.getAttribute('data-actions-bound') === 'true') return;
    moreDiv.setAttribute('data-actions-bound', 'true');

    const isTrash = (typeof getCurrentPath === 'function' ? getCurrentPath() : '').includes('/trash');

    if (!isTrash) {
        const archiveOpt = moreDiv.querySelector(`#archive-opt-${id}`);
        if (archiveOpt) {
            archiveOpt.onclick = (e) => {
                e.stopPropagation();
                closeMoreMenu(moreDiv);
                const item = (typeof DIRECTORY_ITEMS !== 'undefined') ? DIRECTORY_ITEMS[id] : null;
                if (!item) return;
                const filePath = (item.path + '/' + item.id).replaceAll('//', '/');
                if (window.ARCHIVE_MANAGER && typeof window.ARCHIVE_MANAGER.open === 'function') {
                    window.ARCHIVE_MANAGER.open(filePath, item.name);
                }
            };
        }

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
                if (typeof selectItem === 'function') selectItem(id);
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
        if (shareBtn) shareBtn.onclick = (e) => { e.stopPropagation(); closeMoreMenu(moreDiv); shareFile.call(shareBtn); };

        const folderShareBtn = moreDiv.querySelector(`#folder-share-${id}`);
        if (folderShareBtn) folderShareBtn.onclick = (e) => { e.stopPropagation(); closeMoreMenu(moreDiv); shareFolder.call(folderShareBtn); };

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

function positionMoreMenu(moreDiv, targetX, targetY, isFromButton = false, buttonRect = null) {
    if (!moreDiv) return;

    const vpW = window.innerWidth || document.documentElement.clientWidth || 1024;
    const vpH = window.innerHeight || document.documentElement.clientHeight || 768;
    const pad = 10;
    const maxMenuH = Math.max(160, vpH - (pad * 2));

    // Render off-screen initially to measure exact real height
    moreDiv.style.position = 'fixed';
    moreDiv.style.zIndex = '999999';
    moreDiv.style.visibility = 'hidden';
    moreDiv.style.display = 'block';
    moreDiv.style.opacity = '0';
    moreDiv.style.pointerEvents = 'auto';
    moreDiv.style.left = '0px';
    moreDiv.style.top = '0px';
    moreDiv.style.maxHeight = `${maxMenuH}px`;
    moreDiv.style.overflowY = 'auto';
    moreDiv.style.overflowX = 'hidden';
    moreDiv.style.boxSizing = 'border-box';

    const rect = moreDiv.getBoundingClientRect();
    const menuWidth = rect.width > 0 ? rect.width : (moreDiv.offsetWidth || 215);
    const menuHeight = rect.height > 0 ? rect.height : (moreDiv.offsetHeight || 420);

    let x, y;

    if (isFromButton && buttonRect) {
        x = buttonRect.right - menuWidth;
        if (x < pad) x = buttonRect.left;
        x = Math.max(pad, Math.min(x, vpW - menuWidth - pad));

        const spaceBelow = vpH - buttonRect.bottom - pad;
        const spaceAbove = buttonRect.top - pad;

        if (spaceBelow >= menuHeight) {
            y = buttonRect.bottom + 4;
        } else if (spaceAbove >= menuHeight) {
            y = buttonRect.top - menuHeight - 4;
        } else if (spaceBelow >= spaceAbove) {
            y = buttonRect.bottom + 4;
        } else {
            y = buttonRect.top - menuHeight - 4;
        }
    } else {
        x = targetX;
        if (x + menuWidth > vpW - pad) {
            x = targetX - menuWidth;
        }
        x = Math.max(pad, Math.min(x, vpW - menuWidth - pad));

        const spaceBelow = vpH - targetY - pad;
        const spaceAbove = targetY - pad;

        if (spaceBelow >= menuHeight) {
            // Enough room below cursor
            y = targetY;
        } else if (spaceAbove >= menuHeight) {
            // Enough room above cursor -> flip upward
            y = targetY - menuHeight;
        } else if (spaceAbove > spaceBelow) {
            // More room above than below
            y = targetY - menuHeight;
        } else {
            // More room below
            y = targetY;
        }
    }

    // Final safety boundary clamp: ensure the menu is ALWAYS 100% within the viewport
    if (y + menuHeight > vpH - pad) {
        y = vpH - menuHeight - pad;
    }
    if (y < pad) {
        y = pad;
    }

    moreDiv.style.left = `${Math.round(x)}px`;
    moreDiv.style.top = `${Math.round(y)}px`;
    moreDiv.style.visibility = 'visible';
    moreDiv.style.opacity = '1';

    const focusInput = moreDiv.querySelector('.more-options-focus');
    if (focusInput) {
        focusInput.focus();
        focusInput.addEventListener('blur', () => {
            setTimeout(() => closeMoreMenu(moreDiv), 180);
        }, { once: true });
    }

    const onDocClick = (e) => {
        if (!moreDiv.contains(e.target) && (!buttonRect || !e.target.closest('.more-btn'))) {
            closeMoreMenu(moreDiv);
            document.removeEventListener('click', onDocClick);
        }
    };
    setTimeout(() => {
        document.addEventListener('click', onDocClick);
    }, 10);
}

function openMoreButton(div) {
    const id = div.getAttribute('data-id');
    if (window.innerWidth <= 768) {
        if (typeof openMobileBottomSheet === 'function') openMobileBottomSheet(id);
        return;
    }

    closeAllMoreMenus();

    const moreDiv = document.getElementById(`more-option-${id}`);
    if (!moreDiv) return;

    bindMoreMenuActions(moreDiv, id);
    const rect = div.getBoundingClientRect();
    positionMoreMenu(moreDiv, 0, 0, true, rect);
}

function closeMoreMenu(moreDiv) {
    if (!moreDiv) return;
    moreDiv.style.opacity = '0';
    setTimeout(() => {
        moreDiv.style.zIndex = '-1';
        moreDiv.style.pointerEvents = 'none';
    }, 150);
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

    // Global Escape fallback: close the rename modal even when the input
    // itself is not focused (previously it could stay stuck open).
    // Uses the inline opacity flag (not computed style) because the CSS
    // transition makes computed opacity unreliable during animation.
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && renameModal && renameModal.style.opacity === '1') {
            e.stopPropagation();
            closeRenameModal();
        }
    });
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

function resolveShareItem(el, defaultType) {
    const rawId = el ? (el.getAttribute('data-id') || el.getAttribute('id') || '') : '';
    let id = rawId;
    if (id.startsWith('folder-share-')) id = id.substring('folder-share-'.length);
    else if (id.startsWith('share-')) id = id.substring('share-'.length);
    
    const item = (typeof DIRECTORY_ITEMS !== 'undefined' && DIRECTORY_ITEMS[id]) ||
                 (typeof CURRENT_BOTTOM_SHEET_ITEM !== 'undefined' && CURRENT_BOTTOM_SHEET_ITEM && CURRENT_BOTTOM_SHEET_ITEM.id === id ? CURRENT_BOTTOM_SHEET_ITEM : null) ||
                 (typeof SELECTED_ITEM_ID !== 'undefined' && typeof DIRECTORY_ITEMS !== 'undefined' && DIRECTORY_ITEMS[SELECTED_ITEM_ID] ? DIRECTORY_ITEMS[SELECTED_ITEM_ID] : null);

    const moreDiv = document.getElementById(`more-option-${id}`);
    const parentPath = (item && item.path) || (moreDiv ? moreDiv.getAttribute('data-path') : (typeof getCurrentPath === 'function' ? getCurrentPath() : '/')) || '/';
    const name = (item && item.name) || (moreDiv ? moreDiv.getAttribute('data-name') : '') || (el && el.parentElement && el.parentElement.getAttribute ? el.parentElement.getAttribute('data-name') : '') || '';
    const itemType = (item && item.type) || defaultType || 'file';

    return { id, item, parentPath, name, type: itemType };
}

async function shareFile() {
    const info = resolveShareItem(this, 'file');
    openShareModal(info);
}

async function shareFolder() {
    const info = resolveShareItem(this, 'folder');
    openShareModal(info);
}

// =========================================================
// Secure Share Modal Controller
// =========================================================
let SHARE_STATE = { token: null, target: null, name: '' };

function openShareModal(opts) {
    const modal = document.getElementById('share-modal');
    const bgBlur = document.getElementById('bg-blur');
    if (!modal) return;

    const targetPath = `${(opts.parentPath || '/')}/${opts.id}`.replaceAll('//', '/');
    const displayName = opts.name || targetPath.split('/').filter(Boolean).pop() || 'Item';
    SHARE_STATE = { token: null, target: targetPath, name: displayName };

    const nameEl = document.getElementById('share-item-name');
    if (nameEl) nameEl.textContent = displayName;

    // Reset to creation view
    const optsDiv = document.getElementById('share-options');
    const resDiv = document.getElementById('share-result');
    const createBtn = document.getElementById('share-create-btn');
    const pwdInput = document.getElementById('share-password');
    const expirySelect = document.getElementById('share-expiry');
    const allowDl = document.getElementById('share-allow-download');
    const allowPv = document.getElementById('share-allow-preview');
    const errEl = document.getElementById('share-error');

    if (optsDiv) optsDiv.style.display = '';
    if (resDiv) resDiv.style.display = 'none';
    if (createBtn) {
        createBtn.style.display = '';
        createBtn.disabled = false;
        createBtn.textContent = 'Create Link';
    }
    if (pwdInput) pwdInput.value = '';
    if (expirySelect) expirySelect.value = '168';
    if (allowDl) allowDl.checked = true;
    if (allowPv) allowPv.checked = true;
    if (errEl) errEl.textContent = '';

    if (bgBlur) {
        bgBlur.style.zIndex = '100';
        bgBlur.style.opacity = '1';
    }
    modal.style.zIndex = '101';
    modal.style.opacity = '1';
}

function closeShareModal() {
    const modal = document.getElementById('share-modal');
    const bgBlur = document.getElementById('bg-blur');
    if (modal) {
        modal.style.opacity = '0';
        setTimeout(() => { modal.style.zIndex = '-1'; }, 200);
    }
    if (bgBlur) {
        bgBlur.style.opacity = '0';
        setTimeout(() => { bgBlur.style.zIndex = '-1'; }, 200);
    }
}

async function createShareLink() {
    const errEl = document.getElementById('share-error');
    const createBtn = document.getElementById('share-create-btn');
    if (errEl) errEl.textContent = '';

    const pwd = (document.getElementById('share-password')?.value || '').trim();
    if (pwd && pwd.length < 6) {
        if (errEl) errEl.textContent = 'Password must be at least 6 characters.';
        return;
    }
    const hoursVal = document.getElementById('share-expiry')?.value;
    const body = {
        target: SHARE_STATE.target,
        expires_in_hours: (hoursVal === '' || hoursVal === undefined) ? null : Number(hoursVal),
        password: pwd,
        allow_download: !!document.getElementById('share-allow-download')?.checked,
        allow_preview: !!document.getElementById('share-allow-preview')?.checked,
    };

    if (createBtn) {
        createBtn.disabled = true;
        createBtn.textContent = 'Creating...';
    }

    const json = await postJson('/api/share/create', body);

    if (createBtn) {
        createBtn.disabled = false;
        createBtn.textContent = 'Create Link';
    }

    if (json.status !== 'ok' || !json.share) {
        if (errEl) {
            errEl.textContent = json.error === 'invalid_password' ? 'Password must be 6-128 characters.'
                : json.error === 'invalid_expiry' ? 'Invalid expiry selected.'
                : 'Could not create the share link. Please verify the item exists.';
        }
        return;
    }

    SHARE_STATE.token = json.share.token;
    const linkInput = document.getElementById('share-link-input');
    if (linkInput) linkInput.value = json.url;

    const metaLine = document.getElementById('share-meta-line');
    if (metaLine) metaLine.textContent = describeShare(json.share);

    document.getElementById('share-options').style.display = 'none';
    if (createBtn) createBtn.style.display = 'none';
    document.getElementById('share-result').style.display = '';
    showToast('Secure link created 🔗');
}

function describeShare(s) {
    const bits = [];
    bits.push(s.type === 'folder' ? 'Folder link' : 'File link');
    if (s.has_password) bits.push('password protected');
    if (s.expires_at) bits.push(`expires ${new Date(s.expires_at * 1000).toLocaleString()}`);
    else bits.push('never expires');
    if (!s.allow_download) bits.push('preview only');
    else if (!s.allow_preview) bits.push('download only');
    return bits.join(' • ');
}

async function copyShareLink() {
    const input = document.getElementById('share-link-input');
    if (!input || !input.value) return;
    const success = await copyTextToClipboard(input.value);
    const copyBtn = document.getElementById('share-copy-btn');
    if (copyBtn) {
        const oldText = copyBtn.textContent;
        copyBtn.textContent = 'Copied! ✓';
        setTimeout(() => { copyBtn.textContent = oldText; }, 2000);
    }
    showToast(success ? 'Link copied to clipboard! 📋' : 'Link selected — press Ctrl+C to copy');
}

async function regenerateShareLink() {
    if (!SHARE_STATE.token) return;
    const regenBtn = document.getElementById('share-regenerate-btn');
    if (regenBtn) regenBtn.disabled = true;
    const json = await postJson('/api/share/regenerate', { token: SHARE_STATE.token });
    if (regenBtn) regenBtn.disabled = false;

    if (json.status !== 'ok' || !json.share) {
        showToast('Could not regenerate link', true);
        return;
    }
    SHARE_STATE.token = json.share.token;
    const linkInput = document.getElementById('share-link-input');
    if (linkInput) linkInput.value = json.url;
    const metaLine = document.getElementById('share-meta-line');
    if (metaLine) metaLine.textContent = describeShare(json.share);
    showToast('New link generated — previous link is now dead 🔄');
}

async function revokeShareLink() {
    if (!SHARE_STATE.token) return;
    const revokeBtn = document.getElementById('share-revoke-btn');
    if (revokeBtn) revokeBtn.disabled = true;
    const json = await postJson('/api/share/revoke', { token: SHARE_STATE.token });
    if (revokeBtn) revokeBtn.disabled = false;

    if (json.status !== 'ok') {
        showToast('Could not revoke link', true);
        return;
    }
    showToast('Link revoked — access removed 🚫');
    closeShareModal();
}

(function wireShareModal() {
    const on = (id, fn) => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('click', fn);
    };
    on('share-create-btn', createShareLink);
    on('share-copy-btn', copyShareLink);
    on('share-regenerate-btn', regenerateShareLink);
    on('share-revoke-btn', revokeShareLink);
    on('share-done-btn', closeShareModal);

    const pwdInput = document.getElementById('share-password');
    if (pwdInput) {
        pwdInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                createShareLink();
            }
        });
    }
})();

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

    // Global Escape fallback: close the move modal even when no control inside
    // it is focused (matches rename-modal behaviour).
    document.addEventListener('keydown', (e) => {
        const modal = document.getElementById('move-item-modal');
        if (e.key === 'Escape' && modal && modal.style.opacity === '1') {
            e.stopPropagation();
            closeMoveModal();
        }
    });

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