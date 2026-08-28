# SEMANTIQ Agent Source Boundaries

Checkpoint: RVSC-013 — Git / Source Isolation

## Default write scope

Agents assigned to SEMANTIQ may write only to `GitSly1/RAMTech-SEMANTIQ` and only within paths declared by their active RVSC work package.

## Control-center boundary

`GitSly1/RAMTech-RVSC-Control-Center` is a separate control-plane repository. SEMANTIQ implementation agents may read its governance and work-package definitions but must not modify it unless the active work package explicitly grants that repository as a write target.

## Cross-project boundary

Source from other RAMTech products must not be copied into this repository merely for convenience or coordination. Reuse must be performed through an explicitly approved shared component, package, library, or separately governed migration work package.

## Branch rule

Implementation work uses an RVSC work branch and a pull request. Product-development changes should not be committed directly to `main`.

## Required handoff

Every completed agent assignment must identify:
- work-package ID
- files changed
- tests/validation performed
- unresolved risks or assumptions
- commit or pull-request reference

These boundaries remain in force unless superseded by a later RVSC governance checkpoint.
