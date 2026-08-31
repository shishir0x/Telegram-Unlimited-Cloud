// Duplicate Detection & Management UI Controller

window.DUPLICATES_STATE = {
    groups: [],
    selectedUuids: new Set(),
    searchQuery: '',
    category: 'all',
    sortBy: 'recoverable_size',
    isScanning: false,
    pollTimer: null,
    totalRecoverableBytes: 0,
    duplicateGroupsCount: 0
};

// --- Initialization & View Management ---

function initDuplicatesModule() {
    const searchInput = document.getElementById('dup-search-input');
    if (searchInput) {
        let debounceTimer = null;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                window.DUPLICATES_STATE.searchQuery = e.target.value.trim();
                loadDuplicates();
            }, 300);
        });
    }
}

document.addEventListener('DOMContentLoaded', initDuplicatesModule);

window.showDuplicatesView = function(pushState = true) {
    window.CURRENT_PAGE_VIEW = 'duplicates';

    if (pushState) {
        const url = new URL(window.location);
        url.searchParams.set('path', '/duplicates');
        window.history.pushState({ path: '/duplicates' }, '', url.toString());
    }

    // Hide standard drive elements
    const listView = document.getElementById('list-view-container');
    const gridView = document.getElementById('grid-view-container');
    const syncView = document.getElementById('sync-view-container');
    const sharedLinks = document.getElementById('shared-links-container');
    const transfersView = document.getElementById('transfers-container');
    const breadcrumb = document.getElementById('breadcrumbs-container');
    const searchBanner = document.getElementById('search-results-banner');
    const filterChips = document.getElementById('filter-chips-bar');
    const bulkBar = document.getElementById('bulk-actions-bar');
    const statusBar = document.getElementById('gd-status-bar');
    const dupView = document.getElementById('duplicates-view-container');

    if (listView) listView.style.display = 'none';
    if (gridView) gridView.style.display = 'none';
    if (syncView) syncView.style.display = 'none';
    if (sharedLinks) sharedLinks.style.display = 'none';
    if (transfersView) transfersView.style.display = 'none';
    if (searchBanner) searchBanner.style.display = 'none';
    if (filterChips) filterChips.style.display = 'none';
    if (bulkBar) bulkBar.classList.remove('active');
    if (statusBar) statusBar.style.display = 'none';

    // Set custom breadcrumb for duplicates
    if (breadcrumb) {
        breadcrumb.innerHTML = `
            <span class="gd-crumb" style="cursor: pointer;" onclick="navigateToPath('/')">My Drive</span>
            <span class="gd-crumb-sep">›</span>
            <span class="gd-crumb active">Duplicate Finder</span>
        `;
    }

    if (dupView) {
        dupView.style.display = 'flex';
    }

    updateSidebarNavSelection('/duplicates');
    loadDuplicates();
    checkScanStatus();
};

window.hideDuplicatesView = function() {
    const dupView = document.getElementById('duplicates-view-container');
    if (dupView) {
        dupView.style.display = 'none';
    }
    if (window.DUPLICATES_STATE.pollTimer) {
        clearInterval(window.DUPLICATES_STATE.pollTimer);
        window.DUPLICATES_STATE.pollTimer = null;
    }
};

// --- Scanning Background Task ---

