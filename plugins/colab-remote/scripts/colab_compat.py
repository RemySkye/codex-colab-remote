"""Compatibility entry point for native Colab options omitted by CLI 0.6.0."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import fcntl
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jupyter_kernel_client
from colab_cli import client
from colab_cli.cli import main
from colab_cli.runtime import ColabRuntime


LANGUAGE_KERNELS = {"python": "python3", "r": "ir", "julia": "julia"}
WORKDIR_PRELUDE = (
    "import os; os.makedirs('/content', exist_ok=True); os.chdir('/content')"
)
WORKDIR_PRELUDES = {
    "python": WORKDIR_PRELUDE,
    "r": "dir.create('/content', recursive=TRUE, showWarnings=FALSE); setwd('/content')",
    "julia": 'mkpath("/content"); cd("/content")',
}
NATIVE_KERNELS_PATH = (
    Path.home() / ".config" / "colab-cli" / "colab-remote-native-kernels.json"
)
NATIVE_KERNELS_LOCK = NATIVE_KERNELS_PATH.with_suffix(".lock")


def _selected_language() -> str | None:
    language = os.environ.get("COLAB_REMOTE_LANGUAGE", "").strip().lower()
    if not language:
        return None
    if language not in LANGUAGE_KERNELS:
        raise ValueError("COLAB_REMOTE_LANGUAGE must be python, r, or julia")
    return language


def _load_native_kernels_unlocked() -> dict[str, dict[str, str]]:
    try:
        value = json.loads(NATIVE_KERNELS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key): {str(language): str(kernel_id) for language, kernel_id in row.items()}
        for key, row in value.items()
        if isinstance(row, dict)
    }


@contextmanager
def _native_kernels_lock():
    NATIVE_KERNELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NATIVE_KERNELS_LOCK.open("a", encoding="utf-8") as handle:
        try:
            os.chmod(NATIVE_KERNELS_LOCK, 0o600)
        except OSError:
            pass
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _load_native_kernels() -> dict[str, dict[str, str]]:
    with _native_kernels_lock():
        return _load_native_kernels_unlocked()


def _save_native_kernel(runtime_key: str, language: str, kernel_id: str) -> None:
    with _native_kernels_lock():
        state = _load_native_kernels_unlocked()
        state.setdefault(runtime_key, {})[language] = kernel_id
        temporary = NATIVE_KERNELS_PATH.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, NATIVE_KERNELS_PATH)
        try:
            os.chmod(NATIVE_KERNELS_PATH, 0o600)
        except OSError:
            pass


_language = _selected_language()
_kernel_name = LANGUAGE_KERNELS.get(_language or "")


_original_build_assign_url = client.Client._build_assign_url


def _build_assign_url(self, notebook_hash, variant=None, accelerator=None):
    url = _original_build_assign_url(self, notebook_hash, variant, accelerator)
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    shape = os.environ.get("COLAB_REMOTE_MACHINE_SHAPE", "")
    version = os.environ.get("COLAB_REMOTE_RUNTIME_VERSION", "")
    if shape:
        if shape != "hm":
            raise ValueError("COLAB_REMOTE_MACHINE_SHAPE must be 'hm' when set")
        query["shape"] = shape
    if version:
        if not re.fullmatch(r"20\d{2}\.\d{2}", version):
            raise ValueError("COLAB_REMOTE_RUNTIME_VERSION must use YYYY.MM")
        query["runtime_version_label"] = version
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


client.Client._build_assign_url = _build_assign_url


if _language:
    _original_runtime_init = ColabRuntime.__init__
    _original_execute_code = ColabRuntime.execute_code
    _original_kernel_start = jupyter_kernel_client.KernelClient.start

    def _runtime_init(self, url, token, *args, **kwargs):
        if _language != "python":
            runtime_key = hashlib.sha256(url.encode()).hexdigest()
            saved_kernel = _load_native_kernels().get(runtime_key, {}).get(_language)
            kwargs["kernel_id"] = saved_kernel
            kwargs["session_id"] = None
            kwargs["on_kernel_started"] = lambda kernel_id: _save_native_kernel(
                runtime_key, _language, kernel_id
            )
            kwargs["on_session_started"] = None
        _original_runtime_init(self, url, token, *args, **kwargs)

    def _kernel_start(self, name="python3", path=None, timeout=10.0):
        return _original_kernel_start(
            self, name=_kernel_name, path=path, timeout=timeout
        )

    def _execute_code(self, code, *args, **kwargs):
        if code == WORKDIR_PRELUDE:
            code = WORKDIR_PRELUDES[_language]
        return _original_execute_code(self, code, *args, **kwargs)

    ColabRuntime.__init__ = _runtime_init
    ColabRuntime.execute_code = _execute_code
    jupyter_kernel_client.KernelClient.start = _kernel_start

if __name__ == "__main__":
    main()
