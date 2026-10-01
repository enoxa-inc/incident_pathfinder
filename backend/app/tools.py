"""The four read-only tools the agent (Amazon Bedrock) can call.

- get_active_incidents / get_recent_changes: fixed demo data (hackathon scope).
- search_gatepath / get_runbook: logical names for MCP tool calls to the existing Gatepath MCP
  server over HTTP (Gatepath's own `search` and `getDocument` tools). Nothing is searched locally.
"""
from datetime import datetime, timedelta, timezone

from . import gatepath
from .log import log

SPECS = [
    {
        "name": "get_active_incidents",
        "description": "Get currently firing production incidents / alarms: service, error rate, start time, "
                       "severity and status. Read-only.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_changes",
        "description": "Get deployments to production around the incident window: deployment ID, time, service, "
                       "description and changed component. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "service": {"type": "string", "description": "Optional service name filter, e.g. auth-service"}}},
    },
    {
        "name": "search_gatepath",
        "description": "Search company knowledge (Slack, Jira, Confluence, Google Drive, SharePoint) through the "
                       "Gatepath MCP server. Every whitespace-separated term must appear in a document, so pass "
                       "2-3 distinctive keywords that appear verbatim in documents (e.g. 'auth-service', "
                       "'token validation'). Do not paste the question. Returns title, source, snippet and URL.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "2-3 distinctive keywords"},
            "source": {"type": "string", "description": "Optional: slack, jira, confluence, googledrive, sharepoint"}},
            "required": ["query"]},
    },
    {
        "name": "get_runbook",
        "description": "Find the operational runbook for a service or failure mode through the Gatepath MCP server "
                       "and return its text. Read-only.",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "1-2 keywords, e.g. 'authentication' or 'auth-service'"}},
            "required": ["topic"]},
    },
]
SOURCES = ("slack", "jira", "confluence", "googledrive", "sharepoint")


# ------------------------------------------------------------------ demo data (fixed scenario)

def _ago(minutes):
    t = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_active_incidents(_args, _gp):
    return {"incidents": [{
        "incident_id": "INC-2041",
        "title": "Elevated 5xx error rate on public API",
        "service": "public-api (upstream: auth-service)",
        "severity": "SEV-2",
        "status": "investigating",
        "started_at": _ago(26),
        "metrics": {"api_5xx_error_rate": {"baseline": "0.2%", "current": "7.8%"},
                    "auth_failures_per_min": {"baseline": 3, "current": 412}},
        "primary_anomaly": "Authentication failures (token validation errors) from auth-service",
        "source": "CloudWatch alarm public-api-5xx-high (demo data)",
    }]}


def get_recent_changes(args, _gp):
    changes = [
        {"deployment_id": "DEP-1187", "deployed_at": _ago(38), "service": "auth-service",
         "description": "Change authentication handling: new token validation middleware",
         "changed_component": "auth-service/middleware/token_validation", "deployed_by": "ci-pipeline"},
        {"deployment_id": "DEP-1183", "deployed_at": _ago(190), "service": "catalog-service",
         "description": "Update product image cache headers",
         "changed_component": "catalog-service/http/cache", "deployed_by": "ci-pipeline"},
    ]
    service = (args.get("service") or "").strip().lower()
    if service:
        changes = [c for c in changes if service in c["service"]] or changes
    return {"changes": changes, "source": "deployment log (demo data)",
            "note": "DEP-1187 finished about 12 minutes before the 5xx increase started."}


# ------------------------------------------------------------------ Gatepath MCP tool calls

def search_gatepath(args, gp):
    query = " ".join((args.get("query") or "").split()[:4])
    if not query:
        return {"results": [], "message": "Empty query"}
    source = (args.get("source") or "").strip().lower()
    sources = [source] if source in SOURCES else ["all"]
    res = gp.search(query, size=5, source_types=sources)
    terms = query.split()
    if not res["results"] and len(terms) > 1:
        # Gatepath ANDs terms; broaden once by merging adjacent two-term queries (single terms are too noisy).
        pairs = [f"{a} {b}" for a, b in zip(terms, terms[1:])] if len(terms) > 2 else terms
        res = gp.search(",".join(f"[{p}]" for p in pairs), size=5, source_types=sources)
    if not res["results"] and sources != ["all"]:
        # Nothing in the requested source: look across every source Gatepath grants.
        res = gp.search(query, size=5, source_types=["all"])
    return {"query": res["query"], "results": res["results"], "matchedBySource": res["matchedBySource"],
            "message": None if res["results"] else "No matching documents in Gatepath"}


def get_runbook(args, gp):
    topic = " ".join((args.get("topic") or "").split()[:2]) or "authentication"
    res = gp.search(f"[{topic} runbook],[{topic} rollback]", size=5)
    hits = res["results"]
    runbooks = [h for h in hits if "runbook" in h["title"].lower()] or hits
    if not runbooks:
        return {"found": False, "message": f"No runbook for '{topic}' found in Gatepath", "results": []}
    top = runbooks[0]
    text = top["snippet"]
    try:
        doc = gp.get_document(top["documentId"])
        text = (doc.get("docContent") or text)[:2500]
    except gatepath.GatepathError as e:
        log("gatepath_get_document_failed", level="WARN", error=str(e))
    return {"found": True, "runbook": {**top, "content": text}, "results": runbooks[:3]}


HANDLERS = {
    "get_active_incidents": get_active_incidents,
    "get_recent_changes": get_recent_changes,
    "search_gatepath": search_gatepath,
    "get_runbook": get_runbook,
}
