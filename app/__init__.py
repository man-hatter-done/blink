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

def create_app():
    # Return only the Flask app for Gunicorn
    return app

# Import API routes and WebSocket handlers after app is created to avoid circular imports
from app.api import routes
from app.websocket import handlers
from app.terminal import session_manager
from app.auth import auth_middleware

# Register the authentication middleware
app.before_request(auth_middleware.verify_api_key)

logger.info("application_initialized", 
           debug=os.environ.get('DEBUG', 'false').lower() == 'true')
