"""Standard QMT integration helpers.

The package deliberately keeps its protocol layer free of Qlib and third-party
dependencies so the same code can run in the QMT embedded Python runtime.
"""

from .protocol import (
    EOD_SCHEMA_VERSION,
    PLANNER_VERSION,
    RESULT_SCHEMA_VERSION,
    SIGNAL_SCHEMA_VERSION,
)

__all__ = [
    "PLANNER_VERSION",
    "SIGNAL_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "EOD_SCHEMA_VERSION",
]
