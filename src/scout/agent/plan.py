"""Model-driven task plans and verification state.

The planner is exposed to the model as the `update_plan` tool. The agent records
the plan, emits events for the UI, and performs one verification pass before
accepting a final answer while steps are still pending or failed. The tracker is
thread-safe because the tool runs in a worker thread.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

STATUSES = ("pending", "active", "done", "failed", "skipped")


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    id: int
    description: str
    status: PlanStepStatus = PlanStepStatus.PENDING


@dataclass
class TaskPlan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [
                {"id": step.id, "description": step.description, "status": step.status.value}
                for step in self.steps
            ],
        }

    @property
    def outstanding(self) -> int:
        return sum(
            1
            for step in self.steps
            if step.status in (PlanStepStatus.PENDING, PlanStepStatus.ACTIVE)
        )

    @property
    def failed(self) -> int:
        return sum(1 for step in self.steps if step.status is PlanStepStatus.FAILED)


class PlanTracker:
    """Holds the current plan and whether verification has already run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plan: TaskPlan | None = None
        self._revision = 0
        self._verification_started = False

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def plan(self) -> TaskPlan | None:
        with self._lock:
            return self._plan

    def update(self, goal: str, steps: list[dict[str, Any]]) -> TaskPlan:
        normalized: list[PlanStep] = []
        for index, step in enumerate(steps, start=1):
            description = str(step.get("description", "")).strip()
            if not description:
                continue
            raw_status = str(step.get("status", "pending")).strip().lower()
            try:
                status = PlanStepStatus(raw_status)
            except ValueError:
                status = PlanStepStatus.PENDING
            normalized.append(PlanStep(id=index, description=description, status=status))
        with self._lock:
            self._plan = TaskPlan(goal=goal.strip(), steps=normalized)
            self._revision += 1
            return self._plan

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return self._plan.snapshot() if self._plan is not None else None

    def needs_verification(self) -> bool:
        with self._lock:
            if self._plan is None or self._verification_started:
                return False
            return self._plan.outstanding > 0 or self._plan.failed > 0

    @property
    def verification_used(self) -> bool:
        with self._lock:
            return self._verification_started

    def begin_verification(self) -> bool:
        """Claim the single verification pass. Returns False if already used."""
        with self._lock:
            if self._verification_started:
                return False
            self._verification_started = True
            return True

    def verification_note(self) -> str:
        with self._lock:
            if self._plan is None:
                return ""
            outstanding = [
                f"- {step.id}. {step.description} [{step.status.value}]"
                for step in self._plan.steps
                if step.status
                in (PlanStepStatus.PENDING, PlanStepStatus.ACTIVE, PlanStepStatus.FAILED)
            ]
        if not outstanding:
            return ""
        return (
            "Verification pass: your plan still has unfinished or failed steps:\n"
            + "\n".join(outstanding)
            + "\nCheck each one against tool results. If it is complete, call update_plan to "
            "mark it done. If it failed, fix it or explain the exact blocker. Only then reply "
            "with the final answer."
        )
