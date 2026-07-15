"""Sandboxed Google Drive operations executed inside a Colab runtime."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any


DRIVE_MOUNT_PATH = "/content/drive"
DRIVE_WORKSPACE_NAME = "codex-colab"
DRIVE_WORKSPACE_PATH = f"{DRIVE_MOUNT_PATH}/MyDrive/{DRIVE_WORKSPACE_NAME}"
RESULT_MARKER = "CODEX_DRIVE_RESULT="
MAX_DRIVE_PATH_LENGTH = 512


def normalize_drive_path(path: str, *, allow_root: bool = True) -> str:
    """Return a safe POSIX path relative to the dedicated Drive workspace."""
    if not isinstance(path, str):
        raise ValueError("drive_path must be text")
    value = path.strip().replace("\\", "/")
    if value in {"", "."}:
        if allow_root:
            return "."
        raise ValueError("drive_path must name an item inside codex-colab")
    if len(value) > MAX_DRIVE_PATH_LENGTH:
        raise ValueError(
            f"drive_path must be at most {MAX_DRIVE_PATH_LENGTH} characters"
        )
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise ValueError(
            "drive_path must be relative to codex-colab without empty components"
        )
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            "drive_path must stay inside codex-colab and cannot contain '.' or '..'"
        )
    if any(any(ord(character) < 32 for character in part) for part in parts):
        raise ValueError("drive_path cannot contain control characters")
    return "/".join(parts)


def display_drive_path(relative: str = ".") -> str:
    normalized = normalize_drive_path(relative)
    root = f"MyDrive/{DRIVE_WORKSPACE_NAME}"
    return root if normalized == "." else f"{root}/{normalized}"


def absolute_drive_path(relative: str = ".") -> str:
    normalized = normalize_drive_path(relative)
    return (
        DRIVE_WORKSPACE_PATH
        if normalized == "."
        else f"{DRIVE_WORKSPACE_PATH}/{normalized}"
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_symlink_components(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    if current.is_symlink():
        raise PermissionError("The codex-colab Drive workspace cannot be a symlink")
    for part in relative.parts:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            raise PermissionError("Drive workspace paths cannot contain symlinks")
        if current != root and os.path.ismount(current):
            raise PermissionError("Paths cannot cross a nested filesystem mount")


def _workspace_root(mount_root: Path, *, create: bool) -> Path:
    my_drive = mount_root / "MyDrive"
    if not my_drive.is_dir():
        raise RuntimeError(
            "Google Drive is not mounted. Run mount_google_drive and complete any user authorization step."
        )
    root = my_drive / DRIVE_WORKSPACE_NAME
    if os.path.lexists(root) and root.is_symlink():
        raise PermissionError("MyDrive/codex-colab must not be a symlink")
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
    if not root.is_dir():
        raise RuntimeError("MyDrive/codex-colab exists but is not a folder")
    return root.resolve(strict=True)


def _workspace_path(
    root: Path,
    relative: str,
    *,
    must_exist: bool,
    allow_root: bool = True,
) -> Path:
    normalized = normalize_drive_path(relative, allow_root=allow_root)
    candidate = root if normalized == "." else root.joinpath(*normalized.split("/"))
    _reject_symlink_components(candidate, root)
    resolved = candidate.resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise PermissionError("Drive path escapes MyDrive/codex-colab")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"Drive path does not exist: {display_drive_path(normalized)}")
    return candidate


def _content_path(
    raw_path: str,
    *,
    content_root: Path,
    mount_root: Path,
    must_exist: bool,
) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise ValueError("remote_path must be absolute inside /content")
    content = content_root.resolve(strict=True)
    drive_mount = mount_root.resolve(strict=False)
    existing_parent = candidate if candidate.exists() else candidate.parent
    resolved_parent = existing_parent.resolve(strict=False)
    resolved = candidate.resolve(strict=False)
    if not _is_relative_to(resolved, content):
        raise PermissionError("remote_path must stay inside /content")
    if resolved == content:
        raise PermissionError("remote_path must name an item below /content")
    if _is_relative_to(resolved, drive_mount) or _is_relative_to(
        resolved_parent, drive_mount
    ):
        raise PermissionError(
            "Use Drive tools for MyDrive/codex-colab; other mounted Drive paths are blocked"
        )
    # Windows can present the same temporary path in long and 8.3 forms. The
    # production runtime is Linux, where the lexical path is retained so every
    # existing component can be checked before a copy.
    symlink_check_path = resolved if os.name == "nt" else candidate
    _reject_symlink_components(symlink_check_path, content)
    if must_exist and not candidate.exists():
        raise FileNotFoundError(f"Remote path does not exist: {candidate}")
    return candidate


def _reject_symlink_tree(path: Path) -> None:
    if path.is_symlink():
        raise PermissionError("Copying symlinks is not allowed")
    if not path.is_dir():
        return
    for directory, directories, files in os.walk(path, followlinks=False):
        base = Path(directory)
        for name in [*directories, *files]:
            candidate = base / name
            if candidate.is_symlink():
                raise PermissionError("Folders containing symlinks cannot be copied")
            if os.path.ismount(candidate):
                raise PermissionError("Folders containing nested mounts cannot be copied")


def _iter_directory(path: Path, *, recursive: bool):
    if not recursive:
        for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_symlink() and not os.path.ismount(child):
                yield child
        return
    for directory, directories, files in os.walk(path, followlinks=False):
        base = Path(directory)
        safe_directories = []
        for name in sorted(directories, key=str.lower):
            child = base / name
            if child.is_symlink() or os.path.ismount(child):
                continue
            safe_directories.append(name)
            yield child
        directories[:] = safe_directories
        for name in sorted(files, key=str.lower):
            child = base / name
            if not child.is_symlink() and not os.path.ismount(child):
                yield child


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _metadata(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix() or "."
    stats = path.stat()
    if path.is_file():
        size = stats.st_size
        kind = "file"
    elif path.is_dir():
        size = None
        kind = "folder"
    else:
        size = None
        kind = "other"
    return {
        "name": path.name if relative != "." else DRIVE_WORKSPACE_NAME,
        "drive_path": display_drive_path(relative),
        "relative_path": relative,
        "type": kind,
        "bytes": size,
        "modified_at": int(stats.st_mtime),
    }


def _copy(source: Path, destination: Path, *, overwrite: bool) -> None:
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.codex-{secrets.token_hex(8)}.part"
    try:
        if source.is_dir():
            shutil.copytree(source, temporary, symlinks=False)
        elif source.is_file():
            shutil.copy2(source, temporary)
        else:
            raise ValueError("Only regular files and folders can be copied")
        if destination.exists():
            _remove(destination)
        shutil.move(str(temporary), str(destination))
    finally:
        if os.path.lexists(temporary):
            _remove(temporary)


def perform(
    payload: dict[str, Any],
    *,
    mount_root: Path | None = None,
    content_root: Path | None = None,
) -> dict[str, Any]:
    """Perform one validated operation; dependency injection keeps this testable."""
    mount = (mount_root or Path(DRIVE_MOUNT_PATH)).resolve(strict=False)
    content = (content_root or Path("/content")).resolve(strict=True)
    action = str(payload.get("action", ""))
    root = _workspace_root(mount, create=action != "status")

    if action in {"bootstrap", "status"}:
        return {
            "mounted": True,
            "workspace_exists": root.is_dir(),
            "workspace_path": str(root),
            "drive_path": display_drive_path(),
        }

    if action == "list":
        target = _workspace_path(
            root, str(payload.get("drive_path", ".")), must_exist=True
        )
        recursive = bool(payload.get("recursive", False))
        limit = max(1, min(int(payload.get("max_entries", 200)), 1000))
        if target.is_file():
            entries = [_metadata(target, root)]
        elif target.is_dir():
            entries = []
            for candidate in _iter_directory(target, recursive=recursive):
                entries.append(_metadata(candidate, root))
                if len(entries) > limit:
                    break
        else:
            raise ValueError("Drive path is not a regular file or folder")
        return {
            "drive_path": display_drive_path(target.relative_to(root).as_posix()),
            "entries": entries[:limit],
            "entry_count": min(len(entries), limit),
            "truncated": len(entries) > limit,
        }

    if action == "mkdir":
        target = _workspace_path(
            root,
            str(payload.get("drive_path", "")),
            must_exist=False,
            allow_root=False,
        )
        target.mkdir(parents=True, exist_ok=True)
        return _metadata(target, root)

    if action == "save":
        source = _content_path(
            str(payload.get("remote_path", "")),
            content_root=content,
            mount_root=mount,
            must_exist=True,
        )
        _reject_symlink_tree(source)
        destination = _workspace_path(
            root,
            str(payload.get("drive_path", "")),
            must_exist=False,
            allow_root=False,
        )
        _copy(source, destination, overwrite=bool(payload.get("overwrite", False)))
        return {"remote_path": str(source), **_metadata(destination, root)}

    if action == "restore":
        source = _workspace_path(
            root,
            str(payload.get("drive_path", "")),
            must_exist=True,
            allow_root=False,
        )
        _reject_symlink_tree(source)
        destination = _content_path(
            str(payload.get("remote_path", "")),
            content_root=content,
            mount_root=mount,
            must_exist=False,
        )
        _copy(source, destination, overwrite=bool(payload.get("overwrite", False)))
        return {**_metadata(source, root), "remote_path": str(destination)}

    if action == "move":
        source = _workspace_path(
            root,
            str(payload.get("source_drive_path", "")),
            must_exist=True,
            allow_root=False,
        )
        destination = _workspace_path(
            root,
            str(payload.get("destination_drive_path", "")),
            must_exist=False,
            allow_root=False,
        )
        if source == destination:
            return _metadata(source, root)
        if source.is_dir() and _is_relative_to(
            destination.resolve(strict=False), source.resolve(strict=True)
        ):
            raise ValueError("A Drive folder cannot be moved inside itself")
        if destination.exists() and _is_relative_to(
            source.resolve(strict=True), destination.resolve(strict=True)
        ):
            raise ValueError("A Drive item cannot overwrite a folder containing it")
        if destination.exists() and not bool(payload.get("overwrite", False)):
            raise FileExistsError(f"Destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            _remove(destination)
        shutil.move(str(source), str(destination))
        return _metadata(destination, root)

    if action == "delete":
        if payload.get("confirm") is not True:
            raise PermissionError("Set confirm=true to delete this Drive path")
        target = _workspace_path(
            root,
            str(payload.get("drive_path", "")),
            must_exist=True,
            allow_root=False,
        )
        deleted = display_drive_path(target.relative_to(root).as_posix())
        _remove(target)
        return {"deleted": True, "drive_path": deleted}

    raise ValueError(f"Unsupported Drive operation: {action}")


def remote_script(payload: dict[str, Any]) -> str:
    """Build a self-contained standard-library script for the Colab runtime."""
    source = Path(__file__).read_text(encoding="utf-8")
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    return (
        source
        + "\n"
        + "_payload=json.loads(base64.b64decode("
        + repr(encoded)
        + ").decode())\n"
        + "print(RESULT_MARKER+json.dumps(perform(_payload),separators=(',',':')))\n"
    )
