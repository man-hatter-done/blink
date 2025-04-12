"""
Authentication Middleware Module

This module provides authentication middleware for the Flask application.
"""

from flask import request, jsonify, g
import structlog
from app import API_KEY

logger = structlog.get_logger(__name__)

def verify_api_key():
    """
    Middleware to verify the API key in the request headers.
    
    This function checks if the X-API-Key header matches the expected API key.
    For WebSocket connections, this check is done in the WebSocket handler.
    """
    
    # Skip API key verification for OPTIONS requests (CORS preflight)
    if request.method == 'OPTIONS':
        return None
        
    # Skip verification for the WebSocket endpoint
    if request.endpoint == 'socket.io':
        return None
        
    # Get API key from header
    api_key = request.headers.get('X-API-Key')
    
    # Check if API key is correct
    if api_key != API_KEY:
        logger.warning("invalid_api_key", 
                     provided_key=api_key, 
                     endpoint=request.endpoint,
                     remote_addr=request.remote_addr)
        
        return jsonify({
            'error': 'Invalid API key'
        }), 401
    
    # API key is valid, store in flask.g for later use
    g.api_key_verified = True
    return None
