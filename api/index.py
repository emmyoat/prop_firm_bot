from http.server import BaseHTTPRequestHandler
import json
import os

# In-memory storage for multi-account support
# cache["bots"] will store bot_id: data mapping
cache = {
    "bots": {}
}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # API Key authentication
        api_key = self.headers.get('X-API-Key')
        env_key = os.environ.get('DASHBOARD_API_KEY', 'propbot-secret')
        
        if api_key != env_key:
            self.send_response(403)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"error": "Unauthorized"}')
            return
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        # Return all bots. Frontend will handle selection.
        response = cache["bots"] if cache["bots"] else {"error": "No bots connected"}
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        
        try:
            data = json.loads(post_data.decode('utf-8'))
            bot_id = data.get("bot_id", "default")
            
            # Simple API Key check
            api_key = self.headers.get('X-API-Key')
            env_key = os.environ.get('DASHBOARD_API_KEY', 'propbot-secret')
            
            if api_key != env_key:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b'{"error": "Unauthorized"}')
                return

            # Store data keyed by bot_id
            cache["bots"][bot_id] = data
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status": "success"}')
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-API-Key, Content-Type')
        self.end_headers()
