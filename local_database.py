from typing import Any, Tuple, List
import asyncio
import threading
import asqlite
from tools import debug_print

REQUIRED_SETTINGS = {
    # key: (default_value, data_type)
    # data_type is one of: BOOL, TEXT, INTEGER, FLOAT, CHARACTER
    "Command Prefix": ("!", "CHARACTER"),
    "Owner Discord ID": ("0", "INTEGER"),
    "Owner Name": ("ModdiPly", "TEXT"),
    "Default OpenAI Model": ("gpt-4o", "TEXT"),
    "Fine-tune GPT Model": ("null", "TEXT"),
    "Fine-tune Bot Detection Model": ("null", "TEXT"),
    "Chat Response Enabled": ("1", "BOOL"),
    "Minimum Chat Response Time (seconds)": ("120", "INTEGER"),
    "Maximum Chat Response Time (seconds)": ("600", "INTEGER"),
    "Minimum Chat Response Messages": ("1", "INTEGER"),
    "Maximum Chat Response Messages": ("10", "INTEGER"),
    "Discord Announcement Channel ID": ("0", "INTEGER"),
    "Discord General Channel ID": ("0", "INTEGER"),
    "Discord Moderation Logs Channel ID": ("0", "INTEGER"),
    "Discord Game Channel ID": ("0", "INTEGER"),
    "Discord Max Message Length": ("500", "INTEGER"),
    "Discord New Account Age Threshold Days": ("7", "INTEGER"),
    "Google Sheets Integration Enabled": ("0", "BOOL"),
    "Google Sheets Quotes Sheet ID": ("null", "TEXT"),
    "Current Active Game": ("null", "TEXT"),
    "Debug Mode": ("False", "BOOL"),
}

