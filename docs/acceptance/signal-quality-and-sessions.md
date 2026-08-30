# Signal Quality and Sessions Acceptance

Task 13 documents the regression, deployment, and real-Muse acceptance gate for the signal-quality and replayable-sessions milestone.

## Scope

This milestone is validated in three parts:

- **Completed safe local/static verification**: repository-only checks that can run without hardware, live services, SSH, or deployment.
- **Completed server-side deployment/runtime verification**: controlled deployment and loopback-only service checks on `dn-dev-01`.
- **Not run here**: physical Muse 2 runs, SSH tunnel recovery, Windows reconnect/reboot checks, and network reconnect validation.

This document records completed local and server-side verification and what remains a Windows/physical gate.

## Completed safe local/static verification

The Task 13 controller completed the safe local-only checks with these results:

- `.venv/bin/pytest -q`: 178 passed with one pre-existing third-party deprecation warning.
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

## Not run here: Windows / physical acceptance gates

The following checks require the Windows collector and real Muse hardware:

- SSH tunnel recovery remains healthy.
- Muse reconnect remains healthy.
- Network reconnect remains healthy.
- Windows reboot auto-start remains healthy.

Run the real-Muse quality protocol exactly as follows:

1. Record 30 seconds with the Muse worn correctly.
2. Remove one electrode for 20 seconds.
3. Restore the electrode for 30 seconds.
4. Confirm quality worsens and then recovers, with both transitions present in exported session data.
5. Confirm no derived State/Focus output appears during bad quality.

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
