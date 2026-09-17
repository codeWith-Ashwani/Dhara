# Sprint 5 report - learning loop and pilot hardening

## Outcome

Sprint 5 closes D.H.A.R.A.'s shadow feedback loop. Confirmed and refuted officer decisions now
become immutable outcomes; an explicit, idempotent learning job updates reporter trust, creates
auditable calibration candidates, and emits a cost-weighted cell policy. Public metrics expose
aggregate reliability only, while hard runtime controls continue to prohibit live delivery.

The acceptance boundary is deliberately narrow. All rehearsal labels and source data are
synthetic, so the results validate software behavior and do not establish predictive or
operational performance.

## Built

- Immutable confirmed/refuted outcomes linked to the alert, cell, evidence IDs, actor, raw
  `S`/`R`/`F`, tier and timestamps.
- Idempotent conflict behavior: the same label reuses the outcome; the opposite label is
  rejected.
- Hash-chained learning runs keyed by the labelled-input digest, preventing repeated trust
  updates for unchanged evidence.
- Beta-posterior trust updates from adjudicated reporter outcomes.
- Versioned binned calibration candidates for sensor, crowd and fused confidence.
- Promotion gating: at least five labels and an improved replay Brier score are required;
  non-improving candidates remain immutable audit artifacts and do not transform scores.
- Per-H3-cell policy artifacts with a five-to-one miss-versus-false-alarm cost, tuned thresholds,
  sensor weight and corroboration bonus.
- Public POD, FAR, CSI and calibration endpoints with sample-size suppression and removal of
  report, reporter, actor, note and input-digest fields.
- Fail-closed signature, attestation and content-classifier adapter handling.
- Graceful sandbox delivery degradation when one channel adapter fails.
- Hard `shadow` operating-mode lock, public delivery off, CAP `Test`, and runtime translation
  off.
- PostgreSQL contracts for outcomes, calibration artifacts, zone policies and learning runs,
  with database-enforced append-only triggers.
- A shift, triage, degraded-mode, recovery and promotion-gate operator runbook.
- A CI-enforced full Sprint 5 pilot rehearsal.

## Acceptance rehearsal

The committed machine-readable result is `docs/reports/sprint5_rehearsal_results.json`.

- Synthetic 48-hour replay: 18 records, 25 runs, zero rejected records.
- Idempotency: first run created 18 rows; the next 24 runs produced 432 duplicate observations
  and the store remained at 18 rows.
- Replay latency: p50 13.259 ms, p95 15.002 ms, maximum 15.529 ms against a 500 ms local
  objective.
- Feature snapshot SHA-256 remained
  `282f35a91a881db951200acfa443082c4aa33669324303916a448f5188321312`.
- Learning: eight outcomes processed in 5.603 ms against a 2,000 ms local objective; the
  unchanged repeat reused the same job and did not change trust.
- Trusted-reporter score: 0.276393 to 0.554567.
- Noisy-reporter score: 0.276393 to 0.105662.
- Sensor Brier: 0.209063 to 0.140625, promoted.
- Crowd Brier: 0.210710 to 0.177014, promoted.
- Fused candidate Brier: 0.214198 to 0.223889, correctly not promoted.
- Cell policy: sensor weight 0.607278; Advisory 0.338750, Watch 0.538750, Warning 0.738750.
- Public aggregate metrics: POD 0.75, FAR 0.40, CSI 0.50, explicitly synthetic and not an
  operational claim.
- Decision and learning audit chains were valid.
- Classifier outage quarantined the report with zero contribution.
- Simulated SMS outage recorded `sandbox_failed`; push and IVR were still attempted and all
  receipts retained the identical body hash.
- A requested live operating mode was rejected.
- All 13 Sprint 5 acceptance flags passed.

## Safety and privacy controls

- Learning has no path to publish or deliver an alert.
- Outcomes and all learning artifacts reject update/delete operations at the database layer.
- Candidate calibration is separated from promotion, and a failed gate behaves as identity.
- Replaying identical labels cannot update reporter trust twice.
- Adapter outages cannot turn unverifiable evidence into a positive crowd contribution.
- One sandbox-channel failure cannot silently report total success or change the approved body.
- Public metrics are suppressed below five outcomes and expose no direct operational identity or
  evidence references.
- No real recipient, delivery provider, authority gateway or SACHET endpoint exists.

## Verification

- 45 automated tests passed with 91% package coverage; the final CI run repeats the complete
  suite with coverage.
- Ruff linting passed.
- The CI workflow now runs the Sprint 5 rehearsal in addition to lint and tests.
- The rehearsal's replay, learning, privacy, chaos and hard-lock acceptance assertions all
  passed.

## Deliberate prototype limitations

- The eight labels are synthetic and far below a valid operational calibration cohort.
- The promotion gate uses replay fit for software testing; real promotion requires held-out,
  event-grouped, authority-approved evaluation and confidence intervals.
- Trust state is in memory even though labelled outcomes and learning artifacts are durable.
- Job execution is synchronous and locally scheduled by an explicit API call; a production
  worker, distributed lock, retry policy and monitoring are not implemented.
- Public suppression at five records is only a prototype floor and is not a completed privacy
  or re-identification analysis.
- Actor identity is caller-supplied until an identity-aware gateway and RBAC bind it.
- Local hash chains are tamper-evident but do not protect against a privileged database
  administrator; external anchoring remains required.
- PostgreSQL migration contracts are supplied but not exercised by local CI.
- No real public-warning side effect is enabled.

## Next sprint candidates

Sprint 6 should focus on pilot integration rather than widening the hazard scope: durable report
and trust storage, authenticated operator RBAC, authority-owned event-ledger ingestion,
event-grouped held-out calibration with uncertainty bounds, worker scheduling/observability, and
external audit anchoring. Live public delivery must remain out of scope until the authority,
security, language, telecom and SACHET gates in the runbook are independently satisfied.
