# Data register

## Current replay fixture

`data/replays/pune_ward14_48h.jsonl` is a synthetic 48-hour sequence based on the report's worked urban-flood scenario. It exercises all Sprint 1 adapters and includes a rainfall build-up, rising gauge, pump interruption, and rising external forecast probability.

It must not be used to claim predictive performance. Its only purpose is deterministic pipeline testing.

## Sprint 2 model-contract fixture

`data/training/synthetic_sensor_episodes.csv` contains ten deterministic synthetic episodes and 120 feature rows. It exists to exercise grouped evaluation, calibration, model persistence and inference contracts. The generated evaluation report always carries `operational_performance_claim: false`.

Rebuild it with:

```bash
python scripts/build_synthetic_sensor_fixture.py
```

## Ground truth needed for Sprint 2

For the chosen pilot wards, acquire at least five monsoon seasons where possible:

- timestamped IMD rainfall/radar snapshots;
- CWC/India-WRIS gauge and reservoir series;
- ULB drainage, pump and waterlogging logs;
- road closure, response and verified flood-onset records;
- static DEM, drainage-catchment and ward geometry;
- imagery labelled by flood/not-flood and ordinal depth.

Every source must have an owner, licence, acquisition date, spatial/temporal resolution, missingness profile and transformation history in a versioned manifest. News reports may help discover events but should not be treated as sole ground truth.
