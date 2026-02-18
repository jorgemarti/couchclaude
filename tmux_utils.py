"""tmux interaction helpers for couchclaude."""

import os
import re
import subprocess
import time

SNAPSHOT_PATH = os.path.expanduser("~/.couchclaude/.last_snapshot")


def session_exists(session_name):
    """Check if a tmux session exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def capture_pane(session_name, lines=50):
    """Capture the last N lines from the tmux pane."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", f"-{lines}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux capture-pane failed: {result.stderr.strip()}")
    return result.stdout


# Claude Code's input prompt indicator (❯ or > at start of line)
INPUT_PROMPT_RE = re.compile(r"^[\s]*[❯>]\s*$", re.MULTILINE)


def wait_for_input(session_name, timeout=30):
    """Wait until Claude Code's input prompt is visible. Returns True if found."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            screen = capture_pane(session_name, lines=5)
            if INPUT_PROMPT_RE.search(screen):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def send_keys(session_name, text, enter=True):
    """Send keystrokes to a tmux session."""
    # Use -l (literal) to send text as-is, then Enter separately
    result = subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "-l", text],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux send-keys failed: {result.stderr.strip()}")
    if enter:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "Enter"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"tmux send-keys Enter failed: {result.stderr.strip()}")


def save_snapshot(session_name, lines=200):
    """Save current tmux pane content as the 'last interaction' snapshot."""
    try:
        content = capture_pane(session_name, lines=lines)
        with open(SNAPSHOT_PATH, "w") as f:
            f.write(content)
    except Exception:
        pass


def load_snapshot():
    """Load the last interaction snapshot. Returns list of lines or empty list."""
    if not os.path.exists(SNAPSHOT_PATH):
        return []
    with open(SNAPSHOT_PATH, "r") as f:
        return f.read().splitlines()


def get_new_content(session_name, lines=200):
    """Capture current screen and return only content new since last snapshot."""
    current = capture_pane(session_name, lines=lines)
    current_lines = current.splitlines()
    old_lines = load_snapshot()

    if not old_lines:
        return current.strip()

    # Find where the old content ends in the current content.
    # Walk backwards through current to find the last line of old content,
    # then return everything after it.
    old_stripped = [l.rstrip() for l in old_lines if l.strip()]
    if not old_stripped:
        return current.strip()

    last_old = old_stripped[-1]

    # Find the last occurrence of the last old line in current
    best = -1
    for i, line in enumerate(current_lines):
        if line.rstrip() == last_old:
            best = i

    if best >= 0 and best < len(current_lines) - 1:
        new_lines = current_lines[best + 1:]
        result = "\n".join(new_lines).strip()
        if result:
            return result

    # Fallback: couldn't find overlap, return last 30 lines
    return "\n".join(current_lines[-30:]).strip()


def get_session_info(session_name):
    """Get info about a tmux session. Returns dict or None."""
    if not session_exists(session_name):
        return None
    result = subprocess.run(
        ["tmux", "display-message", "-t", session_name, "-p",
         "#{session_name} #{pane_current_path} #{pane_current_command}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split(" ", 2)
    return {
        "session": parts[0] if len(parts) > 0 else session_name,
        "cwd": parts[1] if len(parts) > 1 else "unknown",
        "command": parts[2] if len(parts) > 2 else "unknown",
    }
