"""Tests for options exit target persistence."""

import exit_monitor as exit_monitor_module
from exit_monitor import ExitTarget, _load_targets, register_exit_target, remove_exit_target


def _sample_target() -> ExitTarget:
    return ExitTarget(
        contract_symbol="AAPL260417C00190000",
        underlying="AAPL",
        qty=1,
        entry_price=4.25,
        take_profit_price=5.10,
        stop_loss_price=3.40,
        correlation_id="corr-123",
    )


def test_register_and_remove_exit_target_local_file(tmp_path, monkeypatch):
    targets_file = tmp_path / "targets.json"
    lock_file = tmp_path / "targets.lock"

    monkeypatch.setattr(exit_monitor_module, "_use_blob_store", lambda: False)
    monkeypatch.setattr(exit_monitor_module, "_TARGETS_FILE", targets_file)
    monkeypatch.setattr(exit_monitor_module, "_LOCK_FILE", lock_file)

    target = _sample_target()
    register_exit_target(target)

    stored = _load_targets()
    assert target.contract_symbol in stored
    assert stored[target.contract_symbol]["take_profit_price"] == target.take_profit_price

    remove_exit_target(target.contract_symbol)
    assert _load_targets() == {}


class _FakeDownload:
    def __init__(self, payload: str):
        self._payload = payload

    def readall(self):
        return self._payload


class _FakeBlobClient:
    def __init__(self, store: dict[str, str], name: str):
        self._store = store
        self._name = name

    def upload_blob(self, payload, overwrite=False):
        if not overwrite and self._name in self._store:
            raise RuntimeError("blob exists")
        self._store[self._name] = payload


class _FakeContainerClient:
    def __init__(self):
        self._store: dict[str, str] = {}

    def create_container(self):
        return None

    def upload_blob(self, name: str, payload, overwrite=False):
        if not overwrite and name in self._store:
            raise RuntimeError("blob exists")
        self._store[name] = payload

    def get_blob_client(self, name: str):
        return _FakeBlobClient(self._store, name)

    def list_blobs(self):
        return [type("Blob", (), {"name": name}) for name in self._store]

    def download_blob(self, name: str):
        return _FakeDownload(self._store[name])

    def delete_blob(self, name: str):
        self._store.pop(name, None)


def test_register_and_remove_exit_target_blob_store(monkeypatch):
    fake_container = _FakeContainerClient()

    monkeypatch.setattr(exit_monitor_module, "_use_blob_store", lambda: True)
    monkeypatch.setattr(exit_monitor_module, "_container_client", lambda: fake_container)

    target = _sample_target()
    register_exit_target(target)

    stored = _load_targets()
    assert target.contract_symbol in stored
    assert stored[target.contract_symbol]["underlying"] == "AAPL"

    remove_exit_target(target.contract_symbol)
    assert _load_targets() == {}
