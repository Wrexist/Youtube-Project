"""Credential files are narrowed to their owner on both platforms.

The test that asserts on a real ACL cannot run in CI — the workflows are all
`ubuntu-latest` — so it is skipped everywhere except a developer's own Windows
machine. That is a weaker guarantee than the rest of the suite has, and it is
precisely why `restrict` verifies its own work at runtime rather than trusting that
icacls did what it was asked. The revert path below is tested on every platform.
"""

from __future__ import annotations

import os
import stat
import subprocess

import pytest

from engine import secretfile

windows_only = pytest.mark.skipif(os.name != "nt", reason="NTFS ACLs")
posix_only = pytest.mark.skipif(os.name == "nt", reason="st_mode is synthesised on Windows")


def _secret(tmp_path):
    path = tmp_path / "credential"
    path.write_text("sk-not-a-real-key", encoding="utf-8")
    return path


@posix_only
def test_the_mode_is_narrowed_to_the_owner(tmp_path):
    path = _secret(tmp_path)
    path.chmod(0o644)

    secretfile.restrict(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_file_is_still_readable_afterwards(tmp_path):
    """Tightening an ACL is worthless if the app locks itself out of its own key."""
    path = _secret(tmp_path)

    secretfile.restrict(path)

    assert path.read_text(encoding="utf-8") == "sk-not-a-real-key"


def test_a_missing_tool_is_a_warning_rather_than_a_failed_write(tmp_path, monkeypatch):
    """`restrict` runs after the credential is already on disk. Raising here would
    turn a permissions problem into a lost key."""
    path = _secret(tmp_path)
    monkeypatch.setattr(secretfile, "_run", lambda _argv: None)

    secretfile.restrict(path)  # must not raise

    assert path.read_text(encoding="utf-8") == "sk-not-a-real-key"


def test_a_failed_tightening_is_reverted(tmp_path, monkeypatch):
    """icacls strips the inherited ACEs before it applies the grants. A grant that
    fails halfway therefore leaves the file readable by nobody at all — including the
    engine, on its next start — so the attempt has to be undone, not merely logged.

    Driven through the Windows branch directly, because the bug it guards is not
    reachable on the platform CI runs.
    """
    path = _secret(tmp_path)
    calls: list[list[str]] = []

    def _fake(argv: list[str]):
        calls.append(argv)
        failed = "/inheritance:r" in argv
        return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "Access is denied.")

    monkeypatch.setattr(secretfile, "_run", _fake)
    monkeypatch.setattr(secretfile, "_owner", lambda: "*S-1-5-21-0-0-0-1001")

    secretfile._restrict_windows(path)

    assert ["icacls", str(path), "/reset"] in calls


def test_an_unidentifiable_account_changes_nothing(tmp_path, monkeypatch):
    """Better to leave the inherited ACL in place, and say so, than to strip it and
    grant it back to a principal we could not name."""
    path = _secret(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(secretfile, "_owner", lambda: None)
    monkeypatch.setattr(secretfile, "_run", lambda argv: calls.append(argv))

    secretfile._restrict_windows(path)

    assert calls == []


@windows_only
def test_the_acl_keeps_only_the_owner_system_and_administrators(tmp_path):
    """The finding itself: inheritance can hand a service group read on a profile
    folder, and every credential written underneath it inherits that."""
    path = _secret(tmp_path)

    secretfile.restrict(path)

    listing = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    ).stdout
    # One ACE per line, and icacls writes each as `PRINCIPAL:(FLAGS)`. The path on
    # the first line contains `:\`, never `:(`, so it does not match.
    aces = [line.strip() for line in listing.splitlines() if ":(" in line]

    assert len(aces) == 3, listing
    assert not [ace for ace in aces if "(I)" in ace], f"inherited ACEs survived:\n{listing}"
