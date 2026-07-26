"""Write the engine's OpenAPI document to packages/contracts/openapi.json.

Run after changing any route or request model, then regenerate the TypeScript:

    apps/engine/.venv/bin/python apps/engine/scripts/export_openapi.py
    npm run generate --workspace=@studio/contracts

CI runs this with `--check` and fails when the committed schema is stale, so a new
endpoint cannot ship without the types that describe it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Importing the app must not need a database — this runs in CI before any service.
os.environ.setdefault("STUDIO_PERSIST", "false")

ENGINE = Path(__file__).resolve().parents[1]
REPO = ENGINE.parents[1]
TARGET = REPO / "packages" / "contracts" / "openapi.json"

# Runnable from anywhere, including the repo root, without an editable install.
sys.path.insert(0, str(ENGINE))


def main() -> int:
    from engine.main import app

    # sort_keys so the file is stable across runs; without it dict ordering makes
    # every export a diff and `--check` becomes noise.
    document = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"

    if "--check" in sys.argv:
        if not TARGET.exists():
            print(f"{TARGET} is missing — run this script without --check", file=sys.stderr)
            return 1
        if TARGET.read_text() != document:
            print(
                f"{TARGET.relative_to(REPO)} is stale.\n"
                "Run: apps/engine/.venv/bin/python apps/engine/scripts/export_openapi.py\n"
                "Then: npm run generate --workspace=@studio/contracts",
                file=sys.stderr,
            )
            return 1
        print("openapi.json is up to date")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(document)
    print(f"wrote {TARGET.relative_to(REPO)} ({len(document):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
