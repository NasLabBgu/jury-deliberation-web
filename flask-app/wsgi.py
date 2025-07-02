#!/usr/bin/env python3
"""
WSGI entry point for production deployment
"""

import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from app import app, socketio
    
    logger.info("Successfully imported Flask app and SocketIO")
    logger.info(f"App type: {type(app)}")
    logger.info(f"SocketIO type: {type(socketio)}")
    
    # For Flask-SocketIO with Gunicorn, we use the Flask app
    # SocketIO will automatically handle WebSocket connections
    application = app
    
    logger.info(f"WSGI application ready: {type(application)}")
    logger.info(f"Application callable: {callable(application)}")
    
except Exception as e:
    logger.error(f"Failed to import application: {e}")
    import traceback
    logger.error(f"Traceback: {traceback.format_exc()}")
    raise

if __name__ == "__main__":
    # This is for testing only
    socketio.run(app, debug=False, host='0.0.0.0', port=8080)
