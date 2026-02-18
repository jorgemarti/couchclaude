"""Telegram polling daemon for couchclaude.

Long-running process that polls Telegram for messages and injects them
into the tmux session running Claude Code.
"""

import hashlib
import logging
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata

from config import load_config, setup_logging, validate_config
from telegram_api import TelegramAPI
from tmux_utils import capture_pane, get_session_info, save_snapshot, send_keys, wait_for_input

DOWNLOAD_DIR = os.path.expanduser("~/.couchclaude/downloads")

start_time = time.time()
running = True
last_prompt_hash = None  # Track last forwarded prompt to avoid duplicates

log = logging.getLogger("couchclaude")


def handle_signal(signum, frame):
    global running
    running = False


def format_uptime():
    elapsed = int(time.time() - start_time)
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def handle_command(api, chat_id, config, text):
    """Handle /commands from Telegram. Returns True if handled."""
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    session = config["tmux_session"]

    if cmd == "/ping":
        api.send_message(chat_id, f"pong \u2014 uptime: {format_uptime()}")
        return True

    if cmd == "/esc":
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", session, "Escape"],
                capture_output=True, text=True, check=True,
            )
            api.send_message(chat_id, "\u23cf\ufe0f Escape sent")
        except Exception as e:
            api.send_message(chat_id, f"\u274c Error: {e}")
        return True

    if cmd == "/help":
        help_text = (
            "<b>couchclaude commands</b>\n\n"
            "/ping \u2014 Check if online\n"
            "/status \u2014 Session status\n"
            "/view or /screen \u2014 Current terminal (last 50 lines)\n"
            "/esc \u2014 Send Escape key (exit menus, cancel prompts)\n"
            "/cd &lt;path&gt; \u2014 Change directory\n"
            "/cmd &lt;command&gt; \u2014 Run shell command\n"
            "/help \u2014 This message\n\n"
            "\U0001f4f7 Send a photo or file to forward it to Claude"
        )
        api.send_message(chat_id, help_text)
        return True

    if cmd == "/status":
        info = get_session_info(session)
        if info:
            msg = (
                f"\U0001f7e2 Session: <code>{info['session']}</code>\n"
                f"\U0001f4c2 CWD: <code>{info['cwd']}</code>\n"
                f"\u2699\ufe0f Command: <code>{info['command']}</code>\n"
                f"\u23f1 Uptime: {format_uptime()}"
            )
        else:
            msg = f"\U0001f534 tmux session <code>{session}</code> not found"
        api.send_message(chat_id, msg)
        return True

    if cmd in ("/view", "/screen"):
        try:
            content = capture_pane(session, lines=50)
            content = sanitize_text(content).strip()
            if not content:
                content = "(empty screen)"
            if len(content) > 3900:
                content = content[-3900:]
            api.send_message(chat_id, content, parse_mode=None)
        except Exception as e:
            api.send_message(chat_id, f"\u274c Error: {e}")
        return True

    if cmd == "/cd" and arg:
        try:
            send_keys(session, f"cd {arg}")
            time.sleep(0.5)
            save_snapshot(session)
            api.send_message(chat_id, f"\U0001f4e8 Sent: cd {arg}")
        except Exception as e:
            api.send_message(chat_id, f"\u274c Error: {e}")
        return True

    if cmd == "/cmd" and arg:
        try:
            send_keys(session, arg)
            time.sleep(0.5)
            save_snapshot(session)
            api.send_message(chat_id, f"\U0001f4e8 Sent: {arg}")
        except Exception as e:
            api.send_message(chat_id, f"\u274c Error: {e}")
        return True

    return False


def download_photo(api, msg):
    """Download a photo from Telegram. Returns (local_path, caption) or raises."""
    photos = msg.get("photo", [])
    if not photos:
        return None, ""
    photo = photos[-1]
    file_path = api.get_file(photo["file_id"])
    ext = os.path.splitext(file_path)[1] or ".jpg"
    local_name = f"photo_{int(time.time())}_{photo['file_id'][-6:]}{ext}"
    local_path = os.path.join(DOWNLOAD_DIR, local_name)
    api.download_file(file_path, local_path)
    return local_path, msg.get("caption", "")


def download_document(api, msg):
    """Download a document from Telegram. Returns (local_path, caption) or raises."""
    doc = msg.get("document", {})
    file_path = api.get_file(doc["file_id"])
    file_name = doc.get("file_name", "file")
    local_path = os.path.join(DOWNLOAD_DIR, file_name)
    api.download_file(file_path, local_path)
    return local_path, msg.get("caption", "")


