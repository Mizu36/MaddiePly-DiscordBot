import asyncio
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence
import asyncpg
from dotenv import load_dotenv
from supabase import Client, create_client
from supabase.client import ClientOptions

from tools import debug_print, set_reference, get_reference, get_app_root

load_dotenv()


DEFAULT_DATABASE_URL = os.getenv("SUPABASE_DIRECT_POSTGRES_URL")

_CLIENT_INSTANCE = None
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_supabase_client() -> Client:
    """Return a cached Supabase client so repeated calls reuse HTTP pools."""

    global _CLIENT_INSTANCE
    if _CLIENT_INSTANCE is not None:
        return _CLIENT_INSTANCE

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY must be defined in the environment.")

    _CLIENT_INSTANCE = create_client(
        url,
        key,
        options=ClientOptions(
            postgrest_client_timeout=10,
            storage_client_timeout=10,
            schema="public",
        ),
    )
    return _CLIENT_INSTANCE

class OnlineDatabase:
    """Async helper for direct Postgres access via asyncpg."""
    def __init__(
        self,
        dsn: str | None = None,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 5,
        statement_cache_size: int = 100
    ) -> None:
        self._dsn = dsn or DEFAULT_DATABASE_URL
        set_reference("OnlineDatabase", self)
        if not self._dsn:
            raise RuntimeError(
                "SUPABASE_DIRECT_POSTGRES_URL must be provided (either in env or constructor)."
            )
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()
        self._pool_kwargs = {
            "min_size": min_pool_size,
            "max_size": max_pool_size,
            "statement_cache_size": statement_cache_size,
            "timeout": 10,
        }
        self.list_of_tables = ["users", "gacha", "user_gacha_pulls"]
        self.online_storage: OnlineStorage = get_reference("OnlineStorage")
        debug_print("OnlineDatabase", "Initialized OnlineDatabase with asyncpg connection pool.")

    async def close(self) -> None:
        """Close the underlying connection pool."""

        if self._pool:
            await self._pool.close()
            self._pool = None

    async def fetch_table(self, table: str, limit: int = 1000) -> list[dict[str, Any]]:
        """Fetch up to `limit` rows from a table."""
        debug_print("OnlineDatabase", f"Fetching up to {limit} rows from table '{table}'.")
        query = f"SELECT * FROM {self._ident(table)} LIMIT {limit}"
        rows = await self._run_fetch(query)
        return rows

    async def fetch_data(
        self,
        table: str,
        column_filter: str,
        return_columns: Sequence[str] | None = None,
        value: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch rows with an optional equality filter."""
        columns_sql = self._columns_clause(return_columns)
        query = f"SELECT {columns_sql} FROM {self._ident(table)}"
        params: list[Any] = []

        if value is not None:
            query += f" WHERE {self._ident(column_filter)} = $1"
            params.append(value)
        elif column_filter:
            query += f" ORDER BY {self._ident(column_filter)}"

        rows = await self._run_fetch(query, *params)
        return rows

    async def insert_data(self, table: str, data: dict[str, Any] | Iterable[dict[str, Any]]):
        """Insert one or many rows and return the inserted payloads."""
        debug_print("OnlineDatabase", f"Inserting data into table '{table}'.")
        rows = self._normalize_rows(data)
        if not rows:
            return []
        query, params = self._build_insert_query(table, rows)
        inserted = await self._run_fetch(query, *params)
        return inserted

    async def update_data(
        self,
        table: str,
        column_filter: str,
        value: Any,
        data: dict[str, Any],
    ):
        """Update rows where `column_filter` equals value."""
        debug_print("OnlineDatabase", f"Updating rows in table '{table}' where {column_filter}={value!r}.")
        if not data:
            raise ValueError("Update payload must include at least one column.")
        set_clauses = []
        params: list[Any] = []
        for idx, (column, column_value) in enumerate(data.items(), start=1):
            set_clauses.append(f"{self._ident(column)} = ${idx}")
            params.append(column_value)
        params.append(value)
        query = (
            f"UPDATE {self._ident(table)} SET {', '.join(set_clauses)} "
            f"WHERE {self._ident(column_filter)} = ${len(params)} RETURNING *"
        )
        updated = await self._run_fetch(query, *params)
        return updated

    async def upsert_data(
        self,
        table: str,
        data: dict[str, Any] | Iterable[dict[str, Any]],
        conflict_column: str = "id",
    ):
        """Perform an insert with ON CONFLICT...DO UPDATE semantics (default key: id)."""
        debug_print("OnlineDatabase", f"Upserting data into table '{table}' on conflict column '{conflict_column}'.")
        rows = self._normalize_rows(data)
        if not rows:
            return []
        if conflict_column not in rows[0]:
            raise ValueError(
                f"Conflict column '{conflict_column}' must exist in the payload to upsert."
            )
        columns = self._collect_columns(rows)
        insert_query, params = self._build_insert_query(table, rows, columns)
        update_assignments = [
            f"{self._ident(col)} = EXCLUDED.{self._ident(col)}"
            for col in columns
            if col != conflict_column
        ]
        if not update_assignments:
            update_assignments.append(
                f"{self._ident(conflict_column)} = EXCLUDED.{self._ident(conflict_column)}"
            )
        query = (
            f"{insert_query} ON CONFLICT ({self._ident(conflict_column)}) "
            f"DO UPDATE SET {', '.join(update_assignments)} RETURNING *"
        )
        upserted = await self._run_fetch(query, *params)
        return upserted
    
    async def handle_message_update(self, discord_user_id: str, discord_display_name: str) -> None:
        """Increments discord_number_of_messages by 1 and updates discord_display_name for the given discord_user_id."""
        debug_print("OnlineDatabase", f"Handling message update for discord_user_id '{discord_user_id}'.")
        await self.increment_column(
            table="users",
            column_filter="discord_id",
            value=discord_user_id,
            column_to_increment="discord_number_of_messages",
            increment_by=1,
        )
        await self.update_data(
            table="users",
            column_filter="discord_id",
            value=discord_user_id,
            data={"discord_display_name": discord_display_name},
        )

    async def delete_data(self, table: str, column_filter: str, value: Any):
        """Delete rows and return the deleted payloads."""
        debug_print("OnlineDatabase", f"Deleting rows from table '{table}' where {column_filter}={value!r}.")
        query = (
            f"DELETE FROM {self._ident(table)} WHERE {self._ident(column_filter)} = $1 RETURNING *"
        )
        deleted = await self._run_fetch(query, value)
        return deleted
    
    async def combine_rows(self, twitch_user_id: str, discord_user_id: str) -> None:
        """Checks if there are seperate rows for the given twitch and discord user IDs, and combines them into one row if so.
        Discord row gets deleted, and all data is merged into the Twitch row. None values are overwritten with non-None values.
        The check can result in multiple rows being found for the discord ID, but only the row without a twitch ID is merged and deleted.
        """
        debug_print("OnlineDatabase", f"Combining rows for twitch_user_id '{twitch_user_id}' and discord_user_id '{discord_user_id}'.")
        twitch_rows = await self.fetch_data("users", "twitch_id", value=twitch_user_id)
        discord_rows = await self.fetch_data("users", "discord_id", value=discord_user_id)
        if not twitch_rows or not discord_rows:
            debug_print("OnlineDatabase", f"No rows to combine for twitch_user_id '{twitch_user_id}' and discord_user_id '{discord_user_id}'.")
            return
        twitch_row = twitch_rows[0]
        for row in discord_rows:
            if row["id"] != twitch_row["id"]:
                discord_row = row
                break
        combined_data = {}
        for key in set(twitch_row.keys()).union(discord_row.keys()):
            twitch_value = twitch_row.get(key)
            discord_value = discord_row.get(key)
            combined_data[key] = twitch_value if twitch_value is not None else discord_value
        await self.update_data(
            "users",
            "id",
            twitch_row["id"],
            combined_data,
        )
        await self.delete_data(
            "users",
            "id",
            discord_row["id"],
        )
        debug_print("OnlineDatabase", f"Combined rows for twitch_user_id '{twitch_user_id}' and discord_user_id '{discord_user_id}'.")

    async def increment_column(self, table: str, column_filter: str, value: Any, column_to_increment: str, increment_by: int = 1):
        """Atomically increment a numeric column and return the affected rows."""
        debug_print("OnlineDatabase", f"Incrementing column '{column_to_increment}' by {increment_by} where {column_filter}={value!r} in table '{table}'.")

        query = (
            f"UPDATE {self._ident(table)} "
            f"SET {self._ident(column_to_increment)} = {self._ident(column_to_increment)} + $1 "
            f"WHERE {self._ident(column_filter)} = $2 RETURNING *"
        )
        rows = await self._run_fetch(query, increment_by, value)
        if not rows:
            debug_print("OnlineDatabase", f"Increment requested but no rows matched {column_filter}={value!r} in '{table}'.")
        else:
            debug_print("OnlineDatabase", f"Incremented column '{column_to_increment}' by {increment_by} for {len(rows)} row(s).")
    
    async def user_exists(self, discord_user_id: str) -> bool:
        """Check if a user exists by their Discord user ID."""
        debug_print("OnlineDatabase", f"Checking existence of user with discord_user_id '{discord_user_id}'.")
        rows = await self.fetch_data("users", "discord_id", value=discord_user_id)
        return len(rows) > 0
    
    async def get_specific_user_data(self, discord_user_id: str, field: str) -> Any:
        """Fetch a specific field for a user identified by their Discord user ID."""
        debug_print("OnlineDatabase", f"Fetching field '{field}' for user with discord_user_id '{discord_user_id}'.")
        rows = await self.fetch_data("users", "discord_id", return_columns=[field], value=discord_user_id)
        if rows and field in rows[0]:
            return rows[0][field]
        return None
    
    async def create_user(self, discord_user_id: str, data: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """
        Insert or update user data. Only parameters passed to this method will be updated. discord_user_id is required. 
        Columns include id, twitch_id, twitch_username, twitch_display_name, twitch_number_of_messages, bits_donated, 
        months_subscribed, subs_gifted, channel_points_redeemed, chime, tts_voice, discord_id, discord_username, discord_display_name, 
        discord_number_of_messages, discord_currency, discord_inventory, active_gacha_set, bits_toward_next_gacha_pull
        """
        debug_print(
            "OnlineDatabase",
            f"Creating or updating user with discord_user_id '{discord_user_id}'.",
        )
        payload: dict[str, Any] = {"discord_id": discord_user_id}
        if data:
            payload.update(data)
        if kwargs:
            payload.update(kwargs)

        try:
            inserted = await self.insert_data("users", payload)
            return inserted[0] if inserted else {}
        except asyncpg.UniqueViolationError:
            update_payload = {k: v for k, v in payload.items() if k != "discord_id"}
            if not update_payload:
                # Nothing new to write; return current row for convenience.
                return await self.get_user_data(discord_user_id)
            updated = await self.update_data(
                "users",
                "discord_id",
                discord_user_id,
                update_payload,
            )
            return updated[0] if updated else {}
    
    async def update_user_data(self, discord_user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Update user data for a user identified by their Discord user ID. Only parameters passed to this method will be updated."""
        debug_print("OnlineDatabase", f"Updating user data for discord_user_id '{discord_user_id}'.")
        if not data:
            raise ValueError("At least one field must be provided to update user data.")
        updated = await self.update_data(
            "users",
            "discord_id",
            discord_user_id,
            data,
        )
        return updated[0] if updated else {}
    
    async def get_user_data(self, discord_user_id: str) -> dict[str, Any] | None:
        """Fetch user data by Discord user ID."""
        debug_print("OnlineDatabase", f"Fetching user data for discord_user_id '{discord_user_id}'.")
        rows = await self.fetch_data("users", "discord_id", value=discord_user_id)
        return rows[0] if rows else None
    
    async def get_user_gacha_pulls(self, discord_user_id: str, gacha_id: str) -> int:
        """Fetches gacha pull records for a given twitch user id and gacha_id. Matches twitch_user_id to users.twitch_id.
        Should return only the number of pulls the user has made for that gacha. 
        Columns include id, user_id (foreign key of users.id), gacha_id (foreign key of gachas.id), is_shiny, pull_count
        """
        twitch_id = await self.get_specific_user_data(discord_user_id, "twitch_id")
        query = (
            f"SELECT ugp.* FROM user_gacha_pulls ugp "
            f"JOIN users u ON ugp.user_id = u.id "
            f"WHERE u.twitch_id = $1 AND ugp.gacha_id = $2"
        )
        rows = await self._run_fetch(query, twitch_id, gacha_id)
        debug_print(
            "OnlineDatabase",
            f"Fetched {len(rows)} gacha pull record(s) for discord_user_id '{discord_user_id}' and gacha_id '{gacha_id}'.",
        )
        return rows[0].get("pull_count", 0) if rows else 0
    
    async def get_user_gacha_pull_counts_for_set(self, discord_user_id: str, set_name: str) -> dict[int, int]:
        """Fetch pull counts for every gacha in a set for a specific user."""
        debug_print(
            "OnlineDatabase",
            f"Fetching pull counts for discord_user_id '{discord_user_id}' and set '{set_name}'.",
        )
        twitch_id = await self.get_specific_user_data(discord_user_id, "twitch_id")
        query = (
            f"SELECT ugp.gacha_id, ugp.pull_count FROM user_gacha_pulls ugp "
            f"JOIN users u ON ugp.user_id = u.id "
            f"JOIN gacha g ON ugp.gacha_id = g.id "
            f"WHERE u.twitch_id = $1 AND g.set_name = $2"
        )
        rows = await self._run_fetch(query, twitch_id, set_name)
        pull_map = {row["gacha_id"]: row.get("pull_count", 0) for row in rows}
        debug_print(
            "OnlineDatabase",
            f"Fetched pull counts for {len(pull_map)} gacha(s) in set '{set_name}' for discord_user_id '{discord_user_id}'.",
        )
        return pull_map
    
    async def get_all_gacha_data_by_set_name(self, set_name: str) -> list[dict[str, Any]] | None:
        """Fetch all gacha data in a certain set. Returns none if enabled column for first row is false."""
        debug_print("OnlineDatabase", f"Fetching all gacha data for set_name '{set_name}'.")
        rows = await self.fetch_data("gacha", "set_name", value=set_name)
        if rows and not rows[0].get("enabled", True):
            return None
        return rows if rows else None

    async def get_set_level_for_user(self, discord_user_id: str, set_name: str) -> int:
        """Calculates the gacha set level for a user based on their pulls in that set."""
        debug_print("OnlineDatabase", f"Calculating gacha set level for discord_user_id '{discord_user_id}' and set_name '{set_name}'.")
        twitch_user_id = await self.get_specific_user_data(discord_user_id, "twitch_id")
        query = (
            f"SELECT SUM(ugp.pull_count) AS total_pulls "
            f"FROM user_gacha_pulls ugp "
            f"JOIN users u ON ugp.user_id = u.id "
            f"JOIN gacha g ON ugp.gacha_id = g.id "
            f"WHERE u.twitch_id = $1 AND g.set_name = $2"
        )
        rows = await self._run_fetch(query, twitch_user_id, set_name)
        total_pulls = rows[0]["total_pulls"] if rows and rows[0]["total_pulls"] is not None else 0
        debug_print("OnlineDatabase", f"Gacha set level for discord_user_id '{discord_user_id}' and set_name '{set_name}' is {total_pulls}.")
        return total_pulls
    
    async def get_gacha_data_by_name(self, name: str) -> dict[str, Any] | None:
        """Fetch gacha data by gacha name."""
        debug_print("OnlineDatabase", f"Fetching gacha data for name '{name}'.")
        rows = await self.fetch_data("gacha", "name", value=name)
        return rows[0] if rows else None
    
    async def get_gacha_data_by_id(self, gacha_id: str) -> dict[str, Any] | None:
        """Fetch gacha data by gacha ID."""
        debug_print("OnlineDatabase", f"Fetching gacha data for gacha_id '{gacha_id}'.")
        rows = await self.fetch_data("gacha", "id", value=gacha_id)
        return rows[0] if rows else None
    
    async def get_all_gacha_data(self) -> list[dict[str, Any]]:
        """Fetch all gacha data."""
        debug_print("OnlineDatabase", "Fetching all gacha data.")
        rows = await self.fetch_data("gacha", "id")
        return rows
    
    async def get_all_gacha_sets(self) -> list[str]:
        """Fetch all distinct gacha set names."""
        debug_print("OnlineDatabase", "Fetching all distinct gacha set names.")
        query = "SELECT DISTINCT set_name FROM gacha"
        rows = await self._run_fetch(query)
        set_names = [row["set_name"] for row in rows]
        return set_names

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self._dsn, **self._pool_kwargs)
        return self._pool

    async def _run_fetch(self, query: str, *params: Any) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(query, *params)
        return [dict(row) for row in rows]

    @staticmethod
    def _collect_columns(rows: list[dict[str, Any]]) -> list[str]:
        columns: list[str] = []
        for row in rows:
            for column in row.keys():
                if column not in columns:
                    columns.append(column)
        if not columns:
            raise ValueError("Payload must contain at least one column.")
        return columns

    def _build_insert_query(
        self,
        table: str,
        rows: list[dict[str, Any]],
        columns: list[str] | None = None,
    ) -> tuple[str, list[Any]]:
        if columns is None:
            columns = self._collect_columns(rows)
        values_sql = []
        params: list[Any] = []
        param_index = 1
        for row in rows:
            placeholders = []
            for column in columns:
                placeholders.append(f"${param_index}")
                params.append(row.get(column))
                param_index += 1
            values_sql.append(f"({', '.join(placeholders)})")
        query = (
            f"INSERT INTO {self._ident(table)} ({', '.join(self._ident(col) for col in columns)}) "
            f"VALUES {', '.join(values_sql)} RETURNING *"
        )
        return query, params

    def _columns_clause(self, columns: Sequence[str] | None) -> str:
        if not columns:
            return "*"
        return ", ".join(self._ident(column) for column in columns)

    def _normalize_rows(
        self,
        data: dict[str, Any] | Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            return [data]
        rows = [row for row in data]
        return rows

    def _ident(self, name: str) -> str:
        parts = name.split(".")
        quoted_parts = [self._quote_part(part) for part in parts]
        return ".".join(quoted_parts)

    @staticmethod
    def _quote_part(part: str) -> str:
        if not _IDENTIFIER_RE.match(part):
            raise ValueError(f"Invalid SQL identifier: {part!r}")
        return f'"{part}"'



class OnlineStorage:
    """Convenience wrapper for Supabase Storage buckets."""
    def __init__(self, client = None) -> None:
        self.supabase: Client = client or get_supabase_client()
        set_reference("OnlineStorage", self)
        debug_print("OnlineStorage", "Initialized Supabase Storage client.")

    def _bucket(self, bucket: str):
        return self.supabase.storage.from_(bucket)

    def upload_file(self, bucket: str, destination_name: str, *, file_path: str | os.PathLike[str] | None = None, data: bytes | None = None, content_type: str | None = None, upsert: bool = False, ):
        """Upload bytes or a local file into the specified bucket/path."""

        if data is None and file_path is None:
            raise ValueError("Either 'data' or 'file_path' must be provided for upload.")
        payload = data if data is not None else Path(file_path).read_bytes()  # type: ignore[arg-type]
        options: dict[str, object] = {}
        if content_type:
            options["content-type"] = content_type
        if upsert:
            options["upsert"] = "true"
        response = self._bucket(bucket).upload(destination_name, payload, file_options=options or None)
        debug_print("OnlineStorage", f"Uploaded object '{destination_name}' to bucket '{bucket}' (bytes={len(payload)})")
        return response.path

    def download_file(self, bucket: str, source_name: str, *, destination_path: str | os.PathLike[str]) -> Path:
        """Download an object from storage and persist it locally."""

        data: bytes = self._bucket(bucket).download(source_name)
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        debug_print("OnlineStorage", f"Downloaded object '{source_name}' from bucket '{bucket}' -> {destination}"
        )
        return destination
    
    async def ensure_gacha_image(self, gacha_id: str, is_shiny: bool = False) -> str:
        """Ensure a gacha image exists locally and return the path.

        Layout: media/gacha/<set_name>/<rarity>/<gacha_name>.png. If the file already
        exists it is returned unchanged; otherwise it downloads the remote image into
        that exact path (renaming to match the gacha name).
        """
        debug_print("OnlineStorage", f"Ensuring gacha image for gacha_id '{gacha_id}' (is_shiny={is_shiny}).")
        online_database: OnlineDatabase | None = get_reference("OnlineDatabase")
        if online_database is None:
            raise RuntimeError("OnlineDatabase reference is not available.")
        gacha_data = await online_database.get_gacha_data_by_id(gacha_id)
        if not gacha_data:
            raise ValueError(f"Gacha with ID '{gacha_id}' does not exist in the database.")
        
        local_base = Path(get_app_root()) / "media" / "gacha" / "sets"
        name = gacha_data["name"]
        set_name = gacha_data["set_name"]
        rarity = gacha_data["rarity"]
        rarity_folder_map = {
            "N": "common",
            "R": "uncommon",
            "SR": "rare",
            "SSR": "epic",
            "UR": "legendary",
        }
        rarity_folder = rarity_folder_map.get(rarity)

        local_dir = local_base / set_name / rarity_folder
        shiny_dir = local_base / set_name / rarity_folder / "shiny"
        if is_shiny:
            local_path = shiny_dir / f"{name}.png"
        else:
            local_path = local_dir / f"{name}.png"
        if local_path.exists():
            return str(local_path)

        object_path = gacha_data["shiny_image_path"] if is_shiny else gacha_data["image_path"]
        if is_shiny and not object_path:
            debug_print("OnlineStorage", f"Gacha with ID '{gacha_id}' does not have a shiny image; falling back to normal image.")
            object_path = gacha_data["image_path"]
        if not object_path:
            raise ValueError(f"Gacha with ID '{gacha_id}' does not have an image URL set.")
        
        bucket_name = "gacha-images"
        local_dir.mkdir(parents=True, exist_ok=True)
        self.download_file(bucket=bucket_name, source_name=object_path, destination_path=local_path)
        return str(local_path)
