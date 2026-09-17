# Prototype delivery sprints

The build is organised so each sprint ends in an independently demonstrable capability. Scope is one city, three flood-prone wards, three reviewed languages, and replay-driven shadow operation.

## Sprint 1 - Ground truth and ingestion spine

**Status:** implemented foundation; real ground-truth acquisition remains an external dependency.

Deliverables:

- canonical schemas and provider adapters;
- H3 resolution-9 assignment;
- QC and idempotent persistence;
- Loop A feature contract;
- deterministic 48-hour replay CLI/API;
- synthetic Pune Ward 14 fixture and manifest;
- TimescaleDB/PostGIS migration and CI.

Exit test: replay the supplied window twice; the second run creates no duplicates and produces the same feature snapshot.

## Sprint 2 - Instrument loop

**Status:** implemented in shadow mode using a synthetic contract fixture. Replacement with authority-verified historical events is required before performance can be assessed.

Deliverables:

- event-ledger import and leave-one-event-out split;
- T2 gradient-boosted precursor baseline first;
- T1 novelty and T3 trend models behind the same interface;
- probability calibration, reliability diagram, POD/FAR/CSI and Brier score;
- per-feature explanations stored with every `S` result.

Exit test for the software path: leave-one-event-out evaluation produces versioned per-cell `S` values and a reproducible report. The real-event exit criterion remains pending data acquisition.

## Sprint 3 - Community loop and governed fusion

**Status:** implemented in shadow mode with deterministic prototype adapters and adversarial replay. Production device attestation, hardware-backed keys and a durable report store remain pilot integrations.

Deliverables:

- signed report intake and attestation interface;
- content-classifier and pHash adapter boundaries;
- Beta-posterior reporter trust;
- per-device/per-cell caps, spatial-spread clustering, recency decay and quarantine;
- fusion, divergence, dual-stream Warning gate and hysteresis state machine;
- adversarial replay scenarios.

Exit test: fifteen same-device reports cannot create a Warning; four independent, spatially spread reports can corroborate a sensor event.

Result: passed. The attack contributes one capped device/sub-cell signal and remains at Advisory despite `S = 0.95`. Four independent reports across four sub-cells produce `R = 0.824835`; after two sustained cycles the fusion state becomes a Warning draft that requires officer sign-off. See [the Sprint 3 report](reports/SPRINT_03.md).

## Sprint 4 - Evidence dossier and resilient delivery

Deliverables:

- live cell map and triage queue;
- six-panel evidence dossier with stream-removal and weakest-report counterfactuals;
- officer decision rail and immutable audit events;
- versioned English, Hindi and Marathi alert templates;
- push/SMS/IVR sandbox adapters, offline report codec and CAP v1.2 composer.

Exit test: an evaluator can explain why an alert exists and send the same approved message through every sandbox channel.

## Sprint 5 - Learning loop and pilot hardening

Deliverables:

- confirmed/refuted outcome capture;
- nightly trust, calibration and zone-policy update jobs;
- public calibration metrics;
- load, chaos, privacy and security testing;
- operator runbook, shadow-mode controls and demo rehearsal.

Exit test: injecting labelled outcomes changes trust/calibration through an audited job, while a complete historical replay meets latency and reliability objectives.

## Definition of done for every sprint

- tests cover the safety-critical invariant introduced in that sprint;
- the replay remains deterministic;
- interfaces and policy changes are documented;
- no real public-warning side effect is enabled;
- observed limitations and missing evidence are stated rather than hidden.
