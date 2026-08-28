# SEMANTIQ Baseline Manifest — RTUDES v1.8.9.14

## Source of truth

- Source artifact: `RTUDES_v1.8.9.14_DYNAMIC_RELATIONSHIP_BIND001(2).zip`
- Product: RAM-Tech Universal Data Extraction Studio
- Version: `1.8.9.14`
- Build: `RTUDES-1.8.9.14-DYNAMIC-RELATIONSHIP-BIND001`
- SHA-256: `8680afa2293564780ca562dd1111032d31bb5c176f2fefe2615f66e22fb39107`
- Verified ZIP entries: 257
- Verified Python source files: 128 total; protected executable baseline centered on root `app.py`

## Preservation rule

SEMANTIQ inherits this mature RTUDES baseline. Proven functionality must be preserved first and enhanced second. The protected executable baseline must not be split, moved, or rewritten merely for architectural cleanliness until regression evidence demonstrates that such change is safe and necessary.

## Verified local qualification before import

The following root Python modules compile successfully from the supplied baseline artifact:

- `app.py`
- `geo_reference.py`
- `interpretation_layer.py`
- `semantic_intent.py`

This compile result is pre-import evidence only. It does not replace repository CI or application regression testing after import.

## Pending validation obligations inherited from the source build

1. Validate selecting fields no longer resets Page-N element-list scroll.
2. Validate selected rows remain selected after Include/Exclude or rename refresh.
3. Validate weaker same-page duplicate output candidates are automatically suppressed.
4. Validate Run Extraction blocks unresolved duplicate output names.
5. Validate existing two-level IBBA-style extraction.
6. Validate the next three unrelated test pages/sites through Simple Mode.
7. Validate Ohio Sheriff Sales regression.

## SEM-003 objective

Controlled import of the verified mature RTUDES baseline into the SEMANTIQ repository while preserving the executable architecture, provenance, regression obligations, and evidence trail. Source import does not imply automatic rebranding or refactoring. Those follow only after baseline regression is established.
