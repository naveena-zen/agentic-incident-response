"""
policy_engine.py — Phase 2: Deterministic safety policy (NO LLM calls).

This module is the ONLY place where action functions are called.
The LLM (Phase 1) never has access to restart_service, rollback_deploy, or page_human.

decide() enforces:
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ IF confidence >= 80 AND service in ALLOWLIST AND service != "local-host"│
  │   → auto-action (restart or rollback)  → status = resolved_auto        │
  │ ELSE                                                                    │
  │   → page_human (SMTP email + log)      → status = paged                │
  │                                                                         │
  │ IN ALL PATHS (try/finally guarantee):                                   │
  │   services.investigation_in_progress = FALSE                            │
  │   services.last_investigation_id     = incident.id                     │
  │   incident.decided_at                = now()                            │
  └─────────────────────────────────────────────────────────────────────────┘

The "Approve action" button on the frontend calls execute_approved_action() directly —
it never routes back through the LLM.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from database import AsyncSessionLocal, Incident, IncidentStatus, RecentDeploy, Service

logger = logging.getLogger(__name__)

# ── Safety policy constants ────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 80
# Services that CAN be auto-actioned (must NOT include local-host).
# Only list services that are actually monitored. inventory-api is excluded
# because it is not a seeded/monitored service in this deployment.
ACTION_ALLOWLIST = ["checkout-api"]


# ── Action implementations (mocked for simulated services) ────────────────────

async def restart_service(service: str, reason: str) -> dict:
    """
    MOCKED restart. Clears the in-memory anomaly state.
    local-host must NEVER be passed here (blocked by policy gate above).
    """
    from metrics_loop import clear_anomaly
    logger.warning("🔄 [ACTION] restart_service(%s) — %s", service, reason)
    clear_anomaly(service)
    return {"action": "restart_service", "service": service, "result": "success (simulated)", "reason": reason}


async def rollback_deploy(service: str, reason: str) -> dict:
    """
    MOCKED rollback. Marks the latest deploy as rolled_back in Postgres.
    """
    from metrics_loop import clear_anomaly
    logger.warning("⏪ [ACTION] rollback_deploy(%s) — %s", service, reason)

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(RecentDeploy)
            .where(RecentDeploy.service == service)
            .order_by(RecentDeploy.deployed_at.desc())
            .limit(2)
        )).scalars().all()

    if len(rows) < 2:
        return {"action": "rollback_deploy", "service": service, "result": "error", "detail": "Not enough history"}

    current, target = rows[0], rows[1]
    async with AsyncSessionLocal() as db:
        deploy = await db.get(RecentDeploy, current.id)
        if deploy:
            deploy.status = "rolled_back"
            await db.commit()

    clear_anomaly(service)
    return {
        "action": "rollback_deploy",
        "service": service,
        "rolled_back_from": current.version,
        "rolled_back_to":   target.version,
        "result": "success (simulated)",
        "reason": reason,
    }


async def page_human(
    incident_id: str,
    service: str,
    hypothesis: str,
    recommended_action: str,
    confidence: float,
    reasoning: str = "",
) -> dict:
    """
    Pages human via SMTP (with logging fallback).
    Always called when policy does NOT auto-act.
    """
    from notifications import send_page_email
    logger.warning(
        "📧 [PAGE HUMAN] incident=%s service=%s confidence=%.0f\n  Hypothesis: %s",
        incident_id, service, confidence, hypothesis,
    )
    email_result = await send_page_email(
        incident_id=incident_id,
        service=service,
        summary=f"Vigil detected an anomaly on {service} requiring human review.",
        root_cause_hypothesis=hypothesis,
        recommended_action=recommended_action,
        confidence=confidence,
    )
    return {
        "action": "page_human",
        "service": service,
        "email_sent": email_result.get("success", False),
        "email_error": email_result.get("error"),
    }


# ── Lock release (must run in every code path) ────────────────────────────────

async def _release_lock(incident_id: str, service: str, final_status: IncidentStatus, action_detail: str) -> None:
    """
    Sets investigation_in_progress = FALSE and records decided_at.
    Also stamps the cooldown timer so the rule engine won't immediately re-trigger.
    Called in the finally block of decide() — guaranteed to run even on exception.
    """
    from metrics_loop import _last_investigation_finished
    _last_investigation_finished[service] = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        svc = await db.get(Service, service)
        if svc:
            svc.investigation_in_progress = False
            svc.last_investigation_id     = uuid.UUID(incident_id)

        inc = (await db.execute(
            select(Incident).where(Incident.id == uuid.UUID(incident_id))
        )).scalar_one_or_none()
        if inc:
            inc.status        = final_status
            inc.decided_at    = datetime.now(timezone.utc)
            inc.action_detail = action_detail

        await db.commit()

    logger.info(
        "🔓 Lock released: service=%s incident=%s status=%s",
        service, incident_id, final_status,
    )


# ── Main policy gate ──────────────────────────────────────────────────────────

async def decide(
    incident_id: str,
    service: str,
    terminal_json: dict | None,
) -> None:
    """
    Phase 2: deterministic policy gate — NO LLM calls.

    Parameters
    ----------
    incident_id   : UUID string of the Incident row.
    service       : Service name.
    terminal_json : Output from Phase 1 LLM loop, or None if Phase 1 failed.

    The try/finally guarantees investigation_in_progress is ALWAYS released,
    including on exception (e.g., SMTP failure, DB timeout, unexpected error).
    """
    # Defaults if Phase 1 failed or returned nothing
    confidence         = float((terminal_json or {}).get("confidence", 0))
    hypothesis         = (terminal_json or {}).get("root_cause_hypothesis", "Undetermined — Phase 1 failed")
    recommended_action = (terminal_json or {}).get("recommended_action", "investigate_manually")
    reasoning          = (terminal_json or {}).get("reasoning", "")

    final_status   = IncidentStatus.paged     # default
    action_detail  = ""
    action_result  = {}

    logger.info(
        "⚖️  Policy engine: incident=%s service=%s confidence=%.0f recommended=%s",
        incident_id, service, confidence, recommended_action,
    )

    try:
        # ══════════════════════════════════════════════════════════════
        # SAFETY GATE: auto-action ONLY if ALL three conditions met
        #   1. confidence >= CONFIDENCE_THRESHOLD (80)
        #   2. service is in ACTION_ALLOWLIST
        #   3. service is NOT local-host (never auto-actioned)
        # ══════════════════════════════════════════════════════════════
        can_auto_act = (
            confidence >= CONFIDENCE_THRESHOLD
            and service in ACTION_ALLOWLIST
            and service != "local-host"
        )

        if can_auto_act:
            logger.info("✅ Safety gate PASSED — executing auto-action for %s", service)

            if recommended_action == "rollback":
                action_result = await rollback_deploy(service, hypothesis)
            else:
                # Default auto-action is restart
                action_result = await restart_service(service, hypothesis)

            final_status  = IncidentStatus.resolved_auto
            action_detail = json.dumps(action_result)

            # Embed and add to RAG knowledge base
            asyncio.create_task(_add_to_rag(incident_id, service, hypothesis, action_result))

        else:
            # ── Log WHY the gate blocked auto-action (for debugging/audit) ──
            if service == "local-host":
                block_reason = "local-host is never auto-actioned"
            elif service not in ACTION_ALLOWLIST:
                block_reason = f"service '{service}' is not in ACTION_ALLOWLIST {ACTION_ALLOWLIST}"
            else:
                block_reason = f"confidence {confidence:.0f} < threshold {CONFIDENCE_THRESHOLD}"

            logger.warning("⛔ Safety gate BLOCKED auto-action: %s → paging human", block_reason)

            action_result = await page_human(
                incident_id=incident_id,
                service=service,
                hypothesis=hypothesis,
                recommended_action=recommended_action,
                confidence=confidence,
                reasoning=reasoning,
            )
            final_status  = IncidentStatus.paged
            action_detail = json.dumps(action_result)

    except Exception as exc:
        # Even if the action itself throws, we still release the lock
        logger.error("Policy engine action raised exception: %s — releasing lock anyway", exc)
        final_status  = IncidentStatus.paged
        action_detail = f"Action failed with exception: {exc}; paged as fallback"
        # Attempt fallback page (best-effort, not in try/finally to avoid infinite recursion)
        try:
            await page_human(incident_id, service, hypothesis, recommended_action, confidence)
        except Exception as page_exc:
            logger.error("Fallback page_human also failed: %s", page_exc)

    finally:
        # ── GUARANTEED LOCK RELEASE — runs in every code path ─────────────
        await _release_lock(incident_id, service, final_status, action_detail)


# ── Human Approval path ───────────────────────────────────────────────────────

async def execute_approved_action(incident_id: str, approved_by: str = "demo-user") -> dict:
    """
    Called when the human clicks "Approve action" on a paged incident.
    Calls Python action functions DIRECTLY — never routes back through the LLM.
    Also defensively re-asserts investigation_in_progress = FALSE.
    """
    async with AsyncSessionLocal() as db:
        inc = (await db.execute(
            select(Incident).where(Incident.id == uuid.UUID(incident_id))
        )).scalar_one_or_none()

        if not inc:
            return {"error": "Incident not found"}
        if inc.status != IncidentStatus.paged:
            return {"error": f"Incident status is '{inc.status}', not 'paged'"}

    service            = inc.service
    hypothesis         = inc.root_cause_hypothesis or ""
    recommended_action = "restart"  # default; parse from action_detail if available

    # Try to infer recommended_action from Phase 1 output
    try:
        if inc.phase1_tool_call_trace:
            pass  # could parse, but recommended_action is embedded in incident if we stored it
    except Exception:
        pass

    logger.info("👍 Human approved action for incident=%s service=%s", incident_id, service)

    try:
        if "rollback" in (inc.action_detail or "").lower() or "rollback" in hypothesis.lower():
            result = await rollback_deploy(service, f"Human-approved (incident {incident_id})")
        else:
            result = await restart_service(service, f"Human-approved (incident {incident_id})")

        # Transition: paged → approved
        async with AsyncSessionLocal() as db:
            inc_row = (await db.execute(
                select(Incident).where(Incident.id == uuid.UUID(incident_id))
            )).scalar_one_or_none()
            if inc_row:
                inc_row.status      = IncidentStatus.approved
                inc_row.approved_by = approved_by
                inc_row.approved_at = datetime.now(timezone.utc)
                inc_row.action_detail = json.dumps(result)
                await db.commit()

            # Defensive re-assert: lock should already be FALSE from Phase 2,
            # but we set it explicitly here as a safety measure.
            svc = await db.get(Service, service)
            if svc and svc.investigation_in_progress:
                logger.warning("Lock was still TRUE on approval — releasing defensively for %s", service)
                svc.investigation_in_progress = False
                await db.commit()

        return {"status": "approved", "action_result": result}

    except Exception as exc:
        logger.error("execute_approved_action failed: %s", exc)
        return {"error": str(exc)}


# ── RAG insertion for resolved incidents ──────────────────────────────────────

async def _add_to_rag(incident_id: str, service: str, hypothesis: str, action_result: dict) -> None:
    """Embeds a newly resolved incident and adds it to past_incidents."""
    import asyncio as _asyncio
    from seed import embed
    from database import PastIncident

    title = f"[AUTO-RESOLVED] {service} — {hypothesis[:80]}"
    text  = f"{title}. {hypothesis}"
    vec   = await _asyncio.get_event_loop().run_in_executor(None, embed, text)

    async with AsyncSessionLocal() as db:
        db.add(PastIncident(
            title=title,
            root_cause=hypothesis,
            action_taken=action_result.get("action", "restart"),
            resolution_notes=json.dumps(action_result),
            service=service,
            occurred_at=datetime.now(timezone.utc),
            embedding=vec,
        ))
        await db.commit()

    logger.info("📚 Added resolved incident to RAG knowledge base: %s", title)
