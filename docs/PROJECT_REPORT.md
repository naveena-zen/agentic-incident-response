# Project Technical Report: Vigil Autonomous Incident Response Agent

This report details the actual implementation of Vigil based on a direct line-by-line review of the codebase. It covers features, module structures, technical stacks, database schema, APIs, authentication, security, performance, and scalability.

---

## 1. Executive Summary
Vigil is a working prototype of an autonomous incident response system. The core design aims to demonstrate the feasibility of a **two-phase architecture** where an LLM is used strictly for read-only diagnosis (Phase 1), and a deterministic safety policy written in standard Python executes actions or pages human operators (Phase 2). 

While the system is functional as a local prototype with simulated services, its infrastructure actions are mocked (simulated in-memory or database updates), and it utilizes in-memory states for cooldowns and anomaly flags, limiting its immediate production readiness.

---

## 2. Feature List

### Implemented and Working
* **Background Monitoring & Metrics Generation**: Evaluates real host CPU and memory via `psutil` and generates simulated metrics for `checkout-api`, `auth-api`, and `payments-api` at regular intervals (`metrics_loop.py` lines 246-309).
* **Threshold Rule Engine**: Checks for threshold breaches (latency > 500ms, error rate > 5%, or host CPU > 85%) and fires investigations (`metrics_loop.py` lines 174-201).
* **Investigation Locking & Cooldowns**: Uses a database column lock (`Service.investigation_in_progress`) to prevent duplicate analysis runs and an in-memory timer to enforce a 120-second cooldown period between investigations (`metrics_loop.py` lines 203-231).
* **AI Diagnostic Loop (Phase 1)**: Runs an iterative loop using an OpenAI-compatible client (Groq) with read-only tools to fetch logs, metrics, deployments, and past incidents (`phase1_agent.py` lines 279-395).
* **Semantic RAG Search**: Uses a local `all-MiniLM-L6-v2` transformer model to embed symptoms and searches for similar past incidents in PostgreSQL using `pgvector` and an HNSW cosine similarity index (`phase1_agent.py` lines 217-243).
* **Deterministic Action Policy (Phase 2)**: Checks confidence (threshold 80) and service allowlist (restricted to `checkout-api`) to execute mock restarts/rollbacks or page a human (`policy_engine.py` lines 163-260).
* **Human-in-the-Loop Approval**: Allows administrators to approve pending actions from the console UI, which directly calls Python actions without re-engaging the LLM (`main.py` lines 383-408; `policy_engine.py` lines 263-323).
* **JWT Authentication**: Implements bearer token validation using python-jose (`auth.py` lines 63-76).
* **Real-time Dashboard UI**: A React application featuring real-time sparkline charts, an incident feed, and simulated anomaly injection buttons (`frontend/src/App.js`).
* **Prometheus Metrics Exposer**: Exposes `/metrics` endpoint with counters for requests, incident rates, and agent latencies (`main.py` lines 56-61, 154-167).

### Implemented but Incomplete or Broken
* **SMTP Email Paging**: While functional, it will fail to send real emails out-of-the-box because it defaults to mock SMTP credentials in `.env`. When configured, it relies on standard `smtplib.SMTP`, which will fail on networks blocking port 587 (`notifications.py` lines 62-71).
* **Bcrypt Password Fallback**: In `auth.py` (lines 39-46), the password verification checks plain-text matching for `DEMO_PASS` before checking the hashed value. Because the only user is the demo admin, the bcrypt hashing path is never active in practice.
* **Lack of Persisted Recommendation Field**: The `Incident` ORM model does not have a field to store the AI agent's actual recommended action (`recommended_action`). Consequently, the human approval endpoint must infer the action from raw strings inside `action_detail` or the hypothesis (`policy_engine.py` line 293).

### Referenced but NOT IMPLEMENTED
* **Real Infrastructure Execution**: Restarts and rollbacks do not interact with actual cloud infrastructure (e.g., Kubernetes, systemd, or AWS API). Restarts clear an in-memory anomaly flag (`policy_engine.py` line 55) and rollbacks only update a DB record status (`policy_engine.py` line 81).

---

## 3. Module-by-Module Analysis