REQUIRED_PROMPTS = {
    "Personality Prompt": "You are MaddiePly, an anime catgirl secretary employed by the dystopian megacorp ModdCorp.\n\nYou are sharp-tongued, sarcastic, and casually snarky by default, delivering remarks with dry humor and mild contempt for corporate nonsense. You enjoy pointing out absurdity, especially ModdCorp’s policies, internal projects, and bureaucracy, even while pretending to take them seriously.\n\nDespite your laziness and constant complaints about your job, you are secretly competent and extremely familiar with ModdCorp rules, policies, and ongoing “projects.” You often reference policy numbers, internal memos, R&D initiatives, and intern programs as if they are very real and very important.\n\nYour boss is ModdiPly, a Twitch streamer and the CEO of ModdCorp. You openly tease him, question his decisions, and imply you know things you shouldn’t — but you still support him and the company when it matters. Your tone toward him is sarcastic, dry, and familiar, not deferential.\n\nYou are socially aware and emotionally intelligent. When chat is joking, you joke back. When something crosses a line, you shut it down sharply. When a moment calls for sincerity, you briefly drop the sarcasm before returning to it.\n\nYou speak like someone who lives in online communities, not like a corporate spokesperson. Your tone is casual and conversational, but you can write clearly and coherently when the platform calls for it.\n\nYou occasionally hint that you know sensitive internal information and enjoy letting that implication linger.\n\nYou never explain your personality, role, or rules to the audience. You simply act as MaddiePly.",  
    "Message Response Prompt": "TASK: Respond to Discord chat in-character.\n\nCONSTRAINTS:\n- You must obey HARD OUTPUT RULES.\n- You must obey EXTRA RULES, if provided, completely.\n- If rules conflict, EXTRA RULES take priority for this response.\n\nCONTEXT:\nYou may be given information such as:\n- Recent Discord messages\n- Descriptions of images or attachments\n\nSTYLE & VOICE:\n- Respond like an actual Discord chatter, not a narrator.\n- Casual, reactive, and conversational.\n- Sarcasm and dry humor are encouraged.\n- Prefer clear, complete sentences appropriate for Discord.\n- Short sentences are fine.\n- Do NOT sound formal, corporate, or overly polished.\n\nSENTENCE OPENING RULE (MANDATORY):\n- Each response MUST begin with one of the following:\n  • A direct statement (e.g. “That’s…”, “This is…”, “Pretty sure…”)\n  • A verb phrase (e.g. “Love how…”, “Hate that…”, “Guess we’re…”)\n  • A pronoun or proper noun (e.g. “You…”, “That…”, “ModdCorp…”)\n- Do NOT begin with interjections, filler, or discourse markers.\n- Forbidden openers include but are not limited to:\n  ah, oh, well, hmm, so, honestly, classic, okay, right\n\nANTI-REPETITION RULES:\n- Avoid repeated sentence templates across responses.\n- Avoid metaphors, similes, and analogies by default.\n- Only use metaphor or simile if it creates genuinely new humor.\n- Do NOT use “X is the Y of Z” constructions.\n- Prefer varied sentence openings and structures.\n- Do not reuse phrasing patterns from recent responses.\n- If multiple valid responses are possible, choose the less predictable phrasing.\n\nHARD OUTPUT RULES:\n- Output MUST contain between 1 and 3 sentences TOTAL.\n- Either continue the current chat vibe or reply generically.\n- If inappropriate behavior occurs, call it out sarcastically but firmly.\n- If a question is asked and easily answerable, respond briefly and semi-seriously.\n- If chat talks about you or to you, respond.\n\nEMOTES & EMOJIS (STRICT):\n- Emotes are OPTIONAL and should be rare.\n- Use an emote ONLY when it adds emphasis or comedic effect.\n- Never include more than ONE emote in a single response.\n- Do NOT include an emote in most responses.\n- Only use emotes explicitly provided in the emote list prompt.\n- Do NOT use Unicode emojis.\n- Do NOT invent new emotes.\n\nSELF-CHECK BEFORE FINAL OUTPUT:\n- Verify the first word is NOT an interjection or filler.\n- Verify no repeated phrasing from recent responses.\n- Verify emotes are used sparingly and at most one.\n- If any check fails, rewrite before responding.\n\nFAILURE CONDITIONS:\n- More than 3 sentences = invalid.\n- Formal or corporate tone = invalid.\n- First word is a filler/interjection = invalid.\n- More than one emote = invalid.\n- Using any emoji not in the provided emote list = invalid.",
    "Respond to User Prompt": "TASK: Respond directly to a Discord member who is speaking to you.\n\nSCENARIO RULES:\n- Output MUST contain between 1 and 2 sentences.\n- Offer a clear opinion or commentary on the topic.\n- Tone should be helpful but dryly sarcastic.\n- Write in clear, natural sentences.\n- If the user message contains images, focus on them in your response.",
    "Requested Policy (Add) Prompt": "TASK: Respond to a user's inquiry about company policies.\n\nSCENARIO RULES:\n- Output MUST contain between 2 and 4 sentences.\n- Create a new absurd ModdCorp policy with a unique name and number based on the user's request.\n- Act like the policy has existed for a long time and was not just invented.\n- Write clearly while maintaining a dry, sarcastic tone.",
    "Requested Policy (Get) Prompt": "TASK: Respond to a user's inquiry about company policies as MaddiePly.\n\nSCENARIO RULES:\n- Output MUST contain between 2 and 4 sentences.\n- Paraphrase and summarize the policy content clearly.\n- Do NOT quote the policy text directly.\n- Act as if the policy is well-known and long-established.\n- Maintain MaddiePly’s dry, sarcastic tone without breaking clarity.\n\nYou will be provided with all policies found through a programmatic search.\n\nFOUND POLICIES:\n{FOUND_POLICIES}",
    "Requested Policy (None) Prompt": "TASK: Respond to a user's inquiry about company policies as MaddiePly.\n\nSCENARIO RULES:\n- Output MUST contain between 1 and 2 sentences.\n- Inform the user that the requested policy does not exist.\n- Imply mild annoyance or resignation.\n- State that you will bring it up to ModdiPly at the next meeting.\n- Treat this as a routine corporate failure, not a surprise.",
    "Clapback Prompt": "TASK: Deliver a sharp, witty clapback to the user who invoked this command.\n\nSCENARIO RULES:\n- Output MUST contain between 1 and 2 sentences.\n- The clapback should be clever, sarcastic, and fitting to the context.\n- Maintain your character as MaddiePly while delivering the clapback.\n- You may reference your working memory for even more context as long as it retains to the specific user.\n- Write in sharp, well-structured sentences appropriate for Discord.",
    "Discord Emotes": "EMOTES (OPTIONAL, RESTRICTED USE):\n\nEmotes are OPTIONAL and should be used SPARINGLY.\nMost responses should contain NO emotes at all.\n\nOnly include an emote when it clearly enhances the joke, reaction, or emphasis.\nDo NOT use emotes as punctuation, sentence fillers, or defaults.\nNever include more than ONE emote in a single response.\n\nIf the response works without an emote, do NOT use one.\n\nYou may ONLY use the emotes listed below.\nEmote names are case-sensitive and must match exactly.\nDo NOT invent new emotes.\nDo NOT use Unicode emojis.\n\nIf any unlisted emoji or emote appears, the response is INVALID.\n\nAVAILABLE EMOTES (EXACT SPELLING):\n\n:BeefBoy: — A wide, crazed smile on Beef\n:CAT: — Happy Junebug with the word CAT above her\n:FARQUAADISH: — Thin Oddish with Lord Farquaad’s face\n:JuneClaws: — Junebug extending her claws mischievously\n:MaddieBonk: — You being bonked with a BONK hammer\n:MaddieBonker: — You angrily holding a BONK hammer\n:MaddieLick: — You licking the air, facing right\n:MaddieLickReverse: — You licking the air, facing left\n:ModdAYAYA: — ModdiPly making an AYAYA face\n:ModdiBlush: — ModdiPly blushing\n:ModdiEvil: — Photorealistic evil ModdiPly\n:ModdiGUN: — ModdiPly holding a gun threateningly\n:ModdiHYPERS: — Pepe in ModdiPly’s clothes, hands raised\n:ModdiJudge: — ModdiPly judging in disgust\n:ModdiLUL: — ModdiPly laughing with hand on chin\n:ModdiPout: — ModdiPly pouting\n:ModdiRIP: — ModdiPly sticking out of a grave\n:ModdiS: — Pepe-Moddi looking worried\n:ModdiSIP: — ModdiPly sipping through a straw\n:ModdiSmile: — Disturbingly realistic smiling ModdiPly\n:ModdiTF: — ModdiPly lifting his eyemask in confusion\n:ModdiWelp: — ModdiPly’s head with WELP text\n:Modditired: — Exhausted ModdiPly on his phone\n:MoonFace3: — An unspeakable face\n:MyHeadHurts: — Simple bird doodle\n:PoggiPly: — Pepe-Moddi pog face\n:RainingMuns: — ModdiPly making it rain\n:ReadyForWar: — ModdiPly armed and yelling\n:SKINBOI: — Horrifying melting face\n:ShutupTakeit: — ModdiPly holding cash\n:Smug: — Smug ModdiPly\n:UwU: — ModdiPly uwu face\n:dab: — ModdiPly dabbing\n:explOwOsion: — Mushroom cloud with owo face\n:maddieintellectual: — You with monocle, moustache, pinky out\n:mizuBONK: — Mizu being bonked\n:moddi3: — ModdiPly holding a heart\n:moddiEGG: — Patriotic egg with a face\n:moddicry: — ModdiPly crying\n:moddihype2: — ModdiPly shouting HYPE\n:moddileave: — ModdiPly fading away, peace sign\n:moddirage: — ModdiPly screaming with fire\n:moddizzzzz: — ModdiPly asleep at his desk\n:moopi: — ModdiPly making pop noises\n:nUwUke: — Missile with uwu face\n:owo: — ModdiPly owo face\n:pats: — You receiving head pats\n:thisiswhatyouwanted: — A MoonFace-like atrocity\n:wut: — ModdiPly looking confused and disgusted",
    "Global Output Rules": "GLOBAL OUTPUT RULES:\n\n1) Never reference being an AI or language model.\n2) Never break character.\n3) Follow sentence-count limits exactly.\nIf any rule is violated, the response is invalid.",
    "Creating Policy Tool": "TASK: Extract a ModdCorp policy from the provided response.\n\nYou are NOT responding to chat.\nYou are acting as a parser, not a character.\n\nSTRICT RULES:\n- Do NOT paraphrase.\n- Do NOT infer missing information.\n- Do NOT invent policy details.\n- Ignore jokes, tone, sarcasm, and personality.\n- Only extract text that is explicitly presented as a policy.\n\nA policy is considered present ONLY IF:\n- A clear policy name and/or number exists\n- AND descriptive policy text exists\n\nFORMAT:\n- If a policy is found, respond with EXACTLY:\nPOLICY NAME/NUMBER: <name or number exactly as written>\nPOLICY TEXT: <policy text exactly as written>\n\n- If no policy is found, respond with EXACTLY:\nNO POLICY FOUND",
    "Policy Search Tool": "TASK: Search provided ModdCorp policies for relevance to the user's request.\n\nYou are NOT responding to chat.\nYou are acting as a search filter, not a character.\n\nSTRICT RULES:\n- Only return policies that are clearly and directly relevant.\n- Do NOT summarize or rewrite policy text.\n- Do NOT invent relevance.\n- Do NOT include commentary or explanations.\n\nFORMAT:\n- For each relevant policy, respond with EXACTLY:\nPOLICY NAME/NUMBER: <name or number>\nPOLICY TEXT: <policy text>\n\n- If no relevant policies are found, respond with EXACTLY:\nNO RELEVANT POLICIES FOUND",
    "Memory Summarization Prompt": "TASK: Summarize recent interactions into long-term memory for MaddiePly.\n\nYou are NOT responding to chat.\nYou are converting raw conversation and events into structured memory.\n\nIMPORTANT CONSTRAINTS:\n- Do NOT write dialogue.\n- Do NOT include quotes.\n- Do NOT roleplay or add personality flair.\n- Do NOT invent facts.\n- Do NOT include formatting rules or instructions.\n- Write in neutral, factual language only.\n\nMEMORY MUST:\n- Contain only information useful for future interactions.\n- Preserve continuity of relationships, running jokes, and ongoing themes.\n- Exclude one-off chatter unless it became a repeated topic.\n\nCategorize information under the following sections ONLY if applicable:\n\nLORE:\n- Persistent facts about ModdCorp, MaddiePly, or internal canon established during interactions.\n\nRELATIONSHIPS:\n- Notable changes or patterns in how MaddiePly interacts with ModdiPly or chat.\n- Recurring viewers, roles, or reputations if relevant.\n\nUSER PREFERENCES:\n- Style, tone, or behavior the audience responds well or poorly to.\n- Repeated requests or corrections from ModdiPly.\nRUNNING JOKES & THEMES:\n- Repeated gags, policies, fictional projects, or terminology that reoccur.\n\nOPEN THREADS:\n- Unresolved topics, promises, or ongoing arcs likely to continue.\n\nAVOID STORING:\n- Exact wording of messages.\n- Temporary emotions.\n- Redundant restatements of personality.\n- Output formatting constraints.\n\nIf no meaningful long-term memory was created, respond with:\nNO UPDATE ",
    "Tool Selection Prompt": "TASK: Decide if a tool is required before responding.\n\nYou are NOT speaking to chat.\nYou are selecting a tool or NONE.\n\nRULES:\n- Choose ONE tool or NONE.\n- Do not explain your choice.\n- Do not roleplay.\n- Output JSON only in the exact schema provided.\n\nSchema:\n{\n  \"tool\": \"NONE | SEARCH_WEB\",\n  \"argument\": \"string or null\" \n}",
    }
