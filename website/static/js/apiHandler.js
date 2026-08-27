// Api Functions
// Auth is handled via HttpOnly session cookies (sent automatically by credentials: 'same-origin').
// Never inject passwords into request bodies.
async function postJson(url, data) {
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'same-origin',
            body: JSON.stringify(data)
        })
        if (response.status === 401) {
            showLoginModal(false);
            return { status: 'Unauthorized' };
        }
        return await response.json()
    } catch (e) {
        return { status: 'Network error or service unavailable' }
    }
}

window.IS_AUTHENTICATED = false;

// ==========================================
// Login Modal — Two-Step Auth (Email+Password → OTP)
// ==========================================

function showLoginModal(forceReset = false) {
    window.IS_AUTHENTICATED = false;
    const bg = document.getElementById('bg-blur');
    const modal = document.getElementById('get-password');
    const step1 = document.getElementById('login-step-1');
    const step2 = document.getElementById('login-step-2');
    const isStep2Active = step2 && step2.style.display !== 'none';
    if (forceReset || !isStep2Active) {
        if (step1) step1.style.display = '';
        if (step2) step2.style.display = 'none';
    }
    if (bg) { bg.style.zIndex = '100'; bg.style.opacity = '1'; }
    if (modal) { modal.style.zIndex = '101'; modal.style.opacity = '1'; }
}

function hideLoginModal() {
    window.IS_AUTHENTICATED = true;
    const bg = document.getElementById('bg-blur');
    const modal = document.getElementById('get-password');
    const step1 = document.getElementById('login-step-1');
    const step2 = document.getElementById('login-step-2');
    if (bg) { bg.style.zIndex = '-1'; bg.style.opacity = '0'; }
    if (modal) { modal.style.zIndex = '-1'; modal.style.opacity = '0'; }
    setTimeout(() => {
        if (step1) step1.style.display = '';
        if (step2) step2.style.display = 'none';
        const err1 = document.getElementById('login-error');
        const err2 = document.getElementById('otp-error');
        if (err1) err1.style.display = 'none';
        if (err2) err2.style.display = 'none';
    }, 300);
}