def send_files_to_claude(api, chat_id, session, files):
    """Send one or more downloaded files to Claude as a single message."""
    paths = [f[0] for f in files]
    captions = [f[1] for f in files if f[1]]

    if captions:
        # Caption is the primary message; files are context
        caption_text = " | ".join(captions)
        if len(paths) == 1:
            prompt = f"{caption_text}\n\n(Image attached from Telegram, saved at: {paths[0]})"
        else:
            file_list = " and ".join(paths)
            prompt = f"{caption_text}\n\n({len(paths)} files attached from Telegram, saved at: {file_list})"
    else:
        if len(paths) == 1:
            prompt = f"The user sent a file from Telegram. View it at: {paths[0]}"
        else:
            file_list = " and ".join(paths)
            prompt = f"The user sent {len(paths)} files from Telegram. View them at: {file_list}"

    # Wait for Claude's input prompt before injecting
    if not wait_for_input(session, timeout=30):
        log.warning("Timed out waiting for Claude input prompt, sending anyway")
        api.send_message(chat_id, "\u26a0\ufe0f Claude not at input prompt, file may need manual Enter", parse_mode=None)

    send_keys(session, prompt)
    time.sleep(0.5)
    save_snapshot(session)
    log.info("Files sent to Claude: %s", paths)

    count = len(paths)
    label = "Photo" if count == 1 else f"{count} files"
    api.send_message(chat_id, f"\U0001f4f7 {label} sent to Claude", parse_mode=None)


def handle_callback(api, chat_id, config, callback_query):
    """Handle inline button presses — inject the selected option into tmux."""
    session = config["tmux_session"]
    data = callback_query.get("data", "")
    query_id = callback_query["id"]

    try:
        # data is the option number (e.g., "1", "2")
        send_keys(session, data)
        time.sleep(0.5)
        save_snapshot(session)
        log.info("Callback: sent option %s to tmux", data)
        api.answer_callback_query(query_id, text=f"Sent: {data}")
    except Exception as e:
        log.error("Callback handling failed: %s", e)
        api.answer_callback_query(query_id, text=f"Error: {e}")


last_ratelimit_hash = None  # Track last rate limit notification
ratelimit_waiting = False   # True when user chose to wait for limit reset

# Regex to strip ANSI escape sequences
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[()][0-9A-B]")

# Detection: is Claude Code showing a prompt? Check for UI chrome at bottom.
PROMPT_DETECT_RE = re.compile(
    r"(Enter to select.*to navigate.*Esc to cancel"
    r"|Enter to confirm.*Esc to cancel"
    r"|Esc to cancel.*Tab to amend"
    r"|Do you want to allow)",
    re.IGNORECASE,
)

# Chrome lines to filter out of the displayed message (UI navigation hints only)
SELECTOR_RE = re.compile(
    r"(Enter to select.*to navigate.*Esc to cancel"
    r"|Enter to confirm.*Esc to cancel"
    r"|Esc to cancel.*Tab to amend)",
    re.IGNORECASE,
)

# Pattern to extract numbered options like "❯ 1. Yes" or "  2. No"
OPTION_RE = re.compile(r"^[\s❯>]*(\d+)\.\s+(.+)$")


def sanitize_text(text):
    """Remove ANSI escape codes and non-printable characters from text."""
    text = ANSI_RE.sub("", text)
    text = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or (not unicodedata.category(ch).startswith("C"))
    )
    return text


def parse_prompt_parts(screen):
    """Parse a Claude Code prompt screen into structured parts."""
    lines = sanitize_text(screen).splitlines()

    options = []
    question = None
    first_option_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or all(c in "─━─-_" for c in stripped):
            continue
        if SELECTOR_RE.search(stripped):
            continue

        m = OPTION_RE.match(line)
        if m:
            options.append(f"{m.group(1)}. {m.group(2).strip()}")
            if first_option_idx is None:
                first_option_idx = i

    if not options:
        return None

    if first_option_idx is not None:
        for i in range(first_option_idx - 1, -1, -1):
            stripped = lines[i].strip()
            if not stripped or all(c in "─━─-_" for c in stripped):
                continue
            if OPTION_RE.match(lines[i]):
                continue
            cleaned = re.sub(r'^[❓❯>?\s]+', '', stripped).strip()
            if cleaned and len(cleaned) > 5:
                question = cleaned
                break

    return {"question": question, "options": options}


def parse_prompt(screen):
    """Parse a Claude Code prompt screen into a clean message string."""
    parsed = parse_prompt_parts(screen)
    if not parsed:
        return None

    parts = []
    if parsed["question"]:
        parts.append(parsed["question"])
    parts.append("\n".join(parsed["options"]))
    return "\n\n".join(parts)