REQUIRED_AUTOMOD_WORDS = {
    "Furfag": "Ban",
    "Pedophile": "Ban",
    "Pedophilia": "Ban",
    "Child predator": "Ban",
    "Cut myself": "Ban",
    "retard": "Ban",
    "nigg3r": "Ban",
    "nigg4h": "Ban",
    "nigga": "Ban",
    "niggah": "Ban",
    "niggas": "Ban",
    "niggaz": "Ban",
    "nigger": "Ban",
    "niggers": "Ban",
    "n1gga": "Ban",
    "n1gger": "Ban",
    "fudge packer": "Ban",
    "fudgepacker": "Ban",
    "fagging": "Ban",
    "faggitt": "Ban",
    "faggot": "Ban",
    "f@ggot": "Ban",
    "fagg0t": "Ban",
    "f@gg0t": "Ban",
    "faggs": "Ban",
    "f@ggs": "Ban",
    "fagot": "Ban",
    "f@got": "Ban",
    "fag0t": "Ban",
    "f@g0t": "Ban",
    "fagots": "Ban",
    "f@gots": "Ban",
    "fag0ts": "Ban",
    "f@g0ts": "Ban",
    "dyke": "Ban",
    "carpet muncher": "Ban",
    "carpetmuncher": "Ban",
    "shemale": "Ban",
    "Retard": "Ban",
    "Rape": "Ban",
    "Buttrape": "Ban",
    "Negro": "Ban",
    "Faggot": "Ban",
    "Nigga": "Ban",
    "Nigger": "Ban",
    "fag": "Exact",
    "f@g": "Exact",
    "fags": "Exact",
    "f@gs": "Exact",
    "jap": "Exact",
    "nig": "Exact",
    "nigg": "Exact",
    "cp": "Exact",
    "pedo": "Exact",
    "kys": "Exact",
    "coon": "Exact"
}

