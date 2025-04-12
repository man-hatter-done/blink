"""
Terminal Session Manager Module

This module manages terminal sessions, including creation, validation,
command execution, and termination.
"""

import os
import uuid
import time
import threading
import queue
import pty
import select
import signal
import subprocess
import structlog
from typing import Dict, Any, Optional, Callable
import json

logger = structlog.get_logger(__name__)

# Constants
SESSION_TIMEOUT = int(os.environ.get('SESSION_TIMEOUT', 3600))  # 1 hour in seconds
COMMAND_TIMEOUT = int(os.environ.get('COMMAND_TIMEOUT', 30))    # 30 seconds

class TerminalSession:
    """
    Represents a terminal session, which could be a local shell or SSH connection.
    """
    
    def __init__(self, session_id: str, user_id: str):
        """
        Initialize a terminal session
        
        Args:
            session_id: Unique session identifier
            user_id: User identifier associated with this session
        """
        self.id = session_id
        self.user_id = user_id
        self.created_at = time.time()
        self.last_activity = time.time()
        self.pid = None
        self.fd = None
        self.active = True
        self.input_queue = queue.Queue()
        self.command_outputs = {}
        self.command_callbacks = {}
        self.command_streams = {}
        self.output_thread = None
        self.input_thread = None
        
        # Terminal properties
        self.rows = 24
        self.cols = 80
        self.term = "xterm-256color"
        
        self.log = logger.bind(
            session_id=session_id,
            user_id=user_id
        )
        
        self.log.info("terminal_session_created")
        
    def start(self):
        """Start the terminal session with a shell"""
        self._start_shell()
        
    def _start_shell(self):
        """Start a local shell session using PTY"""
        self.log.info("starting_local_shell")
        
        # Get shell from environment or use bash as default
        shell = os.environ.get('SHELL', '/bin/bash')
        
        # Create PTY and fork process
        self.pid, self.fd = pty.fork()
        
        if self.pid == 0:  # Child process
            # Execute shell
            try:
                os.execvp(shell, [shell])
            except Exception as e:
                print(f"Shell execution failed: {e}")
                os._exit(1)
        else:  # Parent process
            # Set terminal size
            self.resize(self.rows, self.cols)
            
            # Start output thread
            self.output_thread = threading.Thread(
                target=self._output_worker,
                name=f"output-{self.id[:8]}"
            )
            self.output_thread.daemon = True
            self.output_thread.start()
            
            # Start input processing thread
            self.input_thread = threading.Thread(
                target=self._input_worker,
                name=f"input-{self.id[:8]}"
            )
            self.input_thread.daemon = True
            self.input_thread.start()
            
            self.log.info("local_shell_started", pid=self.pid)
    
    def _output_worker(self):
        """Worker thread to read output from PTY"""
        self.log.debug("output_worker_started")
        
        try:
            while self.active:
                r, w, e = select.select([self.fd], [], [], 0.1)
                if self.fd in r:
                    try:
                        data = os.read(self.fd, 4096)
                        if data:
                            # Process output data
                            self._process_output(data)
                        else:
                            # EOF - process terminated
                            self.log.info("process_terminated")
                            break
                    except OSError as e:
                        # Process might have terminated
                        self.log.error("read_error", error=str(e))
                        break
                time.sleep(0.01)
        except Exception as e:
            self.log.error("output_worker_error", error=str(e))
        finally:
            self.terminate()
    
    def _input_worker(self):
        """Worker thread to process input from client"""
        self.log.debug("input_worker_started")
        
        try:
            while self.active:
                try:
                    data = self.input_queue.get(timeout=0.1)
                    if self.fd:
                        os.write(self.fd, data)
                    self.input_queue.task_done()
                except queue.Empty:
                    pass
                except Exception as e:
                    self.log.error("input_processing_error", error=str(e))
        except Exception as e:
            self.log.error("input_worker_error", error=str(e))
        finally:
            self.terminate()
    
    def _process_output(self, data):
        """
        Process output data from the terminal and route it to registered callbacks
        
        Args:
            data: Binary output data from the terminal
        """
        if not data:
            return
            
        # Update last activity timestamp
        self.last_activity = time.time()
        
        try:
            # Convert data to string for streaming
            output_str = data.decode('utf-8', errors='replace')
            
            # Send to all registered command streams
            for command_id, stream_callback in self.command_streams.items():
                try:
                    stream_callback(output_str)
                    
                    # Accumulate output for the command
                    if command_id in self.command_outputs:
                        self.command_outputs[command_id] += output_str
                    else:
                        self.command_outputs[command_id] = output_str
                        
                except Exception as e:
                    self.log.error("stream_callback_error", 
                                  command_id=command_id, 
                                  error=str(e))
        except Exception as e:
            self.log.error("output_processing_error", error=str(e))
    
    def write(self, data):
        """
        Queue data to be written to the terminal
        
        Args:
            data: Data to write (string or bytes)
        """
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.input_queue.put(data)
        self.log.debug("input_queued", size=len(data))
        
        # Update last activity timestamp
        self.last_activity = time.time()
    
    def resize(self, rows, cols):
        """
        Resize the terminal
        
        Args:
            rows: Number of rows
            cols: Number of columns
        """
        import termios
        import fcntl
        import struct
        
        self.rows = rows
        self.cols = cols
        
        if self.fd:
            # Resize PTY
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
                self.log.info("terminal_resized", rows=rows, cols=cols)
            except Exception as e:
                self.log.error("terminal_resize_error", error=str(e))
    
    def execute_command(self, command, command_id, stream_callback=None):
        """
        Execute a command in the terminal session
        
        Args:
            command: Command to execute
            command_id: Unique ID for this command execution
            stream_callback: Optional callback for streaming output
            
        Returns:
            str: Command ID for tracking the execution
        """
        self.log.info("executing_command", command=command, command_id=command_id)
        
        # Update last activity timestamp
        self.last_activity = time.time()
        
        # Reset output buffer for this command
        self.command_outputs[command_id] = ""
        
        # Register stream callback if provided
        if stream_callback:
            self.command_streams[command_id] = stream_callback
        
        # Write command to terminal
        self.write(command + "\n")
        
        # Set up a timer to consider the command complete after a timeout
        timer = threading.Timer(COMMAND_TIMEOUT, 
                               lambda: self._complete_command(command_id))
        timer.daemon = True
        timer.start()
        
        return command_id
    
    def _complete_command(self, command_id):
        """
        Mark a command as complete and trigger its callback
        
        Args:
            command_id: ID of the command to complete
        """
        if command_id in self.command_streams:
            # Get the accumulated output
            output = self.command_outputs.get(command_id, "")
            
            # Clean up
            self.command_streams.pop(command_id, None)
            self.command_outputs.pop(command_id, None)
            
            self.log.info("command_completed", command_id=command_id)
            
            # Return the command output through the callback
            if command_id in self.command_callbacks:
                callback = self.command_callbacks.pop(command_id)
                callback(output)
    
    def register_command_callback(self, command_id, callback):
        """
        Register a callback for when a command completes
        
        Args:
            command_id: ID of the command
            callback: Function to call with the command output
        """
        self.command_callbacks[command_id] = callback
    
    def is_valid(self):
        """
        Check if the session is still valid (not expired)
        
        Returns:
            bool: True if session is valid, False otherwise
        """
        return self.active and (time.time() - self.last_activity) < SESSION_TIMEOUT
    
    def terminate(self):
        """Terminate the session and clean up resources"""
        if not self.active:
            return
            
        self.log.info("terminating_session")
        self.active = False
        
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
                # Give it a moment to terminate gracefully
                time.sleep(0.1)
                # If still running, force kill
                try:
                    os.kill(self.pid, 0)  # This raises an error if process is gone
                    os.kill(self.pid, signal.SIGKILL)
                except OSError:
                    pass  # Process already terminated
            except OSError:
                pass
                
        if self.fd:
            try:
                os.close(self.fd)
            except OSError:
                pass
                
        self.log.info("session_terminated")


