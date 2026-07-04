# Architecture Diagrams: Vigil Incident Response Agent

This document contains Mermaid diagrams mapping the actual structure and flow of the Vigil prototype codebase.

---

## 1. System Architecture Diagram
Shows the high-level boundaries between the user interface, backend application layers, database storage, and external AI providers.

```mermaid
graph TD
    User["SRE / Operator (Web Browser)"]
    
    subgraph LocalHost ["Local Machine / Docker Environment"]
        Frontend["React Web App (Port 3000)"]
        
        subgraph BackendApp ["FastAPI Backend (Port 8000)"]
            API["FastAPI Web Engine"]
            Scheduler["APScheduler Background Engine"]
            EmbModel["SentenceTransformers (all-MiniLM-L6-v2)"]
        end
        
        Database[("PostgreSQL DB (Port 5432)
        - Services
        - Metrics & Logs
        - Incidents
        - Past Incidents (pgvector)")]
    end
    
    Groq["External Groq Cloud API
    (Model: llama-3.1-8b-instant)"]

    User -->|Interacts| Frontend
    Frontend -->|HTTP Requests / JWT Auth| API
    Scheduler -->|Saves Metrics & Runs Rules| Database
    API -->|Async Queries| Database
    API -->|CPU-bound Embeddings| EmbModel
    API -->|Diagnostic Loops| Groq
```

---

## 2. Component Diagram
Shows how individual modules inside the Python backend interact with database tables.

```mermaid
graph TB
    subgraph Modules ["FastAPI Backend Modules"]
        Main["main.py
        (Lifespan, Routes, Middleware)"]
        Auth["auth.py
        (JWT, verify_password)"]
        Db["database.py
        (SQLAlchemy ORM models, get_db)"]
        Metrics["metrics_loop.py
        (Metrics tick, Anomaly Injector)"]
        Agent["phase1_agent.py
        (LLM read-only tool executor)"]
        Policy["policy_engine.py
        (Safety allowlists, lock release, RAG embed)"]
        Notify["notifications.py
        (SMTP / Log alert fallbacks)"]
    end

    subgraph DB_Tables ["PostgreSQL Database Tables"]
        T_Svc["services"]
        T_Met["service_metrics"]
        T_Log["service_logs"]
        T_Dep["recent_deploys"]
        T_Inc["incidents"]
        T_Past["past_incidents (pgvector)"]
    end

    Main -->|Protects routes via Depends| Auth
    Main -->|Fetches Database session| Db
    Main -->|Launches background tasks| Metrics
    Metrics -->|Writes metrics/logs| Db
    Metrics -->|Sets lock and triggers callback| Db
    Agent -->|Executes read-only tools| Db
    Policy -->|Checks allowlist & executes action| Db
    Policy -->|Emails on-call| Notify
    Policy -->|Releases investigation lock| Db
    T_Past -->|Embedding similarity search| Agent
```

---

## 3. Data Flow Diagram
Maps how metric and diagnostic data flow through the system.

```mermaid
graph TD
    Host["psutil Local Metrics"] -->|TickEvery 5s| MetricsLoop["metrics_loop.py"]
    Sim["Simulated Metrics Generator"] -->|TickEvery 5s| MetricsLoop
    
    MetricsLoop -->|INSERT| T_Met["service_metrics table"]
    MetricsLoop -->|INSERT| T_Log["service_logs table"]
    MetricsLoop -->|UPDATEhealth snapshot| T_Svc["services table"]
    
    MetricsLoop -->|Evaluate thresholds| Rules["Threshold Checker"]
    Rules -->|Breached & Unlocked| LockAcquire["Set investigation_in_progress=True"]
    LockAcquire -->|INSERT Incident| T_Inc["incidents table"]
    
    LockAcquire -->|Spawn Background Task| AgentPhase["phase1_agent.py (Phase 1)"]
    AgentPhase -->|Read diagnostic info| T_Met
    AgentPhase -->|Read diagnostic info| T_Log
    AgentPhase -->|Read diagnostic info| T_Dep["recent_deploys table"]
    AgentPhase -->|Read past cases| T_Past["past_incidents table"]
    
    AgentPhase -->|JSON report| SafetyGate["policy_engine.py (Phase 2)"]
    SafetyGate -->|Can Auto-Act| AutoAction["Execute Mock Action & Clear Anomaly"]
    AutoAction -->|UPDATE status=resolved_auto| T_Inc
    SafetyGate -->|Cannot Auto-Act| Escalation["SMTP Page & UPDATE status=paged"]
    Escalation -->|UPDATE status=paged| T_Inc
    
    UserApprove["Console human clicks Approve"] -->|POST /api/incidents/{id}/approve| ManualAction["Execute Mock Action & Clear Anomaly"]
    ManualAction -->|UPDATE status=approved| T_Inc
```

