from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PipelineEvent:
    stage: str
    status: str
    message: str
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: dict[str, Any] | None = None


class PipelineTracker:
    """Collect pipeline progress updates for dashboards and reports."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, stage: str, status: str, message: str, **details: Any) -> None:
        event = PipelineEvent(
            stage=stage,
            status=status,
            message=message,
            details=details or None,
        )
        self.events.append({key: value for key, value in event.__dict__.items() if value is not None})

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.events)