# SEMANTIQ Repository Inventory

Evidence state: VERIFIED-IN-REPO
Inventory basis: SEMANTIQ `main` at SEM-002 dispatch, after SEM-001 merge.

## Top-level components
- `.rvsc/` — RVSC product-boundary metadata; protected from SEM-002 writes.
- `README.md` — repository/product bootstrap documentation.
- `pyproject.toml` — Python package/build metadata; protected from SEM-002 writes.
- `src/` — executable product source; protected from SEM-002 writes.
- `tests/` — test source. SEM-002 may add only `tests/baseline/**`.

## Verified executable baseline
SEM-001 established `src/semantiq/` product identity functionality and identity tests. SEM-002 does not infer or claim that the historical RTUDES extraction engine has been imported into Git.

## Evidence vocabulary
- VERIFIED-IN-REPO: directly observed in the current Git baseline.
- KNOWN-LEGACY-NOT-IMPORTED: capability known from prior RTUDES development records but its executable source is not verified in this repository.
- PLANNED: roadmap capability not claimed as existing implementation.
