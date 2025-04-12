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

def _authenticate_client(data):
    """
    Authenticate client based on API key in the WebSocket message
    
    Args:
        data: Message data containing API key
        
    Returns:
        bool: True if authenticated, False otherwise
    """
    try:
        # Check if data is already parsed JSON
        if isinstance(data, dict):
            api_key = data.get('apiKey')
        else:
            # Try to parse JSON data
            message = json.loads(data)
            api_key = message.get('apiKey')
            
        # Verify API key
        if api_key != API_KEY:
            logger.warning("websocket_invalid_api_key", 
                          client_id=request.sid)
            return False
            
        return True
    except Exception as e:
        logger.error("websocket_auth_error", 
                    client_id=request.sid,
                    error=str(e))
        return False

def _send_error(client_id, command_id, error_message):
    """
    Send error message to client
    
    Args:
        client_id: Client ID
        command_id: Command ID
        error_message: Error message
    """
    socketio.emit('message', json.dumps({
        'type': 'command_error',
        'commandId': command_id,
        'error': error_message
    }), room=client_id)

def _send_command_output(client_id, command_id, output):
    """
    Send command output to client
    
    Args:
        client_id: Client ID
        command_id: Command ID
        output: Command output
    """
    socketio.emit('message', json.dumps({
        'type': 'command_output',
        'commandId': command_id,
        'output': output
    }), room=client_id)

def _send_command_complete(client_id, command_id):
    """
    Send command completion notification to client
    
    Args:
        client_id: Client ID
        command_id: Command ID
    """
    socketio.emit('message', json.dumps({
        'type': 'command_complete',
        'commandId': command_id
    }), room=client_id)
