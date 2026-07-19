"""
db_dialect.py - Single place where every PostgreSQL/SQLite difference lives.

The whole application picks its dialect from DATABASE_URL and nothing else.
Point DATABASE_URL at Postgres and you get Postgres; point it at a SQLite file
(the default) and the app runs with no database server at all.

What this module owns:

  1. ``normalize_database_url`` - adds the right async driver and turns a
     relative SQLite path into an absolute one anchored at the repo root, so the
     backend (cwd=backend/) and the detection process (cwd=repo root) always
     open the *same* file.
  2. ``create_engine_from_url`` - engine construction plus the SQLite
     connect-time PRAGMAs (WAL, busy_timeout, foreign_keys). SQLite has foreign
     keys OFF by default and this schema relies on CASCADE/RESTRICT, so the
     PRAGMA is a correctness requirement, not a nicety.
  3. ``date_trunc`` - a dialect-dispatched SQL function. Postgres gets native
     ``date_trunc()``; SQLite gets the equivalent ``strftime()`` expression.
  4. The analytics views (``mv_*``) - plain views on both dialects, created at
     startup. See the note on materialized views below.

Materialized views
------------------
The original schema used PostgreSQL MATERIALIZED VIEWs refreshed on a timer by
APScheduler. SQLite has no such thing. Rather than keep two schemas we use PLAIN
VIEWS on *both* dialects: they are supported everywhere, they are always fresh
(no refresh job, no staleness window), and the view NAMES are unchanged so every
existing reader keeps working. The trade-off is that reads recompute the
aggregate instead of hitting a precomputed table - fine at this data scale, and
the scheduler's refresh job is now a no-op.
"""
from __future__ import annotations

import logging
import os
from datetime import timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    TypeDecorator,
    event,
    inspect,
    literal_column,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import GenericFunction

logger = logging.getLogger(__name__)

# backend/db_dialect.py -> backend/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Timezone-aware timestamps
# ---------------------------------------------------------------------------


class UtcDateTime(TypeDecorator):
    """A timestamp column that is always tz-aware UTC in Python, on any dialect.

    PostgreSQL has TIMESTAMP WITH TIME ZONE; SQLite has no timezone-aware type at
    all and hands back naive datetimes. Without this, code that compares a stored
    timestamp against ``datetime.now(timezone.utc)`` raises
    "can't compare offset-naive and offset-aware datetimes" on SQLite only.

    The contract, enforced in both directions:
      * going in  - naive values are assumed UTC, aware values are converted to
                    UTC; SQLite additionally stores them naive (it has nowhere to
                    put the offset, and a uniform format keeps its string
                    comparisons and strftime() truncation correct).
      * coming out - always a UTC-aware datetime.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

#: Used when DATABASE_URL is unset. Relative on purpose - it is resolved against
#: REPO_ROOT below, so it means the same file on every machine and from any cwd.
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./vcc.db"


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------


def resolve_database_url() -> str:
    """
    Decide which database this project talks to, ignoring unrelated ambient config.

    ``DATABASE_URL`` is a name many tools claim. A developer machine can easily have
    an exported ``DATABASE_URL`` belonging to a completely different project, and
    because ``load_dotenv()`` does not override variables that already exist, that
    foreign value silently wins over this repo's own ``.env``. The failure mode is
    not a crash -- it is this application running ``create_all`` against someone
    else's database and quietly creating its tables there.

    Precedence, most specific first:

    1. ``VCC_DATABASE_URL`` -- project-namespaced, cannot be claimed by accident.
       This is the variable to set for a real deployment.
    2. ``DATABASE_URL`` -- accepted for compatibility, but see the guard below.
    3. The built-in SQLite default.
    """
    explicit = os.getenv("VCC_DATABASE_URL")
    if explicit:
        return explicit

    # This repo's own .env outranks an ambient DATABASE_URL.
    #
    # Read from the file directly instead of relying on load_dotenv(), which by
    # design refuses to overwrite a variable already present in the environment.
    # A blanket load_dotenv(override=True) would fix this but would also stomp
    # deliberate command-line overrides of unrelated settings, so the override is
    # scoped to just this one setting.
    from_file = _project_env_database_url()
    if from_file:
        return from_file

    generic = os.getenv("DATABASE_URL")
    if generic:
        logger.warning(
            "Using DATABASE_URL inherited from the environment (%s). No "
            "DATABASE_URL was found in this project's .env. If that target is "
            "not this application's database, set VCC_DATABASE_URL instead.",
            describe_database_url(generic),
        )
        return generic

    return DEFAULT_DATABASE_URL


def _project_env_database_url() -> str | None:
    """DATABASE_URL as written in this repo's backend/.env, or None."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        from dotenv import dotenv_values
        return dotenv_values(env_path).get("DATABASE_URL") or None
    except Exception:
        return None


