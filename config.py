"""
Configuration file for Free AutoFrame
Supports multiple deployment modes: development, vercel, hostinger
"""

import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).parent.absolute()

# Deployment mode detection
DEPLOYMENT_MODE = os.getenv('DEPLOYMENT_MODE', 'development')

# Flask configuration
class Config:
    """Base configuration"""

    # Basic Flask settings
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    DEBUG = False
    TESTING = False

    # Upload settings
    UPLOAD_FOLDER = BASE_DIR / 'uploads'
    OUTPUT_FOLDER = BASE_DIR / 'outputs'
    MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH_MB', 120)) * 1024 * 1024  # MB to bytes

    # Video processing limits
    CLIENT_DURATION_LIMIT_SECONDS = int(os.getenv('CLIENT_DURATION_LIMIT_SECONDS', 75))
    MAX_SERVER_DURATION_SECONDS = int(os.getenv('MAX_SERVER_DURATION_SECONDS', 180))
    MAX_BATCH_SIZE = int(os.getenv('MAX_BATCH_SIZE', 10))

    # Allowed extensions
    ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.mkv'}

    # CORS settings (for hybrid deployment)
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', '*').split(',')

    # Parallel processing
    MAX_PARALLEL_JOBS = int(os.getenv('MAX_PARALLEL_JOBS', 2))

    # Cleanup settings
    AUTO_CLEANUP_HOURS = int(os.getenv('AUTO_CLEANUP_HOURS', 24))

    # Ensure directories exist
    UPLOAD_FOLDER.mkdir(exist_ok=True)
    OUTPUT_FOLDER.mkdir(exist_ok=True)


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    DEPLOYMENT_MODE = 'development'
    # Use localhost
    BACKEND_API_URL = os.getenv('BACKEND_API_URL', 'http://localhost:5000')


class VercelConfig(Config):
    """Vercel serverless configuration"""
    DEBUG = False
    DEPLOYMENT_MODE = 'vercel'

    # Vercel has /tmp directory for temporary files
    UPLOAD_FOLDER = Path('/tmp/uploads')
    OUTPUT_FOLDER = Path('/tmp/outputs')

    # Stricter limits for serverless
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max for Vercel
    MAX_SERVER_DURATION_SECONDS = 8  # 10s timeout, leave 2s buffer
    MAX_BATCH_SIZE = 1  # Process one at a time on serverless
    MAX_PARALLEL_JOBS = 1  # No parallel processing on serverless

    # Point to Hostinger for heavy processing
    BACKEND_API_URL = os.getenv('BACKEND_API_URL', os.getenv('HOSTINGER_API_URL', ''))

    # Ensure temp directories exist
    UPLOAD_FOLDER.mkdir(exist_ok=True)
    OUTPUT_FOLDER.mkdir(exist_ok=True)


class HostingerConfig(Config):
    """Hostinger VPS configuration"""
    DEBUG = False
    DEPLOYMENT_MODE = 'hostinger'

    # Full processing capabilities (updated for PAID tier)
    MAX_CONTENT_LENGTH = 400 * 1024 * 1024  # 400MB max (buffer for PAID tier 300MB limit)
    MAX_SERVER_DURATION_SECONDS = 600  # 10 minutes max per video (PAID tier: 20 videos × 300MB)
    MAX_BATCH_SIZE = 20  # PAID tier: up to 20 videos

    # Tier limits (FREE vs PAID)
    TIER_LIMITS = {
        'free': {
            'max_files': 3,
            'max_file_size_mb': 50,
            'max_duration_seconds': 75,
            'max_ratios': 2,
            'daily_limit': 3,
            'mode': 'browser'
        },
        'paid': {
            'max_files': 20,
            'max_file_size_mb': 300,
            'max_duration_seconds': 180,
            'max_ratios': 4,
            'daily_limit': None,
            'mode': 'server'
        }
    }

    # Parallel processing based on CPU cores
    # For VPS 4 (4 vCPU): 2 parallel jobs is safe
    # For VPS 8 (8 vCPU): 4 parallel jobs
    MAX_PARALLEL_JOBS = int(os.getenv('MAX_PARALLEL_JOBS', 2))

    # Backend API URL (self)
    BACKEND_API_URL = os.getenv('BACKEND_API_URL', 'https://api.yourdomain.com')

    # CORS: Allow Vercel frontend
    CORS_ORIGINS = os.getenv('CORS_ORIGINS', 'https://autoframe.vercel.app,https://yourdomain.com').split(',')


# Configuration mapping
config_map = {
    'development': DevelopmentConfig,
    'vercel': VercelConfig,
    'hostinger': HostingerConfig,
}

# Get current configuration
def get_config():
    """Get configuration based on DEPLOYMENT_MODE environment variable"""
    mode = os.getenv('DEPLOYMENT_MODE', 'development').lower()
    return config_map.get(mode, DevelopmentConfig)


# Export current config
current_config = get_config()
