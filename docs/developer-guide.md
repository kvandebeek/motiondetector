# Developer Guide

This guide is intended for maintainers extending `motiondetector`.

## Repository map

- `main.py` — runtime composition root and lifecycle orchestration.
- `config/` — config schema and default JSON.
- `analyzer/` — frame capture, motion analysis, normalization, recording.
- `server/` — FastAPI app, static asset serving, shared status store.
- `ui/` — Qt selector overlay and synchronization helpers.
- `testdata/` — synthetic scene generator/trainer windows.
- `docs/` — architecture notes, backlog, stories.

## Runtime flow (happy path)

1. Start app (`python main.py`).
2. Load and validate config.
3. Start API server thread.
4. Start monitor loop thread.
5. Run Qt selector in main thread.
6. Monitor publishes payloads to `StatusStore`.
7. API and UI consumers read normalized payloads from store.

## Key invariants

- Grid dimensions (`grid_rows * grid_cols`) define tile vector length.
- Disabled tiles are represented as index list + `None` in tile values.
- `StatusStore` is the only mutable shared-state boundary between threads.
- Region coordinates are treated in capture backend coordinate space.

## Extending capture backends

If you add a backend beyond `MSS`:
- keep `Region` semantics unchanged,
- preserve BGRA output contract from `grab`,
- validate backend string in `ScreenCapturer.__init__`,
- document backend-specific DPI/monitor behavior.

## Extending API endpoints

- Keep endpoints thin; move logic to `StatusStore` or dedicated service modules.
- Preserve compatibility paths unless intentionally versioning API.
- Prefer additive JSON changes.

## Debugging checklist

- Region mismatch: verify DPI awareness and monitor scaling.
- No tile updates: check `/tiles` endpoint and UI poller URL.
- Flatlined status: inspect capture errors in `/status` and monitor logs.
- Missing clips: verify recording trigger state, cooldown, and output path.

## Suggested quality gates before release

- `python -m compileall .`
- smoke run of `python main.py --help`
- manual check of `/status`, `/history`, `/tiles`, `/ui`
- docs consistency update (`readme.md`, `docs/architecture.md`)