def describe_database_url(url: str) -> str:
    """Render a URL for logs with any password removed."""
    try:
        parsed = make_url(url)
        if parsed.password:
            parsed = parsed.set(password="***")
        return str(parsed)
    except Exception:
        return "<unparseable>"


def normalize_database_url(url: str | None = None) -> str:
    """Return an async-driver URL with SQLite paths resolved to absolute.

    Accepts every spelling that appears in this repo's configs and scripts::

        sqlite:///./vcc.db            -> sqlite+aiosqlite:////abs/path/vcc.db
        sqlite+aiosqlite:///vcc.db    -> sqlite+aiosqlite:////abs/path/vcc.db
        sqlite+aiosqlite:///:memory:  -> unchanged
        postgres://u:p@h/db           -> postgresql+asyncpg://u:p@h/db
        postgresql://u:p@h/db         -> postgresql+asyncpg://u:p@h/db
        postgresql+asyncpg://...      -> unchanged
    """
    raw = url or resolve_database_url()
    parsed = make_url(raw)

    # Split on the string rather than calling get_backend_name()/get_driver_name(),
    # which import the dialect plugin and would raise NoSuchModuleError on the
    # legacy "postgres://" spelling before we ever get to rewrite it.
    backend, _, driver = parsed.drivername.partition("+")

    if backend == "sqlite":
        if driver in ("", "pysqlite"):
            parsed = parsed.set(drivername="sqlite+aiosqlite")
        db = parsed.database
        # ":memory:" (and an empty database, which also means memory) stay as-is.
        if db and db != ":memory:":
            parsed = parsed.set(database=str((REPO_ROOT / db).resolve()))
        return parsed.render_as_string(hide_password=False)

    # Everything else is treated as PostgreSQL. "postgres://" is the legacy
    # libpq spelling that SQLAlchemy itself no longer accepts.
    if backend in ("postgres", "postgresql") and driver in ("", "psycopg2", "psycopg"):
        parsed = parsed.set(drivername="postgresql+asyncpg")
    return parsed.render_as_string(hide_password=False)


def is_sqlite(url: str) -> bool:
    return make_url(url).drivername.partition("+")[0] == "sqlite"


def sqlite_file_path(url: str) -> str | None:
    """Absolute on-disk path for a SQLite URL, or None (memory / not SQLite)."""
    parsed = make_url(url)
    if not is_sqlite(url):
        return None
    if not parsed.database or parsed.database == ":memory:":
        return None
    return parsed.database


# ---------------------------------------------------------------------------
# Engine construction + SQLite PRAGMAs
# ---------------------------------------------------------------------------


def install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Apply WAL / busy_timeout / foreign_keys on every new SQLite connection.

    Three processes write to this database (API :8000, training app :8002, the
    scheduler). WAL lets readers run while a writer holds the lock, and
    busy_timeout makes a contended writer wait instead of failing instantly with
    "database is locked". foreign_keys=ON is required for the schema's
    CASCADE/RESTRICT rules to be enforced at all.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def create_engine_from_url(url: str | None = None, **overrides: Any) -> AsyncEngine:
    """Build an AsyncEngine appropriate for whichever dialect the URL names."""
    resolved = normalize_database_url(url)

    kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
    if not is_sqlite(resolved):
        # Pool sizing only makes sense for a real server connection.
        kwargs.update(pool_size=10, max_overflow=20)
    kwargs.update(overrides)

    engine = create_async_engine(resolved, **kwargs)
    if is_sqlite(resolved):
        install_sqlite_pragmas(engine)
    return engine


# ---------------------------------------------------------------------------
# date_trunc()
# ---------------------------------------------------------------------------

#: strftime() formats that reproduce PostgreSQL's date_trunc() output.
#: Microseconds are always emitted so the rendered value is byte-identical to
#: SQLAlchemy's SQLite DATETIME bind format - string comparison against a bound
#: datetime would otherwise fail on exact boundaries.
_SQLITE_TRUNC_FORMATS = {
    "second": "%Y-%m-%d %H:%M:%S.000000",
    "minute": "%Y-%m-%d %H:%M:00.000000",
    "hour": "%Y-%m-%d %H:00:00.000000",
    "day": "%Y-%m-%d 00:00:00.000000",
    "month": "%Y-%m-01 00:00:00.000000",
    "year": "%Y-01-01 00:00:00.000000",
}


