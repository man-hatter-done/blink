"""
Main Application Entry Point

This module initializes and runs the Flask application with WebSocket support
for terminal sessions.
"""

import os
import eventlet
import structlog

# Use eventlet as the async backend for Flask-SocketIO
eventlet.monkey_patch()

# Import the Flask application and SocketIO instance
from app import create_app, logger

# Create the application
app, socketio = create_app()

if __name__ == '__main__':
    # Get port from environment or use default
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    
    logger.info("starting_application", 
               host=host, 
               port=port, 
               debug=debug,
               api_key_required=True)
    
    # Start the server with WebSocket support
    socketio.run(app, host=host, port=port, debug=debug)