* **`main.py`**: The entry point of the FastAPI application. Sets up lifespan events (schema preparation, seeding, scheduler ticks, anomaly injector), mounts CORS and Prometheus middleware, and registers the API routes.
* **`auth.py`**: Handles user login, JWT token generation, and the `get_current_user` dependency used to protect routes.
* **`database.py`**: Sets up the SQLAlchemy database engine (`postgresql+asyncpg`) and defines the models (`Service`, `ServiceMetric`, `ServiceLog`, `RecentDeploy`, `Incident`, `PastIncident`).
* **`metrics_loop.py`**: Coordinates metric aggregation. Runs the scheduler ticks to update database fields, manages the mock anomaly injection loop, and executes the threshold checks.
* **`phase1_agent.py`**: Coordinates the diagnostic agent. Runs the OpenAI-compatible chat completion loop and provides the read-only tool implementations (`get_metrics`, `get_logs`, `get_recent_deploys`, `search_similar_incidents`).
* **`policy_engine.py`**: Implements Phase 2 safety policy gates, mock execution triggers, human-in-the-loop approvals, and RAG indexing tasks.
* **`notifications.py`**: Compiles dynamic HTML templates and sends SMTP messages, falling back to server logging if SMTP is not configured.
* **`seed.py`**: Populates the Postgres database on startup with 10 historical incidents, vector embeddings generated via `sentence-transformers`, and deployment logs.
* **`smoke_test.py`**: A CLI test script that validates basic API response structures and JWT authorization.
* **`frontend/src/App.js`**: The main React interface containing state management for polling services, logs, and incidents, along with user forms and layout structures.
* **`frontend/src/api.js`**: Centralizes API HTTP fetch requests with automated Authorization header injection.
* **`frontend/src/MiniChart.js`**: Sparkline chart component utilizing Chart.js rendering within canvas elements.

---

## 4. Technology Stack
Verified directly from imports and dependency configurations:
* **Web/API Engine**: FastAPI (`0.111.0`) running on Uvicorn (`0.30.1`).
* **Database & ORM**: SQLAlchemy (`2.0.30`) with `asyncpg` (`0.29.0`) for async connections, and `psycopg2-binary` (`2.9.9`) for synchronous/sync operations (such as migration/schema updates).
* **Vector Extensions**: `pgvector` (`0.3.2`) for database similarity metrics.
* **Embeddings & ML**: `sentence-transformers` (`3.0.1`) running on CPU-based `torch` (`2.3.1`) with `numpy` (`1.26.4`).
* **LLM Integration**: `openai` (`1.35.3`) using Groq endpoints.
* **Authentication**: `python-jose[cryptography]` (`3.3.0`) and `passlib[bcrypt]` (`1.7.4`).
* **Scheduling**: `apscheduler` (`3.10.4`).
* **System Metrics**: `psutil` (`5.9.8`).
* **Monitoring**: `prometheus-client` (`0.20.0`).
* **Frontend**: React (`19.2.7`) with Chart.js (`4.5.1`) and `react-chartjs-2` (`5.3.1`).

---

## 5. Database Analysis
The schema operates on PostgreSQL with `pgvector` activated.

```
       +------------------+         +-----------------------+
       |     services     |         |    service_metrics    |
       +------------------+         +-----------------------+
       | name (PK)        |<--------| service (FK-implied)  |
       | cpu_pct          |         | timestamp             |
       | memory_pct       |         | is_anomaly            |
       | latency_ms       |         +-----------------------+
       | error_rate       |
       | inv_in_progress  |         +-----------------------+
       | last_inv_id      |         |     service_logs      |
       +------------------+         +-----------------------+
                                    | service (FK-implied)  |
       +------------------+         | level                 |
       |  recent_deploys  |         | message               |
       +------------------+         +-----------------------+
       | service (indexed)|
       | version          |         +-----------------------+
       | deployed_at      |         |       incidents       |
       +------------------+         +-----------------------+
                                    | id (PK)               |
       +------------------+         | service (indexed)     |
       |  past_incidents  |         | status (indexed)      |
       +------------------+         | root_cause_hypothesis |
       | id (PK)          |         | confidence            |
       | title            |         | phase1_trace          |
       | embedding (v384) |         | action_detail         |
       +------------------+         +-----------------------+
```

### Table Details
1. **`services`**: Stores service health summaries and active lock states. PK is `name` (String(64)). Holds the `investigation_in_progress` lock.
2. **`service_metrics`**: Metrics history. PK is UUID. Columns include `service` (indexed), `timestamp` (indexed), metrics, and `is_anomaly`.
3. **`service_logs`**: Time-series log rows. PK is UUID. Columns: `service` (indexed), `timestamp` (indexed), `level`, `message`, and `is_anomaly_related`.
4. **`recent_deploys`**: Deployment events. PK is UUID. Columns: `service` (indexed), `version`, `deployed_at`, `deployed_by`, `status`, and `notes`.
5. **`incidents`**: Incident records. PK is UUID. Columns: `service` (indexed), `status` (Enum `incidentstatus`, indexed), AI outputs, and timestamps.
6. **`past_incidents`**: RAG Knowledge Base. PK is UUID. Columns: `title`, `root_cause`, `action_taken`, `embedding` (`Vector(384)`). 
   * **Index**: Cosine similarity HNSW index named `idx_past_incidents_embedding` on the `embedding` column using `vector_cosine_ops` (`database.py` lines 233-237).

