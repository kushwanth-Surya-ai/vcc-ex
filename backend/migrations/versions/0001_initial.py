"""
migrations/versions/0001_initial.py - Initial VCC schema migration.
"""
from __future__ import annotations
import os, sys
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _bcrypt_hash(plain: str) -> str:
    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(plain)


def upgrade() -> None:
    bind = op.get_bind()

    # locations
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
    )

    # cameras
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location_id", sa.Integer(),
                  sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lane_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rtsp_url", sa.String(1024), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="inactive"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
    )

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # events
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Integer(),
                  sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.Integer(),
                  sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lane_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_class", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("crossing_dir", sa.String(8), nullable=False, server_default="in"),
        sa.Column("timestamp", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_events_location_timestamp", "events", ["location_id", "timestamp"])
    op.create_index("ix_events_vehicle_class_timestamp", "events", ["vehicle_class", "timestamp"])
    op.create_index("ix_events_camera_timestamp", "events", ["camera_id", "timestamp"])

    # alerts
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Integer(),
                  sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Materialized views
    op.execute("""
        CREATE MATERIALIZED VIEW mv_hourly_counts AS
        SELECT location_id, vehicle_class,
               date_trunc('hour', timestamp) AS hour,
               COUNT(*) AS total_count
        FROM events
        GROUP BY location_id, vehicle_class, date_trunc('hour', timestamp)
        WITH NO DATA
    """)
    op.execute("""
        CREATE MATERIALIZED VIEW mv_daily_totals AS
        SELECT vehicle_class,
               date_trunc('day', timestamp)::date AS day,
               COUNT(*) AS total_count
        FROM events
        GROUP BY vehicle_class, date_trunc('day', timestamp)::date
        WITH NO DATA
    """)
    op.execute("""
        CREATE MATERIALIZED VIEW mv_lane_counts AS
        SELECT camera_id, lane_id, vehicle_class,
               COUNT(*) AS total_count
        FROM events
        GROUP BY camera_id, lane_id, vehicle_class
        WITH NO DATA
    """)

    # UNIQUE indexes on materialized views (required for CONCURRENT refresh)
    op.execute("CREATE UNIQUE INDEX uix_mv_hourly_counts ON mv_hourly_counts (location_id, vehicle_class, hour)")
    op.execute("CREATE UNIQUE INDEX uix_mv_daily_totals ON mv_daily_totals (vehicle_class, day)")
    op.execute("CREATE UNIQUE INDEX uix_mv_lane_counts ON mv_lane_counts (camera_id, lane_id, vehicle_class)")

    # Seed admin user
    hashed_pw = _bcrypt_hash("Admin1234!")
    bind.execute(
        sa.text(
            "INSERT INTO users (email, hashed_password, role) "
            "VALUES (:email, :pw, 'admin') ON CONFLICT (email) DO NOTHING"
        ).bindparams(email="admin@vcc.local", pw=hashed_pw)
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_lane_counts")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_daily_totals")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_hourly_counts")
    op.drop_table("alerts")
    op.drop_table("events")
    op.drop_table("users")
    op.drop_table("cameras")
    op.drop_table("locations")
