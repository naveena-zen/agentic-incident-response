"""
database.py — SQLAlchemy async ORM for Vigil (two-phase architecture).

Tables
------
  services        : one row per monitored service; carries the investigation lock flag
  service_metrics : time-series metric snapshots (cpu, mem, latency, error_rate)
  service_logs    : log lines per service
  recent_deploys  : fake deploy history for simulated services
  incidents       : every investigation, with full phase-1 trace + phase-2 decision
  past_incidents  : RAG knowledge-base (pgvector embeddings)

Key design notes
----------------
• services.investigation_in_progress is the distributed lock that prevents duplicate
  investigations for the same service.  policy_engine.decide() always releases it in
  a try/finally block regardless of success or failure.
• incidents.status is a Python Enum: investigating | resolved_auto | paged | approved
• pgvector HNSW index on past_incidents.embedding for fast cosine similarity search.
"""

from __future__ import annotations

import enum
import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

# ── Connection ─────────────────────────────────────────────────────────────────
_HOST = os.getenv("DB_HOST", "localhost")
_PORT = os.getenv("DB_PORT", "5432")
_NAME = os.getenv("DB_NAME", "vigil")
_USER = os.getenv("DB_USER", "postgres")
_PASS = os.getenv("DB_PASSWORD")
if not _PASS:
    raise RuntimeError("DB_PASSWORD environment variable is required")

DATABASE_URL      = f"postgresql+asyncpg://{_USER}:{_PASS}@{_HOST}:{_PORT}/{_NAME}"
DATABASE_URL_SYNC = f"postgresql+psycopg2://{_USER}:{_PASS}@{_HOST}:{_PORT}/{_NAME}"

engine           = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── Base ───────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Incident status enum ───────────────────────────────────────────────────────
class IncidentStatus(str, enum.Enum):
    investigating  = "investigating"
    resolved_auto  = "resolved_auto"
    paged          = "paged"
    approved       = "approved"