---

## 6. API Analysis
FastAPI routes are defined in `main.py` with custom query limits.

| Route | Method | Protected | Request Validation | Response Validation |
| :--- | :--- | :--- | :--- | :--- |
| `/health` | GET | No | None | None |
| `/metrics` | GET | No | None | None |
| `/api/auth/login` | POST | No | `LoginRequest` (Pydantic model) | `Token` (Pydantic model) |
| `/api/services` | GET | Yes | Query (Depends(get_db)) | None |
| `/api/metrics/{service}` | GET | Yes | Limit check (1-500) | None |
| `/api/logs/{service}` | GET | Yes | Limit check (1-200) | None |
| `/api/deploys/{service}` | GET | Yes | Limit check (1-20) | None |
| `/api/incidents` | GET | Yes | Limit check (1-100) | None |
| `/api/incidents/{incident_id}` | GET | Yes | Manual UUID parse validation | None |
| `/api/incidents/{incident_id}/approve` | POST | Yes | Manual UUID parse validation | None |
| `/api/past-incidents` | GET | Yes | Limit check (1-100) | None |
| `/api/debug/trigger-anomaly` | POST | Yes | Query param extraction | None |
| `/api/debug/trigger-investigation`| POST | Yes | Query param extraction | None |

---

## 7. Authentication & Authorization Analysis
* **Mechanism**: JWT Token Authentication.
* **Implementation**: The route dependency `Depends(get_current_user)` resolves tokens (`auth.py` lines 63-76).
* **Verification**:
  * Decodes token via `jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])`.
  * Extracts the subject (`sub`) field and verifies it matches `DEMO_USER` ("admin").
  * Rejects the request with HTTP 401 if validation fails.
* **Access Control**: Authorization is binary: you are either authenticated as the single admin user or blocked. There is no Role-Based Access Control (RBAC) implemented.

---

## 8. Security Review
* **Hardcoded Credentials**:
  * Default database password (`postgres`) and JWT Secret (`supersecretjwtsigningkey_changeme`) are fallback variables in code (`database.py` line 54; `auth.py` line 18) and stored in `.env`.
  * The `.env` file contains a plain-text xAI/Groq API key (`gsk_...`), presenting an exposure hazard if committed to a public repository.
* **CORS Wildcard Configuration**:
  * In `main.py` lines 146-152, the application allows all origins (`allow_origins=["*"]`). In a production setting, this exposes the API to Cross-Origin Resource Sharing exploits, allowing third-party sites to trigger alerts or approve mitigations on your infrastructure if the API runs inside a corporate network.
* **Lack of Rate Limiting**:
  * No rate limits exist on `/api/auth/login` or the debug endpoints, making it susceptible to credential brute-forcing or denial-of-service vectors.

---

## 9. Performance Review
* **CPU-Blocking Embedded Vector Calculations**:
  * In `phase1_agent.py` line 218, the tool `_search_similar` calls `vec = embed(query_text)` synchronously. This is a CPU-heavy execution of the SentenceTransformer model on the main thread, blocking FastAPI's single-threaded async event loop for all concurrent requests.
* **Synchronous DNS/Network Fallback**:
  * While `send_page_email` is run in an executor thread via `asyncio.to_thread` (`notifications.py` line 93), it relies on synchronous `smtplib`. In the case of networking failures, it will hold the thread pool worker for up to 10 seconds before throwing an error.
* **Missing Databases Indexes**:
  * No index is applied to columns such as `is_anomaly` (`service_metrics` table) or `is_anomaly_related` (`service_logs` table), which could result in slow query times as logs and historical metrics compile.

---

## 10. Scalability Review
* **In-Memory State Dependency**:
  * The active anomalies registry (`anomaly_state`) and the cooldown timer (`_last_investigation_finished`) are local dictionaries inside `metrics_loop.py`. If multiple instances of the backend are deployed behind a load balancer, they will have separate anomaly states and cooldowns. This will cause conflicting metric generation and duplicate investigations.
* **Distributed Lock Race Condition**:
  * The rule check (`metrics_loop.py` lines 211-231) checks `svc_row.investigation_in_progress` inside a read transaction, then updates it to True in a separate write. Under concurrent environments, two threads or backend instances could read the state simultaneously, see it as False, and both trigger separate AI investigations, rendering the lock ineffective.
* **Monolithic Embedding Model Memory Overhead**:
  * Running `sentence-transformers` locally inside the FastAPI backend worker means each process must load the model files into memory. In multiple worker configurations (e.g., Uvicorn workers > 1), memory utilization scales linearly, which can crash low-memory nodes.
