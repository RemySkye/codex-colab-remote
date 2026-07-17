"""Resumable parallel file and folder transfers for Colab Remote."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tarfile
import time
from typing import Any

import process_utils


TRANSFER_ID = re.compile(r"[0-9a-f]{24}")
SAFE_PART = re.compile(r"part-[0-9]{6}")


def directory(api: Any, transfer_id: str) -> Path:
    if not TRANSFER_ID.fullmatch(transfer_id):
        raise ValueError("Invalid transfer_id")
    return api.TRANSFERS_ROOT / "managed" / transfer_id


def state_path(api: Any, transfer_id: str) -> Path:
    return directory(api, transfer_id) / "state.json"


def save_state(api: Any, state: dict[str, Any]) -> None:
    api._secure_state_root()
    transfer_id = str(state["transfer_id"])
    root = directory(api, transfer_id)
    root.mkdir(parents=True, exist_ok=True)
    target = root / "state.json"
    temporary = root / f"state.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, target)


def load_state(api: Any, transfer_id: str) -> dict[str, Any]:
    path = state_path(api, transfer_id)
    if not path.is_file():
        raise ValueError(f"Unknown transfer_id: {transfer_id}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("transfer_id") != transfer_id:
        raise ValueError("Invalid transfer state")
    return value


def cancelled(api: Any, transfer_id: str) -> bool:
    return (directory(api, transfer_id) / "cancel.requested").exists()


def progress(
    api: Any,
    state: dict[str, Any],
    *,
    chunks_done: int | None = None,
    bytes_done: int | None = None,
    status: str | None = None,
) -> None:
    if chunks_done is not None:
        state["chunks_done"] = chunks_done
    if bytes_done is not None:
        state["bytes_done"] = bytes_done
    if status is not None:
        state["status"] = status
    total = int(state.get("bytes_total", 0))
    state["progress_percent"] = (
        round(int(state.get("bytes_done", 0)) * 100 / total, 2) if total else 0.0
    )
    state["updated_at"] = int(time.time())
    save_state(api, state)


def split_local(payload: Path, parts_root: Path, chunk_size: int) -> list[Path]:
    parts_root.mkdir(parents=True, exist_ok=True)
    expected = (payload.stat().st_size + chunk_size - 1) // chunk_size
    existing = sorted(parts_root.glob("part-*"))
    if (
        len(existing) == expected
        and sum(path.stat().st_size for path in existing) == payload.stat().st_size
    ):
        return existing
    for path in existing:
        path.unlink(missing_ok=True)
    if payload.stat().st_size == 0:
        part = parts_root / "part-000000"
        part.write_bytes(b"")
        return [part]
    parts = []
    with payload.open("rb") as stream:
        index = 0
        while block := stream.read(chunk_size):
            part = parts_root / f"part-{index:06d}"
            part.write_bytes(block)
            parts.append(part)
            index += 1
    return parts


def create_archive(source: Path, archive: Path) -> None:
    if archive.is_file():
        return
    temporary = archive.with_suffix(".tmp")
    with tarfile.open(temporary, "w:gz") as bundle:
        bundle.add(source, arcname="payload", recursive=True)
    os.replace(temporary, archive)


def safe_extract(archive: Path, destination: Path, overwrite: bool) -> None:
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination}")
    extract_root = archive.parent / "extract"
    shutil.rmtree(extract_root, ignore_errors=True)
    extract_root.mkdir()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            member_path = Path(member.name)
            if (
                member_path.is_absolute()
                or ".." in member_path.parts
                or member.issym()
                or member.islnk()
                or member.isdev()
            ):
                raise ValueError("Archive contains an unsafe path or link")
        bundle.extractall(extract_root, filter="data")
    payload = extract_root / "payload"
    if not payload.exists():
        roots = list(extract_root.iterdir())
        if len(roots) != 1:
            raise RuntimeError("Archive did not contain one payload root")
        payload = roots[0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    shutil.move(str(payload), str(destination))


def remote_parts(api: Any, session: str, remote_stage: str) -> dict[str, int]:
    script = f"""python3 - <<'PY'
