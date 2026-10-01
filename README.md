# Incident Pathfinder

**Ask what happened. Find the evidence. Follow the runbook.**
*Incident Pathfinder powered by Gatepath*

- Demo video: https://www.youtube.com/watch?v=zXQyL2b_z2U
- Live app: https://b3t1swuh0g.execute-api.us-east-1.amazonaws.com/

## 1. What is Incident Pathfinder?

An incident investigation agent for Alexa+. During an outage, an engineer asks in plain language
("What happened in production?", "What changed?", "What should I do?"). Incident Pathfinder checks the
active incident and recent deployments, searches company knowledge (Slack, Jira, Confluence, Google Drive,
SharePoint) **through the existing Gatepath MCP server**, retrieves the runbook, and uses Amazon Bedrock
to give a short, evidence-backed answer.

## 2. The problem

Operational signals say *what* is broken ("5xx is up"). The context that explains *why* and *what to do*
lives elsewhere: a Slack thread reporting the same symptom, the Jira ticket that changed authentication,
the Confluence rollback runbook. On-call engineers lose the first minutes of an incident hunting through
those tools. Incident Pathfinder investigates them together in one conversation.

## 3. Architecture

```
Simulated Alexa+ Web UI (text)
   │  HTTPS
   ▼
API Gateway (HTTP API) ──► AWS Lambda: Incident Pathfinder Agent ──► DynamoDB (session state)
                              │                                   └► CloudWatch Logs
                              ├─► Amazon Bedrock (Converse API + tool use) decides which tools to call
                              │
                              ├─ get_active_incidents ─┐ demo data
                              ├─ get_recent_changes  ──┘
                              │
                              ├─ search_gatepath ─┐  MCP client ── MCP over HTTP (Streamable HTTP, JSON-RPC)
                              └─ get_runbook ─────┘        │
                                                           ▼
                                               Gatepath MCP Server (existing, unchanged)
                                                           │  ACL-filtered search
                                                           ▼
                                   Slack · Jira · Confluence · Google Drive · SharePoint
```

Each turn: Bedrock receives the question, the session context and the four tool definitions, calls the tools
it needs, and writes a 2–4 sentence answer. The UI shows the answer, the **Evidence** returned by Gatepath
(source + title + link), and the tool calls that were made.

## 4. Relationship to Alexa+

**Submission path: Simulated Alexa+ Experience.** Incident Pathfinder uses the Alexa+ Track's
simulated-experience submission path. The web interface simulates the conversational Alexa+ experience,
while the agent uses Gatepath over MCP/HTTP to retrieve authorized enterprise knowledge across connected
systems.

- It is **not** submitted as an Alexa+ Agent Skill or as a self-hosted MCP server for Alexa+, and it does not
  connect to a real Alexa+ service or device.
- The simulation is a self-built web front end: a single conversational surface, short spoken-style answers,
  and follow-up questions that keep context. Text input only; no voice.

```
Simulated Alexa+ Web UI
  → Incident Pathfinder Agent (AWS Lambda + Amazon Bedrock)
  → Gatepath MCP client
  → HTTP / MCP
  → Existing Gatepath MCP Server
  → Slack / Jira / Confluence / Google Drive / SharePoint
```

## 5. Tools

| Tool | What it does | Backed by |
|---|---|---|
| `get_active_incidents` | Current incident: service, error rate, start time, severity, status | Demo data (fixed scenario) |
| `get_recent_changes` | Deployments around the incident: ID, time, service, description, component | Demo data (fixed scenario) |
| `search_gatepath` | Cross-source search of company knowledge | Gatepath MCP tool `search` |
| `get_runbook` | Find the runbook and read its full text | Gatepath MCP tools `search` + `getDocument` |

The agent only calls the tools a question needs (for example "What should I do?" → `get_runbook`, reusing
the incident already identified in the session).

## 6. Gatepath integration

Gatepath already runs as an MCP server and is used here as an existing backend service for enterprise
knowledge, not as the Alexa+ Track's required MCP server (see section 4). Incident Pathfinder is an
**MCP client** of it
(`backend/app/gatepath.py`); it does not reimplement search, connectors or permissions, and Gatepath was
not modified.

- Transport: Streamable HTTP (`https://<gatepath-host>/mcp`)
- Auth: `Authorization: Bearer gpmcp_…` (Gatepath MCP token)
- Settings: the endpoint URL and the token are one JSON document, `{"url": "...", "token": "..."}`, stored in
  the SSM Parameter Store SecureString `/incident-pathfinder/gatepath-mcp`. Neither has a default in the
  source. The Lambda re-reads it every 5 minutes, so switching Gatepath environments needs no redeploy.
- Per turn: `initialize` → `notifications/initialized` → `tools/list` (discovery) → `tools/call search` /
  `tools/call getDocument`
- Results keep title, source type, snippet and URL, shown as Evidence.

Standalone check of the MCP connection:

```
cd backend
GATEPATH_MCP_URL=https://<gatepath-host>/mcp \
  GATEPATH_MCP_TOKEN=gpmcp_... \
  ../.venv/bin/python -m app.gatepath "authentication"
```

**Permissions.** Gatepath applies its own ACL filtering to every search based on the MCP token's grants.
In this hackathon build, the deployed app uses **one Gatepath MCP token** for all visitors; it does not yet
pass each requesting user's identity. Target design: *Incident Pathfinder only retrieves information the
requesting user is authorized to access through Gatepath* (per-user MCP tokens / OAuth).

## 7. AWS services used

