# Architecture

## System overview

`motiondetector` is a local desktop service with three concurrent runtime loops and one shared state boundary.

### Runtime components

1. **Selector UI (Qt / PySide6)**
   - Transparent always-on-top region selection overlay.
   - Grid visualization and tile toggle interaction.
   - Runtime UI setting synchronization against server APIs.

2. **Analyzer loop**
   - Capture selected region frames via `MSS`.
   - Compute diff-based instant + smoothed metrics.
   - Resolve detector state (motion + optional audio context).
   - Publish normalized payloads and optional recordings.

3. **HTTP server (FastAPI/Uvicorn)**
   - Exposes status, history, tile mask, UI settings, and quit endpoint.
   - Serves static dashboard assets.

4. **Shared state (`StatusStore`)**
   - Thread-safe latest payload and rolling history.
   - Tile disable mask and runtime UI settings.
   - Quit coordination flag.

## Threading model

- Main thread: Qt event loop + overlay interaction.
- Monitor thread: capture + analysis loop.
- Server thread: API serving loop.

Synchronization primitives:
- store-internal lock for mutable shared state.
- event/flag-based shutdown coordination.

## Data contracts

### Payload principles
- Published values are normalized for stable client consumption.
- Tile values are row-major and consistent with configured grid shape.
- Disabled tiles are represented both as indices and masked `null` values.
- Error states are surfaced in payloads rather than silently suppressing failures.

## Key design choices

- Thin API routes; business state handled in shared store/services.
- Overlay geometry conversion is explicit to avoid DPI mismatch.
- Analyzer emits continuous payloads to support polling clients and dashboards.
- Optional synthetic test-data mode supports repeatable tuning and regression checks.

## Risks and future improvements

- Add schema-level payload tests to lock API contracts.
- Add explicit analyzer timing telemetry for capture/processing performance.
- Consider adaptive poll intervals for lower idle overhead.
- Continue keeping local-loopback defaults for safer deployments.
