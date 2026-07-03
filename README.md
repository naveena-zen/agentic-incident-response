# Vigil: Agentic AI + RAG for Autonomous Incident Response

Vigil is an autonomous, agentic AI incident response system designed as a working prototype to demonstrate the power of combining tool-augmented LLM reasoning, local retrieval-augmented generation (RAG) over historical incidents, and a deterministic safety policy. When a service threshold is breached, Vigil automatically coordinates a two-phase pipeline: Phase 1 engages a Groq-powered SRE agent running a hand-written execution loop that utilizes read-only diagnostic tools (logs, metrics, deploy history, and vector similarity search over pgvector) to diagnose the root cause, and Phase 2 passes this diagnostic hypothesis to a deterministic safety engine that executes automated mitigation actions (restarts, rollbacks) or alerts an on-call responder for manual approval.

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

## Architecture Overview
Vigil enforces a strict **Two-Phase Architecture** to guarantee safety and predictability in automated production actions:
1. **Phase 1: LLM-Based Diagnosis (Read-Only)**
   The agent is configured as a hand-written execution loop (messages -> tool definitions -> tool dispatch) using the Groq API. It is given strictly read-only tools: `get_metrics`, `get_logs`, `get_recent_deploys`, and `search_similar_incidents`. The agent cannot restart services or perform deployments. It exits when it yields a structured JSON object containing its technical hypothesis, confidence, and recommended action.
2. **Phase 2: Deterministic Python Safety Gate (Write Execution)**
   The terminal JSON from Phase 1 is passed to a pure Python module (`policy_engine.py`) containing no LLM code. The gate executes auto-mitigation actions only if the following strict conditions are met:
   - The diagnosis confidence score is $\ge 80\%$.
   - The target service is explicitly listed in `ACTION_ALLOWLIST` (e.g. `checkout-api`).
   - The service is NOT `local-host` (which is never auto-actioned).
   Any failure to meet these parameters blocks the auto-action and falls back to a SMTP/logging page.

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
