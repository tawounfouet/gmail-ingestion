"""RC8 resource reserve guard protects the huge local Gmail baseline."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("materialize_rc8_current_candidate")


def test_disk_guard_fails_before_loading_baseline(
    module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module.shutil, "disk_usage",
        lambda _: SimpleNamespace(free=8 * 1024**3),
    )
    monkeypatch.setattr(
        module.psutil, "virtual_memory",
        lambda: SimpleNamespace(available=64 * 1024**3),
    )
    with pytest.raises(module.CandidateMaterializationError, match="disk"):
        module.require_resource_headroom(tmp_path, min_free_gib=20)


def test_ram_guard_fails_even_with_large_disk(
    module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module.shutil, "disk_usage",
        lambda _: SimpleNamespace(free=100 * 1024**3),
    )
    monkeypatch.setattr(
        module.psutil, "virtual_memory",
        lambda: SimpleNamespace(available=6 * 1024**3),
    )
    with pytest.raises(module.CandidateMaterializationError, match="RAM"):
        module.require_resource_headroom(tmp_path, min_available_ram_gib=24)


def test_resource_guard_accepts_explicit_headroom(
    module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module.shutil, "disk_usage",
        lambda _: SimpleNamespace(free=64 * 1024**3),
    )
    monkeypatch.setattr(
        module.psutil, "virtual_memory",
        lambda: SimpleNamespace(available=40 * 1024**3),
    )
    result = module.require_resource_headroom(tmp_path)
    assert result == {"free_disk_gib": 64.0, "available_ram_gib": 40.0}


def test_invalid_resource_thresholds_fail_closed(
    module: Any, tmp_path: Path,
) -> None:
    with pytest.raises(module.CandidateMaterializationError, match="Positive"):
        module.require_resource_headroom(tmp_path, min_free_gib=0)
