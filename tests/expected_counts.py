"""How many checks the fast gate must execute.

This is an EQUALITY, not a floor. A floor drifts silently: you add a test, the
number stays stale, and nobody notices. Adding or removing a test is meant to be
loud, so bump this number in the same commit.
"""

EXPECTED_CHECKS = 79
