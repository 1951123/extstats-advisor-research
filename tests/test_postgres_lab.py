from __future__ import annotations

import json
from pathlib import Path

import pytest

import extstats_advisor_research.postgres_lab as lab
from extstats_advisor_research.postgres_lab import (
    ENCODING,
    LAB_FORMAT,
    LOCALE,
    MARKER_NAME,
    configure_command,
    role_spec,
    safe_role_directory,
    start_command,
    validate_destroy_target,
    validate_source_identity,
)


def _identity(root: Path, role: str, source_sha: str = "a" * 40) -> dict:
    spec = role_spec(role)
    role_root = root / role
    return {
        "format_version": LAB_FORMAT,
        "role": role,
        "source_repository": str(spec.source),
        "source_commit_sha": source_sha,
        "source_dirty": False,
        "install_prefix": str(role_root / "install"),
        "build_directory": str(role_root / "build"),
        "data_directory": str(role_root / "data"),
        "socket_directory": str(role_root / "socket"),
    }


def test_role_mapping_and_ports_are_fixed() -> None:
    assert role_spec("stock").port == 55432
    assert role_spec("stock").database == "extstats_stock"
    assert role_spec("patched").port == 55433
    assert role_spec("patched").database == "extstats_patched"
    assert LOCALE == "C.utf8"
    assert ENCODING == "UTF8"
    with pytest.raises(ValueError, match="unsupported"):
        role_spec("random")


def test_configure_and_start_commands_are_out_of_tree_and_local() -> None:
    stock = role_spec("stock")
    configure = configure_command(stock)
    assert configure[0] == str(stock.source / "configure")
    assert f"--prefix={stock.install}" in configure
    assert "--with-icu" in configure
    assert "--enable-debug" not in configure


def test_safe_runtime_path_rejects_arbitrary_roles_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "postgres-lab"
    root.mkdir()
    assert safe_role_directory("stock", root) == root / "stock"
    with pytest.raises(ValueError, match="only stock or patched"):
        safe_role_directory("..", root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "patched").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        safe_role_directory("patched", root)


def test_destroy_requires_marker_and_valid_identity(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "postgres-lab"
    role_root = root / "stock"
    role_root.mkdir(parents=True)
    monkeypatch.setattr(lab, "RUNTIME_ROOT", root)
    with pytest.raises(ValueError, match="marker"):
        validate_destroy_target("stock")
    (role_root / MARKER_NAME).write_text(LAB_FORMAT + "\n", encoding="utf-8")
    (role_root / "identity.json").write_text(json.dumps(_identity(root, "stock")), encoding="utf-8")
    assert validate_destroy_target("stock") == role_root


def test_destroy_rejects_malformed_identity(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "postgres-lab"
    role_root = root / "stock"
    role_root.mkdir(parents=True)
    monkeypatch.setattr(lab, "RUNTIME_ROOT", root)
    (role_root / MARKER_NAME).write_text(LAB_FORMAT + "\n", encoding="utf-8")
    (role_root / "identity.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        validate_destroy_target("stock")


def test_dirty_source_and_wrong_patched_sha_fail_closed() -> None:
    stock = role_spec("stock")
    with pytest.raises(ValueError, match="clean"):
        validate_source_identity({"source_commit_sha": "a" * 40, "source_dirty": True}, stock)
    patched = role_spec("patched")
    with pytest.raises(ValueError, match="expected pinned SHA"):
        validate_source_identity({"source_commit_sha": "a" * 40, "source_dirty": False}, patched)


def test_start_command_uses_fixed_local_socket_and_port(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "postgres-lab"
    bin_dir = root / "patched" / "install" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "pg_ctl").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "pg_ctl").chmod(0o755)
    monkeypatch.setattr(lab, "RUNTIME_ROOT", root)
    command = start_command(role_spec("patched"))
    assert "55433" in command[command.index("-o") + 1]
    assert str(root / "patched" / "socket") in command[command.index("-o") + 1]
    assert "listen_addresses=''" in command[command.index("-o") + 1]


def test_reinit_component_removal_preserves_build_and_install_and_rejects_symlink(
    tmp_path: Path,
) -> None:
    role_root = tmp_path / "stock"
    for name in ("build", "install", "data", "socket", "logs"):
        (role_root / name).mkdir(parents=True)
    (role_root / "build" / "configure.stamp").write_text("keep", encoding="utf-8")
    (role_root / "install" / "postgres").write_text("keep", encoding="utf-8")
    lab._remove_runtime_component(role_root, "data")
    assert (role_root / "build" / "configure.stamp").is_file()
    assert (role_root / "install" / "postgres").is_file()
    outside = tmp_path / "outside"
    outside.mkdir()
    (role_root / "socket").rmdir()
    (role_root / "socket").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe"):
        lab._remove_runtime_component(role_root, "socket")
