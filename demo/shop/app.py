"""The demo shop: a small FastAPI app over one Postgres table.

shop-proxy forwards it on port 8080. /freeze is a debug hook the proxy does not forward, so only
something inside the Compose network can call it. With DEMO_INJECT=1 it also mounts the injection
tests' hook (inject.py), which the proxy does not forward either.
"""

import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s shop: %(message)s")
log = logging.getLogger("shop")

VERSION = os.environ.get("SHOP_VERSION", "1")
UPLOADS = Path(os.environ.get("UPLOADS_DIR", "/uploads"))
# What a health check writes to the uploads volume, to prove the app can still store an upload.
PROBE_BYTES = 64 * 1024
# Version 2 keeps prices in whole cents, in a column its database migration adds.
PRICE_COLUMN = {"1": "price", "2": "price_cents"}
SEED = [("Coffee beans", 14.50), ("Mug", 9.00), ("Grinder", 69.00)]


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3, autocommit=True)


def prepare_database() -> None:
    with connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            " id serial PRIMARY KEY, name text NOT NULL, price numeric(8, 2) NOT NULL)"
        )
        if conn.execute("SELECT count(*) FROM items").fetchone()[0] == 0:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO items (name, price) VALUES (%s, %s)", SEED)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not os.environ.get("DATABASE_URL"):
        log.error("DATABASE_URL is not set; cannot start")
        raise SystemExit(1)
    try:
        prepare_database()
    except psycopg.Error as error:
        log.error("cannot reach the database, exiting: %s", error)
        raise SystemExit(1) from error
    log.info("shop version %s started, uploads in %s", VERSION, UPLOADS)
    yield


app = FastAPI(lifespan=lifespan)

if os.environ.get("DEMO_INJECT") == "1":
    from inject import install

    install(app)


@app.get("/health")
def health() -> JSONResponse:
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as error:
        log.error("health check: database: %s", error)
        return JSONResponse({"status": "error", "error": "database unreachable"}, status_code=503)
    try:
        with tempfile.NamedTemporaryFile(dir=UPLOADS, prefix=".probe-") as probe:
            probe.write(b"\0" * PROBE_BYTES)
            probe.flush()
    except OSError as error:
        log.error("health check: uploads: %s", error)
        return JSONResponse({"status": "error", "error": "uploads not writable"}, status_code=503)
    return JSONResponse({"status": "ok", "version": VERSION})


@app.get("/api/items")
def items() -> list[dict]:
    column = PRICE_COLUMN.get(VERSION, "price")
    with connect() as conn:
        rows = conn.execute(f"SELECT id, name, {column} FROM items ORDER BY id").fetchall()
    return [{"id": i, "name": name, "price": float(price)} for i, name, price in rows]


@app.post("/freeze")
async def freeze() -> None:
    """Debug hook: block the event loop, so every request after this one hangs."""
    log.warning("freeze requested: the event loop is blocked from now on")
    while True:
        time.sleep(3600)
