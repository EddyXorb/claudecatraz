#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer


class HostEchoHandler(BaseHTTPRequestHandler):
    def _reply(self):
        host = self.headers.get("Host", "<none>")
        body = f"Host: {host}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self._reply()


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), HostEchoHandler).serve_forever()