window.startDuplicateScan = async function() {
    const scanBtn = document.getElementById('dup-scan-btn');
    const scanText = document.getElementById('dup-scan-btn-text');
    if (scanBtn) scanBtn.disabled = true;
    if (scanText) scanText.textContent = 'Scanning...';

    try {
        const resp = await fetch('/api/duplicates/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await resp.json();
        pollScanStatus();
    } catch (e) {
        console.error('Failed to trigger duplicate scan:', e);
        if (typeof showToast === 'function') {
            showToast('Failed to start duplicate scan', 'error');
        }
    }
};

async function checkScanStatus() {
    try {
        const resp = await fetch('/api/duplicates/status');
        const data = await resp.json();
        updateScanProgressUI(data);
        if (data.scan_in_progress) {
            pollScanStatus();
        }
    } catch (e) {
        console.error('Failed to check duplicate scan status:', e);
    }
}

function pollScanStatus() {
    if (window.DUPLICATES_STATE.pollTimer) {
        clearInterval(window.DUPLICATES_STATE.pollTimer);
    }
    window.DUPLICATES_STATE.pollTimer = setInterval(async () => {
        try {
            const resp = await fetch('/api/duplicates/status');
            const data = await resp.json();
            updateScanProgressUI(data);
            if (!data.scan_in_progress) {
                clearInterval(window.DUPLICATES_STATE.pollTimer);
                window.DUPLICATES_STATE.pollTimer = null;
                loadDuplicates();
                if (typeof showToast === 'function') {
                    showToast(`Duplicate scan completed. Found ${data.total_duplicates} duplicate files.`, 'success');
                }
            }
        } catch (e) {
            clearInterval(window.DUPLICATES_STATE.pollTimer);
            window.DUPLICATES_STATE.pollTimer = null;
        }
    }, 1500);
}

function updateScanProgressUI(status) {
    const wrap = document.getElementById('dup-progress-wrap');
    const bar = document.getElementById('dup-progress-bar');
    const statusText = document.getElementById('dup-progress-status-text');
    const countText = document.getElementById('dup-progress-count-text');
    const scanBtn = document.getElementById('dup-scan-btn');
    const scanText = document.getElementById('dup-scan-btn-text');

    if (status.scan_in_progress) {
        if (wrap) wrap.style.display = 'block';
        if (scanBtn) scanBtn.disabled = true;
        if (scanText) scanText.textContent = 'Scanning in Background...';

        const percent = Math.min(100, Math.round(status.progress_percent || 0));
        if (bar) bar.style.width = `${percent}%`;
        if (statusText) statusText.textContent = status.current_file ? `Hashing: ${status.current_file}` : 'Scanning directory hierarchy...';
        if (countText) countText.textContent = `${status.hashed_files} / ${status.total_files} (${percent}%)`;
    } else {
        if (wrap) wrap.style.display = 'none';
        if (scanBtn) scanBtn.disabled = false;
        if (scanText) scanText.textContent = 'Scan for Duplicates';
    }
}

// --- Data Fetching & Rendering ---

window.loadDuplicates = async function() {
    const listContainer = document.getElementById('dup-groups-list');
    if (!listContainer) return;

    listContainer.innerHTML = `
        <div class="dup-empty-state">
            <div class="dup-empty-icon">⏳</div>
            <div class="dup-empty-title">Analyzing Duplicates...</div>
            <div class="dup-empty-sub">Cross-referencing cryptographic SHA-256 hashes</div>
        </div>
    `;

    try {
        const resp = await fetch('/api/duplicates/list', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: window.DUPLICATES_STATE.searchQuery,
                category: window.DUPLICATES_STATE.category,
                sort_by: window.DUPLICATES_STATE.sortBy
            })
        });

        const data = await resp.json();
        window.DUPLICATES_STATE.groups = data.groups || [];
        window.DUPLICATES_STATE.totalRecoverableBytes = data.total_recoverable_bytes || 0;
        window.DUPLICATES_STATE.duplicateGroupsCount = data.duplicate_groups_count || 0;
        window.DUPLICATES_STATE.selectedUuids.clear();

        updateSummaryHeader();
        renderDuplicateGroups();
        updateBatchActionBar();
    } catch (e) {
        console.error('Failed to load duplicates list:', e);
        listContainer.innerHTML = `
            <div class="dup-empty-state">
                <div class="dup-empty-icon">⚠️</div>
                <div class="dup-empty-title">Failed to Load Duplicates</div>
                <div class="dup-empty-sub">${escapeHtml(e.message || 'Unknown error occurred')}</div>
            </div>
        `;
    }
};

function updateSummaryHeader() {
    const recText = document.getElementById('dup-recoverable-text');
    const countBadge = document.getElementById('dup-count-badge');

    if (recText) {
        recText.textContent = `${formatBytes(window.DUPLICATES_STATE.totalRecoverableBytes)} Recoverable`;
    }
    if (countBadge) {
        countBadge.textContent = `${window.DUPLICATES_STATE.duplicateGroupsCount} Duplicate Groups`;
    }
}

