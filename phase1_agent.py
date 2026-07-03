"""
phase1_agent.py — Phase 1: LLM-based read-only investigation loop.

DESIGN INVARIANT (enforced here):
  The LLM is ONLY given four read-only tools:
    get_metrics, get_logs, get_recent_deploys, search_similar_incidents.

  restart_service, rollback_deploy, and page_human are NOT in TOOL_DEFINITIONS.
  The model physically cannot call them.

  The loop runs until the model returns a terminal JSON object (no tool call):
    {
      "root_cause_hypothesis": str,
      "confidence": int (0-100),
      "reasoning": str,
      "referenced_similar_incident": str | null,
      "recommended_action": "restart" | "rollback" | "investigate_manually"
    }

  This terminal JSON is then passed to policy_engine.decide() (Phase 2).
  Phase 2 is pure Python — it never calls the LLM again.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone

from openai import AsyncOpenAI
from sqlalchemy import desc, select

from database import AsyncSessionLocal, Incident, IncidentStatus, PastIncident, RecentDeploy, ServiceLog, ServiceMetric
from seed import embed

logger = logging.getLogger(__name__)

# ── Groq client (OpenAI-compatible) ───────────────────────────────────────────
_client = AsyncOpenAI(
    api_key=os.getenv("GROQ_API_KEY", ""),
    base_url="https://api.groq.com/openai/v1",
)
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_ITERS    = int(os.getenv("AGENT_MAX_ITERATIONS", "10"))

# ── READ-ONLY TOOL DEFINITIONS (action tools deliberately absent) ─────────────
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "Get the last 20 metric snapshots (cpu_pct, memory_pct, latency_ms, error_rate, request_rate) for a service. Call this first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Service name: local-host | checkout-api | auth-api | payments-api"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_logs",
            "description": "Get the last 15 log lines for a service. Essential for root-cause clues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "limit":   {"type": "integer", "default": 15},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_deploys",
            "description": "Get the last 5 deployments for a service. Use to correlate incidents with recent code changes.",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_similar_incidents",
            "description": (
                "MANDATORY: Search the historical incident knowledge base using semantic vector similarity. "
                "Call this with a description of the current symptoms. Returns top-3 past incidents. "
                "Reference the most relevant one in your terminal JSON."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_text": {"type": "string", "description": "Description of current symptoms"},
                    "top_k":      {"type": "integer", "default": 3},
                },
                "required": ["query_text"],
            },
        },
    },
]

_SYSTEM_PROMPT = """You are Vigil, an SRE incident investigation agent.
Your ONLY job in this phase is to investigate — you CANNOT take actions.
You have four read-only tools: get_metrics, get_logs, get_recent_deploys, search_similar_incidents.

INVESTIGATION PROTOCOL:
1. get_metrics(service) — understand current metric values.
2. get_logs(service) — read error messages and root-cause clues.
3. get_recent_deploys(service) — check for recent code changes.
4. search_similar_incidents(query_text) — MANDATORY. Describe the symptoms you see.
5. When you have enough information, stop calling tools and respond with ONLY this JSON (no markdown, no extra text):

{
  "root_cause_hypothesis": "specific technical root cause",
  "confidence": 0-100,
  "reasoning": "step-by-step explanation referencing what you found",
  "referenced_similar_incident": "title of the most relevant past incident, or null",
  "recommended_action": "restart" | "rollback" | "investigate_manually"
}

RULES:
- Do not call any tool more than twice.
- You MUST call search_similar_incidents exactly once.
- Your confidence should reflect how certain you are (>= 80 means you are very sure).
- If the issue is on local-host, set recommended_action = "investigate_manually" since hosts cannot be auto-restarted.
- Respond with the terminal JSON only when you have sufficient evidence."""


# ── Tool implementations (read-only) ──────────────────────────────────────────

async def _get_metrics(service: str) -> dict:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ServiceMetric)
            .where(ServiceMetric.service == service)
            .order_by(desc(ServiceMetric.timestamp))
            .limit(20)
        )).scalars().all()

    if not rows:
        return {"error": f"No metrics for {service}"}

    data = [
        {
            "ts": r.timestamp.isoformat() if r.timestamp else None,
            "cpu_pct":   round(r.cpu_pct, 1),
            "mem_pct":   round(r.memory_pct, 1),
            "lat_ms":    round(r.latency_ms,  1) if r.latency_ms  else None,
            "err_rate":  round(r.error_rate,   4) if r.error_rate  else None,
            "rps":       round(r.request_rate, 1) if r.request_rate else None,
            "anomaly":   r.is_anomaly,
        }
        for r in reversed(rows)
    ]
    latest = data[-1]
    return {
        "service": service,
        "latest":  latest,
        "summary": {
            "cpu_max":     max(d["cpu_pct"] for d in data),
            "lat_max_ms":  max((d["lat_ms"] or 0) for d in data),
            "err_max":     max((d["err_rate"] or 0) for d in data),
            "anomaly_ticks": sum(1 for d in data if d["anomaly"]),
        },
        "datapoints": data,
    }


async def _get_logs(service: str, limit: int = 15) -> dict:
    limit = min(limit, 50)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ServiceLog)
            .where(ServiceLog.service == service)
            .order_by(desc(ServiceLog.timestamp))
            .limit(limit)
        )).scalars().all()
    return {
        "service": service,
        "logs": [
            {"ts": r.timestamp.isoformat() if r.timestamp else None,
             "level": r.level, "msg": r.message, "anomaly": r.is_anomaly_related}
            for r in reversed(rows)
        ],
    }


async def _get_recent_deploys(service: str) -> dict:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(RecentDeploy)
            .where(RecentDeploy.service == service)
            .order_by(desc(RecentDeploy.deployed_at))
            .limit(5)
        )).scalars().all()
    return {
        "service": service,
        "deploys": [
            {"version": r.version, "deployed_at": r.deployed_at.isoformat(),
             "by": r.deployed_by, "status": r.status, "notes": r.notes}
            for r in rows
        ],
    }


async def _search_similar(query_text: str, top_k: int = 3) -> dict:
    vec = embed(query_text)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(
                PastIncident,
                PastIncident.embedding.cosine_distance(vec).label("dist"),
            )
            .where(PastIncident.embedding.is_not(None))
            .order_by("dist")
            .limit(top_k)
        )).all()

    return {
        "query": query_text,
        "results": [
            {
                "title":      r.PastIncident.title,
                "root_cause": r.PastIncident.root_cause,
                "action":     r.PastIncident.action_taken,
                "notes":      r.PastIncident.resolution_notes,
                "service":    r.PastIncident.service,
                "similarity": round(1 - r.dist, 4),
            }
            for r in rows
        ],
    }


async def _dispatch(name: str, args: dict) -> dict:
    """Execute a read-only tool call."""
    if name == "get_metrics":          return await _get_metrics(args["service"])
    if name == "get_logs":             return await _get_logs(args["service"], args.get("limit", 15))
    if name == "get_recent_deploys":   return await _get_recent_deploys(args["service"])
    if name == "search_similar_incidents": return await _search_similar(args["query_text"], args.get("top_k", 3))
    return {"error": f"Unknown tool: {name}"}


def _parse_terminal_json(text: str) -> dict | None:
    """Extract the terminal JSON dict from the model's text response."""
    text = text.strip()
    # Try direct parse
    try:
        data = json.loads(text)
        if "root_cause_hypothesis" in data:
            return data
    except json.JSONDecodeError:
        pass
    # Try to extract JSON block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if "root_cause_hypothesis" in data:
                return data
        except json.JSONDecodeError:
            pass
    return None