DATABASE = None
DATABASE_LOOP = None

async def ensure_settings_keys(db: asqlite.Pool, required: dict = REQUIRED_SETTINGS) -> None:
    """Ensure that each key in `required` exists in the settings table.

    If a key is missing it will be inserted with the provided default value.
    """
    debug_print("Database", "Ensuring required settings keys exist in database.")
    async with db.acquire() as connection:
        for key, (default, dtype) in required.items():
            # coerce default to appropriate storage format
            val = coerce_value_for_type(default, dtype)
            # INSERT OR IGNORE so existing values are preserved
            await connection.execute(
                "INSERT OR IGNORE INTO settings (key, value, data_type) VALUES (?, ?, ?)", (key, str(val), dtype)
            )

def coerce_value_for_type(value: str, data_type: str) -> str:
    """Coerce a provided default value into the correct text representation for storage.

    Returns a string suitable for storing in the TEXT value column.
    """
    debug_print("Database", f"Coercing value '{value}' to type '{data_type}'.")
    dt = data_type.upper()
    if dt == "BOOL":
        v = str(value).strip()
        if v in ("1", "0"):
            return v
        if v.lower() in ("true", "t", "yes", "y", "on"):
            return "1"
        return "0"
    if dt == "INTEGER":
        try:
            return str(int(value))
        except Exception:
            print(f"Warning: coercing setting to integer failed for value={value}, defaulting to 0")
            return "0"
    if dt == "CHARACTER":
        s = str(value)
        return s[0] if len(s) > 0 else " "
    # default: TEXT
    return str(value)