function renderDuplicateGroups() {
    const listContainer = document.getElementById('dup-groups-list');
    if (!listContainer) return;

    const groups = window.DUPLICATES_STATE.groups;
    if (!groups || groups.length === 0) {
        listContainer.innerHTML = `
            <div class="dup-empty-state">
                <div class="dup-empty-icon">🎉</div>
                <div class="dup-empty-title">No Duplicate Files Found</div>
                <div class="dup-empty-sub">Your drive is completely clean or matches the current filter criteria. Run a scan anytime to re-verify.</div>
            </div>
        `;
        return;
    }

    let html = '';
    groups.forEach((group, groupIdx) => {
        const hashDisplay = group.sha256 ? group.sha256.substring(0, 10) + '...' + group.sha256.substring(58) : 'unknown';
        const copiesCount = group.copies_count || (group.files ? group.files.length : 0);
        const recoverableFormatted = formatBytes(group.recoverable_bytes || 0);
        const fileSizeFormatted = formatBytes(group.file_size || 0);

        html += `
            <div class="dup-group-card" id="dup-group-${groupIdx}">
                <div class="dup-group-header">
                    <div class="dup-group-title">
                        <span>Group #${groupIdx + 1}</span>
                        <span class="dup-group-hash" title="SHA-256: ${escapeHtml(group.sha256)}">SHA-256: ${escapeHtml(hashDisplay)}</span>
                    </div>
                    <div class="dup-group-stats">
                        <span>${copiesCount} copies</span>
                        <span>•</span>
                        <span>File size: ${fileSizeFormatted}</span>
                        <span>•</span>
                        <span class="dup-group-recoverable">Waste: ${recoverableFormatted}</span>
                    </div>
                </div>
                <div class="dup-items-table">
        `;

        (group.files || []).forEach((file, fileIdx) => {
            const isRetained = file.is_retained || fileIdx === 0;
            const isSelected = window.DUPLICATES_STATE.selectedUuids.has(file.file_uuid);
            const icon = getFileIconEmoji(file.filename);
            const uploadFormatted = file.upload_date ? formatFileDate(file.upload_date) : (file.created_at ? formatFileDate(file.created_at) : '--');

            html += `
                <div class="dup-item-row ${isRetained ? 'is-retained' : ''}" id="dup-item-${file.file_uuid}">
                    <div class="dup-item-select">
                        <input type="checkbox" 
                               ${isRetained ? 'disabled title="Original file is protected and cannot be deleted"' : ''}
                               ${isSelected ? 'checked' : ''}
                               onchange="onDuplicateItemCheckboxChange('${file.file_uuid}', this.checked, ${groupIdx})">
                    </div>
                    <div class="dup-item-status">
                        ${isRetained ? `
                            <span class="dup-item-status-tag tag-retained" title="This original copy is safely preserved">
                                🛡️ Original
                            </span>
                        ` : `
                            <span class="dup-item-status-tag tag-duplicate" title="Identical copy of the original">
                                📑 Duplicate
                            </span>
                        `}
                    </div>
                    <div class="dup-item-icon">${icon}</div>
                    <div class="dup-item-info">
                        <div class="dup-item-name" title="${escapeHtml(file.filename)}">${escapeHtml(file.filename)}</div>
                        <div class="dup-item-path" title="${escapeHtml(file.display_path || file.folder_path)}">📁 ${escapeHtml(file.display_path || file.folder_path || '/')}</div>
                    </div>
                    <div class="dup-item-meta">
                        <span>${uploadFormatted}</span>
                        <span>${formatBytes(file.size || 0)}</span>
                    </div>
                    <div class="dup-item-actions">
                        <button class="dup-action-icon-btn" title="Go to folder" onclick="navigateToPath('${escapeHtml(file.folder_path || '/')}')">
                            <svg viewBox="0 0 24 24"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>
                        </button>
                    </div>
                </div>
            `;
        });

        html += `
                </div>
            </div>
        `;
    });

    listContainer.innerHTML = html;
}

