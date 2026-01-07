"""Sanity check script for Supabase connectivity.

Run this on any machine (ideally in a fresh virtual environment) after copying the
same .env file you ship with the executable. The script loads the SUPABASE_URL
and SUPABASE_SECRET_KEY environment variables, instantiates the Supabase client,
and attempts a simple Storage + PostgREST call so we can capture any TLS or auth
errors directly.
"""

from __future__ import annotations

import os
import sys
import textwrap
from typing import Any

from supabase import Client, create_client


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    try:
        url = _env("SUPABASE_URL")
        key = _env("SUPABASE_SECRET_KEY")
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        print("Ensure the .env (or environment variables) includes the Supabase credentials.")
        return 2

    print("[INFO] Creating Supabase client...")
    try:
        client: Client = create_client(url, key)
    except Exception as exc:  # noqa: BLE001 - we want the raw exception text
        print("[ERROR] Failed to create client:")
        print(exc)
        return 3

    # Try a simple Storage call (list buckets) to exercise HTTPS + auth.
    print("[INFO] Listing storage buckets...")
    try:
        buckets: list[dict[str, Any]] = client.storage.list_buckets()
    except Exception as exc:  # noqa: BLE001
        print("[ERROR] Storage bucket listing failed:")
        print(exc)
        return 4
    else:
        print(f"[OK] Retrieved {len(buckets)} bucket(s).")

    # Try a lightweight PostgREST ping via the rest client.
    print("[INFO] Running PostgREST health check (rpc or select limit 1)...")
    try:
        response = client.table("users").select("id").limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        print("[WARN] PostgREST select failed (this table may not exist):")
        print(exc)
    else:
        count = len(response.data or [])
        print(f"[OK] PostgREST responded with {count} row(s).")

    print(
        textwrap.dedent(
            """
            Done. If every step above printed [OK], Supabase connectivity works
            on this machine. Otherwise, share the specific error output so we can
            diagnose the TLS/auth issue.
            """
        ).strip()
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
