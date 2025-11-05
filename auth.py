"""
Authentication and session management for Free AutoFrame
Handles tier enforcement with session + IP-based tracking
"""

import secrets
from datetime import date
from flask import session, request


# In-memory IP usage tracking (for MVP - use Redis in production)
# Format: {ip: {date: count}}
ip_usage_tracker = {}


def init_session():
    """Initialize anonymous user session with tier and usage tracking"""
    if 'token' not in session:
        session['token'] = secrets.token_hex(16)
        session['tier'] = 'free'  # Default tier
        session['renders_today'] = 0
        session['last_reset'] = date.today().isoformat()
        session.permanent = False  # Session expires when browser closes


def reset_daily_counter():
    """Reset usage counter if it's a new day"""
    today = date.today().isoformat()

    if session.get('last_reset') != today:
        session['renders_today'] = 0
        session['last_reset'] = today

    # Also clean up old IP tracking data
    cleanup_old_ip_data()


def cleanup_old_ip_data():
    """Remove IP tracking data older than today"""
    today = date.today().isoformat()

    for ip in list(ip_usage_tracker.keys()):
        # Remove old dates
        ip_usage_tracker[ip] = {
            d: count for d, count in ip_usage_tracker[ip].items()
            if d == today
        }
        # Remove empty IP entries
        if not ip_usage_tracker[ip]:
            del ip_usage_tracker[ip]


def get_client_ip():
    """Get real client IP (handles proxies/load balancers)"""
    # Check X-Real-IP first (set by Nginx)
    ip = request.headers.get('X-Real-IP')

    if not ip:
        # Fall back to X-Forwarded-For
        ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()

    if not ip:
        # Last resort: direct connection IP
        ip = request.remote_addr

    return ip or 'unknown'


def get_ip_usage_count():
    """Get number of renders from this IP today"""
    ip = get_client_ip()
    today = date.today().isoformat()

    return ip_usage_tracker.get(ip, {}).get(today, 0)


def increment_ip_usage():
    """Increment render count for this IP"""
    ip = get_client_ip()
    today = date.today().isoformat()

    if ip not in ip_usage_tracker:
        ip_usage_tracker[ip] = {}

    if today not in ip_usage_tracker[ip]:
        ip_usage_tracker[ip][today] = 0

    ip_usage_tracker[ip][today] += 1


def check_tier_usage():
    """
    Check if user can process more videos based on their tier.

    Returns:
        tuple: (can_process: bool, error_message: str or None)
    """
    tier = session.get('tier', 'free')

    if tier == 'free':
        # Check BOTH session and IP (prevents bypass via cookie clearing or incognito)
        session_count = session.get('renders_today', 0)
        ip_count = get_ip_usage_count()

        FREE_DAILY_LIMIT = 3

        if session_count >= FREE_DAILY_LIMIT:
            return False, f"FREE tier: Daily limit reached ({FREE_DAILY_LIMIT} batches per session). Upgrade to PAID for unlimited."

        if ip_count >= FREE_DAILY_LIMIT:
            return False, f"FREE tier: Daily limit reached ({FREE_DAILY_LIMIT} batches per IP). Upgrade to PAID for unlimited."

    # PAID tier has no daily limits
    return True, None


def increment_usage():
    """Increment user's daily usage count (both session and IP)"""
    session['renders_today'] = session.get('renders_today', 0) + 1
    increment_ip_usage()


def get_tier():
    """Get current user tier"""
    return session.get('tier', 'free')


def set_tier(tier):
    """Set user tier (for MVP upgrade button)"""
    if tier in ['free', 'paid']:
        session['tier'] = tier
        return True
    return False


def get_usage_stats():
    """Get usage statistics for current user"""
    return {
        'tier': get_tier(),
        'renders_today': session.get('renders_today', 0),
        'ip_renders_today': get_ip_usage_count(),
        'token': session.get('token', 'none')
    }
