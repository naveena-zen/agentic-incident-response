# Stakeholder Summary: Vigil Autonomous Incident Response Agent

## Executive Overview
Vigil is an architectural proof-of-concept demonstrating how AI agents can assist with server diagnostics and incident response. The design enforces a **two-phase pipeline**:
1. **Phase 1 (Diagnosis)**: An AI agent scans logs, metrics, and deployment histories, and searches historical incidents to identify the root cause of server failures. Crucially, the AI is restricted to **read-only access** to prevent it from executing unauthorized commands.
2. **Phase 2 (Safety)**: A deterministic Python-based policy engine acts as the gatekeeper. It evaluates the AI's diagnostic confidence and checks safety rule templates before executing recovery actions or paging on-call engineers.

Vigil functions successfully as a localized demonstration platform, highlighting how to combine AI diagnostics with deterministic safety boundaries. However, it is **not production software** and operates with mocked system integrations.

---

## What Works (Current Capabilities)
* **Real-time Health Monitoring**: Automatically updates CPU, memory, and performance rates for monitored APIs.
* **Automatic Incident Triggers**: Rules detect abnormal patterns (like latencies > 500ms) and lock the service state to prevent concurrent diagnostics.
* **Semantic Historical Matching (RAG)**: Uses machine learning vector searches to match active issues against a library of 10 pre-loaded past incident reports.
* **Operator Console UI**: An interactive React web panel displaying system metrics, diagnostic timelines, and an approval queue.
* **Safety Allowlist Gates**: Restricts auto-mitigation exclusively to low-risk services (`checkout-api`) when AI confidence is $\ge 80\%$.
* **Human-in-the-Loop Approvals**: Enables administrators to review timelines and approve escalations directly from the dashboard, executing the mitigation step immediately.

---

## What Does Not Work (Prototype Limitations)
* **Simulated Recoveries (Mocked Actions)**: Vigil does not restart real servers or rollback actual software packages. Re-launching a service or rolling back a container simply updates status flags inside the database and clears in-memory anomaly lists.
* **Security Deficiencies**:
  * Default database passwords and JWT signing keys are stored in plaintext configurations.
  * The authentication mechanism verifies the administrator credentials using plaintext comparisons, bypassing secure hashing logic.
  * Wildcard CORS rules allow any external website to query the application's endpoints if the server is accessible on a local network.
* **Performance Constraints**: The software runs machine learning embedding calculations directly on the main event thread, which freezes web server request handlers during investigations.
* **Scalability Gaps**: Active anomaly states and cooldown timers are held in standard Python memory structures. Running multiple copies of Vigil behind a load balancer will result in conflicting server charts and duplicate agent activities.

---

## Transitioning to Production (Next Steps)
To convert Vigil from a demonstration model to a live system, the following engineering tasks must be completed:

1. **Build Real Infrastructure Drivers**: Write handlers to connect the policy engine to real orchestration clients (such as the Kubernetes API or cloud providers) to perform actual deployments, task restarts, and network changes.
2. **Decouple Machine Learning Workloads**: Move the SentenceTransformer models out of the FastAPI application. Offload calculation requests to an external hosted service or standalone model worker.
3. **Secure the System Core**: Eliminate hardcoded configuration secrets, restrict CORS rules, and add rate-limiting filters to secure admin actions and credentials.
4. **Implement Centralized Caching and Distributed Locks**: Integrate a Redis server to manage shared stats and enforce atomic lock structures across multiple backend workers.
5. **Support Multi-User Role Permissions**: Build database models to manage user profiles, separating read-only dashboard auditors from authorized SRE responders.
