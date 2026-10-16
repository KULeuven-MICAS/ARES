#!/usr/bin/env python3
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Local HTTP server that records the request bodies Codex sends.

The probe points the Codex CLI at this server using a local model provider. The
server writes request bodies to a JSONL file and answers with HTTP 500, without
forwarding requests. Captures show the tools, instructions, and input declared
for that probe invocation. The capture server requires no provider credentials.
"""
import http.server
import argparse
import json


def make_handler(out_path: str):
    """Return an HTTP handler class that appends each request body to out_path."""
    class Handler(http.server.BaseHTTPRequestHandler):
        """Record each request and answer with HTTP 500, so that no model is called."""
        protocol_version = "HTTP/1.1"

        def _record(self):
            """Append the path, method, and body of the request as one JSON line."""
            n = int(self.headers.get("content-length", 0) or 0)
            body = self.rfile.read(n) if n else b""
            rec = {
                "path": self.path,
                "method": self.command,
                "body": body.decode("utf-8", "replace"),
            }
            with open(out_path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            payload = b'{"error":{"message":"probe capture","type":"probe"}}'
            self.send_response(500)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_POST = _record
        do_GET = _record

        def log_message(self, *args):
            """Keep the server quiet."""
            return

    return Handler


def main() -> int:
    """Serve on a local port until the probe stops the server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out", help="JSONL file for request bodies; headers are never logged"
    )
    parser.add_argument("port", type=int, help="local port; 0 chooses a free port")
    args = parser.parse_args()
    server = http.server.HTTPServer(("127.0.0.1", args.port), make_handler(args.out))
    print(f"READY {server.server_port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
