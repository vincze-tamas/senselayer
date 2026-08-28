# Signal Quality and Replayable Sessions Implementation Plan

> **For Hermes:** Use `software-development/subagent-driven-development` to implement this plan task-by-task. Every task requires spec review, then code-quality review.

**Goal:** Replace the placeholder quality value with explainable EEG quality metrics and add durable, annotated, exportable measurement sessions.

**Architecture:** Raw Muse samples remain on the Windows edge for initial processing. A pure Python quality module produces per-channel and aggregate metrics beside normalized band powers. The receiver owns durable session state in SQLite and associates incoming feature samples with the single active session. Streamlit talks to the receiver API; it does not write SQLite directly for session operations.

**Tech Stack:** Python 3.11, NumPy, MuseLSL/pylsl, requests, FastAPI/Pydantic, SQLite WAL, Streamlit, pandas, pytest.

---

## Scope and constraints

In scope:

- Explainable feature-window quality metrics.
- Backward-compatible receiver payload rollout.
- Durable session and event-marker records.
- Feature/session CSV export.
- Dashboard quality and session controls.
- Automated and real-Muse acceptance tests.

Not in this implementation:

- Medical-grade impedance measurement; MuseLSL does not expose a validated impedance API here.
- Raw EEG upload or archival. Design it after feature-session stability is proven.
- Baseline normalization, mental-state classifiers, or neurofeedback.
- Public listener ports, browser authentication, or cloud exposure.

User-only physical actions during acceptance:

1. Wear the Muse 2 correctly.
2. Remove and restore one electrode when prompted.
3. Power-cycle the Muse when prompted.

Hermes owns all code, deployment, logs, API checks, and rollback.

## Data contracts

### Extended sample payload

Old clients remain valid. New clients add:

```json
{
  "signal_quality": 0.84,
  "quality_label": "good",
  "channel_quality": {
    "TP9": 0.82,
    "AF7": 0.86,
    "AF8": 0.85,
    "TP10": 0.83
  },
  "artifact_flags": ["high_frequency_noise"]
}
```

Labels:

```text
good:     score >= 0.75 and no blocking artifact
marginal: score >= 0.45
bad:      score < 0.45 or a blocking artifact is present
unknown:  legacy payload without detailed quality
```

Initial deterministic checks per two-second window:

- finite sample ratio,
- flatline/near-zero variance,
- extreme amplitude ratio,
- abrupt step ratio,
- 30–45 Hz contamination ratio,
- channel agreement/outlier detection.

Thresholds are named constants and are not presented as medical impedance.

### Session model

```text
sessions
  id TEXT PRIMARY KEY                 # UUID
  name TEXT NOT NULL
  notes TEXT NOT NULL DEFAULT ''
  source TEXT NOT NULL
  started_at REAL NOT NULL
  ended_at REAL NULL
  status TEXT NOT NULL                # active|completed|aborted
  software_version TEXT NOT NULL
  created_at REAL NOT NULL
```

```text
session_events
  id INTEGER PRIMARY KEY AUTOINCREMENT
  session_id TEXT NOT NULL
  timestamp REAL NOT NULL
  kind TEXT NOT NULL
  label TEXT NOT NULL DEFAULT ''
  FOREIGN KEY(session_id) REFERENCES sessions(id)
```

The existing `samples` table gains nullable `session_id`, `quality_label`, `channel_quality_json`, and `artifact_flags_json` columns through idempotent migration checks.

## API contract

```text
POST /sessions
GET  /sessions?limit=100
GET  /sessions/{session_id}
POST /sessions/{session_id}/stop
POST /sessions/{session_id}/events
GET  /sessions/{session_id}/events
GET  /sessions/{session_id}/samples?limit=5000
GET  /sessions/{session_id}/export.csv
```

`POST /sessions` returns `409` if another active session exists. Stop is idempotent: stopping a completed session returns its existing state. Event timestamps default to receiver time but accept an explicit finite Unix timestamp.

---

### Task 1: Freeze quality behavior with synthetic fixtures

**Objective:** Define deterministic quality expectations before implementation.

**Files:**

- Create: `tests/fixtures/eeg_windows.py`
- Create: `tests/test_eeg_quality.py`

