"""
WebSocket Handlers Module

This module implements WebSocket event handlers for terminal communication.
"""

import json
import uuid
import time
import threading
from flask_socketio import emit, join_room, leave_room
from flask import request
import structlog

from app import socketio, API_KEY
from app.terminal.session_manager import session_manager

logger = structlog.get_logger(__name__)

# Keep track of connected clients and their sessions
connected_clients = {}

# Helper functions to send messages to clients
def _send_error(client_id, command_id, error_message):
    """Send error message to client"""
    socketio.emit('message', json.dumps({
        'type': 'command_error',
        'commandId': command_id,
        'error': error_message
    }), room=client_id)

def _send_command_output(client_id, command_id, output):
    """Send command output to client"""
    socketio.emit('message', json.dumps({
        'type': 'command_output',
        'commandId': command_id,
        'output': output
    }), room=client_id)

def _send_command_complete(client_id, command_id):
    """Send command completion notification to client"""
    socketio.emit('message', json.dumps({
        'type': 'command_complete',
        'commandId': command_id
    }), room=client_id)

@socketio.on('connect')
def handle_connect():
    """Handle new WebSocket connection"""
    client_id = request.sid
    logger.info("websocket_client_connected", client_id=client_id)
    
    # Add client to its own room for private messages
    join_room(client_id)
    
    # Send connected message
    socketio.emit('message', json.dumps({
        'type': 'connected'
    }), room=client_id)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    client_id = request.sid
    logger.info("websocket_client_disconnected", client_id=client_id)
    
    # Clean up client data
    client_data = connected_clients.pop(client_id, None)
    
    # Terminate session if it exists
    if client_data and 'session_id' in client_data:
        session_id = client_data['session_id']
        session_manager.terminate_session(session_id)

@socketio.on('message')
def handle_message(message):
    """Handle incoming WebSocket message"""
    client_id = request.sid
    
    try:
        # Parse message
        if isinstance(message, str):
            data = json.loads(message)
        else:
            data = message
        
        # Check API key
        api_key = data.get('apiKey')
        if api_key != API_KEY:
            socketio.emit('message', json.dumps({
                'type': 'error',
                'error': 'Authentication failed'
            }), room=client_id)
            return
            
        # Get action
        action = data.get('action')
        
        if action == 'create_session':
            # Create new session
            user_id = data.get('userId', str(uuid.uuid4()))
            command_id = data.get('commandId', str(uuid.uuid4()))
            
            session_id, _ = session_manager.create_session(user_id)
            connected_clients[client_id] = {'session_id': session_id, 'user_id': user_id}
            
            socketio.emit('message', json.dumps({
                'type': 'session_created',
                'sessionId': session_id,
                'userId': user_id,
                'commandId': command_id
            }), room=client_id)
        
        elif action == 'execute_command':
            # Execute command
            session_id = data.get('sessionId')
            command = data.get('command')
            command_id = data.get('commandId', str(uuid.uuid4()))
            
            if not session_id or not command:
                _send_error(client_id, command_id, "Missing session ID or command")
                return
                
            session = session_manager.get_session(session_id)
            if not session:
                # Create new session
                user_id = connected_clients.get(client_id, {}).get('user_id', str(uuid.uuid4()))
                new_session_id, session = session_manager.create_session(user_id)
                connected_clients[client_id] = {'session_id': new_session_id, 'user_id': user_id}
                
                socketio.emit('message', json.dumps({
                    'type': 'command_output',
                    'commandId': command_id,
                    'output': '',
                    'sessionRenewed': True,
                    'newSessionId': new_session_id
                }), room=client_id)
                
                session_id = new_session_id
            
            # Define streaming callback
            def stream_callback(output):
                _send_command_output(client_id, command_id, output)
            
            # Execute command
            _, future_tuple = session_manager.execute_command(session_id, command, stream_callback)
            
            if future_tuple:
                future, _ = future_tuple
                
                # Set up thread to wait for completion
                def wait_for_completion():
                    if future.wait(timeout=30):
                        _send_command_complete(client_id, command_id)
                    else:
                        _send_error(client_id, command_id, "Command execution timed out")
                
                completion_thread = threading.Thread(target=wait_for_completion)
                completion_thread.daemon = True
                completion_thread.start()
        
        elif action == 'end_session':
            # End session
            session_id = data.get('sessionId')
            if session_id:
                session_manager.terminate_session(session_id)
                if client_id in connected_clients:
                    connected_clients.pop(client_id, None)
        
    except Exception as e:
        logger.error("websocket_message_handling_error", client_id=client_id, error=str(e))
