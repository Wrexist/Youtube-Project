"""Making a credential file readable only by the account that owns it.

`chmod(0o600)` is the whole story on POSIX and almost none of it on Windows, where
the group and other bits mean nothing, Python synthesises `st_mode` as 0o666 whatever
was asked for, and what actually decides who can read a file is the NTFS ACL it
inherits from its directory.

KNOWN-ISSUES §5.9 used to argue that inheritance was good enough in practice, because
a clone under a user profile inherits an ACL that already excludes other
non-administrator users. That reasoning was wrong, and the counterexample was the
machine it was written on: a sandboxing tool had granted a service group Read &
Execute on `C:\\Users\\<user>\\Downloads` with container and object inheritance, so
every key Studio wrote underneath it was readable by two other local accounts. A
profile directory is a convention about permissions, not a guarantee — and the
tools most likely to add an inherited read ACE are exactly the ones a developer
machine collects.

So the ACL is set explicitly instead of assumed.

**Nothing here raises.** It runs after the bytes are already on disk, and the two
failure modes are not equal: a credential file with a wider ACL than intended is a
problem the warning names, while a credential file that cannot be read back is a
broken install. When the tightening cannot be confirmed it is undone.
"""

from __future__ import annotations

import getpass
import os
import stat
import subprocess
from pathlib import Path

from loguru import logger

#: Kept alongside the owner: SYSTEM and the local Administrators group. Excluding
#: them protects nothing — an administrator can take ownership of any file — and it
#: breaks backup and antivirus tooling that legitimately expects to read the volume.
_KEEP = ("*S-1-5-18", "*S-1-5-32-544")

#: icacls on a single file is instant. The timeout is only here so that a wedged
#: process cannot hang a credential write forever.
_TIMEOUT = 15


def restrict(path: Path) -> None:
    """Narrow `path` to its owner, by whatever mechanism the platform actually has."""
    if os.name != "nt":
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            # The module contract is that this never raises, and the POSIX branch
            # was the one place that could. `chmod` fails on a filesystem without
            # permission bits — an SMB or FAT mount, a container bind — and
            # `write_env` unlinks the temp file on any exception, so the credential
            # save would have failed there rather than merely being unprotected.
            logger.warning("could not restrict {}: {}", path, exc)
        return
    _restrict_windows(path)


def _restrict_windows(path: Path) -> None:
    account = _owner()
    if account is None:
        logger.warning(
            "could not identify this account, so {} keeps the permissions it "
            "inherited from its directory",
            path,
        )
        return

    argv = ["icacls", str(path), "/inheritance:r"]
    for grant in (f"{account}:(F)", *(f"{sid}:(F)" for sid in _KEEP)):
        argv += ["/grant:r", grant]

    result = _run(argv)
    if result is None:
        logger.warning("could not run icacls, so {} keeps its inherited permissions", path)
        return

    # Verified rather than trusted. `/inheritance:r` drops every inherited ACE
    # first, so a `/grant` that failed to resolve — an account name this process
    # cannot look up, a volume without ACL support — would leave the file readable
    # by nobody at all, including the app that has to load it on the next start.
    if result.returncode != 0 or not _readable(path):
        _run(["icacls", str(path), "/reset"])
        logger.warning(
            "could not restrict {} and reverted it to inherited permissions: {}",
            path,
            (result.stderr or result.stdout).strip()[:200] or f"exit {result.returncode}",
        )
        return

    logger.debug("{} is restricted to {} plus SYSTEM and Administrators", path, account)


def _owner() -> str | None:
    """This process's account, as a SID in the `*S-1-...` form icacls expects.

    A SID rather than a name because names are localised (`Administrators` is
    `Administratoren` on a German install), may contain spaces, and are ambiguous
    between a local and a domain account that share one. The login name is the
    fallback, which is only what icacls would have had to resolve anyway.
    """
    result = _run(["whoami", "/user", "/fo", "csv", "/nh"])
    if result is not None and result.returncode == 0:
        fields = [field.strip('" ') for field in result.stdout.strip().split(",")]
        if len(fields) >= 2 and fields[1].startswith("S-1-"):
            return "*" + fields[1]

    try:
        name = getpass.getuser()
    except (OSError, KeyError):
        return None
    return name or None


def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell, path is not user input
            argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _readable(path: Path) -> bool:
    try:
        with path.open("rb"):
            return True
    except OSError:
        return False
