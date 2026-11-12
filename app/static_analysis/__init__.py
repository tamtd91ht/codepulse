"""Kiểm tra tĩnh bổ trợ cho review AI (docs/specs/07, user 2026-11-12: phương án C).

Gitleaks (secret lộ trong code) and Semgrep (security and bug rules) run in the review worker on
the files a change touches, read at the reviewed commit; only what lies on the lines the change
adds is kept, so a person is never shown an old problem as theirs. The results:

- are kept apart (`review_static_findings`), shown on the review, and handed to the model as
  hints it confirms (as its own finding, rule `semgrep:…` / `gitleaks:…`) or dismisses;
- do not count in the review's score, verdict, the boards or the reports yet (shadow mode): the
  false-positive rate is measured first (docs/specs/07 §Chế độ chạy song song).

A tool that is not installed, times out or fails is reported in the review's basis and never
fails the review.
"""
