# User Stories

## Overlay and selection

- As a user, I want to drag and resize a transparent always-on-top selector so I can target exactly the screen area I care about.
- As a user, I want a visible grid in the selector so I can align motion zones quickly.
- As a user, I want selector coordinates to match captured pixels under Windows scaling so analysis and visuals stay aligned.
- As a user, I want to disable noisy tiles interactively so irrelevant motion does not affect automation.

## Detection and state

- As a user, I want continuous per-frame motion analysis at configurable FPS so I can balance responsiveness and CPU usage.
- As a user, I want normalized overall and per-tile metrics so downstream consumers can apply stable thresholds.
- As a user, I want motion state categories (no/low/high activity) so I can drive automations.
- As a user, I want no-motion grace logic so transient noise does not cause rapid state flapping.
- As a user, I want optional audio context merged into state labels so motion decisions reflect media playback conditions.

## API and observability

- As a user, I want a `/status` endpoint with latest payload so scripts can react in near real-time.
- As a user, I want a `/history` endpoint so I can graph or review recent behavior.
- As a user, I want UI settings and tile masks controllable via API so external tools can coordinate runtime behavior.
- As a user, I want a lightweight dashboard so I can inspect state without building a custom client.

## Recording and test tooling

- As a user, I want optional clip recording on configured trigger states so I can capture evidence around events.
- As a user, I want recording cooldown and stop-grace controls so clips are useful and not excessively fragmented.
- As a user, I want synthetic test-data mode so I can tune thresholds and regression-check behavior without live content.

## Configuration and operations

- As a user, I want a single config file for server, capture, motion, audio, UI, and recording settings.
- As a user, I want clear startup-time config validation errors so issues are easy to fix.
- As a user, I want graceful shutdown through UI close or `/quit` so integration scripts can stop the service safely.
