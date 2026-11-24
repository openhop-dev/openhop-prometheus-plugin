# Changelog

## [1.1.0] - 2026-11-24

### Added
- A **Log level** selector under **Settings → Plugin runtime**, with DEBUG, INFO, WARNING, ERROR and CRITICAL levels and an option to inherit the environment setting.

### Fixed
- Routine dashboard runtime updates no longer replace persistent configuration. Status is published through private RAM-backed snapshots while the existing authenticated runtime API remains available.
- Successful collections no longer create routine INFO log writes; repeated failures are bounded while first-failure and recovery diagnostics remain visible.
- Dashboard status expires after a stopped or unresponsive publisher instead of continuing to display a cached healthy state.
- Background polling and concurrent actions preserve unsaved settings and completed-request protection.

### Changed
- Dashboard runtime updates require openHop Repeater 1.1.2 or newer and Linux with a shared, writable `/dev/shm` tmpfs. Normal wheel upgrades preserve existing settings, API tokens and completed action IDs without manual mounts or service-unit changes on standard native/single-container installations.
- DEBUG logging is explicitly opt-in and may produce persistent troubleshooting logs. RAM-backed status removes recurring plugin status writes, not all operating-system storage I/O; tmpfs can use swap.

## [1.0.0] - 2026-09-23

### Added
- Prometheus metrics for Repeater packet activity, radio performance, uptime and system-health readings.
- Best-effort mesh analytics for routes, packet types, channel activity, noise floor and neighbor links.
- Background collection with cached `/metrics` and `/healthz` endpoints.
- A five-tab dashboard with light/dark themes, configurable collection settings and inline help.
