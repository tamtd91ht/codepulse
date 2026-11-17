"""CodePulse's own task tracker (docs/specs/31), tied to git and code review."""

# The session hooks that keep the DPI rows of tasks current (§15) register on import.
from app.tasks import dpi as _dpi  # noqa: F401
from app.tasks import search as _search  # noqa: F401  (§21.4 keeps tasks.search_text)
