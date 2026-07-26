# MoneyPrinterTurbo — vendored reference

**This code does not run. Nothing in `apps/engine` imports it.**

`CLAUDE.md` tells you to consult the original when the render core's behaviour is
unclear. It used to point at `C:\Users\IsacC\Downloads\MoneyPrinterTurbo-Portable-…`,
which exists on exactly one machine. This is that source, in the repo, so the
instruction works everywhere — CI, a container, another laptop, an agent.

| | |
|---|---|
| Upstream | https://github.com/harry0703/MoneyPrinterTurbo |
| Commit | `95dd03ed0255ed8a8bcefc118ab869addfaa27cc` |
| Vendored | 2026-07-26 |
| Licence | MIT — see [LICENSE](LICENSE) |

## What is here

Only `app/` (the library) plus `config.example.toml`. Deliberately **not** copied:

- `webui/` — Streamlit UI. We have `apps/web`.
- `resource/songs/` — bundled music with unclear provenance. See
  `KNOWN-ISSUES.md` §3.3. Do not publish anything scored with it.
- `resource/fonts/` — no licence files ship with them, so they are not
  redistributable from here. Point `STUDIO_SUBTITLE_FONT` at a font you have,
  or install `fonts-dejavu-core` (the engine image already does).
- `test/`, `docs/`, Dockerfiles, `main.py`, `cli.py` — no reference value.

## The files worth reading

| Upstream | Ours | Notes |
|---|---|---|
| `app/services/video.py` | `engine/render/compose.py`, `engine/services/effects.py` | Composition, clip fitting, transitions, BGM mix |
| `app/services/voice.py` | `engine/workflows/media.py` (`_synthesize`) | Edge TTS + boundary events. `_match_script_line` is the punctuation-realignment prior art behind our `_restore_punctuation` |
| `app/services/material.py` | `engine/services/stock.py` | Pexels + Pixabay search and download |
| `app/services/subtitle.py` | `engine/render/compose.py` (`transcribe`), `engine/workflows/publish.py` (`_to_srt`) | Whisper fallback, SRT emission |
| `app/services/bgm.py` | `engine/services/bgm.py` | Path allow-listing and mixing |
| `app/services/utils/video_effects.py` | `engine/services/effects.py` | Ken Burns and fades |
| `app/models/schema.py` | `engine/workflows/*` | `VideoParams` — the thing our `RenderRequest` deliberately is not |

## Rules

1. **Read it, don't import it.** Ruff, pytest and the engine Docker build all
   skip this directory. If you find yourself adding `vendor` to a Python path,
   stop — port the code into `engine/services/` instead.
2. **Don't edit it.** It is a snapshot for comparison. An edited snapshot
   compares against nothing. To move to a newer upstream, re-copy the tree and
   update the commit above in one commit that touches nothing else.
3. **Port, don't paste.** The house rules in `CLAUDE.md` still apply to anything
   that leaves this directory: no `config.app.get(...)`, no `sm.state`, no
   `VideoParams` in a response, Chinese comments translated in files you touch.
