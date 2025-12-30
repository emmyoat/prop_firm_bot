import time
from pyngrok import ngrok
import sys

def expose_dashboard():
    # 1. Kill old tunnels to be safe
    tunnels = ngrok.get_tunnels()
    for t in tunnels:
        ngrok.disconnect(t.public_url)

    # 2. Open HTTP tunnel found on port 8501
    try:
        # Open a HTTP tunnel on the default port 8501
        public_url = ngrok.connect(8501, "http").public_url
        print(f"\nPUBLIC DASHBOARD URL: {public_url}")
        print("Share this link to access your dashboard from anywhere.")
        print("Press Ctrl+C to stop sharing.\n")
    except Exception as e:
        print(f"Error starting ngrok: {e}")
        print("Note: You may need to sign up at ngrok.com and run: ngrok config add-authtoken <TOKEN>")
        sys.exit(1)

    # 3. Keep alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping tunnel...")
        ngrok.kill()

if __name__ == "__main__":
    expose_dashboard()
