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

**Status:** implemented in shadow mode. The templates are explicitly sandbox-only drafts; native-speaker approval, DLT registration, provider credentials, operator RBAC and SACHET onboarding remain external deployment gates.

Deliverables:

- live cell map and triage queue;
- six-panel evidence dossier with stream-removal and weakest-report counterfactuals;
- officer decision rail and immutable audit events;
- versioned English, Hindi and Marathi alert templates;
- push/SMS/IVR sandbox adapters, offline report codec and CAP v1.2 composer.

Exit test: an evaluator can explain why an alert exists and send the same approved message through every sandbox channel.

Result: passed for the prototype boundary. A deterministic Warning produces six evidence panels, weak-report and stream-removal counterfactuals, a hash-chained officer decision, identical push/SMS/IVR message bodies, a CAP v1.2 `Test` document, and a signed 34-character offline report. See [the Sprint 4 report](reports/SPRINT_04.md).

## Sprint 5 - Learning loop and pilot hardening

**Status:** implemented and rehearsed in shadow mode with synthetic labels. Authority-owned
ground truth, an approved evaluation protocol, RBAC, external audit anchoring and production
provider integrations remain pilot gates.

Deliverables:

- confirmed/refuted outcome capture;
- nightly trust, calibration and zone-policy update jobs;
- public calibration metrics;
- load, chaos, privacy and security testing;
- operator runbook, shadow-mode controls and demo rehearsal.

Exit test: injecting labelled outcomes changes trust/calibration through an audited job, while a complete historical replay meets latency and reliability objectives.

Result: passed for the prototype boundary. Eight labelled synthetic outcomes changed trusted
and noisy reporter scores in the expected directions; three calibration candidates were stored,
two improving candidates were promoted and the degrading fused candidate was retained without
promotion. The 48-hour replay completed 25 times with zero rejects and a 15.002 ms observed
p95 against a 500 ms local objective. The learning job was idempotent, both audit chains were
valid, public metrics contained no report/reporter/actor identifiers, classifier failure
quarantined evidence, an SMS failure did not block the other sandbox channels, and live mode
was rejected. See [the Sprint 5 report](reports/SPRINT_05.md).

## Sprint 6 - Pilot control plane and durable evidence

**Status:** implemented and rehearsed with synthetic authority events. Integration with a real
identity provider, managed scheduler, secrets manager, object-locked anchor service and
authority-owned event ledger remains a deployment gate.

Deliverables:

- durable community-report and reporter-trust stores with restart-safe replay counters;
- signed short-lived operator identities and viewer/operator/supervisor RBAC;
- immutable, idempotent authority event-ledger import with provenance;
- leave-one-event-group-out calibration evaluation and bootstrap uncertainty bounds;
- single-active nightly job lease, operational metrics and signed audit-chain anchoring;
- authenticated console token flow and full pilot-control rehearsal.

Exit test: restart the community service without losing evidence, trust or anti-replay state;
deny unauthenticated/underprivileged operator actions; ingest authority events idempotently; and
complete one leased nightly run whose held-out, audit-anchor and privacy gates all pass.

Result: passed for the prototype boundary. Community evidence and trust survived restart, a
replayed device counter remained quarantined, missing tokens returned 401, viewer writes and
actor mismatches returned 403, and all 16 synthetic authority rows imported exactly once across
four event groups. The nightly control job completed in 43.046 ms, reused unchanged learning
and anchor state on repeat, promoted two statistically supported streams, blocked the uncertain
fused stream, emitted privacy-filtered public results, and left all three integrity chains valid.
All 16 acceptance flags passed. See [the Sprint 6 report](reports/SPRINT_06.md).

## Definition of done for every sprint

- tests cover the safety-critical invariant introduced in that sprint;
- the replay remains deterministic;
- interfaces and policy changes are documented;
- no real public-warning side effect is enabled;
- observed limitations and missing evidence are stated rather than hidden.
