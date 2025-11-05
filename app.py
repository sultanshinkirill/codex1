# Pillow 10 compatibility shim
try:
    from PIL import Image
    if not hasattr(Image, "ANTIALIAS"): Image.ANTIALIAS = Image.Resampling.LANCZOS
except Exception:
    pass

import io
import json
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from threading import Semaphore, Thread
import time
import shutil

import numpy as np
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
    session,
)
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image, ImageFilter
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from proglog import ProgressBarLogger

from moviepy.editor import CompositeVideoClip, VideoFileClip
from moviepy.video.VideoClip import ColorClip

if not hasattr(Image, "ANTIALIAS"):
    resample_filter = None
    try:
        resample_filter = Image.Resampling.LANCZOS  # Pillow >= 9.1
    except AttributeError:
        pass
    if resample_filter is None:
        resample_filter = getattr(Image, "LANCZOS", None)
    if resample_filter is None:
        resample_filter = getattr(Image, "BICUBIC", None)
    if resample_filter is not None:
        Image.ANTIALIAS = resample_filter
        if hasattr(Image, "Resampling") and not hasattr(Image.Resampling, "ANTIALIAS"):
            setattr(Image.Resampling, "ANTIALIAS", resample_filter)

MAX_FILES_PER_BATCH = int(os.environ.get("MAX_FILES_PER_BATCH", "10"))
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", str(120 * 1024 * 1024)))

ALLOWED_EXTENSIONS = {"mp4", "mov", "m4v", "mkv"}
STYLE_LABELS = {
    "blur": "Blurred background letterbox",
    "fill": "Fill & crop",
    "black": "Black background letterbox",
}
STYLE_SHORT_LABELS = {
    "blur": "blur",
    "fill": "fill",
    "black": "black",
}
ASPECT_OPTIONS = {
    "portrait": {
        "label": "9:16 Portrait",
        "short": "9x16",
        "description": "Vertical stories / shorts",
        "size": (1080, 1920),
        "suffix": "1080x1920",
    },
    "four_five": {
        "label": "4:5 Portrait",
        "short": "4x5",
        "description": "Feeds & portrait posts",
        "size": (1080, 1350),
        "suffix": "1080x1350",
    },
    "square": {
        "label": "1:1 Square",
        "short": "1x1",
        "description": "Feeds & carousels",
        "size": (1080, 1080),
        "suffix": "1080x1080",
    },
    "landscape": {
        "label": "16:9 Landscape",
        "short": "16x9",
        "description": "YouTube & players",
        "size": (1920, 1080),
        "suffix": "1920x1080",
    },
}
SUMMARY_FILENAME = "batch_summary.json"
PATTERN_PRESETS = {
    "base_ratio": "{base_clean}_{ratio}",
    "base_ratio_style": "{base_clean}__{ratio}__{style}",
    "base_dash_ratio": "{base_clean}-{ratio}",
    "base_style": "{base_clean}__{style}",
}
DEFAULT_PATTERN_KEY = "base_ratio"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv"}
DATE_FORMAT = "%Y-%m-%d"
JOB_PROGRESS: dict[str, dict] = {}

def update_job_progress(job_id: str, value: float, status: str = "processing") -> None:
    JOB_PROGRESS[job_id] = {
        "progress": max(0.0, min(1.0, value)),
        "status": status,
    }


def clear_job_progress(job_id: str) -> None:
    JOB_PROGRESS.pop(job_id, None)


class JobProgressLogger(ProgressBarLogger):
    def __init__(self, job_id: str, ratio_index: int, ratio_total: int):
        super().__init__()
        self.job_id = job_id
        self.ratio_index = ratio_index
        self.ratio_total = max(1, ratio_total)

    def bars_callback(self, bar, attr, value, old_value=None):
        bar_data = self.bars.get(bar)
        if not bar_data:
            return
        total = bar_data.get("total") or 0
        if total <= 0:
            return
        # attr == "index" gives frame count processed
        if attr == "index":
            fraction = min(1.0, value / total)
            overall = (self.ratio_index + fraction) / self.ratio_total
            update_job_progress(self.job_id, overall, "processing")

    def callback(self, **changes):  # pragma: no cover - proglog internal usage
        pass

class ClipTooLongError(Exception):
    """Raised when the uploaded clip exceeds the permitted duration."""