// --- Selection & Batch Operations ---

window.onDuplicateItemCheckboxChange = function(fileUuid, isChecked, groupIdx) {
    if (isChecked) {
        window.DUPLICATES_STATE.selectedUuids.add(fileUuid);
    } else {
        window.DUPLICATES_STATE.selectedUuids.delete(fileUuid);
    }
    updateBatchActionBar();
};

window.selectAllDuplicatesKeepOriginals = function() {
    window.DUPLICATES_STATE.selectedUuids.clear();
    const groups = window.DUPLICATES_STATE.groups || [];
    groups.forEach((group) => {
        (group.files || []).forEach((file) => {
            if (!file.is_retained) {
                window.DUPLICATES_STATE.selectedUuids.add(file.file_uuid);
            }
        });
    });
    renderDuplicateGroups();
    updateBatchActionBar();
};

window.deselectAllDuplicates = function() {
    window.DUPLICATES_STATE.selectedUuids.clear();
    renderDuplicateGroups();
    updateBatchActionBar();
};

function updateBatchActionBar() {
    const countLabel = document.getElementById('dup-selected-count-label');
    const deleteBtn = document.getElementById('dup-delete-btn');
    const permBtn = document.getElementById('dup-perm-delete-btn');
    const count = window.DUPLICATES_STATE.selectedUuids.size;

    // Calculate selected bytes
    let selectedBytes = 0;
    const groups = window.DUPLICATES_STATE.groups || [];
    groups.forEach(g => {
        (g.files || []).forEach(f => {
            if (window.DUPLICATES_STATE.selectedUuids.has(f.file_uuid)) {
                selectedBytes += (f.size || 0);
            }
        });
    });

    if (countLabel) {
        countLabel.textContent = `${count} duplicate${count === 1 ? '' : 's'} selected (${formatBytes(selectedBytes)} to recover)`;
    }

    const hasSelection = count > 0;
    if (deleteBtn) deleteBtn.disabled = !hasSelection;
    if (permBtn) permBtn.disabled = !hasSelection;
}

// --- Category & Filter Handlers ---

window.filterDuplicateCategory = function(cat, btn) {
    window.DUPLICATES_STATE.category = cat;
    const chips = document.querySelectorAll('.dup-chip');
    chips.forEach(c => c.classList.remove('active'));
    if (btn) btn.classList.add('active');
    loadDuplicates();
};

window.onDuplicateSortChange = function(sortValue) {
    window.DUPLICATES_STATE.sortBy = sortValue;
    loadDuplicates();
};

// --- Deletion & Safety Invariant Modal ---

