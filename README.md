# D.H.A.R.A.

**Dynamic Hazard Analysis & Resilience Advocate**

D.H.A.R.A. is a dual-loop disaster early-warning and decision-support system. It combines coarse but trustworthy environmental instruments with hyper-local, trust-weighted community evidence, then exposes the reasoning behind every alert to an authorised decision-maker.

The prototype deliberately targets one hazard: urban and peri-urban flooding in India. It is designed as an upstream decision-support layer for CAP v1.2/SACHET, not as a competing public-alert network.

## What exists today

Sprint 1 provides the ingestion and replay spine on which the two detection loops will run:

- provider adapters for IMD rainfall, CWC gauges, ULB pump telemetry and Flood Hub probability snapshots;
- range, timestamp, location, unit and staleness quality checks;
- H3 resolution-9 indexing for every accepted observation;
- idempotent SQLite persistence for local development, with a TimescaleDB/PostGIS production migration;
- 1/3/6/24/72-hour rainfall features plus river-stage trend, pump state and forecast-risk features;
- a deterministic 48-hour Pune Ward 14 replay fixture;
- FastAPI endpoints and a `dhara` command-line replay tool;
- automated tests and GitHub Actions CI.

The fixture is synthetic and clearly labelled. It proves the pipeline contract; it is not presented as historical ground truth.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate
python -m pip install -e ".[dev]"
dhara replay data/replays/pune_ward14_48h.jsonl --database var/dhara.db
uvicorn dhara.api:app --reload
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.

Useful endpoints:

- `GET /health`
- `POST /v1/observations`
- `GET /v1/observations?cell_id=...`
- `GET /v1/features/{cell_id}`
- `POST /v1/replays` (operator-only prototype endpoint; paths are confined to `data/replays`)

Run the checks:

```bash
pytest
ruff check .
```

## Delivery plan

The sprint plan, acceptance criteria, and current status live in [docs/SPRINTS.md](docs/SPRINTS.md). The component boundaries and deployment evolution are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Safety boundary

This repository is an engineering prototype. It must remain in shadow mode until locally calibrated against verified events, security-reviewed, and authorised by the relevant disaster-management authority. It does not issue real public warnings.
