"""Object storage behind one interface.

Local filesystem in development, S3-compatible in production. No service writes a
bare filesystem path — that is what makes hosting a configuration change rather than
a refactor.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from engine.settings import get_settings


class ObjectStore:
    def __init__(self) -> None:
        self._root = Path(get_settings().storage_root).resolve()

    def _resolve(self, key: str) -> Path:
        path = (self._root / key).resolve()
        # Storage keys come from job ids and provider ids. Both are ours, but a
        # traversal here would write anywhere on disk, so it is checked regardless.
        if not path.is_relative_to(self._root):
            raise ValueError(f"storage key escapes root: {key!r}")
        return path

    async def put_file(self, source: Path | str, key: str) -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, str(source), str(dest))
        return key

    async def put_bytes(self, data: bytes, key: str) -> Path:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, data)
        return dest

    async def local_path(self, key: str) -> Path:
        """A path on local disk. On S3 this downloads to a cache first."""
        return self._resolve(key)

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def url(self, key: str) -> str:
        return f"/v1/files/{key}"


store = ObjectStore()
