# Bug List: Vigil Incident Response Agent

This document lists the actual bugs identified in the Vigil codebase. The issues are categorized by priority, referencing the files and lines where they occur.

---

## 1. Critical Priority

### Bug C1: Event-Loop Blocking ML Inference Call
* **File**: [`phase1_agent.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/phase1_agent.py#L217-L218)
* **Function**: `_search_similar`
* **Issue**:
  The RAG search function invokes `vec = embed(query_text)` synchronously. The underlying `embed` function (`seed.py` lines 39-40) loads a local `sentence-transformers` model on the CPU to generate vector dimensions.
  
  Because this async endpoint tool is run on the main thread without a separate worker process or executor (unlike the implementation in `policy_engine.py` line 335), it blocks the single-threaded FastAPI event loop. Under concurrent traffic or multi-service alerts, this will freeze the entire backend server.

---

## 2. High Priority

### Bug H1: Non-Atomic Lock Verification (Race Condition)
* **File**: [`metrics_loop.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/metrics_loop.py#L211-L231)
* **Function**: `_rule_engine_check`
* **Issue**:
  The threshold check queries and updates lock states across separate transactions:
  ```python
  svc_row = await db.get(Service, service)
  if svc_row.investigation_in_progress:
      return
  
  # Lock is written in a separate transaction block
  incident_id = uuid.uuid4()
  svc_row.investigation_in_progress = True
  await db.commit()
  ```
  In multi-worker environments (e.g. running Uvicorn with `--workers` > 1 or multiple container instances), two workers could read the state as `False` at the same time, proceed, write the locks, and trigger duplicate diagnostic agents for the same threshold breach.

### Bug H2: Missing Persistence of AI Recommended Action
* **File**: [`database.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/database.py#L168-L201) (ORM models) and [`policy_engine.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/policy_engine.py#L293) (`execute_approved_action`)
* **Issue**:
  The `Incident` table ORM model does not have a column to store the recommended action string output by Phase 1 (`recommended_action`).
  
  As a result, when the operator clicks "Approve action" on the dashboard, the backend approval handler has to guess what action was recommended by looking for substring occurrences inside the raw diagnostic notes:
  ```python
  if "rollback" in (inc.action_detail or "").lower() or "rollback" in hypothesis.lower():
      result = await rollback_deploy(service, ...)
  else:
      result = await restart_service(service, ...)
  ```
  If the hypothesis notes contain complex SRE vocabulary or fail to match these strings, the system defaults to restarting, potentially executing the wrong mitigation action.

---

## 3. Medium Priority

### Bug M1: Plain-Text Password Check Bypasses Secure Hash Cryptography
* **File**: [`auth.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/auth.py#L39-L46)
* **Function**: `verify_password`
* **Issue**:
  The password handler checks for plain-text password equality before using the bcrypt framework:
  ```python
  if plain == DEMO_PASS:
      return True
  try:
      return pwd_context.verify(plain, hashed)
  ```
  Since the application is configured to authenticate a single demo user (`admin`), the bcrypt path is never used. This stores and compares the password variables in memory as plaintext strings.

### Bug M2: Synchronous SMTP Network Block in send_page_email
* **File**: [`notifications.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/notifications.py#L62-L71)
* **Function**: `_send_sync`
* **Issue**:
  While the async wrapper uses `asyncio.to_thread` to spin up a worker thread, the underlying implementation uses `smtplib.SMTP`. If the outgoing connection is slow, blocks port 587, or is misconfigured, the connection hangs for up to 10 seconds. Under heavy load, this can quickly exhaust the thread pool worker slots, delaying all queued alert processing.

---

## 4. Low Priority

### Bug L1: Inconsistent Model Name Fallbacks
* **File**: [`phase1_agent.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/phase1_agent.py#L46) and [`docker-compose.yml`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/docker-compose.yml#L35)
* **Issue**:
  The model fallback configuration is inconsistent across modules. In `phase1_agent.py`, the default model variable points to `llama-3.3-70b-versatile`. In `docker-compose.yml`, it points to `llama-3.1-8b-instant`. This can result in different behaviors depending on how the backend was launched.

### Bug L2: Wildcard CORS Permissiveness
* **File**: [`main.py`](file:///c:/Users/navee/OneDrive/Desktop/Vigil/main.py#L146-L152)
* **Issue**:
  The FastAPI backend sets the origins wildcard parameter to allow all connections:
  ```python
  allow_origins=["*"]
  ```
  This allows third-party websites to execute API commands and request metrics from a local Vigil backend via cross-origin requests, creating a Cross-Site Request Forgery (CSRF) vulnerability if the service runs on a developer's machine or corporate intranet.