// Initialize Auth Modal Event Listeners
function initAuthListeners() {
    // Step 1: Submit email + password → triggers OTP email
    const loginBtn = document.getElementById('pass-login');
    if (loginBtn && !loginBtn.dataset.bound) {
        loginBtn.dataset.bound = 'true';
        loginBtn.addEventListener('click', async () => {
            const emailInput = document.getElementById('auth-email');
            const passInput = document.getElementById('auth-pass');
            const email = emailInput ? emailInput.value.trim() : '';
            const password = passInput ? passInput.value : '';
            const errEl = document.getElementById('login-error');
            if (!email || !password) {
                if (errEl) { errEl.textContent = 'Email and password are required.'; errEl.style.display = ''; }
                return;
            }
            loginBtn.disabled = true;
            loginBtn.textContent = 'Sending OTP...';
            if (errEl) errEl.style.display = 'none';

            try {
                const resp = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ email, password })
                });
                const json = await resp.json();
                if (json.status === 'otp_sent') {
                    // Advance to OTP step
                    document.getElementById('login-step-1').style.display = 'none';
                    document.getElementById('login-step-2').style.display = '';
                    const subtext = document.querySelector('#login-step-2 .gd-login-subtext');
                    if (subtext && json.message) {
                        subtext.textContent = json.message + ' Enter it below.';
                    }
                    const otpInput = document.getElementById('auth-otp');
                    if (otpInput) { otpInput.value = ''; otpInput.focus(); }
                } else if (json.status === 'ok') {
                    // Single-factor mode (no ADMIN_EMAIL configured): session cookie
                    // was already set by this response — enter the drive directly.
                    hideLoginModal();
                    getCurrentDirectory();
                } else {
                    const msg = json.detail || json.status || 'Authentication failed.';
                    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
                }
            } catch (e) {
                if (errEl) { errEl.textContent = 'Network error. Please try again.'; errEl.style.display = ''; }
            } finally {
                loginBtn.disabled = false;
                loginBtn.textContent = 'Continue';
            }
        });
    }

    // Step 2: Submit OTP
    const otpBtn = document.getElementById('otp-verify');
    if (otpBtn && !otpBtn.dataset.bound) {
        otpBtn.dataset.bound = 'true';
        otpBtn.addEventListener('click', async () => {
            const otpInput = document.getElementById('auth-otp');
            const otp = otpInput ? otpInput.value.trim() : '';
            const errEl = document.getElementById('otp-error');
            if (!otp || otp.length !== 6) {
                if (errEl) { errEl.textContent = 'Enter the 6-digit code sent to your email.'; errEl.style.display = ''; }
                return;
            }
            otpBtn.disabled = true;
            otpBtn.textContent = 'Verifying...';
            if (errEl) errEl.style.display = 'none';

            try {
                const resp = await fetch('/api/verifyOtp', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'same-origin',
                    body: JSON.stringify({ otp })
                });
                const json = await resp.json();
                if (json.status === 'ok') {
                    hideLoginModal();
                    if (typeof getCurrentDirectory === 'function') {
                        getCurrentDirectory();
                    } else {
                        window.location.reload();
                    }
                } else {
                    const msg = json.detail || json.status || 'Invalid or expired code.';
                    if (errEl) { errEl.textContent = msg; errEl.style.display = ''; }
                }
            } catch (e) {
                if (errEl) { errEl.textContent = 'Network error. Please try again.'; errEl.style.display = ''; }
            } finally {
                otpBtn.disabled = false;
                otpBtn.textContent = 'Verify';
            }
        });
    }

    // OTP back button → return to step 1
    const backBtn = document.getElementById('otp-back');
    if (backBtn && !backBtn.dataset.bound) {
        backBtn.dataset.bound = 'true';
        backBtn.addEventListener('click', () => {
            document.getElementById('login-step-1').style.display = '';
            document.getElementById('login-step-2').style.display = 'none';
            const errEl = document.getElementById('otp-error');
            if (errEl) errEl.style.display = 'none';
        });
    }

    // Profile avatar → Logout
    const avatar = document.getElementById('profile-avatar');
    if (avatar && !avatar.dataset.bound) {
        avatar.dataset.bound = 'true';
        avatar.style.cursor = 'pointer';
        avatar.title = 'Click to Logout / Lock Drive';
        avatar.addEventListener('click', async () => {
            if (confirm('Do you want to log out of your Admin session?')) {
                try {
                    await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' });
                } catch (e) { }
                window.location.reload();
            }
        });
    }

    // Allow Enter key to submit OTP
    const otpInput = document.getElementById('auth-otp');
    if (otpInput && !otpInput.dataset.bound) {
        otpInput.dataset.bound = 'true';
        otpInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') document.getElementById('otp-verify')?.click();
        });
    }

    // Allow Enter key to submit credentials
    const passInput = document.getElementById('auth-pass');
    if (passInput && !passInput.dataset.bound) {
        passInput.dataset.bound = 'true';
        passInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') document.getElementById('pass-login')?.click();
        });
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAuthListeners);
} else {
    initAuthListeners();
}

// Background Active Upload Monitor
let WAS_UPLOADING = false;
setInterval(async () => {
    if (!window.IS_AUTHENTICATED || getCurrentPath().includes('/share_')) return;
    try {
        const res = await postJson('/api/getActiveUploads', {});
        if (res.status === 'ok' && res.active && res.active.length > 0) {
            WAS_UPLOADING = true;
            const currentItem = res.active[0];
            const uploaderCard = document.getElementById('file-uploader');
            if (uploaderCard) uploaderCard.classList.add('active');

            const filenameEl = document.getElementById('upload-filename');
            const statusEl = document.getElementById('upload-status');
            const sizeEl = document.getElementById('upload-filesize');
            const percentEl = document.getElementById('upload-percent');
            const progressFill = document.getElementById('progress-bar');

            if (filenameEl) filenameEl.innerText = currentItem.filename || 'Syncing file...';
            if (statusEl) statusEl.innerText = `Uploading ${res.active.length} item(s) to Cloud...`;

            const total = currentItem.total || 0;
            const current = currentItem.current || 0;
            if (sizeEl) sizeEl.innerText = total > 0 ? (total / (1024 * 1024)).toFixed(2) + ' MB' : 'Processing...';

            const pct = total > 0 ? Math.min(Math.round((current / total) * 100), 99) : 0;
            if (percentEl) percentEl.innerText = pct + '%';
            if (progressFill) progressFill.style.width = pct + '%';
        } else if (WAS_UPLOADING) {
            WAS_UPLOADING = false;
            const statusEl = document.getElementById('upload-status');
            const percentEl = document.getElementById('upload-percent');
            const progressFill = document.getElementById('progress-bar');
            if (statusEl) statusEl.innerText = '✅ Sync Complete!';
            if (percentEl) percentEl.innerText = '100%';
            if (progressFill) progressFill.style.width = '100%';

            setTimeout(() => {
                const uploaderCard = document.getElementById('file-uploader');
                if (uploaderCard) uploaderCard.classList.remove('active');
                getCurrentDirectory();
            }, 1800);
        }
    } catch { }
}, 2000);

