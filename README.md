# BCI Live Pipeline (Muse 2 to Edge to VPS to Dashboard)

A lightweight BCI pipeline for streaming Muse 2 EEG-derived band powers from a Windows edge device to a VPS, storing history, and visualizing live plus historical signals in Streamlit.

## What this repository contains

1) Live ingestion backend (FastAPI receiver)
2) Edge collector (Windows, Muse 2 via LSL)
3) History storage (SQLite)
4) Live plus history dashboard (Streamlit)
5) Simulation fallback (when no live sample is available)

## End-to-end architecture

Muse 2 headset
-> muselsl stream on Windows
-> edge collector (Python)
-> HTTP POST /sample to VPS receiver
-> data/latest_sample.json and data/history.db
-> Streamlit dashboard (live bands + time-series history)

## Repository structure

- ui.py
  - Streamlit dashboard
  - Displays current state, current band values, and history chart
  - Falls back to simulator if no live data is available

- services/receiver.py
  - FastAPI server
  - Endpoints:
    - POST /sample: ingest one sample
    - GET /health: freshness, age, status
    - GET /history?limit=N: recent samples from SQLite

- sources/live_source.py
  - Reads latest sample from data/latest_sample.json
  - Returns last good sample on transient read errors

- pipeline/processor.py
  - Signal post-processing and state scoring (Focus vs Noise)

- sim.py
  - Synthetic EEG simulator used as fallback/demo source

- scripts/muse2_edge_collector.py
  - Edge collector script:
    - reads EEG stream via LSL
    - computes normalized delta/theta/alpha/beta/gamma powers
    - posts samples to receiver

- scripts/install_edge_v2.ps1
  - Windows setup script for edge host
  - Creates venv, installs dependencies, registers Task Scheduler autostart

- scripts/EDGE_README.txt
  - Short Windows edge setup notes

- config.json
  - Simulator and visual config

- requirements.txt
  - Python dependencies for dashboard plus receiver

## Data model

Each sample posted to receiver contains:
- timestamp
- source
- delta
- theta
- alpha
- beta
- gamma
- signal_quality

Band values are normalized proportions from computed spectral power.
Receiver stores both original timestamp and server-side received_at.

## Local run (VPS side)

1) Create venv and install dependencies
- python3 -m venv .venv
- source .venv/bin/activate
- pip install -r requirements.txt

2) Start receiver API
- uvicorn services.receiver:app --host 0.0.0.0 --port 8787

3) Start dashboard
- streamlit run ui.py --server.port 8501 --server.address 0.0.0.0

## Windows edge setup (Muse 2)

On Windows machine (PowerShell as Administrator):
- powershell -ExecutionPolicy Bypass -File ./scripts/install_edge_v2.ps1

Then pair Muse 2 once in Windows Bluetooth settings.

Runtime flow on edge:
1) start muselsl stream
2) connect to LSL EEG stream
3) compute band powers on sliding window
4) send to VPS receiver at /sample
5) auto-retry on temporary failures

## Health and verification

- Receiver health:
  curl http://127.0.0.1:8787/health

- Latest history rows:
  curl http://127.0.0.1:8787/history?limit=5

- Dashboard URL:
  http://<host>:8501

## Operational notes

- data/ contains runtime artifacts (history.db, latest_sample.json) and is git-ignored.
- If no fresh live data arrives, dashboard status becomes stale and simulator fallback can still render.
- Slightly jagged curves are expected due to short-window FFT plus sensor/contact jitter.

## Status

Current codebase supports:
- Live Muse 2 ingestion
- Persistent history storage
- Streamlit live/history visualization
- Windows edge bootstrap and autostart path

This repository is a practical baseline for real-time BCI prototyping, not medical-grade signal processing.
