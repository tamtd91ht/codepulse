# CodePulse

GitLab commit/MR collector + AI code review with severity scoring per member, aggregated into weekly/monthly reports. The project is spec-driven: the specs in `docs/specs/` are the source of truth.

## Agent rules

@docs/specs/AGENTS.md

## Where things are

- `docs/specs/00_README.md` – overview and the required reading order.
- `docs/specs/14_TASKS_CHECKLIST.md` – the task list; work one task at a time, in phase order.
- `docs/specs/02_ARCHITECTURE.md` – stack (Python 3.12, FastAPI, PostgreSQL 16, Redis) and the target directory layout.
- `docs/specs/12_TESTING_QA.md` – test layers and coverage thresholds.
- `PROGRESS.md` (repo root) – 1–3 lines per finished task; `IDEAS.md` – ideas outside the spec.

## Before starting a task

1. Read the spec file the task references (the number in parentheses, e.g. `(04)`).
2. Anything marked `[DECISION]` (including all of Phase 0): stop and ask, do not choose.
3. After each phase's **Demo** checkpoint, stop and wait for the user's confirmation.

## Code review

Before committing a non-trivial change, delegate a review of the diff to the `backend-reviewer` subagent and fix critical/high findings.
