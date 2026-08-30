#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import deque
from typing import Dict, Iterable

import numpy as np
import requests

from pipeline.eeg_quality import DEFAULT_CHANNEL_NAMES, analyze_quality

BANDS = ("delta", "theta", "alpha", "beta", "gamma")


def band_powers(window: np.ndarray, fs: int = 256) -> Dict[str, float]:
    """Return median spectral power across EEG channels for each band."""
    arr = np.asarray(window, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    if arr.shape[0] < 2:
        raise ValueError("window must contain at least two samples")
    arr = arr - np.mean(arr, axis=0, keepdims=True)
    arr = arr * np.hanning(arr.shape[0])[:, None]
    spectra = np.abs(np.fft.rfft(arr, axis=0)) ** 2
    freqs = np.fft.rfftfreq(arr.shape[0], 1.0 / fs)

    def power(lo: float, hi: float) -> float:
        mask = np.logical_and(freqs >= lo, freqs < hi)
        channel_powers = np.sum(spectra[mask, :], axis=0)
        return float(np.median(channel_powers))

    return {
        "delta": power(0.5, 4),
        "theta": power(4, 8),
        "alpha": power(8, 13),
        "beta": power(13, 30),
        "gamma": power(30, 45),
    }


def normalize_bands(bands: Dict[str, float]) -> Dict[str, float]:
    values = {key: max(0.0, float(bands[key])) for key in BANDS}
    total = sum(values.values())
    if not math.isfinite(total) or total <= 1e-12:
        raise ValueError("invalid or empty spectral power")
    return {key: value / total for key, value in values.items()}


def post_sample(url: str, payload: Dict[str, object], timeout: float = 5.0) -> None:
    response = requests.post(url.rstrip("/") + "/sample", json=payload, timeout=timeout)
    response.raise_for_status()


def simulated_bands(index: int) -> Dict[str, float]:
    phase = index / 7.0
    raw = {
        "delta": 0.10 + 0.03 * math.sin(phase * 0.7),
        "theta": 0.18 + 0.04 * math.sin(phase * 0.9 + 0.8),
        "alpha": 0.34 + 0.08 * math.sin(phase + 1.4),
        "beta": 0.25 + 0.05 * math.sin(phase * 1.2 + 2.1),
        "gamma": 0.08 + 0.02 * math.sin(phase * 1.7 + 0.2),
    }
    return normalize_bands(raw)


def simulation_payload(
    args: argparse.Namespace, index: int, timestamp: float | None = None
) -> Dict[str, object]:
    return {
        "timestamp": time.time() if timestamp is None else timestamp,
        "source": args.source_name + "-sim",
        **simulated_bands(index),
        "signal_quality": 1.0,
        "quality_label": "good",
        "channel_quality": {name: 1.0 for name in DEFAULT_CHANNEL_NAMES},
        "artifact_flags": [],
    }


def live_payload(
    window: np.ndarray,
    source_name: str,
    sample_rate: int,
    timestamp: float | None = None,
) -> Dict[str, object]:
    values = np.asarray(window, dtype=np.float64)
    bands = normalize_bands(band_powers(values, fs=sample_rate))
    quality = analyze_quality(values, sample_rate)
    return {
        "timestamp": time.time() if timestamp is None else timestamp,
        "source": source_name,
        **bands,
        "signal_quality": quality.score,
        "quality_label": quality.label,
        "channel_quality": quality.channel_quality,
        "artifact_flags": list(quality.artifact_flags),
    }


def run_simulation(args: argparse.Namespace) -> int:
    sent = 0
    while args.max_samples <= 0 or sent < args.max_samples:
        payload = simulation_payload(args, sent)
        post_sample(args.receiver, payload, timeout=args.http_timeout)
        print(json.dumps(payload, sort_keys=True), flush=True)
        sent += 1
        if args.max_samples <= 0 or sent < args.max_samples:
            time.sleep(args.post_interval)
    return 0


def _resolve_eeg(timeout: float):
    from pylsl import resolve_byprop

    streams = resolve_byprop("type", "EEG", timeout=timeout)
    return streams[0] if streams else None


def run_lsl(args: argparse.Namespace) -> int:
    from pylsl import StreamInlet

    print("Resolving LSL stream type=EEG...", flush=True)
    stream = _resolve_eeg(args.resolve_timeout)
    if stream is None:
        print("No EEG LSL stream found", file=sys.stderr, flush=True)
        return 2

    inlet = StreamInlet(stream, max_buflen=60)
    print(f"Connected to LSL stream: {stream.name()}", flush=True)
    window_size = max(2, int(args.window_seconds * args.sample_rate))
    samples: deque[list[float]] = deque(maxlen=window_size)
    last_sample_at = time.monotonic()
    last_post_at = 0.0

    while True:
        sample, _timestamp = inlet.pull_sample(timeout=1.0)
        now = time.monotonic()
        if sample is None:
            if now - last_sample_at >= args.stale_timeout:
                print("EEG stream stale; supervisor must reconnect", file=sys.stderr, flush=True)
                return 3
            continue

        channels = [float(value) for value in sample[:4]]
        if len(channels) < 4 or not all(math.isfinite(value) for value in channels):
            continue
        samples.append(channels)
        last_sample_at = now
        if len(samples) < window_size or now - last_post_at < args.post_interval:
            continue

        try:
            payload = live_payload(np.asarray(samples), args.source_name, args.sample_rate)
            post_sample(args.receiver, payload, timeout=args.http_timeout)
            print(json.dumps(payload, sort_keys=True), flush=True)
            last_post_at = now
        except requests.RequestException as exc:
            print(f"receiver post error: {exc}", file=sys.stderr, flush=True)
        except ValueError as exc:
            print(f"signal window rejected: {exc}", file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Muse 2 LSL to SenseLayer receiver collector")
    parser.add_argument("--receiver", default="http://127.0.0.1:18787")
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--sample-rate", type=int, default=256)
    parser.add_argument("--post-interval", type=float, default=1.0)
    parser.add_argument("--resolve-timeout", type=float, default=20.0)
    parser.add_argument("--stale-timeout", type=float, default=10.0)
    parser.add_argument("--http-timeout", type=float, default=5.0)
    parser.add_argument("--source-name", default="muse2-edge-win11")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--max-samples", type=int, default=0)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.post_interval <= 0 or args.window_seconds <= 0 or args.sample_rate <= 0:
        raise SystemExit("timing and sample-rate values must be positive")
    return run_simulation(args) if args.simulate else run_lsl(args)


if __name__ == "__main__":
    raise SystemExit(main())
