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
    const navComputers = document.getElementById('nav-computers');
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

    if (navComputers) {
        navComputers.addEventListener('click', (e) => {
            e.preventDefault();
            navigateToPath('/');
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

    // Sidebar collapse toggle
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('gd-sidebar');
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
        });
    }
});