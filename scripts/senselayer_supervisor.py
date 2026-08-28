#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

STOP = False


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    required = ("ssh_host", "ssh_user", "identity_file", "known_hosts_file")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError("missing config keys: " + ", ".join(missing))
    return config


def wait_for_receiver(port: int, timeout: float, tunnel: subprocess.Popen) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline and not STOP:
        if tunnel.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def terminate(process: subprocess.Popen | None, name: str) -> None:
    if process is None or process.poll() is not None:
        return
    log(f"stopping {name}")
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def tunnel_command(config: dict[str, Any]) -> list[str]:
    receiver_port = int(config.get("local_receiver_port", 18787))
    dashboard_port = int(config.get("local_dashboard_port", 18501))
    return [
        config.get("ssh_executable", "ssh.exe"),
        "-N", "-T",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={config['known_hosts_file']}",
        "-o", "IdentitiesOnly=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=10",
        "-i", config["identity_file"],
        "-p", str(config.get("ssh_port", 22)),
        "-L", f"127.0.0.1:{receiver_port}:127.0.0.1:8787",
        "-L", f"127.0.0.1:{dashboard_port}:127.0.0.1:8501",
        f"{config['ssh_user']}@{config['ssh_host']}",
    ]


def run_cycle(config: dict[str, Any], base: Path) -> int:
    tunnel = muse = collector = None
    try:
        log("starting SSH tunnel")
        tunnel = subprocess.Popen(tunnel_command(config))
        receiver_port = int(config.get("local_receiver_port", 18787))
        if not wait_for_receiver(receiver_port, 20, tunnel):
            log("SSH tunnel or receiver readiness failed")
            return 10

        simulate = bool(config.get("simulate", False))
        if not simulate:
            muselsl = config.get("muselsl_executable", str(base / ".venv" / "Scripts" / "muselsl.exe"))
            muse_command = [muselsl, "stream"]
            if config.get("muse_name"):
                muse_command += ["--name", str(config["muse_name"])]
            log("starting muselsl")
            muse = subprocess.Popen(muse_command)
            time.sleep(float(config.get("muse_start_delay", 7)))
            if muse.poll() is not None:
                log(f"muselsl exited early: {muse.returncode}")
                return 11

        python = config.get("python_executable", str(base / ".venv" / "Scripts" / "python.exe"))
        collector_script = config.get("collector_script", str(base / "muse2_edge_collector.py"))
        collector_command = [
            python,
            collector_script,
            "--receiver", f"http://127.0.0.1:{receiver_port}",
            "--source-name", str(config.get("source_name", "muse2-edge-win11")),
            "--post-interval", str(config.get("post_interval", 1.0)),
        ]
        if simulate:
            collector_command.append("--simulate")
        log("starting collector")
        collector = subprocess.Popen(collector_command)

        while not STOP:
            if tunnel.poll() is not None:
                log(f"SSH tunnel exited: {tunnel.returncode}")
                return 12
            if muse is not None and muse.poll() is not None:
                log(f"muselsl exited: {muse.returncode}")
                return 13
            if collector.poll() is not None:
                log(f"collector exited: {collector.returncode}")
                return 14
            time.sleep(1)
        return 0
    finally:
        terminate(collector, "collector")
        terminate(muse, "muselsl")
        terminate(tunnel, "SSH tunnel")


def main() -> int:
    parser = argparse.ArgumentParser(description="SenseLayer Windows process supervisor")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    config = load_config(args.config)
    base = args.config.parent

    def stop_handler(_signum, _frame):
        global STOP
        STOP = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    backoff = 3
    while not STOP:
        code = run_cycle(config, base)
        if STOP:
            break
        log(f"pipeline cycle failed ({code}); retry in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)
    log("supervisor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
