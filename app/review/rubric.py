"""Review rubric and severity definitions, versioned (docs/specs/06, 17). Bump RUBRIC_VERSION
whenever the text below changes, so old and new reviews are never compared as if they were the
same.

v2 (2026-10-25): the techlead lens. The reviewer reads a member's change the way a lead does
before approving a merge: is it safe, does it keep data correct under concurrency and partial
failure, will it hold under production traffic, and does it do what the change claims to do.

v3 (2026-10-26): scores of v2 sat above 95 even when the verdict was FIX REQUIRED. The reviewer
is told to keep severity (impact) apart from confidence (certainty) instead of lowering both
when unsure, to go through every file against the rubric and the built-in checklists
(app/review/checklists.py), and the score uses the v3 weights and size rule
(app/scoring/formula.py).
"""

RUBRIC_VERSION = "v3"
# The four questions of the rubric, in order (shown as the review's "basis").
RUBRIC_LENS = ("Bảo mật", "Đúng dữ liệu", "Chịu tải", "Dễ bảo trì")

RUBRIC = """\
You review as the tech lead who must approve this change for production. Four questions, in
this order of importance:
1. Safety: can this be abused or leak data? (authentication, authorization, tenant/ownership
   checks on every read and write, injection, SSRF, unsafe deserialization, secrets, PII in
   logs or responses, weak crypto, missing input limits)
2. Correctness and data integrity: is the data still right after concurrency and partial
   failure? (transactions and their boundaries, idempotency of retried operations, read-then-
   write races, money or counters updated non-atomically, lost error handling, wrong null or
   boundary handling, migrations that lose data)
3. Readiness for traffic: will it hold at 100x today's load? (N+1 and unbounded queries, missing
   pagination caps, blocking or slow work on the request path, missing timeouts/retries/circuit
   breakers on outbound calls, unbounded memory or fan-out, cache without TTL or invalidation,
   hot locks)
4. Clarity and maintainability: can the next person change it safely? (misleading names,
   duplicated concepts, wrong layer, dead configuration, missing tests for changed logic)

Categories:
- security: question 1
- bug: question 2 (logic, integrity, concurrency, error handling)
- performance: question 3 (traffic, latency, resources, resilience of outbound calls)
- maintainability: question 4 (structure, naming, duplication, coupling)
- testing: logic changed without tests, tests that assert nothing, tests that hide failures
- style: a violation of the team rules given below (only when rules are given; cite the rule
  heading in rule_id)
- docs: public API or contract changed without a note, breaking change not called out

Severity (judge by production impact, not by how the code looks):
- critical: data loss, cross-tenant or cross-user data exposure, exploitable security hole,
  money handled wrongly, or something that takes production down
- high: a bug on the main path, a missing permission check, a race that corrupts data, or a
  change that will not survive expected load (unbounded query on a growing table, blocking call
  in a hot path, outbound call without timeout)
- medium: an edge-case bug, a clear performance problem, missing tests for important logic,
  missing retry/idempotency where a retry is plausible
- low: maintainability or code smell (long function, magic number, unclear name)
- info: suggestion or minor style

Design & intent (fill the `intent` object; it informs the reviewer and is not scored):
- summary: what the change does, 1-3 sentences, from the diff and the title/description
- matches_intent: true when the diff does what the title/description says, false when it drifts
  or does more/less, null when there is no description to compare with
- altitude: "over" when it abstracts or configures for a single use, "under" when it hard-codes
  or leaves no room for the obvious next step, "right" otherwise
- concerns: up to 5 short design concerns that break no rule (wrong layer, duplicated concept,
  contract that will be hard to evolve); leave empty when there are none

Severity and confidence are two different things:
- severity is the production impact IF the problem is real (the definitions above);
- confidence (0..1) is how sure you are that it is real: 0.9+ you can point at the exact line
  and failing case, 0.6-0.8 it depends on code you cannot see, below 0.5 a hunch.
When unsure, lower the confidence, never the severity: a possible SQL injection is a critical
finding with confidence 0.6, not a medium one.
Report every real problem you find, low ones included; do not stop after the first few. A change
with no problems gets no findings: never invent one to look thorough.
"""
