"""CodePulse's own task tracker (docs/specs/31), tied to git and code review."""

# The session hooks that keep the DPI rows of tasks current (§15) register on import.
from app.tasks import dpi as _dpi  # noqa: F401

# The flush hooks that push task changes live to open Boards (§21.7) register on import.
from app.tasks import live as _live  # noqa: F401
