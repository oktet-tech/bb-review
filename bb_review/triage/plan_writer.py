"""YAML read/write for fix plans."""

from datetime import datetime
import logging
from pathlib import Path

import yaml

from .models import (
    CommentClassification,
    Difficulty,
    FixPlan,
    FixPlanItem,
    TriageAction,
)


logger = logging.getLogger(__name__)


def write_fix_plan(plan: FixPlan, path: Path) -> None:
    """Write a fix plan to a YAML file."""
    data = {
        "review_request_id": plan.review_request_id,
        "repository": plan.repository,
        "created_at": plan.created_at.isoformat(),
        "items": [_item_to_dict(item) for item in plan.items],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
    logger.info(f"Wrote fix plan to {path} ({len(plan.items)} items)")


def read_fix_plan(path: Path) -> FixPlan:
    """Read a fix plan from a YAML file."""
    data = yaml.safe_load(path.read_text())
    items = [_dict_to_item(d) for d in data.get("items", [])]
    created_at = data.get("created_at", "")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            created_at = datetime.now()
    return FixPlan(
        review_request_id=data["review_request_id"],
        repository=data.get("repository", ""),
        created_at=created_at,
        items=items,
    )


def _item_to_dict(item: FixPlanItem) -> dict:
    d: dict = {
        "comment_id": item.comment_id,
        "action": item.action.value,
        "file_path": item.file_path,
        "line_number": item.line_number,
        "classification": item.classification.value if item.classification else None,
        "difficulty": item.difficulty.value if item.difficulty else None,
        "reviewer": item.reviewer,
        "original_text": item.original_text,
        "fix_hint": item.fix_hint,
    }
    if item.reply_text:
        d["reply_text"] = item.reply_text
    return d


def _dict_to_item(d: dict) -> FixPlanItem:
    classification = None
    if d.get("classification"):
        try:
            classification = CommentClassification(d["classification"])
        except ValueError:
            pass

    difficulty = None
    if d.get("difficulty"):
        try:
            difficulty = Difficulty(d["difficulty"])
        except ValueError:
            pass

    try:
        action = TriageAction(d.get("action", "skip"))
    except ValueError:
        action = TriageAction.SKIP

    return FixPlanItem(
        comment_id=d["comment_id"],
        action=action,
        file_path=d.get("file_path"),
        line_number=d.get("line_number"),
        classification=classification,
        difficulty=difficulty,
        reviewer=d.get("reviewer", ""),
        original_text=d.get("original_text", ""),
        fix_hint=d.get("fix_hint", ""),
        reply_text=d.get("reply_text", ""),
    )