window.confirmDeleteDuplicates = function(permanent = false) {
    const selectedUuids = Array.from(window.DUPLICATES_STATE.selectedUuids);
    if (!selectedUuids.length) return;

    let selectedFiles = [];
    let selectedBytes = 0;
    const groups = window.DUPLICATES_STATE.groups || [];
    groups.forEach(g => {
        (g.files || []).forEach(f => {
            if (window.DUPLICATES_STATE.selectedUuids.has(f.file_uuid)) {
                selectedFiles.push(f);
                selectedBytes += (f.size || 0);
            }
        });
    });

    const actionTitle = permanent ? 'Permanently Delete Duplicates' : 'Move Duplicates to Trash';
    const actionWarning = permanent 
        ? 'These duplicate files will be permanently erased from your Telegram cloud storage.'
        : 'These duplicate files will be moved to Trash. You can restore them if needed.';

    const modalHtml = `
        <div class="gd-modal active" id="dup-delete-modal" style="max-width: 500px;">
            <div class="gd-modal-title">🗑️ ${actionTitle}</div>
            <div class="gd-modal-body">
                <p style="font-size: 14px; margin-bottom: 12px;">${actionWarning}</p>
                <div style="background: rgba(52, 168, 83, 0.08); border-left: 4px solid #34a853; padding: 10px 14px; border-radius: 4px; font-size: 13px; margin-bottom: 16px;">
                    🛡️ <strong>Safety Guarantee:</strong> Original copies will remain completely untouched.
                </div>
                <div style="font-size: 13px; color: #5f6368; margin-bottom: 8px;">
                    <strong>Selected files:</strong> ${selectedFiles.length} (${formatBytes(selectedBytes)} freed)
                </div>
                <div style="max-height: 140px; overflow-y: auto; background: #f8f9fa; border: 1px solid #dadce0; border-radius: 6px; padding: 8px 12px; font-size: 12px; font-family: monospace;">
                    ${selectedFiles.map(f => `<div>• ${escapeHtml(f.display_path || f.folder_path + '/' + f.filename)}</div>`).join('')}
                </div>
            </div>
            <div class="gd-modal-footer">
                <button class="gd-secondary-btn" onclick="closeDuplicateDeleteModal()">Cancel</button>
                <button class="gd-primary-btn ${permanent ? 'gd-danger-btn' : ''}" id="dup-execute-delete-btn" onclick="executeDuplicateDelete(${permanent})">
                    ${permanent ? 'Permanently Delete' : 'Move to Trash'}
                </button>
            </div>
        </div>
    `;

    // Remove any existing modal
    const existing = document.getElementById('dup-delete-modal');
    if (existing) existing.remove();

    const overlay = document.getElementById('bg-blur') || document.getElementById('modal-overlay');
    if (overlay) {
        overlay.style.zIndex = '100';
        overlay.style.opacity = '1';
    }

    document.body.insertAdjacentHTML('beforeend', modalHtml);
};

window.closeDuplicateDeleteModal = function() {
    const modal = document.getElementById('dup-delete-modal');
    if (modal) modal.remove();
    const overlay = document.getElementById('bg-blur') || document.getElementById('modal-overlay');
    if (overlay) {
        overlay.style.opacity = '0';
        setTimeout(() => { overlay.style.zIndex = '-1'; }, 200);
    }
};

window.executeDuplicateDelete = async function(permanent = false) {
    const btn = document.getElementById('dup-execute-delete-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Processing...';
    }

    const targetUuids = Array.from(window.DUPLICATES_STATE.selectedUuids);

    try {
        const resp = await fetch('/api/duplicates/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target_uuids: targetUuids,
                permanent: permanent
            })
        });

        const data = await resp.json();
        closeDuplicateDeleteModal();

        if (typeof showToast === 'function') {
            const msg = permanent
                ? `Permanently deleted ${data.deleted_count} duplicate files (${formatBytes(data.freed_bytes)} freed)`
                : `Moved ${data.deleted_count} duplicate files to Trash (${formatBytes(data.freed_bytes)} recoverable)`;
            showToast(msg, 'success');
        }

        // Refresh duplicates view and update storage breakdown
        loadDuplicates();
        if (typeof getStorageDetails === 'function') {
            getStorageDetails();
        }
    } catch (e) {
        console.error('Duplicate deletion error:', e);
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Error - Retry';
        }
        if (typeof showToast === 'function') {
            showToast(`Deletion failed: ${e.message || 'Unknown error'}`, 'error');
        }
    }
};

// --- Helper Utilities ---

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatFileDate(dateStr) {
    if (!dateStr) return '--';
    try {
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return dateStr;
        return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
        return dateStr;
    }
}

function getFileIconEmoji(filename) {
    if (!filename) return '📄';
    const ext = filename.split('.').pop().toLowerCase();
    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'].includes(ext)) return '🖼️';
    if (['mp4', 'mkv', 'avi', 'mov', 'webm'].includes(ext)) return '🎥';
    if (['mp3', 'flac', 'wav', 'aac', 'ogg', 'm4a'].includes(ext)) return '🎵';
    if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '📦';
    if (['pdf'].includes(ext)) return '📄';
    if (['js', 'py', 'ts', 'html', 'css', 'json', 'txt', 'md'].includes(ext)) return '💻';
    return '📄';
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
