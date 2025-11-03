"""
Gunicorn configuration for Free AutoFrame
Optimized for video processing workloads on Hostinger VPS
"""

import multiprocessing
import os

# Server socket
bind = "127.0.0.1:5000"
backlog = 2048

# Worker processes
# For CPU-intensive tasks like video processing, use fewer workers
# Formula: (2 x CPU cores) + 1 is standard, but we reduce it for video processing
cpu_count = multiprocessing.cpu_count()
workers = max(2, cpu_count)  # At least 2 workers, max = CPU count

# Worker class
worker_class = 'sync'  # Sync workers for video processing
worker_connections = 1000
max_requests = 100  # Restart workers after N requests to prevent memory leaks
max_requests_jitter = 10  # Add randomness to prevent all workers restarting simultaneously

# Timeouts
timeout = 300  # 5 minutes - enough for processing 200MB videos
graceful_timeout = 30  # Graceful shutdown timeout
keepalive = 2

# Logging
accesslog = '/var/log/autoframe/access.log'
errorlog = '/var/log/autoframe/error.log'
loglevel = os.getenv('LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'autoframe'

# Server mechanics
daemon = False
pidfile = '/var/run/autoframe.pid'
umask = 0o007
user = None  # Will be set by systemd
group = None  # Will be set by systemd

# SSL (if needed)
# keyfile = '/path/to/key.pem'
# certfile = '/path/to/cert.pem'

# Server hooks
def on_starting(server):
    """Called just before the master process is initialized."""
    print(f"Starting Gunicorn with {workers} workers")

def on_reload(server):
    """Called to recycle workers during a reload via SIGHUP."""
    print("Reloading Gunicorn workers")

def when_ready(server):
    """Called just after the server is started."""
    print(f"Gunicorn is ready. Listening on: {bind}")

def worker_int(worker):
    """Called when a worker received INT or QUIT signal."""
    print(f"Worker {worker.pid} received INT/QUIT signal")

def worker_abort(worker):
    """Called when a worker received SIGABRT signal."""
    print(f"Worker {worker.pid} aborted")

# Development settings (override with environment variables)
if os.getenv('FLASK_ENV') == 'development':
    reload = True
    loglevel = 'debug'
    workers = 1

# Performance tuning
# Preload app to save memory (but makes reload slower)
preload_app = False  # Set to True for production with stable code

# Tmp directory for uploads
tmp_upload_dir = None  # Use default system tmp

# Limit request line size
limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190
