from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from mealprepper.models.grocery import GroceryList
from mealprepper.models.plans import WeeklyPlan


class WorkflowPhase(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    GROCERY = "grocery"
    ACTIVE = "active"
    COMPLETE = "complete"


@dataclass
class WorkflowState:
    """Lightweight state for the weekly meal planning cycle."""

    phase: WorkflowPhase = WorkflowPhase.IDLE
    week_start: date | None = None
    plan: WeeklyPlan | None = None
    grocery: GroceryList | None = None
    approval_request_id: str | None = None
    last_error: str = ""
    messages: list[str] = field(default_factory=list)

    def record(self, message: str) -> None:
        self.messages.append(message)