RESOLUTION_PATTERN = re.compile(
    r"""
    (?<!\w)                            # no word char before
    [\(\[\{<_-]*\s*                    # optional wrappers
    (?P<num1>\d{3,4})\s*
    (?:x|×|\*|/|:|-|_|\.|\s+by\s+)\s*  # separators
    (?P<num2>\d{3,4})\s*
    (?:px)?\s*
    [\)\]\}>_-]*                       # optional wrappers
    (?!\w)                             # no word char after
    """,
    re.IGNORECASE | re.VERBOSE,
)
ASPECT_RATIO_PATTERN = re.compile(
    r"""
    (?<!\w)
    [\(\[\{<_-]*\s*
    (?:
        (?:\d(?:\.\d+)?)\s*(?:x|×|:|/|\s+by\s+)\s*(?:\d(?:\.\d+)?)
    )
    \s*[\)\]\}>_-]*
    (?!\w)
    """,
    re.IGNORECASE | re.VERBOSE,
)
ASPECT_WORD_PATTERN = re.compile(
    r"(?<!\w)[\(\[\{<_-]*(portrait|vertical|landscape|square)[\)\]\}>_-]*(?!\w)",
    re.IGNORECASE,
)


class SafeFormatDict(dict):
    def __missing__(self, key):
        return ""

app = Flask(__name__)

# Load deployment-specific configuration
try:
    from config import current_config
    app.config.from_object(current_config)
    DEPLOYMENT_MODE = current_config.DEPLOYMENT_MODE
except ImportError:
    # Fallback if config.py doesn't exist
    DEPLOYMENT_MODE = os.getenv('DEPLOYMENT_MODE', 'development')
    app.config.setdefault("MAX_CONTENT_LENGTH", 200 * 1024 * 1024)  # 200 MB

