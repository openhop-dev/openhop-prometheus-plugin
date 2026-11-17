# Runtime-state storage investigation

Tracking issue: [#2 — config.json written very often](https://github.com/openhop-dev/openhop-prometheus-plugin/issues/2).

**Status:** investigation and proposed repair; no runtime fix is implemented by this document.

## Goal

Remove routine persistent-storage writes caused by dashboard runtime snapshots and successful collection logs, while delivering the repair as an ordinary plugin-wheel upgrade that preserves existing settings.

## Confirmed behavior

- `main.py` publishes a runtime snapshot after each collection, with additional error and shutdown publications.
- `runtime_snapshot.py` merges that snapshot into `_runtime` in `data/config.json`, then replaces the configuration file even when user settings have not changed.
- `ui/app.js` currently obtains runtime status from that `_runtime` field through the protected settings API.
- `CollectionManager.collect_once()` logs every successful collector at INFO; Repeater's supervisor captures plugin output in its persistent log.
- An isolated subprocess run of source revision `7291034cbdd5c8102422fe65a52b91060127602f` observed three configuration-file replacements over three collections, with unchanged settings. The test used a five-second interval, loopback HTTP and the Repeater collector disabled. Both `/metrics` and `/healthz` returned HTTP 200. This verifies filesystem writes, not physical SD-card damage or the reporter's device.

## Proposed plugin-only approach

Keep settings in persistent `config.json`, and move the dashboard snapshot to private, verified RAM-backed storage. A stable `data/runtime.json` symlink could let the bundled UI retrieve that snapshot through Repeater's existing authenticated `/api/plugins/runtime` endpoint without introducing a new network listener or requiring a new host IPC protocol.

The current Repeater runtime reader follows symlinks. A narrow test of its production reader methods confirmed that changing RAM-backed snapshots can be read through an unchanged link; complete HTTP, lifecycle, packaging and upgrade behavior remain unverified.

Implementation must replace the RAM-backed snapshot target atomically, not rewrite the persistent symlink on every collection. It must validate the backing filesystem, ownership and permissions, handle missing RAM storage visibly, and never silently fall back to periodic persistent status writes. A RAM-backed filesystem can still use swap; this is not a guarantee of zero physical storage I/O from the operating system.

Routine successful-collection detail should be opt-in DEBUG logging. Repeated failures should be bounded without losing useful startup, shutdown and health-transition diagnostics.

## Implementation and release checklist

- [ ] Add regression tests for unchanged configuration during collection, status polling and scrapes.
- [ ] Implement and verify volatile snapshot publication, safe initialization and atomic updates.
- [ ] Update the bundled UI to read settings and runtime separately while preserving authentication, unsaved settings and existing actions.
- [ ] Establish the minimum compatible Repeater version and verify manager/plugin namespace and permission compatibility.
- [ ] Exercise stopped, crashed, restarted and rebooted plugin behavior without displaying stale status as live.
- [ ] Verify unavailable RAM storage and permission failures without a persistent-write fallback.
- [ ] Remove routine INFO success messages and verify bounded repeated-error logging.
- [ ] Run the full Python and browser suites; validate the built wheel's resources and installed runtime outside the source tree.
- [ ] Exercise a real `v1.0.0` to candidate-wheel upload/upgrade with preserved settings and working dashboard, actions, metrics and health endpoints.
- [ ] Verify native and container lifecycle behavior and inspect recurring persistent-file writes before calling the repair complete.

## Scope of this initial draft

This change records the confirmed failure mechanism, proposed compatibility approach and acceptance criteria. It does not change plugin behavior, publish a release, update the catalogue or deploy anything.
