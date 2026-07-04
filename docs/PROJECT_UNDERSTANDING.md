# Project Understanding: Vigil Incident Response Agent

This document provides a simple, non-technical explanation of what the Vigil project actually does based on a direct review of the codebase, who would use it, and the patterns it demonstrates.

---

## 1. What the Project Actually Does
Vigil is a prototype of an automated assistant for server monitoring and incident response. 

Based on the actual code, the system runs as follows:
* **Simulating Server Metrics**: It generates artificial metrics (like CPU usage, memory, latency, and error rates) for three simulated APIs (`checkout-api`, `auth-api`, and `payments-api`) and fetches real system metrics for the local host (`local-host`) using Python's `psutil` library (`metrics_loop.py` lines 152-160).
* **Detecting Anomalies**: It uses simple rules (like checking if latency exceeds 500 milliseconds or error rates exceed 5%) to detect if a service is having issues (`metrics_loop.py` lines 174-201).
* **AI Diagnosis (Phase 1)**: If a service is marked as having an issue, Vigil starts an investigation. It invokes an AI model (via the Groq API) inside a custom loop (`phase1_agent.py` lines 279-395). The AI is given read-only access to recent metrics, logs, deployment records, and a database of past incidents. It uses these to propose a root-cause explanation and recommend an action (like restarting or rolling back a deployment).
* **Safety Policy Gate (Phase 2)**: Before any action is taken, a standard Python code script acts as a gatekeeper (`policy_engine.py` lines 163-260). If the AI has high confidence (at least 80%) and the service is on an approved list (which only contains `checkout-api`), it executes a simulated mitigation action (like clearing the anomaly state or marking a mock deployment as rolled back). If the confidence is too low or the service is not approved for auto-mitigation, the system generates an email alert (or prints it to the log if email credentials aren't set) and waits for a human to click "Approve" on the dashboard.
* **Interactive Dashboard**: A web interface (built with React and Chart.js) allows users to view real-time charts of the services, manually inject anomalies, view incident diagnostic logs, and approve pending agent recommendations.

### Discrepancies between Aspirational Documentation and Actual Code
* **Mocked Action Execution**: The `README.md` refers to executing mitigation actions (restarts, rollbacks). In reality, these actions are purely mocked: restarting a service just clears the in-memory anomaly flag (`policy_engine.py` lines 48-56), and rolling back a deploy simply updates a status column in the Postgres database (`policy_engine.py` lines 59-92). No actual systems (e.g., Docker, Kubernetes, or cloud servers) are restarted or modified.
* **Hardcoded Authentication**: The login system uses a single hardcoded username/password combination (`admin` / `vigil2025`), defined in `auth.py` (lines 22-23) and fallback environment variables, rather than a real multi-user database.
* **Limited Auto-Action Scope**: While the project implies general autonomous response capability, the code restricts automated mitigation exclusively to `checkout-api` (`policy_engine.py` line 43). All other services (`auth-api`, `payments-api`, and `local-host`) will always fall back to paging a human, regardless of how confident the AI is.

---

## 2. Who Would Use This System?
This prototype is designed for:
* **Site Reliability Engineers (SREs) & DevOps Teams**: Who want to explore how AI can help diagnose server issues faster by scanning logs and comparing current symptoms with past incident databases.
* **SRE Platform Architects**: Who are researching security boundaries, ensuring that AI agents remain strictly read-only and cannot execute direct infrastructure commands without a deterministic guardrail.
* **AI Developers**: Looking for reference patterns on how to build manual tool-calling agent loops using OpenAI/Groq API interfaces.

---

## 3. Honestly Stated Business Value
Vigil is a demonstration/prototype project and is **not production-ready software**. It does not deliver business outcomes like "reducing site downtime" or "improving service availability" directly. Instead, it provides value by demonstrating key architecture patterns:
1. **The Read-Only Agent Pattern**: Proves that you can leverage LLMs for complex diagnostics without giving them destructive write access.
2. **The Dual-Phase Safety Architecture**: Demonstrates the split between probabilistic reasoning (Phase 1, LLM diagnostics) and deterministic safety enforcement (Phase 2, Python validation gates).
3. **Retrieval-Augmented Generation (RAG) for Incident Matching**: Showcases how a vector database (PostgreSQL with `pgvector` and an HNSW index) can perform semantic lookup of historical incident tickets to guide active diagnoses.
4. **Human-in-the-Loop Integration**: Demonstrates a UI flow that presents the AI's diagnostic reasoning and structured metrics to a human operator for final authorization.