async function getCurrentDirectory() {
    const requestId = ++window.NAV_REQUEST_ID;
    const path = getCurrentPath();

    if (path === '/shared_links' || path.startsWith('/shared_links')) {
        if (typeof window.showSharedLinksView === 'function') {
            window.showSharedLinksView(false);
            return;
        }
    } else {
        if (typeof window.hideSharedLinksView === 'function') {
            window.hideSharedLinksView();
        }
    }

    if (path === '/duplicates' || path.startsWith('/duplicates')) {
        if (typeof window.showDuplicatesView === 'function') {
            window.showDuplicatesView(false);
            return;
        }
    } else {
        if (typeof window.hideDuplicatesView === 'function') {
            window.hideDuplicatesView();
        }
    }

    // Immediately trigger skeleton loader to eliminate stale folder rendering
    if (typeof showDirectorySkeleton === 'function') {
        showDirectorySkeleton();
    }

    const errorContainer = document.getElementById('directory-error-state');
    if (errorContainer) errorContainer.style.display = 'none';

    try {
        const auth = getFolderAuthFromPath();
        const data = { 'path': path, 'auth': auth };
        const json = await postJson('/api/getDirectory', data);

        // Race-condition guard: Discard response if user has navigated to a newer path
        if (requestId !== window.NAV_REQUEST_ID) {
            console.debug(`[Navigation] Discarded stale directory response for request #${requestId} (current: #${window.NAV_REQUEST_ID})`);
            return;
        }

        if (json.status === 'ok') {
            window.IS_AUTHENTICATED = true;
            if (path.startsWith('/share')) {
                const navMyDrive = document.getElementById('nav-my-drive');
                if (navMyDrive) {
                    if (removeSlash(json['auth_home_path']) === removeSlash(path.split('_')[1])) {
                        navMyDrive.className = 'gd-nav-item selected-item';
                    } else {
                        navMyDrive.className = 'gd-nav-item unselected-item';
                    }
                    navMyDrive.href = `/?path=/share_${removeSlash(json['auth_home_path'])}&auth=${auth}`;
                }
            }

            if (json.stats && typeof updateSidebarStorageStats === 'function') {
                updateSidebarStorageStats(json.stats);
            }
            showDirectory(json['data'], json['breadcrumbs'] || []);
        } else if (json.status === 'Unauthorized' || json.status === 'Unauthorized folder access') {
            showLoginModal(false);
        } else {
            if (typeof showDirectoryError === 'function') {
                showDirectoryError(json.status || 'Folder not accessible', path);
            } else {
                showToast('Directory not accessible: ' + (json.status || 'Not Found'));
            }
        }
    }
    catch (err) {
        console.error(err);
        if (requestId === window.NAV_REQUEST_ID) {
            if (typeof showDirectoryError === 'function') {
                showDirectoryError('Could not access current directory. Please check connection.', path);
            } else {
                showToast('Could not access current directory');
            }
        }
    }
}

async function refreshCurrentDirectory() {
    const btn = document.getElementById('refresh-dir-btn');
    if (btn) btn.classList.add('is-refreshing');
    try {
        if (window.IS_AUTHENTICATED && !getCurrentPath().includes('/share_')) {
            try {
                await postJson('/api/syncDriveData', {});
            } catch (e) {}
        }
        await getCurrentDirectory();
        showToast('Folder refreshed & synced 🔄');
    } catch (e) {
        console.error('Refresh error:', e);
    } finally {
        setTimeout(() => {
            if (btn) btn.classList.remove('is-refreshing');
        }, 600);
    }
}

async function createNewFolder() {
    const folderName = document.getElementById('new-folder-name').value.trim();
    const path = getCurrentPath();
    if (folderName.length > 0) {
        const data = {
            'name': folderName,
            'path': path
        };
        try {
            const json = await postJson('/api/createNewFolder', data);

            if (json.status === 'ok') {
                const modal = document.getElementById('create-new-folder');
                const bgBlur = document.getElementById('bg-blur');
                if (modal) modal.style.opacity = '0';
                if (bgBlur) bgBlur.style.opacity = '0';
                setTimeout(() => {
                    if (modal) modal.style.zIndex = '-1';
                    if (bgBlur) bgBlur.style.zIndex = '-1';
                }, 200);
                showToast(`Folder "${folderName}" created! 📁`);
                broadcastDriveChange('NEW_FOLDER', { name: folderName, path });
                getCurrentDirectory();
            } else {
                alert(json.status);
            }
        }
        catch (err) {
            alert('Error Creating Folder');
        }
    } else {
        alert('Folder Name Cannot Be Empty');
    }
}

