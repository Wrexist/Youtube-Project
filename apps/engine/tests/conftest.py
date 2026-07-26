"""Test configuration.

Stubs out engine modules that have external dependencies (FastAPI, arq, httpx,
moviepy, …) so the pure-logic tests can run without installing the full stack.
The stubs live here and not in the modules themselves to avoid polluting
production imports.
"""

from __future__ import annotations

import sys
import types


def _stub(name: str, **attrs: object) -> None:
    """Install a minimal module stub under *name* if not already importable."""
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


# engine.providers is a real package (engine/providers/__init__.py exists).
# Only stub the llm sub-module so stages can be imported without needing real
# API keys.  engine.providers.images is NOT stubbed — its PlaceholderProvider
# works without any credentials and the image tests test the real module.
#
# The stub has to satisfy every name the import chain pulls from it, not just the
# one the stages call: engine.main -> api.channels -> workflows.channel_launch and
# api.models both import module-level constants and classes from here.
_stub(
    "engine.providers.llm",
    for_task=lambda *_: None,
    DEFAULT_OLLAMA_URL="http://localhost:11434",
    LLM=type("LLM", (), {}),
    ProviderUnavailable=type("ProviderUnavailable", (Exception,), {}),
    probe_ollama=lambda *_a, **_kw: None,
)
