"""
metrics_loop.py — Background data engine for Vigil.

Responsibilities
----------------
1. Every METRICS_INTERVAL_SECONDS:
   • Collect real psutil stats for "local-host".
   • Generate synthetic metrics for checkout-api, auth-api, payments-api.
   • Write ServiceMetric rows + ServiceLog rows + update Service snapshot columns.
   • Run the THRESHOLD RULE ENGINE (pure Python, no LLM).

2. THRESHOLD RULE ENGINE (runs every tick, no LLM):
   Trigger condition:
     latency_ms > 500  OR  error_rate > 0.05  OR  (local-host) cpu_pct > 85
   Before starting investigation:
     - Check service.investigation_in_progress; if TRUE → skip (duplicate guard).
     - If FALSE: SET it TRUE, create Incident row (status=investigating), fire Phase 1.

3. Anomaly injector loop (separate asyncio task):
   Every 30-60 s, picks a service and injects an anomaly (spikes metrics + writes
   correlated log lines). Auto-clears after 90-180 s.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

import psutil
from sqlalchemy import select, update

from database import AsyncSessionLocal, Incident, IncidentStatus, Service, ServiceLog, ServiceMetric

logger = logging.getLogger(__name__)

METRICS_INTERVAL = int(os.getenv("METRICS_INTERVAL_SECONDS", "5"))
ANOMALY_MIN      = int(os.getenv("ANOMALY_MIN_INTERVAL", "30"))
ANOMALY_MAX      = int(os.getenv("ANOMALY_MAX_INTERVAL", "60"))

SIMULATED_SERVICES = ["checkout-api", "auth-api", "payments-api"]
ALL_SERVICES       = ["local-host"] + SIMULATED_SERVICES

# ── Thresholds for the rule engine ────────────────────────────────────────────
LATENCY_THRESHOLD    = 500.0   # ms
ERROR_RATE_THRESHOLD = 0.05    # 5%
CPU_THRESHOLD_HOST   = 85.0   # % (only for local-host)

# ── Post-investigation cooldown (seconds) ─────────────────────────────────────
# After an investigation completes (lock released), we wait this long before
# the rule engine is allowed to open another investigation for the same service.
INVESTIGATION_COOLDOWN_SECONDS = 120
_last_investigation_finished: dict[str, datetime] = {}  # service -> UTC datetime

# ── Anomaly state (in-memory; source of truth for metric generation) ──────────
anomaly_state: dict[str, dict] = {
    s: {"active": False, "type": None, "started_at": None}
    for s in SIMULATED_SERVICES
}

# ── Callback for kicking off Phase 1 (registered by main.py) ─────────────────
_investigation_callbacks: list[Callable] = []

def register_investigation_callback(cb: Callable) -> None:
    _investigation_callbacks.append(cb)


# ── Normal metric bands ────────────────────────────────────────────────────────
_NORMAL = {
    "checkout-api": dict(cpu=(5,30),  mem=(20,50), lat=(80,250),  err=(0.0,0.02), rps=(50,200)),
    "auth-api":     dict(cpu=(3,25),  mem=(15,40), lat=(30,120),  err=(0.0,0.01), rps=(30,150)),
    "payments-api": dict(cpu=(5,35),  mem=(25,55), lat=(100,350), err=(0.0,0.015),rps=(20,80)),
}

# ── Anomaly templates ──────────────────────────────────────────────────────────
_ANOMALIES = {
    "cpu_spike": {
        "override": lambda m: {**m, "cpu_pct": random.uniform(88,99),
                                    "latency_ms": m["latency_ms"] * random.uniform(2,4)},
        "logs": [
            ("WARN",     "CPU throttling detected — worker threads starved"),
            ("ERROR",    "Request queue depth exceeded 500 — rejecting new connections"),
            ("ERROR",    "Health check timed out — instance under heavy load"),
        ],
    },
    "memory_oom": {
        "override": lambda m: {**m, "memory_pct": random.uniform(90,99),
                                    "cpu_pct": m["cpu_pct"] * random.uniform(1.2,1.8)},
        "logs": [
            ("WARN",     "Heap usage at 88% — approaching container memory limit"),
            ("ERROR",    "OOM killed container — restarting with 0 in-flight requests"),
            ("CRITICAL", "Memory limit exceeded — process terminated by runtime"),
        ],
    },
    "high_latency": {
        "override": lambda m: {**m, "latency_ms": random.uniform(2000,6000),
                                    "error_rate": random.uniform(0.15,0.40)},
        "logs": [
            ("WARN",  "Downstream timeout after 3000ms waiting for dependency"),
            ("ERROR", "Connection pool exhausted — 20/20 slots in use"),
            ("ERROR", "Circuit breaker OPEN — downstream service unreachable"),
            ("WARN",  "All retries exhausted — failing fast"),
        ],
    },
    "high_error_rate": {
        "override": lambda m: {**m, "error_rate": random.uniform(0.20,0.55),
                                    "latency_ms": m["latency_ms"] * random.uniform(1.5,2.5)},
        "logs": [
            ("ERROR",    "HTTP 503 Service Unavailable from upstream"),
            ("ERROR",    "Payment gateway rate limit exceeded — HTTP 429"),
            ("WARN",     "Increased error budget burn — SLO breach imminent"),
            ("CRITICAL", "Error rate 34% exceeds SLO threshold of 1%"),
        ],
    },
    "deploy_regression": {
        "override": lambda m: {**m, "error_rate": random.uniform(0.10,0.35),
                                    "latency_ms": m["latency_ms"] * random.uniform(1.5,3.0),
                                    "memory_pct": min(99.0, m["memory_pct"] * random.uniform(1.3,1.8))},
        "logs": [
            ("ERROR",    "Unhandled exception in new code path — NullPointerException"),
            ("ERROR",    "Breaking change in SDK v4.0 — HMAC signature mismatch"),
            ("WARN",     "Rollback recommended — error rate spike post-deploy"),
            ("CRITICAL", "Deploy introduced regression — automated rollback candidate"),
        ],
    },
}


def _blend(lo: float, hi: float, wave: float) -> float:
    span = hi - lo
    return lo + span * wave + random.uniform(-span * 0.05, span * 0.05)


def _normal_metrics(service: str, tick: int) -> dict:
    n = _NORMAL[service]
    phase = (tick % 720) / 720 * 2 * math.pi
    wave  = 0.5 + 0.3 * math.sin(phase)
    return {
        "cpu_pct":    max(0.0, min(100.0, _blend(*n["cpu"], wave))),
        "memory_pct": max(0.0, min(100.0, _blend(*n["mem"], wave))),
        "latency_ms": max(1.0,  _blend(*n["lat"], wave)),
        "error_rate": max(0.0,  random.uniform(*n["err"])),
        "request_rate": max(0.0, _blend(*n["rps"], wave)),
    }


def _local_host_metrics() -> dict:
    vm = psutil.virtual_memory()
    return {
        "cpu_pct":     psutil.cpu_percent(interval=None),
        "memory_pct":  vm.percent,
        "latency_ms":  None,
        "error_rate":  None,
        "request_rate": None,
    }


def _local_host_log() -> tuple[str, str]:
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    if cpu > 80:
        return ("WARN", f"Host CPU at {cpu:.1f}% — check for runaway processes")
    if mem > 80:
        return ("WARN", f"Host memory at {mem:.1f}%")
    return ("INFO", f"Host healthy — CPU {cpu:.1f}%, MEM {mem:.1f}%, procs {len(psutil.pids())}")


# ── Threshold rule engine ──────────────────────────────────────────────────────
async def _rule_engine_check(service: str, metrics: dict) -> None:
    """
    Pure Python threshold check — no LLM.
    Triggers: latency_ms > 500 OR error_rate > 0.05 OR (local-host) cpu_pct > 85.
    If triggered AND service.investigation_in_progress is FALSE:
      → SET investigation_in_progress = TRUE
      → INSERT incident (status=investigating)
      → Fire Phase 1 callbacks
    """
    triggered = False
    trigger_reason = ""

    if service == "local-host":
        if (metrics.get("cpu_pct") or 0) > CPU_THRESHOLD_HOST:
            triggered = True
            trigger_reason = f"cpu_pct={metrics['cpu_pct']:.1f}% > {CPU_THRESHOLD_HOST}"
    else:
        lat = metrics.get("latency_ms") or 0
        err = metrics.get("error_rate") or 0
        if lat > LATENCY_THRESHOLD:
            triggered = True
            trigger_reason = f"latency_ms={lat:.0f} > {LATENCY_THRESHOLD}"
        elif err > ERROR_RATE_THRESHOLD:
            triggered = True
            trigger_reason = f"error_rate={err:.3f} > {ERROR_RATE_THRESHOLD}"

    if not triggered:
        return

    # ── COOLDOWN CHECK (in-memory, fast) ──────────────────────────────────────
    last_fin = _last_investigation_finished.get(service)
    if last_fin and (datetime.now(timezone.utc) - last_fin).total_seconds() < INVESTIGATION_COOLDOWN_SECONDS:
        logger.debug("Rule engine: %s in cooldown (%.0fs remaining) — skipping",
                     service, INVESTIGATION_COOLDOWN_SECONDS - (datetime.now(timezone.utc) - last_fin).total_seconds())
        return

    async with AsyncSessionLocal() as db:
        # Acquire row-level write lock on the Service table to prevent concurrent lock checks
        stmt = select(Service).where(Service.name == service).with_for_update()
        result = await db.execute(stmt)
        svc_row = result.scalar_one_or_none()
        if not svc_row:
            return

        # ── LOCK CHECK: skip if investigation already in progress ──────────
        if svc_row.investigation_in_progress:
            logger.debug("Rule engine: %s already under investigation — skipping", service)
            return

        # ── Acquire lock and create incident ──────────────────────────────
        incident_id = uuid.uuid4()
        svc_row.investigation_in_progress = True
        svc_row.last_investigation_id     = incident_id

        incident = Incident(
            id=incident_id,
            service=service,
            status=IncidentStatus.investigating,
        )
        db.add(incident)
        await db.commit()

    logger.warning(
        "🚨 RULE ENGINE triggered: service=%s reason=%s incident_id=%s",
        service, trigger_reason, incident_id,
    )

    # Fire Phase 1 callbacks (non-blocking)
    for cb in _investigation_callbacks:
        asyncio.create_task(cb(str(incident_id), service, trigger_reason))


# ── Main metrics tick ─────────────────────────────────────────────────────────
_tick = 0

async def collect_metrics_tick() -> None:
    global _tick
    _tick += 1

    async with AsyncSessionLocal() as db:
        # local-host (real psutil)
        lh = _local_host_metrics()
        db.add(ServiceMetric(
            service="local-host",
            cpu_pct=lh["cpu_pct"],
            memory_pct=lh["memory_pct"],
            latency_ms=None,
            error_rate=None,
            request_rate=None,
            is_anomaly=False,
        ))
        level, msg = _local_host_log()
        db.add(ServiceLog(service="local-host", level=level, message=msg))
        # Update service snapshot
        svc_lh = await db.get(Service, "local-host")
        if svc_lh:
            svc_lh.cpu_pct    = lh["cpu_pct"]
            svc_lh.memory_pct = lh["memory_pct"]

        # Simulated services
        for svc_name in SIMULATED_SERVICES:
            state = anomaly_state[svc_name]
            base  = _normal_metrics(svc_name, _tick)

            if state["active"]:
                atype   = state["type"]
                metrics = _ANOMALIES[atype]["override"](base)
                is_anom = True
                if random.random() < 0.3:
                    lvl, log_msg = random.choice(_ANOMALIES[atype]["logs"])
                    db.add(ServiceLog(service=svc_name, level=lvl, message=log_msg, is_anomaly_related=True))
            else:
                metrics = base
                is_anom = False
                db.add(ServiceLog(
                    service=svc_name, level="INFO",
                    message=f"Service healthy — latency {metrics['latency_ms']:.0f}ms err={metrics['error_rate']:.3f}",
                ))

            db.add(ServiceMetric(service=svc_name, is_anomaly=is_anom, **metrics))

            # Update service snapshot
            svc_row = await db.get(Service, svc_name)
            if svc_row:
                for k, v in metrics.items():
                    setattr(svc_row, k, v)

        await db.commit()

    # Run rule engine for each service (outside the write transaction)
    local_metrics = _local_host_metrics()
    await _rule_engine_check("local-host", local_metrics)
    for svc_name in SIMULATED_SERVICES:
        state   = anomaly_state[svc_name]
        base    = _normal_metrics(svc_name, _tick)
        metrics = _ANOMALIES[state["type"]]["override"](base) if state["active"] else base
        await _rule_engine_check(svc_name, metrics)


# ── Anomaly injector ──────────────────────────────────────────────────────────
async def _inject_anomaly(service: str, anomaly_type: str) -> None:
    anomaly_state[service].update({"active": True, "type": anomaly_type, "started_at": datetime.now(timezone.utc)})
    adef = _ANOMALIES[anomaly_type]
    async with AsyncSessionLocal() as db:
        for lvl, msg in adef["logs"]:
            db.add(ServiceLog(service=service, level=lvl, message=msg, is_anomaly_related=True))
        await db.commit()
    logger.warning("💥 Anomaly injected: service=%s type=%s", service, anomaly_type)


def clear_anomaly(service: str) -> None:
    anomaly_state[service] = {"active": False, "type": None, "started_at": None}


async def anomaly_injector_loop() -> None:
    """Randomly injects anomalies every 30-60 s; auto-clears after 90-180 s."""
    while True:
        wait = random.randint(ANOMALY_MIN, ANOMALY_MAX)
        logger.info("Next anomaly injection in %ds", wait)
        await asyncio.sleep(wait)

        candidates = [s for s in SIMULATED_SERVICES if not anomaly_state[s]["active"]]
        if not candidates:
            continue

        svc   = random.choice(candidates)
        atype = random.choice(list(_ANOMALIES.keys()))
        await _inject_anomaly(svc, atype)

        clear_delay = random.randint(90, 180)
        await asyncio.sleep(clear_delay)
        clear_anomaly(svc)
        logger.info("✅ Anomaly cleared: service=%s", svc)
