#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Dict

import numpy as np
import requests
from pylsl import StreamInlet, resolve_byprop


def band_powers(window: np.ndarray, fs: int = 256) -> Dict[str, float]:
    x = window - np.mean(window)
    fft_vals = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / fs)

    def p(lo, hi):
        m = np.logical_and(freqs >= lo, freqs < hi)
        return float(np.sum(fft_vals[m]))

    return {
        "delta": p(0.5, 4),
        "theta": p(4, 8),
        "alpha": p(8, 13),
        "beta": p(13, 30),
        "gamma": p(30, 45),
    }


def normalize_bands(bands: Dict[str, float]) -> Dict[str, float]:
    total = sum(bands.values()) + 1e-9
    return {k: float(v / total) for k, v in bands.items()}


def post_sample(url: str, payload: Dict, timeout: float = 5.0) -> None:
    r = requests.post(url.rstrip("/") + "/sample", json=payload, timeout=timeout)
    r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receiver", required=True, help="http://dn-platform-01:8787")
    ap.add_argument("--window-seconds", type=float, default=2.0)
    ap.add_argument("--sample-rate", type=int, default=256)
    ap.add_argument("--source-name", default="muse2-edge")
    args = ap.parse_args()

    print("Resolving LSL stream type=EEG ...", flush=True)
    streams = resolve_byprop("type", "EEG", timeout=20)
    if not streams:
        print("No EEG LSL stream found. Start muselsl stream first.", file=sys.stderr)
        return 2

    inlet = StreamInlet(streams[0], max_buflen=60)
    print(f"Connected to stream: {streams[0].name()}", flush=True)

    win_n = int(args.window_seconds * args.sample_rate)
    buf = []

    while True:
        try:
            sample, _ts = inlet.pull_sample(timeout=2.0)
            if sample is None:
                continue
            v = float(np.mean(sample[:4]))
            buf.append(v)
            if len(buf) < win_n:
                continue
            if len(buf) > win_n:
                buf = buf[-win_n:]

            arr = np.array(buf, dtype=np.float64)
            bands = normalize_bands(band_powers(arr, fs=args.sample_rate))
            payload = {
                "timestamp": time.time(),
                "source": args.source_name,
                **bands,
                "signal_quality": 1.0,
            }
            post_sample(args.receiver, payload)
            print(json.dumps(payload), flush=True)
        except requests.RequestException as e:
            print(f"receiver post error: {e}", file=sys.stderr, flush=True)
            time.sleep(2)
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr, flush=True)
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
