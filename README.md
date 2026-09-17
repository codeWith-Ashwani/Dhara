# D.H.A.R.A.

**Dynamic Hazard Analysis & Resilience Advocate**

D.H.A.R.A. is a dual-loop disaster early-warning and decision-support system. It combines coarse but trustworthy environmental instruments with hyper-local, trust-weighted community evidence, then exposes the reasoning behind every alert to an authorised decision-maker.

The prototype deliberately targets one hazard: urban and peri-urban flooding in India. It is designed as an upstream decision-support layer for CAP v1.2/SACHET, not as a competing public-alert network.

## What exists today

Sprints 1 through 6 provide the ingestion spine, both evidence loops, the shadow-mode
operator workflow, an audited outcome-learning cycle, and pilot control-plane boundaries:

- provider adapters for IMD rainfall, CWC gauges, ULB pump telemetry and Flood Hub probability snapshots;
- range, timestamp, location, unit and staleness quality checks;
- H3 resolution-9 indexing for every accepted observation;
- idempotent SQLite persistence for local development, with a TimescaleDB/PostGIS production migration;
- 1/3/6/24/72-hour rainfall features plus river-stage trend, pump state and forecast-risk features;
- a deterministic 48-hour Pune Ward 14 replay fixture;
- FastAPI endpoints and a `dhara` command-line replay tool;
- automated tests and GitHub Actions CI;
- a normal-only reconstruction model, calibrated gradient-boosted precursor model and calibrated trend surrogate;
- fused sensor confidence `S`, signed feature attribution and versioned model artifacts;
- leave-one-event-out Brier, POD, FAR, CSI and reliability evaluation;
- signed and attested community-report intake with content, replay, duplicate and rate gates;
- conservative Beta-posterior reporter trust, recency weighting and spatial-spread controls;
- crowd confidence `R` plus governed `S`/`R` fusion, divergence and hysteresis;
- a dual-stream Warning gate that still requires authorised officer sign-off;
- a responsive live triage console and six-panel evidence dossier;
- weak-report and stream-removal counterfactuals;
- hash-chained, database-enforced append-only officer decisions;
- controlled English, Hindi and Marathi alert templates;
- sandbox push, SMS and IVR adapters plus CAP v1.2 `Test` messages;
- a signed, one-segment offline SMS report codec;
- immutable confirmed/refuted outcome capture from officer decisions;
- idempotent learning jobs that update reporter trust, calibration candidates, and
  cost-weighted zone policy;
- calibration promotion gates that keep a candidate audit-only when its replay Brier
  score does not improve;
- privacy-filtered public calibration metrics with small-sample suppression;
- hard shadow-mode controls and fail-closed classifier, signature, and attestation paths;
- a load/chaos/privacy rehearsal and operator runbook;
- durable community reports, reporter trust, and restart-safe device replay counters;
- short-lived signed operator claims with viewer/operator/supervisor role enforcement;
- immutable, provenance-bearing authority event-ledger import;
- leave-one-event-group-out calibration evaluation with bootstrap uncertainty gates;
- single-active nightly job leases, low-cardinality metrics, and signed external anchor files.

The fixture is synthetic and clearly labelled. It proves the pipeline contract; it is not presented as historical ground truth.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate
python -m pip install -e ".[dev]"
dhara replay data/replays/pune_ward14_48h.jsonl --database var/dhara.db
python scripts/build_synthetic_sensor_fixture.py
dhara train-loop-a data/training/synthetic_sensor_episodes.csv
python scripts/run_sprint3_adversarial_demo.py
python scripts/run_sprint4_acceptance_demo.py
python scripts/run_sprint5_pilot_rehearsal.py
python scripts/run_sprint6_pilot_controls.py
uvicorn dhara.api:app --reload
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.

The operator APIs require a configured secret and a short-lived token. Use a unique secret of at
least 32 bytes; never commit it:

```bash
export DHARA_OPERATOR_HMAC_SECRET="replace-with-a-secret-from-your-secret-manager"
export DHARA_AUDIT_ANCHOR_SECRET="replace-with-a-separate-anchor-secret"
dhara issue-operator-token control-room-01 --role supervisor --minutes 30
```

On PowerShell, set the same values with `$env:DHARA_OPERATOR_HMAC_SECRET` and
`$env:DHARA_AUDIT_ANCHOR_SECRET`. Paste the issued token into `/operator`. When no operator
secret is configured the process uses an ephemeral key, so previously issued tokens cannot
authenticate and restart invalidates all tokens.

Useful endpoints:

- `GET /health`
- `POST /v1/observations`
- `GET /v1/observations?cell_id=...`
- `GET /v1/features/{cell_id}`
- `POST /v1/replays` (operator-only prototype endpoint; paths are confined to `data/replays`)
- `POST /v1/reports/community`
- `GET /v1/crowd/{cell_id}`
- `POST /v1/fusion/evaluate`
- `GET /operator` (operator triage and evidence console)
- `GET /v1/triage`
- `GET /v1/alerts/{alert_id}/dossier`
- `POST /v1/alerts/{alert_id}/actions`
- `GET /v1/alerts/{alert_id}/audit`
- `GET /v1/safety`
- `GET /v1/outcomes`
- `POST /v1/learning/run`
- `GET /v1/learning/status`
- `GET /v1/public/calibration`
- `GET /v1/zone-policy/{cell_id}`
- `POST /v1/authority/events/import`
- `GET /v1/authority/events/summary`
- `POST /v1/heldout/evaluate`
- `GET /v1/public/heldout-calibration`
- `POST /v1/jobs/nightly/run`
- `POST|GET /v1/audit/anchor`
- `GET /v1/operations/status`
- `GET /metrics`

Run the checks:

```bash
pytest
ruff check .
```

## Delivery plan

The sprint plan, acceptance criteria, and current status live in
[docs/SPRINTS.md](docs/SPRINTS.md). The component boundaries and deployment evolution
are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Operational rehearsal and recovery
steps are in [docs/OPERATOR_RUNBOOK.md](docs/OPERATOR_RUNBOOK.md).

## Safety boundary

This repository is an engineering prototype. It must remain in shadow mode until locally calibrated against verified events, security-reviewed, and authorised by the relevant disaster-management authority. Delivery adapters only record sandbox receipts, CAP messages use `status=Test`, and the repository does not issue real public warnings.
