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
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import boto3
import redis
from redis.exceptions import RedisError
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from PIL import Image, ImageFilter
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from rq import Queue
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

S3_BUCKET = os.environ.get("S3_BUCKET") or os.environ.get("AWS_S3_BUCKET")
S3_REGION = os.environ.get("AWS_REGION")
S3_UPLOAD_PREFIX = os.environ.get("S3_UPLOAD_PREFIX", "uploads")
S3_OUTPUT_PREFIX = os.environ.get("S3_OUTPUT_PREFIX", "outputs")
UPLOAD_URL_TTL = int(os.environ.get("UPLOAD_URL_TTL", "900"))
DOWNLOAD_URL_TTL = int(os.environ.get("DOWNLOAD_URL_TTL", "86400"))
MAX_FILES_PER_BATCH = int(os.environ.get("MAX_FILES_PER_BATCH", "10"))
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", str(120 * 1024 * 1024)))
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
JOB_QUEUE_NAME = os.environ.get("JOB_QUEUE_NAME", "autoframe-jobs")
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT_SECONDS", str(60 * 60)))
JOB_RESULT_TTL = int(os.environ.get("JOB_RESULT_TTL_SECONDS", str(7 * 24 * 60 * 60)))

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

JOB_STATUS_PENDING = "pending"
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_PROCESSING = "processing"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"

ASYNC_UNAVAILABLE_CODE = "ASYNC_UNAVAILABLE"


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

JOB_PROGRESS: dict[str, dict] = {}


class ClipTooLongError(Exception):
    """Raised when the uploaded clip exceeds the permitted duration."""