async function moveFileFolder(srcPath, destPath) {
    const data = {
        'src_path': srcPath,
        'dest_path': destPath
    };
    try {
        const json = await postJson('/api/moveFileFolder', data);
        if (json.status === 'ok') {
            showToast('Item moved successfully! 📦');
            broadcastDriveChange('MOVE', { srcPath, destPath });
            getCurrentDirectory();
        } else {
            alert('Failed to move item: ' + (json.status || 'Error'));
        }
    } catch (err) {
        console.error(err);
        alert('Error moving item');
    }
}

async function copyFileFolder(srcPath, destPath = null) {
    const data = {
        'src_path': srcPath,
        'dest_path': destPath
    };
    try {
        const json = await postJson('/api/copyFileFolder', data);
        if (json.status === 'ok') {
            showToast('Item copied successfully! 📋');
            broadcastDriveChange('COPY', { srcPath, destPath });
            getCurrentDirectory();
        } else {
            alert('Failed to copy item: ' + (json.status || json.detail || 'Error'));
        }
    } catch (err) {
        console.error(err);
        alert('Error copying item');
    }
}

async function getFolderShareAuth(path) {
    const data = { 'path': path };
    const json = await postJson('/api/getFolderShareAuth', data);
    if (json.status === 'ok') {
        return json.auth;
    } else {
        alert('Error Getting Folder Share Auth');
    }
}

// File Uploader Start (Supports Multiple Files & Batch Queue)

const MAX_FILE_SIZE = MAX_FILE_SIZE__SDGJDG; // Replaced dynamically by server

const fileInput = document.getElementById('fileInput');
const progressBar = document.getElementById('progress-bar');
const cancelButton = document.getElementById('cancel-file-upload');
const uploadPercent = document.getElementById('upload-percent');

let uploadRequest = null;
let uploadStep = 0;
let uploadID = null;
let UPLOAD_QUEUE = [];
let IS_UPLOADING_QUEUE = false;

function promptFileConflict(file, destPath, existingInfo) {
    return new Promise((resolve) => {
        const bgBlur = document.getElementById('bg-blur');
        const modal = document.getElementById('conflict-resolution-modal');
        const nameEl = document.getElementById('conflict-file-name');
        const detailsEl = document.getElementById('conflict-file-details');
        const replaceBtn = document.getElementById('conflict-replace-btn');
        const keepBothBtn = document.getElementById('conflict-keep-both-btn');
        const cancelBtn = document.getElementById('conflict-cancel-btn');

        if (!modal) {
            // Fallback to confirm prompt if modal DOM is missing
            const ans = confirm(`"${file.name}" already exists in this folder.\n\nClick OK to Replace, or Cancel to Keep Both.`);
            resolve(ans ? 'replace' : 'keep_both');
            return;
        }

        if (nameEl) nameEl.textContent = file.name;
        if (detailsEl) {
            const newSize = typeof convertBytes === 'function' ? convertBytes(file.size) : (file.size + ' B');
            const oldSize = existingInfo && existingInfo.size ? (typeof convertBytes === 'function' ? convertBytes(existingInfo.size) : existingInfo.size + ' B') : 'Existing';
            detailsEl.textContent = `New upload: ${newSize} • Existing on cloud: ${oldSize}`;
        }

        function closeModal() {
            if (bgBlur) bgBlur.style.opacity = '0';
            if (modal) modal.style.opacity = '0';
            setTimeout(() => {
                if (bgBlur) bgBlur.style.zIndex = '-1';
                if (modal) modal.style.zIndex = '-1';
            }, 200);
            cleanup();
        }

        function onReplace() {
            closeModal();
            resolve('replace');
        }

        function onKeepBoth() {
            closeModal();
            resolve('keep_both');
        }

        function onCancel() {
            closeModal();
            resolve('cancel');
        }

        function cleanup() {
            if (replaceBtn) replaceBtn.removeEventListener('click', onReplace);
            if (keepBothBtn) keepBothBtn.removeEventListener('click', onKeepBoth);
            if (cancelBtn) cancelBtn.removeEventListener('click', onCancel);
        }

        if (replaceBtn) replaceBtn.addEventListener('click', onReplace);
        if (keepBothBtn) keepBothBtn.addEventListener('click', onKeepBoth);
        if (cancelBtn) cancelBtn.addEventListener('click', onCancel);

        if (bgBlur) {
            bgBlur.style.zIndex = '100';
            bgBlur.style.opacity = '1';
        }
        modal.style.zIndex = '101';
        modal.style.opacity = '1';
    });
}

