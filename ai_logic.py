from local_database import get_setting, get_prompt, get_database_loop, get_random_prompt_rules
from chatgpt import ChatGPT
from tools import get_reference, set_reference, debug_print
from local_database import DATABASE
import asyncio
import threading
import os
import time
import random
import textwrap
import datetime


try:
    import requests
except ImportError:
    requests = None

_timer_loop = None
_timer_thread = None
_timer_loop_ready = threading.Event()
timer_manager = None
MAX_SEARCH_HISTORY = 50

class AssistantManager():
    def __init__(self):
        set_reference("AssistantManager", self)
        self.assistant_name = None
        self.stationary_assistant_name = None
        self.chatGPT: ChatGPT = get_reference("GPTManager")
        self.online_database = get_reference("OnlineDatabase")
        self.discord_bot = get_reference("DiscordBot")
        self.emotes = []
        self.search_history: list[str] = []
        self._ensure_models_loaded()
        debug_print("Assistant", "AssistantManager initialized.")

    def _ensure_models_loaded(self) -> None:
        """Load OpenAI model settings without requiring an active event loop."""
        async def _load():
            try:
                await self.chatGPT.set_models()
            except Exception as exc:
                print(f"Failed to set OpenAI models: {exc}")

        def _schedule_when_ready():
            while True:
                loop = get_database_loop()
                if loop and _loop_is_running(loop) and not _loop_is_closed(loop):
                    try:
                        asyncio.run_coroutine_threadsafe(_load(), loop)
                    except Exception as exc:
                        print(f"Failed to schedule model load: {exc}")
                    return
                time.sleep(0.1)

        loop = get_database_loop()
        if loop and _loop_is_running(loop) and not _loop_is_closed(loop):
            asyncio.run_coroutine_threadsafe(_load(), loop)
        else:
            threading.Thread(target=_schedule_when_ready, daemon=True).start()

    async def generate_chat_response(self, messages: list) -> None:
        """Gathers messages and context and generates a response from chatgpt"""
        debug_print("Assistant", f"Generating chat response with messages: {messages}")
        if not await get_setting("Chat Response Enabled", True):
            debug_print("Assistant", "Chat response is disabled in settings. Aborting response generation.")
            return
        messages_str = "\n".join(messages)
        if not self.discord_bot:
            self.discord_bot = get_reference("DiscordBot")
        prompt = {"role": "user", "content": f"Current Time: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}\nDiscord Chat Messages:\n{messages_str}"}
        response_prompt = await get_prompt("Message Response Prompt")
        rules = []
        if random.randint(1, 100) <= 25:
            random_rules = 1
            if random.randint(1, 100) <= 10:
                random_rules = 2
            rules = await get_random_prompt_rules(random_rules)
        if rules:
            rules_text = "\n".join([f"- {rule}" for rule in rules])
            response_prompt += f"\n\nEXTA RULES (MANDATORY): \n- You must follow all rules listed here, in addition to the standard HARD OUTPUT RULES.\n- Apply them to your response fully.\n{rules_text}"
            debug_print("Assistant", f"Applying extra rules to response prompt:\n{rules_text}")
        chatGPT = asyncio.to_thread(self.chatGPT.handle_chat, {"role": "system", "content": response_prompt}, prompt)
        response = await chatGPT
        await self.discord_bot.send_chat(response)

    async def general_response(self, prompt: str) -> str:
        """Generates a general response from chatgpt based on a prompt"""
        debug_print("Assistant", f"Generating general response with prompt: {prompt}")
        welcome_prompt = {"role": "user", "content": await get_prompt("Welcome First Chatter")}
        chatGPT = asyncio.to_thread(self.chatGPT.handle_chat, welcome_prompt, {"role": "user", "content": prompt})
        response = await chatGPT
        return response.lower()

    #Tools for AI to use
    def search_web(self, search_phrase: str) -> str:
        """Search Google (via the Custom Search JSON API) and summarize the top matches."""
        debug_print("Assistant", f"Searching google: {search_phrase}")
        query = (search_phrase or "").strip()
        self._record_search_history(query)
        if not query:
            return "SEARCH_WEB: No search phrase provided."

        api_key = os.getenv("GOOGLE_API_KEY")
        search_engine_id = os.getenv("GOOGLE_ENGINE_ID")
        if not api_key or not search_engine_id:
            return (
                "SEARCH_WEB: Google search is not configured."
                " Please set both 'Google Search API Key' and 'Google Search Engine ID'."
            )

        if requests is None:
            return "SEARCH_WEB: Python 'requests' package is unavailable."

        try:
            payload = self._perform_google_search(api_key, search_engine_id, query, 5)
        except Exception as exc:
            return f"SEARCH_WEB: Failed to fetch Google results ({exc})."

        items = payload.get("items") if isinstance(payload, dict) else None
        if not items:
            return f"SEARCH_WEB: No Google results found for '{query}'."

        search_info = payload.get("searchInformation") if isinstance(payload, dict) else None
        results = self._summarize_search_results(query, items, search_info)
        debug_print("Assistant", f"Search results:\n{results}")
        return results

    def _record_search_history(self, term: str) -> None:
        safe_term = term if term else "(empty)"
        self.search_history.append(safe_term)
        if len(self.search_history) > MAX_SEARCH_HISTORY:
            del self.search_history[:-MAX_SEARCH_HISTORY]

    def get_search_history(self) -> list[str]:
        return list(self.search_history)

    def _perform_google_search(self, api_key: str, search_engine_id: str, query: str, num_results: int) -> dict:
        params = {
            "key": api_key,
            "cx": search_engine_id,
            "q": query,
            "num": max(1, min(num_results, 10)),
        }
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=10,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = self._format_google_error(response, query)
            raise RuntimeError(detail) from exc
        return response.json()

    def _format_google_error(self, response, query: str) -> str:
        status = getattr(response, "status_code", "unknown")
        body = None
        try:
            body = response.json()
        except Exception:
            body = response.text if hasattr(response, "text") else ""
        if isinstance(body, dict):
            error_obj = body.get("error") or {}
            message = error_obj.get("message") or body.get("message")
        else:
            message = body
        base = f"Google Custom Search API returned HTTP {status} while querying '{query}'."
        if message:
            base += f" Details: {message}"
        return base

    def _summarize_search_results(self, query: str, items: list[dict], search_info: dict | None) -> str:
        lines = [f"Google search summary for '{query}':"]
        for index, item in enumerate(items[:5], start=1):
            title = (item.get("title") or "Untitled").strip()
            snippet = (item.get("snippet") or item.get("htmlSnippet") or "").strip()
            snippet = " ".join(snippet.split())
            if snippet:
                snippet = textwrap.shorten(snippet, width=220, placeholder="…")
            link = item.get("link") or item.get("formattedUrl") or ""
            if link:
                snippet = f"{snippet} (Source: {link})" if snippet else f"Source: {link}"
            summary_line = f"{index}. {title}"
            if snippet:
                summary_line += f" — {snippet}"
            lines.append(summary_line)

        if search_info and search_info.get("totalResults"):
            lines.append(f"Approximate total results: {search_info['totalResults']}")

        return "\n".join(lines)

    def query_long_term_memory(self, query: str) -> str:
        """Tool to be utilized by the AI to query long term memory database."""
        debug_print("Assistant", f"Querying long term memory with query: {query}")
        #Unused


