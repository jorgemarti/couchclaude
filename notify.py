"""Hook notification script for couchclaude.

Called by Claude Code hooks to send notifications to Telegram.
Usage: python3 notify.py <completed|waiting|error>
"""

import glob
import json
import os
import sys

from config import load_config, validate_config
from telegram_api import TelegramAPI

PREFIXES = {
    "completed": "\u2705",
    "waiting": "\u2753",
    "error": "\u274c",
}


def find_latest_transcript():
    """Find the most recent Claude Code transcript file."""
    # Transcripts are UUID-named .jsonl files under ~/.claude/projects/
    # Exclude subagent files
    candidates = []
    for f in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True):
        if "/subagents/" not in f:
            candidates.append(f)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def extract_last_assistant_text(transcript_path):
    """Extract only the text content from the last assistant turn.

    Skips tool_use blocks — returns only what Claude actually said to the user.
    """
    last_text = None
    with open(transcript_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Transcript entries wrap the message inside a "message" key
            msg = entry.get("message", entry)
            if msg.get("role") != "assistant":
                continue

            content = msg.get("content", "")
            if isinstance(content, str):
                if content.strip():
                    last_text = content.strip()
                continue

            # content is a list of blocks — only grab text blocks
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block["text"])
                elif isinstance(block, str):
                    texts.append(block)
            if texts:
                last_text = "\n\n".join(texts).strip()

    return last_text


def truncate_message(text, max_length):
    """Smart truncation: keep start and end."""
    if len(text) <= max_length:
        return text
    marker = "\n\n\u2026 [truncated] \u2026\n\n"
    available = max_length - len(marker)
    head = available * 2 // 3
    tail = available - head
    return text[:head] + marker + text[-tail:]


def main():
    try:
        _run()
    except Exception:
        # Always exit 0 so Claude Code hooks never fail
        pass


def _run():
    hook_type = sys.argv[1] if len(sys.argv) > 1 else "completed"

    config = load_config()
    missing = validate_config(config)
    if missing:
        # Not configured — exit silently
        return

    api = TelegramAPI(config["telegram_bot_token"])
    chat_id = config["telegram_chat_id"]
    max_len = config.get("max_message_length", 4000)

    # Get the last assistant text from transcript
    message = None
    transcript = find_latest_transcript()
    if transcript:
        try:
            message = extract_last_assistant_text(transcript)
        except Exception:
            pass

    if not message:
        message = "(no text output)"

    prefix = PREFIXES.get(hook_type, "\u2139\ufe0f")
    label = hook_type.capitalize()

    full = f"{prefix} *{label}*\n\n{message}"
    truncated = truncate_message(full, max_len)

    try:
        api.send_message(chat_id, truncated, parse_mode="Markdown")
    except Exception:
        # Markdown parse can fail on special chars — fall back to plain
        plain = f"{prefix} {label}\n\n{message}"
        api.send_message(chat_id, truncate_message(plain, max_len), parse_mode=None)


if __name__ == "__main__":
    main()