# Enable CORS for hybrid deployment (Vercel frontend + Hostinger backend)
CORS(app, resources={
    r"/*": {
        "origins": app.config.get('CORS_ORIGINS', ['*']),
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Add COOP/COEP headers for SharedArrayBuffer support (required for ffmpeg.wasm)
@app.after_request
def add_security_headers(response):
    """
    Add Cross-Origin-Opener-Policy and Cross-Origin-Embedder-Policy headers
    These are REQUIRED for SharedArrayBuffer to work in modern browsers
    Without these, browser-based rendering (ffmpeg.wasm) will fail
    """
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Embedder-Policy'] = 'require-corp'
    return response

# Rate limiting with composite key (session + IP) to prevent bypasses
def get_rate_limit_key():
    """
    Composite rate limit key using both session token and IP
    Prevents bypass via cookie clearing or incognito mode
    """
    from auth import get_client_ip
    # Import here to avoid circular dependency
    token = session.get('token', 'anonymous')
    ip = get_client_ip()
    return f"{token}:{ip}"

limiter = Limiter(
    app=app,
    key_func=get_rate_limit_key,
    default_limits=["200 per hour"],  # Global default
    storage_uri="memory://",  # Use memory storage for MVP (Redis in production)
    headers_enabled=True,
)

# Session management - CRITICAL: Must set SECRET_KEY
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    # For development only - in production this MUST be set via environment
    if DEPLOYMENT_MODE == 'development':
        SECRET_KEY = "dev-secret-change-in-production-" + secrets.token_hex(16)
        print("⚠️  WARNING: Using auto-generated SECRET_KEY. Set SECRET_KEY env var in production!")
    else:
        raise RuntimeError("SECRET_KEY environment variable must be set in production!")

app.secret_key = SECRET_KEY

# Configure session for HTTPS (production)
if DEPLOYMENT_MODE in ['hostinger', 'vercel']:
    app.config['SESSION_COOKIE_SECURE'] = True  # Only send cookie over HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours

# Import auth functions
from auth import init_session, reset_daily_counter, check_tier_usage, increment_usage, get_tier, set_tier, get_usage_stats

# Initialize session before each request
@app.before_request
def before_request():
    """Initialize session and reset daily counters"""
    init_session()
    reset_daily_counter()

app.config.setdefault("MAX_UPLOAD_FILES", MAX_FILES_PER_BATCH)
app.config.setdefault("MAX_UPLOAD_SIZE_BYTES", MAX_UPLOAD_SIZE_BYTES)

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", app.config.get("UPLOAD_FOLDER", "uploads")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", app.config.get("OUTPUT_FOLDER", "outputs")))
for directory in (UPLOAD_DIR, OUTPUT_DIR):
    directory.mkdir(exist_ok=True, parents=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["OUTPUT_FOLDER"] = OUTPUT_DIR

# Concurrency control for video processing (MVP: max 2 concurrent jobs)
MAX_CONCURRENT_JOBS = 2
processing_semaphore = Semaphore(MAX_CONCURRENT_JOBS)

# Cleanup worker configuration
CLEANUP_INTERVAL_HOURS = 1  # Run cleanup every hour
CLEANUP_MAX_AGE_HOURS = int(os.environ.get("AUTO_CLEANUP_HOURS", 1))  # Delete files older than 1 hour
_cleanup_thread = None
_cleanup_shutdown = False


def cleanup_old_files():
    """
    Background worker that cleans up old files from uploads and outputs directories
    Runs periodically to prevent disk space issues
    """
    global _cleanup_shutdown

    logger = logging.getLogger(__name__)
    logger.info(f"Cleanup worker started (interval: {CLEANUP_INTERVAL_HOURS}h, max_age: {CLEANUP_MAX_AGE_HOURS}h)")

    while not _cleanup_shutdown:
        try:
            time.sleep(CLEANUP_INTERVAL_HOURS * 3600)  # Sleep for configured interval

            if _cleanup_shutdown:
                break

            cutoff_time = datetime.now() - timedelta(hours=CLEANUP_MAX_AGE_HOURS)
            deleted_files = 0
            freed_bytes = 0

            # Clean uploads directory
            for file_path in UPLOAD_DIR.glob("*"):
                if file_path.is_file():
                    try:
                        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if mtime < cutoff_time:
                            file_size = file_path.stat().st_size
                            file_path.unlink()
                            deleted_files += 1
                            freed_bytes += file_size
                    except Exception as e:
                        logger.warning(f"Failed to delete upload file {file_path}: {e}")

            # Clean outputs directory (entire job directories)
            for job_dir in OUTPUT_DIR.glob("*"):
                if job_dir.is_dir():
                    try:
                        mtime = datetime.fromtimestamp(job_dir.stat().st_mtime)
                        if mtime < cutoff_time:
                            dir_size = sum(f.stat().st_size for f in job_dir.glob("**/*") if f.is_file())
                            shutil.rmtree(job_dir)
                            deleted_files += 1
                            freed_bytes += dir_size
                    except Exception as e:
                        logger.warning(f"Failed to delete output directory {job_dir}: {e}")

            if deleted_files > 0:
                freed_mb = freed_bytes / (1024 * 1024)
                logger.info(f"Cleanup completed: {deleted_files} items deleted, {freed_mb:.1f} MB freed")

            # Check disk space and warn if low
            try:
                disk_usage = shutil.disk_usage(OUTPUT_DIR)
                free_percent = (disk_usage.free / disk_usage.total) * 100
                if free_percent < 10:
                    logger.warning(f"Low disk space: {free_percent:.1f}% free ({disk_usage.free / (1024**3):.1f} GB)")
            except Exception as e:
                logger.warning(f"Failed to check disk space: {e}")

        except Exception as e:
            logger.error(f"Cleanup worker error: {e}")

    logger.info("Cleanup worker stopped")


def start_cleanup_worker():
    """Start the cleanup background thread"""
    global _cleanup_thread, _cleanup_shutdown

    if _cleanup_thread is not None:
        return

    _cleanup_shutdown = False
    _cleanup_thread = Thread(target=cleanup_old_files, daemon=True, name="CleanupWorker")
    _cleanup_thread.start()
    logging.info("Cleanup worker thread started")


def stop_cleanup_worker():
    """Stop the cleanup background thread"""
    global _cleanup_thread, _cleanup_shutdown

    if _cleanup_thread is None:
        return

    _cleanup_shutdown = True
    _cleanup_thread.join(timeout=5)
    _cleanup_thread = None
    logging.info("Cleanup worker thread stopped")


# Start cleanup worker on app startup
start_cleanup_worker()


@app.context_processor
def inject_config():
    """Inject configuration variables into templates"""
    return {
        "ADS_CLIENT": os.environ.get("ADS_CLIENT"),
        "ADS_SLOT_TOP": os.environ.get("ADS_SLOT_TOP"),
        "ADS_SLOT_BOTTOM": os.environ.get("ADS_SLOT_BOTTOM"),
        "ADS_SLOT_HERO": os.environ.get("ADS_SLOT_HERO"),
        "ADS_SLOT_INLINE": os.environ.get("ADS_SLOT_INLINE"),
        "ADS_REWARDED_SLOT": os.environ.get("ADS_REWARDED_SLOT"),
        "HOSTINGER_API_URL": app.config.get("BACKEND_API_URL", ""),
        "CLIENT_DURATION_LIMIT": app.config.get("CLIENT_DURATION_LIMIT_SECONDS", 75),
    }


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def strip_known_extensions(name: str) -> str:
    base = name
    while True:
        stem, suffix = os.path.splitext(base)
        if suffix.lower() in VIDEO_EXTENSIONS and stem:
            base = stem
        else:
            break
    return base


def remove_naming_tokens(text: str) -> str:
    cleaned = text
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = RESOLUTION_PATTERN.sub(" ", cleaned)
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = ASPECT_RATIO_PATTERN.sub(" ", cleaned)
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = ASPECT_WORD_PATTERN.sub(" ", cleaned)
    cleaned = re.sub(r"[._\-]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def sanitize_component(text: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("_-. ")
    return sanitized


def clean_stub(original_name: str, *, keep_tokens: bool = False) -> str:
    base = strip_known_extensions(original_name)
    base = base.strip()
    if not keep_tokens:
        base = remove_naming_tokens(base)
    base = sanitize_component(base)
    return base or "clip"


def sanitize_filename(candidate: str, *, ext: str) -> str:
    name, current_ext = os.path.splitext(candidate)
    if not name:
        name = "clip"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    name = re.sub(r"_{2,}", "__", name)
    name = re.sub(r"-{2,}", "--", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("_-.")
    if not name:
        name = "clip"
    full_ext = f".{ext.lstrip('.')}" if ext else current_ext
    full_name = f"{name}{full_ext}"
    if len(full_name) > 120:
        max_name_len = 120 - len(full_ext)
        name = name[: max(1, max_name_len)]
        full_name = f"{name}{full_ext}"
    return full_name


def ensure_unique_name(directory: Path, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    counter = 1
    candidate = filename
    while (directory / candidate).exists():
        candidate = f"{base}__{counter:03d}{ext}"
        counter += 1
    return candidate


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_base_overrides(raw_value: Optional[str]) -> dict[str, str]:
    if not raw_value:
        return {}
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError:
        logging.warning("Failed to parse base overrides payload")
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or value is None:
            continue
        cleaned[str(key)] = str(value)
    return cleaned


def build_naming_config(job_id: str, form) -> dict:
    summary = load_summary(job_id)
    summary_config = summary.get("config") or {}

    mode_value = (form.get("naming_mode") if form else None) or summary_config.get("mode", "auto")
    mode_value = "custom" if mode_value == "custom" else "auto"

    date_stamp = summary_config.get("date_stamp") or datetime.utcnow().strftime(DATE_FORMAT)

    if mode_value == "auto":
        return {
            "mode": "auto",
            "pattern_choice": DEFAULT_PATTERN_KEY,
            "pattern": PATTERN_PRESETS[DEFAULT_PATTERN_KEY],
            "custom_pattern": "",
            "auto_clean": True,
            "keep_tokens": False,
            "add_sequence": False,
            "append_date": False,
            "label_mode": "short",
            "date_stamp": date_stamp,
        }

    pattern_choice = form.get("naming_preset") if form else None
    if not pattern_choice:
        pattern_choice = summary_config.get("pattern_choice", DEFAULT_PATTERN_KEY)
    if pattern_choice not in PATTERN_PRESETS and pattern_choice != "custom":
        pattern_choice = DEFAULT_PATTERN_KEY

    custom_pattern = (form.get("naming_custom_pattern") if form else None) or summary_config.get("custom_pattern", "")
    custom_pattern = custom_pattern.strip()

    auto_clean = parse_bool(form.get("naming_auto_clean"), summary_config.get("auto_clean", True))
    keep_tokens = parse_bool(form.get("naming_keep_tokens"), summary_config.get("keep_tokens", False))
    if keep_tokens:
        auto_clean = False

    add_sequence = parse_bool(form.get("naming_add_sequence"), summary_config.get("add_sequence", True))
    append_date = parse_bool(form.get("naming_append_date"), summary_config.get("append_date", True))

    label_mode = (form.get("naming_label_mode") if form else None) or summary_config.get("label_mode", "short")
    if label_mode not in {"short", "friendly"}:
        label_mode = "short"

    if pattern_choice == "custom" and custom_pattern:
        pattern = custom_pattern
    else:
        pattern = PATTERN_PRESETS.get(pattern_choice, PATTERN_PRESETS[DEFAULT_PATTERN_KEY])

    return {
        "mode": "custom",
        "pattern_choice": pattern_choice,
        "pattern": pattern,
        "custom_pattern": custom_pattern,
        "auto_clean": auto_clean,
        "keep_tokens": keep_tokens,
        "add_sequence": add_sequence,
        "append_date": append_date,
        "label_mode": label_mode,
        "date_stamp": date_stamp,
    }


def serialize_config(config: dict) -> dict:
    keys = (
        "mode",
        "pattern_choice",
        "pattern",
        "custom_pattern",
        "auto_clean",
        "keep_tokens",
        "add_sequence",
        "append_date",
        "label_mode",
        "date_stamp",
    )
    return {key: config.get(key) for key in keys if key in config}


def prepare_base_info(original_name: str, override: Optional[str], config: dict) -> dict:
    source_name = override.strip() if override else original_name
    base_candidate = strip_known_extensions(source_name)
    base_candidate = base_candidate.strip()
    sanitized_base = sanitize_component(base_candidate) or "clip"
    if config.get("auto_clean", True) and not config.get("keep_tokens", False):
        base_clean = clean_stub(base_candidate, keep_tokens=False)
    else:
        base_clean = sanitized_base
    if not base_clean:
        base_clean = "clip"
    return {
        "base": sanitized_base,
        "base_clean": base_clean,
        "original_filename": original_name,
    }


def generate_output_filename(
    base_info: dict,
    aspect_key: str,
    style_key: str,
    config: dict,
    seq_number: Optional[int],
    ext: str,
    output_dir: Path,
) -> tuple[str, dict]:
    ratio_meta = ASPECT_OPTIONS.get(aspect_key, {})
    style_long = STYLE_LABELS.get(style_key, style_key)
    style_short = STYLE_SHORT_LABELS.get(style_key, style_key)
    ratio_short = ratio_meta.get("short", aspect_key)
    ratio_friendly = ratio_meta.get("label", aspect_key)

    label_mode = config.get("label_mode", "short")
    ratio_token = ratio_short if label_mode == "short" else ratio_friendly
    style_token = style_short if label_mode == "short" else style_long

    seq_value = f"{seq_number:03d}" if seq_number is not None else ""
    date_value = config.get("date_stamp", "")

    tokens = SafeFormatDict(
        {
            "base": base_info["base"],
            "base_clean": base_info["base_clean"],
            "ratio": ratio_token,
            "style": style_token,
            "w": ratio_meta.get("size", (0, 0))[0],
            "h": ratio_meta.get("size", (0, 0))[1],
            "date": date_value,
            "seq": seq_value,
            "ext": ext.lstrip("."),
        }
    )

    pattern = config.get("pattern") or PATTERN_PRESETS[DEFAULT_PATTERN_KEY]
    formatted = pattern.format_map(tokens).strip("_- ")
    if not formatted:
        formatted = f"{tokens['base_clean']}_{ratio_token}"

    normalized_pattern = pattern.lower()
    if config.get("add_sequence", True) and "{seq}" not in normalized_pattern and seq_value:
        formatted = f"{formatted}__{seq_value}"
    if config.get("append_date", True) and "{date}" not in normalized_pattern and date_value:
        formatted = f"{formatted}__{date_value}"

    filename = sanitize_filename(formatted, ext=ext)
    filename = ensure_unique_name(output_dir, filename)
    tokens["ratio_token"] = ratio_token
    tokens["style_token"] = style_token
    tokens["filename"] = filename
    return filename, tokens


def load_summary(job_id: str) -> dict:
    summary_path = OUTPUT_DIR / job_id / SUMMARY_FILENAME
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text())
            if "videos" not in data:
                data["videos"] = []
            if "config" not in data:
                data["config"] = {}
            return data
        except json.JSONDecodeError:
            logging.warning("Failed to parse summary for job %s", job_id)
    return {"videos": [], "config": {}}


def append_summary(job_id: str, entry: dict, config: Optional[dict] = None) -> None:
    summary = load_summary(job_id)
    summary.setdefault("videos", []).append(entry)
    if config:
        summary["config"] = serialize_config(config)
    summary["updated_at"] = datetime.utcnow().isoformat()
    summary_path = OUTPUT_DIR / job_id / SUMMARY_FILENAME
    summary_path.write_text(json.dumps(summary, indent=2))


def resize_by_factor(clip: VideoFileClip, factor: float):
    return clip.resize(factor)


def crop_center(clip, width: int, height: int):
    return clip.crop(
        width=int(width),
        height=int(height),
        x_center=clip.w / 2,
        y_center=clip.h / 2,
    )


def normalize_orientation(clip: VideoFileClip):
    reader_rotation = getattr(getattr(clip, "reader", None), "rotation", None)
    raw_rotation = getattr(clip, "rotation", None)
    rotation = raw_rotation if raw_rotation not in (None, 0) else reader_rotation
    try:
        angle = int(rotation or 0) % 360
    except (TypeError, ValueError):
        angle = 0

    if angle in (90, 180, 270):
        clip = clip.rotate(angle, expand=True)
        clip.rotation = 0
        if getattr(clip, "reader", None) and hasattr(clip.reader, "rotation"):
            clip.reader.rotation = 0
    return clip

def apply_gaussian_blur(clip, radius: float):
    def blur_frame(frame):
        return np.array(Image.fromarray(frame).filter(ImageFilter.GaussianBlur(radius)))

    blurred = clip.fl_image(blur_frame)
    if clip.mask is not None:
        blurred.mask = clip.mask
    return blurred


def build_blurred_letterbox(clip: VideoFileClip, target_size: tuple[int, int]):
    target_w, target_h = target_size
    # Keep the original framing centered within the target size.
    fit_scale = min(target_w / clip.w, target_h / clip.h)
    letterboxed = resize_by_factor(clip, fit_scale).set_position(("center", "center"))

    # Create a blurred background that fills the target canvas.
    fill_scale = max(target_w / clip.w, target_h / clip.h)
    background = resize_by_factor(clip, fill_scale)
    background = crop_center(background, target_w, target_h)
    background = apply_gaussian_blur(background, radius=16)

    composite = CompositeVideoClip(
        [background, letterboxed],
        size=(target_w, target_h),
    )
    if clip.audio:
        composite = composite.set_audio(clip.audio)
    return composite.set_duration(clip.duration)


def build_black_letterbox(clip: VideoFileClip, target_size: tuple[int, int]):
    target_w, target_h = target_size

    fit_scale = min(target_w / clip.w, target_h / clip.h)
    letterboxed = resize_by_factor(clip, fit_scale).set_position(("center", "center"))

    background = ColorClip(size=(target_w, target_h), color=(0, 0, 0))
    background = background.set_duration(clip.duration)

    composite = CompositeVideoClip([background, letterboxed], size=(target_w, target_h))
    if clip.audio:
        composite = composite.set_audio(clip.audio)
    return composite.set_duration(clip.duration)


def build_fill_and_crop(clip: VideoFileClip, target_size: tuple[int, int]):
    target_w, target_h = target_size
    scale = max(target_w / clip.w, target_h / clip.h)
    filled = resize_by_factor(clip, scale)
    cropped = crop_center(filled, target_w, target_h)
    if clip.audio:
        cropped = cropped.set_audio(clip.audio)
    return cropped.set_duration(clip.duration)




def process_video_file(
    file_storage: FileStorage,
    style: str,
    job_id: str,
    ratios: list[str],
    naming_config: dict,
    base_override: Optional[str] = None,
):
    raw_filename = file_storage.filename or ""
    original_name = secure_filename(raw_filename)
    if not original_name:
        raise ValueError("Missing filename")

    extension = Path(original_name).suffix.lower() or ".mp4"
    upload_target = app.config["UPLOAD_FOLDER"] / f"{job_id}_{uuid.uuid4().hex}{extension}"
    file_storage.save(upload_target)

    with VideoFileClip(str(upload_target)) as tmp_clip:
        duration = getattr(tmp_clip, "duration", None)
        if duration and duration > 180:
            try:
                upload_target.unlink()
            except Exception:
                pass
            raise ClipTooLongError("Max video length for MVP is 3 minutes (180 seconds).")

    summary = load_summary(job_id)
    base_info = prepare_base_info(raw_filename or original_name, base_override, naming_config)
    existing_outputs = sum(len(video.get("outputs", [])) for video in summary.get("videos", []))
    naming_state = {
        "config": naming_config,
        "base_info": base_info,
        "sequence_start": existing_outputs,
    }
    update_job_progress(job_id, 0.0, "processing")

    output_dir = app.config["OUTPUT_FOLDER"] / job_id
    try:
        outputs = render_variants(upload_target, output_dir, style, job_id, ratios, naming_state)
    except Exception:
        update_job_progress(job_id, 1.0, "error")
        raise
    finally:
        try:
            upload_target.unlink()
        except FileNotFoundError:
            pass

    ratio_labels = []
    for item in outputs:
        label = item.get("ratio_label")
        if label and label not in ratio_labels:
            ratio_labels.append(label)
    summary_entry = {
        "original_name": original_name,
        "display_name": base_info["base_clean"],
        "style": STYLE_LABELS.get(style, style),
        "ratios": ratios,
        "ratio_labels": ratio_labels or [ASPECT_OPTIONS[r]["label"] for r in ratios if r in ASPECT_OPTIONS],
        "outputs": [item["filename"] for item in outputs],
    }
    append_summary(job_id, summary_entry, naming_config)
    clear_job_progress(job_id)

    return {
        "original_name": original_name,
        "display_name": base_info["base_clean"],
        "style_label": STYLE_LABELS.get(style, style),
        "selected_ratios": ratios,
        "ratio_labels": ratio_labels or [ASPECT_OPTIONS[r]["label"] for r in ratios if r in ASPECT_OPTIONS],
        "outputs": outputs,
    }


@app.route("/health")
def health_check():
    """Health check endpoint for deployment monitoring"""
    return jsonify({
        "status": "healthy",
        "deployment_mode": DEPLOYMENT_MODE,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }), 200


@app.route('/upgrade', methods=['POST'])
def upgrade_tier():
    """Upgrade/downgrade user tier (MVP - no payment)"""
    data = request.get_json()
    new_tier = data.get('tier', 'free')

    if set_tier(new_tier):
        return jsonify({'tier': new_tier, 'message': f'Switched to {new_tier.upper()} tier'}), 200
    else:
        return jsonify({'error': 'Invalid tier'}), 400


@app.route('/usage')
def get_usage():
    """Get current user's usage statistics"""
    stats = get_usage_stats()
    return jsonify(stats), 200


@app.route('/increment-usage', methods=['POST'])
@limiter.limit("20 per hour")  # Allow multiple client-side renders per hour
def increment_usage_endpoint():
    """Increment usage counter (called after client-side rendering)"""
    increment_usage()
    return jsonify({'status': 'ok'}), 200


@app.route("/progress/<job_id>")
def get_progress(job_id):
    """Get progress for server-side processing jobs"""
    progress_data = JOB_PROGRESS.get(job_id, {
        "progress": 0.0,
        "status": "not_found"
    })
    return jsonify(progress_data), 200


@app.route("/")
def index():
    return render_template(
        "index.html",
        styles=STYLE_LABELS,
        aspects=ASPECT_OPTIONS,
        style_short_labels=STYLE_SHORT_LABELS,
        default_aspects=("portrait", "four_five", "square"),
    )


@app.route("/process", methods=["POST"])
def process_upload():
    style = request.form.get("style", "blur")
    files = request.files.getlist("videos")
    if not files:
        fallback = request.files.get("video")
        files = [fallback] if fallback else []

    valid_files = [f for f in files if f and f.filename]
    if not valid_files:
        flash("Select at least one video before submitting.")
        return redirect(url_for("index"))

    if len(valid_files) > 10:
        flash("You can upload up to 10 videos per batch.")
        return redirect(url_for("index"))

    unsupported = [f.filename for f in valid_files if not allowed_file(f.filename)]
    if unsupported:
        flash(f"Unsupported formats detected: {', '.join(unsupported)}.")
        return redirect(url_for("index"))

    ratios = request.form.getlist("ratios") or request.form.getlist("ratio")
    ratios = [r for r in ratios if r in ASPECT_OPTIONS]
    if not ratios:
        flash("Select at least one output aspect ratio (choose up to two).")
        return redirect(url_for("index"))
    if len(ratios) > 3:
        flash("Please pick no more than three aspect ratios per batch.")
        ratios = ratios[:3]

    style = style if style in STYLE_LABELS else "blur"
    job_id = request.form.get("batch_id") or uuid.uuid4().hex
    naming_config = build_naming_config(job_id, request.form)
    override_map = parse_base_overrides(request.form.get("base_overrides"))

    results = []
    errors = []
    for file_storage in valid_files:
        try:
            raw_name = file_storage.filename or ""
            override = override_map.get(raw_name) or override_map.get(secure_filename(raw_name))
            results.append(
                process_video_file(
                    file_storage,
                    style,
                    job_id,
                    ratios,
                    naming_config,
                    override,
                )
            )
        except ClipTooLongError:
            flash("Max video length for MVP is 3 minutes (180 seconds).")
            continue
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            logging.exception("Video rendering failed")
            errors.append(f"{file_storage.filename}: {exc}")

    if errors:
        flash("Some files failed to render: " + "; ".join(errors))

    if not results:
        if not errors:
            flash("Nothing was rendered.")
        return redirect(url_for("index"))

    return render_template(
        "result.html",
        job_id=job_id,
        results=results,
        style_label=STYLE_LABELS.get(style, style),
        download_all=url_for("download_bundle", job_id=job_id),
    )


@app.post("/api/process")
@limiter.limit("10 per hour")  # Strict limit for server processing
def api_process():
    # CRITICAL: Block FREE tier from using server rendering
    user_tier = get_tier()
    if user_tier == 'free':
        return {"error": "FREE tier must use browser rendering. Upgrade to PAID for server processing."}, 403

    # Check Content-Length before reading request body (prevent bandwidth waste)
    content_length = request.content_length
    max_size = 400 * 1024 * 1024  # 400 MB (buffer above 300MB tier limit)
    if content_length and content_length > max_size:
        return {"error": f"File too large. Maximum upload size is 300MB."}, 413

    # Check usage limits before processing
    can_process, error_msg = check_tier_usage()
    if not can_process:
        return {"error": error_msg}, 429

    # Check if server is at capacity
    if not processing_semaphore.acquire(blocking=False):
        return {"error": f"Server at capacity ({MAX_CONCURRENT_JOBS} jobs processing). Please wait and try again."}, 503

    style = request.form.get("style", "blur")
    file_storage = request.files.get("video")

    if not file_storage or file_storage.filename == "":
        processing_semaphore.release()
        return {"error": "Select a video to upload."}, 400

    if not allowed_file(file_storage.filename):
        processing_semaphore.release()
        return {"error": "Supported formats: mp4, mov, m4v, mkv."}, 400

    ratios = request.form.getlist("ratios") or request.form.getlist("ratio")
    ratios = [r for r in ratios if r in ASPECT_OPTIONS]
    if not ratios:
        processing_semaphore.release()
        return {"error": "Select at least one aspect ratio (max two)."}, 400
    if len(ratios) > 3:
        ratios = ratios[:3]

    style = style if style in STYLE_LABELS else "blur"
    batch_id = request.form.get("batch_id") or uuid.uuid4().hex
    naming_config = build_naming_config(batch_id, request.form)
    base_override = request.form.get("base_override")

    try:
        result = process_video_file(
            file_storage,
            style,
            batch_id,
            ratios,
            naming_config,
            base_override,
        )
    except ClipTooLongError:
        processing_semaphore.release()
        return {"error": "Max video length for MVP is 3 minutes (180 seconds)."}, 400
    except Exception as exc:  # pragma: no cover
        logging.exception("Video rendering failed")
        processing_semaphore.release()
        return {"error": str(exc)}, 500
    finally:
        # Always release semaphore
        processing_semaphore.release()

    # Increment usage counter after successful processing
    increment_usage()

    return {
        "status": "ok",
        "batch_id": batch_id,
        "result": result,
        "downloads": {
            "bundle": url_for("download_bundle", job_id=batch_id),
        },
    }


@app.route("/download/<job_id>/<path:filename>")
def download(job_id: str, filename: str):
    target_dir = app.config["OUTPUT_FOLDER"] / job_id
    if not target_dir.exists():
        abort(404)
    return send_from_directory(target_dir, filename, as_attachment=True)


@app.route("/download/<job_id>/bundle")
def download_bundle(job_id: str):
    target_dir = app.config["OUTPUT_FOLDER"] / job_id
    if not target_dir.exists():
        abort(404)

    buffer = io.BytesIO()
    files_added = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(target_dir.glob("*.mp4")):
            archive.write(path, arcname=path.name)
            files_added += 1

    if files_added == 0:
        abort(404)

    buffer.seek(0)
    summary = load_summary(job_id)
    date_stamp = summary.get("config", {}).get("date_stamp") or datetime.utcnow().strftime(DATE_FORMAT)
    filename = f"Free_AutoFrame__{date_stamp}.zip"
    return send_file(
        buffer,
        mimetype="application/zip",
        download_name=filename,
        as_attachment=True,
    )


@app.get("/progress/<job_id>")
def job_progress(job_id: str):
    entry = JOB_PROGRESS.get(job_id)
    if not entry:
        return {"progress": 0.0, "status": "unknown"}, 404
    return entry


def write_clip(
    clip_obj,
    output_dir: Path,
    fps: float,
    aspect_key: str,
    style: str,
    job_id: str,
    naming_state: dict,
    seq_number: Optional[int],
    logger: Optional[ProgressBarLogger],
):
    config = ASPECT_OPTIONS.get(aspect_key)
    if not config:
        raise ValueError(f"Unsupported aspect key: {aspect_key}")

    filename, tokens = generate_output_filename(
        naming_state["base_info"],
        aspect_key,
        style,
        naming_state["config"],
        seq_number,
        ext="mp4",
        output_dir=output_dir,
    )
    output_path = output_dir / filename
    try:
        clip_obj.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=fps,
            preset="veryfast",
            threads=os.cpu_count() or 4,
            temp_audiofile=str(output_path.with_suffix(".m4a")),
            remove_temp=True,
            logger=logger,
        )
    finally:
        clip_obj.close()

    result = {
        "label": f"{config['label']} • {STYLE_LABELS.get(style, style)}",
        "filename": filename,
        "aspect_key": aspect_key,
        "ratio_label": tokens.get("ratio_token", config.get("short", aspect_key)),
    }
    result["url"] = url_for("download", job_id=job_id, filename=filename)
    return result


def render_variants(
    input_path: Path,
    output_dir: Path,
    style: str,
    job_id: str,
    ratios: list[str],
    naming_state: dict,
) -> list[dict]:
    output_dir.mkdir(exist_ok=True, parents=True)
    outputs: list[dict] = []

    with VideoFileClip(str(input_path)) as clip:
        clip = normalize_orientation(clip)
        fps = getattr(clip, "fps", None) or getattr(clip.reader, "fps", 30)

        builder = build_fill_and_crop if style == "fill" else build_blurred_letterbox
        if style == "black":
            builder = build_black_letterbox

        ratio_total = max(1, len(ratios))
        for idx, aspect_key in enumerate(ratios):
            config = ASPECT_OPTIONS.get(aspect_key)
            if not config:
                continue
            target_clip = builder(clip, config["size"])
            seq_number = naming_state["sequence_start"] + len(outputs) + 1
            logger = JobProgressLogger(job_id, idx, ratio_total)
            update_job_progress(job_id, idx / ratio_total, "processing")
            outputs.append(
                write_clip(
                    target_clip,
                    output_dir,
                    fps,
                    aspect_key,
                    style,
                    job_id,
                    naming_state,
                    seq_number,
                    logger,
                )
            )

    update_job_progress(job_id, 1.0, "done")
    return outputs