# Rate limit detection — matches actual Claude Code messages:
#   "You've hit your limit · resets Jan 30 at 12pm (America/Mazatlan)"
#   "Claude usage limit reached. Your limit will reset at 6pm (Europe/Madrid)."
RATELIMIT_RE = re.compile(
    r"(hit your limit|usage limit reached|limit will reset|/upgrade to increase)",
    re.IGNORECASE,
)

# Extract the reset time from the limit message
RESET_TIME_RE = re.compile(
    r"(resets?\s+.{5,50}?\)"       # "resets Jan 30 at 12pm (America/Mazatlan)"
    r"|reset at\s+.{3,30}?\))",    # "reset at 6pm (Europe/Madrid)."
    re.IGNORECASE,
)


def check_for_ratelimit(api, chat_id, session):
    """Check tmux screen for rate limit / token exhaustion messages."""
    global last_ratelimit_hash, ratelimit_waiting

    try:
        screen = capture_pane(session, lines=20)
    except Exception:
        return

    clean = sanitize_text(screen).strip()
    if not clean:
        return

    if not RATELIMIT_RE.search(clean):
        # Rate limit screen gone — if we were waiting, the limit has reset
        if ratelimit_waiting:
            ratelimit_waiting = False
            log.info("Rate limit cleared — notifying user")
            try:
                api.send_message(chat_id, "\u2705 Rate limit reset — Claude is ready again!")
            except Exception as e:
                log.error("Rate limit recovery notify failed: %s", e)
        last_ratelimit_hash = None
        return

    # Extract relevant lines
    relevant = []
    for line in clean.splitlines():
        stripped = line.strip()
        if stripped and RATELIMIT_RE.search(stripped):
            # Clean up leading terminal chrome (└, │, etc.)
            cleaned = re.sub(r'^[└│├─\s]+', '', stripped).strip()
            if cleaned:
                relevant.append(cleaned)

    if not relevant:
        return

    content = "\n".join(relevant)

    # Deduplicate
    h = hashlib.md5(content.encode()).hexdigest()
    if h == last_ratelimit_hash:
        return
    last_ratelimit_hash = h

    # Extract reset time
    reset_match = RESET_TIME_RE.search(clean)
    reset_info = ""
    if reset_match:
        reset_text = reset_match.group(0).strip().rstrip(".")
        reset_info = f"\n\u23f0 {reset_text}"

    msg = f"\U0001f6d1 *Rate limit reached*\n\n{content}{reset_info}"
    if len(msg) > 4000:
        msg = msg[:4000]

    ratelimit_waiting = True
    log.info("Rate limit detected")

    try:
        api.send_message(chat_id, msg, parse_mode="Markdown")
    except Exception:
        try:
            api.send_message(chat_id, msg, parse_mode=None)
        except Exception as e:
            log.error("Rate limit notify failed: %s", e)


def check_for_prompts(api, chat_id, session):
    """Check tmux screen for permission prompts and forward to Telegram."""
    global last_prompt_hash

    try:
        screen = capture_pane(session, lines=20)
    except Exception:
        return

    screen = screen.strip()
    if not screen:
        return

    # Only trigger when Claude Code's prompt UI is visible at the bottom
    if not PROMPT_DETECT_RE.search(screen):
        last_prompt_hash = None
        return

    prompt_text = parse_prompt(screen)
    if not prompt_text:
        return

    # Hash to avoid duplicates
    h = hashlib.md5(prompt_text.encode()).hexdigest()
    if h == last_prompt_hash:
        return
    last_prompt_hash = h

    log.info("Prompt detected, forwarding to Telegram")
    log.debug("Prompt text: %s", prompt_text)

    # Build inline keyboard buttons from the options
    parsed = parse_prompt_parts(screen)
    buttons = []
    # Emoji prefixes for button options
    btn_emojis = ["\u2705", "\U0001f504", "\u270d\ufe0f", "\U0001f4ac",
                  "\u2b50", "\U0001f517", "\U0001f527", "\u2699\ufe0f"]
    if parsed and parsed["options"]:
        for i, opt in enumerate(parsed["options"]):
            num = opt.split(".")[0].strip()
            label = opt.split(".", 1)[1].strip() if "." in opt else opt
            emoji = btn_emojis[i] if i < len(btn_emojis) else "\u25b6\ufe0f"
            buttons.append({"text": f"{emoji} {label}", "callback_data": num})

    msg = f"\u2753 *Prompt*\n\n{prompt_text}"
    if len(msg) > 4000:
        msg = msg[:4000]

    try:
        if buttons:
            api.send_message_with_buttons(chat_id, msg, buttons, parse_mode="Markdown")
        else:
            api.send_message(chat_id, msg, parse_mode="Markdown")
    except Exception:
        try:
            api.send_message(chat_id, f"\u2753 Prompt\n\n{prompt_text}", parse_mode=None)
        except Exception as e:
            log.error("Prompt notify failed: %s", e)


