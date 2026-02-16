import os
import sys
import sqlite3
import threading
import asyncio
import typing
import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont
from tkinter.scrolledtext import ScrolledText

from discordbot import DiscordBot
from tools import debug_print, path_from_app_root, path_from_storage_root, set_debug, get_reference
from ai_logic import start_timer_manager_in_background
from local_database import close_database_sync, get_database_loop
from online_database import OnlineDatabase


DB_FILENAME = str(path_from_storage_root("maddieply.db"))
os.makedirs(os.path.dirname(DB_FILENAME), exist_ok=True)

ROW_STRIPE_LIGHT = "#ffffff"
ROW_STRIPE_DARK = "#f6f6f6"

COMBO_SETTING_KEYS = {
    "Default OpenAI Model",
    "Fine-tune GPT Model",
    "Fine-tune Bot Detection Model",
}

AUDIO_DEVICES: list[str] = []
ELEVEN_LABS_VOICE_MODELS: list[str] = []


class ConsoleRedirector:
    """Mirror stdout/stderr into the console tab while keeping the original stream."""

    def __init__(self, widget: ScrolledText, original_stream):
        self.widget = widget
        self.original_stream = original_stream

    def write(self, text: str) -> None:
        if not text:
            return
        if self.widget is not None:
            self.widget.configure(state="normal")
            self.widget.insert(tk.END, text)
            self.widget.see(tk.END)
            self.widget.configure(state="disabled")
        if self.original_stream is not None:
            self.original_stream.write(text)

    def flush(self) -> None:
        if self.original_stream is not None:
            self.original_stream.flush()