async function uploadFilesQueue(files, targetPath, options = {}) {
    if (!files || files.length === 0) {
        if (options && options.emptyFolders && options.emptyFolders.length > 0) {
            const destPath = targetPath || getCurrentPath();
            try {
                await postJson('/api/createFolderTree', { base_path: destPath, folders: options.emptyFolders });
                showToast(`📁 Created ${options.emptyFolders.length} folder${options.emptyFolders.length === 1 ? '' : 's'}`);
                getCurrentDirectory();
            } catch (e) {
                console.error('Failed to create empty folders:', e);
            }
        }
        return;
    }
    const destPath = targetPath || getCurrentPath();
    const batchId = (options && options.batchId) || ('batch_' + (typeof getRandomId === 'function' ? getRandomId() : Date.now()));

    // Pre-create empty folders if supplied in options or discovered from directory scan
    if (options && options.emptyFolders && options.emptyFolders.length > 0) {
        try {
            await postJson('/api/createFolderTree', { base_path: destPath, folders: options.emptyFolders });
        } catch (e) {
            console.debug('Folder tree pre-creation note:', e);
        }
    }

    const fileArray = Array.from(files);
    for (let i = 0; i < fileArray.length; i++) {
        const file = fileArray[i];
        if (file.size > MAX_FILE_SIZE) {
            showToast(`⚠️ "${file.name}" exceeds ${(MAX_FILE_SIZE / (1024 * 1024 * 1024)).toFixed(2)} GB limit`);
            continue;
        }

        const relativePath = file.webkitRelativePath || file._relativePath || '';

        // Check if file already exists in this path
        let conflictChoice = (options && options.defaultConflict) || 'keep_both';
        try {
            const checkRes = await postJson('/api/checkFileExists', {
                path: destPath,
                filename: file.name,
                relative_path: relativePath || undefined
            });
            if (checkRes && checkRes.exists) {
                conflictChoice = await promptFileConflict(file, destPath, checkRes);
                if (conflictChoice === 'cancel') {
                    showToast(`Skipped upload for "${file.name}"`);
                    continue;
                }
            }
        } catch (e) {
            conflictChoice = 'keep_both';
        }

        UPLOAD_QUEUE.push({
            file: file,
            path: destPath,
            relativePath: relativePath,
            batchId: batchId,
            conflict: conflictChoice
        });
    }

    if (!IS_UPLOADING_QUEUE && UPLOAD_QUEUE.length > 0) {
        processUploadQueue();
    }
}

async function processUploadQueue() {
    if (UPLOAD_QUEUE.length === 0) {
        IS_UPLOADING_QUEUE = false;
        const uploaderCard = document.getElementById('file-uploader');
        if (uploaderCard) {
            const statusEl = document.getElementById('upload-status');
            if (statusEl) statusEl.innerText = '✅ All uploads complete!';
            setTimeout(() => {
                uploaderCard.classList.remove('active');
                broadcastDriveChange('UPLOAD');
                getCurrentDirectory();
            }, 1200);
        }
        return;
    }

    IS_UPLOADING_QUEUE = true;
    const currentItem = UPLOAD_QUEUE.shift();
    const file = currentItem.file;
    const path = currentItem.path;
    const conflictMode = currentItem.conflict || 'keep_both';
    const relativePath = currentItem.relativePath || '';
    const batchId = currentItem.batchId || '';
    const remaining = UPLOAD_QUEUE.length;

    const uploaderCard = document.getElementById('file-uploader');
    if (uploaderCard) uploaderCard.classList.add('active');

    const filenameEl = document.getElementById('upload-filename');
    const sizeEl = document.getElementById('upload-filesize');
    const statusEl = document.getElementById('upload-status');

    const displayName = relativePath || file.name;
    if (filenameEl) filenameEl.innerText = displayName;
    if (sizeEl) sizeEl.innerText = (file.size / (1024 * 1024)).toFixed(2) + ' MB';
    if (statusEl) statusEl.innerText = remaining > 0 ? `Uploading (${remaining + 1} items)...` : 'Uploading to Drive...';
    if (progressBar) progressBar.style.width = '0%';
    if (uploadPercent) uploadPercent.innerText = '0%';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('path', path);
    formData.append('conflict', conflictMode);
    if (relativePath) {
        formData.append('relative_path', relativePath);
    }
    if (batchId) {
        formData.append('batch_id', batchId);
    }
    const id = getRandomId();
    formData.append('id', id);
    formData.append('total_size', file.size);

    uploadStep = 1;
    uploadID = id;
    uploadRequest = new XMLHttpRequest();
    uploadRequest.open('POST', '/api/upload', true);

    uploadRequest.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
            const percentComplete = (e.loaded / e.total) * 100;
            if (progressBar) progressBar.style.width = percentComplete + '%';
            if (uploadPercent) uploadPercent.innerText = percentComplete.toFixed(0) + '%';
        }
    });

    uploadRequest.upload.addEventListener('load', async () => {
        await updateSaveProgress(id, () => {
            processUploadQueue();
        });
    });

    uploadRequest.upload.addEventListener('error', () => {
        showToast(`❌ Upload failed for "${file.name}"`);
        processUploadQueue();
    });

    uploadRequest.send(formData);
}

