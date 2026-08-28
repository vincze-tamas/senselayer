# Changelog

## Unreleased

- Move the SenseLayer runtime target from `dn-platform-01` to `dn-dev-01`.
- Bind receiver and dashboard exclusively to loopback and expose them only through SSH local forwarding.
- Add a supervised Muse 2/LSL collector with automatic tunnel, BLE and stream recovery.
- Add an idempotent Windows 11 installer, pinned SSH host-key verification, Task Scheduler autostart and safe rollback.
- Add simulated edge mode, receiver validation, SQLite WAL operation and automated tests.
