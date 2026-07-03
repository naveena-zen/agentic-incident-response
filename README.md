# Vigil: Agentic AI + RAG for Autonomous Incident Response

Vigil is an autonomous, agentic AI incident response system designed as a working prototype to demonstrate the power of combining tool-augmented LLM reasoning, local retrieval-augmented generation (RAG) over historical incidents, and a deterministic safety policy. When a service threshold is breached, Vigil automatically coordinates a two-phase pipeline: Phase 1 engages a Groq-powered SRE agent running a hand-written execution loop that utilizes read-only diagnostic tools (logs, metrics, deploy history, and vector similarity search over pgvector) to diagnose the root cause, and Phase 2 passes this diagnostic hypothesis to a deterministic safety engine that executes automated mitigation actions (restarts, rollbacks) or alerts an on-call responder for manual approval.

---

## Architecture Flow

The workflow transitions from metrics collection to deterministic threshold checks, LLM-based RAG diagnosis, and deterministic safety-gated execution.

```
+----------------------------------------------------------------------------+
|                          1. METRICS ENGINE (Background)                    |
|  - psutil collects host stats. Synthetic loops simulate APIs.              |
|  - Check thresholds: latency > 500ms | error > 5% | CPU > 85%              |
+-------------------------------------+--------------------------------------+
                                      |
                            [Threshold Breach]
                                      |
+-------------------------------------v--------------------------------------+
|                       2. RULE ENGINE & INVESTIGATION LOCK                  |
|  - Checks service cooldown (120s) and investigation_in_progress flag       |
|  - Sets lock = True, inserts Incident in DB, fires async investigation.    |
+-------------------------------------+--------------------------------------+
                                      |
                                  [Fires]
                                      |
+-------------------------------------v--------------------------------------+
|                     3. PHASE 1: AGENTIC AI & RAG DIAGNOSIS                 |
|  - Hand-written loop executing OpenAI-compatible tool calls.               |
|  - LLM is strictly READ-ONLY. Allowed tools:                               |
|    - get_metrics(svc)                                                      |
|    - get_logs(svc)                                                         |
|    - get_recent_deploys(svc)                                               |
|    - search_similar_incidents(query)  <-- RAG via local sentence-trans     |
|                                           & pgvector HNSW index.           |
|  - Loop terminates with JSON: Hypothesis, Confidence, Recommended Action.  |
+-------------------------------------+--------------------------------------+
                                      |
                             [Outputs JSON]
                                      |
+-------------------------------------v--------------------------------------+
|                    4. PHASE 2: DETERMINISTIC SAFETY POLICY                 |
|  - Pure Python logic (No LLM calls). Checks conditions:                    |
|    - Confidence >= 80%                                                     |
|    - Service in Allowlist (e.g. checkout-api)                              |
|    - Service is NOT local-host                                             |
|                                                                            |
|          [Passed]                                    [Blocked]             |
|             |                                            |                 |
|    +--------v--------+                         +---------v---------+       |
|    |  AUTO-RESOLVE   |                         |    PAGE HUMAN     |       |
|    | Restart/Rollback|                         | SMTP Log Fallback |       |
|    +--------+--------+                         +---------+---------+       |
|             |                                            |                 |
|     (Adds to RAG KB)                                     |                 |
|             |                                    [Human Click Approve]     |
|             |                                            |                 |
|             |                                  +---------v---------+       |
|             |                                  | MANUAL MITIGATE   |       |
|             |                                  | Executes action   |       |
|             |                                  +---------+---------+       |
|             +-----------------------+--------------------+                 |
|                                     |                                      |
|                             [Finally Block]                                |
|                                     |                                      |
|                        +------------v------------+                         |
|                        |   RELEASE LOCK & STAMP  |                         |
|                        | Sets lock=False, cooldown|                        |
|                        +-------------------------+                         |
+----------------------------------------------------------------------------+
```

---

## Detailed Working of the System

### 1. Active Anomaly Triggers
A background scheduler (`metrics_loop.py`) evaluates service metrics every 5 seconds. If latency, error rates, or CPU percentages breach critical thresholds, Vigil initiates an investigation. To prevent duplicate concurrent analyses, Vigil uses the database column `investigation_in_progress` on the `Service` table as a distributed lock. If locked, the breach is skipped. If free, the lock is acquired, and Phase 1 is scheduled.

### 2. Phase 1: Hand-Written Tool-Calling Loop (Agentic AI)
Phase 1 (`phase1_agent.py`) coordinates the diagnosis. Instead of relying on agent frameworks like LangChain, the investigation logic runs inside a manual loop that invokes the Groq API.
* **Read-Only Constraints**: The LLM is provided with strictly read-only diagnostics (`get_metrics`, `get_logs`, `get_recent_deploys`, and `search_similar_incidents`). Commands that change state (e.g. `restart_service`) are physically absent from the tool definitions, ensuring the model cannot execute destructive commands.
* **Retrieval-Augmented Generation (RAG)**: The `search_similar_incidents` tool converts incident symptoms into a vector embedding using a local `all-MiniLM-L6-v2` transformer model. It then performs a cosine similarity query on the PostgreSQL `past_incidents` table using the `pgvector` extension and an HNSW index, returning the top 3 matches to help ground the diagnosis.
* **Structured Output**: The agent executes iterations until it stops requesting tool calls and returns a structured JSON payload with its root-cause hypothesis, confidence level (0-100), and recommended action.

