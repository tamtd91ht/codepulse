"""Engineering Insights (docs/specs/20): team-level flow, review load, pipelines, knowledge risk.

Pure computations over the domain layer. Kept apart from `app.scoring` on purpose (§3.2): no
review score or finding ever feeds these numbers. Bump INSIGHTS_VERSION whenever a definition
in §4 changes, and record the change in the spec.
"""

INSIGHTS_VERSION = "3"  # 2: by component map (§4.9b); 3: a tree, warnings counted once (§4.9c)
