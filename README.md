# Customizable AI Discord Chatbot

A framework for building your own AI Discord chatbot. The bot's personality, response style, and behavior are driven by editable prompts, so you can make it any character you like. It chats in your server using OpenAI, moderates, tracks users and currency, and comes with a Tk desktop control panel for configuration.

The repository ships with an example persona, **MaddiePly** (a sarcastic anime catgirl secretary for the fictional megacorp "ModdCorp"), as the default prompt set. Replace it with your own.

## Making your own bot

1. Run `python gui.py` and open the **Prompts** tab.
2. Rewrite the **Personality Prompt** to define who your bot is.
3. Adjust the task prompts (Message Response, Respond to User, Respond to Reply, Clapback, Policy prompts, etc.) to match your character's voice and output rules.
4. Tune behavior in the **Settings** tab (command prefix, owner, response timing/message thresholds, channel IDs, default model).

Prompts and settings are stored in the local database, so no code changes are needed to change the persona. Defaults live in `REQUIRED_PROMPTS` / `REQUIRED_SETTINGS` in `local_database.py`; edit those if you want different out-of-the-box defaults. Some command names and defaults (e.g. `askmaddie`, `Owner Name`) are still MaddiePly-flavored.

## Features

- **AI chat responses** – queued guild messages are answered in character once configurable time/message thresholds are met (`ResponseTimer` in `ai_logic.py`). Image attachments are captioned and included as context.
- **Slash commands** – `/ping`, `/currency`, `/stats`, `/help`, `/convert_timezone`, `/link_twitch`, `/quotes`, `/settings`, `/probation_status`, `/report`.
- **Prefix commands** – `askmaddie`, `clapback`, `policy`, `quote`, `game`, `reload`, `report` (prefix is configurable, default `!`).
- **Context menus** – Report Message, Put on Probation, Remove from Probation.
- **Moderation** – automod word lists and a probation system (`automod.py`).
- **Storage** – local SQLite (`data/maddieply.db`) for settings, prompts, policies and automod words; Supabase for user data.
- **Integrations** – Google Custom Search (web lookups) and Google Sheets (quotes).
- **GUI control panel** – tabs for Settings, Prompts, Users, Memory and Console.

## Project layout

| File | Purpose |
| --- | --- |
| `gui.py` | Operator entrypoint (Tk control panel) |
| `discordbot.py` | Discord client, commands, message handling |
| `ai_logic.py` | `AssistantManager`, `ResponseTimer`, response pipeline |
| `chatgpt.py` | OpenAI wrapper (chat, tools, memory, image analysis) |
| `local_database.py` | SQLite settings/prompts and defaults |
| `online_database.py` | Supabase access |
| `google_api.py` | Google Sheets / search helpers |
| `automod.py` | Automated moderation |
| `tools.py` | Shared references, paths, debug helpers |
| `launcher.py` | Headless launcher |
| `maddieply.spec` | PyInstaller build spec |

## Setup

1. Install Python 3 and dependencies:
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Create a `.env` file in the project root:
   ```env
   DISCORD_TOKEN=
   CHATGPT_API_KEY=
   SUPABASE_URL=
   SUPABASE_SECRET_KEY=
   SUPABASE_ANON_KEY=
   SUPABASE_DIRECT_POSTGRES_URL=
   # Optional, for web search
   GOOGLE_API_KEY=
   GOOGLE_ENGINE_ID=
   ```
3. For Google Sheets integration, provide a `credentials.json` file.
4. Verify Supabase connectivity (optional):
   ```powershell
   python test_supabase_connection.py
   ```

## Running

```powershell
python gui.py          # control panel (recommended)
python launcher.py     # headless bot
python discordbot.py   # headless bot, directly
```

Default settings and prompts are created automatically on first start and can be edited from the GUI or with `/settings`.

## Building an executable

```powershell
pyinstaller maddieply.spec
```

Output goes to `dist/`. Data is stored in `data/` next to the executable.

## Notes

- Initialize the database pool before importing modules that read prompts/settings (`ai_logic.py` instantiates `ChatGPT` at import).
- Use `tools.debug_print()` for diagnostics; output is mirrored to the GUI Console tab.
