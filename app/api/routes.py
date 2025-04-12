"""
API Routes Module

This module defines the HTTP routes for the Flask application.
"""

from flask import request, jsonify, g
from app import app
from app.terminal.session_manager import session_manager
import structlog
import threading
import time
import uuid

logger = structlog.get_logger(__name__)

@app.route('/')
def index():
    """Root endpoint for health checks"""
    return jsonify({
        'status': 'Blink Terminal Backend Running',
        'version': '1.0.0'
    })

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'active_sessions': len(session_manager.sessions)
    })

@app.route('/create-session', methods=['POST'])
def create_session():
    """
    Create a new terminal session
    
    Expected JSON payload:
    {
        "userId": "string"  # User identifier
    }
    
    Returns:
        JSON with session ID and user ID
    """
    try:
        # Extract user ID from request
        data = request.get_json()
        user_id = data.get('userId', str(uuid.uuid4()))
        
        # Create a new session
        session_id, session = session_manager.create_session(user_id)
        
        logger.info("api_session_created", 
                   session_id=session_id, 
                   user_id=user_id)
        
        # Return session info
        return jsonify({
            'sessionId': session_id,
            'userId': user_id
        })
    except Exception as e:
        logger.error("api_session_creation_error", error=str(e))
        return jsonify({
            'error': f"Failed to create session: {str(e)}"
        }), 500

@app.route('/session', methods=['GET'])
def validate_session():
    """
    Validate an existing session
    
    Expected headers:
    - X-Session-Id: Session ID to validate
    
    Returns:
        200 OK if session is valid
        404 Not Found if session is invalid or expired
    """
    try:
        # Get session ID from header
        session_id = request.headers.get('X-Session-Id')
        
        if not session_id:
            logger.warning("api_missing_session_id")
            return jsonify({
                'error': 'Missing session ID'
            }), 400
        
        # Validate session
        valid = session_manager.validate_session(session_id)
        
        if valid:
            logger.info("api_session_validated", session_id=session_id)
            return jsonify({
                'valid': True,
                'sessionId': session_id
            })
        else:
            logger.warning("api_session_invalid", session_id=session_id)
            return jsonify({
                'error': 'Invalid or expired session'
            }), 404
    except Exception as e:
        logger.error("api_session_validation_error", error=str(e))
        return jsonify({
            'error': f"Failed to validate session: {str(e)}"
        }), 500

@app.route('/session', methods=['DELETE'])
def end_session():
    """
    Terminate an existing session
    
    Expected headers:
    - X-Session-Id: Session ID to terminate
    
    Returns:
        200 OK if session is terminated
        404 Not Found if session not found
    """
    try:
        # Get session ID from header
        session_id = request.headers.get('X-Session-Id')
        
        if not session_id:
            logger.warning("api_missing_session_id")
            return jsonify({
                'error': 'Missing session ID'
            }), 400
        
        # Terminate session
        terminated = session_manager.terminate_session(session_id)
        
        if terminated:
            logger.info("api_session_terminated", session_id=session_id)
            return jsonify({
                'success': True
            })
        else:
            logger.warning("api_session_not_found", session_id=session_id)
            return jsonify({
                'error': 'Session not found'
            }), 404
    except Exception as e:
        logger.error("api_session_termination_error", error=str(e))
        return jsonify({
            'error': f"Failed to terminate session: {str(e)}"
        }), 500

@app.route('/execute-command', methods=['POST'])
def execute_command():
    """
    Execute a command in a terminal session
    
    Expected headers:
    - X-Session-Id: Session ID
    
    Expected JSON payload:
    {
        "command": "string"  # Command to execute
    }
    
    Returns:
        JSON with command output
    """
    try:
        # Get session ID from header
        session_id = request.headers.get('X-Session-Id')
        
        if not session_id:
            logger.warning("api_missing_session_id")
            return jsonify({
                'error': 'Missing session ID'
            }), 400
        
        # Get command from request
        data = request.get_json()
        command = data.get('command')
        
        if not command:
            logger.warning("api_missing_command", session_id=session_id)
            return jsonify({
                'error': 'Missing command'
            }), 400
        
        # Check if session exists
        session = session_manager.get_session(session_id)
        
        if not session:
            logger.warning("api_session_not_found", session_id=session_id)
            
            # Create new session if the old one expired
            new_user_id = str(uuid.uuid4())
            new_session_id, new_session = session_manager.create_session(new_user_id)
            
            logger.info("api_session_renewed", 
                       old_session_id=session_id, 
                       new_session_id=new_session_id)
            
            # Execute command
            command_id, future_tuple = session_manager.execute_command(new_session_id, command)
            
            if command_id and future_tuple:
                future, result = future_tuple
                
                # Wait for command to complete with timeout
                if future.wait(timeout=30):
                    output = result[0] or ""
                    
                    logger.info("api_command_executed", 
                               session_id=new_session_id,
                               command_id=command_id)
                    
                    return jsonify({
                        'output': output,
                        'sessionRenewed': True,
                        'newSessionId': new_session_id
                    })
                else:
                    logger.warning("api_command_timeout", 
                                  session_id=new_session_id,
                                  command_id=command_id)
                    return jsonify({
                        'error': 'Command execution timed out',
                        'sessionRenewed': True,
                        'newSessionId': new_session_id
                    }), 500
            else:
                logger.error("api_command_execution_failed", 
                            session_id=new_session_id)
                return jsonify({
                    'error': 'Failed to execute command',
                    'sessionRenewed': True,
                    'newSessionId': new_session_id
                }), 500
        
        # Execute command
        command_id, future_tuple = session_manager.execute_command(session_id, command)
        
        if command_id and future_tuple:
            future, result = future_tuple
            
            # Wait for command to complete with timeout
            if future.wait(timeout=30):
                output = result[0] or ""
                
                logger.info("api_command_executed", 
                           session_id=session_id,
                           command_id=command_id)
                
                return jsonify({
                    'output': output
                })
            else:
                logger.warning("api_command_timeout", 
                              session_id=session_id,
                              command_id=command_id)
                return jsonify({
                    'error': 'Command execution timed out'
                }), 500
        else:
            logger.error("api_command_execution_failed", 
                        session_id=session_id)
            return jsonify({
                'error': 'Failed to execute command'
            }), 500
    except Exception as e:
        logger.error("api_command_execution_error", error=str(e))
        return jsonify({
            'error': f"Failed to execute command: {str(e)}"
        }), 500

@app.route('/terminal-ws')
def terminal_ws_info():
    """
    Information about WebSocket connection
    
    Returns:
        Information about how to connect to the WebSocket endpoint
    """
    return jsonify({
        'info': 'WebSocket endpoint is available at /terminal-ws',
        'usage': 'Connect using WebSocket protocol, not HTTP'
    })
