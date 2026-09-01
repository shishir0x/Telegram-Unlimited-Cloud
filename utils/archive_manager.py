"""
archive_manager.py — Production-grade archive manager for Telegram-Unlimited-Cloud.

Supported formats: ZIP (stdlib zipfile — no extra dependencies).

Security guarantees:
  - Path traversal: every member path is canonicalized and checked to stay
    inside the sandbox before any byte is written.
  - Zip bombs (size): total uncompressed bytes are capped at MAX_EXTRACT_SIZE.
  - Zip bombs (file count): total member count capped at MAX_EXTRACT_FILES.
  - Zip bombs (ratio): per-member compression ratio capped at MAX_RATIO.
  - Nesting depth: capped at MAX_NESTING_DEPTH path components.
  - No overwrites: conflicts resolved with keep_both rename logic.
  - Temp cleanup: sandbox dir always deleted by caller via cleanup_archive_temp().
"""

import os
import io
import time
import secrets
import zipfile
import shutil
import posixpath
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import List, Optional, Dict, Any, Set

from utils.logger import Logger

logger = Logger(__name__)

# ---------------------------------------------------------------------------
# Default sandbox parent — all extractions land here
# ---------------------------------------------------------------------------
ARCHIVE_TEMP_DIR = Path("./cache/temp_archives")
ARCHIVE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_FORMATS = ["zip"]

# ---------------------------------------------------------------------------
# Security configuration dataclass
# ---------------------------------------------------------------------------


@dataclass
class ArchiveSecurity:
    """Holds per-extraction security limits. Defaults match config.py constants."""
    from utils.extra import is_low_memory_env
    max_extract_size: int = 250 * 1024 * 1024 if is_low_memory_env() else 2 * 1024 ** 3   # 250MB on Render, 2GB default
    max_extract_files: int = 10_000
    max_nesting_depth: int = 32
    max_ratio: int = 200                     # compressed-to-uncompressed ratio cap


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ArchiveEntry:
    """A single file or directory entry inside an archive."""
    name: str                    # bare filename (not full path)
    path: str                    # full archive-relative POSIX path
    is_dir: bool
    size: int                    # uncompressed bytes (0 for dirs)
    compressed_size: int         # compressed bytes (0 for dirs)
    ratio: float                 # compression ratio (size / compressed_size)
    depth: int                   # nesting level (0 = root)
    children: List["ArchiveEntry"] = field(default_factory=list)


@dataclass
class ArchiveManifest:
    """Full inspection result for a single archive."""
    format: str
    total_files: int
    total_dirs: int
    total_size: int              # sum of uncompressed sizes
    total_compressed_size: int
    entries: List[ArchiveEntry]  # flat list of all entries
    tree: List[ArchiveEntry]     # nested tree (root entries only, with children)


@dataclass
class ExtractResult:
    """Result of an extraction run."""
    sandbox: Path
    extracted_files: List[Path]  # absolute paths inside sandbox
    skipped: List[str]           # member paths that were skipped (with reason)
    errors: List[str]            # non-fatal extraction errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_member_path(raw_path: str) -> Optional[str]:
    """
    Normalises a zip member path to a safe, sandbox-relative POSIX string.
    Returns None if the path is unsafe (traversal attempt, absolute, drive letters, etc.).
    """
    if not raw_path or "\x00" in raw_path:
        return None

    # Normalise backslashes
    norm = raw_path.replace("\\", "/")

    # Reject drive letters (e.g. C:) or URI schemes
    if ":" in norm:
        return None

    # Reject absolute paths
    if norm.startswith("/"):
        return None

    # Resolve .. relative to /sandbox
    target = "/sandbox/" + norm
    resolved = posixpath.normpath(target)

    # Must strictly remain inside /sandbox/
    if resolved == "/sandbox" or not resolved.startswith("/sandbox/"):
        return None

    relative = resolved[len("/sandbox/"):].strip("/")
    if not relative:
        return None

    return relative


def _nesting_depth(posix_path: str) -> int:
    """Returns the directory depth of a POSIX path string."""
    return len([p for p in posix_path.split("/") if p])


def _keep_both_name(dest: Path) -> Path:
    """Returns a non-conflicting path using the (N) suffix scheme."""
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def detect_format(file_path: Path) -> Optional[str]:
    """Detects archive format by magic bytes and extension."""
    ext = file_path.suffix.lower()
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
    except Exception:
        return None

    # ZIP: PK signature
    if header[:2] == b"PK":
        return "zip"

    return None


# ---------------------------------------------------------------------------
# Core: inspect
# ---------------------------------------------------------------------------


