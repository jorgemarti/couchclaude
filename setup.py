"""Interactive setup for couchclaude."""

import json
import os
import sys
import time

from config import CONFIG_PATH, DEFAULTS, save_config
from telegram_api import TelegramAPI


def prompt(msg, default=None):
    if default:
        val = input(f"{msg} [{default}]: ").strip()
        return val if val else default
    while True:
        val = input(f"{msg}: ").strip()
        if val:
            return val
        print("  (required)")


def setup():
    print("=== couchclaude setup ===\n")

    # 1. Bot token
    env_token = os.environ.get("CCTR_BOT_TOKEN", "")
    if env_token:
        print(f"Found CCTR_BOT_TOKEN in environment.")
        token = prompt("Telegram bot token", env_token)
    else:
        print("Create a bot via @BotFather on Telegram and paste the token here.")
        token = prompt("Telegram bot token")

    # 2. Validate token
    print("\nValidating token...")
    api = TelegramAPI(token)
    try:
        bot = api.get_me()
        print(f"  Bot: @{bot.get('username', '???')} ({bot.get('first_name', '')})")
    except Exception as e:
        print(f"  ERROR: Invalid token - {e}")
        sys.exit(1)

    # 3. Detect chat ID
    env_chat = os.environ.get("CCTR_CHAT_ID", "")
    if env_chat:
        chat_id = int(env_chat)
        print(f"\nUsing chat_id from environment: {chat_id}")
    else:
        print(f"\nNow send any message to @{bot.get('username', 'your bot')} on Telegram.")
        input("Press Enter when you've sent the message...")

        chat_id = None
        for attempt in range(5):
            try:
                updates = api.get_updates(timeout=5)
                for u in updates:
                    msg = u.get("message", {})
                    cid = msg.get("chat", {}).get("id")
                    if cid:
                        chat_id = cid
                        user = msg.get("from", {})
                        print(f"  Detected chat_id: {chat_id} (from {user.get('first_name', 'unknown')})")
                        break
                if chat_id:
                    break
            except Exception:
                pass
            print(f"  Waiting for message... (attempt {attempt + 1}/5)")
            time.sleep(2)

        if not chat_id:
            print("  Could not detect chat_id. Please enter it manually.")
            chat_id = int(prompt("Telegram chat_id"))

    # 4. tmux session name
    tmux_session = prompt("tmux session name", DEFAULTS["tmux_session"])

    # 5. Save config
    config = {
        "telegram_bot_token": token,
        "telegram_chat_id": chat_id,
        "tmux_session": tmux_session,
        "max_message_length": DEFAULTS["max_message_length"],
        "poll_interval": DEFAULTS["poll_interval"],
    }
    save_config(config)
    print(f"\nConfig saved to {CONFIG_PATH}")

    # 6. Install Claude Code hooks
    install_hooks = input("\nInstall Claude Code hooks? [Y/n]: ").strip().lower()
    if install_hooks != "n":
        install_claude_hooks()

    # 7. Optionally install systemd service
    install_svc = input("\nInstall systemd user service? [y/N]: ").strip().lower()
    if install_svc == "y":
        install_systemd_service()

    # 8. Test message
    print("\nSending test message...")
    try:
        api.send_message(chat_id, "\U0001f389 couchclaude setup complete! This is a test message.")
        print("  Test message sent successfully!")
    except Exception as e:
        print(f"  Warning: could not send test message: {e}")

    print("\n=== Setup complete! ===")
    print(f"\nTo start the polling daemon:")
    print(f"  couchclaude start")
    print(f"\nOr to run as a systemd service:")
    print(f"  systemctl --user start couchclaude")


def install_claude_hooks():
    """Install couchclaude hooks into Claude Code settings."""
    settings_path = os.path.expanduser("~/.claude/settings.json")

    settings = {}
    if os.path.exists(settings_path):
        with open(settings_path, "r") as f:
            settings = json.load(f)

    couchclaude_dir = os.path.expanduser("~/.couchclaude")

    hooks = settings.setdefault("hooks", {})

    # Stop hook
    stop_hooks = hooks.setdefault("Stop", [])
    stop_entry = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {couchclaude_dir}/notify.py completed",
            "timeout": 10,
        }],
    }
    # Check if already installed
    if not any("couchclaude" in json.dumps(h) for h in stop_hooks):
        stop_hooks.append(stop_entry)
        print("  Added Stop hook")
    else:
        print("  Stop hook already exists")

    # SubagentStop hook
    sub_hooks = hooks.setdefault("SubagentStop", [])
    sub_entry = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {couchclaude_dir}/notify.py waiting",
            "timeout": 10,
        }],
    }
    if not any("couchclaude" in json.dumps(h) for h in sub_hooks):
        sub_hooks.append(sub_entry)
        print("  Added SubagentStop hook")
    else:
        print("  SubagentStop hook already exists")

    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
    print(f"  Hooks written to {settings_path}")


def install_systemd_service():
    """Install the systemd user service."""
    service_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(service_dir, exist_ok=True)

    src = os.path.expanduser("~/.couchclaude/couchclaude.service")
    dst = os.path.join(service_dir, "couchclaude.service")

    if os.path.exists(src):
        import shutil
        shutil.copy2(src, dst)
    else:
        # Generate inline
        home = os.path.expanduser("~")
        content = f"""[Unit]
Description=Claude Code Telegram Remote
After=network.target

[Service]
ExecStart=/usr/bin/python3 {home}/.couchclaude/poll.py
Restart=always
RestartSec=10
Environment=PATH={home}/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
"""
        with open(dst, "w") as f:
            f.write(content)

    print(f"  Service installed to {dst}")
    print(f"  Run: systemctl --user daemon-reload")
    print(f"  Run: systemctl --user enable --now couchclaude")


if __name__ == "__main__":
    setup()
