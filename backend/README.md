# VidX Backend API

This backend exposes a generic HTTP API so any client (web, desktop, Android, iOS) can submit a job and download results.

## Storage backends (pluggable)

### Local storage (default)
Stores all files under a local directory on disk.

Environment variables:
- `STORAGE_BACKEND=local` (default)
- `VIDX_DATA_DIR=/absolute/path` (default: `backend_data` in repo root)

Layout per job:
```
VIDX_DATA_DIR/
  <job_id>/
    input/
      base_video
      recipients
    output/
      videos/
      videos.zip
```

### S3 storage (placeholder)
S3 storage is **not implemented yet**. The backend will raise an error if you set `STORAGE_BACKEND=s3` without implementing `S3Storage`.

Environment variables (planned):
- `STORAGE_BACKEND=s3`
- `S3_BUCKET=<bucket-name>`
- (optional) `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

To implement S3, update `/Users/himanshu/Desktop/VidX/backend/storage.py`:
- `S3Storage.save_upload`
- `S3Storage.open`
- `S3Storage.exists`
- `S3Storage.mkdir`

## Run

```bash
python3 -m venv /Users/himanshu/Desktop/VidX/backend/.venv
source /Users/himanshu/Desktop/VidX/backend/.venv/bin/activate
pip install -r /Users/himanshu/Desktop/VidX/backend/requirements.txt

# also install video deps used by the core tool
pip install -r /Users/himanshu/Desktop/VidX/requirements.txt

# run API
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

### CORS for web clients
If your web app runs on a different origin (e.g., `http://localhost:3000`), set:

```
export VIDX_ALLOWED_ORIGINS=http://localhost:3000
```

## API documentation

Base URL: `http://<host>:8000`

### 1. Create job
`POST /jobs` (multipart form)

Form fields:
- `base_video` (file, required)  
- `recipients` (file, required)  
- `voice_sample` (file, optional) Voice sample audio for external TTS providers. Required when `tts_provider=elevenlabs`.  
- `insert_mode` (string, optional, `silver|gold|diamond`, default `silver`)  
  - `silver`: inserts name at start by padding video with a frozen first frame (keeps sync)
  - `gold`: replaces the first spoken "generic name" audio segment (video continues; slight lip-sync mismatch tolerated)
- `name_position` (string, optional, `start|end`, default `start`)  
- `text` (string, optional, default `{name}`)  
- `lang` (string, optional, default `hi`)  
- `tts_provider` (string, optional, `gtts|elevenlabs|command|none`, default `gtts`)  
- `tts_cmd` (string, optional; used only with `command`)  
- `elevenlabs_api_key` (string, optional; used only with `elevenlabs`)  
- `elevenlabs_voice_id` (string, optional; used only with `elevenlabs`) Typically not needed: VidX can clone from `voice_sample`.  
- `elevenlabs_model_id` (string, optional; used only with `elevenlabs`)  
- `silence_db` (float, optional, default `-30.0`)  
- `silence_dur` (float, optional, default `0.3`)  
- `convert_mov` (bool, optional, default `false`) Converts input .MOV to MP4 before processing  

Example (curl):
```bash
curl -X POST http://localhost:8000/jobs \
  -F "base_video=@/path/to/base.mp4" \
  -F "recipients=@/path/to/recipients.xlsx" \
  -F "voice_sample=@/path/to/voice.wav" \
  -F "insert_mode=silver" \
  -F "name_position=start" \
  -F "text={name}" \
  -F "lang=hi" \
  -F "convert_mov=true"
```

Response:
```json
{ "job_id": "abc123", "status": "queued" }
```

### 2. Job status
`GET /jobs/{job_id}`

Example:
```bash
curl http://localhost:8000/jobs/abc123
```

Response (running):
```json
{
  "job_id": "abc123",
  "status": "running",
  "created_at": 1739020000.123,
  "updated_at": 1739020010.456,
  "error": null
}
```

Response (done):
```json
{
  "job_id": "abc123",
  "status": "done",
  "created_at": 1739020000.123,
  "updated_at": 1739020030.456,
  "error": null,
  "download_url": "/jobs/abc123/download"
}
```

### 3. Download output
`GET /jobs/{job_id}/download`

Returns a ZIP containing all personalized videos.

## Caching behavior
- Voice cloning: cached by voice sample hash in `backend_data/elevenlabs_voice_cache.json`
- Name audio clips: cached globally in `backend_data/name_audio_cache/`

Example:
```bash
curl -O http://localhost:8000/jobs/abc123/download
```

### 4. Convert MOV to MP4
`POST /convert` (multipart form)

Form fields:
- `base_video` (file, required)  
- `crf` (int, optional, default `20`)  
- `preset` (string, optional, default `medium`)  
- `audio_bitrate` (string, optional, default `160k`)  

Example (curl):
```bash
curl -X POST http://localhost:8000/convert \
  -F "base_video=@/path/to/base.MOV" \
  -F "crf=20" \
  -F "preset=medium" \
  -F "audio_bitrate=160k" \
  -o converted.mp4
```

## Notes
- The backend calls `personalized_video.py` as a subprocess. Keep it in the repo root.
- For production, add auth and a real queue (e.g., Celery or RQ).
- The API uses local disk by default but is structured so S3 can be added later without changing endpoints.

## Running the tool directly (CLI)
Yes, you can run the original Python command directly without the API.

Basic usage:
```bash
python3 /Users/himanshu/Desktop/VidX/personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --outdir /Users/himanshu/Desktop/VidX/output
```

Add name at the start (default):
```bash
python3 /Users/himanshu/Desktop/VidX/personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --name-position start
```

Add name at the end (after speaker stops):
```bash
python3 /Users/himanshu/Desktop/VidX/personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --name-position end \
  --silence-db -30 \
  --silence-dur 0.3
```

Custom text and language:
```bash
python3 /Users/himanshu/Desktop/VidX/personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --text "{name}" \
  --lang hi
```

Dry run (no TTS, no ffmpeg):
```bash
python3 /Users/himanshu/Desktop/VidX/personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --outdir /Users/himanshu/Desktop/VidX/output \
  --dry-run
```
