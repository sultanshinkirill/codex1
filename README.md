
# Free AutoFrame
A tiny Flask app that batch-converts vertical 9:16 clips into square (1:1) and widescreen (16:9).
Choose between a blurred background letterbox or a fill-and-crop look.

## Deployment

**For production deployment with auto-deployment to Vercel (free) and Hostinger VPS (paid), see [DEPLOYMENT.md](DEPLOYMENT.md).**

The app supports dual deployment:
- **Vercel (Free)**: Frontend + client-side rendering for videos ≤75s
- **Hostinger VPS (Paid)**: Full server-side processing for large videos up to 200MB × 10 files
- **Auto-deployment**: Push to GitHub → automatically deploys to both platforms

## Quick start (Local Development)
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

## Render flow
The UI now begins with an in-browser FFmpeg pass and automatically falls back to the Flask renderer whenever the browser would struggle.

1. **Client-side FFmpeg (≤ ~75s clips)** – files are processed with `ffmpeg.wasm` directly in the browser so users see preview links immediately and nothing leaves their machine.
2. **Automatic server fallback** – longer clips or WASM failures trigger an `XMLHttpRequest` to the `/api/process` endpoint. Progress is tracked via `/progress/<job_id>` and the UI exposes the downloaded outputs plus a zipped bundle link.

### Browser renderer
- Up to 10 compatible videos at a time (`.mp4`, `.mov`, `.m4v`, `.mkv`).
- Keep the tab visible; Chrome may throttle background tabs during a render.
- Temporary files are cleaned up after each ratio to keep the WASM FS from ballooning.

### Server fallback
- Kicks in automatically when a clip exceeds ~75 seconds or if WASM errors are encountered.
- Each file uploads sequentially; the progress widget polls `/progress/<job_id>` until MoviePy finishes.
- Results list mirrors the server response and exposes `/download/<job_id>/bundle` for batch grabs.

## Configuration
| Variable | Purpose | Default |
| --- | --- | --- |
| `MAX_FILES_PER_BATCH` | Maximum files accepted per request | `10` |
| `MAX_UPLOAD_SIZE_BYTES` | Maximum bytes per uploaded file | `125829120` (120 MB) |
| `UPLOAD_DIR` | Where raw uploads are cached on disk | `uploads/` |
| `OUTPUT_DIR` | Where rendered clips and summaries are stored | `outputs/` |

Adjust the client-side duration threshold by editing `CLIENT_DURATION_LIMIT_SECONDS` in `static/js/app.js` (default `75` seconds).

## Tips
- Rendering requires `ffmpeg` binaries bundled through MoviePy; no external system `ffmpeg` installation is necessary.
- If the UI appears stuck, refresh the page — in-progress renders continue on the server and can be resumed by visiting the result link again.