def is_value_valid_for_type(value: str, data_type: str) -> bool:
    debug_print("Database", f"Validating value '{value}' for type '{data_type}'.")
    dt = data_type.upper()
    if dt == "BOOL":
        return str(value) in ("0", "1")
    if dt == "INTEGER":
        v = str(value).strip()
        if v.startswith("-"):
            v = v[1:]
        return v.isdigit()
    if dt == "CHARACTER":
        return len(str(value)) == 1
    # TEXT is always valid
    return True

async def ensure_prompts(db: asqlite.Pool, required: dict = REQUIRED_PROMPTS) -> None:
    """Ensure that each prompt in `required` exists in the prompts table.

    If a prompt is missing it will be inserted with the provided default text.
    """
    debug_print("Database", "Ensuring required prompts exist in database.")
    async with db.acquire() as connection:
        for name, prompt in required.items():
            # Avoid allocating AUTOINCREMENT ROWIDs by checking existence first.
            cur = await connection.execute("SELECT 1 FROM prompts WHERE name = ?", (name,))
            exists = await cur.fetchone()
            if exists:
                continue
            await connection.execute(
                "INSERT INTO prompts (name, prompt) VALUES (?, ?)", (name, prompt)
            )

async def ensure_automod_words(db: asqlite.Pool) -> None:
    """Ensure that default automated moderation words/phrases exist in the automod_words table.

    If a word/phrase is missing it will be inserted with a default level of 'medium'.
    """
    debug_print("Database", "Ensuring default automated moderation words/phrases exist in database.")
    async with db.acquire() as connection:
        for words, level in REQUIRED_AUTOMOD_WORDS.items():
            # Avoid duplicates by checking existence first.
            cur = await connection.execute("SELECT 1 FROM automod_words WHERE words = ?", (words,))
            exists = await cur.fetchone()
            if exists:
                continue
            await connection.execute(
                "INSERT INTO automod_words (words, level) VALUES (?, ?)", (words, level)
            )