class AsyncPipelineUnavailable(RuntimeError):
    """Raised when the async rendering pipeline dependencies are unavailable."""


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
app.config.setdefault("MAX_CONTENT_LENGTH", 200 * 1024 * 1024)  # 200 MB
app.config.setdefault("MAX_UPLOAD_FILES", MAX_FILES_PER_BATCH)
app.config.setdefault("MAX_UPLOAD_SIZE_BYTES", MAX_UPLOAD_SIZE_BYTES)
app.config.setdefault("S3_BUCKET", S3_BUCKET)
app.config.setdefault("S3_UPLOAD_PREFIX", S3_UPLOAD_PREFIX)
app.config.setdefault("S3_OUTPUT_PREFIX", S3_OUTPUT_PREFIX)
app.config.setdefault("UPLOAD_URL_TTL", UPLOAD_URL_TTL)
app.config.setdefault("DOWNLOAD_URL_TTL", DOWNLOAD_URL_TTL)
app.config.setdefault("REDIS_URL", REDIS_URL)
app.config.setdefault("JOB_QUEUE_NAME", JOB_QUEUE_NAME)
app.config.setdefault("JOB_TIMEOUT", JOB_TIMEOUT)
app.config.setdefault("JOB_RESULT_TTL", JOB_RESULT_TTL)

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "uploads"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "outputs"))
for directory in (UPLOAD_DIR, OUTPUT_DIR):
    directory.mkdir(exist_ok=True, parents=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["OUTPUT_FOLDER"] = OUTPUT_DIR
app.secret_key = os.environ.get("VIBE_RESIZER_SECRET", "dev-secret-change-me")


@app.context_processor
def inject_ads():
    return {
        "ADS_CLIENT": os.environ.get("ADS_CLIENT"),
        "ADS_SLOT_TOP": os.environ.get("ADS_SLOT_TOP"),
        "ADS_SLOT_BOTTOM": os.environ.get("ADS_SLOT_BOTTOM"),
        "ADS_SLOT_HERO": os.environ.get("ADS_SLOT_HERO"),
        "ADS_SLOT_INLINE": os.environ.get("ADS_SLOT_INLINE"),
        "ADS_REWARDED_SLOT": os.environ.get("ADS_REWARDED_SLOT"),
    }


def redis_connection() -> redis.Redis:
    url = app.config.get("REDIS_URL")
    if not url:
        raise AsyncPipelineUnavailable("Redis URL is not configured.")
    cache = getattr(app, "_redis_conn", None)
    if cache is None:
        try:
            cache = redis.from_url(url)
        except RedisError as exc:
            raise AsyncPipelineUnavailable("Unable to initialize Redis connection.") from exc
        app._redis_conn = cache  # type: ignore[attr-defined]
    return cache  # type: ignore[return-value]


def _clear_cached_redis_connection() -> None:
    if hasattr(app, "_redis_conn"):
        delattr(app, "_redis_conn")


def job_queue() -> Queue:
    if not hasattr(app, "_job_queue"):
        try:
            app._job_queue = Queue(
                app.config["JOB_QUEUE_NAME"],
                connection=redis_connection(),
                default_timeout=app.config["JOB_TIMEOUT"],
            )
        except AsyncPipelineUnavailable:
            raise
        except RedisError as exc:
            _clear_cached_redis_connection()
            raise AsyncPipelineUnavailable("Redis queue is unavailable.") from exc
    return app._job_queue  # type: ignore[return-value]


def is_s3_enabled() -> bool:
    return bool(app.config.get("S3_BUCKET"))


def async_pipeline_available() -> bool:
    try:
        connection = redis_connection()
        connection.ping()
    except AsyncPipelineUnavailable:
        return False
    except RedisError:
        _clear_cached_redis_connection()
        return False
    return True


def s3_client():
    if not is_s3_enabled():
        raise RuntimeError("Object storage is not configured.")
    if not hasattr(app, "_s3_client"):
        client_kwargs: dict[str, Any] = {}
        if S3_REGION:
            client_kwargs["region_name"] = S3_REGION
        app._s3_client = boto3.client("s3", **client_kwargs)
    return app._s3_client


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


def job_storage_key(job_id: str) -> str:
    return f"job:{job_id}"


def load_job_record(job_id: str) -> Optional[dict]:
    try:
        connection = redis_connection()
    except AsyncPipelineUnavailable:
        raise
    try:
        raw_value = connection.get(job_storage_key(job_id))
    except RedisError as exc:
        _clear_cached_redis_connection()
        raise AsyncPipelineUnavailable("Unable to fetch job record.") from exc
    if not raw_value:
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        logging.warning("Corrupt job record for %s", job_id)
    return None


def persist_job_record(record: dict) -> None:
    ttl = app.config.get("JOB_RESULT_TTL", JOB_RESULT_TTL)
    try:
        connection = redis_connection()
    except AsyncPipelineUnavailable:
        raise
    try:
        connection.setex(job_storage_key(record["id"]), ttl, json.dumps(record))
    except RedisError as exc:
        _clear_cached_redis_connection()
        raise AsyncPipelineUnavailable("Unable to persist job record.") from exc


def update_job_record(job_id: str, updates: dict) -> Optional[dict]:
    record = load_job_record(job_id)
    if not record:
        return None
    record.update(updates)
    persist_job_record(record)
    return record


def mark_job_status(job_id: str, status: str, extra: Optional[dict] = None) -> Optional[dict]:
    payload = {"status": status, "updated_at": datetime.utcnow().isoformat()}
    if extra:
        payload.update(extra)
    return update_job_record(job_id, payload)


def public_job_view(record: dict) -> dict:
    allowed_keys = {
        "id",
        "status",
        "style",
        "ratios",
        "files",
        "results",
        "progress",
        "created_at",
        "queued_at",
        "started_at",
        "completed_at",
        "message",
    }
    return {key: record.get(key) for key in allowed_keys if key in record}


def ensure_job_naming(record: dict) -> dict:
    naming_cfg = record.get("naming")
    if isinstance(naming_cfg, dict):
        return naming_cfg
    config = build_naming_config(record["id"], None)
    record["naming"] = config
    persist_job_record(record)
    return config


def store_job_progress(job_id: str, progress: float, message: Optional[str] = None) -> Optional[dict]:
    payload = {
        "progress": round(max(0.0, min(1.0, progress)), 4),
        "updated_at": datetime.utcnow().isoformat(),
    }
    if message is not None:
        payload["message"] = message
    return update_job_record(job_id, payload)


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


@app.route("/")
def index():
    return render_template(
        "index.html",
        styles=STYLE_LABELS,
        aspects=ASPECT_OPTIONS,
        style_short_labels=STYLE_SHORT_LABELS,
        default_aspects=("portrait", "four_five", "square"),
        async_enabled=async_pipeline_available(),
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
def api_process():
    style = request.form.get("style", "blur")
    file_storage = request.files.get("video")

    if not file_storage or file_storage.filename == "":
        return {"error": "Select a video to upload."}, 400

    if not allowed_file(file_storage.filename):
        return {"error": "Supported formats: mp4, mov, m4v, mkv."}, 400

    ratios = request.form.getlist("ratios") or request.form.getlist("ratio")
    ratios = [r for r in ratios if r in ASPECT_OPTIONS]
    if not ratios:
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
        return {"error": "Max video length for MVP is 3 minutes (180 seconds)."}, 400
    except Exception as exc:  # pragma: no cover
        logging.exception("Video rendering failed")
        return {"error": str(exc)}, 500

    return {
        "status": "ok",
        "batch_id": batch_id,
        "result": result,
        "downloads": {
            "bundle": url_for("download_bundle", job_id=batch_id),
        },
    }


def validate_reward_token(token: Optional[str]) -> bool:
    # Placeholder for rewarded ad verification hook.
    return bool(token)


@app.post("/api/upload-url")
def api_upload_url():
    if not is_s3_enabled():
        return {"error": "Object storage not configured."}, 503
    payload = request.get_json(silent=True) or {}
    filename = (payload.get("filename") or "").strip()
    if not filename or not allowed_file(filename):
        return {"error": "Provide a supported video filename."}, 400

    try:
        declared_size = int(payload.get("size") or 0)
    except (TypeError, ValueError):
        declared_size = 0
    if declared_size <= 0 or declared_size > app.config["MAX_UPLOAD_SIZE_BYTES"]:
        return {
            "error": f"Each file must be between 1 byte and {app.config['MAX_UPLOAD_SIZE_BYTES']} bytes.",
        }, 400

    content_type = (payload.get("content_type") or "video/mp4").strip()
    if not content_type.startswith("video/"):
        return {"error": "Only video uploads are supported."}, 400

    job_id = (payload.get("job_id") or "").strip() or uuid.uuid4().hex
    extension = Path(filename).suffix.lower() or ".mp4"
    key = payload.get("key") or f"{app.config['S3_UPLOAD_PREFIX'].rstrip('/')}/{job_id}/{uuid.uuid4().hex}{extension}"

    try:
        presigned = s3_client().generate_presigned_post(
            Bucket=app.config["S3_BUCKET"],
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, app.config["MAX_UPLOAD_SIZE_BYTES"]],
            ],
            ExpiresIn=app.config["UPLOAD_URL_TTL"],
        )
    except Exception:
        logging.exception("Failed to create presigned POST for %s", filename)
        return {"error": "Unable to generate upload URL."}, 500

    return {
        "job_id": job_id,
        "upload": presigned,
        "file": {
            "key": key,
            "original_name": filename,
            "content_type": content_type,
            "size": declared_size,
        },
        "expires_in": app.config["UPLOAD_URL_TTL"],
    }, 201