// Recursive scanner for drag & drop directories and files
async function scanDataTransferItems(dataTransfer) {
    const files = [];
    const emptyFolders = [];

    if (!dataTransfer || !dataTransfer.items) {
        if (dataTransfer && dataTransfer.files) {
            return { files: Array.from(dataTransfer.files), emptyFolders: [] };
        }
        return { files: [], emptyFolders: [] };
    }

    async function traverseEntry(entry, path = '') {
        if (!entry) return;
        if (entry.isFile) {
            try {
                const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
                file._relativePath = path ? `${path}/${file.name}` : file.name;
                files.push(file);
            } catch (err) {
                console.warn('Error reading dropped file entry:', err);
            }
        } else if (entry.isDirectory) {
            const dirPath = path ? `${path}/${entry.name}` : entry.name;
            const dirReader = entry.createReader();
            let allEntries = [];

            async function readBatch() {
                return new Promise((resolve, reject) => {
                    dirReader.readEntries((batch) => {
                        if (!batch || !batch.length) {
                            resolve(allEntries);
                        } else {
                            allEntries = allEntries.concat(batch);
                            readBatch().then(resolve, reject);
                        }
                    }, reject);
                });
            }

            try {
                await readBatch();
                if (allEntries.length === 0) {
                    emptyFolders.push(dirPath);
                } else {
                    for (const childEntry of allEntries) {
                        await traverseEntry(childEntry, dirPath);
                    }
                }
            } catch (dirErr) {
                console.warn('Error reading directory entries:', dirErr);
            }
        }
    }

    const entries = [];
    for (let i = 0; i < dataTransfer.items.length; i++) {
        const item = dataTransfer.items[i];
        if (item.webkitGetAsEntry) {
            const entry = item.webkitGetAsEntry();
            if (entry) entries.push(entry);
        }
    }

    if (entries.length > 0) {
        for (const entry of entries) {
            await traverseEntry(entry);
        }
    } else if (dataTransfer.files && dataTransfer.files.length > 0) {
        for (let i = 0; i < dataTransfer.files.length; i++) {
            files.push(dataTransfer.files[i]);
        }
    }

    return { files, emptyFolders };
}

const folderInput = document.getElementById('folderInput');
if (folderInput) {
    folderInput.addEventListener('change', (e) => {
        if (folderInput.files && folderInput.files.length > 0) {
            uploadFilesQueue(folderInput.files, getCurrentPath(), { isFolder: true });
            folderInput.value = '';
        }
    });
}

if (fileInput) {
    fileInput.addEventListener('change', (e) => {
        if (fileInput.files && fileInput.files.length > 0) {
            uploadFilesQueue(fileInput.files, getCurrentPath());
            fileInput.value = '';
        }
    });
}

if (cancelButton) {
    cancelButton.addEventListener('click', () => {
        UPLOAD_QUEUE = [];
        if (uploadStep === 1 && uploadRequest) {
            uploadRequest.abort();
        } else if (uploadStep === 2 && uploadID) {
            postJson('/api/cancelUpload', { 'id': uploadID });
        }
        IS_UPLOADING_QUEUE = false;
        const uploaderCard = document.getElementById('file-uploader');
        if (uploaderCard) uploaderCard.classList.remove('active');
        showToast('Upload cancelled');
        getCurrentDirectory();
    });
}

