# Signal Quality and Sessions Acceptance

Task 13 documents the regression, deployment, and real-Muse acceptance gate for the signal-quality and replayable-sessions milestone.

## Scope

This milestone is validated in three parts:

- **Completed safe local/static verification**: repository-only checks that can run without hardware, live services, SSH, or deployment.
- **Completed server-side deployment/runtime verification**: controlled deployment and loopback-only service checks on `dn-dev-01`.
- **Completed Windows/physical verification**: live Muse quality transitions, SSH/network recovery, and Windows ONLOGON reboot recovery.

This document records the completed local, server-side, Windows, and physical verification.

## Completed safe local/static verification

The Task 13 controller completed the safe local-only checks with these results:

- `.venv/bin/pytest -q`: 182 passed with one pre-existing third-party deprecation warning.
- `python3 -m compileall -q services sources pipeline scripts sim.py ui.py`: passed.
- `git diff --check 632783a..HEAD`: passed for the cumulative Task 13 range.
- `git status --porcelain`: empty after the final commit.
- PowerShell parser validation for `scripts/install_windows.ps1` using Docker: passed.

  ```bash
  docker run --rm -v "$PWD:/work:ro" mcr.microsoft.com/powershell:latest \
    pwsh -NoProfile -Command '[System.Management.Automation.Language.Parser]::ParseFile("/work/scripts/install_windows.ps1",[ref]$null,[ref]$null) | Out-Null'
  ```

If any of these fail, fix the repository before moving to the live gate.

## Completed server-side deployment/runtime verification

On 2026-08-30, commit `f7022c7e75b21def5b5eef55b06f219a94e3a545` was deployed to `dn-dev-01` with this evidence:

- The receiver and dashboard were stopped before backup and code replacement, then returned to `enabled` and `active`.
- `/opt/backups/senselayer/20260830-100943` contains the prior deployed code and the `history.db`, `history.db-wal`, and `history.db-shm` trio; every recorded SHA-256 checksum passed verification.
- SQLite `PRAGMA integrity_check` remained `ok`; all 980 pre-existing sample rows remained present after migration.
- The `sessions` and `session_events` tables were added without recreating the existing database.
- Receiver `/ready`, `/health`, and dashboard `/_stcore/health` returned success.
- Listeners remained bound only to `127.0.0.1:8787` and `127.0.0.1:8501`.
- The dashboard rendered `Live data unavailable` while the collector was stale and did not fabricate simulated live data; the production unit explicitly sets `SENSELAYER_ALLOW_SIMULATION=false`.
- A named deployment-smoke session completed start, event marker, stop, event CSV, and sample CSV-header checks. No active smoke session was left behind.
- `pip check` reported no broken requirements and recent service logs contained no warnings.

## Completed Windows / physical acceptance

On 2026-08-30, the Windows edge package from commit `dbd5dab7071630f95bf5ee4458267d23667e58c5` was installed and verified:

- Package SHA-256: `edf85f9fbf5d45b489a96e67ca3da0c60a7bf10df31cbb2f6ab8494cd4f97b58`.
- Installed `pipeline/eeg_quality.py` SHA-256: `c2718745a17b6c3cc3553fb0c4c9efd5ea67671279e78cf1a5b64fb400f09929`.
- The `SenseLayerMuse2` task reported `Running`.
- The installer disables the Task Scheduler battery restrictions that had left the task `Queued`; targeted tests and PowerShell parser validation passed.

The real-Muse protocol completed under session `72525cbb-efb3-4ddf-b965-bdc08672fbd0` (`physical-acceptance-v4-2026-08-30`):

1. **Good contact, 30 seconds:** 31/31 samples were `good`; median quality was `0.967`; no artifact flags were present.
2. **TP10 removed, 20 seconds:** 20/21 samples were `bad`; median TP10 quality fell to `0.649`; `extreme_amplitude`, `abrupt_steps`, and `channel_outlier` flags appeared.
3. **TP10 restored, 30 seconds:** median aggregate quality recovered to `0.936`; median TP10 quality recovered to `0.893`; contact-loss flags returned to zero. Twenty samples were `good`; eleven were conservatively blocked only by `high_frequency_noise`.
4. The completed session export contains 190 ordered sample rows and both quality transitions. CSV SHA-256: `9b707803dcc0411c8014188fb72010869ece3d14c441a47832e1bdb655968b0d`.
5. Captured physical `bad` samples exercise the production `bad` label. `ui.should_show_derived_state("bad")` is false, and the production render regression verifies both State and Focus are replaced with `Suppressed: insufficient signal quality`; the focused dashboard/client suite passed 66 tests. The CSV intentionally contains no derived State/Focus columns.
6. The session stopped as `completed`, and the active-session count returned to zero.

Runtime recovery also passed:

- **Network and SSH-tunnel reconnect:** the receiver became `stale` during the deliberate Wi-Fi interruption, then returned to sustained `fresh` automatically without restarting the task. The collector posts only to `http://127.0.0.1:18787`; `senselayer_supervisor.py` creates that local endpoint exclusively with `ssh -L 127.0.0.1:18787:127.0.0.1:8787` and waits for its `/health` response before starting the collector. Resumed remote ingestion therefore verifies recovery of the supervised SSH-forwarded data path, not merely local collector activity.
- **Windows reboot / ONLOGON:** the reboot produced a 101.7-second sample gap; after login, ingestion returned to `fresh` without manually starting the task or installer.
- These runs also exercised collector supervision, Muse reconnect, network reconnect, and Windows ONLOGON startup.

## Rollback guidance

If the milestone must be reverted, use the following order:

1. Stop the receiver and every process that can write `data/history.db` before replacing code or taking the database backup.
2. Back up `data/history.db`, `data/history.db-wal`, and `data/history.db-shm` together.
3. Restore the prior Git commit and the matching venv-compatible requirements.
4. Restart services and verify `/ready`, `/health`, and loopback bindings.
5. Never roll back by deleting or recreating the migrated database.

## Acceptance intent

The milestone is acceptable only when:

- the safe local/static checks pass,
- the live runtime gates pass in the deployed environment,
- and the rollback path is known to preserve the migrated SQLite database.
