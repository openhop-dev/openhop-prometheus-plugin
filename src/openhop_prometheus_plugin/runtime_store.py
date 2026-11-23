"""Private Linux tmpfs transport for Repeater's existing runtime-file API.

Only the compatibility symlink lives on persistent storage. Snapshot replacement,
locking and temporary files all live in the verified tmpfs directory.
"""

from __future__ import annotations

import ctypes

import hashlib
import json
import os
import secrets
import stat
import sys
import threading
from pathlib import Path
from typing import Any

RAM_ROOT = Path("/dev/shm")
TMPFS_MAGIC = 0x01021994
MAX_SNAPSHOT_BYTES = 256 * 1024


class RuntimeStorageError(RuntimeError):
    """Runtime publication is unavailable; never fall back to persistent files."""


def _is_tmpfs(fd: int) -> bool:
    # f_type is the first native long in Linux struct statfs on 32/64-bit hosts.
    # Use an oversized aligned buffer rather than reproducing arch-specific ABI.
    libc = ctypes.CDLL(None, use_errno=True)
    buffer = (ctypes.c_long * 64)()
    if libc.fstatfs(ctypes.c_int(fd), ctypes.byref(buffer)) != 0:
        raise OSError(ctypes.get_errno(), "cannot verify RAM filesystem")
    return buffer[0] == TMPFS_MAGIC


def _private(fd: int, *, directory: bool) -> None:
    info = os.fstat(fd)
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeStorageError(
            "RAM runtime storage must be privately owned (0700 directory, 0600 files)"
        )
    if not directory and info.st_nlink != 1:
        raise RuntimeStorageError("RAM runtime file must not have additional hard links")


class RuntimePublisher:
    """One publisher per canonical data directory, with a stable RAM target."""

    def __init__(self, data_dir: str | Path):
        if sys.platform != "linux":
            raise RuntimeStorageError("dashboard RAM runtime storage requires Linux tmpfs")
        import fcntl

        self.data_dir = Path(data_dir).resolve()
        digest = hashlib.sha256(os.fsencode(self.data_dir)).hexdigest()
        self.directory = RAM_ROOT / f"openhop-prometheus-{os.geteuid()}-{digest}"
        self.target = self.directory / "runtime.json"
        self.link = self.data_dir / "runtime.json"
        self._directory_fd: int | None = None
        self._lock_fd: int | None = None
        self._mutex = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        root_fd = None
        try:
            root_fd = os.open(RAM_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if not _is_tmpfs(root_fd):
                raise RuntimeStorageError(
                    "/dev/shm is not tmpfs; RAM runtime storage is unavailable"
                )
            try:
                os.mkdir(self.directory.name, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            self._directory_fd = os.open(
                self.directory.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            _private(self._directory_fd, directory=True)
            if not _is_tmpfs(self._directory_fd):
                raise RuntimeStorageError("runtime target is not on tmpfs")
            self._lock_fd = os.open(
                ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=self._directory_fd,
            )
            _private(self._lock_fd, directory=False)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeStorageError(
                    "another process owns this plugin's RAM runtime storage"
                ) from exc
            self._ensure_link()
            # A killed writer can leave one incomplete private RAM temporary.
            # The exclusive instance lock makes this cleanup ownership-safe.
            for name in os.listdir(self._directory_fd):
                if name.startswith(".snapshot-") and name.endswith(".tmp"):
                    os.unlink(name, dir_fd=self._directory_fd)
        except (OSError, RuntimeStorageError) as exc:
            self.close()
            if isinstance(exc, RuntimeStorageError):
                raise
            raise RuntimeStorageError(
                f"cannot initialize private /dev/shm runtime storage ({type(exc).__name__}); "
                "check shared tmpfs access and service ownership"
            ) from exc
        finally:
            if root_fd is not None:
                os.close(root_fd)

    def _ensure_link(self) -> None:
        try:
            current = self.link.lstat()
        except FileNotFoundError:
            try:
                self.link.symlink_to(self.target)
            except FileExistsError:
                self._ensure_link()
            return
        if not stat.S_ISLNK(current.st_mode) or os.readlink(self.link) != str(self.target):
            raise RuntimeStorageError(
                "data/runtime.json must be the plugin's own RAM runtime symlink"
            )
        if current.st_uid != os.geteuid():
            raise RuntimeStorageError("data/runtime.json symlink has a different owner")

    def write(self, value: dict[str, Any]) -> None:
        payload = (
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        ).encode()
        if len(payload) > MAX_SNAPSHOT_BYTES:
            raise RuntimeStorageError("runtime snapshot exceeds the 256 KiB limit")
        with self._mutex:
            fd = self._directory_fd
            if fd is None:
                raise RuntimeStorageError("RAM runtime publisher is closed")
            _private(fd, directory=True)
            # Detect unlinked/replaced directories: the host must read our inode.
            current = self.directory.stat(follow_symlinks=False)
            held = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                raise RuntimeStorageError("RAM runtime directory was replaced; restart the plugin")
            self._ensure_link()
            temporary = f".snapshot-{secrets.token_hex(12)}.tmp"
            try:
                out = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=fd,
                )
                with os.fdopen(out, "wb") as stream:
                    stream.write(payload)
                os.replace(temporary, "runtime.json", src_dir_fd=fd, dst_dir_fd=fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def close(self) -> None:
        with self._mutex:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            if self._directory_fd is not None:
                os.close(self._directory_fd)
                self._directory_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
