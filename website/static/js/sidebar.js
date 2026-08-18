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

    // Toggle Nav Menu selection
    const navMyDrive = document.getElementById('nav-my-drive');
    const navComputers = document.getElementById('nav-computers');
    const navTrash = document.getElementById('nav-trash');

    if (isTrash) {
        if (navMyDrive) navMyDrive.className = 'gd-nav-item unselected-item';
        if (navComputers) navComputers.className = 'gd-nav-item unselected-item';
        if (navTrash) navTrash.className = 'gd-nav-item selected-item';
        if (newBtn) newBtn.style.display = 'none';
    } else {
        if (navMyDrive) navMyDrive.className = 'gd-nav-item selected-item';
        if (navComputers) navComputers.className = 'gd-nav-item unselected-item';
        if (navTrash) navTrash.className = 'gd-nav-item unselected-item';
    }

    if (navComputers) {
        navComputers.addEventListener('click', (e) => {
            e.preventDefault();
            window.location.href = '/?path=/';
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

    // New Folder Button
    const newFolderBtn = document.getElementById('new-folder-btn');
    const createFolderModal = document.getElementById('create-new-folder');
    const newFolderNameInput = document.getElementById('new-folder-name');
    const newFolderCancel = document.getElementById('new-folder-cancel');
    const newFolderCreate = document.getElementById('new-folder-create');

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
            newFolderCancel.addEventListener('click', () => {
                createFolderModal.style.opacity = '0';
                bgBlur.style.opacity = '0';
                setTimeout(() => {
                    createFolderModal.style.zIndex = '-1';
                    bgBlur.style.zIndex = '-1';
                }, 200);
            });
        }

        if (newFolderCreate) {
            newFolderCreate.addEventListener('click', createNewFolder);
        }
    }

    // URL / Remote Upload Button
    const urlUploadBtn = document.getElementById('url-upload-btn');
    const urlUploadModal = document.getElementById('new-url-upload');
    const remoteUrlInput = document.getElementById('remote-url');
    const remoteCancel = document.getElementById('remote-cancel');
    const remoteStart = document.getElementById('remote-start');

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
            remoteCancel.addEventListener('click', () => {
                urlUploadModal.style.opacity = '0';
                bgBlur.style.opacity = '0';
                setTimeout(() => {
                    urlUploadModal.style.zIndex = '-1';
                    bgBlur.style.zIndex = '-1';
                }, 200);
            });
        }

        if (remoteStart) {
            remoteStart.addEventListener('click', Start_URL_Upload);
        }
    }

    // Sidebar collapse toggle
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('gd-sidebar');
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
        });
    }
});