class ResponseTimer():
    def __init__(self):
        set_reference("ResponseTimer", self)
        self.db = DATABASE
        self.message_count = 0
        self.messages_to_process: list[dict] = []
        self.received_messages: list[str] = []
        self.assistant: AssistantManager = get_reference("AssistantManager")
        # Do NOT create asyncio tasks at import time (no running loop when GUI imports).
        # The timer can be started explicitly by calling start_timer() from
        # an async context when the event loop is running.
        self.timer_task = None
        self.processor_task = None
        self.chatGPT: ChatGPT = get_reference("GPTManager")
        self._target_message_count: int = 0
        debug_print("ResponseTimer", "ResponseTimer initialized.")

    async def start_timer(self) -> None:
        """Starts the current response timer"""
        debug_print("ResponseTimer", f"Starting response timer.")
        chat_response_enabled = await get_setting("Chat Response Enabled", False)
        if not chat_response_enabled:
            debug_print("ResponseTimer", "Chat response is disabled. Timer will not start.")
            self._target_message_count = 0
            return
        if self.timer_task:
            if not self.timer_task.done():
                debug_print("ResponseTimer", "Timer is already running. Will not start a new one.")
                return
        maximum_length = await get_setting("Maximum Chat Response Time (seconds)", "600")
        minimum_length = await get_setting("Minimum Chat Response Time (seconds)", "120")
        maximum_messages = await get_setting("Maximum Chat Response Messages", "10")
        minimum_messages = await get_setting("Minimum Chat Response Messages", "1")
        length = random.randint(minimum_length, maximum_length)
        messages = random.randint(minimum_messages, maximum_messages)
        self._target_message_count = messages
        self.timer_task = asyncio.create_task(self.timer(length, messages))

    async def timer(self, length: int, messages: int) -> None:
        """Waits for the specified time and message count before making response"""
        debug_print("ResponseTimer", f"Timer started for {length} seconds and {messages} messages.")
        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            if elapsed >= length:
                debug_print("ResponseTimer", f"Timer reached maximum length of {length} seconds.")
                while True:
                    if self.message_count >= messages:
                        self.message_count = 0
                        debug_print("ResponseTimer", f"Received maximum of {messages} messages to process.")
                        if not self.assistant:
                            self.assistant = get_reference("AssistantManager")
                        messages_snapshot = list(self.received_messages)
                        self.received_messages.clear()
                        message_list = [msg["content"] for msg in messages_snapshot]
                        respond = asyncio.create_task(self.assistant.generate_chat_response(message_list))
                        self.timer_task = None
                        await respond
                        await self.start_timer()
                        return
                    else:
                        await asyncio.sleep(1)
            else:
                await asyncio.sleep(1)

    async def message_processor_loop(self) -> None:
        """Processes messages collected for chat response"""
        """{"content", "author_display", "author_username", "created_at", "attachment_urls"}"""
        while True:
            if self.messages_to_process:
                message_data = self.messages_to_process.pop(0)
                attached_images_descriptions = ""
                if message_data["attachment_urls"]:
                    if not self.chatGPT:
                        self.chatGPT = get_reference("GPTManager")
                    attachements = message_data["attachment_urls"]
                    task_list = []
                    for url in attachements: #Limit to first 3 images for performance
                        if len(task_list) >= 3:
                            break
                        task_list.append(asyncio.to_thread(self.chatGPT.analyze_image, url))
                    response_list = await asyncio.gather(*task_list)
                    attached_images_descriptions = f"The message contains {len(response_list)} images."
                    for i, desc in enumerate(response_list, start=1):
                        attached_images_descriptions += f"\nImage Description {i}: {desc}"
                else:
                    pass
                created_at_str = message_data["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                formatted_string = f"[{created_at_str}] {message_data['author_display'] or message_data['author_username']}: {message_data['content']}\n\n{attached_images_descriptions}"
                message = {"id": message_data.get("message_id"), "content": formatted_string}
                self.received_messages.append(message)
                self.message_count += 1
                await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(5)

    def remove_processed_message(self, message_id: int) -> None:
        """Removes a message from the received messages list after it has been processed."""
        for i, msg in enumerate(self.received_messages):
            if msg.get("id") == message_id:
                del self.received_messages[i]
                self.message_count -= 1
                break

    def edit_processed_message(self, message_id: int, display_name: str, new_content: str) -> None:
        """Edits a message in the received messages list after it has been edited."""
        for i, msg in enumerate(self.received_messages):
            if msg.get("id") == message_id:
                created_at_str = ""
                content_parts = msg.get("content", "").split("]: ", 1)
                if len(content_parts) == 2:
                    timestamp_part = content_parts[0].lstrip("[")
                    try:
                        timestamp = datetime.datetime.strptime(timestamp_part, "%Y-%m-%d %H:%M:%S")
                        created_at_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        created_at_str = ""
                formatted_string = f"[{created_at_str}] {display_name}: {new_content}"
                self.received_messages[i]["content"] = formatted_string
                break
        
    def get_progress_snapshot(self) -> tuple[int, int]:
        """Return a tuple of (messages_received, target_messages)."""
        received = len(self.received_messages) + len(self.messages_to_process)
        target = self._target_message_count
        return received, target

ChatGPT()

async def setup_gpt_manager():
    """Sets up the GPT manager by loading settings from the database."""
    debug_print("AILogic", "Setting up GPT manager with personality prompt.")
    gpt_manager = get_reference("GPTManager")
    await gpt_manager.prepare_history()

def _loop_is_closed(loop) -> bool:
    if loop is None:
        return True
    try:
        return loop.is_closed()
    except Exception:
        return True


def _loop_is_running(loop) -> bool:
    try:
        return loop is not None and loop.is_running() and not loop.is_closed()
    except Exception:
        return False
    
def _pool_is_closed(pool) -> bool:
    if pool is None:
        return True
    indicators = (
        getattr(pool, "closed", None),
        getattr(pool, "_closed", None),
        getattr(pool, "is_closed", None),
        getattr(pool, "_closing", None),
    )
    for flag in indicators:
        current = flag
        if callable(current):
            try:
                current = current()
            except Exception:
                current = None
        if hasattr(current, "is_set"):
            try:
                current = current.is_set()
            except Exception:
                current = None
        if isinstance(current, bool) and current:
            return True
    return False

def _ensure_response_timer_loop() -> asyncio.AbstractEventLoop:
    """Ensure a dedicated asyncio loop exists for ResponseTimer fallback work."""
    global _timer_loop, _timer_thread, _timer_loop_ready
    if _loop_is_running(_timer_loop):
        return _timer_loop

    def _run_loop(loop: asyncio.AbstractEventLoop, ready_evt: threading.Event):
        asyncio.set_event_loop(loop)
        ready_evt.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:
                pass

    new_loop = asyncio.new_event_loop()
    _timer_loop_ready = threading.Event()
    _timer_thread = threading.Thread(
        target=_run_loop,
        args=(new_loop, _timer_loop_ready),
        name="ResponseTimerLoop",
        daemon=True,
    )
    _timer_thread.start()
    _timer_loop_ready.wait()
    _timer_loop = new_loop
    return _timer_loop

def start_timer_manager_in_background():
    """Create a ResponseTimer and start its asyncio loop in a background thread.

    This is safe to call from the synchronous GUI entrypoint. It will create
    a new event loop in a daemon thread, run ResponseTimer.start_timer() to
    schedule the internal timer task, and then run the loop forever.
    """
    global timer_manager, _timer_loop, _timer_thread
    if timer_manager is not None:
        return

    # Start a background initializer thread so we don't block the main (GUI) thread.
    def _initializer():
        global timer_manager, _timer_loop
        import local_database as _db

        wait_counter = 0
        while True:
            pool_obj = getattr(_db, "DATABASE", None)
            loop_obj = getattr(_db, "DATABASE_LOOP", None)
            if pool_obj and not _pool_is_closed(pool_obj) and loop_obj and not _loop_is_closed(loop_obj):
                break
            if wait_counter % 6 == 0:
                print("[INFO]Waiting for database and event loop to be initialized before starting ResponseTimer...")
            wait_counter += 1
            time.sleep(0.5)

        timer_manager = ResponseTimer()
        set_reference("ResponseTimer", timer_manager)

        def _schedule_on(loop: asyncio.AbstractEventLoop) -> bool:
            pool_obj = getattr(_db, "DATABASE", None)
            if _pool_is_closed(pool_obj) or loop is None or _loop_is_closed(loop):
                return False
            try:
                async def _bootstrap_timer():
                    if not timer_manager.processor_task or timer_manager.processor_task.done():
                        timer_manager.processor_task = asyncio.create_task(timer_manager.message_processor_loop())
                    await timer_manager.start_timer()

                asyncio.run_coroutine_threadsafe(_bootstrap_timer(), loop)
            except Exception as e:
                print(f"[WARN] Failed to start ResponseTimer on loop: {e}")
                return False
            return True

        while True:
            loop = _db.get_database_loop()
            if loop is not None and not _loop_is_closed(loop):
                if _schedule_on(loop):
                    _timer_loop = loop
                    return
                print("[WARN] DB event loop unavailable for ResponseTimer; falling back to dedicated loop.")
            else:
                print("[WARN] DB event loop missing; attempting fallback ResponseTimer loop.")

            loop = _ensure_response_timer_loop()
            _timer_loop = loop
            if _schedule_on(loop):
                return
            print("[WARN] Failed to start ResponseTimer; retrying in 1s.")
            time.sleep(1.0)

    threading.Thread(target=_initializer, daemon=True).start()