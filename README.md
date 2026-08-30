# SenseLayer

Muse 2 EEG collector, private receiver and dashboard. The receiver and dashboard are intentionally loopback-only on `dn-dev-01`; Windows reaches them through a pinned-host-key SSH tunnel.

## Roadmap

- [Product roadmap](docs/ROADMAP.md)
- [Signal quality and replayable sessions implementation plan](docs/plans/2026-08-28-signal-quality-and-sessions.md)
- [Task 13 acceptance and rollback notes](docs/acceptance/signal-quality-and-sessions.md)

## Task 13 verification

Safe local-only verification for the signal-quality and sessions milestone:

- `.venv/bin/pytest -q`: 178 passed.
- `python3 -m compileall -q services sources pipeline scripts sim.py ui.py`: passed.
- `git diff --check 632783a..HEAD`: passed for the cumulative Task 13 range.
- `git status --porcelain`: empty after the final commit.
- PowerShell parser validation for `scripts/install_windows.ps1` with Docker: passed.

The controlled server-side deployment of `f7022c7` is complete: rollback backup checksums passed, all 980 pre-existing samples survived migration, services are enabled/active on loopback only, health probes passed, the fail-closed dashboard rendered without simulated data, and a named session/API/CSV smoke test passed. Physical Muse acceptance, SSH tunnel recovery, network reconnect, and Windows reboot validation remain a separate live gate.

## Rollback guidance

- Stop the receiver and every process that can write `data/history.db` before replacing code or taking the database backup.
- Back up `data/history.db`, `data/history.db-wal`, and `data/history.db-shm` together.
- Restore the prior Git commit and venv-compatible requirements.
- Restart services and verify `/ready`, `/health`, and loopback bindings.
- Never delete or recreate the migrated database during rollback.

## Security model

- Server listeners: `127.0.0.1:8787` and `127.0.0.1:8501` only.
- Windows forwards: `127.0.0.1:18787 -> server 127.0.0.1:8787` and `127.0.0.1:18501 -> server 127.0.0.1:8501`.
- SSH uses a dedicated key, strict host-key verification, keepalives and fail-closed forwarding.
- No password authentication and no public firewall rules for 8501/8787.

## Windows 11 installation — gated live step

Do **not** run this until the Windows change gate is approved.

1. Install current Windows updates, Python 3.11 and the built-in OpenSSH Client.
2. Charge Muse 2, disconnect it from phones/tablets, and enable Windows Bluetooth.
3. Copy the `scripts` directory locally.
4. In normal PowerShell, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
```

First run generates `%USERPROFILE%\.ssh\senselayer_ed25519` and deliberately stops if its public key is not authorized on `dn-dev-01`. Add the displayed `.pub` key to the dedicated server account, then rerun the same command. Reruns are safe and update the venv, scripts, config and Task Scheduler entry.

The installer pins the current dn-dev-01 ED25519 fingerprint:

```text
SHA256:mAUvRHWRLdip9jdSZqsJLdBE/v+yK3PJy1F8wc4Hrh0
```

A mismatch is a hard failure. Do not bypass it.

## Exact live Muse 2 verification

1. Press Muse power until the light is on. Keep it within one metre of the PC.
2. Remove competing phone/tablet connections. Muse supports one active BLE connection.
3. Check discovery before autostart:

```powershell
$Base = "$env:LOCALAPPDATA\SenseLayer"
& "$Base\.venv\Scripts\muselsl.exe" list
```

Expected: a Muse device name/address. If none appears, toggle Bluetooth, power-cycle Muse, then retry. Windows Bluetooth pairing is not required by every adapter; discovery by `muselsl list` is the deciding check.

4. Start the installed pipeline:

```powershell
schtasks /Run /TN SenseLayerMuse2
Start-Sleep 15
Get-Content "$env:LOCALAPPDATA\SenseLayer\senselayer.log" -Tail 80
```

Expected log sequence: `starting SSH tunnel`, `starting muselsl`, `starting collector`, `Connected to LSL stream`.

5. Verify receiver freshness through the tunnel:

```powershell
Invoke-RestMethod http://127.0.0.1:18787/health | Format-List
```

Expected: `ok=True`, `status=fresh`, `source=muse2-edge-win11`, and `age_sec` under 15.

6. Open `http://127.0.0.1:18501`. Expected source is `live`, history grows roughly once per second, and the five normalized bands move.
7. Disconnect test: power Muse off for 20 seconds. The log must show a stale/exit and a supervised retry. Power it on; `health` must return to `fresh` without manual process cleanup.
8. Tunnel test: disconnect network for 30 seconds and reconnect. The SSH process must fail and the supervisor must recreate the whole pipeline.
9. Reboot Windows and log in. The `SenseLayerMuse2` task must start automatically and health must become fresh without opening PowerShell.

## Simulated edge test

Set `simulate` to `true` in `%LOCALAPPDATA%\SenseLayer\config.json`, start the task, and verify `/health` and dashboard. Set it back to `false` before live Muse testing.

## Rollback

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_windows.ps1
```

This removes autostart but keeps files and keys for rollback. No server port needs opening.

## Developer test

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest -q
```
