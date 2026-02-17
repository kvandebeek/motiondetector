## Epic: UI (overlay / region selection)
- As a user I want to select the monitored region by dragging and resizing a transparent, always-on-top overlay window.
- As a user I want the overlay to show a guide grid so I can align detection areas visually.
- As a user I want the overlay grid to match the analyzed tiles exactly so what I see aligns with what the detector measures.
- As a user I want the app to be DPI-aware so overlay coordinates match real screen pixels when Windows scaling is enabled.
- As a user I want the app to keep running until I close the overlay or trigger quit so it behaves like a monitor service.

## Epic: Analyzer (capture + motion detection)
- As a user I want a Windows tool that detects motion in a user-defined on-screen region.
- As a user I want the capture backend to be configurable (currently MSS) so the app can work reliably on my system.
- As a user I want motion detection to run continuously at a configurable FPS so I can balance responsiveness vs CPU usage.
- As a user I want the monitored region to be split into a configurable rows×cols grid so I can measure motion per tile.
- As a user I want an overall motion score so I can react to “how much” motion is happening.
- As a user I want per-tile motion values so I can see where motion is happening within the region.
- As a user I want motion to be classified into simple states (NO_MOTION / LOW_ACTIVITY / MOTION) so I can build automations on top.
- As a user I want motion values to be normalized and smoothed over time so detection is less noisy.
- As a user I want thresholds for “no motion” and “low activity” so I can tune sensitivity.
- As a user I want to ignore a configurable inset inside the selected region during analysis so borders/UI chrome don’t create false motion.

## Epic: Payload (status schema + compatibility)
- As a user I want the monitored region coordinates included in status output so I can verify what’s being captured.
- As a user I want the grid dimensions included in status output so I can correctly interpret tile arrays.
- As a user I want the tile values provided both as an ordered list and as named keys so parsing is convenient.
- As a user I want the system to handle capture errors and still publish an error status payload so clients can detect failures.
- As a user I want status payloads to include whether data is stale and how old it is so consumers can detect freezes.
- As a user I want status payloads to include a “last change” timestamp signal so clients can detect when the scene stopped updating.

## Epic: Server (API + status store)
- As a user I want to expose the latest detection payload as JSON so other tools can consume it.
- As a user I want an endpoint that returns a rolling history of motion payloads so I can graph/inspect recent activity.
- As a user I want the app to retain history for a configurable amount of time so I can control memory usage.
- As a user I want a lightweight dashboard page so I can view motion status without writing a client.
- As a user I want a clean way to request shutdown via an API endpoint so I can stop the app remotely/local-scripted.
- As a user I want the capture loop and server to run in background threads so the UI stays responsive.
- As a user I want status storage to be thread-safe so concurrent UI/server/monitor access can’t corrupt state.

## Epic: Recorder (video clips)
- As a user I want optional video recording so I can store evidence/diagnostics around motion states.
- As a user I want recording to be enabled/disabled via config so I can run lightweight monitoring when I don’t need clips.
- As a user I want recording to trigger on a configurable state (e.g. NO_MOTION) so I can record based on my workflow.
- As a user I want recordings to have a fixed clip length so disk usage is predictable.
- As a user I want a cooldown between recordings so I don’t generate excessive clips during long events.
- As a user I want recordings written to a configurable assets directory so I can manage storage location.
- As a user I want a short “stop grace” window so recordings end cleanly without being cut off when the trigger stops.

## Epic: Config (configuration loading / validation)
- As a user I want all core settings in a single config file so setup and tuning is straightforward.
- As a user I want config validation with clear errors so I can quickly fix invalid settings.
- As a user I want to configure UI look/feel parameters (border width, grid line width) so the overlay is usable on my display.
- As a user I want to configure analysis parameters (grid size, thresholds, smoothing, inset) so detection matches my scenario.
- As a user I want to configure server and history retention settings so integrations and resource usage are predictable.
- As a user I want to configure recorder settings (trigger, clip length, cooldown, assets dir, stop grace) so recording matches my workflow.