def inspect_archive(
    file_path: Path,
    security: Optional[ArchiveSecurity] = None,
) -> ArchiveManifest:
    """
    Opens an archive and returns a full manifest without extracting anything.

    Raises:
        ValueError  — unsupported format, security violation, malformed archive
        OSError     — file not found / unreadable
    """
    if security is None:
        security = ArchiveSecurity()

    fmt = detect_format(file_path)
    if fmt != "zip":
        raise ValueError(f"Unsupported or unrecognised archive format for: {file_path.name}")

    entries_flat: List[ArchiveEntry] = []
    total_files = 0
    total_dirs = 0
    total_size = 0
    total_compressed = 0

    with zipfile.ZipFile(file_path, "r") as zf:
        members = zf.infolist()

        if len(members) > security.max_extract_files:
            raise ValueError(
                f"Archive contains {len(members)} members — exceeds safety limit "
                f"of {security.max_extract_files:,} files."
            )

        for info in members:
            raw_path = info.filename

            clean = _sanitize_member_path(raw_path)
            if clean is None:
                logger.warning(f"inspect_archive: unsafe member path skipped: {raw_path!r}")
                continue

            depth = _nesting_depth(clean)
            if depth > security.max_nesting_depth:
                raise ValueError(
                    f"Archive member '{clean}' nests {depth} levels deep — "
                    f"exceeds safety limit of {security.max_nesting_depth}."
                )

            is_dir = raw_path.endswith("/") or info.is_dir() if hasattr(info, "is_dir") else raw_path.endswith("/")

            if not is_dir:
                total_size += info.file_size
                total_compressed += info.compress_size

                if total_size > security.max_extract_size:
                    raise ValueError(
                        f"Archive total uncompressed size exceeds safety limit "
                        f"of {security.max_extract_size // (1024**3)} GB."
                    )

                # Per-member ratio check (avoid divide-by-zero)
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > security.max_ratio:
                        raise ValueError(
                            f"Member '{clean}' has a compression ratio of {ratio:.0f}× "
                            f"— exceeds safety limit of {security.max_ratio}× (zip-bomb suspected)."
                        )
                else:
                    ratio = 1.0

                total_files += 1
                entries_flat.append(ArchiveEntry(
                    name=Path(clean).name,
                    path=clean,
                    is_dir=False,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    ratio=round(ratio, 2),
                    depth=depth - 1,  # depth of the file itself within its folder
                ))
            else:
                total_dirs += 1
                entries_flat.append(ArchiveEntry(
                    name=Path(clean).name or clean,
                    path=clean,
                    is_dir=True,
                    size=0,
                    compressed_size=0,
                    ratio=1.0,
                    depth=depth - 1,
                ))

    tree = _build_tree(entries_flat)

    return ArchiveManifest(
        format="zip",
        total_files=total_files,
        total_dirs=total_dirs,
        total_size=total_size,
        total_compressed_size=total_compressed,
        entries=entries_flat,
        tree=tree,
    )


def _build_tree(entries: List[ArchiveEntry]) -> List[ArchiveEntry]:
    """Converts a flat entry list into a nested tree by path."""
    # Map: path → entry
    by_path: Dict[str, ArchiveEntry] = {}
    for e in entries:
        by_path[e.path] = e

    roots: List[ArchiveEntry] = []

    for e in entries:
        parts = [p for p in e.path.split("/") if p]
        if len(parts) <= 1:
            roots.append(e)
        else:
            parent_path = "/".join(parts[:-1])
            parent = by_path.get(parent_path)
            if parent:
                parent.children.append(e)
            else:
                # Parent directory not explicitly listed — attach to roots
                roots.append(e)

    return roots


def manifest_to_dict(manifest: ArchiveManifest) -> Dict[str, Any]:
    """Serialises an ArchiveManifest to a JSON-serializable dict."""
    def entry_to_dict(e: ArchiveEntry) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": e.name,
            "path": e.path,
            "is_dir": e.is_dir,
            "size": e.size,
            "compressed_size": e.compressed_size,
            "ratio": e.ratio,
            "depth": e.depth,
        }
        if e.children:
            d["children"] = [entry_to_dict(c) for c in e.children]
        return d

    return {
        "format": manifest.format,
        "total_files": manifest.total_files,
        "total_dirs": manifest.total_dirs,
        "total_size": manifest.total_size,
        "total_compressed_size": manifest.total_compressed_size,
        "entries": [entry_to_dict(e) for e in manifest.entries],
        "tree": [entry_to_dict(e) for e in manifest.tree],
    }


# ---------------------------------------------------------------------------
# Core: extract
# ---------------------------------------------------------------------------


