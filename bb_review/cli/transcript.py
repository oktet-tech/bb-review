"""Transcript pretty-printer for agent review sessions."""

import json
from pathlib import Path
import sys

import click

from . import main


@main.command("transcript")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--raw", is_flag=True, help="Pretty-print full JSON without filtering")
def transcript_cmd(file: Path, raw: bool) -> None:
    """Pretty-print an agent transcript file.

    Reads transcript files saved by --transcript from claude/codex/opencode
    commands and displays them in a readable format.

    Supports Claude (JSON array), Codex (JSONL), and OpenCode (text log).
    """
    content = file.read_text().strip()
    if not content:
        click.echo("Empty transcript file.", err=True)
        sys.exit(1)

    if raw:
        _print_raw_json(content)
        return

    # Detect format and dispatch
    if content.startswith("["):
        _print_claude_transcript(content)
    elif content.startswith("{"):
        _print_codex_transcript(content)
    else:
        # OpenCode or unknown -- just print as-is
        click.echo(content)


def _print_raw_json(content: str) -> None:
    """Pretty-print JSON/JSONL with indentation."""
    try:
        data = json.loads(content)
        click.echo(json.dumps(data, indent=2))
    except json.JSONDecodeError:
        # Try JSONL
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                click.echo(json.dumps(obj, indent=2))
            except json.JSONDecodeError:
                click.echo(line)


def _print_claude_transcript(content: str) -> None:
    """Format Claude --verbose JSON array transcript."""
    try:
        events = json.loads(content)
    except json.JSONDecodeError:
        click.echo("Failed to parse Claude transcript JSON", err=True)
        click.echo(content)
        return

    if not isinstance(events, list):
        events = [events]

    for event in events:
        event_type = event.get("type", "unknown")

        if event_type == "system" and event.get("subtype") == "init":
            model = event.get("model", "?")
            cwd = event.get("cwd", "?")
            version = event.get("claude_code_version", "?")
            session = event.get("session_id", "?")[:8]
            click.echo(f"--- INIT: model={model} cwd={cwd} version={version} session={session}...")
            click.echo()

        elif event_type == "assistant":
            msg = event.get("message", {})
            model = msg.get("model", "")
            contents = msg.get("content", [])
            usage = msg.get("usage", {})

            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            click.echo(f"--- ASSISTANT [{model}] (in={in_tok} out={out_tok} cache={cache_read})")

            for block in contents:
                block_type = block.get("type", "")
                if block_type == "text":
                    click.echo(block.get("text", ""))
                elif block_type == "tool_use":
                    tool = block.get("name", "?")
                    tool_input = block.get("input", {})
                    # Show tool call compactly
                    input_str = json.dumps(tool_input, ensure_ascii=False)
                    if len(input_str) > 300:
                        input_str = input_str[:300] + "..."
                    click.echo(f"  >> TOOL: {tool}")
                    click.echo(f"     {input_str}")
                elif block_type == "tool_result":
                    content_val = block.get("content", "")
                    if isinstance(content_val, str) and len(content_val) > 500:
                        content_val = content_val[:500] + "..."
                    click.echo(f"  << RESULT: {content_val}")
            click.echo()

        elif event_type == "result":
            subtype = event.get("subtype", "?")
            duration = event.get("duration_ms", 0)
            turns = event.get("num_turns", 0)
            cost = event.get("total_cost_usd", 0)
            result_text = event.get("result", "")

            click.echo(f"--- RESULT: {subtype} turns={turns} duration={duration}ms cost=${cost:.4f}")
            if result_text:
                if len(result_text) > 200:
                    click.echo(f"  {result_text[:200]}...")
                else:
                    click.echo(f"  {result_text}")

            # Show model usage breakdown
            model_usage = event.get("modelUsage", {})
            for model_name, usage in model_usage.items():
                in_tok = usage.get("inputTokens", 0)
                out_tok = usage.get("outputTokens", 0)
                cache_create = usage.get("cacheCreationInputTokens", 0)
                cache_read = usage.get("cacheReadInputTokens", 0)
                cost_usd = usage.get("costUSD", 0)
                click.echo(
                    f"  {model_name}: in={in_tok} out={out_tok} "
                    f"cache_create={cache_create} cache_read={cache_read} "
                    f"cost=${cost_usd:.4f}"
                )
            click.echo()

        elif event_type == "rate_limit_event":
            # Skip noise
            pass

        else:
            # Unknown event type -- show type and compact dump
            click.echo(f"--- {event_type.upper()}")
            compact = json.dumps(event, ensure_ascii=False)
            if len(compact) > 500:
                compact = compact[:500] + "..."
            click.echo(f"  {compact}")
            click.echo()


def _print_codex_transcript(content: str) -> None:
    """Format Codex JSONL event stream."""
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            click.echo(line)
            continue

        event_type = event.get("type", "unknown")

        if event_type == "thread.started":
            thread_id = event.get("thread_id", "?")[:8]
            click.echo(f"--- THREAD: {thread_id}...")
            click.echo()

        elif event_type == "item.started":
            item = event.get("item", {})
            item_type = item.get("type", "?")
            if item_type == "command_execution":
                cmd = item.get("command", "?")
                click.echo(f"  >> EXEC: {cmd}")

        elif event_type == "item.completed":
            item = event.get("item", {})
            item_type = item.get("type", "?")
            if item_type == "command_execution":
                output = item.get("aggregated_output", "")
                exit_code = item.get("exit_code", "?")
                if len(output) > 500:
                    output = output[:500] + "..."
                click.echo(f"  << EXIT={exit_code}")
                if output.strip():
                    for out_line in output.strip().splitlines()[:20]:
                        click.echo(f"     {out_line}")
                click.echo()
            elif item_type == "agent_message":
                text = item.get("text", "")
                click.echo("--- AGENT MESSAGE:")
                click.echo(text)
                click.echo()

        elif event_type == "turn.started":
            pass  # noise

        elif event_type == "turn.completed":
            usage = event.get("usage", {})
            in_tok = usage.get("input_tokens", 0)
            out_tok = usage.get("output_tokens", 0)
            if in_tok or out_tok:
                click.echo(f"--- TURN COMPLETE: in={in_tok} out={out_tok}")
                click.echo()

        else:
            compact = json.dumps(event, ensure_ascii=False)
            if len(compact) > 300:
                compact = compact[:300] + "..."
            click.echo(f"--- {event_type}: {compact}")
            click.echo()