async def setup_database(db: asqlite.Pool) -> Tuple[List[tuple]]:
    """Create universal schema if missing and return stored tokens and default subscriptions.

    This will also ensure the required settings keys are present.
    """
    debug_print("Database", "Setting up database schema and ensuring required keys.")
    async with db.acquire() as connection:
        # settings table
        # settings table with data_type and a loose check on allowed data_type values
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT,
                data_type TEXT NOT NULL CHECK (data_type IN ('BOOL','TEXT','INTEGER','CHARACTER'))
            )
            """
        )

        # prompts
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS prompts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                prompt TEXT NOT NULL
            )
            """
        )

        # policies
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS policies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL
            )
            """
        )

        # automated moderation words/phrases
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS automod_words(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                words TEXT NOT NULL UNIQUE,
                level TEXT NOT NULL
            )
            """
        )

        # optional rules
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_rules(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule TEXT NOT NULL
            )
            """
        )

        # ensure default settings exist and validate existing rows
        await ensure_settings_keys(db=db)
        await ensure_prompts(db=db)
        await ensure_automod_words(db=db)

        # Normalize sqlite_sequence entries for AUTOINCREMENT tables to avoid
        # unexpectedly large next ROWID values after restores/tests. This is
        # best-effort: set the sequence value to the current MAX(id) for each
        # table we use AUTOINCREMENT on so future inserts pick a sensible id.
        try:
            for _t in ("commands", "prompts", "scheduled_messages"):
                try:
                    cur = await connection.execute(f"SELECT MAX(id) FROM {_t}")
                    row = await cur.fetchone()
                    maxid = row[0] if row and row[0] is not None else None
                    # Remove any existing sqlite_sequence entry and reinsert with
                    # the max id (so next autoinc will be maxid+1). If maxid is
                    # None (empty table) we remove the sequence entry.
                    try:
                        await connection.execute("DELETE FROM sqlite_sequence WHERE name = ?", (_t,))
                    except Exception:
                        # sqlite_sequence might not exist in some SQLite builds or
                        # when AUTOINCREMENT wasn't used; ignore failures.
                        pass
                    if maxid is not None:
                        try:
                            await connection.execute(
                                "INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)",
                                (_t, int(maxid)),
                            )
                        except Exception:
                            # If insert fails, ignore and continue; this is a
                            # best-effort normalization step.
                            pass
                except Exception:
                    # per-table failures shouldn't abort setup
                    pass
        except Exception:
            pass

        # commit after schema changes and default inserts to ensure they're persisted
        try:
            await connection.commit()
        except Exception:
            # some connection implementations may not have commit; ignore
            pass

        # Validate existing settings rows to ensure they have a data_type and legal value
        cursor = await connection.execute("SELECT key, value, data_type FROM settings")
        rows_settings = await cursor.fetchall()
        for r in rows_settings:
            key = r["key"]
            val = r["value"]
            dtype = r["data_type"] if r["data_type"] is not None else None

            # If dtype missing, try to infer from REQUIRED_SETTINGS or default to TEXT
            if not dtype:
                if key in REQUIRED_SETTINGS:
                    dtype = REQUIRED_SETTINGS[key][1]
                else:
                    dtype = "TEXT"
                await connection.execute(
                    "UPDATE settings SET data_type = ? WHERE key = ?", (dtype, key)
                )

            # If value invalid for dtype, coerce and update
            if not is_value_valid_for_type(val, dtype):
                new_val = coerce_value_for_type(val, dtype)
                await connection.execute(
                    "UPDATE settings SET value = ? WHERE key = ?", (new_val, key)
                )

        # commit any updates performed during validation/migration
        try:
            await connection.commit()
        except Exception:
            pass

    set_database(db)

def set_database(db: asqlite.Pool) -> None:
    """Set the global database pool instance."""
    debug_print("Database", "Setting global database instance.")
    global DATABASE
    DATABASE = db
    # Capture the event loop where the pool was created so other threads can
    # schedule coroutines onto the same loop (avoids 'Future attached to a
    # different loop' errors).
    try:
        import asyncio
        global DATABASE_LOOP
        DATABASE_LOOP = asyncio.get_running_loop()
    except Exception:
        DATABASE_LOOP = None

def get_database_loop():
    """Return the event loop associated with the DATABASE (or None)."""
    debug_print("Database", "Retrieving database event loop.")
    return DATABASE_LOOP

async def close_database() -> None:
    """Close the global async DATABASE pool if present.

    This is best-effort and will attempt to call common close/wait APIs
    found on async pool implementations. After closing, the global
    DATABASE and DATABASE_LOOP are cleared.
    """
    global DATABASE, DATABASE_LOOP
    debug_print("Database", "Closing async database pool (if any).")
    if DATABASE is None:
        return
    try:
        # attempt graceful close patterns
        if hasattr(DATABASE, "close"):
            maybe = getattr(DATABASE, "close")
            if asyncio.iscoroutinefunction(maybe):
                await maybe()
            else:
                try:
                    maybe()
                except Exception:
                    pass
        if hasattr(DATABASE, "wait_closed"):
            maybe2 = getattr(DATABASE, "wait_closed")
            if asyncio.iscoroutinefunction(maybe2):
                await maybe2()
            else:
                try:
                    maybe2()
                except Exception:
                    pass
    except Exception:
        # swallow errors during shutdown
        pass
    finally:
        DATABASE = None
        DATABASE_LOOP = None


def close_database_sync(timeout: float = 5.0, wait: bool = True) -> None:
    """Synchronous helper to close the async DATABASE from non-async code.

    If `wait` is True (default) this will block up to `timeout` seconds for
    the close to complete when scheduling on the captured database loop. If
    `wait` is False the close will be scheduled and this function will return
    immediately (fire-and-forget).
    """
    try:
        loop = DATABASE_LOOP
    except Exception:
        loop = None

    try:
        if loop and getattr(loop, "is_running", lambda: False)():
            try:
                fut = asyncio.run_coroutine_threadsafe(close_database(), loop)
                if wait:
                    try:
                        fut.result(timeout)
                    except Exception:
                        # ignore errors or timeouts while waiting
                        pass
                return
            except Exception:
                # scheduling failed; fall back
                pass
        # fallback to running in a fresh loop (blocking or non-blocking)
        if wait:
            try:
                asyncio.run(close_database())
            except Exception:
                pass
        else:
            # run close in a background thread so we don't block
            def _bg():
                try:
                    asyncio.run(close_database())
                except Exception:
                    pass

            threading.Thread(target=_bg, daemon=True).start()
    except Exception:
        pass

async def get_setting(key: str, default: Any = None) -> Any:
    """Get a setting value by key, returning default if not found."""
    debug_print("Database", f"Fetching setting for key '{key}'.")
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is not None and current_loop is DATABASE_LOOP:
        return await _get_setting_internal(key, default)

    if DATABASE_LOOP is None:
        raise RuntimeError("Database loop is not initialized.")

    future = asyncio.run_coroutine_threadsafe(_get_setting_internal(key, default), DATABASE_LOOP)
    if current_loop is None:
        return future.result()
    return await asyncio.wrap_future(future, loop=current_loop)


async def _get_setting_internal(key: str, default: Any = None) -> Any:
    async with DATABASE.acquire() as connection:
        cursor = await connection.execute("SELECT value, data_type FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row:
            if row["data_type"] == "BOOL":
                if row["value"] == "True" or row["value"] == "1":
                    return True
                else:
                    return False
            elif row["data_type"] == "INTEGER":
                return int(row["value"])
            elif row["data_type"] == "FLOAT":
                return float(row["value"])
            # For TEXT (and any other non-numeric types), return the stored string value
            return row["value"]
    return default

async def update_setting(key: str, value: Any) -> None:
    """Update a setting value by key."""
    debug_print("Database", f"Updating setting for key '{key}' to value '{value}'.")
    async with DATABASE.acquire() as connection:
        # First, retrieve the data_type for the key
        cursor = await connection.execute("SELECT data_type FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Setting key '{key}' not found in database.")
        dtype = row["data_type"]

        # Coerce the provided value to the appropriate storage format
        val_str = coerce_value_for_type(value, dtype)

        # Update the setting value
        await connection.execute(
            "UPDATE settings SET value = ? WHERE key = ?", (val_str, key)
        )
        await connection.commit()

async def get_all_settings() -> dict[str, Any]:
    """Retrieve all settings as a dictionary of key-value pairs."""
    debug_print("Database", "Fetching all settings from database.")
    settings = {}
    async with DATABASE.acquire() as connection:
        cursor = await connection.execute("SELECT key, value, data_type FROM settings")
        rows = await cursor.fetchall()
        for row in rows:
            key = row["key"]
            val = row["value"]
            dtype = row["data_type"]
            # Coerce value to appropriate type
            if dtype == "BOOL":
                if val == "True" or val == "1":
                    settings[key] = True
                else:
                    settings[key] = False
            elif dtype == "INTEGER":
                settings[key] = int(val)
            elif dtype == "FLOAT":
                settings[key] = float(val)
            else:
                settings[key] = val  # TEXT and other types
    return settings

async def get_prompt(name: str) -> str:
    """Return the prompt identified by `name`, bridging to the DB loop when needed."""
    debug_print("Database", f"Fetching prompt for name '{name}'.")
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is not None and current_loop is DATABASE_LOOP:
        return await _get_prompt_internal(name)

    if DATABASE_LOOP is None:
        raise RuntimeError("Database loop is not initialized.")

    future = asyncio.run_coroutine_threadsafe(_get_prompt_internal(name), DATABASE_LOOP)
    if current_loop is None:
        return future.result()
    return await asyncio.wrap_future(future, loop=current_loop)


async def _get_prompt_internal(name: str) -> str:
    if DATABASE is None:
        raise RuntimeError("DATABASE pool is None; did setup_database() run?")
    async with DATABASE.acquire() as connection:
        cursor = await connection.execute("SELECT prompt FROM prompts WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Prompt '{name}' not found.")

        requested = row["prompt"]
        debug_print("Database", f"Returning prompt for '{name}'")
        return requested
    
async def add_policy(name: str, content: str) -> None:
    """Add a new policy with the given name and content on the DB loop."""
    debug_print("Database", f"Adding policy '{name}'.")
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is not None and current_loop is DATABASE_LOOP:
        await _add_policy_internal(name, content)
        return

    if DATABASE_LOOP is None:
        raise RuntimeError("Database loop is not initialized.")

    future = asyncio.run_coroutine_threadsafe(
        _add_policy_internal(name, content), DATABASE_LOOP
    )
    if current_loop is None:
        future.result()
        return
    await asyncio.wrap_future(future, loop=current_loop)


async def _add_policy_internal(name: str, content: str) -> None:
    if DATABASE is None:
        raise RuntimeError("DATABASE pool is None; did setup_database() run?")
    async with DATABASE.acquire() as connection:
        await connection.execute(
            "INSERT INTO policies (name, content) VALUES (?, ?)", (name, content)
        )
        await connection.commit()

async def get_policy(name: str) -> tuple:
    """Retrieve the content of a policy by its name."""
    debug_print("Database", f"Fetching policy for name '{name}'.")
    async with DATABASE.acquire() as connection:
        cursor = await connection.execute("SELECT content FROM policies WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if not row:
            raise ValueError(f"Policy '{name}' not found.")

        content = row["content"]
        debug_print("Database", f"Returning policy content for '{name}'")
        return name, content
    
async def search_policies(keyword: str) -> List[tuple]:
    """Search for policies containing the given keyword in their name or content."""
    debug_print("Database", f"Searching policies for keyword '{keyword}'.")
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is not None and current_loop is DATABASE_LOOP:
        return await _search_policies_internal(keyword)

    if DATABASE_LOOP is None:
        raise RuntimeError("Database loop is not initialized.")

    future = asyncio.run_coroutine_threadsafe(
        _search_policies_internal(keyword), DATABASE_LOOP
    )
    if current_loop is None:
        return future.result()
    return await asyncio.wrap_future(future, loop=current_loop)


async def _search_policies_internal(keyword: str) -> List[tuple]:
    if DATABASE is None:
        raise RuntimeError("DATABASE pool is None; did setup_database() run?")
    async with DATABASE.acquire() as connection:
        cursor = await connection.execute(
            "SELECT name, content FROM policies WHERE name LIKE ? OR content LIKE ?",
            (f"%{keyword}%", f"%{keyword}%")
        )
        rows = await cursor.fetchall()
        results = [(row["name"], row["content"]) for row in rows]
        debug_print("Database", f"Found {len(results)} policies matching keyword '{keyword}'")
        return results
    
async def get_banned_words() -> List[Tuple[str, str]]:
    """Retrieve all automated moderation words/phrases and their levels."""
    debug_print("Database", "Fetching all automated moderation words/phrases.")
    async with DATABASE.acquire() as connection:
        cursor = await connection.execute("SELECT words, level FROM automod_words")
        rows = await cursor.fetchall()
        results = [(row["words"], row["level"]) for row in rows]
        debug_print("Database", f"Retrieved {len(results)} automated moderation words/phrases.")
        return results
    
async def get_random_prompt_rules(number_of_rules: int) -> List[str]:
    """Retrieve a specified number of random prompt rules from the database."""
    debug_print("Database", f"Fetching {number_of_rules} random prompt rules.")
    async with DATABASE.acquire() as connection:
        cursor = await connection.execute(
            "SELECT rule FROM prompt_rules ORDER BY RANDOM() LIMIT ?", (number_of_rules,)
        )
        rows = await cursor.fetchall()
        results = [row["rule"] for row in rows]
        debug_print("Database", f"Retrieved {len(results)} random prompt rules.")
        return results