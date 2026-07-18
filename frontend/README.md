# SRE Incident Response Console Frontend

This is the interactive frontend dashboard for **Vigil**, the Autonomous Incident Response Agent. It is built using React and Chart.js.

## Features
- **Real-time Metrics Dashboard**: Visualizes service metrics (CPU, memory, latency, error rates, and request rates) using Chart.js.
- **Incident Investigation Timeline**: Renders the AI diagnostics loop trace, including tool calls, RAG search results, hypothesis generation, confidence metrics, and policies evaluated.
- **Manual Actions Control**: Allows operators to manually trigger mock anomalies (e.g. `high_latency`, `memory_oom`) or bypass the AI to approve mitigation actions directly.
- **JWT Authentication**: Secure login flow.

## Running Locally

1. Ensure you have Node.js (version 18+) installed.
2. Install dependencies:
   ```bash
   npm install --legacy-peer-deps
   ```
3. Start the development server:
   ```bash
   npm start
   ```
   The application will open in development mode at `http://localhost:3000`.

## Docker Containerization

The SRE Console is containerized using Nginx and can be run as part of the Compose stack:
```bash
docker-compose up --build
```
This runs the frontend container, exposes port `3000`, and builds the bundle with the configured backend URL.

## Configuration

You can customize the backend API address by setting the `REACT_APP_API_BASE_URL` environment variable at build time:
- **Default**: `http://localhost:8000` (FastAPI backend local address)

To build with a different backend address:
```bash
REACT_APP_API_BASE_URL=http://your-remote-api:8000 npm run build
```

## Demo Authentication
Access the dashboard using the default demo operator credentials:
- **Username**: `admin`
- **Password**: `vigil2025`
