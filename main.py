"""
main.py — Vigil FastAPI application.

Startup sequence:
  1. Create DB tables (pgvector extension, all tables, HNSW index).
  2. Seed services, past_incidents (with embeddings), deploy history.
  3. Start metrics + anomaly injector loops via APScheduler / asyncio task.
  4. Register rule engine callback → Phase1 → Phase2 pipeline.

Phase flow per anomaly:
  rule_engine (pure Python) → Phase 1 (LLM read-only) → policy_engine.decide() (pure Python)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as StarletteResponse

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from database import (
    AsyncSessionLocal, Incident, IncidentStatus, PastIncident,
    RecentDeploy, Service, ServiceLog, ServiceMetric,
    create_all_tables, get_db,
)
from metrics_loop import (
    ALL_SERVICES, SIMULATED_SERVICES,
    anomaly_injector_loop, anomaly_state,
    collect_metrics_tick, register_investigation_callback,
)
from seed import seed_all

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Prometheus metrics ────────────────────────────────────────────────────────
HTTP_REQUEST_COUNT = Counter("vigil_http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
INCIDENT_COUNT     = Counter("vigil_incidents_total", "Total investigations triggered", ["service", "status"])
AGENT_LATENCY      = Histogram("vigil_agent_latency_seconds", "Phase 1 agent execution latency", buckets=[1, 2, 5, 10, 20, 30, 45, 60])

scheduler = AsyncIOScheduler()


# ── Investigation pipeline callback (glues rule engine → Phase1 → Phase2) ─────

async def _on_investigation_triggered(incident_id: str, service: str, trigger_reason: str) -> None:
    """
    Called by the rule engine when a threshold breach is detected and the lock acquired.
    Runs Phase 1 (LLM) then Phase 2 (policy_engine) sequentially.
    This is async but runs as an asyncio.Task — non-blocking from the rule engine.
    """
    logger.info("🚀 Investigation pipeline started: incident=%s service=%s", incident_id, service)
    start_time = time.perf_counter()
    try:
        from phase1_agent import run_phase1
        terminal_json = await run_phase1(incident_id, service, trigger_reason)

        # Record Phase 1 LLM duration
        duration = time.perf_counter() - start_time
        AGENT_LATENCY.observe(duration)

        from policy_engine import decide
        await decide(incident_id, service, terminal_json)

        # Get final status of this incident to increment metric
        async with AsyncSessionLocal() as db:
            inc = await db.get(Incident, uuid.UUID(incident_id))
            status_str = inc.status.value if inc else "unknown"
        INCIDENT_COUNT.labels(service=service, status=status_str).inc()

    except Exception as exc:
        logger.error("Investigation pipeline error for incident=%s: %s", incident_id, exc)
        INCIDENT_COUNT.labels(service=service, status="failed").inc()
        # Even on unexpected crash, release the lock
        try:
            from policy_engine import _release_lock
            await _release_lock(incident_id, service, IncidentStatus.paged, f"Pipeline error: {exc}")
        except Exception as inner:
            logger.critical("Lock release also failed: %s", inner)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Vigil starting…")

    # 1. Schema
    await create_all_tables()
    logger.info("✅ DB schema ready")

    # 2. Seed data (idempotent)
    await seed_all()
    logger.info("✅ Seed data ready")

    # 3. Metrics scheduler
    interval = int(os.getenv("METRICS_INTERVAL_SECONDS", "5"))
    scheduler.add_job(collect_metrics_tick, "interval", seconds=interval, id="metrics_tick")
    scheduler.start()
    logger.info("✅ Metrics scheduler started (every %ds)", interval)

    # 4. Anomaly injector (long-running async task)
    injector_task = asyncio.create_task(anomaly_injector_loop())

    # 5. Register the Phase1→Phase2 pipeline callback
    register_investigation_callback(_on_investigation_triggered)
    logger.info("✅ Investigation pipeline registered")

    logger.info("🟢 Vigil is live on port 8000")
    yield

    scheduler.shutdown(wait=False)
    injector_task.cancel()
    logger.info("🔴 Vigil shut down")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Vigil — Autonomous Incident Response Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS must be first
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus HTTP request counting middleware
class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = request.url.path
        if path in ("/metrics", "/health"):
            return await call_next(request)
        
        response = await call_next(request)
        status = str(response.status_code)
        HTTP_REQUEST_COUNT.labels(method=method, endpoint=path, status=status).inc()
        return response

app.add_middleware(PrometheusMiddleware)


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM & METRICS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics", tags=["system"])
async def get_metrics_endpoint():
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return StarletteResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION
# ══════════════════════════════════════════════════════════════════════════════

from auth import LoginRequest, Token, create_access_token, get_current_user, verify_password

@app.post("/api/auth/login", response_model=Token, tags=["auth"])
async def login(req: LoginRequest):
    # verify_password handles checking against DEMO_PASS
    if not verify_password(req.password, "") or req.username != "admin":
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    from datetime import timedelta
    access_token = create_access_token(
        data={"sub": req.username},
        expires_delta=timedelta(minutes=480)
    )
    return {"access_token": access_token, "token_type": "bearer"}


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD — Services
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/services", dependencies=[Depends(get_current_user)], tags=["dashboard"])
async def get_services(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Service))).scalars().all()
    return {
        "services": [
            {
                "name":                       r.name,
                "is_simulated":               r.is_simulated,
                "cpu_pct":                    r.cpu_pct,
                "memory_pct":                 r.memory_pct,
                "latency_ms":                 r.latency_ms,
                "error_rate":                 r.error_rate,
                "request_rate":               r.request_rate,
                "investigation_in_progress":  r.investigation_in_progress,
                "last_investigation_id":      str(r.last_investigation_id) if r.last_investigation_id else None,
                "anomaly_active":             anomaly_state.get(r.name, {}).get("active", False),
                "anomaly_type":               anomaly_state.get(r.name, {}).get("type"),
            }
            for r in rows
        ]
    }


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD — Metrics / Logs / Deploys
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/metrics/{service}", dependencies=[Depends(get_current_user)], tags=["dashboard"])
async def get_metrics(
    service: str,
    limit: int = Query(default=60, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    if service not in ALL_SERVICES:
        raise HTTPException(404, f"Unknown service: {service}")

    rows = (await db.execute(
        select(ServiceMetric)
        .where(ServiceMetric.service == service)
        .order_by(desc(ServiceMetric.timestamp))
        .limit(limit)
    )).scalars().all()

    return {
        "service": service,
        "metrics": [
            {
                "id":           str(r.id),
                "timestamp":    r.timestamp.isoformat() if r.timestamp else None,
                "cpu_pct":      r.cpu_pct,
                "memory_pct":   r.memory_pct,
                "latency_ms":   r.latency_ms,
                "error_rate":   r.error_rate,
                "request_rate": r.request_rate,
                "is_anomaly":   r.is_anomaly,
            }
            for r in reversed(rows)
        ],
    }


@app.get("/api/logs/{service}", dependencies=[Depends(get_current_user)], tags=["dashboard"])
async def get_logs(
    service: str,
    limit: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    if service not in ALL_SERVICES:
        raise HTTPException(404, f"Unknown service: {service}")

    rows = (await db.execute(
        select(ServiceLog)
        .where(ServiceLog.service == service)
        .order_by(desc(ServiceLog.timestamp))
        .limit(limit)
    )).scalars().all()

    return {
        "service": service,
        "logs": [
            {
                "id":                str(r.id),
                "timestamp":         r.timestamp.isoformat() if r.timestamp else None,
                "level":             r.level,
                "message":           r.message,
                "is_anomaly_related": r.is_anomaly_related,
            }
            for r in reversed(rows)
        ],
    }


@app.get("/api/deploys/{service}", dependencies=[Depends(get_current_user)], tags=["dashboard"])
async def get_deploys(
    service: str,
    limit: int = Query(default=5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(RecentDeploy)
        .where(RecentDeploy.service == service)
        .order_by(desc(RecentDeploy.deployed_at))
        .limit(limit)
    )).scalars().all()

    return {
        "service": service,
        "deploys": [
            {
                "id":          str(r.id),
                "version":     r.version,
                "deployed_at": r.deployed_at.isoformat() if r.deployed_at else None,
                "deployed_by": r.deployed_by,
                "status":      r.status,
                "notes":       r.notes,
            }
            for r in rows
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD — Incidents
# ══════════════════════════════════════════════════════════════════════════════

def _inc_to_dict(r: Incident) -> dict[str, Any]:
    import json as _json
    return {
        "id":                          str(r.id),
        "service":                     r.service,
        "status":                      r.status,
        "root_cause_hypothesis":       r.root_cause_hypothesis,
        "confidence":                  r.confidence,
        "reasoning":                   r.reasoning,
        "referenced_similar_incident": r.referenced_similar_incident,
        "action_taken":                r.action_taken,
        "action_detail":               r.action_detail,
        "approved_by":                 r.approved_by,
        "similar_incidents":           _json.loads(r.similar_incidents) if r.similar_incidents else [],
        "tool_call_trace":             _json.loads(r.phase1_tool_call_trace) if r.phase1_tool_call_trace else [],
        "created_at":                  r.created_at.isoformat()          if r.created_at          else None,
        "phase1_completed_at":         r.phase1_completed_at.isoformat() if r.phase1_completed_at else None,
        "decided_at":                  r.decided_at.isoformat()          if r.decided_at          else None,
        "approved_at":                 r.approved_at.isoformat()         if r.approved_at         else None,
    }


@app.get("/api/incidents", dependencies=[Depends(get_current_user)], tags=["dashboard"])
async def list_incidents(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(Incident).order_by(desc(Incident.created_at)).limit(limit)
    )).scalars().all()
    return {"incidents": [_inc_to_dict(r) for r in rows]}


@app.get("/api/incidents/{incident_id}", dependencies=[Depends(get_current_user)], tags=["dashboard"])
async def get_incident(incident_id: str, db: AsyncSession = Depends(get_db)):
    try:
        uid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(400, "Invalid incident ID")
    row = (await db.execute(select(Incident).where(Incident.id == uid))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Incident not found")
    return _inc_to_dict(row)


# ══════════════════════════════════════════════════════════════════════════════
# HUMAN APPROVAL — paged → approved
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/incidents/{incident_id}/approve", dependencies=[Depends(get_current_user)], tags=["dashboard"])
async def approve_incident(incident_id: str, db: AsyncSession = Depends(get_db)):
    """
    Human approval path.
    Calls Python action functions DIRECTLY — never routes back through the LLM.
    Defensively re-asserts investigation_in_progress = FALSE.
    """
    try:
        uid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(400, "Invalid incident ID")

    row = (await db.execute(select(Incident).where(Incident.id == uid))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Incident not found")
    if row.status != IncidentStatus.paged:
        raise HTTPException(400, f"Incident status is '{row.status}', expected 'paged'")

    from policy_engine import execute_approved_action
    result = await execute_approved_action(incident_id, approved_by="demo-user")

    if "error" in result:
        raise HTTPException(500, result["error"])

    return {"status": "approved", "incident_id": incident_id, "action_result": result}


# ══════════════════════════════════════════════════════════════════════════════
# PAST INCIDENTS (RAG knowledge base)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/past-incidents", dependencies=[Depends(get_current_user)], tags=["rag"])
async def list_past_incidents(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(PastIncident).order_by(desc(PastIncident.created_at)).limit(limit)
    )).scalars().all()
    return {
        "past_incidents": [
            {
                "id":               str(r.id),
                "title":            r.title,
                "root_cause":       r.root_cause,
                "action_taken":     r.action_taken,
                "resolution_notes": r.resolution_notes,
                "service":          r.service,
                "occurred_at":      r.occurred_at.isoformat() if r.occurred_at else None,
            }
            for r in rows
        ]
    }


# ══════════════════════════════════════════════════════════════════════════════
# DEBUG — manual anomaly trigger (for testing Step 2/3)
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/debug/trigger-anomaly", dependencies=[Depends(get_current_user)], tags=["debug"])
async def trigger_anomaly(service: str = "checkout-api", anomaly_type: str = "high_latency"):
    """Manually inject an anomaly to test the investigation pipeline."""
    if service not in SIMULATED_SERVICES:
        raise HTTPException(400, f"{service} is not a simulated service")
    from metrics_loop import _inject_anomaly
    asyncio.create_task(_inject_anomaly(service, anomaly_type))
    return {"status": "injected", "service": service, "type": anomaly_type}


@app.post("/api/debug/trigger-investigation", dependencies=[Depends(get_current_user)], tags=["debug"])
async def trigger_investigation(service: str = "checkout-api", reason: str = "manual test"):
    """Directly fire the investigation pipeline for testing (bypasses threshold check)."""
    # Check lock first
    async with AsyncSessionLocal() as db:
        svc = await db.get(Service, service)
        if not svc:
            raise HTTPException(404, f"Service {service} not found")
        if svc.investigation_in_progress:
            return {"status": "skipped", "reason": "investigation_already_in_progress"}

        incident_id = uuid.uuid4()
        svc.investigation_in_progress = True
        svc.last_investigation_id     = incident_id
        db.add(Incident(id=incident_id, service=service, status=IncidentStatus.investigating))
        await db.commit()

    asyncio.create_task(_on_investigation_triggered(str(incident_id), service, reason))
    return {"status": "started", "incident_id": str(incident_id), "service": service}


# ── Local import needed for type hints ────────────────────────────────────────
from sqlalchemy.ext.asyncio import AsyncSession

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
