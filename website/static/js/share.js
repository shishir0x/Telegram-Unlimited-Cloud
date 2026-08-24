/* Secure Share Page — public viewer for /s/<token> links. */
(function () {
  "use strict";

  const pathParts = location.pathname.split("/").filter(Boolean);
  const TOKEN = decodeURIComponent(pathParts.pop() || "");
  const $ = (id) => document.getElementById(id);

  const state = { meta: null, rel: "", previewableCache: {} };

  /* ── helpers ─────────────────────────────────────────── */
  async function postJSON(url, body) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      let json = {};
      try { json = await res.json(); } catch (_) { /* empty */ }
      return { status: res.status, json };
    } catch (err) {
      return { status: 0, json: { error: "network_error" } };
    }
  }

  function formatSize(bytes) {
    if (bytes === null || bytes === undefined) return "";
    if (!isFinite(bytes) || bytes < 0) return "";
    if (bytes === 0) return "0 B";
    const u = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), u.length - 1);
    return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
  }

  function showToast(msg, isErr) {
    const t = $("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.toggle("err-t", !!isErr);
    t.classList.remove("hidden");
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.add("hidden"), 2600);
  }

  function showOnly(id) {
    ["st-loading", "st-invalid", "st-revoked", "st-expired", "st-gone", "st-rate-limited", "st-error", "st-locked", "st-main"]
      .forEach((s) => {
        const el = $(s);
        if (el) el.classList.toggle("hidden", s !== id);
      });
  }

  const fileUrl = (rel, dl) => {
    const cleanRel = (rel || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
    const base = `/share/${encodeURIComponent(TOKEN)}/file${cleanRel ? "/" + cleanRel : ""}`;
    return dl ? `${base}?dl=1` : base;
  };

  const thumbUrl = (rel) => {
    const cleanRel = (rel || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
    return `/share/${encodeURIComponent(TOKEN)}/thumb${cleanRel ? "/" + cleanRel : ""}`;
  };

  const zipUrl = (rel) => {
    const cleanRel = (rel || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
    return `/share/${encodeURIComponent(TOKEN)}/zip${cleanRel ? "/" + cleanRel : ""}`;
  };

  /* ── boot ────────────────────────────────────────────── */
  async function init() {
    if (!TOKEN) {
      return showOnly("st-invalid");
    }
    showOnly("st-loading");
    await loadRel("");
  }

  async function loadRel(rel) {
    const { status, json } = await postJSON("/api/share/meta", { token: TOKEN, rel });

    if (status === 429) return showOnly("st-rate-limited");
    if (status >= 500) return showOnly("st-error");
    if (status === 410) {
      if (json.error === "revoked") return showOnly("st-revoked");
      if (json.error === "expired") return showOnly("st-expired");
      return showOnly("st-gone");
    }
    if (status === 404) {
      if (json.error === "revoked") return showOnly("st-revoked");
      if (json.error === "expired") return showOnly("st-expired");
      if (json.error === "gone") return showOnly("st-gone");
      return showOnly("st-invalid");
    }
    if (json.status === "locked") {
      $("unlock-error").classList.add("hidden");
      return showOnly("st-locked");
    }
    if (json.status !== "ok") return showOnly("st-invalid");

    state.meta = json;
    state.rel = rel || "";
    renderExpiry(json.expires_at);
    showOnly("st-main");
    if (json.type === "folder") renderFolder(json);
    else renderFile(json);
  }

  function renderExpiry(expiresAt) {
    const chip = $("expiry-chip");
    if (!chip) return;
    if (!expiresAt) return chip.classList.add("hidden");
    chip.textContent = `Link expires ${new Date(expiresAt * 1000).toLocaleString()}`;
    chip.classList.remove("hidden");
  }

  /* ── file view ───────────────────────────────────────── */
  function iconFor(kind) {
    const map = {
      image: "🖼️", video: "🎬", audio: "🎵", pdf: "📕", text: "📄",
      folder: "📁", file: "📦",
    };
    const span = document.createElement("span");
    span.style.cssText = "font-size:30px;line-height:1";
    span.textContent = map[kind] || map.file;
    return span;
  }

  function renderFile(m) {
    $("folder-view").classList.add("hidden");
    $("crumbs").classList.add("hidden");
    const zipBtn = $("btn-download-folder-zip");
    if (zipBtn) zipBtn.classList.add("hidden");
    $("file-card").classList.remove("hidden");

    $("file-name").textContent = m.name;
    $("file-size").textContent = formatSize(m.size);
    $("file-date").textContent = m.date ? `  •  ${m.date}` : "";

    const dl = $("btn-download");
    if (m.allow_download) {
      dl.disabled = false;
      dl.onclick = () => { window.location.href = fileUrl("", true); showToast("Download started"); };
      $("perm-note").classList.add("hidden");
    } else {
      dl.disabled = true;
      dl.title = "The owner disabled downloads for this link";
      $("perm-note").textContent = "Downloads were disabled by the owner — preview only.";
      $("perm-note").classList.remove("hidden");
      dl.onclick = () => showToast("Download unavailable for this link", true);
    }

    renderPreviewArea(m);
  }

  function renderPreviewArea(m) {
    const area = $("file-preview-area");
    area.textContent = "";
    if (!m.allow_preview || !m.preview_kind) {
      area.appendChild(fallbackPreview(m.preview_kind));
      return;
    }
    const src = fileUrl("");
    if (m.preview_kind === "image") {
      const img = document.createElement("img");
      img.alt = m.name; img.src = src; area.appendChild(img);
    } else if (m.preview_kind === "video") {
      const v = document.createElement("video");
      v.controls = true; v.preload = "metadata"; v.src = src; area.appendChild(v);
    } else if (m.preview_kind === "audio") {
      const a = document.createElement("audio");
      a.controls = true; a.src = src; area.appendChild(a);
    } else if (m.preview_kind === "pdf") {
      const f = document.createElement("iframe");
      f.src = src; f.title = m.name; area.appendChild(f);
    } else if (m.preview_kind === "text") {
      fetch(src, { headers: { Range: "bytes=0-204799" } })
        .then((r) => r.text())
        .then((txt) => {
          const pre = document.createElement("pre");
          pre.textContent = txt.length > 200000 ? txt.slice(0, 200000) + "\n… truncated" : txt;
          area.replaceChildren(pre);
        })
        .catch(() => area.replaceChildren(fallbackPreview("text")));
    }
  }

  function fallbackPreview(kind) {
    const box = document.createElement("div");
    box.className = "preview-fallback";
    box.appendChild(iconFor(kind || "file"));
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = kind ? "No inline preview available — use the download button." : "Preview is disabled for this link.";
    box.appendChild(p);
    return box;
  }

  /* ── folder view ─────────────────────────────────────── */
  function renderFolder(m) {
    $("file-card").classList.add("hidden");
    $("folder-view").classList.remove("hidden");

    renderCrumbs(m.breadcrumbs || []);

    const zipBtn = $("btn-download-folder-zip");
    if (zipBtn) {
      if (m.allow_download) {
        zipBtn.classList.remove("hidden");
        zipBtn.onclick = () => {
          window.location.href = zipUrl(state.rel);
          showToast("Preparing ZIP download... 📦");
        };
      } else {
        zipBtn.classList.add("hidden");
      }
    }

    const list = $("listing");
    list.textContent = "";

    const children = m.children || [];
    $("empty-folder").classList.toggle("hidden", children.length > 0);

    children.forEach((c) => {
      const li = document.createElement("li");
      li.className = "row";

      const icoWrap = document.createElement("div");
      icoWrap.className = "ico";
      if (c.type === "file" && isThumbable(c.name)) {
        const im = document.createElement("img");
        im.loading = "lazy"; im.alt = ""; im.width = 34; im.height = 34;
        im.style.cssText = "width:34px;height:34px;object-fit:cover;border-radius:7px";
        im.src = thumbUrl(fullRel(c.key));
        im.onerror = () => im.replaceWith(iconFor(extKind(c.name)));
        icoWrap.appendChild(im);
      } else {
        icoWrap.appendChild(iconFor(c.type));
      }
      li.appendChild(icoWrap);

      const nameEl = document.createElement("div");
      nameEl.className = "name";
      nameEl.textContent = c.name;
      nameEl.title = c.name;
      li.appendChild(nameEl);

      const sz = document.createElement("span");
      sz.className = "sz";
      sz.textContent = c.size === null ? "" : formatSize(c.size);
      li.appendChild(sz);

      const act = document.createElement("div");
      act.className = "act";
      if (c.type === "file") {
        if (state.meta.allow_preview && extKind(c.name)) {
          act.appendChild(iconBtn("👁", "Preview", () => openOverlay(c)));
        }
        const dlBtn = iconBtn("⬇", "Download", () => {
          if (!state.meta.allow_download) return showToast("Download unavailable for this link", true);
          window.location.href = fileUrl(fullRel(c.key), true);
          showToast("Download started");
        });
        if (!state.meta.allow_download) dlBtn.classList.add("disabled");
        act.appendChild(dlBtn);
      }
      li.appendChild(act);

      li.addEventListener("click", (ev) => {
        if (ev.target.closest(".act")) return;
        if (c.type === "folder") loadRel(state.rel ? `${state.rel}/${c.key}` : c.key);
        else if (state.meta.allow_preview && extKind(c.name)) openOverlay(c);
      });

      list.appendChild(li);
    });
  }

  function fullRel(key) { return state.rel ? `${state.rel}/${key}` : key; }

  function isThumbable(name) {
    return state.previewableCache[name] !== false &&
      /\.(jpg|jpeg|png|webp|gif|bmp)$/i.test(name);
  }

  function extKind(name) {
    const e = (name.split(".").pop() || "").toLowerCase();
    if (["jpg","jpeg","png","webp","gif","bmp","svg","avif"].includes(e)) return "image";
    if (["mp4","webm","mkv","mov","m4v","ogv"].includes(e)) return "video";
    if (["mp3","wav","ogg","flac","m4a","aac","opus"].includes(e)) return "audio";
    if (e === "pdf") return "pdf";
    if (["txt","md","json","csv","log","xml","yml","yaml","py","js","ts","html","css"].includes(e)) return "text";
    return null;
  }

  function iconBtn(char, title, fn) {
    const b = document.createElement("button");
    b.className = "icon-btn";
    b.title = title;
    b.setAttribute("aria-label", title);
    b.textContent = char;
    b.addEventListener("click", (ev) => { ev.stopPropagation(); fn(); });
    return b;
  }

  function renderCrumbs(crumbs) {
    const nav = $("crumbs");
    nav.textContent = "";
    nav.classList.toggle("hidden", !(crumbs && crumbs.length));

    crumbs.forEach((c, i) => {
      if (i > 0) {
        const sep = document.createElement("span");
        sep.className = "crumb-sep";
        sep.textContent = "/";
        nav.appendChild(sep);
      }
      const b = document.createElement("button");
      b.className = "crumb";
      b.textContent = c.name;
      const last = i === crumbs.length - 1;
      if (last || c.rel === undefined) b.disabled = last;
      b.addEventListener("click", () => { if (!b.disabled) loadRel(c.rel); });
      nav.appendChild(b);
    });
  }

  /* ── preview overlay (folder context) ────────────────── */
  function openOverlay(item) {
    if (!state.meta.allow_preview) return showToast("Preview unavailable for this link", true);
    const kind = extKind(item.name);
    const rel = fullRel(item.key);

    $("pv-title").textContent = item.name;
    const body = $("pv-body");
    body.textContent = "";

    const dlA = $("pv-download");
    dlA.onclick = null;
    if (state.meta.allow_download) {
      dlA.classList.remove("disabled");
      dlA.href = fileUrl(rel, true);
    } else {
      dlA.classList.add("disabled");
      dlA.removeAttribute("href");
      dlA.onclick = (e) => { e.preventDefault(); showToast("Download unavailable for this link", true); };
    }

    const src = fileUrl(rel, false);
    if (kind === "image") { const i = document.createElement("img"); i.alt = item.name; i.src = src; body.appendChild(i); }
    else if (kind === "video") { const v = document.createElement("video"); v.controls = true; v.autoplay = true; v.src = src; body.appendChild(v); }
    else if (kind === "audio") { const a = document.createElement("audio"); a.controls = true; a.autoplay = true; a.src = src; body.appendChild(a); }
    else if (kind === "pdf") { const f = document.createElement("iframe"); f.src = src; f.title = item.name; body.appendChild(f); }
    else if (kind === "text") {
      fetch(src, { headers: { Range: "bytes=0-204799" } }).then((r) => r.text()).then((t) => {
        const pre = document.createElement("pre");
        pre.textContent = t.length > 200000 ? t.slice(0, 200000) + "\n… truncated" : t;
        body.appendChild(pre);
      });
    }
    $("pv-overlay").classList.remove("hidden");
  }

  function closeOverlay() {
    $("pv-body").textContent = "";
    $("pv-overlay").classList.add("hidden");
  }
  $("pv-close").addEventListener("click", closeOverlay);
  $("pv-overlay").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeOverlay(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeOverlay(); });

  /* ── password gate ───────────────────────────────────── */
  $("unlock-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = $("unlock-btn");
    const err = $("unlock-error");
    btn.disabled = true;
    btn.textContent = "Unlocking…";
    const { status, json } = await postJSON("/api/share/unlock", { token: TOKEN, password: $("unlock-pwd").value });
    btn.disabled = false;
    btn.textContent = "Unlock";

    if (status === 429) {
      err.textContent = "Too many attempts. Please wait a minute.";
      err.classList.remove("hidden");
      return;
    }
    if (status === 401 || json.error === "bad_password") {
      err.textContent = "Incorrect password. Please try again.";
      err.classList.remove("hidden");
      const card = $("st-locked");
      card.classList.remove("shake");
      void card.offsetWidth;
      card.classList.add("shake");
      $("unlock-pwd").select();
      return;
    }
    if (status === 410) {
      err.textContent = json.error === "expired" ? "This link has expired." : "This link was revoked.";
      err.classList.remove("hidden");
      return;
    }
    if (status !== 200) {
      err.textContent = "Could not unlock this link.";
      err.classList.remove("hidden");
      return;
    }
    err.classList.add("hidden");
    $("unlock-pwd").value = "";
    showToast("Unlocked successfully");
    loadRel("");
  });

  init();
})();

