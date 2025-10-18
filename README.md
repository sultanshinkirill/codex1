# Free AutoFrame
A tiny Flask app that batch-converts vertical 9:16 clips into square (1:1) and widescreen (16:9).
Choose between a blurred background letterbox or a fill-and-crop look.

## Quick start
1) Drop these files into your cloned `codex1` folder (replace if asked).
2) In Terminal:
```
cd ~/Desktop/codex1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --debug
```
Open http://127.0.0.1:5000

## Async rendering pipeline (MVP architecture)
The app now exposes an asynchronous workflow designed for ad-gated, fire-and-forget batches:

1. **Get an upload slot** – `POST /api/upload-url` returns a presigned POST for your object-storage bucket. Upload each video directly from the browser, bypassing the Flask process.
2. **Create a job** – `POST /api/jobs` with the uploaded file keys, style, and target aspect ratios. Jobs are persisted in Redis with status `pending`.
3. **Rewarded-ad gate** – once your ad callback succeeds, hit `POST /api/jobs/<id>/start`. The endpoint validates the supplied `reward_token` placeholder and enqueues work in Redis Queue.
4. **Background worker** – an `rq` worker downloads the sources (S3/B2), renders the requested ratios with ffmpeg/moviepy, uploads the derivatives, and pushes presigned download links back into the job record.
5. **Status & delivery** – poll `GET /api/jobs/<id>/status` or hook up SSE/WebSockets to reflect progress. Each completed output carries a signed download URL so the frontend can surface the bundle without touching your servers.

### Runtime services
- **Redis** for the job queue & job metadata (`REDIS_URL`, default `redis://localhost:6379/0`)
- **Object storage** for uploads/results (`S3_BUCKET`, `AWS_REGION`, optional `S3_UPLOAD_PREFIX`, `S3_OUTPUT_PREFIX`)
- **rq worker** process to execute `process_render_job`

Launch the worker from the project root:
```
source .venv/bin/activate
rq worker --with-scheduler --path . app
```

### Key environment variables
| Variable | Purpose | Default |
| --- | --- | --- |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `JOB_QUEUE_NAME` | rq queue name | `autoframe-jobs` |
| `JOB_TIMEOUT_SECONDS` | Per-job execution timeout | `3600` |
| `JOB_RESULT_TTL_SECONDS` | How long job metadata is retained | `604800` (7 days) |
| `S3_BUCKET` / `AWS_S3_BUCKET` | Target S3-compatible bucket | _required for presigned uploads_ |
| `AWS_REGION` | Bucket region | _(optional)_ |
| `S3_UPLOAD_PREFIX` | Prefix for source uploads | `uploads` |
| `S3_OUTPUT_PREFIX` | Prefix for rendered outputs | `outputs` |
| `MAX_UPLOAD_FILES` | Max files per batch | `10` |
| `MAX_UPLOAD_SIZE_BYTES` | Max bytes per file | `125829120` (120 MB) |
| `UPLOAD_URL_TTL` | Presigned upload lifetime (seconds) | `900` |
| `DOWNLOAD_URL_TTL` | Presigned download lifetime (seconds) | `86400` |

### Ad-gating hook
`POST /api/jobs/<id>/start` accepts a `reward_token` field. The current implementation calls `validate_reward_token()` as a stub so you can plug in Google Publisher Tag callbacks or a verification API before queueing work. Extend the helper to record impressions, reject invalid tokens, or branch to a premium queue.

### Local fallback
If you skip the S3 configuration the worker copies from the on-disk `uploads/` folder and serves finished clips via the existing `/download/<job_id>/...` endpoints. This keeps development simple while matching the production flow.
