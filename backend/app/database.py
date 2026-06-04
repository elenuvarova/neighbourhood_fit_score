import os
from dotenv import load_dotenv
from sqlmodel import create_engine, Session

# Load .env before reading env vars (no-op in production where env is injected)
load_dotenv()

_url = os.getenv("DATABASE_URL", "")

# Render's Postgres URLs use postgres:// but SQLAlchemy 2 requires postgresql://
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

if _url.startswith("postgresql://"):
    db_kind = "postgres"
    # SSL is conditional. Managed Postgres that needs TLS (e.g. Supabase, Neon)
    # carries `sslmode=require` in the URL; honour it then. The Coolify-internal
    # Postgres speaks plaintext on the Docker network and FORCING sslmode=require
    # would crash-loop the app, so only enable SSL when the URL already asks for it.
    connect_args: dict = {}
    if "sslmode=require" in _url:
        connect_args["sslmode"] = "require"
    engine = create_engine(
        _url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
else:
    db_kind = "sqlite"
    _path = os.getenv("SQLITE_PATH", "./data.sqlite")
    engine = create_engine(f"sqlite:///{_path}", connect_args={"check_same_thread": False})


def get_session():
    with Session(engine) as session:
        yield session
