# Telegram Bot Setup

## 1. Create a Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a **name** (display name, e.g. "Couch Claude")
4. Choose a **username** (must end in `bot`, e.g. `couchclaude_bot`)
5. BotFather replies with your **bot token** — a string like `123456789:ABCdefGhIjKlMnOpQrStUvWxYz`. Save it.

## 2. Get Your Chat ID

You need your personal chat ID so the bot only responds to you.

1. Open a chat with your new bot in Telegram and send any message (e.g. "hello")
2. Open this URL in your browser, replacing `<TOKEN>` with your bot token:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Look for `"chat":{"id":123456789,...}` in the JSON response — that number is your **chat ID**

Alternatively, `couchclaude setup` detects the chat ID automatically — it asks you to send a message and picks it up via the API.

## 3. Configure couchclaude

Run the interactive setup:

```bash
couchclaude setup
```

Or create `~/.couchclaude/config.json` manually:

```json
{
  "telegram_bot_token": "123456789:ABCdefGhIjKlMnOpQrStUvWxYz",
  "telegram_chat_id": 123456789,
  "tmux_session": "claude"
}
```
