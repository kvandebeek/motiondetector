# Architecture Review (Current)

This document summarizes the current repository architecture after the latest tag and describes strengths, risks, and practical next steps.

## 1) System overview

`motiondetector` is a Windows-focused desktop service composed of five runtime layers:

1. **UI layer (Qt / PySide6)**
   - Transparent always-on-top selector overlay.
   - Region movement/resize and tile toggling.
   - Server-synchronized UI settings (tile number visibility).

2. **Capture + analysis layer**
   - Region frame capture (`MSS`).
   - Frame differencing + tile metrics + smoothing.
   - Motion state assignment and confidence scoring.
   - Optional clip recorder state machine.

3. **State layer**
   - Thread-safe `StatusStore` as cross-thread source of truth:
     latest payload, history, disabled tiles, UI settings, quit flag.

4. **HTTP/API layer (FastAPI + Uvicorn)**
   - JSON endpoints for status/history/ui/tile control.
   - Static dashboard assets and lightweight browser UI.

5. **Composition/runtime orchestration (`main.py`)**
   - Wires all subsystems.
   - Handles startup order and cooperative shutdown.

---

## 2) Threading and lifecycle model

### Thread roles
- **Main thread**: Qt event loop and selector window.
- **Monitor thread**: capture/analysis loop.
- **Server thread**: Uvicorn/FastAPI event loop.

### Coordination primitives
- `threading.Event` quit flag for process-level shutdown intent.
- SIGINT (`Ctrl+C`) is bridged into the same quit flag so terminal shutdown and UI-close follow one path.
- `StatusStore` lock protects shared mutable state.
- callback-based region access (`get_region`) decouples monitor from UI objects.

### Assessment
- ✅ Good separation of concerns between UI, analysis, and transport.
- ✅ `StatusStore` centralization simplifies consistency across consumers.
- ⚠️ Single-lock store can become contention point if history size or route polling grows significantly.

---

## 3) Data model and contracts

### Runtime payload contract
- Analyzer emits normalized payloads with:
  - capture status
  - video metrics/state
  - grid/tile values
  - region metadata
  - errors
- Store injects disabled-tile mask and UI settings for consistent client reads.

### Assessment
- ✅ JSON shape is stable and pragmatic for UI + API consumers.
- ✅ Masking disabled tiles to `None` is explicit and safe.
- ⚠️ Contract is implicitly enforced by code/docstrings; no dedicated schema tests yet.

---

## 4) UI architecture

UI code is cleanly split under `ui/selector/`:
- geometry, paint, interaction, region emit, state/settings models.
- `SelectorWindow` acts as composition shell.

### Assessment
- ✅ Good modularization and readability.
- ✅ Poller-based synchronization keeps coupling low.
- ⚠️ Polling intervals are static; adaptive backoff could lower idle CPU/network chatter.

---

## 5) Capture and analysis architecture

- Capture abstraction exists but currently single backend (`MSS`).
- Analysis pipeline includes:
  - grayscale conversion
  - differencing
  - tile means/top-k activity
  - state classification and confidence
  - optional inset-based ROI crop

### Assessment
- ✅ Robust operational behavior (errors published instead of thread crash).
- ✅ Practical normalization and state logic suitable for automation.
- ⚠️ No explicit benchmark/telemetry hooks for tuning across machines.

---

## 6) API/server architecture

- Thin routes delegate to store.
- Backward-compatible aliases exist (`/ui/settings`, `/server/assets`).
- Graceful shutdown is signaled through store instead of abrupt server stop.

### Assessment
- ✅ Appropriate for local single-user service.
- ⚠️ No auth by design; should remain loopback-bound for safety.

---

## 7) Audio loopback architecture

- Audio meter uses `pyaudiowpatch` loopback input capture.
- Device selection supports explicit `audio.device_index` and fallback auto-select (`audio.device_substr`, then first loopback input).
- Failures are surfaced in payload (`audio.available=false`, `audio.reason=capture_failed:*`) without crashing the monitor loop.

### Assessment
- ✅ Backend aligns with Windows loopback capture behavior and avoids SoundCard API incompatibilities.
- ⚠️ Device numbering is machine-specific; production configs should pin `audio.device_index` where possible.

---

## 8) Configuration architecture

- `config/config.py` validates and normalizes JSON into immutable `AppConfig`.
- Required and optional sections are clearly separated.

### Assessment
- ✅ Startup-time failure model is clear and deterministic.
- ⚠️ Validation is procedural; a future schema test suite would improve regression safety.

---

## 9) Priority recommendations

1. Add focused unit tests for:
   - `server/state_machine.py`
   - payload normalization behavior
   - disabled tile masking and history trimming in `StatusStore`
2. Add API contract tests for `/status`, `/history`, `/tiles`, `/ui`.
3. Add lightweight performance diagnostics (capture time, analysis time, loop jitter).
4. Persist selected server/UI state (optional) for smoother restarts.
5. Keep localhost defaults in examples and docs for secure local operation.

---

## 10) Bottom line

The repository has a **solid, maintainable architecture** for a local desktop motion detector:
- clear boundaries,
- practical thread model,
- coherent status contract,
- and readable modular UI code.

Main improvements now are around **test coverage and observability**, not major redesign.
