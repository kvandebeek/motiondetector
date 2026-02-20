# Developer Guide

This guide is for contributors extending `motiondetector`.

## Repository map

- `main.py` - composition root and lifecycle wiring.
- `config/` - runtime config loading, validation, and runtime patch helpers.
- `analyzer/` - capture abstraction, per-frame metrics, state resolution, recording.
- `server/` - FastAPI app, routes, static UI, state machine, shared `StatusStore`.
- `ui/` - selector overlay and synchronization utilities.
- `testdata/` - synthetic scenario engine, profiling settings, logging/summary helpers.
- `docs/` - architecture, guide, stories, backlog.

## Local setup

1. Create venv.
2. `pip install -r requirements.lock.txt`
3. Run smoke command:
   - `python main.py --help`

## Runtime flow

1. Parse CLI and load config.
2. Enable DPI awareness (Windows).
3. Start server thread.
4. Start monitor loop thread.
5. Start Qt selector UI in main thread.
6. Publish normalized payloads into `StatusStore`.
7. Shutdown on UI close or `/quit` request.

## Extension guidelines

### Analyzer changes
- Preserve `Region` semantics and tile order contract (row-major).
- Keep payload fields additive where possible.
- Avoid breaking endpoint consumers that expect disabled tiles as `null`.

### API changes
- Keep routes thin and push state logic into `StatusStore`.
- Treat `/ui/settings` compatibility alias as public behavior.
- Prefer additive payload changes over renames/removals.

### UI changes
- Keep overlay geometry and emitted region consistent.
- Be careful with DPI and monitor coordinate conversions on Windows.

## Troubleshooting checklist

- Misaligned capture region: verify DPI awareness and monitor scaling.
- Missing tile interaction: inspect `/tiles` and selector poll/sync logs.
- Flat or stale metrics: check capture backend health and `/status` error field.
- Recorder not producing clips: verify trigger state, cooldown, and output path.

## Recommended validation before commit

- `python -m compileall .`
- `python main.py --help`
- Manual endpoint check for `/status`, `/history`, `/tiles`, `/ui`
- Refresh docs when behavior or payload contracts change
