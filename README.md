# Vigil: Autonomous Incident Response Agent

Vigil is an autonomous incident response prototype. It uses a **two-phase architecture** that splits probabilistic diagnostic reasoning from deterministic safety controls. Under abnormal conditions, Vigil executes a read-only agent loop (Phase 1) using Groq API model instances to isolate issues, then hands off findings to a deterministic Python policy engine (Phase 2) for automated mitigation (such as simulated service restarts or rollbacks) or escalation (human paging).

---

## How to Run

### Prerequisite: Database Setup
Ensure a PostgreSQL database instance is running with `pgvector` enabled. For Windows users, a script is provided at [`install_pgvector.ps1`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/install_pgvector.ps1) to copy extension binaries. In Docker environments, the Compose file automatically configures a PG database with pgvector.

### Option 1: Running Locally (Backend & Frontend)

1. **Setup Backend**:
   Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
   Start the FastAPI development server:
   ```bash
   python -m uvicorn main:app --host 0.0.0.0 --port 8000
   ```
   *Note: This automatically prepares the database schema, runs database seeding (adding 10 historical incidents with embeddings), starts the metrics loop scheduler, and launches the server.*

2. **Setup Frontend**:
   Navigate to the frontend folder, install dependencies, and run:
   ```bash
   cd frontend
   npm install
   npm start
   ```
   *The React console will open at `http://localhost:3000`.*

3. **Verify Installation**:
   Verify the endpoints are running by executing the smoke test script:
   ```bash
   python smoke_test.py
   ```

### Option 2: Running via Docker Compose
Build and start the containerized PostgreSQL and FastAPI backend:
```bash
docker-compose up --build
```
*Note: The frontend must still be run locally at `http://localhost:3000` using Node/NPM.*

---

## Features 
* **Background Monitoring Tick**: Regularly records real CPU and memory metrics for the local host via `psutil` and generates simulated metrics for internal services (`metrics_loop.py` lines 246-309).
* **Threshold Alarm Engine**: Triggers an alert when metrics exceed thresholds (latency > 500ms, error rate > 5%, or CPU > 85%) and reserves a service-level lock (`Service.investigation_in_progress`) to block duplicate runs (`metrics_loop.py` lines 174-201).
* **AI Diagnostics Loop (Phase 1)**: Iterates a read-only OpenAI-compatible (Groq) tool calling agent to examine server context (`phase1_agent.py` lines 279-395).
* **Local Semantic RAG**: Matches incident symptoms to historical documents in PostgreSQL using a local SentenceTransformer (`all-MiniLM-L6-v2`) and a pgvector HNSW index (`phase1_agent.py` lines 217-243).
* **Action Policy Gates (Phase 2)**: Evaluates agent output deterministic policies (rules, confidence, allowlist) to trigger mock recoveries or page handlers (`policy_engine.py` lines 163-260).
* **Interactive SRE Console**: Presents incident details, metrics, logs, and a timeline. Awaiting escalations can be resolved using the "Approve Action" console button, which directly bypasses the LLM (`frontend/src/App.js`).
* **Prometheus Instrumentation**: Exposes metrics counters for route performance and incident monitoring on `/metrics` (`main.py` lines 56-61).
* **JWT Access Security**: Secures endpoints against unauthorized modifications (`auth.py`).

---

## Usage
1. **Login**: Access the web UI at `http://localhost:3000` and login using credentials defined by `DEMO_USERNAME` / `DEMO_PASSWORD` (defaults: `admin` / `vigil2025`).
2. **Injecting Anomaly**: Click the **Inject anomaly** button on the top-right header to manually inject a `high_latency` error into the simulated `checkout-api`.
3. **Tracking Incident**: Observe the logs, metrics, and incoming investigations in the **Incidents** section. If the issue affects `checkout-api` and confidence is $\ge 80\%$, the engine auto-mitigates and displays the results. If it affects any other service or has low confidence, it waits for human approval.
4. **Manual Approval**: For paged events, review the reasoning timeline and click the **Approve action** button to resolve it.

---

## Folder Structure
```
.
├── auth.py                  # JWT credential authentication checks
├── database.py              # PostgreSQL database initialization & SQLAlchemy ORM mapping
├── Dockerfile               # Build configuration for running the FastAPI application
├── docker-compose.yml       # Configuration for deploying Postgres DB and API service
├── install_pgvector.ps1     # Powershell installation script for pgvector (Windows local)
├── main.py                  # FastAPI application entry points, endpoints & scheduler tasks
├── metrics_loop.py          # Background metric collection & threshold rule evaluator
├── notifications.py         # SMTP email paging and fallback logging mechanisms
├── phase1_agent.py          # SRE agent tool-calling loop utilizing the Groq SDK
├── policy_engine.py         # Safety allowlists, mock actions, and RAG compilation
├── requirements.txt         # List of Python dependencies
├── seed.py                  # Seeding script for services, deploys, and vector data
├── smoke_test.py            # Local endpoint integration verification test script
├── docs/                    # Architecture diagrams, reports, assessment, and roadmaps
└── frontend/                # React dashboard frontend project files
    ├── package.json         # Node.js dependencies
    └── src/                 # React component source code
```

---

## Architecture
Vigil uses a decoupled pipeline to prevent LLM hallucinations from causing uncontrolled infrastructure actions:
1. **Metrics Engine & Rules**: Triggers incidents when metric thresholds are breached and sets a database-level lock.
2. **Phase 1 (Diagnostic Agent)**: Explores diagnostics using strictly read-only tools (`get_metrics`, `get_logs`, `get_recent_deploys`, `search_similar_incidents`). No action-executing tools are defined in the LLM context.
3. **Phase 2 (Safety Policy)**: Evaluates the LLM's diagnostic JSON output using a deterministic Python engine. If safety criteria (service allowlist, confidence threshold) are satisfied, it executes a simulated action. Otherwise, it sends a page to a human operator.
4. **Human Review**: A human operator can review the timeline on the React console and manually approve the recovery action.

For detailed sequence diagrams, system component maps, and data flow visualizations, see the [Architecture Diagrams Document](file:///c:/Users/navee/OneDrive/Desktop/Vigil/docs/ARCHITECTURE_DIAGRAMS.md).

---

## Future Enhancements
* Transition mock actions into actual provider integration (e.g. AWS ECS/EKS task restarts).
* Replace in-memory anomaly and cooldown variables with distributed storage models (e.g. Redis).
* Eliminate CPU-blocking calls in the API event loop by executing embeddings calculations inside thread executors or microservices.
* Implement role-based authorization to secure administrative API routes.

---

## Contribution Guide
1. **Fork the Repository**: Ensure changes are committed to functional feature branches.
2. **Linting & Formatting**: Follow pep8 conventions for python code.
3. **Dependency Injection**: Add new backend packages directly to `requirements.txt`.
4. **Test Coverage**: Run `smoke_test.py` to ensure core endpoints operate successfully before merging.