def main(daemon=False):
    global running

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    config = load_config()
    setup_logging(config, daemon=daemon)

    missing = validate_config(config)
    if missing:
        log.error("Missing config fields: %s", missing)
        sys.exit(1)

    api = TelegramAPI(config["telegram_bot_token"])
    chat_id = config["telegram_chat_id"]
    session = config["tmux_session"]
    poll_interval = config.get("poll_interval", 2)

    # Announce online
    try:
        api.send_message(chat_id, "\U0001f7e2 couchclaude online")
    except Exception as e:
        log.warning("Could not send startup message: %s", e)

    offset = None
    last_screen_check = 0
    screen_check_interval = 5  # Check tmux screen every 5 seconds

    log.info("couchclaude polling started (session=%s, chat_id=%s, log_level=%s)",
             session, chat_id, config.get("log_level", "INFO"))

    while running:
        # Use shorter timeout so we can check for prompts between polls
        try:
            updates = api.get_updates(offset=offset, timeout=5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("Polling error: %s", e)
            time.sleep(poll_interval)
            continue

        # Periodically check tmux screen for permission prompts
        now = time.time()
        if now - last_screen_check >= screen_check_interval:
            last_screen_check = now
            check_for_prompts(api, chat_id, session)
            check_for_ratelimit(api, chat_id, session)

        # Collect files from this batch to send as one message
        pending_files = []

        for update in updates:
            offset = update["update_id"] + 1

            # Handle inline button callbacks
            cb = update.get("callback_query")
            if cb:
                cb_chat = cb.get("message", {}).get("chat", {}).get("id")
                if cb_chat == chat_id:
                    try:
                        handle_callback(api, chat_id, config, cb)
                    except Exception as e:
                        log.error("Callback error: %s", e)
                continue

            msg = update.get("message")
            if not msg:
                continue

            # Only process messages from authorized chat
            if msg.get("chat", {}).get("id") != chat_id:
                log.debug("Ignoring message from chat_id=%s", msg.get("chat", {}).get("id"))
                continue

            # Collect photos and documents — send batched after loop
            if msg.get("photo"):
                try:
                    path, caption = download_photo(api, msg)
                    if path:
                        pending_files.append((path, caption))
                except Exception as e:
                    log.error("Photo download error: %s", e)
                    api.send_message(chat_id, f"\u274c Photo error: {e}", parse_mode=None)
                continue

            if msg.get("document"):
                try:
                    path, caption = download_document(api, msg)
                    if path:
                        pending_files.append((path, caption))
                except Exception as e:
                    log.error("Document download error: %s", e)
                    api.send_message(chat_id, f"\u274c File error: {e}", parse_mode=None)
                continue

            # text is in "text" for regular messages, "caption" for media
            text = msg.get("text", "") or msg.get("caption", "")
            if not text:
                continue

            log.info("Received: %s", text[:100])

            # Handle commands
            if text.startswith("/"):
                try:
                    handle_command(api, chat_id, config, text)
                except Exception as e:
                    log.error("Command error: %s", e)
                continue

            # Regular message: inject into tmux
            try:
                send_keys(session, text)
                time.sleep(0.5)  # Brief pause for tmux to update
                save_snapshot(session)
                log.debug("Injected into tmux: %s", text[:100])
                api.send_message(chat_id, "\U0001f4e8 Sent to Claude")
            except Exception as e:
                log.error("Send keys failed: %s", e)
                api.send_message(chat_id, f"\u274c Could not send: {e}")

        # Send all collected files as a single message to Claude
        if pending_files:
            try:
                send_files_to_claude(api, chat_id, session, pending_files)
            except Exception as e:
                log.error("File send error: %s", e)
                api.send_message(chat_id, f"\u274c File send error: {e}", parse_mode=None)

    # Announce offline
    try:
        api.send_message(chat_id, "\U0001f534 couchclaude offline")
    except Exception:
        pass

    log.info("couchclaude polling stopped")


if __name__ == "__main__":
    main(daemon="--daemon" in sys.argv)
