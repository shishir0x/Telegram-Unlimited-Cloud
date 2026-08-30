// ===========================================================================
// Archive Manager — Browse, Inspect & Extract ZIP archives
// Telegram-Unlimited-Cloud
// ===========================================================================

window.ARCHIVE_MANAGER = (function () {
    'use strict';

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------
    let _currentFilePath = null;   // drive path of the archive being browsed
    let _manifest = null;          // last inspect response
    let _selectedPaths = new Set();
    let _downloadToken = null;     // short-lived token from last extract response
    let _isOpen = false;

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    /**
     * Open the archive manager for a given drive file path.
     * @param {string} driveFilePath  — virtual drive path, e.g. "/Photos/archive.zip"
     * @param {string} [displayName] — label shown in the modal header
     */
    async function open(driveFilePath, displayName) {
        if (!driveFilePath) return;
        _currentFilePath = driveFilePath;
        _selectedPaths = new Set();
        _downloadToken = null;
        _isOpen = true;

        _showModal(displayName || _basename(driveFilePath));
        _setStatus('Inspecting archive…', 'loading');

        try {
            const resp = await _post('/api/archive/inspect', { file_path: driveFilePath });
            if (resp.detail) throw new Error(resp.detail);
            _manifest = resp;
            _renderManifest(resp);
            _setStatus('', '');
        } catch (err) {
            _setStatus('Failed to inspect archive: ' + err.message, 'error');
        }
    }

    function close() {
        _isOpen = false;
        _manifest = null;
        _selectedPaths = new Set();
        const modal = document.getElementById('archive-mgr-modal');
        if (modal) modal.style.display = 'none';
    }

    // -----------------------------------------------------------------------
    // HTTP helpers
    // -----------------------------------------------------------------------

    async function _post(url, data) {
        const r = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(data),
        });
        if (r.status === 401) { if (window.showLoginModal) showLoginModal(false); throw new Error('Unauthorized'); }
        return r.json();
    }

    async function _get(url) {
        const r = await fetch(url, { credentials: 'same-origin' });
        if (r.status === 401) { if (window.showLoginModal) showLoginModal(false); throw new Error('Unauthorized'); }
        return r.json();
    }

    // -----------------------------------------------------------------------
    // Render manifest / tree
    // -----------------------------------------------------------------------

    function _renderManifest(manifest) {
        const treeEl = document.getElementById('archive-mgr-tree');
        const infoEl = document.getElementById('archive-mgr-info');
        if (!treeEl || !infoEl) return;

        // Info panel
        infoEl.innerHTML = `
            <span class="am-badge">ZIP</span>
            <span class="am-info-item">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                ${manifest.total_files.toLocaleString()} files
            </span>
            <span class="am-info-item">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
                ${manifest.total_dirs.toLocaleString()} folders
            </span>
            <span class="am-info-item">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                ${_fmtBytes(manifest.total_size)} uncompressed
            </span>
            <span class="am-info-item am-ratio">
                ↘ ${_compressionPct(manifest.total_size, manifest.total_compressed_size)} smaller
            </span>`;

        // Tree
        treeEl.innerHTML = '';
        if (!manifest.tree || manifest.tree.length === 0) {
            treeEl.innerHTML = '<p class="am-empty">Archive is empty.</p>';
            return;
        }
        treeEl.appendChild(_buildTreeEl(manifest.tree, 0));
    }

    function _buildTreeEl(entries, depth) {
        const ul = document.createElement('ul');
        ul.className = 'am-tree-list';
        if (depth > 0) ul.classList.add('am-nested');

        entries.forEach(entry => {
            const li = document.createElement('li');
            li.className = 'am-tree-item' + (entry.is_dir ? ' am-dir' : ' am-file');

            const row = document.createElement('div');
            row.className = 'am-row';

            // Expand toggle for dirs
            let toggle = null;
            if (entry.is_dir) {
                toggle = document.createElement('span');
                toggle.className = 'am-toggle';
                toggle.innerHTML = '▶';
                row.appendChild(toggle);
            } else {
                const spacer = document.createElement('span');
                spacer.className = 'am-toggle am-toggle-spacer';
                row.appendChild(spacer);
            }

            // Checkbox
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'am-cb';
            cb.dataset.path = entry.path;
            cb.addEventListener('change', _onCheckboxChange);
            row.appendChild(cb);

            // Icon
            const icon = document.createElement('span');
            icon.className = 'am-icon';
            icon.innerHTML = entry.is_dir ? _iconFolder() : _iconFile(entry.name);
            row.appendChild(icon);

            // Name
            const name = document.createElement('span');
            name.className = 'am-name';
            name.textContent = entry.name;
            row.appendChild(name);

            // Size badge
            if (!entry.is_dir) {
                const size = document.createElement('span');
                size.className = 'am-size';
                size.textContent = _fmtBytes(entry.size);
                row.appendChild(size);
            }

            li.appendChild(row);

            // Children (collapsed by default for dirs)
            if (entry.is_dir && entry.children && entry.children.length > 0) {
                const childEl = _buildTreeEl(entry.children, depth + 1);
                childEl.style.display = 'none';
                li.appendChild(childEl);

                toggle.addEventListener('click', () => {
                    const hidden = childEl.style.display === 'none';
                    childEl.style.display = hidden ? '' : 'none';
                    toggle.innerHTML = hidden ? '▼' : '▶';
                });
                row.addEventListener('dblclick', () => toggle.click());
            }

            ul.appendChild(li);
        });

        return ul;
    }

    function _onCheckboxChange(e) {
        const path = e.target.dataset.path;
        if (e.target.checked) {
            _selectedPaths.add(path);
        } else {
            _selectedPaths.delete(path);
        }
        _updateSelectionLabel();
    }

    function _updateSelectionLabel() {
        const label = document.getElementById('archive-mgr-sel-label');
        if (label) {
            const n = _selectedPaths.size;
            label.textContent = n === 0 ? 'Nothing selected' : `${n} item${n === 1 ? '' : 's'} selected`;
        }
    }

    // -----------------------------------------------------------------------
    // Actions
    // -----------------------------------------------------------------------

    async function _extractSelected() {
        if (_selectedPaths.size === 0) {
            _setStatus('Select at least one file or folder first.', 'warn');
            return;
        }
        await _doExtract([..._selectedPaths]);
    }

    async function _extractAll() {
        await _doExtract(null);
    }

    async function _doExtract(members) {
        _setStatus('Queuing extraction…', 'loading');
        _setActionsDisabled(true);
        try {
            const resp = await _post('/api/archive/extract', {
                file_path: _currentFilePath,
                members: members,
                conflict: 'keep_both',
            });

            if (resp.detail) throw new Error(resp.detail);

            _downloadToken = resp.download_token || null;
            _setStatus(
                `✓ Extraction queued — ${resp.extracted_count} file${resp.extracted_count !== 1 ? 's' : ''} sent to Transfer Manager.`,
                'success'
            );

            // Show transfer dock
            if (window.TRANSFER_MANAGER && typeof TRANSFER_MANAGER.startPolling === 'function') {
                TRANSFER_MANAGER.startPolling();
            }

            // If there are skipped files, show them
            if (resp.skipped && resp.skipped.length > 0) {
                const warn = document.getElementById('archive-mgr-warnings');
                if (warn) {
                    warn.innerHTML = `<b>${resp.skipped.length} item${resp.skipped.length !== 1 ? 's' : ''} skipped:</b><br>` +
                        resp.skipped.slice(0, 10).map(s => `<code>${_esc(s)}</code>`).join('<br>');
                    warn.style.display = '';
                }
            }
        } catch (err) {
            _setStatus('Extraction failed: ' + err.message, 'error');
        } finally {
            _setActionsDisabled(false);
        }
    }

    async function _downloadDirect(memberPath) {
        if (!_downloadToken) {
            _setStatus('No download token available. Extract files first.', 'warn');
            return;
        }
        const url = `/api/archive/download?token=${encodeURIComponent(_downloadToken)}&member=${encodeURIComponent(memberPath)}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = _basename(memberPath);
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    // -----------------------------------------------------------------------
    // Modal creation & helpers
    // -----------------------------------------------------------------------

    function _ensureModal() {
        if (document.getElementById('archive-mgr-modal')) return;

        const modal = document.createElement('div');
        modal.id = 'archive-mgr-modal';
        modal.innerHTML = `
<div class="am-overlay" id="archive-mgr-overlay"></div>
<div class="am-panel" role="dialog" aria-modal="true" aria-labelledby="archive-mgr-title">
  <div class="am-header">
    <div class="am-header-left">
      <span class="am-header-icon">${_iconZip()}</span>
      <span class="am-title" id="archive-mgr-title">Archive</span>
    </div>
    <div class="am-header-actions">
      <button class="am-btn am-btn-ghost" id="archive-mgr-close-btn" title="Close (Esc)">✕</button>
    </div>
  </div>

  <div class="am-info-bar" id="archive-mgr-info"></div>

  <div class="am-body">
    <div class="am-tree-panel">
      <div class="am-tree-toolbar">
        <label class="am-cb-all-label">
          <input type="checkbox" id="archive-mgr-cb-all"> Select all
        </label>
        <span class="am-sel-label" id="archive-mgr-sel-label">Nothing selected</span>
      </div>
      <div class="am-tree-scroll" id="archive-mgr-tree"></div>
    </div>
  </div>

  <div class="am-footer">
    <div class="am-status" id="archive-mgr-status"></div>
    <div class="am-warnings" id="archive-mgr-warnings" style="display:none"></div>
    <div class="am-actions">
      <button class="am-btn am-btn-primary" id="archive-mgr-extract-all-btn">
        ${_iconExtract()} Extract All
      </button>
      <button class="am-btn am-btn-secondary" id="archive-mgr-extract-sel-btn">
        ${_iconExtract()} Extract Selected
      </button>
      <button class="am-btn am-btn-ghost" id="archive-mgr-cancel-btn">Cancel</button>
    </div>
  </div>
</div>`;

        document.body.appendChild(modal);

        // Bindings
        document.getElementById('archive-mgr-close-btn').addEventListener('click', close);
        document.getElementById('archive-mgr-cancel-btn').addEventListener('click', close);
        document.getElementById('archive-mgr-overlay').addEventListener('click', close);
        document.getElementById('archive-mgr-extract-all-btn').addEventListener('click', _extractAll);
        document.getElementById('archive-mgr-extract-sel-btn').addEventListener('click', _extractSelected);
        document.getElementById('archive-mgr-cb-all').addEventListener('change', e => {
            document.querySelectorAll('#archive-mgr-tree .am-cb').forEach(cb => {
                cb.checked = e.target.checked;
                const path = cb.dataset.path;
                if (e.target.checked) _selectedPaths.add(path);
                else _selectedPaths.delete(path);
            });
            _updateSelectionLabel();
        });

        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && _isOpen) close();
        });
    }

    function _showModal(title) {
        _ensureModal();
        const modal = document.getElementById('archive-mgr-modal');
        const titleEl = document.getElementById('archive-mgr-title');
        const treeEl = document.getElementById('archive-mgr-tree');
        const infoEl = document.getElementById('archive-mgr-info');
        const warn = document.getElementById('archive-mgr-warnings');

        if (titleEl) titleEl.textContent = title;
        if (treeEl) treeEl.innerHTML = '';
        if (infoEl) infoEl.innerHTML = '';
        if (warn) { warn.innerHTML = ''; warn.style.display = 'none'; }

        _setStatus('', '');
        _setActionsDisabled(false);
        _selectedPaths = new Set();
        _updateSelectionLabel();

        modal.style.display = 'flex';
    }

    function _setStatus(msg, type) {
        const el = document.getElementById('archive-mgr-status');
        if (!el) return;
        el.textContent = msg;
        el.className = 'am-status am-status-' + (type || '');
    }

    function _setActionsDisabled(disabled) {
        ['archive-mgr-extract-all-btn', 'archive-mgr-extract-sel-btn'].forEach(id => {
            const btn = document.getElementById(id);
            if (btn) btn.disabled = disabled;
        });
    }

    // -----------------------------------------------------------------------
    // Utility
    // -----------------------------------------------------------------------

    function _basename(p) {
        return (p || '').split('/').filter(Boolean).pop() || p;
    }

    function _fmtBytes(n) {
        if (!n || n < 0) return '0 B';
        if (n < 1024) return n + ' B';
        if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
        if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
        return (n / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    }

    function _compressionPct(raw, compressed) {
        if (!raw || !compressed || compressed >= raw) return '0%';
        return ((1 - compressed / raw) * 100).toFixed(1) + '%';
    }

    function _esc(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function _iconZip() {
        return `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><line x1="9" y1="15" x2="15" y2="15"/></svg>`;
    }

    function _iconFolder() {
        return `<svg width="16" height="16" viewBox="0 0 24 24" fill="#f6a623" stroke="#e89515" stroke-width="1.5"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>`;
    }

    function _iconFile(name) {
        const ext = (name || '').split('.').pop().toLowerCase();
        const colors = {
            jpg: '#4CAF50', jpeg: '#4CAF50', png: '#4CAF50', gif: '#4CAF50', webp: '#4CAF50',
            mp4: '#9C27B0', mov: '#9C27B0', avi: '#9C27B0', mkv: '#9C27B0',
            mp3: '#FF9800', wav: '#FF9800', flac: '#FF9800',
            pdf: '#F44336', doc: '#2196F3', docx: '#2196F3', xls: '#4CAF50', xlsx: '#4CAF50',
            zip: '#795548', tar: '#795548', gz: '#795548',
        };
        const color = colors[ext] || '#90A4AE';
        return `<svg width="16" height="16" viewBox="0 0 24 24" fill="${color}" fill-opacity="0.2" stroke="${color}" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
    }

    function _iconExtract() {
        return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="8 17 12 21 16 17"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.88 18.09A5 5 0 0018 9h-1.26A8 8 0 103 16.29"/></svg>`;
    }

    // -----------------------------------------------------------------------
    // CSS injection (self-contained, no external dependencies)
    // -----------------------------------------------------------------------

    function _injectStyles() {
        if (document.getElementById('archive-mgr-styles')) return;
        const style = document.createElement('style');
        style.id = 'archive-mgr-styles';
        style.textContent = `
/* ===== Archive Manager Modal ===== */
#archive-mgr-modal {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 9000;
    align-items: center;
    justify-content: center;
}
.am-overlay {
    position: absolute;
    inset: 0;
    background: rgba(0,0,0,0.65);
    backdrop-filter: blur(6px);
}
.am-panel {
    position: relative;
    z-index: 1;
    width: min(860px, 96vw);
    max-height: min(700px, 92vh);
    background: #181c24;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 16px;
    box-shadow: 0 32px 80px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.05) inset;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    animation: am-slide-in 0.22s cubic-bezier(.4,0,.2,1);
}
@keyframes am-slide-in {
    from { opacity: 0; transform: translateY(16px) scale(0.97); }
    to   { opacity: 1; transform: none; }
}
/* Header */
.am-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    flex-shrink: 0;
}
.am-header-left { display: flex; align-items: center; gap: 10px; }
.am-header-icon { color: #7c8cf8; }
.am-title {
    font-size: 15px;
    font-weight: 600;
    color: #e8eaf6;
    letter-spacing: 0.01em;
    max-width: 480px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
/* Info bar */
.am-info-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 20px;
    background: rgba(255,255,255,0.03);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    flex-shrink: 0;
    flex-wrap: wrap;
    font-size: 12px;
    color: #9098b0;
    min-height: 36px;
}
.am-badge {
    background: #7c8cf8;
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    letter-spacing: 0.05em;
}
.am-info-item {
    display: flex;
    align-items: center;
    gap: 5px;
}
.am-info-item svg { width: 13px; height: 13px; opacity: 0.6; }
.am-ratio { color: #81d4fa; }
/* Body / tree */
.am-body { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.am-tree-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.am-tree-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 20px 6px;
    font-size: 12px;
    color: #9098b0;
    flex-shrink: 0;
}
.am-cb-all-label {
    display: flex;
    align-items: center;
    gap: 7px;
    cursor: pointer;
    user-select: none;
}
.am-sel-label { color: #7c8cf8; font-weight: 500; }
.am-tree-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 4px 8px 8px;
    scrollbar-width: thin;
    scrollbar-color: rgba(124,140,248,.3) transparent;
}
/* Tree */
.am-tree-list { list-style: none; margin: 0; padding: 0; }
.am-tree-list.am-nested { padding-left: 22px; }
.am-tree-item { }
.am-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border-radius: 7px;
    cursor: default;
    transition: background 0.12s;
    font-size: 13px;
}
.am-row:hover { background: rgba(255,255,255,0.05); }
.am-toggle {
    width: 14px;
    font-size: 9px;
    color: #6670a0;
    flex-shrink: 0;
    cursor: pointer;
    transition: transform 0.15s;
    user-select: none;
}
.am-toggle-spacer { cursor: default; }
.am-cb { accent-color: #7c8cf8; flex-shrink: 0; cursor: pointer; }
.am-icon { flex-shrink: 0; display: flex; align-items: center; }
.am-name { flex: 1; color: #d0d6f0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.am-dir > .am-row > .am-name { color: #e8eaf6; font-weight: 500; }
.am-size { font-size: 11px; color: #6670a0; flex-shrink: 0; }
.am-empty { color: #6670a0; font-size: 13px; padding: 24px 20px; text-align: center; }
/* Footer */
.am-footer {
    flex-shrink: 0;
    border-top: 1px solid rgba(255,255,255,0.07);
    padding: 10px 20px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.am-status { font-size: 12px; color: #9098b0; min-height: 16px; }
.am-status-loading { color: #7c8cf8; }
.am-status-success { color: #66bb6a; }
.am-status-error   { color: #ef5350; }
.am-status-warn    { color: #ffa726; }
.am-warnings {
    font-size: 11px;
    color: #ffa726;
    background: rgba(255,167,38,.08);
    border-radius: 6px;
    padding: 7px 10px;
    max-height: 80px;
    overflow-y: auto;
    line-height: 1.6;
}
.am-warnings code { font-family: monospace; font-size: 10px; opacity: 0.85; }
.am-actions { display: flex; gap: 8px; flex-wrap: wrap; }
/* Buttons */
.am-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 7px 16px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border: none;
    transition: background 0.15s, opacity 0.15s;
}
.am-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.am-btn-primary   { background: #7c8cf8; color: #fff; }
.am-btn-primary:hover:not(:disabled)   { background: #8f9dff; }
.am-btn-secondary { background: rgba(124,140,248,0.15); color: #9aabff; }
.am-btn-secondary:hover:not(:disabled) { background: rgba(124,140,248,0.25); }
.am-btn-ghost     { background: rgba(255,255,255,0.06); color: #9098b0; }
.am-btn-ghost:hover:not(:disabled)     { background: rgba(255,255,255,0.10); }
`;
        document.head.appendChild(style);
    }

    // Inject styles as soon as the module loads
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _injectStyles);
    } else {
        _injectStyles();
    }

    // -----------------------------------------------------------------------
    // Public interface
    // -----------------------------------------------------------------------
    return { open, close };
})();