#: Whitelist. The unit is inlined into SQL (see below), so it must never be
#: attacker-controlled free text.
VALID_TRUNC_UNITS = frozenset(_SQLITE_TRUNC_FORMATS) | {"week"}


class date_trunc(GenericFunction):  # noqa: N801 - matches the SQL function name
    """Dialect-aware timestamp truncation: ``date_trunc('hour', events.timestamp)``.

    Renders natively on PostgreSQL and as ``strftime()`` on SQLite. Typed as
    UtcDateTime so SQLite's formatted result string is parsed back into a
    tz-aware datetime rather than handed to callers as a bare string.

    The unit is passed as a *literal* rather than a bind parameter, and that is
    load-bearing: SQLAlchemy's compiled-statement cache does not key on bind
    parameter values, so with a bound unit the SQL compiled for
    ``date_trunc('day', ...)`` would be silently reused for
    ``date_trunc('week', ...)`` - the truncation is baked into the SQL text by
    the compiler hooks below, not passed at execution time. As a literal it is
    part of the cache key, so each unit gets its own compilation. It is
    whitelisted in __init__ because it is inlined into the statement.
    """

    name = "date_trunc"
    type = UtcDateTime()
    inherit_cache = True

    def __init__(self, unit, expr, **kw):
        if not isinstance(unit, str) or unit.lower() not in VALID_TRUNC_UNITS:
            raise ValueError(
                "date_trunc() unit must be one of %s, got %r"
                % (", ".join(sorted(VALID_TRUNC_UNITS)), unit)
            )
        self.unit = unit.lower()
        super().__init__(literal_column("'%s'" % self.unit), expr, **kw)


@compiles(date_trunc)
def _compile_date_trunc_default(element, compiler, **kw):  # pragma: no cover - PG path
    """PostgreSQL (and anything else): emit the native function call."""
    return compiler.visit_function(element, **kw)


@compiles(date_trunc, "sqlite")
def _compile_date_trunc_sqlite(element, compiler, **kw):
    clauses = list(element.clauses)
    if len(clauses) != 2:
        raise ValueError("date_trunc() takes exactly (unit, timestamp)")

    unit = element.unit
    ts_sql = compiler.process(clauses[1], **kw)

    if unit == "week":
        # PostgreSQL date_trunc('week') is Monday-based. In SQLite, 'weekday 0'
        # moves forward to the next Sunday (staying put if already Sunday), so
        # stepping back 6 days lands on the Monday that starts the same week.
        return (
            "strftime('%s', %s, 'weekday 0', '-6 days')"
            % (_SQLITE_TRUNC_FORMATS["day"], ts_sql)
        )

    fmt = _SQLITE_TRUNC_FORMATS.get(unit)
    if fmt is None:
        raise ValueError("Unsupported date_trunc() unit on SQLite: %r" % (unit,))
    return "strftime('%s', %s)" % (fmt, ts_sql)


# ---------------------------------------------------------------------------
# Additive schema upgrades
# ---------------------------------------------------------------------------

#: Columns added to `cameras` after the initial schema shipped, in the order
#: they were introduced.
#:
#: This list exists because ``Base.metadata.create_all()`` creates *missing
#: tables* and nothing else - it will never add a column to a table that already
#: exists. Any database created before one of these columns landed (a long-lived
#: vcc.db, or a cached test database under the system temp directory) therefore
#: keeps the old shape and fails at the first INSERT with "table cameras has no
#: column named ...". Applying these ALTERs is what closes that gap.
#:
#: The third element is extra DDL appended after the type. It exists for
#: source_type, which is NOT NULL: a bare ADD COLUMN would leave every existing
#: row NULL and violate the constraint immediately, so the DEFAULT is what
#: backfills them as 'live'. Both dialects accept "NOT NULL DEFAULT <constant>".
#:
#: Types are SQLAlchemy types, not raw SQL strings, so each dialect renders its
#: own spelling - SQLite has no TIMESTAMP WITH TIME ZONE.
CAMERA_UPGRADE_COLUMNS: tuple[tuple[str, Any, str], ...] = (
    ("latitude", Float(), ""),
    ("longitude", Float(), ""),
    ("last_seen_at", UtcDateTime(), ""),
    ("counting_line", String(255), ""),
    # --- uploaded-video support ---
    ("source_type", String(16), "NOT NULL DEFAULT 'live'"),
    ("processing_status", String(16), ""),
    ("video_filename", String(255), ""),
    ("video_size_bytes", BigInteger(), ""),
    ("uploaded_at", UtcDateTime(), ""),
    ("processed_at", UtcDateTime(), ""),
)


