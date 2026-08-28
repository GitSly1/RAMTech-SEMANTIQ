# SEMANTIQ Migration Gaps

## Verified gap
The current SEMANTIQ Git repository contains the governed bootstrap and SEM-001 identity/test foundation, but the historical RTUDES application source baseline is not verified as present in this repository.

## Required before legacy behavior migration
1. Identify the authoritative legacy RTUDES/SEMANTIQ source build to migrate from.
2. Record its version/build identifier and provenance.
3. Inventory modules and regression-critical behavior from that baseline.
4. Compare legacy modules against the SEMANTIQ roadmap before importing source.
5. Import/migrate only through bounded Work Packages; do not bulk-copy into `main`.

## Safe work that may proceed without legacy source import
- semantic domain/contracts design;
- mechanics-discovery interfaces;
- normalization contracts and test vectors;
- Work Package/agent orchestration improvements;
- documentation and regression planning.

## Risk
Starting feature reconstruction before an authoritative legacy baseline is registered could duplicate already-working RTUDES functionality or lose regression-critical behavior. Therefore source migration remains gated until that baseline is available and registered.