import json
from pathlib import Path
d=Path({remote_stage!r})/"parts"
print("CODEX_TRANSFER_PARTS="+json.dumps({{p.name:p.stat().st_size for p in d.glob("part-*") if p.is_file()}},separators=(",",":")))
PY"""
    result = api._remote_shell(session, script, timeout=120)
    value = api._extract_json_marker(result.stdout, "CODEX_TRANSFER_PARTS=")
    return {str(name): int(size) for name, size in value.items()}


def parallel_parts(
    api: Any,
    state: dict[str, Any],
    tasks: list[tuple[str, int]],
    operation: Any,
) -> None:
    transfer_id = str(state["transfer_id"])
    chunks_done = int(state.get("chunks_done", 0))
    bytes_done = int(state.get("bytes_done", 0))
    workers = max(1, min(int(state.get("parallelism", 4)), 8))
    attempts = max(1, min(int(state.get("retry_attempts", 3)), 10))

    def retry(name: str) -> None:
        for attempt in range(1, attempts + 1):
            try:
                operation(name)
                return
            except Exception:
                if attempt == attempts or cancelled(api, transfer_id):
                    raise
                time.sleep(min(2 ** (attempt - 1), 4))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(retry, name): (name, size) for name, size in tasks
        }
        for future in as_completed(future_map):
            future.result()
            _, size = future_map[future]
            chunks_done += 1
            bytes_done += size
            progress(
                api,
                state,
                chunks_done=chunks_done,
                bytes_done=bytes_done,
            )
            if cancelled(api, transfer_id):
                for pending in future_map:
                    pending.cancel()
                raise InterruptedError("Transfer cancellation requested")


def upload(api: Any, state: dict[str, Any]) -> None:
    transfer_id = str(state["transfer_id"])
    session = api._validate_session_name(str(state["session_name"]))
    source = api._allowed_local_path(str(state["local_path"]), must_exist=True)
    destination = api._validate_remote_path(str(state["remote_path"]))
    root = directory(api, transfer_id)
    compress = bool(state.get("compress")) or source.is_dir()
    payload = root / "payload.tar.gz" if compress else source
    if compress:
        create_archive(source, payload)
    parts_root = root / "parts"
    parts = split_local(payload, parts_root, api.TRANSFER_CHUNK_SIZE)
    state["bytes_total"] = payload.stat().st_size
    state["chunks_total"] = len(parts)
    state["compressed"] = compress
    remote_stage = f"/content/.codex-remote/transfers/managed/{transfer_id}"
    api._remote_shell(
        session, f"mkdir -p {shlex.quote(remote_stage)}/parts", timeout=120
    )
    existing = (
        remote_parts(api, session, remote_stage) if state.get("resume", True) else {}
    )
    completed = [
        (part.name, part.stat().st_size)
        for part in parts
        if existing.get(part.name) == part.stat().st_size
    ]
    pending = [
        (part.name, part.stat().st_size)
        for part in parts
        if existing.get(part.name) != part.stat().st_size
    ]
    progress(
        api,
        state,
        chunks_done=len(completed),
        bytes_done=sum(size for _, size in completed),
        status="running",
    )

    def upload_part(name: str) -> None:
        if cancelled(api, transfer_id):
            raise InterruptedError("Transfer cancellation requested")
        api._colab(
            [
                "upload",
                "-s",
                session,
                api._wsl_path(parts_root / name),
                f"{remote_stage}/parts/{name}",
            ],
            timeout=1800,
            serialize_session=False,
        )

    parallel_parts(api, state, pending, upload_part)
    digest = hashlib.sha256()
    with payload.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    assembled = f"{remote_stage}/payload"
    command = (
        "set -euo pipefail; "
        f"cat {shlex.quote(remote_stage)}/parts/part-* > {shlex.quote(assembled)}; "
        f"test \"$(sha256sum {shlex.quote(assembled)} | cut -d' ' -f1)\" = {shlex.quote(digest.hexdigest())}; "
    )
    if compress:
        command += (
            f"rm -rf {shlex.quote(remote_stage)}/extract; mkdir -p {shlex.quote(remote_stage)}/extract; "
            f"tar -xzf {shlex.quote(assembled)} -C {shlex.quote(remote_stage)}/extract; "
            f"mkdir -p {shlex.quote(destination.rsplit('/', 1)[0] or '/')}; rm -rf {shlex.quote(destination)}; "
            f"mv {shlex.quote(remote_stage)}/extract/payload {shlex.quote(destination)}; "
        )
    else:
        command += (
            f"mkdir -p {shlex.quote(destination.rsplit('/', 1)[0] or '/')}; "
            f"mv -f {shlex.quote(assembled)} {shlex.quote(destination)}; "
        )
    command += f"rm -rf {shlex.quote(remote_stage)}"
    api._remote_shell(session, command, timeout=1800)
    state["sha256"] = digest.hexdigest()


def download(api: Any, state: dict[str, Any]) -> None:
    transfer_id = str(state["transfer_id"])
    session = api._validate_session_name(str(state["session_name"]))
    source = api._validate_remote_path(str(state["remote_path"]))
    destination = api._allowed_local_path(str(state["local_path"]), must_exist=False)
    root = directory(api, transfer_id)
    remote_stage = f"/content/.codex-remote/transfers/managed/{transfer_id}"
    kind = api._remote_shell(
        session,
        f"test -d {shlex.quote(source)} && echo directory || (test -f {shlex.quote(source)} && echo file || exit 12)",
        timeout=120,
    ).stdout.strip()
    compress = bool(state.get("compress")) or kind == "directory"
    remote_payload = source
    if compress:
        remote_payload = f"{remote_stage}/payload.tar.gz"
        api._remote_shell(
            session,
            f"mkdir -p {shlex.quote(remote_stage)}/archive; cp -a {shlex.quote(source)} {shlex.quote(remote_stage)}/archive/payload; "
            f"tar -czf {shlex.quote(remote_payload)} -C {shlex.quote(remote_stage)}/archive payload",
            timeout=1800,
        )
    metadata = api._remote_file_metadata(session, remote_payload)
    state["bytes_total"] = int(metadata["bytes"])
    state["compressed"] = compress
    split = api._remote_shell(
        session,
        f"mkdir -p {shlex.quote(remote_stage)}/parts; split -b {api.TRANSFER_CHUNK_SIZE} -d -a 6 {shlex.quote(remote_payload)} {shlex.quote(remote_stage)}/parts/part-; "
        f"test -e {shlex.quote(remote_stage)}/parts/part-000000 || : > {shlex.quote(remote_stage)}/parts/part-000000; "
        f"find {shlex.quote(remote_stage)}/parts -maxdepth 1 -type f -name 'part-*' -printf '%f %s\\n' | sort",
        timeout=1800,
    )
    remote_parts = []
    for line in split.stdout.splitlines():
        name, _, size = line.partition(" ")
        if SAFE_PART.fullmatch(name) and size.isdigit():
            remote_parts.append((name, int(size)))
    if not remote_parts:
        raise RuntimeError("Remote split produced no transfer parts")
    state["chunks_total"] = len(remote_parts)
    parts_root = root / "parts"
    if parts_root.exists() and not state.get("resume", True):
        shutil.rmtree(parts_root)
    parts_root.mkdir(parents=True, exist_ok=True)
    completed = [
        (name, size)
        for name, size in remote_parts
        if (parts_root / name).is_file() and (parts_root / name).stat().st_size == size
    ]
    pending = [
        (name, size) for name, size in remote_parts if (name, size) not in completed
    ]
    progress(
        api,
        state,
        chunks_done=len(completed),
        bytes_done=sum(size for _, size in completed),
        status="running",
    )

    def download_part(name: str) -> None:
        if cancelled(api, transfer_id):
            raise InterruptedError("Transfer cancellation requested")
        api._colab(
            [
                "download",
                "-s",
                session,
                f"{remote_stage}/parts/{name}",
                api._wsl_path(parts_root / name),
            ],
            timeout=1800,
            serialize_session=False,
        )

    parallel_parts(api, state, pending, download_part)
    payload = root / ("payload.tar.gz" if compress else "payload")
    digest = hashlib.sha256()
    with payload.open("wb") as output:
        for name, _ in remote_parts:
            with (parts_root / name).open("rb") as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    output.write(block)
                    digest.update(block)
    if digest.hexdigest() != metadata["sha256"]:
        raise RuntimeError("Downloaded payload checksum did not match")
    if compress:
        safe_extract(payload, destination, bool(state.get("overwrite")))
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not state.get("overwrite"):
            raise FileExistsError(f"Destination already exists: {destination}")
        os.replace(payload, destination)
    api._remote_shell(session, f"rm -rf {shlex.quote(remote_stage)}", timeout=120)
    state["sha256"] = digest.hexdigest()


def run_worker(api: Any, transfer_id: str) -> None:
    state = load_state(api, transfer_id)
    cancel_path = directory(api, transfer_id) / "cancel.requested"
    try:
        progress(api, state, status="running")
        if state["direction"] == "upload":
            upload(api, state)
        else:
            download(api, state)
        progress(
            api,
            state,
            chunks_done=int(state.get("chunks_total", 0)),
            bytes_done=int(state.get("bytes_total", 0)),
            status="completed",
        )
        root = directory(api, transfer_id)
        shutil.rmtree(root / "parts", ignore_errors=True)
        shutil.rmtree(root / "extract", ignore_errors=True)
        (root / "payload").unlink(missing_ok=True)
        (root / "payload.tar.gz").unlink(missing_ok=True)
        cancel_path.unlink(missing_ok=True)
    except InterruptedError as exc:
        state["error"] = api._redact(str(exc))
        progress(api, state, status="cancelled")
    except Exception as exc:
        state["error"] = api._redact(str(exc))
        progress(api, state, status="failed")


def spawn(api: Any, state: dict[str, Any]) -> dict[str, Any]:
    transfer_id = str(state["transfer_id"])
    (directory(api, transfer_id) / "cancel.requested").unlink(missing_ok=True)
    state.update({"status": "starting", "updated_at": int(time.time())})
    save_state(api, state)
    process = process_utils.background_popen(
        [
            sys.executable,
            str(Path(api.__file__).resolve()),
            "--run-transfer",
            transfer_id,
        ],
        windowless_python_entrypoint=True,
    )
    state["worker_pid"] = process.pid
    save_state(api, state)
    return state


def list_states(api: Any, limit: int) -> list[dict[str, Any]]:
    root = api.TRANSFERS_ROOT / "managed"
    if not root.exists():
        return []
    states = []
    for path in root.glob("*/state.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                states.append(value)
        except (OSError, ValueError):
            continue
    states.sort(key=lambda item: int(item.get("updated_at", 0)), reverse=True)
    return states[: max(1, min(limit, 200))]
