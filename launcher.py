"""Launcher entry point for the MaddiePly control panel.

This module wires up the GUI and every long-running backend service so the
packaged executable can start everything from a single entry point.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, TypeVar, Any
from urllib.parse import urlparse

import asqlite
import certifi

from gui import DBEditor
from tools import debug_print, path_from_storage_root, get_reference, set_debug
from local_database import setup_database, get_database_loop, get_setting
from online_database import OnlineDatabase, OnlineStorage
from google_api import GoogleSheets
from chatgpt import ChatGPT
from ai_logic import AssistantManager, start_timer_manager_in_background, setup_gpt_manager
from discordbot import DiscordBot


SERVICE_REGISTRY: dict[str, object] = {}
STARTUP_LOG = path_from_storage_root("startup.log")
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
_T = TypeVar("_T")

def log_startup(message: str) -> None:
	timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	try:
		with open(STARTUP_LOG, "a", encoding="utf-8") as handle:
			handle.write(f"[{timestamp}] {message}\n")
	except Exception:
		pass


def _mask_secret(value: str, *, head: int = 4, tail: int = 3) -> str:
	if not value:
		return "<missing>"
	if len(value) <= head + tail:
		return "*" * len(value)
	return f"{value[:head]}***{value[-tail:]}"


def _log_supabase_env_state() -> None:
	url = os.getenv("SUPABASE_URL", "").strip()
	secret = os.getenv("SUPABASE_SECRET_KEY", "").strip()
	anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
	url_info = urlparse(url) if url else None
	log_startup(
		"Supabase URL present=%s host=%s" % (
			bool(url),
			url_info.netloc if url_info else "n/a",
		)
	)
	log_startup(
		f"Supabase secret key present={bool(secret)} len={len(secret)} sample={_mask_secret(secret)}"
	)
	log_startup(
		f"Supabase anon key present={bool(anon)} len={len(anon)} sample={_mask_secret(anon)}"
	)
	log_startup(f"SSL_CERT_FILE={os.environ.get('SSL_CERT_FILE', '<unset>')}")


def _call_with_timeout(
	factory: Callable[[], _T],
	*,
	timeout: float = 15.0,
	label: str | None = None,
) -> tuple[bool, _T | None, Exception | None]:
	"""Invoke `factory` inside a daemon thread and return (timed_out, value, error)."""

	result: dict[str, Any] = {}
	thread_label = label or getattr(factory, "__name__", "factory")

	def _runner() -> None:
		try:
			result["value"] = factory()
		except Exception as exc:
			result["error"] = exc

	thread = threading.Thread(
		target=_runner,
		name=f"InitFactoryThread-{thread_label}",
		daemon=True,
	)
	thread.start()
	thread.join(timeout)
	if thread.is_alive():
		frame = sys._current_frames().get(thread.ident or -1)
		if frame is not None:
			stack = "".join(traceback.format_stack(frame))
			log_startup(f"{thread_label} factory thread stuck:\n{stack}")
	return thread.is_alive(), result.get("value"), result.get("error")


def _bootstrap_database(db_path: Path, ready_event: threading.Event, status: dict[str, Exception | None]) -> None:
	"""Run inside a background thread to host the asqlite event loop permanently."""

	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)

	async def _init() -> None:
		pool = await asqlite.create_pool(str(db_path))
		await setup_database(pool)

	try:
		loop.run_until_complete(_init())
	except Exception as exc:  # pragma: no cover - startup failures should surface to the main thread
		status["error"] = exc
	finally:
		ready_event.set()

	if status.get("error") is None:
		loop.run_forever()


def ensure_local_database_ready() -> None:
	"""Spin up the SQLite connection pool and keep its event loop alive."""

	db_path = path_from_storage_root("maddieply.db")
	db_path.parent.mkdir(parents=True, exist_ok=True)
	log_startup(f"ensure_local_database_ready: db_path={db_path}")

	ready_event = threading.Event()
	status: dict[str, Exception | None] = {"error": None}

	threading.Thread(
		target=_bootstrap_database,
		name="SQLiteLoop",
		args=(db_path, ready_event, status),
		daemon=True,
	).start()

	ready_event.wait()
	if status.get("error") is not None:
		log_startup(f"SQLite loop failed: {status['error']}")
		raise status["error"]  # type: ignore[misc]

	log_startup("SQLite loop initialized; waiting for DATABASE_LOOP capture")
	# Wait for local_database.set_database() to capture the loop
	for _ in range(200):
		if get_database_loop() is not None:
			log_startup("DATABASE_LOOP detected")
			return
		time.sleep(0.05)
	log_startup("Database event loop failed to initialize within timeout")
	raise RuntimeError("Database event loop failed to initialize.")


def run_on_db_loop(coro, *, timeout: float | None = 30.0):
	"""Schedule a coroutine on the persistent DB loop and block until it finishes."""

	loop = get_database_loop()
	if loop is None:
		raise RuntimeError("Database loop is not ready.")
	future = asyncio.run_coroutine_threadsafe(coro, loop)
	return future.result(timeout)


def get_setting_sync(key: str, default=None):
	"""Helper to fetch settings from synchronous code."""

	return run_on_db_loop(get_setting(key, default))


def wait_for_reference(name: str, *, timeout: float = 30.0):
	"""Poll the shared reference registry until an object becomes available."""

	deadline = time.time() + timeout
	while time.time() < deadline:
		ref = get_reference(name)
		if ref is not None:
			return ref
		time.sleep(0.1)
	raise TimeoutError(f"Timed out waiting for reference '{name}'.")


def initialize_services() -> None:
	"""Bring every long-running backend component online in order."""
	print("Initializing backend services...")
	log_startup("initialize_services: starting")
	log_startup(f"Python executable: {sys.executable}")
	log_startup(f"Python version: {sys.version}")
	_log_supabase_env_state()

	debug_print("Launcher", "Initializing backend services...")

	log_startup("Initializing OnlineStorage...")
	timed_out, storage, storage_error = _call_with_timeout(OnlineStorage, timeout=15.0, label="OnlineStorage")
	if timed_out:
		log_startup("OnlineStorage init timed out after 15s; skipping")
		debug_print("Launcher", "OnlineStorage init timed out; service skipped")
	elif storage_error is not None:
		debug_print("Launcher", f"OnlineStorage init failed: {storage_error}")
		log_startup(f"OnlineStorage init failed: {storage_error}")
	elif storage is not None:
		SERVICE_REGISTRY["online_storage"] = storage
		log_startup("OnlineStorage initialized")

	log_startup("Initializing OnlineDatabase...")
	try:
		database = OnlineDatabase()
		SERVICE_REGISTRY["online_database"] = database
		log_startup("OnlineDatabase initialized")
	except Exception as exc:
		debug_print("Launcher", f"OnlineDatabase init failed: {exc}")
		log_startup(f"OnlineDatabase init failed: {exc}")

	log_startup("Initializing GoogleSheets client...")
	timed_out, sheets, sheets_error = _call_with_timeout(GoogleSheets, timeout=15.0, label="GoogleSheets")
	if timed_out:
		log_startup("GoogleSheets init timed out after 15s; skipping")
		debug_print("Launcher", "GoogleSheets init timed out; service skipped")
	elif sheets_error is not None:
		debug_print("Launcher", f"GoogleSheets init failed: {sheets_error}")
		log_startup(f"GoogleSheets init failed: {sheets_error}")
	elif sheets is not None:
		SERVICE_REGISTRY["google_sheets"] = sheets
		log_startup("GoogleSheets initialized")

	gpt_manager = get_reference("GPTManager")
	if gpt_manager is None:
		log_startup("Initializing ChatGPT manager...")
		try:
			gpt_manager = ChatGPT()
		except Exception as exc:
			debug_print("Launcher", f"ChatGPT init failed: {exc}")
			log_startup(f"ChatGPT init failed: {exc}")
		else:
			SERVICE_REGISTRY["gpt_manager"] = gpt_manager
			log_startup("ChatGPT manager initialized")
	else:
		SERVICE_REGISTRY["gpt_manager"] = gpt_manager
		log_startup("Reused existing ChatGPT manager")

	log_startup("Running setup_gpt_manager")
	try:
		run_on_db_loop(setup_gpt_manager())
		log_startup("setup_gpt_manager completed")
	except Exception as exc:
		debug_print("Launcher", f"setup_gpt_manager failed: {exc}")
		log_startup(f"setup_gpt_manager failed: {exc}")

	log_startup("Initializing AssistantManager...")
	try:
		assistant = AssistantManager()
		SERVICE_REGISTRY["assistant_manager"] = assistant
		log_startup("AssistantManager initialized")
	except Exception as exc:
		debug_print("Launcher", f"AssistantManager init failed: {exc}")
		log_startup(f"AssistantManager init failed: {exc}")

	log_startup("Starting ResponseTimer manager...")
	try:
		start_timer_manager_in_background()
		response_timer = wait_for_reference("ResponseTimer", timeout=60.0)
		SERVICE_REGISTRY["response_timer"] = response_timer
		log_startup("ResponseTimer initialized")
	except Exception as exc:
		debug_print("Launcher", f"ResponseTimer init failed: {exc}")
		log_startup(f"ResponseTimer init failed: {exc}")

	log_startup("Starting Discord bot thread...")
	start_discord_bot_thread()
	log_startup("initialize_services: finished")


def start_discord_bot_thread() -> None:
	"""Launch the Discord bot on a dedicated daemon thread."""

	token = os.getenv("DISCORD_TOKEN", "").strip()
	if not token:
		debug_print("Launcher", "DISCORD_TOKEN env variable is missing; skipping Discord bot startup.")
		log_startup("DISCORD_TOKEN missing; Discord bot not started")
		return

	try:
		prefix = get_setting_sync("Command Prefix", "!")
	except Exception as exc:
		debug_print("Launcher", f"Failed to fetch command prefix: {exc}")
		log_startup(f"Failed to fetch command prefix: {exc}")
		prefix = "!"


	def _run_bot() -> None:
		try:
			bot = DiscordBot(token=token, prefix=prefix)
			log_startup("Discord bot initialized; entering run_forever")
			bot.run_forever()
		except Exception as exc:  # pragma: no cover - long-running background task
			debug_print("Launcher", f"Discord bot stopped unexpectedly: {exc}")
			print(f"[Launcher] Discord bot stopped unexpectedly: {exc}")
			log_startup(f"Discord bot stopped unexpectedly: {exc}")
			traceback.print_exc()

	threading.Thread(target=_run_bot, name="DiscordBotThread", daemon=True).start()
	log_startup("Discord bot thread started")


def launch_gui() -> None:
	"""Instantiate the Tkinter GUI and kick off backend initialization."""

	app = DBEditor()
	log_startup("DBEditor instantiated; starting service init thread")
	threading.Thread(target=initialize_services, name="ServiceInitThread", daemon=True).start()
	app.mainloop()


def main() -> None:
	log_startup("launcher.main starting")
	ensure_local_database_ready()
	set_debug(True)
	launch_gui()
	log_startup("launcher.main exited")


if __name__ == "__main__":
	main()
