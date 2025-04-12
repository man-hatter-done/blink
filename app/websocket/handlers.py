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
                'type': 'error',
                'error': 'Authentication failed'
            }), room=client_id)
            return
            
        # Handle different message types based on action
        action = data.get('action')
        
        if not action:
            logger.warning("websocket_missing_action", client_id=client_id)
            return
            
        logger.info("websocket_action_received", 
                   client_id=client_id, 
                   action=action)
                   
        # This is a simplified handler for the key message types
        # In a full implementation, we would have separate handler functions
        
        if action == 'create_session':
            # Create a new session
            user_id = data.get('userId', str(uuid.uuid4()))
            command_id = data.get('commandId', str(uuid.uuid4()))
            
            # Create session
            session_id, _ = session_manager.create_session(user_id)
            
            # Store in client data
            connected_clients[client_id] = {
                'session_id': session_id,
                'user_id': user_id
            }
            
            # Send response
            socketio.emit('message', json.dumps({
                'type': 'session_created',
                'sessionId': session_id,
                'userId': user_id,
                'commandId': command_id
            }), room=client_id)
            
        elif action == 'execute_command':
            # Execute a command
            session_id = data.get('sessionId')
            command = data.get('command')
            command_id = data.get('commandId', str(uuid.uuid4()))
            
            if not session_id or not command:
                socketio.emit('message', json.dumps({
                    'type': 'command_error',
                    'commandId': command_id,
                    'error': 'Missing session ID or command'
                }), room=client_id)
                return
            
            # Check for session validity and execute command
            session = session_manager.get_session(session_id)
            
            if not session:
                # Create new session if needed
                user_id = connected_clients.get(client_id, {}).get('user_id', str(uuid.uuid4()))
                new_session_id, session = session_manager.create_session(user_id)
                
                # Update client data
                connected_clients[client_id] = {
                    'session_id': new_session_id,
                    'user_id': user_id
                }
                
                # Tell client about the new session
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
                socketio.emit('message', json.dumps({
                    'type': 'command_output',
                    'commandId': command_id,
                    'output': output
                }), room=client_id)
            
            # Execute the command
            _, future_tuple = session_manager.execute_command(
                session_id, 
                command, 
                stream_callback
            )
            
            if future_tuple:
                future, _ = future_tuple
                
                # Set up thread to notify when command completes
                def wait_for_command():
                    if future.wait(timeout=30):
                        socketio.emit('message', json.dumps({
                            'type': 'command_complete',
                            'commandId': command_id
                        }), room=client_id)
                    else:
                        socketio.emit('message', json.dumps({
                            'type': 'command_error',
                            'commandId': command_id,
                            'error': 'Command execution timed out'
                        }), room=client_id)
                
                thread = threading.Thread(target=wait_for_command)
                thread.daemon = True
                thread.start()
                
        elif action == 'end_session':
            # End a session
            session_id = data.get('sessionId')
            
            if session_id:
                # Terminate the session
                session_manager.terminate_session(session_id)
                
                # Remove from client data
                if client_id in connected_clients:
                    connected_clients.pop(client_id, None)
        
        else:
            logger.warning("websocket_unknown_action", 
                          client_id=client_id, 
                          action=action)
            
    except json.JSONDecodeError:
        logger.error("websocket_invalid_json", client_id=client_id)
    except Exception as e:
        logger.error("websocket_mess# Let's try again with a smaller, more focused file
cat > app/websocket/handlers.py << 'EOL'
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
