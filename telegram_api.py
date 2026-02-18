"""Telegram Bot API wrapper using requests only."""

import os

import requests

API_BASE = "https://api.telegram.org/bot{token}"
FILE_BASE = "https://api.telegram.org/file/bot{token}"


class TelegramAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = API_BASE.format(token=token)
        self.file_url = FILE_BASE.format(token=token)
        self.session = requests.Session()

    def get_me(self):
        """Validate the bot token. Returns bot info dict or raises."""
        resp = self.session.get(f"{self.base_url}/getMe", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]

    def send_message(self, chat_id, text, parse_mode="HTML"):
        """Send a text message. Returns the sent message dict."""
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = self.session.post(
            f"{self.base_url}/sendMessage",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]

    def send_message_with_buttons(self, chat_id, text, buttons, parse_mode="Markdown"):
        """Send a message with inline keyboard buttons.

        buttons: list of dicts with 'text' and 'callback_data' keys.
        """
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [[btn] for btn in buttons],
            },
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = self.session.post(
            f"{self.base_url}/sendMessage",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]

    def answer_callback_query(self, callback_query_id, text=None):
        """Acknowledge an inline button press."""
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        resp = self.session.post(
            f"{self.base_url}/answerCallbackQuery",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()

    def get_file(self, file_id):
        """Get file path from Telegram servers. Returns file_path string."""
        resp = self.session.get(
            f"{self.base_url}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]["file_path"]

    def download_file(self, file_path, dest_path):
        """Download a file from Telegram to a local path."""
        resp = self.session.get(
            f"{self.file_url}/{file_path}",
            timeout=30,
        )
        resp.raise_for_status()
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(resp.content)
        return dest_path

    def get_updates(self, offset=None, timeout=30):
        """Long-poll for updates. Returns list of update dicts."""
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        resp = self.session.get(
            f"{self.base_url}/getUpdates",
            params=params,
            timeout=timeout + 10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]