# ══════════════════════════════════════════════════════════════════════════════
# 1.  services  — one row per monitored service, carries the investigation lock
# ══════════════════════════════════════════════════════════════════════════════
class Service(Base):
    """
    investigation_in_progress = True  means Phase 1 is actively running
    (or policy_engine.decide() has not yet finished) for this service.

    The rule engine checks this flag BEFORE creating a new incident.
    policy_engine.decide() ALWAYS resets it to False in a try/finally block.
    """
    __tablename__ = "services"

    name                   = Column(String(64),  primary_key=True)
    is_simulated           = Column(Boolean,     nullable=False, default=True)

    # Latest metrics snapshot (updated every metrics tick)
    cpu_pct                = Column(Float,  nullable=True)
    memory_pct             = Column(Float,  nullable=True)
    latency_ms             = Column(Float,  nullable=True)
    error_rate             = Column(Float,  nullable=True)
    request_rate           = Column(Float,  nullable=True)

    # Investigation lock
    investigation_in_progress = Column(Boolean, nullable=False, default=False)
    last_investigation_id     = Column(UUID(as_uuid=True), nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ══════════════════════════════════════════════════════════════════════════════
# 2.  service_metrics  — historical time-series for charts
# ══════════════════════════════════════════════════════════════════════════════
class ServiceMetric(Base):
    __tablename__ = "service_metrics"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service      = Column(String(64), nullable=False, index=True)
    timestamp    = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    cpu_pct      = Column(Float,   nullable=False, default=0.0)
    memory_pct   = Column(Float,   nullable=False, default=0.0)
    latency_ms   = Column(Float,   nullable=True)
    error_rate   = Column(Float,   nullable=True)
    request_rate = Column(Float,   nullable=True)
    is_anomaly   = Column(Boolean, nullable=False, default=False, index=True)


# ══════════════════════════════════════════════════════════════════════════════
# 3.  service_logs  — log lines
# ══════════════════════════════════════════════════════════════════════════════
class ServiceLog(Base):
    __tablename__ = "service_logs"

    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service            = Column(String(64),  nullable=False, index=True)
    timestamp          = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    level              = Column(String(16),  nullable=False, default="INFO")
    message            = Column(Text,        nullable=False)
    is_anomaly_related = Column(Boolean,     nullable=False, default=False, index=True)


# ══════════════════════════════════════════════════════════════════════════════
# 4.  recent_deploys
# ══════════════════════════════════════════════════════════════════════════════
class RecentDeploy(Base):
    __tablename__ = "recent_deploys"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service     = Column(String(64),  nullable=False, index=True)
    version     = Column(String(32),  nullable=False)
    deployed_at = Column(DateTime(timezone=True), nullable=False)
    deployed_by = Column(String(64),  nullable=False, default="ci-bot")
    status      = Column(String(32),  nullable=False, default="success")
    notes       = Column(Text,        nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# 5.  incidents  — full investigation record
# ══════════════════════════════════════════════════════════════════════════════
class Incident(Base):
    """
    Lifecycle timestamps:
      created_at     — row inserted, investigation_in_progress set True
      phase1_completed_at — LLM returned terminal JSON
      decided_at     — policy_engine.decide() ran and set final status
      approved_at    — human clicked Approve (status: paged -> approved)
    """
    __tablename__ = "incidents"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service = Column(String(64),  nullable=False, index=True)

    # Status enum (the single source of truth for what happened)
    status  = Column(
        SAEnum(IncidentStatus, name="incidentstatus"),
        nullable=False,
        default=IncidentStatus.investigating,
        index=True,
    )

    # Phase 1 output (LLM)
    root_cause_hypothesis        = Column(Text,    nullable=True)
    confidence                   = Column(Float,   nullable=True)   # 0-100
    reasoning                    = Column(Text,    nullable=True)
    referenced_similar_incident  = Column(Text,    nullable=True)   # title of top RAG hit
    recommended_action           = Column(String(64),  nullable=True) # recommended by Phase 1 LLM
    phase1_tool_call_trace       = Column(Text,    nullable=True)   # JSON list

    # Phase 2 output (policy_engine, deterministic Python)
    action_taken       = Column(String(64),  nullable=True)   # restart | rollback | page_human
    action_detail      = Column(Text,        nullable=True)   # reason / SMTP result
    similar_incidents  = Column(Text,        nullable=True)   # JSON top-3 RAG hits

    # Human approval (paged → approved path)
    approved_by = Column(String(64),  nullable=True)

    # Timestamps for each status transition
    created_at           = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    phase1_completed_at  = Column(DateTime(timezone=True), nullable=True)
    decided_at           = Column(DateTime(timezone=True), nullable=True)
    approved_at          = Column(DateTime(timezone=True), nullable=True)


# ══════════════════════════════════════════════════════════════════════════════
# 6.  past_incidents  — RAG knowledge-base with pgvector
# ══════════════════════════════════════════════════════════════════════════════
class PastIncident(Base):
    __tablename__ = "past_incidents"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title            = Column(String(256), nullable=False)
    root_cause       = Column(Text,        nullable=False)
    action_taken     = Column(Text,        nullable=False)
    resolution_notes = Column(Text,        nullable=True)
    service          = Column(String(64),  nullable=True)
    occurred_at      = Column(DateTime(timezone=True), nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    # 384-dim vector from all-MiniLM-L6-v2
    embedding = Column(Vector(384), nullable=True)


# ── Bootstrap ──────────────────────────────────────────────────────────────────
async def create_all_tables() -> None:
    """
    Idempotent startup helper.
    1. Enables pgvector extension.
    2. Creates all tables (CREATE TABLE IF NOT EXISTS semantics via SQLAlchemy).
    3. Creates HNSW cosine index on past_incidents.embedding.
    """
    # Fail startup if required env secrets are missing
    for var in ["GROQ_API_KEY", "JWT_SECRET", "DEMO_PASSWORD", "DB_PASSWORD"]:
        if not os.getenv(var):
            raise RuntimeError(f"{var} environment variable is required")

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_past_incidents_embedding
            ON past_incidents
            USING hnsw (embedding vector_cosine_ops)
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_service_metrics_is_anomaly
            ON service_metrics (is_anomaly)
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_service_logs_is_anomaly_related
            ON service_logs (is_anomaly_related)
        """))
