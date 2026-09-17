# Sprint 6 report - pilot control plane and durable evidence

## Outcome

Sprint 6 moves D.H.A.R.A. from a single-process demonstration toward a governable shadow pilot.
Community evidence, trust and anti-replay state now survive restart; operator actions are bound
to signed short-lived identities and roles; authority labels enter through an immutable atomic
ledger; and calibration candidates face event-grouped held-out and uncertainty gates.

The sprint does not enable live warnings. Identity claims, authority events and external anchors
remain prototype integration boundaries. The committed rehearsal uses only synthetic data and
makes no predictive or operational-performance claim.

## Built

- SQLite community-report persistence for accepted and quarantined evidence, with immutable
  report IDs and database triggers rejecting updates/deletes.
- Durable reporter role, Beta parameters and last-activity state.
- Startup reconstruction of monotonic device counters and rate-window arrivals, preventing a
  process restart from resetting replay protection.
- Signed HMAC-SHA256 operator claims containing subject, viewer/operator/supervisor role, issue
  and expiry times, issuer, audience and unique token ID.
- Route-level RBAC plus exact binding between an officer action's actor ID and its authenticated
  subject.
- Token-aware operator console and a CLI token-issuance command that refuses to operate without
  a configured secret.
- Atomic, idempotent authority-event batch import with source reference, event group, approving
  supervisor, classification and raw `S`/`R`/`F`.
- Leave-one-event-group-out binned calibration assessment, ensuring no physical episode appears
  in both a fold's training and validation data.
- Deterministic 95% bootstrap bounds and promotion gates requiring at least 12 samples, three
  event groups, lower held-out Brier score and a positive lower improvement bound.
- Single-active SQLite nightly-job leases with guaranteed release on success or failure.
- Integrated nightly coordinator for outcome learning, held-out evaluation and audit anchoring.
- Low-cardinality JSON and Prometheus metrics with no operator, reporter, alert or cell labels.
- Signed, hash-chained decision/learning head receipts in a separate fsynced JSONL exchange file.
- PostgreSQL contracts for durable reports, trust, authority events, held-out artifacts and job
  leases.
- A deterministic Sprint 6 acceptance rehearsal enforced in CI.

## Acceptance rehearsal

The committed machine-readable result is `docs/reports/sprint6_pilot_controls_results.json`.

- An accepted report survived restart and reporter trust remained exactly `0.706841`.
- Reusing the device counter after restart was quarantined as `replayed_counter` with zero
  contribution.
- Missing authentication returned 401; viewer write access and actor/token mismatch returned
  403; an authenticated operator replay succeeded.
- Sixteen synthetic authority rows imported once across four event groups; the identical repeat
  produced 16 duplicates and zero new rows.
- The integrated nightly job completed in 43.046 ms against a 2,000 ms local objective.
- The unchanged repeat reused both the learning run and the signed anchor receipt.
- Sensor held-out Brier improved from 0.160000 to 0.015625; the 95% improvement interval lower
  bound was 0.144375, so the candidate passed the software gate.
- Crowd held-out Brier improved from 0.040000 to 0.015625; its lower bound was 0.024375, so it
  passed the software gate.
- Fused held-out Brier changed from 0.340000 to 0.250000, but the interval was -0.060000 to
  0.240000; the uncertain candidate was correctly blocked.
- Decision, learning and anchor chains were valid.
- Public held-out output omitted input digests, source references, authority IDs and approving
  identities.
- All 16 Sprint 6 acceptance flags passed.

## Authentication and authorization policy

- Viewer: inspect triage, dossiers, audit/learning/operations status and metrics.
- Operator: viewer access plus replay, sensor/fusion evaluation and ground-verification request.
- Supervisor: operator access plus confirm/refute, sandbox CAP signing, authority import,
  learning, nightly jobs and anchor creation.
- Final officer actions are rejected when request actor and signed subject differ.
- Tokens expire after a bounded lifetime of at most eight hours; the CLI defaults to 30 minutes.
- A configured HMAC secret must contain at least 32 bytes. Missing secrets create an ephemeral
  deny-by-default integration state rather than a committed development credential.

## Verification

- 52 automated tests passed with 89% package coverage.
- Ruff linting passed.
- Dashboard JavaScript syntax validation passed.
- Both Sprint 5 and Sprint 6 rehearsals are CI steps.
- PostgreSQL schema includes the new pilot contracts, while SQLite exercises the behavior in CI.

## Deliberate prototype limitations

- HMAC claims model the authority-gateway contract; production needs OIDC/JWKS verification,
  MFA, revocation, account lifecycle, key rotation and managed secrets.
- SQLite stores and leases coordinate one host, not a multi-replica deployment. PostgreSQL
  advisory locks or a workflow platform remain required.
- The local anchor JSONL file is signed and separately stored but is not independent evidence.
  Production must export it to object-locked storage or a transparency/notary service.
- The 16 authority rows are synthetic and deliberately regular. Real promotion needs multiple
  seasons, authority-owned event definitions, confidence intervals and subgroup review.
- Bootstrap observations within an event-grouped evaluation are not a substitute for a
  hierarchical uncertainty model on real correlated spatiotemporal data.
- Reporter profiles are durable but concurrent multi-process trust updates still require
  transactional versioning.
- API tokens are pasted manually into the console and held only in page memory; production needs
  a secure login redirect and browser session controls.
- No public-warning side effect, real provider credential or SACHET transport is enabled.

## Next sprint candidates

Sprint 7 should exercise real integration seams without widening hazard scope: OIDC/JWKS and
managed-key adapters, transactional multi-worker trust updates, an actual scheduler with retries
and alerts, authority event-manifest signing, object-locked anchor export, and a multi-season
event-grouped evaluation protocol reviewed by the pilot authority.