@app.post("/api/local-upload")
def api_local_upload():
    file_storage = request.files.get("video")
    if not file_storage or file_storage.filename == "":
        return {"error": "No video supplied."}, 400

    if not allowed_file(file_storage.filename):
        return {"error": "Unsupported format."}, 400

    job_id = request.form.get("job_id") or uuid.uuid4().hex
    safe_name = secure_filename(file_storage.filename)
    target_dir = app.config["UPLOAD_FOLDER"] / job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    file_storage.save(target_path)

    return {
        "job_id": job_id,
        "file": {
            "key": f"{job_id}/{safe_name}",
            "original_name": file_storage.filename,
            "size": target_path.stat().st_size,
            "content_type": file_storage.mimetype or "video/mp4",
        },
    }, 201


@app.post("/api/jobs")
def api_create_job():
    data = request.get_json(silent=True) or {}
    job_id = (data.get("job_id") or "").strip() or uuid.uuid4().hex

    if not async_pipeline_available():
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503

    raw_files = data.get("files") or []
    if not isinstance(raw_files, list) or not raw_files:
        return {"error": "Attach at least one uploaded file."}, 400
    if len(raw_files) > app.config["MAX_UPLOAD_FILES"]:
        return {"error": f"Maximum {app.config['MAX_UPLOAD_FILES']} files per batch."}, 400

    cleaned_files = []
    for entry in raw_files:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("key") or "").strip()
        original_name = (entry.get("original_name") or entry.get("name") or "").strip()
        if not key or not original_name:
            return {"error": "Each file must include both a storage key and original name."}, 400
        if not allowed_file(original_name):
            return {"error": f"Unsupported file type: {original_name}"}, 400
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0 or size > app.config["MAX_UPLOAD_SIZE_BYTES"]:
            return {"error": f"Invalid size for {original_name}."}, 400
        cleaned_files.append(
            {
                "key": key,
                "original_name": original_name,
                "size": size,
                "content_type": (entry.get("content_type") or "video/mp4").strip(),
                "base_override": (entry.get("base_override") or "").strip() or None,
            }
        )

    if not cleaned_files:
        return {"error": "No valid files provided."}, 400

    style = (data.get("style") or "blur").strip()
    if style not in STYLE_LABELS:
        style = "blur"

    ratios = [r for r in (data.get("ratios") or []) if r in ASPECT_OPTIONS]
    if not ratios:
        return {"error": "Select at least one aspect ratio."}, 400
    if len(ratios) > 3:
        ratios = ratios[:3]

    naming_config = data.get("naming")
    if not isinstance(naming_config, dict):
        naming_config = build_naming_config(job_id, None)

    record = {
        "id": job_id,
        "status": JOB_STATUS_PENDING,
        "style": style,
        "ratios": ratios,
        "files": cleaned_files,
        "results": [],
        "progress": 0.0,
        "message": "",
        "created_at": datetime.utcnow().isoformat(),
        "metadata": {
            "reward_token": data.get("reward_token"),
            "user_token": data.get("user_token"),
        },
        "naming": naming_config,
    }
    try:
        persist_job_record(record)
    except AsyncPipelineUnavailable:
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503
    return {"job": public_job_view(record)}, 201