**Steps:**

1. Add fixture functions for clean 10 Hz alpha, flatline, extreme amplitude, abrupt steps, high-frequency contamination, one-channel outlier, and non-finite samples.
2. Write failing parameterized tests for label, blocking flags, score range, and per-channel keys.
3. Run:

```bash
.venv/bin/pytest tests/test_eeg_quality.py -v
```

Expected: collection/import failure because `pipeline.eeg_quality` does not exist.

4. Commit only after Task 2 makes the tests pass.

### Task 2: Implement pure EEG quality analysis

**Objective:** Add a reusable, side-effect-free quality module.

**Files:**

- Create: `pipeline/__init__.py`
- Create: `pipeline/eeg_quality.py`
- Test: `tests/test_eeg_quality.py`

**Required public API:**

```python
@dataclass(frozen=True)
class QualityResult:
    score: float
    label: str
    channel_quality: dict[str, float]
    artifact_flags: tuple[str, ...]


def analyze_quality(
    window: np.ndarray,
    sample_rate: int,
    channel_names: tuple[str, ...] = ("TP9", "AF7", "AF8", "TP10"),
) -> QualityResult:
    ...
```

**Steps:**

1. Implement named constants and small private scoring helpers.
2. Clamp all scores to `[0, 1]`; reject wrong dimensions/sample rate with `ValueError`.
3. Keep the module independent of LSL, HTTP, FastAPI, and Streamlit.
4. Run:

```bash
.venv/bin/pytest tests/test_eeg_quality.py -v
.venv/bin/pytest -q
```

Expected: all tests pass.

5. Commit:

```bash
git add pipeline/eeg_quality.py tests/fixtures/eeg_windows.py tests/test_eeg_quality.py
git commit -m "feat: add explainable EEG quality analysis"
```

### Task 3: Integrate quality into the Windows collector

**Objective:** Replace the hardcoded live `signal_quality=1.0` value.

**Files:**

- Modify: `scripts/muse2_edge_collector.py`
- Modify: `scripts/install_windows.ps1`
- Modify: `scripts/requirements-edge.txt` only if imports require packaging changes
- Modify: `tests/test_collector.py`

**Steps:**

1. Add failing collector tests that patch `analyze_quality` and assert the exact extended payload.
2. Ensure simulation emits explicit deterministic quality fields but remains distinguishable through the `-sim` source suffix.
3. Import `pipeline.eeg_quality` from the collector. In `install_windows.ps1`, create `$Base\pipeline`, then copy both `pipeline\__init__.py` and `pipeline\eeg_quality.py` there. Python adds the collector script directory (`$Base`) to `sys.path`, so the scheduled task does not depend on the repository working directory.
4. Run targeted tests and PowerShell parser validation.
5. Run the full test suite.
6. Commit:

```bash
git commit -m "feat: publish live Muse signal quality"
```

### Task 4: Introduce idempotent receiver storage migrations

**Objective:** Separate persistence from API handlers and evolve existing SQLite databases safely.

**Files:**

- Create: `services/storage.py`
- Create: `tests/test_storage.py`
- Modify: `services/receiver.py`
- Modify: `tests/test_receiver.py`

**Steps:**

1. Write failing tests that initialize both an empty database and a copy of the current legacy schema.
2. Implement `connect_database(path: Path)` and `migrate(connection)`.
3. Enable and test `PRAGMA foreign_keys=ON` on every receiver connection before adding session-event foreign keys.
4. Use `PRAGMA table_info(...)` before each `ALTER TABLE`; never drop or recreate live tables.
5. Preserve WAL and busy timeout behavior.
6. Move current sample insert/history queries behind storage functions without changing API responses.
7. Run:

```bash
.venv/bin/pytest tests/test_storage.py tests/test_receiver.py -v
.venv/bin/pytest -q
```

7. Commit:

```bash
git commit -m "refactor: add idempotent receiver storage layer"
```

### Task 5: Extend receiver sample validation and persistence

**Objective:** Accept detailed quality while keeping legacy collectors compatible.

**Files:**

- Modify: `services/receiver.py`
- Modify: `services/storage.py`
- Modify: `tests/test_receiver.py`

**Steps:**

