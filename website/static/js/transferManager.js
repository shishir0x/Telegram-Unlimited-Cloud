// ===========================================================================
// Production-Grade Transfer Manager Client Controller
// Telegram-Unlimited-Cloud (Google Drive-Grade Transfer Dock & Drawer)
// ===========================================================================

window.TRANSFER_MANAGER = (function () {
    let _pollInterval = null;
    let _isPolling = false;
    let _activeTab = 'all'; // 'all', 'upload', 'download', 'active'
    let _lastData = { transfers: [], stats: {} };
    let _isExpanded = false;
    let _hasUserClosed = false;

    function init() {
        bindEvents();
        startPolling();
    }

    function bindEvents() {
        const dockPill = document.getElementById('transfer-dock-pill');
        const drawer = document.getElementById('transfer-manager-drawer');
        const minBtn = document.getElementById('tm-minimize-btn');
        const closeBtn = document.getElementById('tm-close-btn');
        const clearBtn = document.getElementById('tm-clear-btn');

        if (dockPill) {
            dockPill.addEventListener('click', () => {
                _isExpanded = !_isExpanded;
                _hasUserClosed = false;
                renderDrawerVisibility();
            });
        }

        if (minBtn) {
            minBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                _isExpanded = false;
                renderDrawerVisibility();
            });
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                _isExpanded = false;
                _hasUserClosed = true;
                renderDrawerVisibility();
            });
        }

        if (clearBtn) {
            clearBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await clearFinished();
            });
        }

        // Tab switches
        document.querySelectorAll('.gd-tm-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.gd-tm-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                _activeTab = tab.getAttribute('data-tab') || 'all';
                renderItems();
            });
        });
    }

    function renderDrawerVisibility() {
        const dockPill = document.getElementById('transfer-dock-pill');
        const drawer = document.getElementById('transfer-manager-drawer');
        const legacyCard = document.getElementById('file-uploader');

        const activeCount = (_lastData.stats && _lastData.stats.total_active) || 0;
        const totalCount = (_lastData.transfers && _lastData.transfers.length) || 0;

        // Auto-show dock if there are active transfers or recent items
        if (totalCount > 0 && !_hasUserClosed) {
            if (dockPill) dockPill.style.display = 'flex';
        } else if (activeCount === 0 && totalCount === 0) {
            if (dockPill) dockPill.style.display = 'none';
            if (drawer) drawer.classList.remove('active');
            _isExpanded = false;
            return;
        }

        if (drawer) {
            if (_isExpanded) {
                drawer.classList.add('active');
                if (dockPill) dockPill.classList.add('is-open');
            } else {
                drawer.classList.remove('active');
                if (dockPill) dockPill.classList.remove('is-open');
            }
        }

        // Keep legacy card hidden to prevent UI collision
        if (legacyCard) legacyCard.style.display = 'none';
    }

    async function fetchTransfers() {
        if (typeof postJson !== 'function') return;
        try {
            const res = await postJson('/api/getTransfers', {});
            if (res && res.status === 'ok') {
                _lastData = res;
                updateUI(res);
            }
        } catch (err) {
            console.debug('[TransferManager] Polling error:', err);
        }
    }

    function startPolling() {
        if (_isPolling) return;
        _isPolling = true;
        fetchTransfers();

        _pollInterval = setInterval(async () => {
            await fetchTransfers();
        }, 1200);
    }

    function updateUI(data) {
        const transfers = data.transfers || [];
        const stats = data.stats || {};
        const activeCount = stats.total_active || 0;

        // Auto expand drawer on new active transfer if user hasn't explicitly closed it
        if (activeCount > 0 && !_isExpanded && !_hasUserClosed) {
            _isExpanded = true;
        }

        renderDock(stats, transfers);
        renderHeader(stats, transfers);
        renderBatchProgress(transfers);
        renderItems();
        renderDrawerVisibility();
    }

    function renderDock(stats, transfers) {
        const dockPill = document.getElementById('transfer-dock-pill');
        const dockLabel = document.getElementById('dock-status-label');
        const dockSpeed = document.getElementById('dock-speed-badge');
        const dockSpinner = document.getElementById('dock-spinner-icon');

        if (!dockPill) return;

        const activeCount = stats.total_active || 0;
        const upSpeed = stats.upload_speed_formatted || '0 B/s';
        const dlSpeed = stats.download_speed_formatted || '0 B/s';

        if (activeCount > 0) {
            dockPill.classList.add('has-active');
            if (dockSpinner) dockSpinner.classList.add('spinning');
            if (dockLabel) {
                dockLabel.innerText = `${activeCount} transfer${activeCount === 1 ? '' : 's'} in progress`;
            }
            if (dockSpeed) {
                const parts = [];
                if (stats.upload_speed > 0) parts.push(`▲ ${upSpeed}`);
                if (stats.download_speed > 0) parts.push(`▼ ${dlSpeed}`);
                dockSpeed.innerText = parts.length ? parts.join(' ') : 'Transferring...';
                dockSpeed.style.display = 'inline-block';
            }
        } else {
            dockPill.classList.remove('has-active');
            if (dockSpinner) dockSpinner.classList.remove('spinning');
            const totalCount = transfers.length;
            if (dockLabel) {
                dockLabel.innerText = totalCount > 0 ? `${totalCount} transfer${totalCount === 1 ? '' : 's'} complete` : 'No transfers';
            }
            if (dockSpeed) dockSpeed.style.display = 'none';
        }
    }

    function renderHeader(stats, transfers) {
        const countBadge = document.getElementById('tm-header-count');
        const tabAll = document.getElementById('tm-tab-count-all');
        const tabUpload = document.getElementById('tm-tab-count-upload');
        const tabDownload = document.getElementById('tm-tab-count-download');
        const tabActive = document.getElementById('tm-tab-count-active');

        const activeCount = stats.total_active || 0;
        if (countBadge) countBadge.innerText = activeCount > 0 ? `${activeCount}` : `${transfers.length}`;

        const uploadCount = transfers.filter(t => t.type === 'upload').length;
        const downloadCount = transfers.filter(t => t.type === 'download').length;

        if (tabAll) tabAll.innerText = transfers.length;
        if (tabUpload) tabUpload.innerText = uploadCount;
        if (tabDownload) tabDownload.innerText = downloadCount;
        if (tabActive) tabActive.innerText = activeCount;
    }

    function renderBatchProgress(transfers) {
        const batchSection = document.getElementById('tm-batch-section');
        const batchFill = document.getElementById('tm-batch-fill');
        const batchText = document.getElementById('tm-batch-text');
        const batchSpeed = document.getElementById('tm-batch-speed');

        const activeItems = transfers.filter(t => ['queued', 'preparing', 'uploading', 'downloading', 'retrying'].includes(t.state));

        if (activeItems.length === 0) {
            if (batchSection) batchSection.style.display = 'none';
            return;
        }

        if (batchSection) batchSection.style.display = 'block';

        let totalBytes = 0;
        let transferredBytes = 0;
        let totalSpeed = 0;

        activeItems.forEach(item => {
            totalBytes += item.size || 0;
            transferredBytes += item.transferred || 0;
            totalSpeed += item.speed || 0;
        });

        const overallPercent = totalBytes > 0 ? Math.min(100, Math.round((transferredBytes / totalBytes) * 100)) : 0;

        if (batchFill) batchFill.style.width = `${overallPercent}%`;
        if (batchText) batchText.innerText = `${activeItems.length} active (${overallPercent}%)`;
        if (batchSpeed) batchSpeed.innerText = totalSpeed > 0 ? `${formatBytes(totalSpeed)}/s` : '';
    }

    function renderItems() {
        const container = document.getElementById('tm-items-container');
        if (!container) return;

        let list = _lastData.transfers || [];

        if (_activeTab === 'upload') {
            list = list.filter(t => t.type === 'upload');
        } else if (_activeTab === 'download') {
            list = list.filter(t => t.type === 'download');
        } else if (_activeTab === 'active') {
            list = list.filter(t => ['queued', 'preparing', 'uploading', 'downloading', 'retrying'].includes(t.state));
        }

        if (list.length === 0) {
            container.innerHTML = `
                <div class="gd-tm-empty">
                    <div class="gd-tm-empty-icon">📂</div>
                    <div class="gd-tm-empty-title">No transfers in this view</div>
                    <div class="gd-tm-empty-desc">Upload files or download remote links to view progress here.</div>
                </div>
            `;
            return;
        }

        let html = '';
        list.forEach(item => {
            html += buildItemCardHtml(item);
        });

        container.innerHTML = html;
        bindItemActions(container);
    }

    function buildItemCardHtml(item) {
        const isUpload = item.type === 'upload';
        const typeIcon = isUpload ? '▲' : '▼';
        const typeBadgeClass = isUpload ? 'badge-upload' : 'badge-download';
        const stateClass = `state-${item.state}`;
        const stateLabel = getStateBadgeLabel(item);

        const transferredFormatted = formatBytes(item.transferred);
        const totalFormatted = formatBytes(item.size);
        const percent = Math.min(100, Math.max(0, Math.round(item.percentage || 0)));

        const isRunning = ['uploading', 'downloading', 'preparing', 'retrying'].includes(item.state);
        const isFinished = ['completed', 'failed', 'cancelled'].includes(item.state);

        let subMeta = '';
        if (isRunning) {
            const speed = item.speed_formatted || '0 B/s';
            const eta = item.eta_formatted ? ` • ${item.eta_formatted} left` : '';
            subMeta = `${transferredFormatted} of ${totalFormatted} (${percent}%) • ${speed}${eta}`;
        } else if (item.state === 'queued') {
            subMeta = `Queued • ${totalFormatted}`;
        } else if (item.state === 'completed') {
            subMeta = `Completed • ${totalFormatted}`;
        } else if (item.state === 'failed') {
            subMeta = `Failed • ${item.error_reason || 'Unknown error'}`;
        } else if (item.state === 'cancelled') {
            subMeta = `Cancelled • ${transferredFormatted} transferred`;
        }

        const canCancel = ['queued', 'preparing', 'uploading', 'downloading', 'retrying'].includes(item.state);
        const canRetry = ['failed', 'cancelled'].includes(item.state);
        const canRemove = isFinished;
        const canLocate = item.state === 'completed' && item.target_path;

        const targetDisplay = item.relative_path || (item.target_path || '/');

        return `
            <div class="gd-tm-card ${stateClass}" data-id="${escapeHtml(item.id)}">
                <div class="gd-tm-card-header">
                    <div class="gd-tm-file-info">
                        <span class="gd-tm-type-badge ${typeBadgeClass}">${typeIcon}</span>
                        <div class="gd-tm-names">
                            <div class="gd-tm-filename" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</div>
                            <div class="gd-tm-target-path" title="Destination: ${escapeHtml(targetDisplay)}">📁 ${escapeHtml(targetDisplay)}</div>
                        </div>
                    </div>
                    <div class="gd-tm-status-pill ${stateClass}">
                        ${stateLabel}
                    </div>
                </div>

                <div class="gd-tm-progress-track">
                    <div class="gd-tm-progress-bar ${stateClass}" style="width: ${percent}%;"></div>
                </div>

                <div class="gd-tm-card-footer">
                    <div class="gd-tm-submeta ${item.state === 'failed' ? 'error-text' : ''}">
                        ${escapeHtml(subMeta)}
                    </div>
                    <div class="gd-tm-actions">
                        ${canLocate ? `
                            <button class="gd-tm-action-btn locate-btn" data-action="locate" data-path="${escapeHtml(item.target_path)}" title="Open folder">
                                📁
                            </button>
                        ` : ''}
                        ${canRetry ? `
                            <button class="gd-tm-action-btn retry-btn" data-action="retry" data-id="${escapeHtml(item.id)}" title="Retry transfer">
                                ↺ Retry
                            </button>
                        ` : ''}
                        ${canCancel ? `
                            <button class="gd-tm-action-btn cancel-btn" data-action="cancel" data-id="${escapeHtml(item.id)}" title="Cancel transfer">
                                ✕
                            </button>
                        ` : ''}
                        ${canRemove ? `
                            <button class="gd-tm-action-btn remove-btn" data-action="remove" data-id="${escapeHtml(item.id)}" title="Remove from list">
                                🗑️
                            </button>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }

    function getStateBadgeLabel(item) {
        if (item.cooldown_remaining && item.cooldown_remaining > 0) {
            return `⏳ Cooling down (${Math.ceil(item.cooldown_remaining)}s)`;
        }
        switch (item.state) {
            case 'queued':
                return '⏳ Queued';
            case 'preparing':
                return '⚙️ Preparing';
            case 'uploading':
                return `▲ Uploading ${Math.round(item.percentage)}%`;
            case 'downloading':
                return `▼ Downloading ${Math.round(item.percentage)}%`;
            case 'completed':
                return '✓ Done';
            case 'failed':
                return '✕ Failed';
            case 'cancelled':
                return '⊘ Cancelled';
            case 'retrying':
                return `↺ Retrying (${item.retry_count}/${item.max_retries})`;
            default:
                return item.state;
        }
    }

    function bindItemActions(container) {
        container.querySelectorAll('[data-action]').forEach(btn => {
            btn.onclick = async function (e) {
                e.stopPropagation();
                const action = this.getAttribute('data-action');
                const id = this.getAttribute('data-id');
                const path = this.getAttribute('data-path');

                if (action === 'cancel') {
                    await cancelTransfer(id);
                } else if (action === 'retry') {
                    await retryTransfer(id);
                } else if (action === 'remove') {
                    await removeTransfer(id);
                } else if (action === 'locate' && path) {
                    if (typeof navigateToPath === 'function') {
                        navigateToPath(path);
                    }
                }
            };
        });
    }

    async function cancelTransfer(id) {
        if (!id) return;
        try {
            await postJson(`/api/transfers/${encodeURIComponent(id)}/cancel`, {});
            showToast('Transfer cancelled ✕');
            fetchTransfers();
        } catch (e) {
            showToast('Failed to cancel transfer');
        }
    }

    async function retryTransfer(id) {
        if (!id) return;
        try {
            const res = await postJson(`/api/transfers/${encodeURIComponent(id)}/retry`, {});
            if (res && res.status === 'ok') {
                showToast('Transfer re-queued ↺');
                fetchTransfers();
            } else {
                showToast(`Retry error: ${res.status || 'Could not retry'}`);
            }
        } catch (e) {
            showToast('Failed to retry transfer');
        }
    }

    async function removeTransfer(id) {
        if (!id) return;
        try {
            await postJson(`/api/transfers/${encodeURIComponent(id)}/remove`, {});
            fetchTransfers();
        } catch (e) {
            showToast('Failed to remove transfer');
        }
    }

    async function clearFinished() {
        try {
            const res = await postJson('/api/transfers/clear', {});
            if (res && res.status === 'ok') {
                showToast(`Cleared ${res.cleared_count || 0} finished transfers 🧹`);
                fetchTransfers();
            }
        } catch (e) {
            showToast('Failed to clear transfers');
        }
    }

    function formatBytes(bytes) {
        if (!bytes || bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
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

    return {
        init,
        fetchTransfers,
        cancelTransfer,
        retryTransfer,
        removeTransfer,
        clearFinished,
        expand: () => { _isExpanded = true; _hasUserClosed = false; renderDrawerVisibility(); },
        collapse: () => { _isExpanded = false; renderDrawerVisibility(); },
    };
})();

document.addEventListener('DOMContentLoaded', () => {
    window.TRANSFER_MANAGER.init();
});