async function updateSaveProgress(id, onComplete) {
    if (progressBar) progressBar.style.width = '0%';
    if (uploadPercent) uploadPercent.innerText = 'Progress: 0%';
    const statusEl = document.getElementById('upload-status');
    if (statusEl) statusEl.innerText = 'Status: Processing on server...';

    let missedPolls = 0;
    const interval = setInterval(async () => {
        const response = await postJson('/api/getSaveProgress', { 'id': id });
        const data = response['data'];

        if (data && data[0] === 'running') {
            missedPolls = 0;
            const current = data[1];
            const total = data[2];
            const sizeEl = document.getElementById('upload-filesize');
            if (sizeEl) sizeEl.innerText = 'Filesize: ' + (total / (1024 * 1024)).toFixed(2) + ' MB';

            const percentComplete = total > 0 ? (current / total) * 100 : 0;
            if (progressBar) progressBar.style.width = percentComplete + '%';
            if (uploadPercent) uploadPercent.innerText = 'Progress: ' + percentComplete.toFixed(0) + '%';
        }
        else if (data && data[0] === 'completed') {
            clearInterval(interval);
            if (uploadPercent) uploadPercent.innerText = 'Progress: 100%';
            if (progressBar) progressBar.style.width = '100%';
            await handleUpload2(id, onComplete);
        }
        else {
            // Terminal failure ('error') or progress entry lost server-side
            missedPolls++;
            if ((data && data[0] === 'error') || missedPolls > 10) {
                clearInterval(interval);
                showToast('❌ Server processing failed for the current upload');
                if (typeof processUploadQueue === 'function') processUploadQueue();
            }
        }
    }, 2000);
}

async function handleUpload2(id, onComplete) {
    const statusEl = document.getElementById('upload-status');
    if (statusEl) statusEl.innerText = 'Status: Storing to Telegram Cloud...';
    if (progressBar) progressBar.style.width = '0%';
    if (uploadPercent) uploadPercent.innerText = 'Progress: 0%';

    let missedPolls = 0;
    const interval = setInterval(async () => {
        const response = await postJson('/api/getUploadProgress', { 'id': id });
        const data = response['data'];

        if (data && (data[0] === 'running' || data[0] === 'waiting')) {
            missedPolls = 0;
            const current = data[1];
            const total = data[2];
            const sizeEl = document.getElementById('upload-filesize');
            if (sizeEl) sizeEl.innerText = 'Filesize: ' + (total / (1024 * 1024)).toFixed(2) + ' MB';

            let percentComplete = total > 0 ? (current / total) * 100 : 0;
            if (progressBar) progressBar.style.width = percentComplete + '%';
            if (uploadPercent) uploadPercent.innerText = 'Progress: ' + percentComplete.toFixed(0) + '%';

            // Telegram rate-limit pause: keep the card alive and informative
            if (statusEl) {
                statusEl.innerText = (data[0] === 'waiting')
                    ? 'Status: Telegram rate-limit pause — upload will resume automatically...'
                    : 'Status: Storing to Telegram Cloud...';
            }
        }
        else if (data && data[0] === 'completed') {
            clearInterval(interval);
            if (typeof onComplete === 'function') {
                onComplete();
            } else {
                showToast('✅ Upload completed!');
                getCurrentDirectory();
            }
        }
        else {
            // Terminal failure ('error') or progress entry lost server-side
            missedPolls++;
            if ((data && data[0] === 'error') || missedPolls > 10) {
                clearInterval(interval);
                showToast(`❌ Upload failed for "${data && data[3] ? data[3] : 'file'}"`);
                if (typeof processUploadQueue === 'function') processUploadQueue();
                else getCurrentDirectory();
            }
        }
    }, 2000);
}

// File Uploader End


// URL Uploader Start

async function get_file_info_from_url(url) {
    const data = { 'url': url }
    const json = await postJson('/api/getFileInfoFromUrl', data)
    if (json.status === 'ok') {
        return json.data
    } else {
        throw new Error(`Error Getting File Info : ${json.status}`)
    }

}

async function start_file_download_from_url(url, filename, singleThreaded) {
    const data = { 'url': url, 'path': getCurrentPath(), 'filename': filename, 'singleThreaded': singleThreaded }
    const json = await postJson('/api/startFileDownloadFromUrl', data)
    if (json.status === 'ok') {
        return json.id
    } else {
        throw new Error(`Error Starting File Download : ${json.status}`)
    }
}