1. Add failing tests for valid extended payloads, invalid labels, unknown channel keys, non-finite scores, oversized artifact lists, and legacy payloads.
2. Add bounded Pydantic fields. Legacy payloads map to `quality_label="unknown"`, empty channel map, and empty flags.
3. Serialize structured fields as sorted compact JSON.
4. Return the detailed fields from `/history` without breaking existing keys.
5. Run targeted and full tests.
6. Commit:

```bash
git commit -m "feat: persist detailed signal quality"
```

### Task 6: Add session lifecycle storage

**Objective:** Persist one active session and make restart behavior deterministic.

**Files:**

- Modify: `services/storage.py`
- Modify: `tests/test_storage.py`

**Required storage operations:**

```python
create_session(...)
get_active_session()
list_sessions(limit)
get_session(session_id)
complete_session(session_id, ended_at)
abort_session(session_id, ended_at)
```

**Steps:**

1. Write failing tests for create, conflict, complete, repeat-complete, restart/reopen, and unknown ID.
2. Enforce one active session using a partial unique index:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_session
ON sessions(status) WHERE status = 'active';
```

3. Use UUID strings generated in Python.
4. Run storage tests and full suite.
5. Commit:

```bash
git commit -m "feat: add durable measurement sessions"
```

### Task 7: Add session lifecycle API

**Objective:** Expose start, stop, list, and detail operations through FastAPI.

**Files:**

- Modify: `services/receiver.py`
- Modify: `tests/test_receiver.py`

**Steps:**

1. Add failing API tests for success, conflict `409`, missing `404`, validation `422`, and idempotent stop.
2. Add bounded request/response models; name max 120 characters, notes max 4000.
3. Add the four lifecycle routes defined above.
4. Run targeted and full tests.
5. Commit:

```bash
git commit -m "feat: expose session lifecycle API"
```

### Task 8: Associate samples with the active session

**Objective:** Assign every incoming sample to the session active at receiver ingest time.

**Files:**

- Modify: `services/storage.py`
- Modify: `services/receiver.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_receiver.py`

**Steps:**

1. Write failing tests for pre-session, active-session, and post-stop samples.
2. Resolve active session and insert the sample in one SQLite transaction to avoid boundary races.
3. Return `session_id` from `POST /sample` and `/history`.
4. Change pruning so rows with non-null `session_id` are not automatically deleted.
5. Run all tests.
6. Commit:

```bash
git commit -m "feat: attach incoming samples to active sessions"
```

### Task 9: Add event markers

**Objective:** Record protocol events against active or completed sessions.

**Files:**

- Modify: `services/storage.py`
- Modify: `services/receiver.py`
- Modify: `tests/test_storage.py`
- Modify: `tests/test_receiver.py`

**Steps:**

1. Add failing tests for default timestamp, explicit timestamp, ordering, missing session, and invalid kind/label.
2. Implement storage and API operations.
3. Permit markers on completed sessions only when their timestamp falls inside the session interval.
4. Run tests.
5. Commit:

```bash
git commit -m "feat: add timestamped session event markers"
```

### Task 10: Add session samples and CSV export

**Objective:** Provide bounded retrieval and deterministic export.

**Files:**

- Modify: `services/storage.py`
- Modify: `services/receiver.py`
- Modify: `tests/test_receiver.py`

**CSV columns:**

```text
session_id,timestamp,received_at,source,delta,theta,alpha,beta,gamma,
signal_quality,quality_label,channel_quality_json,artifact_flags_json
```

**Steps:**

1. Add failing tests for ordering, empty session, row count, header order, quoting, and unknown session.
2. Implement `/samples` with limit bounded to `1..5000`.
3. Implement streaming CSV export without loading unbounded data into memory.
4. Keep event retrieval on the dedicated JSON endpoint for this milestone. Do not invent a malformed multi-table CSV; add a separate `events.csv` endpoint only through a future scoped card.
5. Run tests.
6. Commit:

```bash
git commit -m "feat: export session feature data as CSV"
```

### Task 11: Add dashboard quality status

**Objective:** Make data trustworthiness visible and suppress misleading state output.

**Files:**

- Modify: `ui.py`
- Create: `tests/test_dashboard_helpers.py`

**Steps:**

1. Extract pure presentation helpers for quality color/text and state visibility.
2. Test `good`, `marginal`, `bad`, and `unknown` behavior.
3. Display aggregate quality, per-channel quality, and artifact flags above derived bands.
4. When quality is `bad` or `unknown`, replace State/Focus with `Suppressed: insufficient signal quality`.
5. Do not silently fall back to simulation when a stale live source exists; label simulation explicitly and require an environment setting to permit it in production.
6. Run tests and visually inspect the dashboard through the SSH tunnel.
7. Commit:

```bash
git commit -m "feat: gate dashboard metrics by signal quality"
```

### Task 12: Add dashboard session controls

**Objective:** Start, annotate, stop, list, and download sessions without shell access.

**Files:**

- Create: `services/client.py`
- Create: `tests/test_receiver_client.py`
- Modify: `ui.py`

**Steps:**

1. Add a small receiver HTTP client using `SENSELAYER_RECEIVER_URL`, defaulting to `http://127.0.0.1:8787` on the VPS.
2. Unit-test requests, timeouts, and error display through mocked HTTP responses.
3. Add session name/notes fields, Start/Stop buttons, standard marker buttons, free-text marker, recent session list, and CSV download.
4. Disable invalid actions based on active-session state.
5. Run tests and a browser smoke test.
6. Commit:

