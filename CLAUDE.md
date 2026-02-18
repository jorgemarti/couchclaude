# Claude Code Telegram Remote (couchclaude)

## Project Overview

A lightweight Python tool to remotely monitor and interact with Claude Code sessions via Telegram. No webhooks, no external exposure — just simple Telegram Bot API polling running on the local machine.

**Target environment:** Ubuntu WSL2 on Windows

## Problem Statement

When running long Claude Code sessions, the user may step away (errands, different room, out of tokens waiting for cooldown). Claude Code may ask questions or finish tasks while the user is away. The user wants to see Claude Code output and respond from their phone via Telegram — without exposing any services to the internet.

## Architecture

```
┌──────────────┐     Claude Code Hooks      ┌──────────────┐
│  Claude Code │ ──── (Stop/SubagentStop) ──▶│    couchclaude       │
│  (in tmux)   │                             │              │
│              │◀──── tmux send-keys ────────│  - notify.py │ ◀──┐
└──────────────┘                             │  - poll.py   │    │
                                             └──────────────┘    │
                                                    │            │
                                          Telegram Bot API       │
                                            (polling only)       │
                                                    │            │
                                                    ▼            │
                                             ┌────────────┐      │
                                             │  Telegram   │ ────┘
                                             │  (phone)    │  user replies
                                             └────────────┘
```

### Components

1. **`notify.py`** — Called by Claude Code hooks. Reads the last Claude output from the transcript and sends it to Telegram.
2. **`poll.py`** — Long-running daemon that polls Telegram for messages and injects them into the tmux session running Claude Code.
3. **`config.py`** — Shared configuration (bot token, chat ID, tmux session name).

## Detailed Specification

### 1. Configuration (`config.py`)

- Load from `~/.couchclaude/config.json`
- Required fields:
  ```json
  {
    "telegram_bot_token": "YOUR_BOT_TOKEN",
    "telegram_chat_id": 4556610,
    "tmux_session": "claude",
    "max_message_length": 4000,
    "poll_interval": 2
  }
  ```
- Also support env vars as override: `COUCHCLAUDE_BOT_TOKEN`, `COUCHCLAUDE_CHAT_ID`, `COUCHCLAUDE_TMUX_SESSION`
- Provide a `couchclaude setup` command that interactively creates the config

### 2. Notification Script (`notify.py`)

**Triggered by:** Claude Code hooks (configured in `~/.claude/settings.json`)

**What it does:**
1. Receives a single argument: the hook type (`completed`, `waiting`, `error`)
2. Reads the Claude Code transcript to extract the last assistant turn's **text only** (skips tool_use blocks)
   - Transcripts are UUID-named JSONL files under `~/.claude/projects/`
   - Each entry wraps the message inside a `message` key: `entry["message"]["role"]`, `entry["message"]["content"]`
   - Excludes subagent files (`/subagents/` path)
3. Truncates the message to `max_message_length` (Telegram limit is 4096 chars)
4. Sends to Telegram via Bot API `sendMessage`:
   - Prefix with an emoji indicator: ✅ completed, ❓ waiting for input, ❌ error
   - Use `parse_mode=Markdown` with automatic fallback to plain text if Markdown parsing fails
5. **Wrapped in try/except** — always exits with code 0 so Claude Code hooks never fail, even when couchclaude is not configured
6. Exits immediately (hooks have a timeout)

**Claude Code hooks configuration** (to be added to `~/.claude/settings.json`):
```json
{
  "hooks": {
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 ~/.couchclaude/notify.py completed",
        "timeout": 10
      }]
    }],
    "SubagentStop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 ~/.couchclaude/notify.py waiting",
        "timeout": 10
      }]
    }]
  }
}
```

### 3. Polling Daemon (`poll.py`)

**Runs as:** systemd user service or manually in a separate tmux pane

**What it does:**
1. On startup, sends a "🟢 couchclaude online" message to Telegram
2. Polls `getUpdates` with long polling (`timeout=30`) in a loop
3. Filters messages: only process messages from the configured `chat_id`
4. When a message arrives:
   - If it starts with `/` handle as a command (see commands below)
   - Otherwise, inject the text into the tmux session as keystrokes:
     ```bash
     tmux send-keys -t {session} "message text" Enter
     ```
   - Send a confirmation back: "📨 Sent to Claude"
5. Track the `update_id` offset to avoid processing old messages
6. On clean shutdown (SIGTERM/SIGINT), send "🔴 couchclaude offline"
7. **Prompt detection** — every 5 seconds, captures the tmux screen and checks if Claude Code's selection UI is visible (`Enter to select · ↑/↓ to navigate · Esc to cancel`). If detected, parses the question and numbered options, strips ANSI codes and terminal chrome, and forwards a clean message to Telegram. Hashes the parsed content to avoid duplicate sends.

