$ErrorActionPreference = "Stop"
$BASE = "$env:USERPROFILE\muse-edge"
$VENV = "$BASE\.venv"

function Ensure-Python {
  try {
    py -3.11 -V | Out-Null
    return "py -3.11"
  } catch {}
  try {
    py -3 -V | Out-Null
    return "py -3"
  } catch {}
  throw "Python Launcher nincs telepítve. Telepítsd: winget install -e --id Python.Python.3.11"
}

$PYLAUNCH = Ensure-Python

New-Item -ItemType Directory -Force -Path $BASE | Out-Null

& cmd /c "$PYLAUNCH -m venv \"$VENV\""
if (!(Test-Path "$VENV\Scripts\python.exe")) { throw "Venv nem jött létre: $VENV" }

$PY = "$VENV\Scripts\python.exe"
& $PY -m pip install --upgrade pip
& $PY -m pip install muselsl pylsl numpy requests

$collector = @'
import argparse, json, sys, time
import numpy as np, requests
from pylsl import StreamInlet, resolve_byprop

def band_powers(window, fs=256):
    x = window - np.mean(window)
    fft_vals = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / fs)
    def p(lo, hi):
        m = np.logical_and(freqs >= lo, freqs < hi)
        return float(np.sum(fft_vals[m]))
    return {"delta": p(0.5,4), "theta": p(4,8), "alpha": p(8,13), "beta": p(13,30), "gamma": p(30,45)}

def norm(b):
    t = sum(b.values()) + 1e-9
    return {k: float(v / t) for k,v in b.items()}

def post_sample(url, payload):
    r = requests.post(url.rstrip("/")+"/sample", json=payload, timeout=5)
    r.raise_for_status()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--receiver", required=True)
    ap.add_argument("--sample-rate", type=int, default=256)
    ap.add_argument("--window-seconds", type=float, default=2.0)
    ap.add_argument("--source-name", default="muse2-edge-win11")
    args = ap.parse_args()

    streams = resolve_byprop("type", "EEG", timeout=20)
    if not streams:
        print("No EEG stream found", file=sys.stderr)
        return 2
    inlet = StreamInlet(streams[0], max_buflen=60)
    win_n = int(args.window_seconds * args.sample_rate)
    buf = []
    while True:
        try:
            sample, _ = inlet.pull_sample(timeout=2.0)
            if sample is None:
                continue
            v = float(np.mean(sample[:4]))
            buf.append(v)
            if len(buf) < win_n:
                continue
            if len(buf) > win_n:
                buf = buf[-win_n:]
            bands = norm(band_powers(np.array(buf, dtype=np.float64), fs=args.sample_rate))
            payload = {"timestamp": time.time(), "source": args.source_name, **bands, "signal_quality": 1.0}
            post_sample(args.receiver, payload)
            print(json.dumps(payload), flush=True)
        except Exception as e:
            print(f"collector error: {e}", file=sys.stderr, flush=True)
            time.sleep(2)

if __name__ == "__main__":
    raise SystemExit(main())
'@

$runner = @'
$ErrorActionPreference = "Continue"
$BASE = "$env:USERPROFILE\muse-edge"
$PY = "$BASE\.venv\Scripts\python.exe"
$MUSELSL = "$BASE\.venv\Scripts\muselsl.exe"
$RECEIVER = "http://116.203.66.80:8787"
while ($true) {
  try {
    Start-Process -FilePath $MUSELSL -ArgumentList "stream" -NoNewWindow
    Start-Sleep -Seconds 6
    & $PY "$BASE\muse2_edge_collector.py" --receiver $RECEIVER --source-name "muse2-edge-win11"
  } catch {
    Start-Sleep -Seconds 4
  }
}
'@

Set-Content -Path "$BASE\muse2_edge_collector.py" -Value $collector -Encoding UTF8
Set-Content -Path "$BASE\run_edge.ps1" -Value $runner -Encoding UTF8

schtasks /Create /SC ONLOGON /TN "Muse2EdgeCollector" /TR "powershell -ExecutionPolicy Bypass -File $BASE\run_edge.ps1" /RL HIGHEST /F | Out-Null
Write-Host "OK: edge telepítve." -ForegroundColor Green
Write-Host "Indítás most: powershell -ExecutionPolicy Bypass -File $BASE\run_edge.ps1" -ForegroundColor Yellow