# ── Main Phase 1 loop ─────────────────────────────────────────────────────────

async def run_phase1(incident_id: str, service: str, trigger_reason: str) -> dict | None:
    """
    Runs the Phase 1 LLM read-only investigation loop.

    Returns the terminal JSON dict (root_cause_hypothesis, confidence, …)
    or None if the loop exhausted MAX_ITERS without a valid response.

    Caller (main.py / agent dispatcher) must then call policy_engine.decide().
    """
    logger.info("🔍 Phase 1 started: incident=%s service=%s", incident_id, service)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"INVESTIGATION REQUEST\n"
                f"Service:        {service}\n"
                f"Trigger reason: {trigger_reason}\n"
                f"Incident ID:    {incident_id}\n"
                f"Started at:     {datetime.now(timezone.utc).isoformat()}\n\n"
                f"Please investigate and return your terminal JSON when ready."
            ),
        },
    ]

    tool_trace: list[dict] = []
    similar_results: list[dict] = []
    terminal_json: dict | None = None

    for iteration in range(1, MAX_ITERS + 1):
        logger.info("🔄 Phase 1 iteration %d/%d (incident=%s)", iteration, MAX_ITERS, incident_id)

        try:
            response = await _client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.1,
            )
        except Exception as exc:
            logger.error("Phase 1 LLM error: %s", exc)
            break

        msg = response.choices[0].message

        # ── No tool calls → LLM gave text response → try to parse terminal JSON
        if not msg.tool_calls:
            content = msg.content or ""
            terminal_json = _parse_terminal_json(content)
            if terminal_json:
                logger.info("✅ Phase 1 terminal JSON received (confidence=%s)", terminal_json.get("confidence"))
                break
            else:
                # LLM gave text but not valid JSON — ask it to format properly
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "Please respond with ONLY the terminal JSON object as specified. No markdown, no explanation.",
                })
                continue

        # ── Tool calls → execute each
        messages.append(msg.model_dump(exclude_none=True))

        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            result = await _dispatch(tool_name, tool_args)

            if tool_name == "search_similar_incidents":
                similar_results = result.get("results", [])

            tool_trace.append({
                "iter": iteration,
                "tool": tool_name,
                "args": tool_args,
                "result_summary": str(result)[:400],
            })

            messages.append({
                "role":        "tool",
                "tool_call_id": tc.id,
                "content":     json.dumps(result),
            })

    # ── Persist Phase 1 results to the incident row ───────────────────────────
    async with AsyncSessionLocal() as db:
        result_q = await db.execute(
            select(Incident).where(Incident.id == uuid.UUID(incident_id))
        )
        inc = result_q.scalar_one_or_none()
        if inc:
            inc.phase1_tool_call_trace = json.dumps(tool_trace)
            inc.similar_incidents      = json.dumps(similar_results[:3])
            inc.phase1_completed_at    = datetime.now(timezone.utc)

            if terminal_json:
                inc.root_cause_hypothesis       = terminal_json.get("root_cause_hypothesis", "")
                inc.confidence                  = float(terminal_json.get("confidence", 0))
                inc.reasoning                   = terminal_json.get("reasoning", "")
                inc.referenced_similar_incident = terminal_json.get("referenced_similar_incident")
            else:
                inc.root_cause_hypothesis = "Phase 1 timed out — no conclusive hypothesis"
                inc.confidence = 0

            await db.commit()

    if not terminal_json:
        logger.warning("Phase 1 exhausted without terminal JSON for incident=%s", incident_id)

    return terminal_json