---

## 4. Sequence Diagram: Incident Investigation Flow
Tracks the operational sequence from anomaly detection to automated mitigation or human intervention.

```mermaid
sequenceDiagram
    autonumber
    participant Metrics as metrics_loop.py
    participant DB as PostgreSQL Database
    participant Agent as phase1_agent.py (LLM)
    participant Groq as Groq API
    participant Policy as policy_engine.py
    participant Frontend as React Console
    participant Operator as SRE Operator

    Note over Metrics: Every 5 seconds (collect_metrics_tick)
    Metrics->>DB: Query service investigation_in_progress lock
    DB-->>Metrics: Lock is False
    Note over Metrics: Latency > 500ms or Error > 5% detected
    Metrics->>DB: Set investigation_in_progress = True, INSERT Incident (status='investigating')
    Metrics->>Agent: Spawn run_phase1 task (non-blocking)
    
    loop Diagnostic Iterations (up to 10 iterations)
        Agent->>Groq: Request next step with tool context
        Groq-->>Agent: Request tool call (e.g. get_logs, search_similar_incidents)
        Agent->>DB: Execute read-only query (or cosine distance vector search)
        DB-->>Agent: Return query results
        Agent->>Groq: Return tool response contents
    end
    
    Groq-->>Agent: Return terminal JSON (hypothesis, recommended_action, confidence)
    Agent->>DB: UPDATE Incident (hypothesis, confidence, reasoning, tool trace)
    Agent->>Policy: Call decide(incident_id, service, terminal_json)
    
    alt Confidence >= 80 AND Service in Allowlist (checkout-api)
        Note over Policy: Auto-mitigation flow
        Policy->>Metrics: Clear anomaly flag (mock restart / rollback)
        Policy->>DB: UPDATE Incident (status='resolved_auto', action_detail)
        Policy->>DB: INSERT resolved case to past_incidents (RAG embedding)
    else Confidence < 80 OR Service not in Allowlist (auth-api, payments-api, local-host)
        Note over Policy: Escalate to human
        Policy->>DB: UPDATE Incident (status='paged', action_detail)
        Policy->>Operator: Send SMTP Page / Fallback Server Log
        
        loop Web Console Polling
            Frontend->>DB: Fetch active incidents list
            DB-->>Frontend: Return list containing 'paged' incident
            Frontend->>Operator: Render reasoning timeline & "Approve" button
        end
        
        Operator->>Frontend: Click "Approve action"
        Frontend->>DB: POST /api/incidents/{incident_id}/approve
        Note over Policy: Bypasses LLM diagnostics entirely
        Policy->>Metrics: Clear anomaly flag (mock restart / rollback)
        Policy->>DB: UPDATE Incident (status='approved', approved_by='demo-user')
    end

    Note over Policy: Finally block execution (guaranteed)
    Policy->>DB: Set service investigation_in_progress = False, stamp cooldown
```

---

## 5. Deployment Diagram
Illustrates how containers are grouped and exposed in the local running environment.

```mermaid
graph TD
    subgraph Browser ["Web Browser Client"]
        ReactApp["React DOM Javascript Client
        (Served locally on http://localhost:3000)"]
    end

    subgraph DockerEnv ["Docker Compose Sandbox Environment"]
        subgraph BackendContainer ["Container: vigil-backend"]
            FastAPI["Uvicorn Server (main:app)
            (Listening on port 8000)"]
            SentenceTransformers["SentenceTransformers (CPU local)"]
        end

        subgraph DBContainer ["Container: vigil-db"]
            PostgreSQL["PostgreSQL Engine (Port 5432)"]
            PGVector["pgvector extension & HNSW Index"]
            Vol["Docker Volume: pgdata"]
        end
    end

    ReactApp -->|REST Requests & WebSocket/Polling| FastAPI
    FastAPI -->|Queries & Updates via asyncpg| PostgreSQL
    PostgreSQL -->|Reads/Writes data files| Vol
    PostgreSQL --- PGVector
    FastAPI --- SentenceTransformers
```