```bash
git commit -m "feat: add dashboard measurement session workflow"
```

### Task 13: Regression, deployment, and real-Muse acceptance

**Objective:** Prove the feature without weakening the working cutover baseline.

**Files:**

- Modify: `README.md`
- Modify: `CHANGELOG.md` under the single `Unreleased` section
- Create: `docs/acceptance/signal-quality-and-sessions.md`

**Automated checks:**

```bash
.venv/bin/pytest -q
python3 -m compileall -q services sources pipeline scripts sim.py ui.py
git diff --check
```

PowerShell parser check:

```bash
docker run --rm -v "$PWD:/work:ro" mcr.microsoft.com/powershell:latest \
  pwsh -NoProfile -Command '[System.Management.Automation.Language.Parser]::ParseFile("/work/scripts/install_windows.ps1",[ref]$null,[ref]$null) | Out-Null'
```

Runtime checks:

- Receiver/dashboard remain enabled and active.
- Listeners remain only on `127.0.0.1:8787` and `127.0.0.1:8501`.
- Existing database migrates with no row loss.
- SSH tunnel, Muse reconnect, network reconnect, and Windows reboot remain green.

Real-Muse quality protocol:

1. Record 30 seconds worn correctly.
2. Remove one electrode for 20 seconds.
3. Restore it for 30 seconds.
4. Confirm quality worsens then recovers and both transitions are present in exported session data.
5. Confirm no derived State/Focus is displayed during bad quality.

Rollback:

- Stop deployment before replacing code.
- Back up `data/history.db`, `-wal`, and `-shm` together.
- Restore the prior Git commit and venv-compatible requirements.
- Restart services and verify `/ready`, `/health`, and loopback bindings.
- Never roll back by deleting or recreating the migrated database.

Final commit:

```bash
git commit -m "docs: verify signal quality and session milestone"
```

## Kanban seed

Create one card per task, preserving task order. Initial state:

```text
Task 1  Ready
Task 2  Backlog (depends on 1)
Task 3  Backlog (depends on 2)
Task 4  Ready
Task 5  Backlog (depends on 3 and 4)
Task 6  Backlog (depends on 4)
Tasks 7-13 Backlog
```

Tasks 1–3 and 4 can run as separate workstreams, but Task 5 is the merge gate. No other parallelism is justified yet.

## Final plan review checklist

- [ ] Every referenced existing path and symbol was verified against commit `f563723`.
- [ ] New symbols are explicitly marked as new APIs.
- [ ] Legacy collector payload remains accepted during rollout.
- [ ] Database migration is idempotent and non-destructive.
- [ ] Retention cannot delete saved-session samples.
- [ ] Dashboard cannot show derived mental-state labels for bad/unknown quality.
- [ ] No public listener, weaker SSH option, or new secret is introduced.
- [ ] Hardware and reboot gates remain user-only; all technical work remains agent-owned.