| Service | Use |
|---|---|
| Amazon Bedrock | Converse API with tool use (default model `us.amazon.nova-2-lite-v1:0`; set `bedrock_model_id` to switch) |
| AWS Lambda | Agent backend (Python 3.13, no third-party dependencies) and the web page |
| Amazon API Gateway | HTTP API: `GET /` (UI), `POST /api/chat` |
| Amazon DynamoDB | Session state: session ID, recent conversation, incident context (24 h TTL) |
| Amazon CloudWatch | Lambda logs (structured JSON: Bedrock rounds, tool calls, Gatepath MCP calls) and API access logs |
| AWS Systems Manager Parameter Store | Gatepath MCP connection settings: URL + token as JSON (SecureString) |

## 8. Security / read-only policy

Incident Pathfinder is read-only. It has no tool that can roll back, deploy, restart, update Jira, post to
Slack or change AWS resources, and its IAM role only allows writing its own logs, reading/writing its own
session table, invoking Bedrock and reading the Gatepath token. When a user asks for a change, the agent
declines and states:

> No production change will be executed without explicit confirmation.

Tool results are treated as untrusted data in the prompt. API Gateway throttles requests.

## 9. Demo scenario

The latest deployment of `auth-service` (DEP-1187) changed authentication handling (new token validation
middleware). About 12 minutes later, the public API's 5xx error rate rose from 0.2% to 7.8%. In Gatepath:
a Slack report of authentication failures, the Jira ticket for the change, and a Confluence
"Authentication Rollback Runbook". This demo data was created in a Gatepath demo tenant for the hackathon.

1. **What happened in production?** → `get_active_incidents`, `get_recent_changes`, `search_gatepath`
2. **What changed?** → session context + `search_gatepath` (Jira / Slack)
3. **What should I do?** → session context + `get_runbook` (Confluence) — ends with the read-only policy

## 10. How to run

Local (uses real Bedrock and the real Gatepath MCP server):

```
python3 -m venv .venv
.venv/bin/pip install boto3 certifi
cd backend
STORE_BACKEND=memory AWS_PROFILE=sandbox AWS_REGION=us-east-1 \
  GATEPATH_MCP_URL=https://<gatepath-host>/mcp \
  GATEPATH_MCP_TOKEN=gpmcp_... \
  ../.venv/bin/python local_server.py
```

Deploy (AWS account 402178233131, us-east-1):

```
aws ssm put-parameter --profile sandbox --region us-east-1 \
  --name /incident-pathfinder/gatepath-mcp \
  --type SecureString --overwrite \
  --value '{"url": "https://<gatepath-host>/mcp", "token": "gpmcp_..."}'
cd infra
AWS_PROFILE=sandbox terraform init
AWS_PROFILE=sandbox terraform apply
```

The `app_url` output is the public URL.

Logs:

```
aws logs tail /aws/lambda/incident-pathfinder-api \
  --profile sandbox --region us-east-1 --follow
```

## 11. Hackathon limitations

- Incident and deployment data are fixed demo data, not live CloudWatch alarms or GitHub deployments.
- One shared Gatepath MCP token scoped to a Gatepath demo tenant (demo data only); per-user authorization
  pass-through is not implemented yet.
- Text only; no Alexa device or voice integration.
- One incident at a time; no write actions of any kind.
- No authentication on the demo web page (throttled API only).

## 12. Future work

- Real CloudWatch incident integration
- GitHub deployment integration
- Alexa+ native integration: future versions could expose Incident Pathfinder as an Alexa+ Agent Skill or a
  self-hosted MCP server using the required Alexa+ MCP specification and Streamable HTTP transport
- Voice interaction
- Per-user Gatepath authorization (user's own MCP token / OAuth)
- Jira / Slack write actions
- Human-approved rollback workflows

## 13. Teardown (after judging)

**When** (dates from the [official rules](https://amazonappdev2026.devpost.com/rules)):

| Date | Event | App |
|---|---|---|
| Oct 23, 2026 12:00 PT | Submission deadline | Must be running |
| Nov 9 – Nov 20, 2026 12:00 PT | Judging period | **Must stay available** — the rules require the project to be available to the Sponsor, Administrator and Judges, free and without restriction, until the Judging Period ends |
| ~Dec 3, 2026 12:00 PT (Dec 4, 05:00 JST) | Winner announcement | Keep running until here in case of winner verification |
| **After Dec 3, 2026** | — | **Delete** |

Never delete before **Nov 20, 2026 12:00 PT (Nov 21, 05:00 JST)**. It is a demo with an unauthenticated public
URL that searches Gatepath with a shared token, so do not keep it up long after the announcement.

**What to delete** (all in AWS account 402178233131, us-east-1, prefix `incident-pathfinder-`):

1. App resources (Lambda, API Gateway, DynamoDB table, IAM role, log groups):
   ```
   cd infra
   AWS_PROFILE=sandbox terraform destroy
   ```
2. The Gatepath settings parameter (created outside Terraform):
   ```
   aws ssm delete-parameter --profile sandbox --region us-east-1 \
     --name /incident-pathfinder/gatepath-mcp
   ```
3. Revoke the Gatepath MCP token used by the demo on the Gatepath token page, so it cannot be used even if a
   copy remains somewhere.
4. Optional, last: the Terraform state bucket `incident-pathfinder-tfstate-402178233131` (versioned, so delete
   all object versions first, then the bucket). Keep it if you may redeploy later.

The demo data in Gatepath (Slack post, Jira AUTH-1, Confluence runbook) lives in the Gatepath demo tenant and
can be kept for future demos.

## License

MIT — see [LICENSE](LICENSE).
