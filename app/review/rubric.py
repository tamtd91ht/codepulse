"""Review rubric and severity definitions, versioned (docs/specs/06). Bump RUBRIC_VERSION whenever
the text below changes, so old and new reviews are never compared as if they were the same."""

RUBRIC_VERSION = "v1"

RUBRIC = """\
Categories:
- security: injection, hard-coded secrets, missing authorization, unsafe deserialization, SSRF, XSS
- bug: wrong logic, null/undefined, race conditions, missing error handling, off-by-one
- performance: N+1 queries, heavy loops, obviously missing index or caching, memory leaks
- maintainability: overly long or complex functions, duplication, poor naming, coupling
- testing: logic changed without tests, tests that assert nothing
- style: violations of the team conventions given below (only if conventions are given)
- docs: public API without docstring, breaking change without a note

Severity:
- critical: can lose data, an exploitable security hole, or take production down
  (e.g. SQL injection, leaked secret, deleting data on a wrong condition)
- high: a bug that will happen on the main path, or a significant security risk
  (e.g. null dereference on the main path, missing permission check)
- medium: an edge-case bug, a clear performance problem, missing tests for important logic
  (e.g. N+1 query, no timeout handling)
- low: maintainability or code smell (e.g. 150-line function, magic number)
- info: suggestion or minor style (e.g. rename a variable)
"""
