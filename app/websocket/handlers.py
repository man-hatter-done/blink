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

def _handle_create_session(client_id, message):
    """
    Handle session creation request
    
    Args:
        client_id: Client ID
        message: Message data
    """
    try:
        # Extract user ID
        user_id = message.get('userId', str(uuid.uuid4()))
        command_id = message.get('commandId', str(uuid.uuid4()))
        
        # Create new session
        session_id, session = session_manager.create_session(user_id)
        
        # Store session in client data
        connected_clients[client_id] = {
            'session_id': session_id,
            'user_id': user_id
        }
        
        logger.info("websocket_session_created", 
                   client_id=client_id, 
                   session_id=session_id, 
                   user_id=user_id)
        
        # Send success response
        socketio.emit('message', json.dumps({
            'type': 'session_created',
            'sessionId': session_id,
            'userId': user_id,
            'commandId': command_id
        }), room=client_id)
    except Exception as e:
        logger.error("websocket_session_creation_error", 
                    client_id=client_id, 
                    error=str(e))
        
        # Send error response
        _send_error(client_id, message.get('commandId', ''), f"Failed to create session: {str(e)}")

def _handle_join_session(client_id, message):
    """
    Handle request to join an existing session
    
    Args:
        client_id: Client ID
        message: Message data
    """
    try:
        # Extract session ID
        session_id = message.get('sessionId')
        
        if not session_id:
            logger.warning("websocket_missing_session_id", client_id=client_id)
            return
            
        # Validate session
        session = session_manager.get_session(session_id)
        
        if not session:
            logger.warning("websocket_session_not_found", 
                          client_id=client_id, 
                          session_id=session_id)
            
            # Send session expired message
            socketio.emit('message', json.dumps({
                'type': 'session_expired',
                'sessionId': session_id
            }), room=client_id)
            return
            
        # Store session in client data
        connected_clients[client_id] = {
            'session_id': session_id,
            'user_id': session.user_id
        }
        
        logger.info("websocket_session_joined", 
                   client_id=client_id, 
                   session_id=session_id)
                   
        # Send success response
        socketio.emit('message', json.dumps({
            'type': 'session_joined',
            'sessionId': session_id
        }), room=client_id)
    except Exception as e:
        logger.error("websocket_join_session_error", 
                    client_id=client_id, 
                    error=str(e))

def _handle_execute_command(client_id, message):
    """
    Handle command execution request
    
    Args:
        client_id: Client ID
        message: Message data
    """
    try:
        # Extract command and session ID
        command = message.get('command')
        session_id = message.get('sessionId')
        command_id = message.get('commandId', str(uuid.uuid4()))
        
        if not command:
            logger.warning("websocket_missing_command", client_id=client_id)
            _send_error(client_id, command_id, "Missing command")
            return
            
        if not session_id:
            logger.warning("websocket_missing_session_id", client_id=client_id)
            _send_error(client_id, command_id, "Missing session ID")
            return
            
        # Check if session exists
        session = session_manager.get_session(session_id)
        
        if not session:
            logger.warning("websocket_session_not_found", 
                          client_id=client_id, 
                          session_id=session_id)
            
            # Create new session with the user ID from the client data
            user_id = connected_clients.get(client_id, {}).get('user_id', str(uuid.uuid4()))
            
            new_session_id, new_session = session_manager.create_session(user_id)
            
            logger.info("websocket_session_renewed", 
                       old_session_id=session_id, 
                       new_session_id=new_session_id)
            
            # Update client data
            connected_clients[client_id] = {
                'session_id': new_session_id,
                'user_id': user_id
            }
            
            # Send session renewed message
            socketio.emit('message', json.dumps({
                'type': 'command_output',
                'commandId': command_id,
                'output': '',
                'sessionRenewed': True,
                'newSessionId': new_session_id
            }), room=client_id)
            
            # Use the new session
            session_id = new_session_id
            session = new_session
        
        # Define a streaming callback
        def stream_callback(output):
            _send_command_output(client_id, command_id, output)
        
        # Execute command
        executed_command_id, future_tuple = session_manager.execute_command(
            session_id, 
            command, 
            stream_callback
        )
        
        if executed_command_id and future_tuple:
            future, result = future_tuple
            
            # Set up a thread to wait for command completion
            def wait_for_completion():
                if future.wait(timeout=30):
                    # Command completed
                    _send_command_complete(client_id, command_id)
                else:
                    # Command timed out
                    _send_error(client_id, command_id, "Command execution timed out")
            
            # Start the completion thread
            completion_thread = threading.Thread(target=wait_for_completion)
            completion_thread.daemon = True
            completion_thread.start()
            
            logger.info("websocket_command_execution_started", 
                       client_id=client_id,
                       session_id=session_id,
                       command_id=command_id)
        else:
            logger.error("websocket_command_execution_failed", 
                        client_id=client_id,
                        session_id=session_id)
            _send_error(client_id, command_id, "Failed to execute command")
    except Exception as e:
        logger.error("websocket_execute_command_error", 
                    client_id=client_id, 
                    error=str(e))
        _send_error(client_id, message.get('commandId', ''), f"Failed to execute command: {str(e)}")

def _handle_end_session(client_id, message):
    """
    Handle session termination request
    
    Args:
        client_id: Client ID
        message: Message data
    """
    try:
        # Extract session ID
        session_id = message.get('sessionId')
        
        if not session_id:
            logger.warning("websocket_missing_session_id", client_id=client_id)
            return
            
        # Terminate session
        terminated = session_manager.terminate_session(session_id)
        
        if terminated:
            logger.info("websocket_session_terminated", 
                       client_id=client_id, 
                       session_id=session_id)
            
            # Remove session from client data
            if client_id in connected_clients:
                connected_clients.pop(client_id, None)
        else:
            logger.warning("websocket_session_not_found", 
                          client_id=client_id, 
                          session_id=session_id)
    except Exception as e:
        logger.error("websocket_end_session_error", 
                    client_id=client_id, 
                    error=str(e))

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
        terminated = session_manager.terminate_session(session_id)
        
        if terminated:
            logger.info("websocket_session_auto_terminated", 
                       client_id=client_id, 
                       session_id=session_id)

@socketio.on('message')
def handle_message(message):
    """
    Handle incoming WebSocket message
    
    Args:
        message: Message data (string)
    """
    client_id = request.sid
    
    try:
        # Parse message
        if isinstance(message, str):
            data = json.loads(message)
        else:
            data = message
        
        # Authenticate client
        if not _authenticate_client(data):
            socketio.emit('message', json.dumps({
 # Fix the app/__init__.py file
cat > app/__init__.py << 'EOL'
"""
Blink Terminal Backend Main Module

This module initializes the Flask application and sets up all components.
"""

import os
import logging
import structlog
from flask import Flask, request
from flask_socketio import SocketIO
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_change_in_production')

# Set up API key for authentication
API_KEY = "B2D4G5"  # Hard-coded API key to match the Swift client

# Configure CORS for SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Import API routes and WebSocket handlers
from app.api import routes
from app.websocket import handlers
from app.terminal import session_manager
from app.auth import auth_middleware

# Register the authentication middleware
app.before_request(auth_middleware.verify_api_key)

logger.info("application_initialized", 
           debug=os.environ.get('DEBUG', 'false').lower() == 'true')

def create_app():
    # Return only the Flask app for Gunicorn
    return app

# Export socketio for use in app.py
# This allows us to use the SocketIO instance with app.py for development
# but return just the app for Gunicorn in production
