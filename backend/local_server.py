"""Local dev server: same handler as Lambda.

STORE_BACKEND=memory AWS_PROFILE=sandbox GATEPATH_MCP_TOKEN=gpmcp_... python3 local_server.py
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import handler


class H(BaseHTTPRequestHandler):
    def _send(self, res):
        self.send_response(res["statusCode"])
        for k, v in res["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(res["body"].encode())

    def do_GET(self):
        self._send(handler.lambda_handler({"routeKey": f"GET {self.path}"}, None))

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        self._send(handler.lambda_handler({"routeKey": f"POST {self.path}", "body": body}, None))


if __name__ == "__main__":
    print("http://localhost:8080")
    HTTPServer(("127.0.0.1", 8080), H).serve_forever()
