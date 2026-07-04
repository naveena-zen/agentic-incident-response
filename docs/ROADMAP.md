# Product Roadmap: Vigil Incident Response Agent

This roadmap outlines targeted improvements to transition the Vigil prototype into a secure, scalable, and production-ready system. The milestones are directly mapped to architectural and security gaps identified in active code.

---

## 1. Immediate Improvements (Focus: Critical & High Priority Bugs)
* **Resolve Event-Loop Blocking ML Operations**:
  * *Action*: Update `phase1_agent.py` line 218 to run the `embed` lookup inside an executor thread (`await asyncio.get_event_loop().run_in_executor(None, embed, query_text)`) or migrate to an external async embedding API to prevent blocking the web app event loop.
* **Fix the Investigation Lock Race Condition**:
  * *Action*: Refactor `metrics_loop.py` lines 211-231 to use atomic transactions with PostgreSQL locking syntax (e.g., `SELECT ... FOR UPDATE` via SQLAlchemy) or implement a distributed locking token via Redis to prevent concurrent workers from spawning duplicate agent runs.
* **Persist Recommended Actions in the Database Schema**:
  * *Action*: Add a `recommended_action` string/enum column to the `Incident` ORM model in `database.py`. Update `run_phase1` to persist this field directly, and refactor `execute_approved_action` to read this column rather than performing string checks on diagnostic text.
* **Eliminate Plain-Text Password Comparison**:
  * *Action*: Remove the raw string comparison check in `auth.py` lines 41-42. Store user credentials as secure bcrypt hashes and ensure all authentication passes through the `pwd_context.verify` cryptography path.

---

## 2. Short-Term Improvements (Focus: Security, Stability & Reliability)
* **Upgrade Alerts Notification Framework**:
  * *Action*: Replace the blocking `smtplib` code in `notifications.py` with an async library such as `aiosmtplib`, or hand off alert deliveries to an asynchronous background queue (e.g. Celery or ARQ).
  * *Integration*: Add built-in drivers for SRE paging endpoints like PagerDuty, Opsgenie, or Webhook integrations for Slack and Microsoft Teams.
* **Tighten API Security Controls**:
  * *Action*: Replace CORS wildcard configurations (`allow_origins=["*"]`) in `main.py` with domain allowlists loaded from environment configs. Add API rate-limiting middleware (such as `slowapi`) to protect backend services from Denial of Service (DoS) and login brute-forcing.
* **Vault-Managed Secret Configurations**:
  * *Action*: Remove default credentials and fallback Groq API keys from source files. Throw startup exceptions if critical secrets (`GROQ_API_KEY`, `JWT_SECRET`) are missing or set to insecure defaults.

---

## 3. Long-Term Improvements (Focus: Scaling & Production Deployments)
* **Production Orchestration Connectors**:
  * *Action*: Code concrete action drivers in `policy_engine.py` to replace simulated mocks. Build client handlers for Kubernetes namespaces (to restart deployments or rollback pod image hashes), systemd controllers, or AWS ECS/EKS SDK APIs.
* **Distributed State Architecture**:
  * *Action*: Replace in-memory dictionaries (`anomaly_state`, `_last_investigation_finished`) with a Redis database. This allows multiple, load-balanced FastAPI backend instances to coordinate cooldown timers and system stats.
* **Decoupled Machine Learning Inference**:
  * *Action*: Extract `sentence-transformers` calculations from the FastAPI web server. Deploy the model to an external, autoscaling inference server (e.g., Triton Inference Server, AWS SageMaker, or Hugging Face TGI) to limit backend container memory usage and separate compute workloads.
* **Multi-User RBAC (Role-Based Access Control)**:
  * *Action*: Introduce database tables for user accounts and write schema filters to assign read-only access (dashboard observers) vs. write-action permissions (SRE operators authorized to click "Approve action" or trigger anomalies).
