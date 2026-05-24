import time

from flask import request
from flask_socketio import emit
from dotenv import load_dotenv
import redis
load_dotenv()



active_clients = set()
r = redis.Redis(host='localhost', port=6379, decode_responses=True)



def register_socketio_events(socketio):
    @socketio.on('connect')
    def handle_connect():
        client_id = request.sid
        active_clients.add(client_id)
        print(f'Client connected: {client_id}')
        current_time = time.time()

    @socketio.on('disconnect')
    def handle_disconnect():
        client_id = request.sid
        active_clients.discard(client_id)
        print(f'Client disconnected: {client_id}')

    @socketio.on('start')
    def handle_message(data):
        """Handle incoming messages from client"""
        print(f'GOTTEM')
        r.publish('start', 'start')
