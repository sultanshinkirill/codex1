# Vibe Resizer
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
