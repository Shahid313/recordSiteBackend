from sqlalchemy.orm import Session
from app.db.base import Base
from app.db.session import engine
from app.models.user import User
from app.core.security import get_password_hash
from app.db.session import SessionLocal
import os
from sqlalchemy import text

def init_db() -> None:
    """
    Initialize database - create tables
    """
    # Import models here to register them with Base (works for API process)
    import app.db.models  # noqa: F401
    
    _upgrade_schema(engine)
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created successfully!")

    # Ensure an admin user exists (superuser)
    try:
        db = SessionLocal()
        ensure_admin_user(db)
    finally:
        db.close()

def create_first_user(db: Session) -> None:
    """
    Create a test user (optional)
    """
    from app.models.user import User
    from app.core.security import get_password_hash
    
    user = db.query(User).filter(User.email == "admin@example.com").first()
    if not user:
        user = User(
            email="admin@example.com",
            hashed_password=get_password_hash("admin123"),
            full_name="Admin User",
            is_active=True
        )
        db.add(user)
        db.commit()
        print("✅ Test user created: admin@example.com / admin123")


def ensure_admin_user(db: Session) -> None:
    """
    Create an initial admin user if it doesn't exist.

    Configure via env:
      ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_FULL_NAME

    Defaults (dev):
      admin@example.com / admin123
    """
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    admin_full_name = os.getenv("ADMIN_FULL_NAME", "Admin User")

    user = db.query(User).filter(User.email == admin_email).first()
    if user:
        # Ensure superuser flag is set (non-destructive to password)
        if not user.is_superuser:
            user.is_superuser = True
            db.commit()
        return

    user = User(
        email=admin_email,
        hashed_password=get_password_hash(admin_password),
        full_name=admin_full_name,
        is_active=True,
        is_superuser=True,
    )
    db.add(user)
    db.commit()
    print(f"✅ Admin user created: {admin_email} / {admin_password}")


def _upgrade_schema(engine) -> None:
    """
    Lightweight schema upgrade for environments without Alembic.

    - Adds Phase 2 columns to `panoramas` table if missing
    - Adds transform columns to `floorplans` table if missing
    - Safe for repeated runs
    """
    dialect = engine.dialect.name
    if dialect == "postgresql":
        stmts = [
            "ALTER TABLE panoramas ADD COLUMN IF NOT EXISTS position_x DOUBLE PRECISION",
            "ALTER TABLE panoramas ADD COLUMN IF NOT EXISTS position_y DOUBLE PRECISION",
            "ALTER TABLE panoramas ADD COLUMN IF NOT EXISTS orientation DOUBLE PRECISION",
            "ALTER TABLE panoramas ADD COLUMN IF NOT EXISTS floor_id INTEGER REFERENCES floorplans(id) ON DELETE SET NULL",
            "ALTER TABLE panoramas ADD COLUMN IF NOT EXISTS is_transition BOOLEAN DEFAULT FALSE NOT NULL",
            "ALTER TABLE panoramas ADD COLUMN IF NOT EXISTS transition_floor_id INTEGER REFERENCES floorplans(id) ON DELETE SET NULL",
            "ALTER TABLE panoramas ADD COLUMN IF NOT EXISTS transition_pano_id INTEGER REFERENCES panoramas(id) ON DELETE SET NULL",
            "ALTER TABLE edit_history ALTER COLUMN undone TYPE BOOLEAN USING (undone::boolean)",
            # Floorplans: replace old JSON transform_matrix with individual columns
            "ALTER TABLE floorplans ADD COLUMN IF NOT EXISTS transform_scale DOUBLE PRECISION DEFAULT 1.0 NOT NULL",
            "ALTER TABLE floorplans ADD COLUMN IF NOT EXISTS transform_rotation DOUBLE PRECISION DEFAULT 0.0 NOT NULL",
            "ALTER TABLE floorplans ADD COLUMN IF NOT EXISTS transform_offset_x DOUBLE PRECISION DEFAULT 0.0 NOT NULL",
            "ALTER TABLE floorplans ADD COLUMN IF NOT EXISTS transform_offset_y DOUBLE PRECISION DEFAULT 0.0 NOT NULL",
            "ALTER TABLE floorplans DROP COLUMN IF EXISTS transform_matrix",
        ]
    elif dialect == "sqlite":
        # SQLite supports ADD COLUMN but not IF NOT EXISTS reliably across versions;
        # best-effort with try/except.
        stmts = [
            "ALTER TABLE panoramas ADD COLUMN position_x REAL",
            "ALTER TABLE panoramas ADD COLUMN position_y REAL",
            "ALTER TABLE panoramas ADD COLUMN orientation REAL",
            "ALTER TABLE panoramas ADD COLUMN floor_id INTEGER",
            "ALTER TABLE panoramas ADD COLUMN is_transition INTEGER DEFAULT 0",
            "ALTER TABLE panoramas ADD COLUMN transition_floor_id INTEGER",
            "ALTER TABLE panoramas ADD COLUMN transition_pano_id INTEGER",
            "ALTER TABLE floorplans ADD COLUMN transform_scale REAL DEFAULT 1.0",
            "ALTER TABLE floorplans ADD COLUMN transform_rotation REAL DEFAULT 0.0",
            "ALTER TABLE floorplans ADD COLUMN transform_offset_x REAL DEFAULT 0.0",
            "ALTER TABLE floorplans ADD COLUMN transform_offset_y REAL DEFAULT 0.0",
        ]
    else:
        # Unknown dialect: rely on create_all for new tables; skip column upgrades.
        return

    with engine.begin() as conn:
        for sql in stmts:
            try:
                conn.execute(text(sql))
            except Exception:
                # Likely "duplicate column" on SQLite or unsupported; ignore.
                pass