# D.H.A.R.A. technical architecture

## Design objective

D.H.A.R.A. must make a defensible, hyper-local flood-risk recommendation even when either evidence stream is incomplete. Instruments provide lead time and authority but miss street-scale drainage failures. Community reports provide immediacy and resolution but are noisy and adversarial. The system keeps their inference chains independent until a governed fusion boundary.

```text
IMD / CWC / Flood Hub / ULB telemetry                 Citizen / node reports
                  |                                             |
       normalize -> QC -> features                 integrity -> content -> dedup
                  |                                  -> trust -> spatial spread
        Loop A sensor confidence S                    Loop B crowd confidence R
                  |                                             |
                  +------------- fusion ------------------------+
                                      |
                      divergence + governance + hysteresis
                                      |
                  monitor / advisory / watch / warning draft
                                      |
                     evidence dossier + officer decision
                                      |
                  push / SMS / IVR / CAP v1.2 / local action
                                      |
                        confirmed outcomes and feedback
```

## Non-negotiable safety rules

1. A public Warning cannot be produced automatically unless both `S >= 0.5` and `R >= 0.5`.
2. Warning publication requires authorised officer sign-off and an immutable audit record.
3. Escalation requires two sustained five-minute cycles; de-escalation requires three.
4. `abs(S - R) > 0.5` creates a direction-specific divergence case instead of averaging disagreement away.
5. Life-safety messages are rendered from versioned, human-reviewed language templates. Runtime machine translation is prohibited.
6. The prototype runs in shadow mode and never contacts citizens or SACHET.

## Component boundaries

### Ingestion and normalisation

Provider adapters translate source-specific payloads into `Observation`. Normalisation is deliberately separate from persistence so recorded payloads can be replayed after connector changes. A provider/external-ID pair is idempotent.

Current adapters:

- IMD: 15-minute rainfall totals;
- CWC: gauge water level;
- ULB: binary pump status;
- Flood Hub: forecast probability.

Every observation receives an H3 resolution-9 cell. QC can accept, flag, or reject a record. Rejected records are retained in the upstream raw-event archive in production; the canonical observation store contains accepted and flagged records.

### Feature materialisation

Sprint 1 exposes the stable Loop A feature contract:

- rainfall accumulation over 1, 3, 6, 24 and 72 hours;
- latest river stage and rate of change;
- latest pump-running state;
- latest external forecast probability;
- counts of quality-flagged observations.

Sprint 2 model services consume this contract. They do not read provider payloads directly.

### Loop A - instrument inference

Three calibrated model heads run independently over the shared feature view:

- T1 novelty: autoencoder reconstruction anomaly;
- T2 known precursor: gradient-boosted probability with SHAP attribution;
- T3 trend: temporal convolution/LSTM threshold-exceedance probability.

Their calibrated outputs combine as `S = clip(w1*A + w2*C + w3*T, 0, 1)`. Initial weights are `(0.25, 0.40, 0.35)` and later become zone-specific, versioned policy.

Sprint 2 implements the contract in shadow mode. T1 is a small normal-only MLP reconstruction model over feature snapshots. T2 is an isotonic-calibrated gradient-boosted classifier with signed feature-importance attribution. T3 is currently a sigmoid-calibrated logistic surrogate over sequence-derived rainfall, river-rate and forecast features. The production T3 TCN/LSTM and TreeSHAP explanation path intentionally remain substitutions to be validated once the real event ledger is available; the public interface does not change when they replace the surrogates.

Evaluation is grouped by event: every fold holds out one complete episode, preventing adjacent timesteps from the same event leaking into both training and validation. Model artifacts are versioned and their reports include a dataset SHA-256. Joblib artifacts are executable Python serialization and must only be loaded from the controlled internal model registry, never from user uploads.

### Loop B - community inference

Reports pass capture-integrity, hazard-content, perceptual-deduplication, reporter-trust, spatial-spread and burst-rate gates. The cluster score is a trust-weighted noisy-OR, damped by spatial spread and exponential recency. Multiple reports from one device or micro-cell are capped before aggregation.

Sprint 3 implements these boundaries with signed canonical report payloads, an attestation adapter, monotonic per-device counters, content-classification and perceptual-hash adapters, and explicit quarantine reasons. Quarantined evidence is retained for review but never contributes to `R`. Reporter reliability is a Beta posterior with role-based priors and conservative scoring (`mean - standard deviation`); inactive accounts decay toward their original prior.

The default cluster policy uses a 30-minute window, a 45-minute recency half-life, one contribution per device and geohash-7 sub-cell, and a minimum of four devices across three sub-cells. A verified-node report permits a sparse-zone floor of two devices across two sub-cells. The included HMAC signature, static attestation and deterministic content adapters are executable test doubles, not substitutes for hardware-backed keys, Play Integrity/App Attest, and a reviewed production vision model.

### Fusion and governance

The initial policy computes:

`F = (beta*S + (1-beta)*R + gamma*S*R) / (1 + gamma)`

with initial `beta = 0.60` and `gamma = 0.15`. Thresholds, divergence limits and miss-versus-false-alarm cost are explicit, versioned authority policy rather than hidden model constants.

The initial tiers are Monitor below `0.35`, Advisory at `0.35`, Watch at `0.55`, and Warning at `0.75`. Regardless of the fused score, a Warning candidate is gated unless both `S` and `R` are at least `0.5`. Escalation needs two consecutive cycles and de-escalation needs three. Stream divergence above `0.5` creates a direction-specific human-review question. A committed Warning is still only a draft and carries `requires_officer_signoff = true`.

### Evidence and delivery

Every proposed alert carries the raw `S`, `R`, and `F`, model attribution, contributing reports, independence audit, threshold/hysteresis state, and counterfactuals with weak reports or either stream removed. Delivery adapters render the same controlled template to app push, SMS and IVR; CAP v1.2 is the government interoperability boundary.

## Runtime topology

### Local prototype

- one FastAPI process;
- SQLite observation store;
- in-memory community report, trust and fusion state stores;
- deterministic JSONL replay files;
- synchronous feature computation;
- no outbound delivery.

This profile is intentionally runnable on a laptop and is not a production topology.

### Pilot and production target

- API and workers behind an API gateway;
- Redpanda/Kafka immutable event log;
- TimescaleDB + PostGIS canonical time-series and geospatial state;
- S3-compatible object storage for media and model artifacts;
- Redis hot cell state, rate limits and feature cache;
- separate model-serving, fusion, dossier, delivery and learning workers;
- Prometheus/Grafana observability and MLflow model registry;
- sovereign-cloud deployment with encrypted backups and audited RBAC.

`infra/postgres/001_init.sql` establishes the production observation-table contract without forcing contributors to run the full topology during Sprint 1.

## Privacy and security posture

- no continuous location tracking;
- coarse home/work cells for subscriptions; foreground precise location only for a report;
- phone numbers represented by salted hashes;
- identity and report evidence stored separately;
- hardware-backed device signatures and monotonic counters at the production capture boundary;
- full-precision operational data aged into coarser cells under a configured retention policy;
- every official read and decision audited;
- DPDP purpose limitation and consent withdrawal designed into storage lifecycles.

## Architectural decisions still open

- pilot city and authority-approved hazard thresholds;
- primary data-feed credentials and licensing;
- event ledger and ground-truth ownership;
- SMS/IVR providers and DLT registration;
- precise operational retention windows;
- SACHET onboarding and CAP transport profile.
