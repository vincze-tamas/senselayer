Windows 11 edge one-time setup:
1) Open PowerShell as Administrator.
2) Run:
   powershell -ExecutionPolicy Bypass -File .\install_edge_v2.ps1
3) Pair Muse 2 once in Windows Bluetooth settings.
4) Done. Task Scheduler will autostart collector on login.

Runtime behavior:
- Starts muselsl stream
- Connects to Muse via LSL
- Computes band powers
- Posts to http://116.203.66.80:8787/sample
- Auto-retry on disconnect