### 3. Phase 2: Deterministic Policy Gate (Safety)
The policy engine (`policy_engine.py`) takes the JSON output and acts as the gatekeeper.
* **Auto-Action Decision**: It checks if confidence is $\ge 80\%$, if the service is in the `ACTION_ALLOWLIST` (currently `checkout-api`), and if the service is simulated.
  - **If Met**: It executes the python mitigation function (e.g., restarts the service or rolls back the latest deployment version in the DB). It then embeds this newly resolved case and adds it back to the RAG knowledge base so the agent learns from its success.
  - **If Failed**: It falls back to paging a human through email/logging and marks the incident as `paged`.
* **Guaranteed Release**: To avoid deadlock conditions, the lock release (`investigation_in_progress = False`) and the 120-second post-investigation cooldown timestamp are registered inside a `finally` block, guaranteeing execution even on system exceptions.

### 4. Human-in-the-Loop Action
For incidents that fail the auto-action gate, the React frontend displays a reasoning timeline showing the logs, metrics, RAG matches, confidence level, and an **"Approve Action"** button. Clicking this button triggers the `/api/incidents/{incident_id}/approve` endpoint, bypassing the LLM entirely and executing the Python mitigation action directly.

---

## How to Run

### Prerequisite: Database Setup
Ensure you have a PostgreSQL database running with the `pgvector` extension enabled.
Connection URL settings should match the environment configuration below.

### Environment Configuration
Create a `.env` file in the project root containing the following configurations (refer to `.env.example` for a template):
```ini
# Postgres Database Connection
DB_HOST=localhost
DB_PORT=5432
DB_NAME=vigil
DB_USER=postgres
DB_PASSWORD=postgres

# LLM API Settings (Groq)
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.1-8b-instant

# JWT Secret
JWT_SECRET=supersecretjwtsigningkey_changeme
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=480

# SMTP Paging Settings (Optional — falls back to log if not configured)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_gmail@gmail.com
SMTP_PASSWORD=your_gmail_app_password
ALERT_EMAIL_TO=your_alert_recipient@example.com
```

### Running Locally

#### 1. Setup Backend
Install Python dependencies (requires Python 3.10+):
```bash
pip install -r requirements.txt
```

Launch the Uvicorn development server:
```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```
This automatically initializes the Postgres schema, runs the seed script (populating 10 historical incidents with local embeddings), and starts the background metrics loop.

#### 2. Setup Frontend
Navigate to the frontend directory:
```bash
cd frontend
npm install
```

Start the React development server:
```bash
npm start
```
The React monitoring dashboard will be available at `http://localhost:3000`.

### Running via Docker Compose
To spin up the entire stack (PostgreSQL, pgvector, and the FastAPI backend) with a single command:
```bash
docker-compose up --build
```
*(Note: You will still need to run the React frontend locally at `http://localhost:3000` via npm or build the bundle).*

---

## Tech Stack
- **Backend Framework**: FastAPI (asyncio)
- **Database ORM**: SQLAlchemy (asyncpg)
- **Database Engine**: PostgreSQL with `pgvector`
- **Embedding Model**: `sentence-transformers` (`all-MiniLM-L6-v2` loaded locally, no external API)
- **LLM Engine**: Groq API (`llama-3.1-8b-instant` / `llama-3.3-70b-versatile`)
- **Frontend Dashboard**: React.js with Chart.js line charts
- **Metrics & Logging**: `psutil` (host metrics) and `prometheus-client` (:8000/metrics)
- **Containerization**: Docker & Docker Compose

---

## Features
- **Deterministic Trigger**: Threshold rule engine (latency > 500ms, error rate > 5%, or host CPU > 85%) fires investigations.
- **Investigation Lock**: Distributed lock flag (`investigation_in_progress`) prevents redundant concurrent diagnostic runs on the same service.
- **Local RAG Integration**: Cosine similarity search over past incidents via local SentenceTransformers model.
- **Auto-Mitigation**: Automatically performs restarts or mark rollbacks in the DB if the safety policy is satisfied.
- **Human-in-the-Loop Approval**: Incident dashboard with detailed diagnostic timelines and an "Approve Action" button for paged incidents.
- **JWT Authentication**: Protected REST endpoints, including secure debug routes.
- **Prometheus Metrics**: High-availability monitoring indicators on endpoint latencies and incident rates.

---

## Safety Design
- **Separation of Concerns**: Diagnostic reasoning is separated from action execution to eliminate LLM hallucinations leading to wild infrastructure commands.
- **Locked Cooldowns**: Enforces a 120-second cooldown window post-investigation to prevent rule-engine cascade loops.
- **Guaranteed Lock Release**: Lock states are released in a `try/finally` block to ensure a single crash (e.g., SMTP timeout) doesn't permanently lock down a service.

---

## Known Limitations
- **Simulated Infrastructure**: Service rollbacks and restarts are mocked on memory/db states rather than executing on live cloud infrastructure.
- **Tokens-Per-Minute Quotas**: High metric and log trace sizes can hit free-tier Groq API TPM limits under parallel multi-service anomalies.
- **Host Metrics Caveat**: Host CPU and memory measurements utilize the server's local environment where the Python process is executed.