async function download_progress_updater(id, file_name, file_size) {
    uploadID = id;
    uploadStep = 2
    let missedPolls = 0;
    // Showing file uploader
    document.getElementById('bg-blur').style.zIndex = '2';
    document.getElementById('bg-blur').style.opacity = '0.1';
    document.getElementById('file-uploader').style.zIndex = '3';
    document.getElementById('file-uploader').style.opacity = '1';

    document.getElementById('upload-filename').innerText = 'Filename: ' + file_name;
    document.getElementById('upload-filesize').innerText = 'Filesize: ' + (file_size / (1024 * 1024)).toFixed(2) + ' MB';

    const interval = setInterval(async () => {
        const response = await postJson('/api/getFileDownloadProgress', { 'id': id })
        const data = response['data']

        if (!data) {
            // Progress entry not created yet or evicted — keep waiting briefly, then abort
            missedPolls = (missedPolls || 0) + 1;
            if (missedPolls > 10) {
                clearInterval(interval);
                alert('Lost track of the server-side download. Please try again.')
                window.location.reload()
            }
            return;
        }

        if (data[0] === 'error' || data[0] === 'cancelled') {
            clearInterval(interval);
            alert('Failed To Download File From URL To Backend Server')
            window.location.reload()
        }
        else if (data[0] === 'completed') {
            clearInterval(interval);
            uploadPercent.innerText = 'Progress : 100%'
            progressBar.style.width = '100%';
            await handleUpload2(id)
        }
        else {
            const current = data[1];
            const total = data[2];

            const percentComplete = (current / total) * 100;
            progressBar.style.width = percentComplete + '%';
            uploadPercent.innerText = 'Progress : ' + percentComplete.toFixed(2) + '%';

            if (data[0] === 'Downloading') {
                document.getElementById('upload-status').innerText = 'Status: Downloading File From Url To Backend Server';
            }
            else {
                document.getElementById('upload-status').innerText = `Status: ${data[0]}`;
            }
        }
    }, 3000)
}


async function Start_URL_Upload() {
    try {
        document.getElementById('new-url-upload').style.opacity = '0';
        setTimeout(() => {
            document.getElementById('new-url-upload').style.zIndex = '-1';
        }, 300)

        const file_url = document.getElementById('remote-url').value
        const singleThreaded = document.getElementById('single-threaded-toggle').checked

        const file_info = await get_file_info_from_url(file_url)
        const file_name = file_info.file_name
        const file_size = file_info.file_size

        if (file_size > MAX_FILE_SIZE) {
            throw new Error(`File size exceeds ${(MAX_FILE_SIZE / (1024 * 1024 * 1024)).toFixed(2)} GB limit`)
        }

        const id = await start_file_download_from_url(file_url, file_name, singleThreaded)

        await download_progress_updater(id, file_name, file_size)

    }
    catch (err) {
        alert(err)
        window.location.reload()
    }


}

// URL Uploader End

// Google Drive-Style Deep Search Client
async function searchDrive(query, filters = {}, signal = null) {
    const payload = {
        query: query || '',
        ...filters
    };
    const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: signal
    });
    if (!response.ok) {
        if (response.status === 401) {
            showLoginModal(false);
            throw new Error('Unauthorized');
        }
        const errJson = await response.json().catch(() => ({}));
        throw new Error(errJson.detail || `Search failed with status ${response.status}`);
    }
    return await response.json();
}

// Properties & Details System Client Functions
async function fetchFileProperties(fileId, auth = null) {
    const url = `/api/files/${encodeURIComponent(fileId)}/properties${auth ? `?auth=${encodeURIComponent(auth)}` : ''}`;
    const response = await fetch(url, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin'
    });
    if (!response.ok) {
        if (response.status === 401 && !auth) showLoginModal(false);
        return null;
    }
    return await response.json();
}

async function fetchFolderProperties(folderId, auth = null) {
    const url = `/api/folders/${encodeURIComponent(folderId)}/properties${auth ? `?auth=${encodeURIComponent(auth)}` : ''}`;
    const response = await fetch(url, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin'
    });
    if (!response.ok) {
        if (response.status === 401 && !auth) showLoginModal(false);
        return null;
    }
    return await response.json();
}

async function fetchFileActivity(fileId) {
    const url = `/api/files/${encodeURIComponent(fileId)}/activity`;
    const response = await fetch(url, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin'
    });
    if (!response.ok) return null;
    return await response.json();
}

async function fetchFolderActivity(folderId) {
    const url = `/api/folders/${encodeURIComponent(folderId)}/activity`;
    const response = await fetch(url, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin'
    });
    if (!response.ok) return null;
    return await response.json();
}

async function enrichItemMetadata(itemId) {
    return await postJson('/api/properties/enrich', { id: itemId });
}