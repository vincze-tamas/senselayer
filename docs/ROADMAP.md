# SenseLayer Roadmap

## Product direction

SenseLayer is a private Muse 2 measurement platform, not a medical device. Its next job is to produce trustworthy, replayable measurement sessions before it attempts attention, stress, emotion, or neurofeedback claims.

## Non-negotiable principles

- Preserve the loopback-only VPS services and pinned-host-key SSH tunnel.
- Keep Windows reconnect and ONLOGON autostart behavior regression-tested.
- Never present a derived mental-state label without visible signal-quality context.
- Store enough provenance to reproduce every displayed metric.
- Add schema changes through idempotent migrations; never destroy existing measurements.
- Use TDD, one Kanban card per reviewable change, and small commits.
- Require a real Muse 2 acceptance run for every milestone that changes acquisition.

## Current baseline — complete

- Muse 2 BLE discovery and LSL streaming on Windows 11.
- Supervised Muse, collector, and SSH tunnel recovery.
- Pinned SSH host key and restricted forwarding to VPS loopback services.
- FastAPI receiver with validation, SQLite WAL history, and health endpoints.
- Streamlit live dashboard.
- Windows Task Scheduler autostart verified after reboot.
- Runtime moved to `dn-dev-01`; former `dn-platform-01` runtime removed.

## Milestone 1 — Measurement integrity

**Outcome:** every feature sample carries honest, explainable quality information.

Deliverables:

- Extract reusable EEG processing from the Windows collector.
- Calculate per-channel quality from finite values, flatline detection, extreme amplitude, high-frequency contamination, and channel agreement.
- Produce aggregate quality score, quality label, and explicit artifact flags.
- Keep thresholds named, documented, and covered by synthetic-signal tests.
- Extend the receiver schema without breaking old clients.
- Display quality and artifacts prominently in the dashboard.
- Suppress derived state labels when quality is unacceptable.

Success criteria:

- Clean synthetic alpha passes with `good` quality.
- Flatline, clipping/extreme amplitude, and high-frequency-noise fixtures are rejected or flagged deterministically.
- Existing payloads remain accepted during rollout.
- A live Muse run visibly reacts when electrodes are removed and restored.

## Milestone 2 — Replayable sessions

**Outcome:** the user can start, annotate, stop, inspect, and export a bounded measurement.

Deliverables:

- Session model with UUID, name, notes, start/end time, status, source, and software version.
- At most one active session per receiver instance.
- Automatic association of incoming feature samples with the active session.
- Timestamped event markers such as `eyes_open`, `eyes_closed`, `breathing`, `task_start`, and free-text notes.
- Session list/detail APIs and dashboard controls.
- CSV export of feature samples, quality fields, and events.
- Retention behavior that never prunes samples belonging to a saved session.
- Optional raw-EEG chunk storage designed separately after feature-session stability is proven.

Success criteria:

- Start → mark events → stop works without restarting the pipeline.
- Export row count and timestamps match stored session data.
- A session remains viewable after service restart.
- Samples outside a session remain live-viewable but are clearly marked unassigned.

## Milestone 3 — Personal baseline and normalized metrics

**Outcome:** trends are interpreted relative to the user and protocol, not generic thresholds.

Deliverables:

- Guided eyes-open and eyes-closed calibration protocol.
- Baseline records linked to device, protocol, and quality criteria.
- Robust median/MAD normalization and confidence/coverage reporting.
- Baseline-relative band trends and left/right channel summaries.
- Explicit invalidation when quality coverage is insufficient.

Success criteria:

- Re-running a fixed synthetic fixture produces stable normalized values.
- A baseline cannot be completed with insufficient good-quality windows.
- Dashboard distinguishes raw relative band power from baseline-normalized metrics.

## Milestone 4 — Experiment and dashboard UX

**Outcome:** repeatable protocols can be run without touching PowerShell or the database.

Deliverables:

- Connection, Muse, stream freshness, battery-if-available, and storage status.
- Session timeline with quality overlays and event markers.
- Protocol runner for timed blocks.
- Session comparison and download UI.
- Mobile-readable local dashboard.
- Honest copy: no diagnostic or medical language.

Success criteria:

- A user can complete a protocol from the dashboard alone after login/autostart.
- Every chart identifies source, time range, quality coverage, and normalization mode.

## Milestone 5 — Windows productization and operations

**Outcome:** operation no longer depends on Task Scheduler commands or log-file inspection.

Deliverables:

- Signed or checksum-verified release artifact.
- Tray application for connection state, start/stop, logs, and dashboard launch.
- Versioned upgrades and rollback.
- Bounded logs, offline buffering, and upload retry.
- Server backup, retention, and restore drill.
- SSH key rotation and revocation procedure.

Success criteria:

- Clean install, upgrade, reconnect, reboot, uninstall, and rollback pass on real Windows 11.
- No data loss during a defined short network outage.

## Milestone 6 — Validated neurofeedback experiments

**Outcome:** only quality-gated, baseline-relative feedback is presented.

Deliverables:

- Small protocol library: relaxation, eyes-open/closed alpha, and focused task.
- Audio/visual feedback with configurable thresholds.
- Within-session and before/after summaries.
- Validation notes describing what each metric can and cannot claim.

Explicitly excluded until evidence exists:

- Medical diagnosis.
- Emotion detection.
- Universal focus/stress scores.
- ML models trained on unlabeled or artifact-heavy personal data.

## Delivery workflow

Each roadmap item becomes a Kanban card with:

- one observable outcome,
- exact acceptance criteria,
- test paths and commands,
- security/runtime impact,
- rollback notes,
- spec review and code-quality review.

Card states:

```text
Backlog → Ready → In Progress → Spec Review → Code Review → Done
```

Definition of Done:

- targeted tests pass,
- full test suite passes,
- diff and secret checks pass,
- docs/changelog updated once under `Unreleased`,
- real hardware gate completed when acquisition behavior changes,
- commit pushed and remote SHA verified.
