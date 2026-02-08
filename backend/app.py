from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .jobs import JobStore
from .storage import get_storage_backend

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DATA_DIR = Path(os.environ.get("VIDX_DATA_DIR", str(REPO_ROOT / "backend_data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobs.sqlite3"

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


def run_pipeline(job_id: str) -> None:
    job = job_store.get(job_id)
    if not job:
        return

    input_dir = Path(job.input_dir)
    output_dir = Path(job.output_dir)
    options = json.loads(job.options_json)

    job_store.update_status(job_id, "running")
    try:
        base_video = Path(options.get("base_path", input_dir / "base_video"))
        recipients = Path(options.get("recipients_path", input_dir / "recipients"))
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

        cmd = [
            "python3",
            str(REPO_ROOT / "personalized_video.py"),
            "--video",
            str(base_video),
            "--recipients",
            str(recipients),
            "--outdir",
            str(out_dir),
            "--name-position",
            options.get("name_position", "start"),
            "--text",
            options.get("text", "{name}"),
            "--lang",
            options.get("lang", "hi"),
            "--tts-provider",
            options.get("tts_provider", "gtts"),
            "--tts-cmd",
            options.get("tts_cmd", ""),
            "--silence-db",
            str(options.get("silence_db", -30.0)),
            "--silence-dur",
            str(options.get("silence_dur", 0.3)),
        ]

        subprocess.run(cmd, check=True)

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
    name_position: str = Form("start"),
    text: str = Form("{name}"),
    lang: str = Form("hi"),
    tts_provider: str = Form("gtts"),
    tts_cmd: str = Form(""),
    silence_db: float = Form(-30.0),
    silence_dur: float = Form(0.3),
    convert_mov: bool = Form(False),
):
    job_id = uuid.uuid4().hex
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

    options = {
        "name_position": name_position,
        "text": text,
        "lang": lang,
        "tts_provider": tts_provider,
        "tts_cmd": tts_cmd,
        "silence_db": silence_db,
        "silence_dur": silence_dur,
        "convert_mov": convert_mov,
    }
    options["base_path"] = str(base_path)
    options["recipients_path"] = str(rec_path)
    job_store.create(job_id, input_dir, output_dir, options)

    background_tasks.add_task(run_pipeline, job_id)

    return {"job_id": job_id, "status": "queued"}


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
