"""Models for the review queue."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class QueueStatus(str, Enum):
    """Status of a queue item."""

    TODO = "todo"
    NEXT = "next"
    IGNORE = "ignore"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


# Valid state transitions: current_status -> set of allowed next statuses
VALID_TRANSITIONS: dict[QueueStatus, set[QueueStatus]] = {
    QueueStatus.TODO: {QueueStatus.NEXT, QueueStatus.IGNORE},
    QueueStatus.NEXT: {QueueStatus.IN_PROGRESS, QueueStatus.IGNORE, QueueStatus.TODO},
    QueueStatus.IGNORE: {QueueStatus.NEXT},
    QueueStatus.IN_PROGRESS: {QueueStatus.DONE, QueueStatus.FAILED},
    QueueStatus.FAILED: {QueueStatus.NEXT, QueueStatus.IGNORE},
    QueueStatus.DONE: {QueueStatus.TODO},
}


@dataclass
class QueueItem:
    """A review request in the queue."""

    id: int
    review_request_id: int
    diff_revision: int
    status: QueueStatus
    repository: str | None = None
    submitter: str | None = None
    summary: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    rb_created_at: datetime | None = None
    synced_at: datetime | None = None
    updated_at: datetime | None = None
    analysis_id: int | None = None
    error_message: str | None = None
