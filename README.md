# Personalized Video Tool (Python)

This script creates one personalized video per person by replacing the ending audio with a TTS message.

## What it does
- Reads recipients from CSV or XLSX
- Generates Hindi TTS audio (gTTS by default)
- Replaces the ending audio of a base video with the personalized TTS
- Writes one MP4 per person

## Requirements
- Python 3.9+
- `ffmpeg` + `ffprobe` installed and on PATH
- Python packages in `requirements.txt`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install ffmpeg (macOS):

```bash
brew install ffmpeg
```

## Sample recipients

See `sample/recipients.csv`.

Columns expected by default:
- `name`
- `phone`

## Usage

```bash
python3 personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --outdir output
```

Dry run (no TTS, no ffmpeg work):

```bash
python3 personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --outdir output \
  --dry-run
```

Custom Hindi text (must contain `{name}`):

```bash
python3 personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --text "{name}" \
  --lang hi
```

Speak the name at the start of the video (default):

```bash
python3 personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --name-position start
```

Custom TTS provider (external command):

```bash
python3 personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --tts-provider command \
  --tts-cmd "python3 /path/to/your_tts.py --text \"{text}\" --out \"{out}\""
```

## Notes
- gTTS is free for testing but requires internet and has usage limits.
- If you want higher quality or scaling later, we can plug in a paid TTS API by using the `--tts-provider command` hook.
- You can later wrap this script into a web app without changing the core pipeline.
- The script detects the end of speech using `silencedetect` and inserts the name right after the speaker stops.
   You can tune this with `--silence-db` and `--silence-dur`.
- When the name is inserted at the start, the video is padded with a frozen first frame to keep audio/video in sync.
- Name loudness is auto-matched to the base audio (can be tuned with `--name-loudness-max-gain-db`).
- Name cache build can synthesize all names in one TTS request and split by silence (`--batch-name-tts`).

## Backend API (pluggable storage)
If you want a generic API backend for web/desktop/mobile clients, see:

`/Users/himanshu/Desktop/VidX/backend/README.md`

The backend also exposes a standalone conversion endpoint:
- `POST /convert` to convert `.MOV` to Android-friendly `.mp4`.

## CLI usage (same tool, no API)

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

Dry run (no TTS, no ffmpeg):
```bash
python3 /Users/himanshu/Desktop/VidX/personalized_video.py \
  --video /path/to/base.mp4 \
  --recipients /path/to/recipients.xlsx \
  --outdir /Users/himanshu/Desktop/VidX/output \
  --dry-run
```

## Run Web App + Backend (dev)

This starts the backend on `8010` and the web app on `3003`.

```bash
/Users/himanshu/Desktop/VidX/run_dev.sh
```
