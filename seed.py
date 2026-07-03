"""
seed.py — Seeds past_incidents with 10 realistic historical incidents (with embeddings)
and populates the services + recent_deploys tables.

Run standalone:  python seed.py
Or called from main.py lifespan on startup (idempotent).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sentence_transformers import SentenceTransformer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import (
    DATABASE_URL, PastIncident, RecentDeploy, Service,
    create_all_tables,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

_MODEL_NAME = "all-MiniLM-L6-v2"
_embedding_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading embedding model: %s", _MODEL_NAME)
        _embedding_model = SentenceTransformer(_MODEL_NAME)
    return _embedding_model


def embed(text_input: str) -> list[float]:
    return get_embedding_model().encode(text_input, normalize_embeddings=True).tolist()


# ── Past incidents seed data ───────────────────────────────────────────────────
_PAST_INCIDENTS = [
    {
        "title": "checkout-api connection pool exhausted during Black Friday",
        "root_cause": "Traffic spike 8x normal caused all 20 DB connection-pool slots to be consumed. New requests queued indefinitely and timed out.",
        "action_taken": "Restarted checkout-api; increased pool max to 80; added pgBouncer.",
        "resolution_notes": "Alert threshold lowered from 90% pool usage to 70%. Root cause: missing connection timeout + pool not scaled for peak.",
        "service": "checkout-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=45),
    },
    {
        "title": "auth-api OOM killed — JWT library memory leak",
        "root_cause": "JWT library v2.1.0 had an unbounded key-cache memory leak; container OOM-killed after ~6h of operation.",
        "action_taken": "Rolled back auth-api to v1.8.2 (previous stable). Vendor patched in v2.1.3.",
        "resolution_notes": "Added container memory limit alert at 80%. Scheduled weekly canary deploys with memory profiling.",
        "service": "auth-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=30),
    },
    {
        "title": "payments-api elevated error rate after SDK version bump",
        "root_cause": "Minor version upgrade of payments SDK introduced breaking change in HMAC signature algorithm; 100% of requests returned HTTP 403.",
        "action_taken": "Rolled back payments-api to v3.4.1.",
        "resolution_notes": "Enforced pin-exact dependency versions. Added integration smoke test for payment flow in CI.",
        "service": "payments-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=21),
    },
    {
        "title": "checkout-api high latency — missing DB index after migration",
        "root_cause": "Schema migration removed index on SKU column. Full table scans caused P99 latency > 8 s; checkout timed out on inventory checks.",
        "action_taken": "Added index with CREATE INDEX CONCURRENTLY. Restarted checkout-api to clear backlog.",
        "resolution_notes": "Schema migrations now require index-presence verification in staging before production rollout.",
        "service": "checkout-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=18),
    },
    {
        "title": "auth-api high CPU — bcrypt work-factor misconfiguration",
        "root_cause": "Config change accidentally set bcrypt cost factor to 14 (was 10). Each login took ~4 s CPU; 100% saturation under normal load.",
        "action_taken": "Reverted bcrypt cost factor to 10; restarted auth-api.",
        "resolution_notes": "Config changes to security parameters now require security-team PR review. Added CPU alert at 85%.",
        "service": "auth-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=14),
    },
    {
        "title": "payments-api intermittent failures — external gateway rate limiting",
        "root_cause": "Payment gateway enforces 100 req/s per API key. Batch job + peak traffic pushed to 140 req/s → HTTP 429 → 22% error rate.",
        "action_taken": "Batch job rescheduled to 3 AM. Implemented token-bucket rate limiter client-side.",
        "resolution_notes": "No restart needed. Requested higher rate-limit tier from vendor.",
        "service": "payments-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=10),
    },
    {
        "title": "local-host CPU spike — runaway Python process during log rotation",
        "root_cause": "Log rotation cron script spawned a new grep process per log line instead of once; 4,000+ grep processes pinned all CPU cores for 12 min.",
        "action_taken": "Killed runaway processes; fixed cron script.",
        "resolution_notes": "Added process-count alert (>200 processes = warning). Log rotation script now tested in CI.",
        "service": "local-host",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=8),
    },
    {
        "title": "checkout-api memory leak — debug list never cleared",
        "root_cause": "Custom session middleware held strong references to request objects in a class-level list for debugging; heap grew 50 MB/hour → OOM after 16 h.",
        "action_taken": "Restarted checkout-api; removed debug list in hotfix v2.9.1.",
        "resolution_notes": "Memory profiling (tracemalloc) added to nightly CI. Debug instrumentation must be behind a feature flag.",
        "service": "checkout-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=5),
    },
    {
        "title": "auth-api complete outage — TLS certificate expiry",
        "root_cause": "TLS certificate for auth.internal expired; service refused all inbound connections. Alert sent to archived distribution list.",
        "action_taken": "Renewed certificate; restarted auth-api. Updated alert recipient list.",
        "resolution_notes": "Certificate rotation automated via cert-manager auto-renew. Alert routing audited quarterly.",
        "service": "auth-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=3),
    },
    {
        "title": "payments-api stale payment status — DB replica lag",
        "root_cause": "Long-running analytics query on read replica caused 45 s replication lag; customers saw 'pending' for confirmed payments.",
        "action_taken": "Killed analytics query; replica caught up in 3 min. Paged DBA team.",
        "resolution_notes": "Analytics workloads moved to dedicated replica. Replication lag alert added at 5 s threshold.",
        "service": "payments-api",
        "occurred_at": datetime.now(timezone.utc) - timedelta(days=1),
    },
]

# ── Service rows (one per monitored service) ───────────────────────────────────
_SERVICES = [
    {"name": "local-host",    "is_simulated": False},
    {"name": "checkout-api",  "is_simulated": True},
    {"name": "auth-api",      "is_simulated": True},
    {"name": "payments-api",  "is_simulated": True},
]

# ── Deploy history ─────────────────────────────────────────────────────────────
def _deploy_history():
    now = datetime.now(timezone.utc)
    return [
        RecentDeploy(service="checkout-api", version="v2.8.0", deployed_at=now - timedelta(days=7),  deployed_by="ci-bot", status="success", notes="Added cart persistence"),
        RecentDeploy(service="checkout-api", version="v2.8.1", deployed_at=now - timedelta(days=4),  deployed_by="alice",  status="success", notes="Fix: session cleanup on timeout"),
        RecentDeploy(service="checkout-api", version="v2.9.0", deployed_at=now - timedelta(hours=18),deployed_by="ci-bot", status="success", notes="Upgrade DB driver"),
        RecentDeploy(service="auth-api",     version="v1.9.0", deployed_at=now - timedelta(days=6),  deployed_by="ci-bot", status="success", notes="Improved token refresh"),
        RecentDeploy(service="auth-api",     version="v1.9.1", deployed_at=now - timedelta(days=3),  deployed_by="bob",    status="success", notes="Security: upgrade crypto lib"),
        RecentDeploy(service="auth-api",     version="v2.0.0", deployed_at=now - timedelta(hours=6), deployed_by="ci-bot", status="success", notes="Switch to RS256 JWT signing"),
        RecentDeploy(service="payments-api", version="v3.4.0", deployed_at=now - timedelta(days=5),  deployed_by="ci-bot", status="success", notes="New payment methods"),
        RecentDeploy(service="payments-api", version="v3.4.1", deployed_at=now - timedelta(days=2),  deployed_by="carol",  status="success", notes="Fix: decimal precision"),
        RecentDeploy(service="payments-api", version="v3.5.0", deployed_at=now - timedelta(hours=2), deployed_by="ci-bot", status="success", notes="Upgrade payments SDK v4.0"),
    ]


async def seed_all(force: bool = False) -> None:
    """
    Idempotent seed. Skips if past_incidents already has rows (unless force=True).
    Always upserts service rows.
    """
    eng = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    await create_all_tables()

    async with Session() as session:
        # Upsert service rows
        for svc in _SERVICES:
            existing = await session.get(Service, svc["name"])
            if not existing:
                session.add(Service(**svc))
        await session.commit()

        # Seed deploy history if empty
        result = await session.execute(select(RecentDeploy).limit(1))
        if not result.scalar_one_or_none():
            for d in _deploy_history():
                session.add(d)
            await session.commit()
            logger.info("Seeded deploy history")

        # Seed past incidents if empty
        result = await session.execute(select(PastIncident).limit(1))
        if result.scalar_one_or_none() and not force:
            logger.info("past_incidents already seeded — skipping")
            await eng.dispose()
            return

    model = get_embedding_model()

    async with Session() as session:
        for rec in _PAST_INCIDENTS:
            query_text = f"{rec['title']}. {rec['root_cause']}"
            vec = model.encode(query_text, normalize_embeddings=True).tolist()
            session.add(PastIncident(
                title=rec["title"],
                root_cause=rec["root_cause"],
                action_taken=rec["action_taken"],
                resolution_notes=rec.get("resolution_notes"),
                service=rec.get("service"),
                occurred_at=rec.get("occurred_at"),
                embedding=vec,
            ))
        await session.commit()
    logger.info("Seeded %d past incidents with embeddings", len(_PAST_INCIDENTS))
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(seed_all())
