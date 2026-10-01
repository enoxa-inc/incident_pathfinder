"""Lambda entry point behind API Gateway (HTTP API, payload v2).

GET  /           -> the Alexa+-style web UI
POST /api/chat   -> {"session_id", "message"} -> {"answer", "evidence", "tools", "notices"}
"""
import base64
import json
import re
from pathlib import Path

from . import agent, store
from .log import log

INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()
SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def lambda_handler(event, _context):
    route = event.get("routeKey", "")
    if route == "GET /":
        return {"statusCode": 200, "headers": {"Content-Type": "text/html; charset=utf-8",
                                               "Cache-Control": "no-cache"}, "body": INDEX_HTML}
    if route == "POST /api/chat":
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode()
        try:
            req = json.loads(body)
        except ValueError:
            return _json(400, {"error": "Invalid JSON"})
        return _json(*chat(req.get("session_id", ""), req.get("message", "")))
    return _json(404, {"error": "Not found"})


def chat(session_id, message):
    message = (message or "").strip()[:500]
    if not SESSION_RE.match(session_id or "") or not message:
        return 400, {"error": "session_id and message are required"}
    state = store.load(session_id)
    log("chat_request", session_id=session_id, message=message,
        has_incident=bool((state.get("incident_context") or {}).get("incident")))
    try:
        reply, state = agent.answer(message, state)
    except agent.BedrockError as e:
        return 200, {"answer": f"I couldn't reach Amazon Bedrock just now ({e}). Please try again.",
                     "evidence": [], "tools": [], "notices": [str(e)], "error": "bedrock"}
    store.save(session_id, state)
    return 200, reply


def _json(status, data):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(data)}