@app.post("/api/jobs/<job_id>/start")
def api_start_job(job_id: str):
    if not async_pipeline_available():
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503
    try:
        record = load_job_record(job_id)
    except AsyncPipelineUnavailable:
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503
    if not record:
        return {"error": "Unknown job."}, 404
    if record.get("status") not in {JOB_STATUS_PENDING, JOB_STATUS_FAILED}:
        return {"job": public_job_view(record)}, 200

    payload = request.get_json(silent=True) or {}
    reward_token = payload.get("reward_token") or record.get("metadata", {}).get("reward_token")
    if not validate_reward_token(reward_token):
        return {"error": "Rewarded ad verification failed."}, 403

    try:
        mark_job_status(
            job_id,
            JOB_STATUS_QUEUED,
            {"queued_at": datetime.utcnow().isoformat(), "progress": 0.0, "message": ""},
        )
    except AsyncPipelineUnavailable:
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503

    try:
        job_queue().enqueue(
            "app.process_render_job",
            job_id,
            result_ttl=app.config["JOB_RESULT_TTL"],
            failure_ttl=app.config["JOB_RESULT_TTL"],
        )
    except AsyncPipelineUnavailable:
        logging.warning("Failed to enqueue job %s: async pipeline unavailable", job_id)
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503
    except Exception:
        logging.exception("Failed to enqueue job %s", job_id)
        try:
            mark_job_status(job_id, JOB_STATUS_FAILED, {"message": "Queue unavailable."})
        except AsyncPipelineUnavailable:
            pass
        return {"error": "Unable to queue rendering job at the moment."}, 503

    try:
        queued_record = load_job_record(job_id)
    except AsyncPipelineUnavailable:
        queued_record = None
    return {"job": public_job_view(queued_record or record)}, 202


