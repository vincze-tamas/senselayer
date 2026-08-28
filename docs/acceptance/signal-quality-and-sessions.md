# Signal Quality and Sessions Acceptance

Task 13 documents the regression, deployment, and real-Muse acceptance gate for the signal-quality and replayable-sessions milestone.

## Scope

This milestone is validated in two parts:

- **Completed safe local/static verification**: repository-only checks that can run without hardware, live services, SSH, or deployment.
- **Not run here**: live receiver/dashboard health probes, physical Muse 2 runs, SSH tunnel checks, Windows reboot checks, and any deployment-side runtime validation.

This document records what is safe to verify locally and what remains a live gate.

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

## Not run here: live / physical acceptance gates

The following checks remain outside the safe local-only scope and must be run only in the live environment with the real deployment and Muse hardware:

- Receiver and dashboard stay enabled and active.
- Listeners remain bound only to `127.0.0.1:8787` and `127.0.0.1:8501`.
- Existing `history.db` migrates without row loss.
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
