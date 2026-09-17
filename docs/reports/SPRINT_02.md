# Sprint 2 report - instrument intelligence loop

## Outcome

Sprint 2 delivers a runnable, versioned Loop A sensor-confidence pipeline. It trains three independent model heads, calibrates their outputs, combines them into `S`, explains the precursor contribution, evaluates by held-out event, persists the model, and serves predictions through the API.

The software exit criterion is complete. The operational exit criterion is not: the current fixture is synthetic, so its scores demonstrate plumbing and invariants rather than real-world predictive skill.

## Built

- Stable ten-feature training contract matching the Sprint 1 materialized feature view.
- Strict CSV event-ledger loader with timezone, outcome, identifier, finite-value and schema validation.
- T1 novelty model: normal-only MLP reconstruction error mapped to a bounded probability.
- T2 precursor model: isotonic-calibrated gradient boosting.
- T3 trend model: sigmoid-calibrated logistic surrogate over sequence-derived features.
- Initial governed fusion weights: T1 `0.25`, T2 `0.40`, T3 `0.35`.
- Signed feature-importance attribution for every prediction.
- Leave-one-event-out evaluation that holds out complete episodes rather than random rows.
- Brier score, probability of detection, false-alarm ratio, critical-success index and reliability bins.
- Versioned Joblib model artifact and dataset SHA-256 in the generated evaluation report.
- `dhara train-loop-a` command and `/v1/risk/sensor` inference endpoint.
- `/v1/models/loop-a` status endpoint that explicitly reports shadow mode.
- Deterministic 120-row, ten-event synthetic model-contract fixture and rebuild script.

## Verification result

The committed evaluation artifact is `docs/reports/sprint2_evaluation.json`.

- Rows: 120
- Events: 10
- Positive rows: 20
- Grouping: one complete event held out per fold
- Brier score: 0.017755
- Probability of detection: 1.0
- False-alarm ratio: 0.0
- Critical-success index: 1.0

These near-perfect values are expected because the deterministic synthetic scenarios are intentionally separable. They are not evidence of field accuracy and must never be quoted as an operational claim. The report itself records `data_classification: synthetic` and `operational_performance_claim: false`.

## Safety and governance controls

- Inference returns probabilities in `[0, 1]` and a version identifier.
- Model weights must be non-negative and sum to one.
- APIs remain shadow-only and have no delivery side effects.
- Cross-validation is event-grouped to prevent temporal leakage.
- Only controlled internal Joblib artifacts may be loaded; uploaded serialized models are prohibited.
- The synthetic data classification is carried into the report rather than inferred from performance.

## Deliberate substitutions

Two production components are represented by interface-compatible Sprint 2 surrogates:

- The report's temporal novelty model becomes a tabular MLP reconstruction model until dense multivariate sequences are available.
- The report's TCN/LSTM trend model becomes calibrated logistic regression over sequence-derived features.
- Signed tree feature importance is exposed now; TreeSHAP replaces it when the real boosted model and event ledger are stable.

These substitutions keep the prototype executable without pretending synthetic volume is sufficient for deep sequence learning.

## Tests

The suite covers dataset validation, event grouping, metric arithmetic, model fitting, probability bounds, weight governance, model round-trip persistence, versioned API inference and all Sprint 1 regression behavior.

## Remaining external dependencies

- Authority-approved pilot city and ward boundaries.
- Five or more seasons of timestamped rainfall, gauge, pump and verified flood-outcome data.
- Ground-truth ownership and a documented label adjudication process.
- Policy-approved miss-versus-false-alarm costs and tier thresholds.

## Sprint 3 handoff

Sprint 3 can consume versioned `S` without knowing model internals. Its work is the citizen evidence pipeline: signed intake, attestation adapter, content and duplicate checks, Beta trust, spatial-spread anti-Sybil logic, quarantine, crowd confidence `R`, and governed `S`/`R` fusion.
