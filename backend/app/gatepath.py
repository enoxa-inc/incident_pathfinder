"""MCP client for the existing Gatepath MCP server (Streamable HTTP, JSON-RPC over HTTPS).

Incident Pathfinder is only an MCP *client*. It never talks to Slack / Jira / Confluence /
Google Drive / SharePoint directly, and it does not reimplement search or permissions:
Gatepath resolves the MCP token into the user's grants and filters results by ACL.

Flow per session: initialize -> notifications/initialized -> tools/list (discovery) -> tools/call.

Connection settings are one JSON document, {"url": "https://<host>/mcp", "token": "gpmcp_..."}, stored in
the SSM SecureString named by GATEPATH_CONFIG_PARAMETER. Locally, GATEPATH_MCP_URL + GATEPATH_MCP_TOKEN
override it. There is deliberately no default URL.

Standalone check:
    GATEPATH_MCP_URL=https://<host>/mcp GATEPATH_MCP_TOKEN=gpmcp_... python -m app.gatepath "authentication"
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from .log import log

CONFIG_PARAMETER = os.environ.get("GATEPATH_CONFIG_PARAMETER", "/incident-pathfinder/gatepath-mcp")
CONFIG_TTL_SECONDS = 300   # re-read SSM so a URL/token change applies without a redeploy
PROTOCOL_VERSION = "2025-03-26"
TIMEOUT = 15
SEARCH_TOOL = "search"          # existing Gatepath tool, used as-is
DOCUMENT_TOOL = "getDocument"   # existing Gatepath tool, used as-is

_config = None
_config_loaded_at = 0.0


class GatepathError(Exception):
    pass


def _get_config():
    """Return {"url", "token"} from the environment (local) or the SSM JSON parameter (AWS)."""
    global _config, _config_loaded_at
    if os.environ.get("GATEPATH_MCP_URL") and os.environ.get("GATEPATH_MCP_TOKEN"):
        return {"url": os.environ["GATEPATH_MCP_URL"], "token": os.environ["GATEPATH_MCP_TOKEN"]}
    if _config is None or time.time() - _config_loaded_at > CONFIG_TTL_SECONDS:
        import boto3
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        try:
            data = json.loads(ssm.get_parameter(Name=CONFIG_PARAMETER, WithDecryption=True)["Parameter"]["Value"])
        except Exception as e:  # noqa: BLE001 - missing/invalid config means Gatepath is unavailable
            raise GatepathError("Gatepath connection settings are missing or invalid") from e
        if not data.get("url") or not data.get("token"):
            raise GatepathError("Gatepath connection settings need both url and token")
        _config, _config_loaded_at = {"url": data["url"], "token": data["token"]}, time.time()
    return _config


class GatepathMcpClient:
    """One MCP session with the Gatepath MCP server."""

    def __init__(self):
        config = _get_config()
        self.url, self.token = config["url"], config["token"]
        self.session_id = None
        self.next_id = 0
        self.server_info = None
        self.tools = {}

    # ---------------------------------------------------------------- transport
    def _post(self, body):
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                self.session_id = r.headers.get("Mcp-Session-Id") or self.session_id
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            raise GatepathError(f"Gatepath MCP server returned HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise GatepathError(f"Gatepath MCP server is unreachable ({type(e).__name__})") from e
        if "id" not in body:
            return None
        # The response is either plain JSON or an SSE stream of "data:{...}" lines.
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:]
            if line.startswith("{"):
                msg = json.loads(line)
                if msg.get("id") == body["id"]:
                    if "error" in msg:
                        raise GatepathError(msg["error"].get("message", "Gatepath MCP error"))
                    return msg["result"]
        raise GatepathError("Gatepath MCP server returned no response")

    def _request(self, method, params):
        self.next_id += 1
        return self._post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})

    # ---------------------------------------------------------------- MCP lifecycle
    def connect(self):
        """initialize -> initialized notification -> tools/list."""
        result = self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                              "clientInfo": {"name": "incident-pathfinder", "version": "0.1.0"}})
        self.server_info = result.get("serverInfo")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self.tools = {t["name"]: t for t in self._request("tools/list", {}).get("tools", [])}
        log("gatepath_mcp_connected", server=self.server_info, tools=sorted(self.tools))
        if SEARCH_TOOL not in self.tools:
            raise GatepathError(f"Gatepath MCP server does not expose the '{SEARCH_TOOL}' tool")
        return self

    def call_tool(self, name, arguments):
        if not self.tools:
            self.connect()
        if name not in self.tools:
            raise GatepathError(f"Gatepath MCP tool '{name}' is not available")
        log("gatepath_mcp_tools_call", tool=name, arguments=arguments)
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = next((c["text"] for c in result.get("content", []) if c.get("type") == "text"), "")
        if result.get("isError"):
            raise GatepathError(text[:300] or "Gatepath tool error")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}

    # ---------------------------------------------------------------- convenience wrappers
    def search(self, query, size=5, source_types=None):
        """Gatepath `search`. Returns normalized hits: title, source, snippet, url, documentId."""
        args = {"query": query, "size": size, "sourceTypes": source_types or ["all"]}
        data = self.call_tool(SEARCH_TOOL, args)
        hits = [_normalize(r) for r in data.get("results", [])]
        log("gatepath_search", query=query, source_types=args["sourceTypes"], hits=len(hits),
            matched_by_source=data.get("matchedBySource"))
        return {"query": data.get("usedQuery", query), "results": hits,
                "matchedBySource": data.get("matchedBySource")}

    def get_document(self, document_id):
        return self.call_tool(DOCUMENT_TOOL, {"documentId": document_id})


def _clean(text):
    """Make Slack mrkdwn readable: drop :emoji: codes and *bold*/_italic_/`code`/> markers, unescape entities."""
    text = html.unescape(text or "")
    text = re.sub(r":[a-z0-9_+-]+:", "", text)
    text = re.sub(r"(?<!\w)[*_`~]+|[*_`~]+(?!\w)", "", text)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def _snippet(content, title):
    text = re.split(r"\s+Source: Slack\b", _clean(content))[0][:500]   # drop Slack metadata trailer
    return "" if text in title else text


def _normalize(r):
    source = {"googledrive": "Google Drive", "sharepoint": "SharePoint"}.get(
        r.get("sourceType"), (r.get("sourceType") or "unknown").capitalize())
    title = _clean(r.get("documentTitle")) or "(untitled)"
    url = r.get("sourceUrl") or ""
    if r.get("sourceType") == "jira" and "/browse/" in url:
        key = url.rsplit("/browse/", 1)[1].split("?")[0]
        if key and key not in title:
            title = f"{key} {title}"
    return {
        "documentId": r.get("documentId"),
        "title": title if len(title) <= 100 else title[:97] + "...",
        "source": source,
        "sourceType": r.get("sourceType"),
        "snippet": _snippet(r.get("content"), title),
        "url": r.get("sourceUrl"),
    }


if __name__ == "__main__":
    client = GatepathMcpClient().connect()
    print("server:", client.server_info)
    for name, tool in client.tools.items():
        print("tool:", name, json.dumps(tool.get("inputSchema", {}).get("properties", {}))[:300])
    for q in sys.argv[1:] or ["authentication"]:
        res = client.search(q)
        print(f"\nsearch {q!r}: {len(res['results'])} results, matchedBySource={res['matchedBySource']}")
        for h in res["results"]:
            print(f"  [{h['source']}] {h['title']} — {h['url']}")