**Telegram commands:**
- `/status` — Reply with: tmux session alive? Claude process running? Current working directory
- `/screen` or `/view` — Capture current tmux pane content and send it (last 50 lines)
- `/cd <path>` — Change the working directory context (send `cd <path>` + Enter to tmux)
- `/cmd <shell command>` — Run a shell command in tmux (e.g., `/cmd git status`)
- `/help` — Show available commands
- `/ping` — Reply with "pong" + uptime

### 4. Setup Script (`setup.py`)

Interactive setup:
1. Ask for Telegram bot token (or detect from env)
2. Validate the token via `getMe`
3. Ask user to send a message to the bot, then auto-detect the chat ID via `getUpdates`
4. Ask for tmux session name (default: `claude`)
5. Write `~/.couchclaude/config.json`
6. Install Claude Code hooks into `~/.claude/settings.json` (merge, don't overwrite)
7. Optionally install systemd user service for `poll.py`
8. Print summary and test by sending a test message to Telegram

### 5. Entry Point (`couchclaude`)

A single CLI entry point at `~/.couchclaude/couchclaude` (or installed to `~/bin/couchclaude`):

```
Usage: couchclaude <command>

Commands:
  setup       Interactive first-time setup
  start       Start the polling daemon (foreground)
  daemon      Start as background process (systemd)
  stop        Stop the daemon
  status      Show daemon and session status  
  notify      Send notification (used by hooks)
  test        Send a test message to Telegram
  view        Capture and display current tmux screen
```

## File Structure

```
~/.couchclaude/
├── config.json          # Configuration
├── couchclaude                 # Main CLI entry point
├── notify.py            # Hook notification script  
├── poll.py              # Telegram polling daemon
├── config.py            # Shared config loader
├── telegram_api.py      # Telegram Bot API wrapper (requests only)
├── tmux_utils.py        # tmux interaction helpers
├── setup.py             # Interactive setup
└── couchclaude.service         # systemd user service template
```

## Dependencies

**Python standard library only + `requests`**. No frameworks, no async libraries.

```
pip install requests
```

That's the only external dependency. Everything else uses stdlib.

## Key Design Decisions

1. **Polling, not webhooks** — No need to expose any port. The bot polls Telegram's API outbound. Works behind NAT, firewalls, corporate networks.

2. **tmux as the integration layer** — Claude Code runs inside tmux. We read output via `tmux capture-pane` and inject input via `tmux send-keys -l` (literal flag to avoid key name interpretation, with Enter sent separately). This is dead simple.

3. **Claude Code hooks for push notifications** — Instead of continuously watching the terminal, we use Claude Code's built-in hook system to trigger notifications only when something happens. This is efficient and reliable.

4. **No state beyond config** — No database, no session files, no token management. The tmux session IS the state.

5. **Single dependency (requests)** — No aiohttp, no python-telegram-bot library, no framework. Just HTTP calls with `requests`. Easy to debug, easy to understand.

6. **Graceful message truncation** — Telegram has a 4096 char limit. Smart truncation: keep the first and last parts of long messages with "... [truncated] ..." in the middle.

## Security Considerations

- Bot token stored in `~/.couchclaude/config.json` with `0600` permissions
- Only messages from the configured `chat_id` are processed
- No inbound network connections required
- tmux `send-keys` injects text as-is — be aware this is equivalent to typing at the terminal
- The `/cmd` command runs arbitrary shell commands through tmux — this is intentional but should only be accessible to the authorized Telegram user

## Workflow Example

```
# First time setup
cd ~/.couchclaude
python3 setup.py

# Start Claude Code in tmux
tmux new-session -s claude
claude

# In another terminal (or let systemd handle it)
couchclaude start

# Now on your phone:
# 1. Claude finishes a task → you get a Telegram notification with the output
# 2. You reply "yes, looks good. now add tests" → injected into Claude session  
# 3. You send /view → see the current terminal screen
# 4. You send /status → see if Claude is still running
```

## systemd User Service

```ini
[Unit]
Description=Claude Code Telegram Remote
After=network.target

[Service]
ExecStart=/usr/bin/python3 %h/.couchclaude/poll.py
Restart=always
RestartSec=10
Environment=PATH=%h/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

Install with:
```bash
mkdir -p ~/.config/systemd/user
cp ~/.couchclaude/couchclaude.service ~/.config/systemd/user/
systemctl --user enable couchclaude
systemctl --user start couchclaude
```

## Testing Checklist

- [ ] `couchclaude setup` creates valid config and hooks
- [ ] `couchclaude test` sends a message to Telegram
- [ ] Claude Code hook fires `notify.py` on task completion
- [ ] Notification appears on Telegram with last Claude output
- [ ] Reply from Telegram gets injected into tmux
- [ ] `/view` returns current terminal content
- [ ] `/status` shows session info
- [ ] `/cmd git status` executes correctly
- [ ] Long messages are properly truncated
- [ ] Only authorized chat_id messages are processed
- [ ] Daemon survives Claude Code restarts
- [ ] Daemon handles network interruptions gracefully