def extract_archive(
    file_path: Path,
    member_paths: Optional[List[str]],
    sandbox: Path,
    security: Optional[ArchiveSecurity] = None,
) -> ExtractResult:
    """
    Extracts selected members (or all if member_paths is None) into ``sandbox``.

    The sandbox must already exist. Every extracted path is verified to remain
    inside the sandbox before bytes are written. No files outside the sandbox
    are ever created.

    Returns an ExtractResult with the list of successfully extracted file paths.
    """
    if security is None:
        security = ArchiveSecurity()

    sandbox = sandbox.resolve()
    extracted: List[Path] = []
    skipped: List[str] = []
    errors: List[str] = []

    # Build a normalised set of requested member paths for fast lookup
    requested: Optional[Set[str]] = None
    if member_paths is not None:
        requested = set()
        for mp in member_paths:
            clean = _sanitize_member_path(mp)
            if clean:
                requested.add(clean)

    total_extracted_bytes = 0
    file_count = 0

    with zipfile.ZipFile(file_path, "r") as zf:
        for info in zf.infolist():
            raw_path = info.filename

            # --- Sanitize member path ---
            clean = _sanitize_member_path(raw_path)
            if clean is None:
                skipped.append(f"{raw_path!r}: unsafe path (traversal attempt blocked)")
                continue

            # --- Filter if specific members were requested ---
            if requested is not None and clean not in requested:
                continue

            is_dir = raw_path.endswith("/") or (hasattr(info, "is_dir") and info.is_dir())

            # Skip directory entries — we create parent dirs implicitly
            if is_dir:
                continue

            # --- Security: depth ---
            depth = _nesting_depth(clean)
            if depth > security.max_nesting_depth:
                skipped.append(f"{clean}: nesting depth {depth} > limit {security.max_nesting_depth}")
                continue

            # --- Security: per-member ratio ---
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > security.max_ratio:
                    skipped.append(
                        f"{clean}: compression ratio {ratio:.0f}× > limit {security.max_ratio}× (zip-bomb)"
                    )
                    continue

            # --- Security: cumulative size ---
            total_extracted_bytes += info.file_size
            if total_extracted_bytes > security.max_extract_size:
                skipped.append(f"{clean}: total extraction size would exceed {security.max_extract_size} bytes")
                break

            # --- Security: file count ---
            file_count += 1
            if file_count > security.max_extract_files:
                skipped.append(f"{clean}: file count exceeds limit {security.max_extract_files}")
                break

            # --- Compute destination path ---
            dest = sandbox / clean
            dest = dest.resolve()

            # Double-check the resolved path is still inside sandbox
            try:
                dest.relative_to(sandbox)
            except ValueError:
                skipped.append(f"{clean}: resolved path escapes sandbox (traversal blocked)")
                continue

            # --- Create parent directories ---
            dest.parent.mkdir(parents=True, exist_ok=True)

            # --- Conflict resolution: keep_both ---
            dest = _keep_both_name(dest)

            # --- Extract member bytes ---
            try:
                with zf.open(info) as src, open(dest, "wb") as out:
                    # Stream in 512 KB chunks to avoid OOM on large members
                    while True:
                        chunk = src.read(512 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)

                extracted.append(dest)
            except Exception as e:
                errors.append(f"{clean}: extraction failed — {e}")
                # Remove partial output
                try:
                    if dest.exists():
                        dest.unlink()
                except Exception:
                    pass

    return ExtractResult(
        sandbox=sandbox,
        extracted_files=extracted,
        skipped=skipped,
        errors=errors,
    )


def make_sandbox() -> Path:
    """Creates a fresh, uniquely-named sandbox directory for one extraction job."""
    ARCHIVE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    sandbox = (ARCHIVE_TEMP_DIR / token).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)
    return sandbox


def cleanup_archive_temp(sandbox: Path) -> None:
    """Safely removes the sandbox directory and all contents."""
    try:
        if sandbox.exists() and str(sandbox.resolve()).startswith(str(ARCHIVE_TEMP_DIR.resolve())):
            shutil.rmtree(sandbox, ignore_errors=True)
            logger.info(f"Archive sandbox cleaned up: {sandbox.name}")
    except Exception as e:
        logger.warning(f"Failed to clean archive sandbox {sandbox}: {e}")
    finally:
        from utils.extra import clean_memory
        clean_memory()


# ---------------------------------------------------------------------------
# Short-lived download token registry
# Token maps to: {"sandbox": Path, "members": {safe_path: abs_path}}
# TTL: 10 minutes
# ---------------------------------------------------------------------------

_download_tokens: Dict[str, Dict[str, Any]] = {}
_TOKEN_TTL = 600  # seconds


def register_download_token(sandbox: Path, file_map: Dict[str, Path]) -> str:
    """
    Registers a short-lived download token for direct-browser extraction downloads.
    Returns the token string.
    """
    _purge_expired_tokens()
    token = secrets.token_urlsafe(24)
    _download_tokens[token] = {
        "sandbox": sandbox,
        "members": file_map,
        "expires": time.monotonic() + _TOKEN_TTL,
    }
    return token


def resolve_download_token(token: str, member: str) -> Optional[Path]:
    """
    Resolves a download token + member path to an absolute file path.
    Returns None if the token is invalid, expired, or the member is not found.
    """
    _purge_expired_tokens()
    entry = _download_tokens.get(token)
    if not entry:
        return None
    if time.monotonic() > entry["expires"]:
        _download_tokens.pop(token, None)
        return None
    clean = _sanitize_member_path(member)
    if not clean:
        return None
    file_path = entry["members"].get(clean)
    if not file_path or not file_path.exists():
        return None
    return file_path


def _purge_expired_tokens() -> None:
    now = time.monotonic()
    expired = [k for k, v in _download_tokens.items() if now > v["expires"]]
    for k in expired:
        rec = _download_tokens.pop(k, None)
        if rec:
            cleanup_archive_temp(rec["sandbox"])
