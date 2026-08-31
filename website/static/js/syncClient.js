/**
 * SyncClient — Browser ↔ Server Synchronization
 * ================================================
 * Maintains a WebSocket connection to the server for real-time push
 * notifications. Falls back to polling /api/sync/status when WebSocket
 * is unavailable.
 *
 * Usage:
 *   SyncClient.init()          — called once on page load
 *   SyncClient.isEnabled()     — false until authenticated
 *   SyncClient.getCurrentVersion() — last-known server version
 *   SyncClient.forceRefresh()  — manually pull missed changes
 *
 * The client stores the last-known sync version in localStorage so it
 * survives page reloads and can catch up on missed events after being
 * offline.
 */
(function () {
    'use strict';

    // ─── State ───────────────────────────────────────────────────────
    let _ws = null;                // WebSocket instance
    let _version = 0;              // Last-known server sync version
    let _status = 'idle';          // 'idle' | 'connected' | 'polling' | 'offline' | 'error'
    let _pollTimer = null;         // setInterval handle for polling fallback
    let _reconnectTimer = null;    // setTimeout handle for reconnect
    let _initialized = false;
    let _enabled = false;          // Set to true after successful auth
    let _isPageVisible = true;     // Track document visibility

    const STORAGE_KEY = 'tg_drive_sync_version';
    const WS_RECONNECT_DELAY = 3000;
    const WS_MAX_RECONNECT_DELAY = 30000;
    const POLL_INTERVAL = 8000;    // 8 seconds
    const POLL_SHORT_INTERVAL = 3000; // 3 seconds when catching up

    // ─── Initialization ──────────────────────────────────────────────

    function init() {
        if (_initialized) return;
        _initialized = true;

        // Restore last-known version from localStorage
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved) _version = parseInt(saved, 10) || 0;
        } catch (e) { /* ignore */ }

        // Track page visibility for pause/resume
        document.addEventListener('visibilitychange', () => {
            _isPageVisible = !document.hidden;
            if (_isPageVisible && _enabled) {
                _onPageVisible();
            } else if (!_isPageVisible) {
                _onPageHidden();
            }
        });

        // Track online/offline
        window.addEventListener('online', () => {
            if (_enabled) {
                _updateStatus('idle');
                _connectWebSocket();
            }
        });
        window.addEventListener('offline', () => {
            _updateStatus('offline');
            _disconnectWebSocket();
            _stopPolling();
        });

        console.log('[SyncClient] Initialized (version:', _version, ')');
    }

    function enable() {
        if (_enabled) return;
        _enabled = true;

        // Fetch current version from server
        _fetchVersion().then(serverVersion => {
            if (serverVersion > _version) {
                // Missed changes while we were away — fetch them
                console.log('[SyncClient] Catching up from', _version, 'to', serverVersion);
                _fetchChangesSince(_version);
            }
        });

        // Start connection
        if (navigator.onLine) {
            _connectWebSocket();
        }
    }

    function disable() {
        _enabled = false;
        _disconnectWebSocket();
        _stopPolling();
        _updateStatus('idle');
    }

    // ─── WebSocket Connection ────────────────────────────────────────

    function _connectWebSocket() {
        if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) {
            return; // Already connected or connecting
        }

        try {
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${proto}//${location.host}/api/sync/ws`;
            _ws = new WebSocket(url);

            _ws.onopen = () => {
                console.log('[SyncClient] WebSocket connected');
                _updateStatus('connected');
                _stopPolling(); // WebSocket takes over from polling
                _sendPing();
            };

            _ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    _handleMessage(msg);
                } catch (e) {
                    console.warn('[SyncClient] Bad WS message:', e);
                }
            };

            _ws.onclose = (event) => {
                console.log('[SyncClient] WebSocket closed:', event.code, event.reason);
                _ws = null;
                if (_enabled && navigator.onLine) {
                    _scheduleReconnect();
                }
            };

            _ws.onerror = (event) => {
                console.warn('[SyncClient] WebSocket error');
                // onclose will fire after this, triggering reconnect
            };
        } catch (e) {
            console.warn('[SyncClient] WebSocket creation failed:', e);
            _scheduleReconnect();
        }
    }

    function _disconnectWebSocket() {
        if (_ws) {
            try { _ws.close(); } catch (e) { /* ignore */ }
            _ws = null;
        }
    }

    let _reconnectDelay = WS_RECONNECT_DELAY;

    function _scheduleReconnect() {
        if (_reconnectTimer) return;
        _updateStatus('polling');
        _startPolling(); // Fall back to polling immediately

        _reconnectTimer = setTimeout(() => {
            _reconnectTimer = null;
            if (_enabled && navigator.onLine) {
                _connectWebSocket();
            }
        }, _reconnectDelay);

        // Exponential backoff (cap at 30s)
        _reconnectDelay = Math.min(_reconnectDelay * 1.5, WS_MAX_RECONNECT_DELAY);
    }

    function _sendPing() {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
            try { _ws.send(JSON.stringify({ type: 'ping' })); } catch (e) { /* ignore */ }
        }
    }

    // ─── Message Handling ────────────────────────────────────────────

    function _handleMessage(msg) {
        if (!msg || !msg.type) return;

        switch (msg.type) {
            case 'pong':
                // Server keepalive response — connection is alive
                break;

            case 'sync_event':
                _handleSyncEvent(msg);
                break;

            case 'version_check':
                if (msg.has_changes) {
                    _fetchChangesSince(_version);
                }
                break;

            default:
                // Unknown message types are silently ignored
                break;
        }
    }

    function _handleSyncEvent(event) {
        const { operation, entity_id, entity_type, version, user_id } = event;
        if (!operation || !entity_id) return;

        console.log('[SyncClient] Sync event:', operation, entity_type, entity_id, 'v' + version);

        // Update stored version
        if (version > _version) {
            _version = version;
            _saveVersion();
        }

        // Determine if this event affects the current view
        _processEvent(operation, entity_id, entity_type);
    }

    // ─── Event Processing ────────────────────────────────────────────

    function _processEvent(operation, entityId, entityType) {
        // Always refresh if the operation is a create/delete in the current folder
        // For rename/move, refresh to show updated names or moved items
        // For trash, remove the item from view or refresh

        const currentPath = _getCurrentPath();
        const directoryItems = window.DIRECTORY_ITEMS || {};

        switch (operation) {
            case 'FILE_CREATED':
            case 'FOLDER_CREATED':
                // New item appeared — check if it's in the current folder
                _refreshCurrentView();
                break;

            case 'FILE_RENAMED':
            case 'FOLDER_RENAMED':
                // Name changed — update if visible
                if (directoryItems[entityId]) {
                    _refreshCurrentView();
                }
                break;

            case 'FILE_MOVED':
            case 'FOLDER_MOVED':
                // Moved between folders — refresh current view (item may appear/disappear)
                _refreshCurrentView();
                break;

            case 'FILE_TRASHED':
            case 'FOLDER_TRASHED':
                // Trashed — remove from current view if visible
                if (directoryItems[entityId]) {
                    delete directoryItems[entityId];
                    _reRenderCurrentView();
                }
                break;

            case 'FILE_RESTORED':
            case 'FOLDER_RESTORED':
                // Restored — refresh to show it
                _refreshCurrentView();
                break;

            case 'FILE_DELETED':
            case 'FOLDER_DELETED':
                // Permanently deleted — remove from view
                if (directoryItems[entityId]) {
                    delete directoryItems[entityId];
                    _reRenderCurrentView();
                }
                break;

            default:
                // Unknown operation — refresh to be safe
                _refreshCurrentView();
                break;
        }
    }

    function _refreshCurrentView() {
        if (typeof getCurrentDirectory === 'function') {
            getCurrentDirectory();
        }
    }

    function _reRenderCurrentView() {
        // Re-render from the modified DIRECTORY_ITEMS without a full server fetch
        // This is faster for simple removals (trash/delete)
        if (typeof showDirectory === 'function' && window.CURRENT_DIRECTORY_DATA) {
            window.CURRENT_DIRECTORY_DATA.contents = window.DIRECTORY_ITEMS;
            showDirectory(window.CURRENT_DIRECTORY_DATA, window.CURRENT_BREADCRUMBS);
        } else {
            _refreshCurrentView();
        }
    }

    // ─── Polling Fallback ────────────────────────────────────────────

    function _startPolling() {
        if (_pollTimer) return;
        _pollTimer = setInterval(_pollForChanges, POLL_INTERVAL);
    }

    function _stopPolling() {
        if (_pollTimer) {
            clearInterval(_pollTimer);
            _pollTimer = null;
        }
    }

    async function _pollForChanges() {
        if (!_enabled || !_isPageVisible) return;

        try {
            const serverVersion = await _fetchVersion();
            if (serverVersion > _version) {
                await _fetchChangesSince(_version);
            }
        } catch (e) {
            console.warn('[SyncClient] Poll error:', e);
        }
    }

    // ─── API Helpers ─────────────────────────────────────────────────

    async function _fetchVersion() {
        try {
            const resp = await fetch('/api/sync/status', {
                method: 'GET',
                credentials: 'same-origin',
            });
            if (resp.status === 401) {
                _enabled = false;
                return _version;
            }
            if (!resp.ok) return _version;
            const data = await resp.json();
            return data.version || _version;
        } catch (e) {
            return _version;
        }
    }

    async function _fetchChangesSince(sinceVersion) {
        try {
            const resp = await fetch(`/api/sync/changes?since=${sinceVersion}&limit=500`, {
                method: 'GET',
                credentials: 'same-origin',
            });
            if (resp.status === 401 || !resp.ok) return;
            const data = await resp.json();
            if (!data.changes || data.changes.length === 0) return;

            console.log('[SyncClient] Received', data.changes.length, 'changes (since v' + sinceVersion + ')');

            // Process each change
            for (const change of data.changes) {
                _processEvent(change.operation, change.entity_id, change.entity_type);
            }

            // Update version
            if (data.current_version > _version) {
                _version = data.current_version;
                _saveVersion();
            }
        } catch (e) {
            console.warn('[SyncClient] Fetch changes error:', e);
        }
    }

    // ─── Page Visibility Handlers ────────────────────────────────────

    function _onPageVisible() {
        // Page became visible — check for missed changes
        _fetchVersion().then(serverVersion => {
            if (serverVersion > _version) {
                console.log('[SyncClient] Page visible — catching up from', _version, 'to', serverVersion);
                _fetchChangesSince(_version);
            }
        });

        // Reconnect WebSocket if disconnected
        if (!_ws || _ws.readyState !== WebSocket.OPEN) {
            _connectWebSocket();
        }

        // Resume polling
        if (!_ws || _ws.readyState !== WebSocket.OPEN) {
            _startPolling();
        }
    }

    function _onPageHidden() {
        // Reduce activity when page is not visible
        _stopPolling();
    }

    // ─── Utilities ───────────────────────────────────────────────────

    function _getCurrentPath() {
        try {
            const url = new URL(window.location.href);
            let path = url.searchParams.get('path');
            return path || '/';
        } catch {
            return '/';
        }
    }

    function _saveVersion() {
        try {
            localStorage.setItem(STORAGE_KEY, String(_version));
        } catch (e) { /* ignore */ }
    }

    function _updateStatus(newStatus) {
        const oldStatus = _status;
        _status = newStatus;

        // Reset reconnect delay on successful connection
        if (newStatus === 'connected') {
            _reconnectDelay = WS_RECONNECT_DELAY;
        }

        // Update DOM indicator
        _renderIndicator(newStatus);

        // Dispatch custom event for other modules
        if (oldStatus !== newStatus) {
            try {
                window.dispatchEvent(new CustomEvent('syncstatuschange', {
                    detail: { status: newStatus, version: _version }
                }));
            } catch (e) { /* ignore */ }
        }
    }

    function _renderIndicator(status) {
        const indicator = document.getElementById('sync-indicator');
        if (!indicator) return;

        const dot = indicator.querySelector('.sync-dot');
        const label = indicator.querySelector('.sync-label');
        if (!dot || !label) return;

        switch (status) {
            case 'connected':
                dot.className = 'sync-dot sync-dot-synced';
                label.textContent = 'Synced';
                indicator.title = `Connected via WebSocket (v${_version})`;
                break;
            case 'polling':
                dot.className = 'sync-dot sync-dot-polling';
                label.textContent = 'Syncing...';
                indicator.title = `Polling for changes (v${_version})`;
                break;
            case 'offline':
                dot.className = 'sync-dot sync-dot-offline';
                label.textContent = 'Offline';
                indicator.title = 'No network connection';
                break;
            case 'error':
                dot.className = 'sync-dot sync-dot-error';
                label.textContent = 'Sync failed';
                indicator.title = 'Sync error — retrying...';
                break;
            default: // 'idle'
                dot.className = 'sync-dot sync-dot-idle';
                label.textContent = '';
                indicator.title = 'Sync idle';
                break;
        }
    }

    // ─── Force Refresh (manual) ──────────────────────────────────────

    async function forceRefresh() {
        _updateStatus('polling');
        try {
            const serverVersion = await _fetchVersion();
            await _fetchChangesSince(_version);
            _version = serverVersion;
            _saveVersion();
            _updateStatus('connected');
        } catch (e) {
            _updateStatus('error');
        }
    }

    // ─── Public API ──────────────────────────────────────────────────

    window.SyncClient = {
        init: init,
        enable: enable,
        disable: disable,
        isEnabled: () => _enabled,
        getCurrentVersion: () => _version,
        getStatus: () => _status,
        forceRefresh: forceRefresh,
    };
})();
