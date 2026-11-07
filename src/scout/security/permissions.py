"""Permission levels shared by tools and the approval policy.

Kept in its own module so the policy engine can reason about tool permissions
without importing the tool registry (which would create an import cycle).
"""

from __future__ import annotations

from enum import StrEnum


class PermissionLevel(StrEnum):
    """Central permission categories used by the policy engine."""

    READ = "read"
    LOW_RISK = "low_risk"
    WRITE = "write"
    EXECUTE = "execute"
    HIGH_RISK = "high_risk"