@app.get("/api/jobs/<job_id>/status")
def api_job_status(job_id: str):
    if not async_pipeline_available():
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503
    try:
        record = load_job_record(job_id)
    except AsyncPipelineUnavailable:
        return {
            "error": "Async rendering is unavailable. Use fallback rendering instead.",
            "code": ASYNC_UNAVAILABLE_CODE,
        }, 503
    if not record:
        return {"error": "Unknown job."}, 404
    return {"job": public_job_view(record)}, 200


def process_render_job(job_id: str) -> None:
    with app.app_context():
        try:
            record = load_job_record(job_id)
        except AsyncPipelineUnavailable:
            logging.warning("Async pipeline unavailable; cannot process job %s", job_id)
            return
        if not record:
            logging.warning("Skipping unknown job %s", job_id)
            return

        record["results"] = []
        try:
            persist_job_record(record)
        except AsyncPipelineUnavailable:
            logging.warning("Async pipeline unavailable; cannot update job %s before processing", job_id)
            return

        try:
            mark_job_status(
                job_id,
                JOB_STATUS_PROCESSING,
                {"started_at": datetime.utcnow().isoformat(), "progress": 0.0, "message": ""},
            )
        except AsyncPipelineUnavailable:
            logging.warning("Async pipeline unavailable; cannot mark job %s as processing", job_id)
            return
        try:
            naming_config = ensure_job_naming(record)
        except AsyncPipelineUnavailable:
            logging.warning("Async pipeline unavailable; cannot load naming config for job %s", job_id)
            return

        total_targets = len(record.get("files", [])) * max(1, len(record.get("ratios", [])))
        if total_targets <= 0:
            try:
                mark_job_status(job_id, JOB_STATUS_FAILED, {"message": "Job has no files or ratios."})
            except AsyncPipelineUnavailable:
                pass
            return

        processed = 0
        bucket = app.config.get("S3_BUCKET")
        s3 = s3_client() if is_s3_enabled() else None

        try:
            for file_entry in record.get("files", []):
                original_name = file_entry.get("original_name") or "clip.mp4"
                base_override = file_entry.get("base_override")
                storage_key = file_entry.get("key")
                if not storage_key:
                    raise ValueError(f"Missing storage key for {original_name}")

                with tempfile.TemporaryDirectory(prefix=f"{job_id}_") as tmp_dir:
                    tmp_dir_path = Path(tmp_dir)
                    input_path = tmp_dir_path / Path(storage_key).name

                    if is_s3_enabled():
                        s3.download_file(bucket, storage_key, str(input_path))  # type: ignore[arg-type]
                    else:
                        local_candidate = Path(storage_key)
                        if not local_candidate.is_absolute():
                            local_candidate = app.config["UPLOAD_FOLDER"] / local_candidate
                        if not local_candidate.exists():
                            raise FileNotFoundError(f"Source file not found: {storage_key}")
                        shutil.copy(local_candidate, input_path)

                    with VideoFileClip(str(input_path)) as tmp_clip:
                        duration = getattr(tmp_clip, "duration", None)
                        if duration and duration > 180:
                            raise ClipTooLongError("Max video length for MVP is 3 minutes (180 seconds).")

                    base_info = prepare_base_info(original_name, base_override, naming_config)
                    naming_state = {
                        "config": naming_config,
                        "base_info": base_info,
                        "sequence_start": processed,
                    }
                    tmp_output_dir = tmp_dir_path / "outputs"
                    results = render_variants(
                        input_path,
                        tmp_output_dir,
                        record["style"],
                        job_id,
                        record["ratios"],
                        naming_state,
                        include_urls=False,
                    )

                    file_outputs = []
                    for result in results:
                        processed += 1
                        output_file = tmp_output_dir / result["filename"]
                        if not output_file.exists():
                            continue

                        if is_s3_enabled():
                            output_key = f"{app.config['S3_OUTPUT_PREFIX'].rstrip('/')}/{job_id}/{result['filename']}"
                            s3.upload_file(str(output_file), bucket, output_key)  # type: ignore[arg-type]
                            download_url = s3.generate_presigned_url(
                                "get_object",
                                Params={"Bucket": bucket, "Key": output_key},
                                ExpiresIn=app.config["DOWNLOAD_URL_TTL"],
                            )
                            storage_ref = {"key": output_key, "url": download_url}
                        else:
                            dest_dir = app.config["OUTPUT_FOLDER"] / job_id
                            dest_dir.mkdir(parents=True, exist_ok=True)
                            target_path = dest_dir / result["filename"]
                            shutil.move(str(output_file), target_path)
                            download_url = url_for(
                                "download",
                                job_id=job_id,
                                filename=result["filename"],
                                _external=True,
                            )
                            storage_ref = {"path": str(target_path), "url": download_url}

                        file_outputs.append(
                            {
                                "label": result.get("label"),
                                "ratio_label": result.get("ratio_label"),
                                "filename": result["filename"],
                                **storage_ref,
                            }
                        )
                        try:
                            store_job_progress(job_id, processed / max(1, total_targets))
                        except AsyncPipelineUnavailable:
                            logging.warning("Async pipeline unavailable; unable to store progress for job %s", job_id)

                    record["results"].append(
                        {
                            "original_name": original_name,
                            "outputs": file_outputs,
                        }
                    )
                    try:
                        persist_job_record(record)
                    except AsyncPipelineUnavailable:
                        logging.warning("Async pipeline unavailable; unable to persist progress for job %s", job_id)

            try:
                mark_job_status(
                    job_id,
                    JOB_STATUS_COMPLETED,
                    {
                        "completed_at": datetime.utcnow().isoformat(),
                        "progress": 1.0,
                        "results": record["results"],
                        "message": "Rendering finished.",
                    },
                )
            except AsyncPipelineUnavailable:
                logging.warning("Async pipeline unavailable; unable to mark job %s complete", job_id)
        except ClipTooLongError as exc:
            logging.warning("Job %s failed validation: %s", job_id, exc)
            try:
                mark_job_status(
                    job_id,
                    JOB_STATUS_FAILED,
                    {"message": str(exc), "progress": 1.0, "failed_at": datetime.utcnow().isoformat()},
                )
            except AsyncPipelineUnavailable:
                pass
        except Exception as exc:
            logging.exception("Job %s failed", job_id)
            try:
                mark_job_status(
                    job_id,
                    JOB_STATUS_FAILED,
                    {
                        "message": str(exc),
                        "progress": processed / max(1, total_targets),
                        "failed_at": datetime.utcnow().isoformat(),
                    },
                )
            except AsyncPipelineUnavailable:
                pass
            raise


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
    include_url: bool = True,
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
    if include_url:
        result["url"] = url_for("download", job_id=job_id, filename=filename)
    return result


def render_variants(
    input_path: Path,
    output_dir: Path,
    style: str,
    job_id: str,
    ratios: list[str],
    naming_state: dict,
    include_urls: bool = True,
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
                    include_url=include_urls,
                )
            )

    update_job_progress(job_id, 1.0, "done")
    return outputs
