"""LLM-based triage analyzer for review comments."""

import json
import logging

from ..reviewers.llm import _extract_json_object
from ..reviewers.providers import LLMProvider, create_provider
from .models import (
    CommentClassification,
    Difficulty,
    RBComment,
    TriagedComment,
    TriageResult,
)


logger = logging.getLogger(__name__)


TRIAGE_SYSTEM_PROMPT = """\
You are a senior developer triaging review comments on your code.
Your job is to classify each comment and suggest the right response.

For each comment, determine:
1. classification: one of valid, confused, nitpick, outdated, already_fixed, duplicate
   - valid: reviewer found a real issue that should be fixed
   - confused: reviewer misunderstood the code or context
   - nitpick: style/preference issue, not a real problem
   - outdated: comment is about code that has changed since
   - already_fixed: the issue was already addressed
   - duplicate: same issue raised by another comment
2. difficulty: trivial, simple, moderate, or complex (null for non-fix items)
3. fix_hint: brief description of what to fix (empty for non-fix items)
4. reply_suggestion: suggested reply text to the reviewer

Respond with valid JSON matching this schema:
{
  "summary": "Brief overall assessment",
  "comments": [
    {
      "comment_id": 12345,
      "classification": "valid",
      "difficulty": "simple",
      "fix_hint": "Add null check before line 42",
      "reply_suggestion": "Good catch, will add a null check."
    }
  ]
}

Important:
- Be honest about valid issues -- don't dismiss real bugs
- For confused comments, write a polite, educational reply
- Keep reply suggestions concise and professional
- No emojis in reply text"""


class TriageAnalyzer:
    """Classifies review comments using an LLM."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str = "",
        max_tokens: int = 4096,
    ):
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens

    @classmethod
    def from_config(
        cls,
        provider_name: str,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        base_url: str | None = None,
        site_url: str | None = None,
        site_name: str = "BB Review",
    ) -> "TriageAnalyzer":
        """Create analyzer from config parameters."""
        llm = create_provider(
            provider=provider_name,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=0.1,  # low temp for deterministic classification
            base_url=base_url,
            site_url=site_url,
            site_name=site_name,
        )
        return cls(provider=llm, model=model, max_tokens=max_tokens)

    def analyze(
        self,
        comments: list[RBComment],
        diff: str,
        file_contexts: dict[str, str] | None = None,
        guidelines_text: str = "",
    ) -> TriageResult:
        """Triage a list of review comments.

        Args:
            comments: Comments to classify.
            diff: The diff being reviewed.
            file_contexts: Optional file context for better understanding.
            guidelines_text: Optional repo guidelines text.
        """
        if not comments:
            return TriageResult(review_request_id=0, summary="No comments to triage")

        rr_id = comments[0].review_id
        prompt = self._build_prompt(comments, diff, file_contexts, guidelines_text)

        logger.info(f"Triaging {len(comments)} comments ({len(prompt)} chars prompt)")

        try:
            response_text = self.provider.complete(TRIAGE_SYSTEM_PROMPT, prompt)
            return self._parse_response(response_text, comments, rr_id)
        except Exception as e:
            logger.error(f"Triage LLM call failed: {e}")
            raise

    def _build_prompt(
        self,
        comments: list[RBComment],
        diff: str,
        file_contexts: dict[str, str] | None = None,
        guidelines_text: str = "",
    ) -> str:
        parts: list[str] = []

        if guidelines_text:
            parts.append(f"## Repository Guidelines\n{guidelines_text}")

        parts.append(f"## Diff Under Review\n```diff\n{diff}\n```")

        if file_contexts:
            parts.append("## File Context")
            for path, context in file_contexts.items():
                parts.append(f"### {path}\n```\n{context}\n```")

        parts.append("## Comments to Triage")
        for c in comments:
            location = ""
            if c.file_path:
                location = f" ({c.file_path}"
                if c.line_number:
                    location += f":{c.line_number}"
                location += ")"
            kind = "body comment" if c.is_body_comment else "diff comment"
            issue = " [issue]" if c.issue_opened else ""
            parts.append(
                f"- comment_id={c.comment_id}, reviewer={c.reviewer}, "
                f'type={kind}{issue}{location}\n  "{c.text}"'
            )

        parts.append(
            "\n## Instructions\n"
            "Classify each comment above and respond as JSON. "
            "Use the exact comment_id values provided."
        )

        return "\n\n".join(parts)

    def _parse_response(
        self,
        response_text: str,
        comments: list[RBComment],
        rr_id: int,
    ) -> TriageResult:
        json_str = _extract_json_object(response_text)
        if json_str is None:
            logger.warning("Could not find JSON in triage response")
            return self._fallback_result(comments, rr_id)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse triage JSON: {e}")
            return self._fallback_result(comments, rr_id)

        # Build lookup by comment_id
        comment_map = {c.comment_id: c for c in comments}
        classified = data.get("comments", [])
        triaged: list[TriagedComment] = []

        for item in classified:
            cid = item.get("comment_id")
            source = comment_map.get(cid)
            if source is None:
                logger.debug(f"Triage returned unknown comment_id={cid}, skipping")
                continue

            try:
                classification = CommentClassification(item.get("classification", "valid"))
            except ValueError:
                classification = CommentClassification.VALID

            difficulty = None
            if item.get("difficulty"):
                try:
                    difficulty = Difficulty(item["difficulty"])
                except ValueError:
                    pass

            triaged.append(
                TriagedComment(
                    source=source,
                    classification=classification,
                    difficulty=difficulty,
                    fix_hint=item.get("fix_hint", ""),
                    reply_suggestion=item.get("reply_suggestion", ""),
                )
            )

        # Add any comments the LLM missed with default classification
        classified_ids = {t.source.comment_id for t in triaged}
        for c in comments:
            if c.comment_id not in classified_ids:
                triaged.append(
                    TriagedComment(
                        source=c,
                        classification=CommentClassification.VALID,
                    )
                )

        return TriageResult(
            review_request_id=rr_id,
            triaged_comments=triaged,
            summary=data.get("summary", ""),
        )

    def _fallback_result(self, comments: list[RBComment], rr_id: int) -> TriageResult:
        """Return all comments as unclassified (valid) when parsing fails."""
        return TriageResult(
            review_request_id=rr_id,
            triaged_comments=[
                TriagedComment(source=c, classification=CommentClassification.VALID) for c in comments
            ],
            summary="Failed to parse triage response -- all comments marked as valid",
        )
