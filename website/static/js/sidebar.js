// Google Drive Sidebar & New Button Actions
document.addEventListener('DOMContentLoaded', () => {
    const newBtn = document.getElementById('new-button');
    const newUploadDropdown = document.getElementById('new-upload');
    const newUploadFocus = document.getElementById('new-upload-focus');
    const bgBlur = document.getElementById('bg-blur');

    const currentPath = getCurrentPath();
    const isTrash = currentPath.startsWith('/trash');
    const isSearch = currentPath.startsWith('/search');
    const isShare = currentPath.startsWith('/share');

    // Toggle Nav Menu selection via SPA routing
    const navMyDrive = document.getElementById('nav-my-drive');
    const navRecent = document.getElementById('nav-recent');
    const navTrash = document.getElementById('nav-trash');

    updateSidebarNavSelection(currentPath);

    const logoLink = document.getElementById('gd-logo-link');
    if (logoLink) {
        logoLink.addEventListener('click', (e) => {
            e.preventDefault();
            navigateToPath('/');
        });
    }

    if (navMyDrive) {
        navMyDrive.addEventListener('click', (e) => {
            e.preventDefault();
            navigateToPath('/');
        });
    }

    if (navRecent) {
        navRecent.addEventListener('click', (e) => {
            e.preventDefault();
            navigateToPath('/recent');
        });
    }

    if (navTrash) {
        navTrash.addEventListener('click', (e) => {
            e.preventDefault();
            navigateToPath('/trash');
        });
    }

    // Toggle "+ New" Dropdown
    if (newBtn && newUploadDropdown) {
        newBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            newUploadDropdown.classList.toggle('active');
            if (newUploadDropdown.classList.contains('active')) {
                if (newUploadFocus) newUploadFocus.focus();
            }
        });

        document.addEventListener('click', (e) => {
            if (!newUploadDropdown.contains(e.target) && e.target !== newBtn) {
                newUploadDropdown.classList.remove('active');
            }
        });
    }

    // File Upload Button
    const fileUploadBtn = document.getElementById('file-upload-btn');
    const fileInput = document.getElementById('fileInput');
    if (fileUploadBtn && fileInput) {
        fileUploadBtn.addEventListener('click', () => {
            newUploadDropdown.classList.remove('active');
            fileInput.click();
        });
    }

    // New Folder Button & Modal
    const newFolderBtn = document.getElementById('new-folder-btn');
    const createFolderModal = document.getElementById('create-new-folder');
    const newFolderNameInput = document.getElementById('new-folder-name');
    const newFolderCancel = document.getElementById('new-folder-cancel');
    const newFolderCreate = document.getElementById('new-folder-create');

    function closeNewFolderModal() {
        if (createFolderModal) {
            createFolderModal.style.opacity = '0';
            bgBlur.style.opacity = '0';
            setTimeout(() => {
                createFolderModal.style.zIndex = '-1';
                bgBlur.style.zIndex = '-1';
            }, 200);
        }
    }

    if (newFolderBtn && createFolderModal) {
        newFolderBtn.addEventListener('click', () => {
            newUploadDropdown.classList.remove('active');
            if (newFolderNameInput) newFolderNameInput.value = '';

            bgBlur.style.zIndex = '100';
            bgBlur.style.opacity = '1';

            createFolderModal.style.zIndex = '101';
            createFolderModal.style.opacity = '1';

            setTimeout(() => {
                if (newFolderNameInput) newFolderNameInput.focus();
            }, 200);
        });

        if (newFolderCancel) {
            newFolderCancel.addEventListener('click', closeNewFolderModal);
        }

        if (newFolderCreate) {
            newFolderCreate.addEventListener('click', createNewFolder);
        }

        if (newFolderNameInput) {
            newFolderNameInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    createNewFolder();
                } else if (e.key === 'Escape') {
                    closeNewFolderModal();
                }
            });
        }
    }

    // URL / Remote Upload Button & Modal
    const urlUploadBtn = document.getElementById('url-upload-btn');
    const urlUploadModal = document.getElementById('new-url-upload');
    const remoteUrlInput = document.getElementById('remote-url');
    const remoteCancel = document.getElementById('remote-cancel');
    const remoteStart = document.getElementById('remote-start');

    function closeUrlModal() {
        if (urlUploadModal) {
            urlUploadModal.style.opacity = '0';
            bgBlur.style.opacity = '0';
            setTimeout(() => {
                urlUploadModal.style.zIndex = '-1';
                bgBlur.style.zIndex = '-1';
            }, 200);
        }
    }

    if (urlUploadBtn && urlUploadModal) {
        urlUploadBtn.addEventListener('click', () => {
            newUploadDropdown.classList.remove('active');
            if (remoteUrlInput) remoteUrlInput.value = '';

            bgBlur.style.zIndex = '100';
            bgBlur.style.opacity = '1';

            urlUploadModal.style.zIndex = '101';
            urlUploadModal.style.opacity = '1';

            setTimeout(() => {
                if (remoteUrlInput) remoteUrlInput.focus();
            }, 200);
        });

        if (remoteCancel) {
            remoteCancel.addEventListener('click', closeUrlModal);
        }

        if (remoteStart) {
            remoteStart.addEventListener('click', Start_URL_Upload);
        }

        if (remoteUrlInput) {
            remoteUrlInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    Start_URL_Upload();
                } else if (e.key === 'Escape') {
                    closeUrlModal();
                }
            });
        }
    }

    // Close any open modal on Escape key press anywhere
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeNewFolderModal();
            closeUrlModal();
        }
    });

    // Sidebar collapse & mobile off-canvas drawer toggle
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebarCloseBtn = document.getElementById('sidebar-close-btn');
    const sidebar = document.getElementById('gd-sidebar');
    const sidebarBackdrop = document.getElementById('sidebar-backdrop');

    function closeSidebarDrawer() {
        if (sidebar) sidebar.classList.remove('open');
        if (sidebarBackdrop) sidebarBackdrop.classList.remove('active');
        document.body.classList.remove('drawer-open');
    }

    function openSidebarDrawer() {
        if (sidebar) sidebar.classList.add('open');
        if (sidebarBackdrop) sidebarBackdrop.classList.add('active');
        document.body.classList.add('drawer-open');
    }

    function toggleSidebar() {
        if (window.innerWidth <= 768) {
            if (sidebar && sidebar.classList.contains('open')) {
                closeSidebarDrawer();
            } else {
                openSidebarDrawer();
            }
        } else {
            if (sidebar) {
                sidebar.classList.toggle('collapsed');
            }
        }
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSidebar();
        });
    }

    if (sidebarCloseBtn) {
        sidebarCloseBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeSidebarDrawer();
        });
    }

    if (sidebarBackdrop) {
        sidebarBackdrop.addEventListener('click', closeSidebarDrawer);
    }

    // Auto-close mobile drawer when navigation links are clicked
    document.querySelectorAll('.gd-nav-item').forEach(item => {
        item.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                closeSidebarDrawer();
            }
        });
    });

    // Handle screen resize smoothly without stuck classes
    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            closeSidebarDrawer();
        } else {
            if (sidebar) sidebar.classList.remove('collapsed');
        }
    });

    // Mobile FAB and "+ New" Bottom Sheet
    const mobileFab = document.getElementById('mobile-fab');
    const fabBottomSheet = document.getElementById('fab-bottom-sheet');
    const fabBackdrop = document.getElementById('fab-sheet-backdrop');
    const fabSheetClose = document.getElementById('fab-sheet-close');

    function closeFabBottomSheet() {
        if (fabBottomSheet) fabBottomSheet.classList.remove('active');
        if (fabBackdrop) fabBackdrop.classList.remove('active');
    }

    if (mobileFab && fabBottomSheet) {
        mobileFab.addEventListener('click', () => {
            if (fabBackdrop) fabBackdrop.classList.add('active');
            fabBottomSheet.classList.add('active');
        });
    }

    if (fabBackdrop) fabBackdrop.addEventListener('click', closeFabBottomSheet);
    if (fabSheetClose) fabSheetClose.addEventListener('click', closeFabBottomSheet);

    const mobNewFolder = document.getElementById('mob-new-folder');
    if (mobNewFolder && newFolderBtn) {
        mobNewFolder.addEventListener('click', () => {
            closeFabBottomSheet();
            newFolderBtn.click();
        });
    }

    const mobFileUpload = document.getElementById('mob-file-upload');
    if (mobFileUpload && fileInput) {
        mobFileUpload.addEventListener('click', () => {
            closeFabBottomSheet();
            fileInput.click();
        });
    }

    const mobUrlUpload = document.getElementById('mob-url-upload');
    if (mobUrlUpload && urlUploadBtn) {
        mobUrlUpload.addEventListener('click', () => {
            closeFabBottomSheet();
            urlUploadBtn.click();
        });
    }
});