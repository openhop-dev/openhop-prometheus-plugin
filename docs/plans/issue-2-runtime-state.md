# Runtime-state storage investigation

Tracking issue: [#2 — config.json written very often](https://github.com/openhop-dev/openhop-prometheus-plugin/issues/2).

**Status:** implemented and verified in this branch, including an installed-wheel native upgrade, native/systemd appliance reboot recovery, and a disposable Docker container restart. Release, catalogue publication and merging are separate from these checks.

## Goal

Remove routine persistent-storage writes caused by dashboard runtime snapshots and successful collection logs, while delivering the repair as an ordinary plugin-wheel upgrade that preserves existing settings.

## Confirmed baseline behavior (v1.0.0)

- `main.py` publishes a runtime snapshot after each collection, with additional error and shutdown publications.
- `runtime_snapshot.py` merges that snapshot into `_runtime` in `data/config.json`, then replaces the configuration file even when user settings have not changed.
- `ui/app.js` currently obtains runtime status from that `_runtime` field through the protected settings API.
- `CollectionManager.collect_once()` logs every successful collector at INFO; Repeater's supervisor captures plugin output in its persistent log.
- An isolated subprocess run of source revision `7291034cbdd5c8102422fe65a52b91060127602f` observed three configuration-file replacements over three collections, with unchanged settings. The test used a five-second interval, loopback HTTP and the Repeater collector disabled. Both `/metrics` and `/healthz` returned HTTP 200. This verifies filesystem writes, not physical SD-card damage or the reporter's device.

## Implemented plugin-only approach

Settings remain in persistent `config.json`, and dashboard snapshots use private, verified RAM-backed storage. A stable `data/runtime.json` symlink lets the bundled UI retrieve snapshots through Repeater's existing authenticated `/api/plugins/runtime` endpoint without introducing a new listener or host IPC protocol. The minimum stable Repeater version is 1.1.2; native manager/plugin processes must share the tmpfs and service UID.

The Repeater runtime reader follows the symlink. Verification now includes real HTTP → IPC → storage dispatch, the versioned wheel, native upload/upgrade, live authenticated dashboard/actions, and a non-root container manager/plugin restart with cleared tmpfs.

`runtime_store.py` verifies tmpfs through the opened directory descriptor and checks private ownership/permissions. Per-instance locking prevents simultaneous publishers; only the RAM snapshot is atomically replaced. A five-second heartbeat updates cached status even during a long collection, with a thirty-second expiry for crash detection in the UI. Missing/unavailable RAM is visible and never falls back to a persistent runtime file; metrics collection continues. Tmpfs can still use swap, so this is not a guarantee of zero physical operating-system I/O.

Routine successful-collection detail is opt-in DEBUG. First failures, recoveries and five-minute failure reminders remain visible, with bounded retained logging state and diagnostic text. The durable completed-action ledger remains event-driven. Legacy `config._runtime` is ignored without a startup migration write; an intentional UI configuration save removes it.

## Implementation and release checklist

- [x] Configuration is unchanged during collection, status polling, scrapes, errors and shutdown.
- [x] Volatile publication, safe initialization, atomic concurrent reads, private permissions, stable links and ownership/descriptor cleanup are covered.
- [x] The UI reads settings and runtime separately, retains dashboard authentication and protects unsaved edits and queued actions from delayed/out-of-order responses.
- [x] Minimum stable Repeater 1.1.2 and actual native/container UID/mount compatibility are established.
- [x] Stopped, crashed and restarted behavior is covered, including real Docker container restart with cleared RAM and reboot of the native/systemd LXC appliance. The underlying Proxmox host was not rebooted.
- [x] Unavailable tmpfs, permission failures and unsafe paths have no persistent-file fallback; metrics remain available.
- [x] Routine INFO success logging is removed and repeated-error/recovery behavior is tested.
- [x] The complete suite passes on Python 3.10 and 3.11, with no skipped browser tests; a clean installed wheel passes the complete suite outside the source checkout.
- [x] The real multipart Plugin Manager upload upgrades published v1.0.0 to the candidate without resetting settings, API token, catalogue provenance or completed actions.
- [x] Native stop/start, live Test/Refresh actions and container lifecycle pass; recurring plugin file/log writes were observed through several real collections and polls.

## Verification record

- **126 tests passed** on Python 3.10, **126 passed** on Python 3.11, and **126 passed** again against the noneditable installed wheel. All runs include Chromium browser tests and have zero skips. Independent focused review found no introduced blockers.
- The source retains release version 1.0.0 pending a release decision. Only an isolated source export uses local test version **1.0.0+pr3.2**, consistently across runtime, metadata, manifest, UI, HTTP banner and collector User-Agent. This is not a published release.
- Candidate wheel SHA256: `10261f00187afd6b7a1fc18e85b29dab11a26a6c10010900260d48bbdf7ad934`. ZIP integrity, all 30 RECORD entries, 16 packaged Python files and all eight bundled plugin resources were verified against that build input.
- A native Repeater 1.1.5.dev231/Python 3.11 installation was first verified byte-for-byte against the public v1.0.0 wheel. Following the actual multipart upload, installed Python/UI files matched the candidate, with no missing, differing or extra source/UI files. That initial upgrade did not restart the Repeater daemon; the subsequent explicitly authorized appliance reboot tested automatic recovery of the daemon and plugin manager.
- The live exporter produced **344 metric samples**, with both collectors healthy. The actual same-origin Bearer-authenticated dashboard passed mobile/light and desktop/dark checks, all five tabs, unsaved edits and Reload. Anonymous settings/runtime requests remained HTTP 401.
- Over **105.86 seconds**, collector attempts advanced from 7 to 11 while configuration, action state, persistent-link inode/mtime and default log inode/mtime/size remained unchanged. The probe made 24 runtime polls, 24 settings reads, eight scrapes and eight health reads. Plugin `/proc` I/O reported a storage `write_bytes` delta of zero while RAM publications continued. This is plugin/process evidence, not a device-wide block-I/O or SD-endurance test.
- Endpoint Test, Repeater Test and manual Refresh each completed successfully through the real UI. Other completed actions did not replay. A native plugin stop/start retained all results and settings; the UI showed stopped and recovered to healthy.
- A disposable Repeater container ran the candidate under UID 15888 through the real manager/IPC path. Persistent settings, action ledger, symlink and default log remained unchanged while collections/scrapes advanced. Actual container restart cleared `/dev/shm`, retained the persistent data and link, and restored healthy publication automatically.
- The native/systemd LXC appliance was then rebooted once. Its boot ID and PID1 start time changed, both enabled units started automatically, and the pre-reboot volatile marker disappeared. The plugin recreated a private, fresh RAM snapshot through the unchanged persistent symlink. Configuration/token and action-ledger bytes and metadata survived unchanged; completed actions did not replay. No manual service starts or additional reboot were needed.
- During a further **105.39-second post-boot observation**, both collectors advanced from 12 to 15 attempts while configuration, action state, symlink metadata and default logs remained unchanged. Authenticated settings/runtime access, anonymous HTTP 401 responses, `/metrics` and `/healthz` passed. Metric cardinality is data-dependent and was lower after restart; both collectors remained healthy. The radio reported `ok` and its counters contained post-boot reception/forwarding, but no additional packets arrived during this particular observation window.
- LXC reboot verification uses changed boot identity, PID1 start time, unit activation times and cleared RAM, not a requirement that the PID namespace inode change: this appliance retained that namespace across its guest reboot. An initial overly strict verification waiter timed out despite successful automatic recovery; the corrected read-only checks confirmed the actual reboot without issuing another.

## Remaining publication boundary

No tag, release, catalogue update or merge is implied by this implementation. Native/systemd appliance and Docker restart recovery were exercised; a physical Raspberry Pi power cycle and SD-card endurance were not measured. A private rollback backup was saved, and its configuration/action-ledger copies were verified; restoration was not exercised.

## Original tracking commit

The initial documentation-only commit recorded the failure mechanism, proposal and acceptance criteria. The implementation and evidence above were added afterward; the earlier investigation results remain as baseline evidence.