async def apply_camera_upgrades(conn: AsyncConnection) -> None:
    """Add any CAMERA_UPGRADE_COLUMNS the `cameras` table is missing.

    Idempotent, and safe to run on every startup. We inspect first and add only
    what is genuinely absent because SQLite rejects ``ADD COLUMN IF NOT EXISTS``.

    Deliberately not wrapped in a warn-and-continue handler: schema shape is not
    optional. If this fails the application cannot serve requests correctly, and
    a silent warning would turn that into a confusing runtime error much later.
    """
    existing = {
        c["name"]
        for c in await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_columns("cameras"))
    }
    dialect = conn.engine.dialect

    for col_name, col_type, col_extra in CAMERA_UPGRADE_COLUMNS:
        if col_name in existing:
            continue
        ddl_type = col_type.compile(dialect=dialect)
        ddl = "ALTER TABLE cameras ADD COLUMN %s %s" % (col_name, ddl_type)
        if col_extra:
            ddl += " " + col_extra
        await conn.execute(text(ddl))
        logger.info("Added missing cameras.%s column (%s)", col_name, ddl_type)


# ---------------------------------------------------------------------------
# Analytics views
# ---------------------------------------------------------------------------

VIEW_NAMES = ("mv_hourly_counts", "mv_daily_totals", "mv_lane_counts")

#: Table objects for the views. These live on their own MetaData so that
#: Base.metadata.create_all() never tries to CREATE TABLE them. Selecting through
#: these instead of raw text() gives typed bind params and typed results, which
#: is what makes the same query work on both dialects.
view_metadata = MetaData()

mv_hourly_counts = Table(
    "mv_hourly_counts",
    view_metadata,
    Column("location_id", Integer),
    Column("vehicle_class", String(32)),
    Column("hour", UtcDateTime()),
    Column("total_count", Integer),
)

mv_daily_totals = Table(
    "mv_daily_totals",
    view_metadata,
    Column("vehicle_class", String(32)),
    Column("day", Date),
    Column("total_count", Integer),
)

mv_lane_counts = Table(
    "mv_lane_counts",
    view_metadata,
    Column("camera_id", Integer),
    Column("lane_id", Integer),
    Column("vehicle_class", String(32)),
    Column("total_count", Integer),
)


def _view_definitions(dialect: str) -> list[tuple[str, str]]:
    if dialect == "sqlite":
        hour_expr = "strftime('%s', timestamp)" % _SQLITE_TRUNC_FORMATS["hour"]
        day_expr = "date(timestamp)"
    else:
        hour_expr = "date_trunc('hour', timestamp)"
        day_expr = "CAST(date_trunc('day', timestamp) AS date)"

    return [
        (
            "mv_hourly_counts",
            "SELECT location_id, vehicle_class, {h} AS hour, COUNT(*) AS total_count "
            "FROM events GROUP BY location_id, vehicle_class, {h}".format(h=hour_expr),
        ),
        (
            "mv_daily_totals",
            "SELECT vehicle_class, {d} AS day, COUNT(*) AS total_count "
            "FROM events GROUP BY vehicle_class, {d}".format(d=day_expr),
        ),
        (
            "mv_lane_counts",
            "SELECT camera_id, lane_id, vehicle_class, COUNT(*) AS total_count "
            "FROM events GROUP BY camera_id, lane_id, vehicle_class",
        ),
    ]


async def create_analytics_views(conn: AsyncConnection) -> None:
    """(Re)create the mv_* views. Idempotent; safe to run on every startup.

    On PostgreSQL a pre-existing *materialized* view of the same name (created by
    the 0001 migration) is dropped first, because CREATE OR REPLACE VIEW cannot
    replace a matview.
    """
    dialect = conn.engine.dialect.name

    for name, body in _view_definitions(dialect):
        if dialect == "postgresql":
            relkind = (
                await conn.execute(
                    text(
                        "SELECT c.relkind FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relname = :n AND pg_catalog.pg_table_is_visible(c.oid)"
                    ),
                    {"n": name},
                )
            ).scalar()
            if relkind == "m":
                await conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS %s CASCADE" % name))
            elif relkind == "v":
                await conn.execute(text("DROP VIEW IF EXISTS %s CASCADE" % name))
        else:
            await conn.execute(text("DROP VIEW IF EXISTS %s" % name))

        await conn.execute(text("CREATE VIEW %s AS %s" % (name, body)))

    logger.info("Analytics views created (%s): %s", dialect, ", ".join(VIEW_NAMES))