class SessionManager:
    """
    Manages terminal sessions for multiple users
    """
    
    _instance = None
    
    def __new__(cls):
        """Singleton pattern to ensure only one instance of SessionManager exists"""
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the session manager if not already initialized"""
        if self._initialized:
            return
            
        self.sessions = {}
        self.log = logger.bind(component="SessionManager")
        self._cleanup_thread = None
        self._initialized = True
        
        # Start cleanup thread
        self._start_cleanup_thread()
        
        self.log.info("session_manager_initialized")
    
    def _start_cleanup_thread(self):
        """Start a background thread to clean up expired sessions"""
        if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
            return
            
        self._cleanup_thread = threading.Thread(target=self._cleanup_worker)
        self._cleanup_thread.daemon = True
        self._cleanup_thread.start()
    
    def _cleanup_worker(self):
        """Worker thread to periodically clean up expired sessions"""
        self.log.info("cleanup_thread_started")
        
        while True:
            try:
                # Sleep for a while
                time.sleep(60)  # Check every minute
                
                # Find expired sessions
                expired_sessions = []
                for session_id, session in self.sessions.items():
                    if not session.is_valid():
                        expired_sessions.append(session_id)
                
                # Clean up expired sessions
                for session_id in expired_sessions:
                    self.log.info("cleaning_expired_session", session_id=session_id)
                    self.terminate_session(session_id)
                    
            except Exception as e:
                self.log.error("cleanup_worker_error", error=str(e))
    
    def create_session(self, user_id):
        """
        Create a new terminal session
        
        Args:
            user_id: User identifier for the session
            
        Returns:
            tuple: (session_id, session) - Information about the created session
        """
        session_id = str(uuid.uuid4())
        session = TerminalSession(session_id, user_id)
        session.start()
        
        # Store the session
        self.sessions[session_id] = session
        
        self.log.info("session_created", session_id=session_id, user_id=user_id)
        return session_id, session
    
    def get_session(self, session_id):
        """
        Get a session by ID
        
        Args:
            session_id: ID of the session to get
            
        Returns:
            TerminalSession or None: The session if found and valid, None otherwise
        """
        session = self.sessions.get(session_id)
        
        if session and session.is_valid():
            return session
            
        # Session not found or expired
        if session:
            self.log.info("session_expired", session_id=session_id)
            self.terminate_session(session_id)
            
        return None
    
    def validate_session(self, session_id):
        """
        Check if a session is valid
        
        Args:
            session_id: ID of the session to validate
            
        Returns:
            bool: True if session is valid, False otherwise
        """
        session = self.get_session(session_id)
        return session is not None
    
    def execute_command(self, session_id, command, stream_callback=None):
        """
        Execute a command in a session
        
        Args:
            session_id: ID of the session
            command: Command to execute
            stream_callback: Optional callback for streaming output
            
        Returns:
            tuple: (command_id, future) - Information to track the command execution
        """
        session = self.get_session(session_id)
        
        if not session:
            self.log.warning("session_not_found", session_id=session_id)
            return None, None
            
        # Generate command ID
        command_id = str(uuid.uuid4())
        
        # Execute the command
        session.execute_command(command, command_id, stream_callback)
        
        # Create a future to get the result
        future = threading.Event()
        result = [None]
        
        def callback(output):
            result[0] = output
            future.set()
            
        session.register_command_callback(command_id, callback)
        
        self.log.info("command_execution_started", 
                     session_id=session_id, 
                     command_id=command_id)
                     
        return command_id, (future, result)
    
    def terminate_session(self, session_id):
        """
        Terminate a session
        
        Args:
            session_id: ID of the session to terminate
            
        Returns:
            bool: True if session was terminated, False otherwise
        """
        session = self.sessions.pop(session_id, None)
        
        if session:
            session.terminate()
            self.log.info("session_terminated", session_id=session_id)
            return True
            
        return False
    
    def close_all_for_client(self, client_id):
        """
        Close all sessions for a client
        
        Args:
            client_id: ID of the client
            
        Returns:
            int: Number of sessions closed
        """
        # In a real implementation, sessions would need to track their client_id
        # For now, we don't do anything
        return 0


# Create a singleton instance
session_manager = SessionManager()
