from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .jobs import JobStore
from .storage import get_storage_backend

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

# Load env vars from backend/.env if present
load_dotenv(dotenv_path=BASE_DIR / ".env")
DATA_DIR = Path(os.environ.get("VIDX_DATA_DIR", str(REPO_ROOT / "backend_data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobs.sqlite3"
GLOBAL_NAME_AUDIO_DIR = DATA_DIR / "name_audio_cache"

storage = get_storage_backend()
job_store = JobStore(DB_PATH)

app = FastAPI(title="VidX API", version="0.1.0")

allowed_origins = os.environ.get("VIDX_ALLOWED_ORIGINS", "http://localhost:3000")
origins = [origin.strip() for origin in allowed_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VOICE_CACHE_PATH = DATA_DIR / "elevenlabs_voice_cache.json"


def _load_voice_cache() -> dict:
    if not VOICE_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(VOICE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_voice_cache(cache: dict) -> None:
    VOICE_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _new_job_id() -> str:
    # Local-time, human-readable format with am/pm.
    now = datetime.now()
    return now.strftime("%Y-%m-%d_%I-%M-%S-%p").lower()


def elevenlabs_clone_voice(*, api_key: str | None, voice_name: str, voice_sample_path: Path) -> str:
    api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ElevenLabs API key missing. Set ELEVENLABS_API_KEY or pass elevenlabs_api_key.")
    if not voice_sample_path.exists():
        raise RuntimeError(f"Voice sample not found: {voice_sample_path}")

    url = "https://api.elevenlabs.io/v1/voices/add"
    headers = {"xi-api-key": api_key, "accept": "application/json"}
    with open(voice_sample_path, "rb") as f:
        files = [("files", (voice_sample_path.name, f, "application/octet-stream"))]
        data = {"name": voice_name}
        resp = requests.post(url, headers=headers, data=data, files=files, timeout=90)
    if resp.status_code >= 300:
        raise RuntimeError(f"ElevenLabs voice clone failed ({resp.status_code}): {resp.text[:600]}")
    payload = resp.json()
    voice_id = payload.get("voice_id") or payload.get("voice", {}).get("voice_id")
    if not voice_id:
        raise RuntimeError(f"ElevenLabs voice clone response missing voice_id: {payload}")
    return str(voice_id)


def run_pipeline(job_id: str) -> None:
    job = job_store.get(job_id)
    if not job:
        return

    input_dir = Path(job.input_dir)
    output_dir = Path(job.output_dir)
    options = json.loads(job.options_json)

    job_store.update_status(job_id, "running")
    try:
        requested_mode = options.get("insert_mode", "silver")
        insert_mode = "gold" if requested_mode in ("diamond", "platinum") else requested_mode
        if insert_mode not in ("silver", "gold"):
            raise RuntimeError(f"insert_mode={insert_mode} is not implemented yet.")

        base_video = Path(options.get("base_path", input_dir / "base_video"))
        recipients = Path(options.get("recipients_path", input_dir / "recipients"))
        voice_sample = options.get("voice_sample_path")
        out_dir = output_dir / "videos"
        out_dir.mkdir(parents=True, exist_ok=True)

        if options.get("convert_mov", False):
            if base_video.suffix.lower() == ".mov":
                converted = input_dir / "base_video_converted.mp4"
                convert_cmd = [
                    "python3",
                    str(REPO_ROOT / "backend" / "convert_video.py"),
                    "--input",
                    str(base_video),
                    "--output",
                    str(converted),
                ]
                subprocess.run(convert_cmd, check=True)
                base_video = converted

        name_audio_dir = output_dir / "name_audio"
        names_master_out = output_dir / "names_master.wav"

        tts_provider = options.get("tts_provider", "gtts")
        eleven_api_key = options.get("elevenlabs_api_key", "") or None
        eleven_voice_id = options.get("elevenlabs_voice_id", "") or None
        eleven_model_id = options.get("elevenlabs_model_id", "") or None
        eleven_speed = float(options.get("elevenlabs_speed", 1.0))
        lip_sync_provider = options.get("lip_sync_provider", "none")
        wav2lip_repo = options.get("wav2lip_repo", "")
        wav2lip_checkpoint = options.get("wav2lip_checkpoint", "")
        wav2lip_pads = options.get("wav2lip_pads", "0 10 0 0")
        wav2lip_python = options.get("wav2lip_python", "python3")
        batch_name_tts = bool(options.get("batch_name_tts", True))
        batch_split_silence_db = float(options.get("batch_split_silence_db", -40.0))
        batch_split_silence_dur = float(options.get("batch_split_silence_dur", 0.18))
        batch_gap_hint = str(options.get("batch_gap_hint", "ठहराव"))
        diamond_natural_name = bool(options.get("diamond_natural_name", False))
        diamond_gap_seconds = float(options.get("diamond_gap_seconds", 0.12))
        platinum_placeholders = str(options.get("platinum_placeholders", "NAME1,NAME2"))
        if requested_mode not in ("diamond", "platinum"):
            lip_sync_provider = "none"

        if tts_provider == "elevenlabs":
            if not voice_sample:
                raise RuntimeError("ElevenLabs selected but no voice_sample was provided.")
            voice_sample_path = Path(voice_sample)
            sample_hash = _file_hash(voice_sample_path)
            cache = _load_voice_cache()
            cached = cache.get(sample_hash)
            if cached and cached.get("voice_id"):
                eleven_voice_id = cached["voice_id"]
                if not eleven_model_id:
                    eleven_model_id = cached.get("model_id") or None
            else:
                cloned_voice_id = elevenlabs_clone_voice(
                    api_key=eleven_api_key,
                    voice_name=f"vidx-{job_id}",
                    voice_sample_path=voice_sample_path,
                )
                eleven_voice_id = cloned_voice_id
                cache[sample_hash] = {
                    "voice_id": cloned_voice_id,
                    "model_id": eleven_model_id or "eleven_multilingual_v2",
                    "voice_sample_path": str(voice_sample_path),
                }
                _save_voice_cache(cache)
            (output_dir / "elevenlabs_voice_id.txt").write_text(str(eleven_voice_id), encoding="utf-8")

        cmd = [
            "python3",
            str(REPO_ROOT / "personalized_video.py"),
            "--video",
            str(base_video),
            "--recipients",
            str(recipients),
            "--outdir",
            str(out_dir),
            "--insert-mode",
            insert_mode,
            "--lip-sync-provider",
            lip_sync_provider,
            "--wav2lip-repo",
            wav2lip_repo,
            "--wav2lip-checkpoint",
            wav2lip_checkpoint,
            "--wav2lip-pads",
            wav2lip_pads,
            "--wav2lip-python",
            wav2lip_python,
            "--name-position",
            options.get("name_position", "start"),
            "--text",
            options.get("text", "{name}"),
            "--lang",
            options.get("lang", "hi"),
            "--tts-provider",
            tts_provider,
            "--tts-cmd",
            options.get("tts_cmd", ""),
            "--elevenlabs-api-key",
            eleven_api_key or "",
            "--elevenlabs-voice-id",
            eleven_voice_id or "",
            "--elevenlabs-model-id",
            eleven_model_id or "",
            "--elevenlabs-speed",
            f"{eleven_speed:.3f}",
            "--silence-db",
            str(options.get("silence_db", -30.0)),
            "--silence-dur",
            str(options.get("silence_dur", 0.3)),
            "--build-name-cache",
            "--name-cache-dir",
            str(GLOBAL_NAME_AUDIO_DIR),
            "--names-master-out",
            str(names_master_out),
            "--batch-split-silence-db",
            str(batch_split_silence_db),
            "--batch-split-silence-dur",
            str(batch_split_silence_dur),
            "--batch-gap-hint",
            batch_gap_hint,
            "--diamond-gap-seconds",
            str(diamond_gap_seconds),
            "--platinum-placeholders",
            platinum_placeholders,
        ]
        cmd.append("--batch-name-tts" if batch_name_tts else "--no-batch-name-tts")
        cmd.append("--diamond-natural-name" if diamond_natural_name else "--no-diamond-natural-name")
        cmd.append("--platinum-mode" if requested_mode == "platinum" else "--no-platinum-mode")
        if voice_sample:
            cmd += ["--voice-sample", str(voice_sample)]

        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip()[-4000:]
            stdout_tail = (proc.stdout or "").strip()[-1500:]
            details = stderr_tail or stdout_tail or "No subprocess output."
            raise RuntimeError(f"personalized_video.py failed:\n{details}")

        zip_path = output_dir / "videos.zip"
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(zip_path.with_suffix("")), "zip", out_dir)

        job_store.update_status(job_id, "done", zip_path=zip_path)
    except Exception as exc:
        job_store.update_status(job_id, "failed", error=str(exc))


@app.post("/jobs")
def create_job(
    background_tasks: BackgroundTasks,
    base_video: UploadFile = File(...),
    recipients: UploadFile = File(...),
    voice_sample: UploadFile | None = File(None),
    insert_mode: str = Form("silver"),
    name_position: str = Form("start"),
    text: str = Form("{name}"),
    lang: str = Form("hi"),
    tts_provider: str = Form("gtts"),
    tts_cmd: str = Form(""),
    elevenlabs_api_key: str = Form(""),
    elevenlabs_voice_id: str = Form(""),
    elevenlabs_model_id: str = Form(""),
    elevenlabs_speed: float = Form(1.0),
    lip_sync_provider: str = Form("none"),
    wav2lip_repo: str = Form(""),
    wav2lip_checkpoint: str = Form(""),
    wav2lip_pads: str = Form("0 10 0 0"),
    wav2lip_python: str = Form("python3"),
    batch_name_tts: bool = Form(True),
    batch_split_silence_db: float = Form(-40.0),
    batch_split_silence_dur: float = Form(0.18),
    batch_gap_hint: str = Form("ठहराव"),
    diamond_natural_name: bool = Form(True),
    diamond_gap_seconds: float = Form(0.12),
    platinum_placeholders: str = Form("NAME1,NAME2"),
    silence_db: float = Form(-30.0),
    silence_dur: float = Form(0.3),
    convert_mov: bool = Form(False),
):
    job_id = _new_job_id()
    job_dir = DATA_DIR / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"

    storage.mkdir(input_dir)
    storage.mkdir(output_dir)

    base_suffix = Path(base_video.filename or "").suffix or ".mp4"
    rec_suffix = Path(recipients.filename or "").suffix or ".xlsx"
    base_path = input_dir / f"base_video{base_suffix}"
    rec_path = input_dir / f"recipients{rec_suffix}"

    storage.save_upload(base_video.file, base_path)
    storage.save_upload(recipients.file, rec_path)
    voice_path = None
    if voice_sample is not None:
        voice_suffix = Path(voice_sample.filename or "").suffix or ".wav"
        voice_path = input_dir / f"voice_sample{voice_suffix}"
        storage.save_upload(voice_sample.file, voice_path)
    if tts_provider == "elevenlabs" and voice_path is None:
        raise HTTPException(status_code=400, detail="voice_sample is required when tts_provider=elevenlabs")
    if insert_mode in ("diamond", "platinum") and lip_sync_provider == "wav2lip":
        repo_raw = (wav2lip_repo or os.environ.get("WAV2LIP_REPO", "")).strip()
        ckpt_raw = (wav2lip_checkpoint or os.environ.get("WAV2LIP_CHECKPOINT", "")).strip()
        if not repo_raw:
            raise HTTPException(status_code=400, detail="wav2lip_repo is required when Diamond + Wav2Lip is selected")
        if not ckpt_raw:
            raise HTTPException(status_code=400, detail="wav2lip_checkpoint is required when Diamond + Wav2Lip is selected")
        repo_path = Path(repo_raw).expanduser()
        ckpt_path = Path(ckpt_raw).expanduser()
        if not repo_path.exists():
            raise HTTPException(status_code=400, detail=f"wav2lip_repo not found: {repo_path}")
        if not (repo_path / "inference.py").exists():
            raise HTTPException(status_code=400, detail=f"Wav2Lip inference.py not found in repo: {repo_path}")
        if not ckpt_path.exists():
            raise HTTPException(status_code=400, detail=f"wav2lip_checkpoint not found: {ckpt_path}")
    if insert_mode == "platinum" and not platinum_placeholders.strip():
        raise HTTPException(status_code=400, detail="platinum_placeholders is required for platinum tier")

    options = {
        "insert_mode": insert_mode,
        "name_position": name_position,
        "text": text,
        "lang": lang,
        "tts_provider": tts_provider,
        "tts_cmd": tts_cmd,
        "elevenlabs_api_key": elevenlabs_api_key,
        "elevenlabs_voice_id": elevenlabs_voice_id,
        "elevenlabs_model_id": elevenlabs_model_id,
        "elevenlabs_speed": elevenlabs_speed,
        "lip_sync_provider": lip_sync_provider,
        "wav2lip_repo": wav2lip_repo,
        "wav2lip_checkpoint": wav2lip_checkpoint,
        "wav2lip_pads": wav2lip_pads,
        "wav2lip_python": wav2lip_python,
        "batch_name_tts": batch_name_tts,
        "batch_split_silence_db": batch_split_silence_db,
        "batch_split_silence_dur": batch_split_silence_dur,
        "batch_gap_hint": batch_gap_hint,
        "diamond_natural_name": diamond_natural_name,
        "diamond_gap_seconds": diamond_gap_seconds,
        "platinum_placeholders": platinum_placeholders,
        "silence_db": silence_db,
        "silence_dur": silence_dur,
        "convert_mov": convert_mov,
    }
    options["base_path"] = str(base_path)
    options["recipients_path"] = str(rec_path)
    if voice_path is not None:
        options["voice_sample_path"] = str(voice_path)
    job_store.create(job_id, input_dir, output_dir, options)

    background_tasks.add_task(run_pipeline, job_id)

    return {"job_id": job_id, "status": "queued"}


@app.post("/cache/name-audio/clear")
def clear_name_audio_cache():
    removed_files = 0
    removed_dirs = 0
    if GLOBAL_NAME_AUDIO_DIR.exists():
        for p in GLOBAL_NAME_AUDIO_DIR.rglob("*"):
            if p.is_file():
                removed_files += 1
            elif p.is_dir():
                removed_dirs += 1
        shutil.rmtree(GLOBAL_NAME_AUDIO_DIR)
    GLOBAL_NAME_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "status": "ok",
        "cleared_dir": str(GLOBAL_NAME_AUDIO_DIR),
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
    }


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    response = {
        "job_id": job.id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "error": job.error,
    }
    if job.status == "done" and job.zip_path:
        response["download_url"] = f"/jobs/{job.id}/download"
    return response


@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done" or not job.zip_path:
        raise HTTPException(status_code=400, detail="job not ready")
    zip_path = Path(job.zip_path)
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="output not found")
    return FileResponse(zip_path, filename=f"{job_id}.zip")


@app.post("/convert")
def convert_video(
    base_video: UploadFile = File(...),
    crf: int = Form(20),
    preset: str = Form("medium"),
    audio_bitrate: str = Form("160k"),
):
    job_id = uuid.uuid4().hex
    job_dir = DATA_DIR / f"convert_{job_id}"
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"

    storage.mkdir(input_dir)
    storage.mkdir(output_dir)

    input_suffix = Path(base_video.filename or "").suffix or ".mov"
    input_path = input_dir / f"input_video{input_suffix}"
    output_path = output_dir / "converted.mp4"
    storage.save_upload(base_video.file, input_path)

    try:
        convert_cmd = [
            "python3",
            str(REPO_ROOT / "backend" / "convert_video.py"),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--crf",
            str(crf),
            "--preset",
            preset,
            "--audio-bitrate",
            audio_bitrate,
        ]
        subprocess.run(convert_cmd, check=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not output_path.exists():
        raise HTTPException(status_code=500, detail="conversion failed")
    return FileResponse(output_path, filename="converted.mp4")