class DBEditor(tk.Tk):
    """Tkinter control panel for managing settings, prompts, users, and logs."""

    def __init__(self) -> None:
        super().__init__()
        self.title("MaddiePly Control Panel")
        self.geometry("1280x820")
        self.minsize(1100, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.frames: dict[str, typing.Any] = {}
        self.inline_settings_container: ttk.Frame | None = None
        self.console_text: ScrolledText | None = None
        self.prompt_rows: list[sqlite3.Row] = []
        self.prompt_rules_rows: list[sqlite3.Row] = []
        self.policies_rows: list[sqlite3.Row] = []
        self._prompt_rules_lookup: dict[str, sqlite3.Row] = {}
        self._policies_lookup: dict[str, sqlite3.Row] = {}

        self.users_tree: ttk.Treeview | None = None
        self.users_row_data: dict[str, dict[str, typing.Any]] = {}
        self._users_font: tkfont.Font | None = None
        self._users_source_rows: list[dict[str, typing.Any]] = []
        self._users_sort_column: str | None = None
        self._users_sort_direction: str | None = None
        self._users_column_headings = {
            "discord_display_name": "Discord Display Name",
            "twitch_status": "Twitch Link",
            "discord_number_of_messages": "Messages",
            "discord_currency": "Currency",
        }
        self._users_tooltip: tk.Toplevel | None = None
        self._users_tooltip_label: ttk.Label | None = None

        self._openai_model_choices: list[str] | None = None
        self._online_db: OnlineDatabase | None = None
        self._online_db_lock = threading.Lock()
        self._online_db_loop: asyncio.AbstractEventLoop | None = None
        self._online_db_loop_thread: threading.Thread | None = None

        self._stdout_redirector: ConsoleRedirector | None = None
        self._stderr_redirector: ConsoleRedirector | None = None
        self.response_status_label: ttk.Label | None = None
        self._reference_cache: dict[str, object] = {}

        self._build_ui()
        self._build_response_overlay()

        self.after(50, lambda: self.refresh_table("settings"))
        self.after(100, self.refresh_prompts_tab)
        self.after(125, self.refresh_prompt_rules_tab)
        self.after(150, self.refresh_users_tab)
        self.after(200, self.refresh_policies_tab)
        self.after(250, self._memory_tab_poll)

    # ------------------------------------------------------------------
    # Tk/DB helpers
    # ------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_FILENAME)
        conn.row_factory = sqlite3.Row
        return conn

    def _build_ui(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(container)
        notebook.pack(fill=tk.BOTH, expand=True)

        self._build_settings_tab(notebook)
        self._build_prompts_tab(notebook)
        self._build_prompt_rules_tab(notebook)
        self._build_policies_tab(notebook)
        self._build_memory_tab(notebook)
        self._build_users_tab(notebook)
        self._build_console_tab(notebook)

    # ------------------------------------------------------------------
    # Settings tab
    # ------------------------------------------------------------------
    def _build_settings_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=3)
        tab.rowconfigure(1, weight=1)
        notebook.add(tab, text="Settings")

        tree_frame = ttk.Frame(tab)
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("key", "value")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("key", text="Setting")
        tree.heading("value", text="Value")
        tree.grid(row=0, column=0, sticky="nsew")
        tree.tag_configure("row_light", background=ROW_STRIPE_LIGHT)
        tree.tag_configure("row_dark", background=ROW_STRIPE_DARK)
        tree.bind("<Double-1>", self._on_settings_tree_double_click)
        self.frames["settings_tree"] = tree

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        btn_frame = ttk.Frame(tree_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        btn_frame.columnconfigure((0, 1, 2), weight=1)

        ttk.Button(btn_frame, text="Edit", command=lambda: self._open_setting_dialog(edit_existing=True)).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(btn_frame, text="Refresh", command=lambda: self.refresh_table("settings")).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(btn_frame, text="Resync Slash Commands", command=self._resync_slash_commands).grid(row=0, column=2, sticky="ew", padx=2)

        inline = ttk.Frame(tab)
        inline.grid(row=1, column=0, sticky="nw", padx=10, pady=(0, 10))
        inline.columnconfigure(0, weight=0)
        inline.columnconfigure(1, weight=0)
        self.inline_settings_container = inline

    def refresh_table(self, table: str) -> None:
        if table != "settings":
            return
        tree: ttk.Treeview | None = self.frames.get("settings_tree")
        if tree is None:
            return
        rows = self._fetch_settings_rows()
        for item in tree.get_children():
            tree.delete(item)
        display_rows = [
            row
            for row in rows
            if str(row["data_type"]).upper() != "BOOL" and row["key"] not in COMBO_SETTING_KEYS
        ]
        for idx, row in enumerate(display_rows):
            tag = "row_light" if idx % 2 == 0 else "row_dark"
            tree.insert("", tk.END, iid=row["key"], values=(row["key"], row["value"]), tags=(tag,))
        self.autosize_columns(tree)
        self._refresh_inline_settings(rows)

    def _fetch_settings_rows(self) -> list[sqlite3.Row]:
        conn = self.connect()
        try:
            cursor = conn.execute("SELECT key, value, data_type FROM settings ORDER BY key")
            return cursor.fetchall()
        finally:
            conn.close()

    def _refresh_inline_settings(self, rows: list[sqlite3.Row]) -> None:
        if self.inline_settings_container is None:
            return
        container = self.inline_settings_container
        for child in container.winfo_children():
            child.destroy()

        bool_rows = [row for row in rows if str(row["data_type"]).upper() == "BOOL"]
        combo_rows = [row for row in rows if row["key"] in COMBO_SETTING_KEYS]

        next_column = 0

        def _place_frame(frame: ttk.LabelFrame) -> None:
            nonlocal next_column
            container.grid_columnconfigure(next_column, weight=0)
            frame.grid(row=0, column=next_column, sticky="nw", padx=8, pady=8)
            frame.configure(width=360)
            next_column += 1

        if bool_rows:
            frame = ttk.LabelFrame(container, text="Toggles")
            frame.columnconfigure(0, weight=1)
            for idx, row in enumerate(sorted(bool_rows, key=lambda r: r["key"].lower())):
                key = row["key"]
                value = str(row["value"]).lower() in {"1", "true", "t", "yes", "y", "on"}
                var = tk.BooleanVar(value=value)

                def _on_toggle(*_, k=key, var_ref=var):
                    self.save_setting_inline(k, "1" if var_ref.get() else "0", "BOOL")

                cb = ttk.Checkbutton(frame, text=key, variable=var)
                cb.grid(row=idx, column=0, sticky="w", padx=4, pady=2)
                var.trace_add("write", _on_toggle)
            _place_frame(frame)

        if combo_rows:
            frame = ttk.LabelFrame(container, text="GPT Models")
            frame.columnconfigure(1, weight=1)
            model_options = self._get_openai_model_choices()
            for idx, row in enumerate(sorted(combo_rows, key=lambda r: r["key"].lower())):
                key = row["key"]
                ttk.Label(frame, text=key).grid(row=idx, column=0, sticky="w", padx=4, pady=2)
                cb = ttk.Combobox(frame, values=model_options, state="readonly")
                cb.grid(row=idx, column=1, sticky="ew", padx=4, pady=2)
                cb.set(row["value"] or "")

                def _on_select(_event=None, k=key, widget=cb):
                    self.save_setting_inline(k, widget.get(), "TEXT")

                cb.bind("<<ComboboxSelected>>", _on_select)
            _place_frame(frame)

    def _get_openai_model_choices(self) -> list[str]:
        if self._openai_model_choices is not None:
            return self._openai_model_choices
        models: list[str] = []
        try:
            gpt_manager = get_reference("GPTManager")
        except Exception as exc:
            debug_print("GUI", f"Failed to access GPTManager for model list: {exc}")
            gpt_manager = None
        if gpt_manager is not None:
            try:
                fetched = gpt_manager.get_all_models() or []
                models.extend(str(m) for m in fetched if m)
            except Exception as exc:
                debug_print("GUI", f"Error fetching OpenAI models: {exc}")
        if not models:
            models.extend(["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-4o-mini", "gpt-5"])
        seen: set[str] = set()
        deduped: list[str] = []
        for model in models:
            if model not in seen:
                seen.add(model)
                deduped.append(model)
        self._openai_model_choices = deduped
        return self._openai_model_choices

    def _on_settings_tree_double_click(self, event) -> None:
        tree: ttk.Treeview | None = self.frames.get("settings_tree")
        if tree is None:
            return
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        try:
            tree.selection_set(row_id)
            tree.focus(row_id)
        except Exception:
            pass
        self._open_setting_dialog(edit_existing=True)

    def _open_setting_dialog(self, edit_existing: bool = False) -> None:
        tree: ttk.Treeview | None = self.frames.get("settings_tree")
        selected_key: str | None = None
        row_data: sqlite3.Row | None = None
        if edit_existing:
            if tree is None:
                return
            selection = tree.selection()
            if not selection:
                messagebox.showinfo("Edit Setting", "Select a setting to edit.", parent=self)
                return
            selected_key = selection[0]
            conn = self.connect()
            try:
                cursor = conn.execute("SELECT key, value, data_type FROM settings WHERE key = ?", (selected_key,))
                row_data = cursor.fetchone()
            finally:
                conn.close()
            if row_data is None:
                messagebox.showerror("Edit Setting", "The selected setting no longer exists.", parent=self)
                return

        dlg = tk.Toplevel(self)
        dlg.transient(self)
        dlg.title("Edit Setting" if edit_existing else "Add Setting")
        dlg.grab_set()

        ttk.Label(dlg, text="Key").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        key_var = tk.StringVar(value=row_data["key"] if row_data else "")
        key_entry = ttk.Entry(dlg, textvariable=key_var)
        key_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        if edit_existing:
            key_entry.configure(state="disabled")

        dtype_value = str(row_data["data_type"]).upper() if row_data and row_data["data_type"] else "TEXT"
        ttk.Label(dlg, text=f"Type: {dtype_value}").grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=6)

        value_frame = ttk.Frame(dlg)
        value_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        value_frame.columnconfigure(0, weight=1)
        ttk.Label(value_frame, text="Value").grid(row=0, column=0, sticky="w")
        value_var = tk.StringVar(value=row_data["value"] if row_data else "")
        bool_var = tk.BooleanVar(value=str(row_data["value"]).lower() in {"1", "true"} if row_data else False)
        if dtype_value == "BOOL":
            ttk.Checkbutton(value_frame, text="Enabled", variable=bool_var).grid(row=1, column=0, sticky="w", pady=(4, 0))
        else:
            ttk.Entry(value_frame, textvariable=value_var).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        button_row = ttk.Frame(dlg)
        button_row.grid(row=3, column=0, columnspan=2, pady=10)

        def _on_save() -> None:
            key = key_var.get().strip()
            dtype = dtype_value or "TEXT"
            if not key:
                messagebox.showerror("Save", "Key cannot be empty.", parent=dlg)
                return
            if dtype == "BOOL":
                value = "1" if bool_var.get() else "0"
            else:
                value = value_var.get().strip()
                if dtype == "INTEGER" and value:
                    try:
                        int(value)
                    except Exception:
                        messagebox.showerror("Save", "Value must be an integer.", parent=dlg)
                        return
                if dtype == "CHARACTER" and len(value) != 1:
                    messagebox.showerror("Save", "Value must be a single character.", parent=dlg)
                    return
            conn = self.connect()
            try:
                if edit_existing:
                    conn.execute("UPDATE settings SET value = ?, data_type = ? WHERE key = ?", (value, dtype, key))
                else:
                    conn.execute("INSERT INTO settings(key, value, data_type) VALUES (?, ?, ?)", (key, value, dtype))
                conn.commit()
            except sqlite3.IntegrityError:
                messagebox.showerror("Save", f"A setting named '{key}' already exists.", parent=dlg)
                return
            finally:
                conn.close()
            self.refresh_table("settings")
            dlg.destroy()

        ttk.Button(button_row, text="Save", command=_on_save).grid(row=0, column=0, padx=6)
        ttk.Button(button_row, text="Cancel", command=dlg.destroy).grid(row=0, column=1, padx=6)
        dlg.columnconfigure(1, weight=1)
        self._center_window_over_self(dlg)
        key_entry.focus_set()

    def _delete_selected_setting(self) -> None:
        tree: ttk.Treeview | None = self.frames.get("settings_tree")
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Delete Setting", "Select a setting to delete.", parent=self)
            return
        key = selection[0]
        if not messagebox.askyesno("Delete Setting", f"Delete '{key}'?", parent=self):
            return
        conn = self.connect()
        try:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()
        self.refresh_table("settings")

    # ------------------------------------------------------------------
    # Prompts tab
    # ------------------------------------------------------------------
    def _build_prompts_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Prompts")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        list_frame = ttk.Frame(tab)
        list_frame.grid(row=0, column=0, sticky="ns", padx=(10, 4), pady=10)

        listbox = tk.Listbox(list_frame, exportselection=False, height=20, width=32)
        listbox.pack(side=tk.LEFT, fill=tk.Y)
        listbox.bind("<<ListboxSelect>>", self._on_prompt_select)
        self.frames["prompts_listbox"] = listbox

        lb_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview)
        lb_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.configure(yscrollcommand=lb_scroll.set)

        editor_frame = ttk.Frame(tab)
        editor_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=10)
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)

        text = ScrolledText(editor_frame, wrap=tk.WORD)
        text.grid(row=0, column=0, sticky="nsew")
        self.frames["prompts_text"] = text

        btn_frame = ttk.Frame(editor_frame)
        btn_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        btn_frame.columnconfigure((0, 1, 2), weight=1)
        ttk.Button(btn_frame, text="Save", command=self._save_prompt_text).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(btn_frame, text="Reset", command=self._reset_prompt_text).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh_prompts_tab).grid(row=0, column=2, sticky="ew", padx=2)

    def refresh_prompts_tab(self) -> None:
        listbox: tk.Listbox | None = self.frames.get("prompts_listbox")
        text: ScrolledText | None = self.frames.get("prompts_text")
        if listbox is None or text is None:
            return
        conn = self.connect()
        try:
            cursor = conn.execute("SELECT id, name, prompt FROM prompts ORDER BY name")
            self.prompt_rows = cursor.fetchall()
        finally:
            conn.close()

        listbox.delete(0, tk.END)
        for row in self.prompt_rows:
            listbox.insert(tk.END, row["name"] or "(unnamed)")
        self._apply_listbox_stripes(listbox)
        if self.prompt_rows:
            listbox.selection_set(0)
            self._load_prompt_into_editor(0)
        else:
            text.configure(state="normal")
            text.delete("1.0", tk.END)
            text.configure(state="disabled")

    def _on_prompt_select(self, _event) -> None:
        listbox: tk.Listbox | None = self.frames.get("prompts_listbox")
        if listbox is None:
            return
        selection = listbox.curselection()
        if not selection:
            return
        self._load_prompt_into_editor(selection[0])

    def _load_prompt_into_editor(self, index: int) -> None:
        text: ScrolledText | None = self.frames.get("prompts_text")
        if text is None or index >= len(self.prompt_rows):
            return
        prompt = self.prompt_rows[index]["prompt"] or ""
        text.configure(state="normal")
        text.delete("1.0", tk.END)
        text.insert(tk.END, prompt)
        text.configure(state="normal")

    def _save_prompt_text(self) -> None:
        listbox: tk.Listbox | None = self.frames.get("prompts_listbox")
        text: ScrolledText | None = self.frames.get("prompts_text")
        if listbox is None or text is None:
            return
        selection = listbox.curselection()
        if not selection:
            messagebox.showinfo("Save Prompt", "Select a prompt first.", parent=self)
            return
        index = selection[0]
        row = self.prompt_rows[index]
        new_text = text.get("1.0", tk.END).rstrip()
        conn = self.connect()
        try:
            conn.execute("UPDATE prompts SET prompt = ? WHERE id = ?", (new_text, row["id"]))
            conn.commit()
        finally:
            conn.close()
        self.refresh_prompts_tab()

    def _reset_prompt_text(self) -> None:
        listbox: tk.Listbox | None = self.frames.get("prompts_listbox")
        if listbox is None:
            return
        selection = listbox.curselection()
        if not selection:
            return
        self._load_prompt_into_editor(selection[0])

    # ------------------------------------------------------------------
    # Prompt Rules tab
    # ------------------------------------------------------------------
    def _build_prompt_rules_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Prompt Rules")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(1, weight=0)

        columns = ("rule",)
        tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        tree.heading("rule", text="Rule")
        tree.column("rule", anchor=tk.W)
        tree.tag_configure("row_light", background=ROW_STRIPE_LIGHT)
        tree.tag_configure("row_dark", background=ROW_STRIPE_DARK)
        tree.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))
        self.frames["prompt_rules_tree"] = tree

        yscroll = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns", pady=(10, 5))

        btn_frame = ttk.Frame(tab)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)
        btn_frame.columnconfigure(2, weight=1)
        ttk.Button(btn_frame, text="Add", command=self._open_prompt_rule_dialog).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(btn_frame, text="Delete", command=self._delete_selected_prompt_rule).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh_prompt_rules_tab).grid(row=0, column=2, sticky="ew", padx=4)

    def refresh_prompt_rules_tab(self) -> None:
        tree: ttk.Treeview | None = self.frames.get("prompt_rules_tree")
        if tree is None:
            return
        conn = self.connect()
        try:
            cursor = conn.execute("SELECT id, rule FROM prompt_rules ORDER BY id")
            rows = cursor.fetchall()
        except sqlite3.Error as exc:
            debug_print("GUI", f"Failed to load prompt rules: {exc}")
            rows = []
        finally:
            conn.close()

        for iid in tree.get_children():
            tree.delete(iid)

        self.prompt_rules_rows = rows
        self._prompt_rules_lookup = {str(row["id"]): row for row in rows if row["id"] is not None}

        for idx, row in enumerate(rows):
            iid = str(row["id"])
            rule_text = row["rule"] or ""
            tag = "row_light" if idx % 2 == 0 else "row_dark"
            tree.insert("", tk.END, iid=iid, values=(rule_text,), tags=(tag,))
        self.autosize_columns(tree)

    def _open_prompt_rule_dialog(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.transient(self)
        dlg.title("Add Prompt Rule")
        dlg.grab_set()

        ttk.Label(dlg, text="Rule Text").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 4))
        text_widget = ScrolledText(dlg, width=60, height=6, wrap=tk.WORD)
        text_widget.grid(row=1, column=0, sticky="nsew", padx=8)
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(1, weight=1)

        button_row = ttk.Frame(dlg)
        button_row.grid(row=2, column=0, pady=8)

        def _save_rule() -> None:
            content = text_widget.get("1.0", tk.END).strip()
            if not content:
                messagebox.showerror("Add Prompt Rule", "Rule text cannot be empty.", parent=dlg)
                return
            conn = self.connect()
            try:
                conn.execute("INSERT INTO prompt_rules(rule) VALUES (?)", (content,))
                conn.commit()
            except sqlite3.Error as exc:
                messagebox.showerror("Add Prompt Rule", f"Failed to save rule: {exc}", parent=dlg)
                return
            finally:
                conn.close()
            self.refresh_prompt_rules_tab()
            dlg.destroy()

        ttk.Button(button_row, text="Save", command=_save_rule).grid(row=0, column=0, padx=6)
        ttk.Button(button_row, text="Cancel", command=dlg.destroy).grid(row=0, column=1, padx=6)
        self._center_window_over_self(dlg)
        text_widget.focus_set()

    def _delete_selected_prompt_rule(self) -> None:
        tree: ttk.Treeview | None = self.frames.get("prompt_rules_tree")
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            messagebox.showinfo("Delete Prompt Rule", "Select a rule to delete.", parent=self)
            return
        iid = selection[0]
        row = self._prompt_rules_lookup.get(iid)
        if row is None:
            messagebox.showerror("Delete Prompt Rule", "Unable to locate the selected rule.", parent=self)
            return
        rule_text = (row["rule"] or "").strip()
        if not messagebox.askyesno("Delete Prompt Rule", f"Delete this rule?\n\n{rule_text}", parent=self):
            return
        conn = self.connect()
        try:
            conn.execute("DELETE FROM prompt_rules WHERE id = ?", (int(iid),))
            conn.commit()
        except sqlite3.Error as exc:
            messagebox.showerror("Delete Prompt Rule", f"Failed to delete rule: {exc}", parent=self)
            return
        finally:
            conn.close()
        self.refresh_prompt_rules_tab()

    # ------------------------------------------------------------------
    # Policies tab
    # ------------------------------------------------------------------
    def _build_policies_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Policies")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=3)
        tab.rowconfigure(1, weight=2)
        tab.rowconfigure(2, weight=0)

        tree_frame = ttk.Frame(tab)
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("name", "summary")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("name", text="Policy")
        tree.heading("summary", text="Summary")
        tree.column("name", anchor=tk.W, width=220)
        tree.column("summary", anchor=tk.W)
        tree.tag_configure("row_light", background=ROW_STRIPE_LIGHT)
        tree.tag_configure("row_dark", background=ROW_STRIPE_DARK)
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<<TreeviewSelect>>", self._on_policy_select)
        self.frames["policies_tree"] = tree

        yscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")

        detail = ScrolledText(tab, wrap=tk.WORD, state="disabled")
        detail.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.frames["policies_detail"] = detail

        btn_frame = ttk.Frame(tab)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        ttk.Button(btn_frame, text="Refresh", command=self.refresh_policies_tab).grid(row=0, column=0, sticky="w")

    def refresh_policies_tab(self) -> None:
        tree: ttk.Treeview | None = self.frames.get("policies_tree")
        if tree is None:
            return
        conn = self.connect()
        try:
            cursor = conn.execute("SELECT name, content FROM policies ORDER BY name COLLATE NOCASE")
            rows = cursor.fetchall()
        except sqlite3.Error as exc:
            debug_print("GUI", f"Failed to load policies: {exc}")
            rows = []
        finally:
            conn.close()

        for iid in tree.get_children():
            tree.delete(iid)

        self.policies_rows = rows
        self._policies_lookup = {row["name"]: row for row in rows if row["name"]}

        for idx, row in enumerate(rows):
            name = row["name"] or f"Policy {idx + 1}"
            preview = self._format_policy_preview(row["content"])
            tag = "row_light" if idx % 2 == 0 else "row_dark"
            tree.insert("", tk.END, iid=name, values=(name, preview), tags=(tag,))
        self.autosize_columns(tree)

        if rows:
            first_id = rows[0]["name"]
            if first_id:
                try:
                    tree.selection_set(first_id)
                    tree.focus(first_id)
                except Exception:
                    pass
                self._load_policy_detail(first_id)
                return
        self._load_policy_detail(None)

    def _on_policy_select(self, _event=None) -> None:
        tree: ttk.Treeview | None = self.frames.get("policies_tree")
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            self._load_policy_detail(None)
            return
        self._load_policy_detail(selection[0])

    def _load_policy_detail(self, policy_name: str | None) -> None:
        detail: ScrolledText | None = self.frames.get("policies_detail")
        if detail is None:
            return
        detail.configure(state="normal")
        detail.delete("1.0", tk.END)
        if policy_name:
            row = self._policies_lookup.get(policy_name)
            if row:
                name = row["name"] or policy_name
                content = row["content"] or ""
                detail.insert(tk.END, f"{name}\n\n{content}")
        detail.configure(state="disabled")

    @staticmethod
    def _format_policy_preview(content: str | None) -> str:
        text = (content or "").strip()
        if not text:
            return ""
        text = " ".join(text.split())
        return text if len(text) <= 120 else f"{text[:117]}..."

    # ------------------------------------------------------------------
    # Memory tab
    # ------------------------------------------------------------------
    def _build_memory_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Memory")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        memory_frame = ttk.LabelFrame(tab, text="Working Memory")
        memory_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 5))
        mem_text = ScrolledText(memory_frame, wrap=tk.WORD)
        mem_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.frames["working_memory_text"] = mem_text

        history_frame = ttk.LabelFrame(tab, text="Google Search History")
        history_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        history_text = ScrolledText(history_frame, wrap=tk.WORD)
        history_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.frames["search_history_text"] = history_text

        btn_frame = ttk.Frame(tab)
        btn_frame.place(relx=1.0, rely=0.0, anchor="ne", x=-20, y=10)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh_memory_tab).grid(row=0, column=0)

    def refresh_memory_tab(self) -> None:
        self._populate_memory_tab()

    def _memory_tab_poll(self) -> None:
        self._populate_memory_tab()
        self.after(2000, self._memory_tab_poll)

    def _populate_memory_tab(self) -> None:
        memory_widget: ScrolledText | None = self.frames.get("working_memory_text")
        history_widget: ScrolledText | None = self.frames.get("search_history_text")
        if memory_widget is None or history_widget is None:
            return

        gpt_manager = self._get_cached_reference("GPTManager")
        working_memory = "No working memory available."
        if gpt_manager and hasattr(gpt_manager, "get_working_memory"):
            try:
                working_memory = gpt_manager.get_working_memory() or "(empty)"
            except Exception as exc:
                working_memory = f"Failed to load working memory: {exc}"

        assistant = self._get_cached_reference("AssistantManager")
        search_history_display = "No searches recorded yet."
        if assistant and hasattr(assistant, "get_search_history"):
            try:
                history_items = assistant.get_search_history()
            except Exception as exc:
                history_items = [f"Failed to load search history: {exc}"]
            if history_items:
                search_history_display = "\n".join(
                    f"{idx + 1}. {term}" for idx, term in enumerate(history_items)
                )

        self._set_scrolledtext_content(memory_widget, working_memory)
        self._set_scrolledtext_content(history_widget, search_history_display)

    @staticmethod
    def _set_scrolledtext_content(widget: ScrolledText, content: str) -> None:
        if widget is None or not widget.winfo_exists():
            return
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, content)
        widget.configure(state="disabled")

    # ------------------------------------------------------------------
    # Reference helpers
    # ------------------------------------------------------------------
    def _get_cached_reference(self, name: str):
        cached = self._reference_cache.get(name)
        if cached is not None:
            return cached
        ref = get_reference(name)
        if ref is not None:
            self._reference_cache[name] = ref
        return ref

    # ------------------------------------------------------------------
    # Overlay + status helpers
    # ------------------------------------------------------------------
    def _build_response_overlay(self) -> None:
        label = ttk.Label(self, text="Response Timer: unavailable", padding=(10, 5))
        label.place(relx=1.0, x=-20, y=10, anchor="ne")
        self.response_status_label = label
        self._update_response_status_label()

    def _update_response_status_label(self) -> None:
        label = self.response_status_label
        if label is None or not label.winfo_exists():
            self.response_status_label = None
            return
        timer = self._get_cached_reference("ResponseTimer")
        status_text = "Response Timer: unavailable"
        if timer and hasattr(timer, "get_progress_snapshot"):
            try:
                received, target = timer.get_progress_snapshot()
                if target > 0:
                    status_text = f"Response Timer: {min(received, target)}/{target} msgs"
                elif received > 0:
                    status_text = f"Response Timer: {received} msgs queued"
                else:
                    status_text = "Response Timer: waiting..."
            except Exception as exc:
                status_text = f"Response Timer: error ({exc})"
        label.configure(text=status_text)
        self.after(1000, self._update_response_status_label)

    # ------------------------------------------------------------------
    # Users tab
    # ------------------------------------------------------------------
    def _build_users_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Users")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        columns = (
            "discord_display_name",
            "twitch_status",
            "discord_number_of_messages",
            "discord_currency",
        )
        tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            tree.heading(col, text=self._users_column_headings[col], anchor=tk.CENTER,
                        command=lambda c=col: self._on_users_heading_click(c))
            tree.column(col, anchor=tk.CENTER)
        tree.tag_configure("row_light", background=ROW_STRIPE_LIGHT)
        tree.tag_configure("row_dark", background=ROW_STRIPE_DARK)
        tree.grid(row=0, column=0, sticky="nsew")
        tree.bind("<Motion>", self._on_users_tree_motion)
        tree.bind("<Leave>", lambda _e: self._hide_users_tooltip())
        self.users_tree = tree

        yscroll = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")

        btn_frame = ttk.Frame(tab)
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        btn_frame.columnconfigure((0, 1), weight=1)
        ttk.Button(btn_frame, text="Refresh", command=self.refresh_users_tab).grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Button(btn_frame, text="Remove Selected", command=self._remove_selected_user).grid(row=0, column=1, sticky="ew", padx=4)

    def refresh_users_tab(self) -> None:
        def worker():
            try:
                rows = self._run_online_db_task(lambda db: db.fetch_table("users", limit=2000)) or []
            except Exception as exc:
                debug_print("GUI", f"Failed to fetch users: {exc}")
                rows = []
            self.after(0, lambda data=rows: self._apply_users_rows(data))

        threading.Thread(target=worker, daemon=True).start()

    def _run_online_db_task(self, coro_factory: typing.Callable[[OnlineDatabase], typing.Awaitable[typing.Any]]):
        db = self._get_online_db()
        loop = self._ensure_online_db_loop()
        future = asyncio.run_coroutine_threadsafe(coro_factory(db), loop)
        return future.result()

    def _get_online_db(self) -> OnlineDatabase:
        with self._online_db_lock:
            if self._online_db is not None:
                return self._online_db
            existing = get_reference("OnlineDatabase")
            if isinstance(existing, OnlineDatabase):
                self._online_db = existing
            else:
                self._online_db = OnlineDatabase()
            return self._online_db

    def _ensure_online_db_loop(self) -> asyncio.AbstractEventLoop:
        with self._online_db_lock:
            loop = self._online_db_loop
            if loop is not None and loop.is_running():
                return loop

            ready = threading.Event()

            def _runner():
                new_loop = asyncio.new_event_loop()
                with self._online_db_lock:
                    self._online_db_loop = new_loop
                ready.set()
                try:
                    new_loop.run_forever()
                finally:
                    try:
                        new_loop.close()
                    except Exception:
                        pass

            thread = threading.Thread(target=_runner, name="OnlineDBLoop", daemon=True)
            self._online_db_loop_thread = thread
            thread.start()

        ready.wait()
        assert self._online_db_loop is not None
        return self._online_db_loop

    def _shutdown_online_db_loop(self) -> None:
        loop = self._online_db_loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        self._online_db_loop = None
        self._online_db_loop_thread = None

    def _apply_users_rows(self, rows: list[dict[str, typing.Any]]) -> None:
        if self.users_tree is None:
            return
        tree = self.users_tree
        for iid in tree.get_children():
            tree.delete(iid)
        self.users_row_data.clear()
        prepared: list[dict[str, typing.Any]] = []
        filtered_rows = [row for row in rows if str(row.get("discord_id") or "").strip()]
        for idx, row in enumerate(filtered_rows):
            normalized = self._prepare_user_row(dict(row), idx)
            prepared.append(normalized)
        self._users_source_rows = prepared
        sorted_rows = self._get_sorted_user_rows(prepared)
        for idx, data in enumerate(sorted_rows):
            iid = data["_tree_id"]
            self.users_row_data[iid] = data
            tag = "row_light" if idx % 2 == 0 else "row_dark"
            tree.insert("", tk.END, iid=iid, values=self._build_user_row_values(data), tags=(tag,))
        self._update_users_tree_headings()
        self._autosize_users_columns()

    def _prepare_user_row(self, row_data: dict[str, typing.Any], fallback_index: int) -> dict[str, typing.Any]:
        row_data = dict(row_data)
        row_data["_fallback_index"] = fallback_index

        def _first_str(*names: str) -> str | None:
            for name in names:
                value = row_data.get(name)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    return text
            return None

        def _first_int(*names: str, default: int = 0) -> int:
            for name in names:
                value = row_data.get(name)
                if value is None:
                    continue
                try:
                    return int(value)
                except (ValueError, TypeError):
                    try:
                        return int(float(value))
                    except Exception:
                        continue
            return default

        discord_display_name = _first_str("discord_display_name", "discord_username", "display_name", "username") or "Unknown"
        discord_username = _first_str("discord_username")
        twitch_display_name = _first_str("twitch_display_name", "twitch_username")
        twitch_id = _first_str("twitch_id")
        twitch_linked = bool(twitch_id)
        discord_messages = _first_int("discord_number_of_messages")
        discord_currency = _first_int("discord_currency")

        row_data["_tree_id"] = self._resolve_user_row_id(row_data, fallback_index)
        row_data["_discord_display_name"] = discord_display_name
        row_data["_discord_display_name_sort"] = discord_display_name.casefold()
        row_data["_discord_username"] = discord_username
        row_data["_twitch_status"] = "Linked" if twitch_linked else "Unlinked"
        row_data["_twitch_linked"] = twitch_linked
        row_data["_twitch_display_name"] = twitch_display_name
        row_data["_discord_messages"] = discord_messages
        row_data["_discord_currency"] = discord_currency
        return row_data

    def _build_user_row_values(self, row_data: dict[str, typing.Any]) -> tuple[typing.Any, ...]:
        return (
            row_data.get("_discord_display_name", "Unknown"),
            row_data.get("_twitch_status", "Unlinked"),
            row_data.get("_discord_messages", 0),
            row_data.get("_discord_currency", 0),
        )

    def _get_sorted_user_rows(self, rows: list[dict[str, typing.Any]]) -> list[dict[str, typing.Any]]:
        if not rows:
            return []
        column = self._users_sort_column
        direction = self._users_sort_direction
        if not column or direction not in {"asc", "desc"}:
            return sorted(rows, key=self._user_identity_sort_key)
        reverse = direction == "desc"
        return sorted(
            rows,
            key=lambda row: (
                self._users_sort_key_for_column(row, column),
                self._user_identity_sort_key(row),
            ),
            reverse=reverse,
        )

    def _users_sort_key_for_column(self, row: dict[str, typing.Any], column: str):
        if column == "discord_display_name":
            return (row.get("_discord_display_name_sort", ""),)
        if column == "twitch_status":
            return (
                0 if row.get("_twitch_linked") else 1,
                row.get("_discord_display_name_sort", ""),
            )
        if column == "discord_number_of_messages":
            return (row.get("_discord_messages", 0),)
        if column == "discord_currency":
            return (row.get("_discord_currency", 0),)
        return self._user_identity_sort_key(row)

    def _user_identity_sort_key(self, row: dict[str, typing.Any]) -> tuple[int, typing.Any]:
        for candidate in (
            row.get("id"),
            row.get("discord_id"),
            row.get("twitch_id"),
            row.get("_tree_id"),
        ):
            if candidate is None:
                continue
            text = str(candidate).strip()
            if not text:
                continue
            if text.isdigit():
                return (0, int(text))
            try:
                return (0, int(float(text)))
            except Exception:
                return (1, text.casefold())
        return (2, row.get("_fallback_index", 0))

    def _on_users_heading_click(self, column_id: str) -> None:
        if column_id not in self._users_column_headings:
            return
        if self._users_sort_column == column_id:
            self._users_sort_direction = self._cycle_users_sort_direction(self._users_sort_direction)
            if self._users_sort_direction is None:
                self._users_sort_column = None
        else:
            self._users_sort_column = column_id
            self._users_sort_direction = "asc"
        self._apply_users_rows(list(self._users_source_rows))

    @staticmethod
    def _cycle_users_sort_direction(current: str | None) -> str | None:
        if current == "asc":
            return "desc"
        if current == "desc":
            return None
        return "asc"

    def _update_users_tree_headings(self) -> None:
        if self.users_tree is None:
            return
        for col_id, base_label in self._users_column_headings.items():
            suffix = ""
            if self._users_sort_column == col_id:
                if self._users_sort_direction == "asc":
                    suffix = " ↑"
                elif self._users_sort_direction == "desc":
                    suffix = " ↓"
            self.users_tree.heading(col_id, text=f"{base_label}{suffix}")

    def _autosize_users_columns(self) -> None:
        if self.users_tree is None:
            return
        tree = self.users_tree
        if self._users_font is None:
            try:
                self._users_font = tkfont.nametofont(tree.cget("font"))
            except Exception:
                self._users_font = tkfont.nametofont("TkDefaultFont")
        font = self._users_font
        padding = 24
        for col in tree["columns"]:
            heading = tree.heading(col)
            header_text = heading.get("text", col)
            max_width = font.measure(header_text)
            for item in tree.get_children():
                text = tree.set(item, col)
                if text is None:
                    continue
                width = font.measure(str(text))
                if width > max_width:
                    max_width = width
            tree.column(col, width=max_width + padding)

    def _resolve_user_row_id(self, row: dict[str, typing.Any], fallback_index: int) -> str:
        for key in ("id", "discord_id", "discord_username", "discord_display_name", "twitch_id"):
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return f"row-{fallback_index}"

    def _remove_selected_user(self) -> None:
        if self.users_tree is None:
            return
        selection = self.users_tree.selection()
        if not selection:
            messagebox.showinfo("Remove User", "Select a user first.", parent=self)
            return
        iid = selection[0]
        row_snapshot = self.users_row_data.get(iid)
        if not row_snapshot:
            messagebox.showerror("Remove User", "Could not locate selected user.", parent=self)
            return
        display_name = row_snapshot.get("_discord_display_name", iid)
        if not messagebox.askyesno("Remove User", f"Remove '{display_name}' from the database?", parent=self):
            return

        lookup_column = "discord_id" if row_snapshot.get("discord_id") else "id"
        lookup_value = row_snapshot.get(lookup_column) or row_snapshot.get("id") or iid

        def worker():
            try:
                self._run_online_db_task(lambda db: db.delete_data("users", lookup_column, lookup_value))
            except Exception as exc:
                debug_print("GUI", f"Failed to remove user {lookup_value}: {exc}")
                self.after(0, lambda: messagebox.showerror("Remove User", "Failed to remove the selected user."))
                return
            self.after(0, self.refresh_users_tab)

        threading.Thread(target=worker, daemon=True).start()

    def _on_users_tree_motion(self, event) -> None:
        if self.users_tree is None:
            return
        column = self.users_tree.identify_column(event.x)
        if column not in {"#1", "#2"}:
            self._hide_users_tooltip()
            return
        item_id = self.users_tree.identify_row(event.y)
        if not item_id:
            self._hide_users_tooltip()
            return
        row = self.users_row_data.get(str(item_id))
        if not row:
            self._hide_users_tooltip()
            return
        tooltip_text = ""
        if column == "#1":
            username = row.get("_discord_username") or row.get("discord_username")
            discord_id = row.get("discord_id")
            details = username or discord_id or "Unknown user"
            tooltip_text = f"Discord handle: {details}"
        elif column == "#2":
            if row.get("_twitch_linked"):
                linked_name = row.get("_twitch_display_name") or row.get("twitch_display_name") or row.get("twitch_username")
                tooltip_text = f"Linked handle: {linked_name or 'Unknown'}"
            else:
                tooltip_text = "No linked handle"

        if tooltip_text:
            self._show_users_tooltip(tooltip_text, event.x_root, event.y_root)
        else:
            self._hide_users_tooltip()

    def _show_users_tooltip(self, text: str, root_x: int, root_y: int) -> None:
        if not text:
            self._hide_users_tooltip()
            return
        if self._users_tooltip is None:
            self._users_tooltip = tk.Toplevel(self)
            self._users_tooltip.wm_overrideredirect(True)
            try:
                self._users_tooltip.attributes("-topmost", True)
            except Exception:
                pass
            self._users_tooltip_label = ttk.Label(
                self._users_tooltip,
                text=text,
                background="#ffffe0",
                relief=tk.SOLID,
                borderwidth=1,
                padding=4,
            )
            self._users_tooltip_label.pack()
        else:
            assert self._users_tooltip_label is not None
            self._users_tooltip_label.configure(text=text)
        self._users_tooltip.wm_geometry(f"+{root_x + 16}+{root_y + 16}")

    def _hide_users_tooltip(self) -> None:
        if self._users_tooltip is not None:
            try:
                self._users_tooltip.destroy()
            except Exception:
                pass
            finally:
                self._users_tooltip = None
                self._users_tooltip_label = None

    # ------------------------------------------------------------------
    # Console tab
    # ------------------------------------------------------------------
    def _build_console_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Console")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)

        text = ScrolledText(tab, wrap=tk.WORD, state="disabled")
        text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.console_text = text

        self._stdout_redirector = ConsoleRedirector(text, sys.stdout)
        self._stderr_redirector = ConsoleRedirector(text, sys.stderr)
        sys.stdout = self._stdout_redirector
        sys.stderr = self._stderr_redirector

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def autosize_columns(self, tree: ttk.Treeview) -> None:
        try:
            font = tkfont.nametofont("TkDefaultFont")
        except Exception:
            font = tkfont.Font()
        padding = 16
        cols = list(tree["columns"]) if tree["columns"] else []
        for col in cols:
            header = tree.heading(col).get("text", str(col))
            maxw = font.measure(str(header)) + padding
            for iid in tree.get_children():
                txt = str(tree.set(iid, col) or "")
                w = font.measure(txt) + padding
                if w > maxw:
                    maxw = w
            maxw = min(maxw, 800)
            tree.column(col, width=maxw)

    def _apply_listbox_stripes(self, listbox: tk.Listbox | None) -> None:
        if listbox is None:
            return
        try:
            size = listbox.size()
        except Exception:
            return
        for idx in range(size):
            color = ROW_STRIPE_DARK if idx % 2 == 0 else ROW_STRIPE_LIGHT
            try:
                listbox.itemconfig(idx, background=color)
            except Exception:
                break

    def _center_window_over_self(self, window: tk.Toplevel) -> None:
        try:
            self.update_idletasks()
            window.update_idletasks()
            width = window.winfo_width() or window.winfo_reqwidth()
            height = window.winfo_height() or window.winfo_reqheight()
            parent_x = self.winfo_rootx()
            parent_y = self.winfo_rooty()
            parent_width = self.winfo_width()
            parent_height = self.winfo_height()
            x = parent_x + max((parent_width - width) // 2, 0)
            y = parent_y + max((parent_height - height) // 2, 0)
            window.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Setting persistence helpers
    # ------------------------------------------------------------------
    def save_setting_inline(self, key: str, value: str, data_type: str = "TEXT") -> None:
        debug_print("GUI", f"Inline save: key={key}, value={value}, data_type={data_type}")
        conn = self.connect()
        try:
            if data_type.upper() == "BOOL":
                v = "1" if str(value) in ("1", "True", "true") else "0"
            else:
                v = str(value)
            conn.execute("UPDATE settings SET value = ?, data_type = ? WHERE key = ?", (v, data_type.upper(), key))
            conn.commit()
        finally:
            conn.close()
        self._handle_setting_side_effect(key, v)
        try:
            if key == "Debug Mode":
                debug_bool = str(v) in ("1", "True", "true")
                set_debug(debug_bool)
        except Exception:
            pass
        try:
            self.refresh_table("settings")
        except Exception:
            pass

    def _handle_setting_side_effect(self, key: str | None, value: str | None) -> None:
        if not key:
            return
        normalized = str(value).strip().lower() if value is not None else ""
        should_enable = normalized in ("1", "true", "t", "yes", "y", "on")

        if key.startswith("Shared Chat"):
            self._refresh_shared_chat_settings_async()

        if key == "Chat Response Enabled":
            try:
                start_timer_manager_in_background()
            except Exception:
                pass
            try:
                timer = get_reference("ResponseTimer")
            except Exception:
                timer = None
            if timer is None:
                debug_print("GUI", "Chat Response toggled but ResponseTimer is unavailable.")
                return
            loop = None
            try:
                loop = get_database_loop()
                if loop is not None and (loop.is_closed() or not loop.is_running()):
                    loop = None
            except Exception:
                loop = None
            if loop is None:
                try:
                    import ai_logic

                    loop = getattr(ai_logic, "_timer_loop", None)
                    if loop is not None and (loop.is_closed() or not loop.is_running()):
                        loop = None
                    if loop is None:
                        loop = ai_logic._ensure_response_timer_loop()
                except Exception:
                    loop = None
            if loop is None:
                debug_print("GUI", "Chat Response toggle ignored because no event loop is available for ResponseTimer.")
                return
            try:
                coro = timer.start_timer() if should_enable else timer.end_timer()
                asyncio.run_coroutine_threadsafe(coro, loop)
                debug_print("GUI", f"Scheduled ResponseTimer {'start' if should_enable else 'stop'} after UI toggle.")
            except Exception as e:
                debug_print("GUI", f"Failed to schedule ResponseTimer update: {e}")

    def _refresh_shared_chat_settings_async(self) -> None:
        debug_print("GUI", "Shared chat settings refreshed.")

    def _resync_slash_commands(self) -> None:
        if not messagebox.askyesno(
            "Resync Slash Commands",
            "This will unregister all slash commands across every guild before re-registering the current set. Continue?",
            parent=self,
        ):
            return

        bot = self._get_cached_reference("DiscordBot")
        if bot is None or not hasattr(bot, "refresh_slash_commands"):
            messagebox.showerror(
                "Resync Slash Commands",
                "The Discord bot is not running, so commands cannot be refreshed.",
                parent=self,
            )
            return

        def _worker() -> None:
            try:
                loop = getattr(bot.bot, "loop", None)
                if loop is None or not loop.is_running():
                    raise RuntimeError("Discord bot loop is not running.")
                future = asyncio.run_coroutine_threadsafe(bot.refresh_slash_commands(), loop)
                summary = future.result(timeout=180)
            except Exception as exc:
                self.after(
                    0,
                    lambda err=exc: messagebox.showerror(
                        "Resync Slash Commands",
                        f"Failed to refresh slash commands.\n{err}",
                        parent=self,
                    ),
                )
                return

            def _notify() -> None:
                registered = 0
                guilds = len(getattr(bot.bot, "guilds", []) or [])
                if isinstance(summary, dict):
                    registered = int(summary.get("global_registered", 0) or 0)
                    guilds = int(summary.get("guilds_processed", guilds) or guilds)
                messagebox.showinfo(
                    "Resync Slash Commands",
                    f"Slash commands successfully re-registered.\nGlobal commands: {registered}\nGuilds processed: {guilds}",
                    parent=self,
                )

            self.after(0, _notify)

        threading.Thread(target=_worker, name="SlashCommandResync", daemon=True).start()

    # ------------------------------------------------------------------
    # Background helpers / shutdown
    # ------------------------------------------------------------------
    def start_bot_background(self):
        debug_print("GUI", "Starting bot in background thread.")

        def run_bot():
            try:
                import discordbot

                print("Starting discord bot...\n")
                discordbot.main()
            except Exception as e:
                print(f"Bot thread exception: {e}\n")

        threading.Thread(target=run_bot, daemon=True).start()

    def _on_close(self):
        try:
            debug_print("GUI", "Scheduling async database pool close (non-blocking).")
            close_database_sync(wait=False)
        except Exception:
            pass
        self._shutdown_online_db_loop()
        if self._stdout_redirector is not None and self._stdout_redirector.original_stream is not None:
            sys.stdout = self._stdout_redirector.original_stream
        if self._stderr_redirector is not None and self._stderr_redirector.original_stream is not None:
            sys.stderr = self._stderr_redirector.original_stream
        self.destroy()


def main():
    app = DBEditor()
    try:
        app.after(1000, app.start_bot_background)
        debug_print("GUI", "DBEditor initialized.")
    except Exception:
        pass
    try:
        start_timer_manager_in_background()
    except Exception as e:
        print(f"Failed to start ResponseTimer in background: {e}")
    app.mainloop()


if __name__ == "__main__":
    main()
