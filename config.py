"""Shared configuration loader for couchclaude."""

import json
import logging
import os
import sys

CONFIG_PATH = os.path.expanduser("~/.couchclaude/config.json")
LOG_PATH = os.path.expanduser("~/.couchclaude/couchclaude.log")

DEFAULTS = {
    "max_message_length": 4000,
    "poll_interval": 2,
    "tmux_session": "claude",
    "log_level": "WARNING",
}


def load_config():
    """Load config from config.json, with env var overrides."""
    config = dict(DEFAULTS)

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config.update(json.load(f))

    # Env var overrides
    if os.environ.get("COUCHCLAUDE_BOT_TOKEN"):
        config["telegram_bot_token"] = os.environ["COUCHCLAUDE_BOT_TOKEN"]
    if os.environ.get("COUCHCLAUDE_CHAT_ID"):
        config["telegram_chat_id"] = int(os.environ["COUCHCLAUDE_CHAT_ID"])
    if os.environ.get("COUCHCLAUDE_TMUX_SESSION"):
        config["tmux_session"] = os.environ["COUCHCLAUDE_TMUX_SESSION"]
    if os.environ.get("COUCHCLAUDE_LOG_LEVEL"):
        config["log_level"] = os.environ["COUCHCLAUDE_LOG_LEVEL"]

    return config


def save_config(config):
    """Save config to config.json with restrictive permissions."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def validate_config(config):
    """Check that required fields are present. Returns list of missing fields."""
    required = ["telegram_bot_token", "telegram_chat_id"]
    return [f for f in required if f not in config or not config[f]]


def setup_logging(config, daemon=False):
    """Configure logging based on config and mode.

    - Foreground (daemon=False): logs to stdout
    - Daemon (daemon=True): logs to couchclaude.log file
    - Level controlled by config["log_level"] or COUCHCLAUDE_LOG_LEVEL env var
    """
    level_name = config.get("log_level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("couchclaude")
    logger.setLevel(level)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if daemon:
        # Check if running under systemd (has journal socket)
        if os.environ.get("INVOCATION_ID") or os.environ.get("JOURNAL_STREAM"):
            # Under systemd — log to stdout, journalctl captures it
            handler = logging.StreamHandler(sys.stdout)
        else:
            # Manual daemon — log to file
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            handler = logging.FileHandler(LOG_PATH)
    else:
        handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger
