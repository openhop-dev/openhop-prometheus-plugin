"""Real tmpfs files and symlink/permission boundaries for the compatibility bridge."""

import json
import os
import shutil
import stat
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openhop_prometheus_plugin import runtime_store
from openhop_prometheus_plugin.runtime_store import RuntimePublisher, RuntimeStorageError


@pytest.fixture(autouse=True)
def private_ram_root(monkeypatch):
    root = Path("/dev/shm") / f"openhop-prometheus-test-{uuid.uuid4().hex}"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(runtime_store, "RAM_ROOT", root)
    yield root
    shutil.rmtree(root)


def test_stable_link_private_files_and_missing_target_recovery(tmp_path):
    with RuntimePublisher(tmp_path) as publisher:
        publisher.write({"generation": 1})
        link = publisher.link.lstat()
        assert stat.S_IMODE(publisher.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(publisher.target.stat().st_mode) == 0o600
        publisher.target.unlink()
        publisher.write({"generation": 2})
        assert json.loads(publisher.link.read_text()) == {"generation": 2}
        assert publisher.link.lstat() == link
        assert sorted(path.name for path in tmp_path.iterdir()) == ["runtime.json"]
        directory = publisher.directory
    with RuntimePublisher(tmp_path) as restarted:
        assert restarted.directory == directory
        restarted.write({"generation": 3})
        assert restarted.link.lstat() == link


def test_restart_after_cleared_ram_keeps_same_persistent_link(tmp_path):
    with RuntimePublisher(tmp_path) as publisher:
        publisher.write({"running": True})
        link = publisher.link.lstat()
        directory = publisher.directory
    shutil.rmtree(directory)  # Simulate a reboot's cleared tmpfs, not a host reboot.
    assert not (tmp_path / "runtime.json").exists()
    with RuntimePublisher(tmp_path) as publisher:
        publisher.write({"status": "starting"})
        assert publisher.link.lstat() == link
        assert json.loads(publisher.link.read_text())["status"] == "starting"


def test_instances_are_isolated_and_duplicate_owner_is_rejected(tmp_path):
    first_data, second_data = tmp_path / "a", tmp_path / "b"
    with RuntimePublisher(first_data) as first, RuntimePublisher(second_data) as second:
        first.write({"instance": "a"})
        second.write({"instance": "b"})
        assert first.target != second.target
        alias = tmp_path / "alias"
        alias.symlink_to(first_data, target_is_directory=True)
        with pytest.raises(RuntimeStorageError, match="another process"):
            RuntimePublisher(alias)
        assert json.loads(first.link.read_text()) == {"instance": "a"}
        assert json.loads(second.link.read_text()) == {"instance": "b"}


def test_non_tmpfs_never_falls_back_to_persistent_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_store, "_is_tmpfs", lambda _: False)
    with pytest.raises(RuntimeStorageError, match="not tmpfs"):
        RuntimePublisher(tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["file", "foreign_link", "directory"])
def test_unsafe_runtime_path_is_preserved_not_followed(tmp_path, kind):
    path = tmp_path / "runtime.json"
    foreign = tmp_path / "keep.json"
    foreign.write_text("preserve")
    if kind == "file":
        path.write_text("preserve")
    elif kind == "foreign_link":
        path.symlink_to(foreign)
    else:
        path.mkdir()
    before = path.lstat()
    with pytest.raises(RuntimeStorageError, match="own RAM runtime symlink"):
        RuntimePublisher(tmp_path)
    assert path.lstat() == before
    assert foreign.read_text() == "preserve"


def test_untrusted_root_symlink_is_not_followed(tmp_path, private_ram_root, monkeypatch):
    alias = tmp_path / "ram"
    alias.symlink_to(private_ram_root, target_is_directory=True)
    monkeypatch.setattr(runtime_store, "RAM_ROOT", alias)
    with pytest.raises(RuntimeStorageError, match="cannot initialize"):
        RuntimePublisher(tmp_path / "data")


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o777])
def test_private_directory_permissions_are_not_silently_fixed(tmp_path, mode):
    with RuntimePublisher(tmp_path) as publisher:
        directory = publisher.directory
    directory.chmod(mode)
    with pytest.raises(RuntimeStorageError, match="privately owned"):
        RuntimePublisher(tmp_path)
    assert stat.S_IMODE(directory.stat().st_mode) == mode


