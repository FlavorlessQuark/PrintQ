"""
Solgri Backend API
Main Flask application entry point with WebSocket server
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import os
from dotenv import load_dotenv
from websocket import register_socketio_events
import redis
import threading
import json


load_dotenv()

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
CORS(app, resources={r"/api/*": {"origins": "*"}})

TIME_BASE = 60  # 60 minutes
register_socketio_events(socketio)

def send_status():
    """Send status update to all connected clients"""
    pubsub = r.pubsub()
    pubsub.subscribe('status')
    print("Subscribed to Redis channel 'time_control'. Listening for messages...")
    for message in pubsub.listen():
        print(f"Received message")
        if message['type'] == 'message':
                data = json.loads(message['data'])
                print(data["success"])
                socketio.emit('status_update', message['data'])

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify backend is running"""
    return jsonify({'status': 'ok', 'message': 'Backend is running'})

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'True') == 'True'
    port = int(os.getenv('FLASK_PORT', 5000))
    threading.Thread(target=send_status, daemon=True).start()

    print("\n" + "="*60)
    print("🚀 PrintQ Backend Starting")
    print("="*60)
    print(f"📡 REST API available at: http://0.0.0.0:{port}/api/*")
    print(f"🔍 Debug mode: {debug_mode}")
    print(f"📊 Current data endpoint: GET /api/current")
    print("="*60 + "\n")

    socketio.run(app, host='0.0.0.0', port=port, debug=True)

