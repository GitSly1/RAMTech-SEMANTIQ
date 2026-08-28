# Legacy Capability Migration Map

This document separates historical RTUDES capability knowledge from source that is actually present in SEMANTIQ Git.

| Capability | Evidence state | Migration disposition |
|---|---|---|
| Product identity / package bootstrap | VERIFIED-IN-REPO | Retain as SEMANTIQ foundation |
| Intent-first desired-output definition | KNOWN-LEGACY-NOT-IMPORTED | Re-specify under SEM-003 before implementation |
| Industry/subindustry semantic presets | KNOWN-LEGACY-NOT-IMPORTED | Re-specify under SEM-003 |
| DOM / API / JSON / table mechanics discovery | KNOWN-LEGACY-NOT-IMPORTED | Architecture target SEM-004 |
| Search/form and pagination/load-more/infinite-scroll discovery | KNOWN-LEGACY-NOT-IMPORTED | Architecture target SEM-004/SEM-008 |
| Deep field discovery with sample values | KNOWN-LEGACY-NOT-IMPORTED | Architecture target SEM-005 |
| Multi-level relationship traversal | KNOWN-LEGACY-NOT-IMPORTED | Architecture target SEM-006 |
| Structured resource / document extraction | KNOWN-LEGACY-NOT-IMPORTED | Map into SEM-008 |
| Address/name/phone/email/date/money/identifier normalization | PLANNED | Core target SEM-007 |
| Modern desktop guided workflow | PLANNED | Integration target SEM-009 |
| Reference-site regression qualification | PLANNED | SEM-010 |
| Packaging/migration/release qualification | PLANNED | SEM-011 |

## Rule
`KNOWN-LEGACY-NOT-IMPORTED` means capability history exists, but this repository does not yet contain verified executable source for it. No migration work may silently recreate or overwrite legacy behavior without a bounded Work Package and acceptance evidence.