def test_lock_symlink_and_hardlink_are_rejected(tmp_path):
    with RuntimePublisher(tmp_path) as publisher:
        lock = publisher.directory / ".lock"
    lock.unlink()
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    lock.symlink_to(victim)
    with pytest.raises(RuntimeStorageError, match="cannot initialize"):
        RuntimePublisher(tmp_path)
    assert victim.read_text() == "unchanged"
    lock.unlink()
    lock.write_text("")
    lock.chmod(0o600)
    os.link(lock, lock.with_name("other"))
    with pytest.raises(RuntimeStorageError, match="hard links"):
        RuntimePublisher(tmp_path)


def test_atomic_readers_see_complete_snapshots(tmp_path):
    done, ready = threading.Event(), threading.Event()
    with RuntimePublisher(tmp_path) as publisher:
        publisher.write({"generation": -1, "value": "x" * 8192})

        def read():
            count = 0
            ready.set()
            while not done.is_set():
                value = json.loads(publisher.link.read_text())
                assert len(value["value"]) == 8192
                assert isinstance(value["generation"], int)
                count += 1
            return count

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(read)
            try:
                assert ready.wait(2)
                for generation in range(150):
                    publisher.write({"generation": generation, "value": "x" * 8192})
            finally:
                done.set()
            assert result.result(timeout=3) > 0
        assert json.loads(publisher.link.read_text())["generation"] == 149
        assert sorted(p.name for p in publisher.directory.iterdir()) == [".lock", "runtime.json"]


def test_failed_atomic_replace_retains_last_snapshot_and_cleans_temp(tmp_path, monkeypatch):
    with RuntimePublisher(tmp_path) as publisher:
        publisher.write({"generation": 1})
        original = publisher.target.read_bytes()

        def fail(*args, **kwargs):
            raise PermissionError("injected replace failure")

        monkeypatch.setattr(runtime_store.os, "replace", fail)
        with pytest.raises(PermissionError):
            publisher.write({"generation": 2})
        assert publisher.target.read_bytes() == original
        assert sorted(p.name for p in publisher.directory.iterdir()) == [".lock", "runtime.json"]


def test_replaced_directory_cannot_redirect_writer_to_disk(tmp_path):
    with RuntimePublisher(tmp_path) as publisher:
        publisher.write({"generation": 1})
        held = publisher.directory.with_name(publisher.directory.name + "-old")
        publisher.directory.rename(held)
        try:
            publisher.directory.symlink_to(tmp_path, target_is_directory=True)
            with pytest.raises(RuntimeStorageError, match="replaced"):
                publisher.write({"generation": 2})
            assert not (tmp_path / "runtime.json.tmp").exists()
            assert json.loads((held / "runtime.json").read_text()) == {"generation": 1}
        finally:
            publisher.directory.unlink()
            held.rename(publisher.directory)


def test_orphaned_snapshot_temporary_files_are_reclaimed_on_restart(tmp_path):
    with RuntimePublisher(tmp_path) as publisher:
        orphan = publisher.directory / ".snapshot-interrupted.tmp"
        orphan.write_text("partial publication")
    with RuntimePublisher(tmp_path) as publisher:
        assert not orphan.exists()
        publisher.write({"status": "starting"})


def test_oversized_snapshot_is_rejected_before_any_replacement(tmp_path):
    with RuntimePublisher(tmp_path) as publisher:
        publisher.write({"generation": 1})
        with pytest.raises(RuntimeStorageError, match="256 KiB"):
            publisher.write({"value": "x" * (runtime_store.MAX_SNAPSHOT_BYTES + 1)})
        assert json.loads(publisher.target.read_text()) == {"generation": 1}
    with pytest.raises(RuntimeStorageError, match="closed"):
        publisher.write({"generation": 2})
