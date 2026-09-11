"""Isolated browser fixture, not a production Matrix identity verification."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def respond(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/_matrix/client/v3/login' and body.get('password') == 'isolated-browser-only':
            return self.respond(200, {'user_id':'@finance:isolated', 'access_token':'isolated-browser-token'})
        self.respond(403, {'errcode':'M_FORBIDDEN'})
    def do_GET(self):
        if self.path == '/_matrix/client/v3/account/whoami' and self.headers.get('Authorization') == 'Bearer isolated-browser-token':
            return self.respond(200, {'user_id':'@finance:isolated'})
        self.respond(403, {'errcode':'M_FORBIDDEN'})
HTTPServer(('0.0.0.0', 8008), Handler).serve_forever()
