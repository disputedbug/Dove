#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import requests

try:
    from gtts import gTTS
except Exception:
    gTTS = None


def die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")


def apply_lip_sync(
    *,
    video_path: Path,
    provider: str,
    wav2lip_repo: str,
    wav2lip_checkpoint: str,
    wav2lip_pads: str,
    wav2lip_python: str,
) -> Path:
    if provider == "none":
        return video_path
    if provider == "sync_api":
        raise RuntimeError("Lip sync provider 'sync_api' is not implemented yet.")
    if provider != "wav2lip":
        raise RuntimeError(f"Unsupported lip sync provider: {provider}")

    repo_dir = Path(wav2lip_repo or os.environ.get("WAV2LIP_REPO", "")).expanduser()
    checkpoint = Path(wav2lip_checkpoint or os.environ.get("WAV2LIP_CHECKPOINT", "")).expanduser()
    if not repo_dir.exists():
        raise RuntimeError("Wav2Lip repo not found. Set --wav2lip-repo or WAV2LIP_REPO.")
    if not checkpoint.exists():
        raise RuntimeError("Wav2Lip checkpoint not found. Set --wav2lip-checkpoint or WAV2LIP_CHECKPOINT.")

    pads = [p for p in wav2lip_pads.split() if p.strip()]
    if len(pads) != 4:
        raise RuntimeError("Wav2Lip pads must contain 4 integers, e.g. '0 10 0 0'.")

    synced_tmp = video_path.with_name(video_path.stem + "_lipsynced.mp4")
    cmd = [
        wav2lip_python,
        str(repo_dir / "inference.py"),
        "--checkpoint_path",
        str(checkpoint),
        "--face",
        str(video_path),
        "--audio",
        str(video_path),
        "--outfile",
        str(synced_tmp),
        "--pads",
        *pads,
    ]
    run(cmd)
    shutil.move(str(synced_tmp), str(video_path))
    return video_path


def build_atempo_filter(speed: float) -> str:
    # ffmpeg atempo supports [0.5, 2.0] per stage, so chain if needed.
    if speed <= 0:
        return "atempo=1.0"
    stages: list[str] = []
    remaining = speed
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining /= 0.5
    stages.append(f"atempo={remaining:.6f}")
    return ",".join(stages)


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not parse duration for {path}") from exc


def mean_volume_db(path: Path, start: float | None = None, duration: float | None = None) -> float | None:
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    if start is not None and start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None and duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-af", "volumedetect", "-f", "null", "-"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return None
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def match_audio_loudness(
    *,
    source_wav: Path,
    reference_wav: Path,
    out_wav: Path,
    max_gain_db: float = 8.0,
) -> Path:
    src_mean = mean_volume_db(source_wav)
    ref_mean = mean_volume_db(reference_wav)
    if src_mean is None or ref_mean is None:
        shutil.copyfile(source_wav, out_wav)
        return out_wav

    gain_db = max(-max_gain_db, min(max_gain_db, ref_mean - src_mean))
    if abs(gain_db) < 0.3:
        shutil.copyfile(source_wav, out_wav)
        return out_wav

    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source_wav),
            "-af",
            f"volume={gain_db:.3f}dB,alimiter=limit=0.95",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out_wav),
        ]
    )
    return out_wav


def detect_speech_end(video_path: Path, noise_db: float, min_silence: float) -> float | None:
    # Uses silencedetect to find the start of trailing silence (speaker stop).
    # Returns the last silence_start timestamp if found.
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"silencedetect failed for {video_path}: {result.stderr}")

    silence_start = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                value = line.split("silence_start:")[1].strip()
                silence_start = float(value)
            except ValueError:
                continue
    return silence_start


def detect_first_speech_segment(
    video_path: Path, noise_db: float, min_silence: float
) -> tuple[float, float] | None:
    # Returns (speech_start, speech_end) for the first speech segment.
    # Assumption for Gold tier: the video starts with a "generic name" spoken early.
    # We find the first non-silence region by parsing silencedetect output.
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"silencedetect failed for {video_path}: {result.stderr}")

    events: list[tuple[str, float]] = []
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                value = line.split("silence_start:")[1].strip()
                events.append(("silence_start", float(value)))
            except ValueError:
                continue
        if "silence_end:" in line:
            try:
                value = line.split("silence_end:")[1].split("|")[0].strip()
                events.append(("silence_end", float(value)))
            except ValueError:
                continue

    if not events:
        return None

    events.sort(key=lambda e: e[1])

    # If starts in silence, speech starts at first silence_end.
    # Otherwise speech starts at t=0.
    speech_start = 0.0
    if events[0][0] == "silence_start" and abs(events[0][1] - 0.0) < 0.05:
        for typ, ts in events:
            if typ == "silence_end":
                speech_start = ts
                break

    if speech_start is None:
        return None

    # Speech ends at next silence_start after speech_start.
    speech_end = None
    for typ, ts in events:
        if typ == "silence_start" and ts > speech_start + 0.02:
            speech_end = ts
            break

    if speech_end is None:
        return None

    if speech_end <= speech_start:
        return None
    return (speech_start, speech_end)


def safe_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    return slug.strip("_") or "person"


def read_recipients(path: Path, name_col: str, phone_col: str) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)
    if name_col not in df.columns:
        die(f"Missing column '{name_col}' in {path}")
    if phone_col not in df.columns:
        die(f"Missing column '{phone_col}' in {path}")
    df = df[[name_col, phone_col]].dropna()
    return df


def tts_gtts(text: str, lang: str, out_mp3: Path) -> None:
    if gTTS is None:
        die("gTTS is not installed. Run: pip install gTTS")
    tts = gTTS(text=text, lang=lang)
    tts.save(str(out_mp3))

def tts_elevenlabs(
    *,
    text: str,
    out_mp3: Path,
    api_key: str | None,
    voice_id: str | None,
    model_id: str | None,
    speed: float | None,
) -> None:
    api_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
    voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID")
    model_id = model_id or os.environ.get("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2"

    if not api_key:
        die("ElevenLabs API key missing. Set ELEVENLABS_API_KEY or pass --elevenlabs-api-key.")
    if not voice_id:
        die("ElevenLabs voice id missing. Set ELEVENLABS_VOICE_ID or pass --elevenlabs-voice-id.")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id,
    }
    if speed is not None:
        payload["voice_settings"] = {"speed": max(0.7, min(1.2, float(speed)))}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code >= 300:
        raise RuntimeError(f"ElevenLabs TTS failed ({resp.status_code}): {resp.text[:400]}")
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    out_mp3.write_bytes(resp.content)


def tts_command(cmd_template: str, text: str, out_mp3: Path, voice_sample: Path | None) -> None:
    if not cmd_template:
        die("TTS command template is empty. Provide --tts-cmd.")
    cmd = cmd_template.format(text=text, out=str(out_mp3), voice=str(voice_sample or ""))
    args = shlex.split(cmd)
    if not args:
        die("TTS command template produced an empty command.")
    run(args)

def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def name_cache_filename(name: str, cache_key: str) -> str:
    digest = hashlib.sha1(f"{name.strip()}|{cache_key}".encode("utf-8")).hexdigest()[:12]
    return f"{safe_slug(name)}_{digest}.wav"


def name_audio_cache_key(
    *,
    text_template: str,
    lang: str,
    tts_provider: str,
    tts_cmd: str,
    voice_sample: Path | None,
    elevenlabs_voice_id: str | None,
    elevenlabs_model_id: str | None,
    elevenlabs_speed: float | None,
) -> str:
    voice_hash = ""
    if voice_sample and voice_sample.exists():
        voice_hash = file_hash(voice_sample)
    return "|".join(
        [
            tts_provider or "",
            lang or "",
            text_template or "",
            tts_cmd or "",
            elevenlabs_voice_id or "",
            elevenlabs_model_id or "",
            "" if elevenlabs_speed is None else f"{float(elevenlabs_speed):.3f}",
            voice_hash,
        ]
    )


def ensure_name_clip_wav(
    *,
    name: str,
    text_template: str,
    lang: str,
    tts_provider: str,
    tts_cmd: str,
    cache_dir: Path,
    voice_sample: Path | None,
    elevenlabs_api_key: str | None,
    elevenlabs_voice_id: str | None,
    elevenlabs_model_id: str | None,
    elevenlabs_speed: float | None,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = name_audio_cache_key(
        text_template=text_template,
        lang=lang,
        tts_provider=tts_provider,
        tts_cmd=tts_cmd,
        voice_sample=voice_sample,
        elevenlabs_voice_id=elevenlabs_voice_id,
        elevenlabs_model_id=elevenlabs_model_id,
        elevenlabs_speed=elevenlabs_speed,
    )
    out_wav = cache_dir / name_cache_filename(name, cache_key)
    if out_wav.exists():
        return out_wav

    if tts_provider == "none":
        die("TTS provider is 'none'. Cannot build name audio cache.")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tts_mp3 = tmp / "tts.mp3"
        wav_tmp = tmp / "tts.wav"

        text = text_template.format(name=name)
        if tts_provider == "gtts":
            tts_gtts(text=text, lang=lang, out_mp3=tts_mp3)
        elif tts_provider == "elevenlabs":
            tts_elevenlabs(
                text=text,
                out_mp3=tts_mp3,
                api_key=elevenlabs_api_key,
                voice_id=elevenlabs_voice_id,
                model_id=elevenlabs_model_id,
                speed=elevenlabs_speed,
            )
        elif tts_provider == "command":
            tts_command(cmd_template=tts_cmd, text=text, out_mp3=tts_mp3, voice_sample=voice_sample)
        else:
            die(f"Unsupported TTS provider: {tts_provider}")

        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(tts_mp3),
                "-acodec",
                "pcm_s16le",
                str(wav_tmp),
            ]
        )
        shutil.move(str(wav_tmp), str(out_wav))

    return out_wav


def detect_nonsilent_segments(
    *, audio_wav: Path, noise_db: float, min_silence: float, min_segment: float = 0.08
) -> list[tuple[float, float]]:
    cmd = [
        "ffmpeg",
        "-i",
        str(audio_wav),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"silencedetect failed for {audio_wav}: {result.stderr}")

    silences: list[tuple[float, float]] = []
    current_start: float | None = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                current_start = float(line.split("silence_start:")[1].strip())
            except ValueError:
                current_start = None
        elif "silence_end:" in line and current_start is not None:
            try:
                end_ts = float(line.split("silence_end:")[1].split("|")[0].strip())
                silences.append((max(0.0, current_start), max(0.0, end_ts)))
            except ValueError:
                pass
            current_start = None

    total = ffprobe_duration(audio_wav)
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor and (start - cursor) >= min_segment:
            segments.append((cursor, start))
        cursor = max(cursor, end)
    if total > cursor and (total - cursor) >= min_segment:
        segments.append((cursor, total))
    return segments


def ensure_name_clips_batch_tts(
    *,
    names: list[str],
    text_template: str,
    lang: str,
    tts_provider: str,
    tts_cmd: str,
    cache_dir: Path,
    voice_sample: Path | None,
    elevenlabs_api_key: str | None,
    elevenlabs_voice_id: str | None,
    elevenlabs_model_id: str | None,
    elevenlabs_speed: float | None,
    split_silence_db: float,
    split_silence_dur: float,
    batch_gap_hint: str,
) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = name_audio_cache_key(
        text_template=text_template,
        lang=lang,
        tts_provider=tts_provider,
        tts_cmd=tts_cmd,
        voice_sample=voice_sample,
        elevenlabs_voice_id=elevenlabs_voice_id,
        elevenlabs_model_id=elevenlabs_model_id,
        elevenlabs_speed=elevenlabs_speed,
    )
    name_to_wav: dict[str, Path] = {}
    missing: list[str] = []
    for name in names:
        out = cache_dir / name_cache_filename(name, cache_key)
        if out.exists():
            name_to_wav[name] = out
        else:
            missing.append(name)
    if not missing:
        return name_to_wav

    if tts_provider == "none":
        die("TTS provider is 'none'. Cannot build name audio cache.")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        batch_mp3 = tmp / "batch.mp3"
        batch_wav = tmp / "batch.wav"

        sep = (batch_gap_hint or "...").strip()
        sep_text = f". {sep}\n"
        batch_text = sep_text.join(text_template.format(name=n) for n in missing) + "."
        if tts_provider == "gtts":
            tts_gtts(text=batch_text, lang=lang, out_mp3=batch_mp3)
        elif tts_provider == "elevenlabs":
            tts_elevenlabs(
                text=batch_text,
                out_mp3=batch_mp3,
                api_key=elevenlabs_api_key,
                voice_id=elevenlabs_voice_id,
                model_id=elevenlabs_model_id,
                speed=elevenlabs_speed,
            )
        elif tts_provider == "command":
            tts_command(cmd_template=tts_cmd, text=batch_text, out_mp3=batch_mp3, voice_sample=voice_sample)
        else:
            die(f"Unsupported TTS provider: {tts_provider}")

        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(batch_mp3),
                "-acodec",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(batch_wav),
            ]
        )

        split_trials = [
            (split_silence_db, split_silence_dur),
            (split_silence_db + 5.0, max(0.08, split_silence_dur * 0.66)),
            (split_silence_db + 10.0, max(0.05, split_silence_dur * 0.5)),
        ]
        segments: list[tuple[float, float]] = []
        for trial_db, trial_dur in split_trials:
            segments = detect_nonsilent_segments(
                audio_wav=batch_wav,
                noise_db=trial_db,
                min_silence=trial_dur,
            )
            if len(segments) >= len(missing):
                break
        if len(segments) < len(missing):
            raise RuntimeError(
                f"Batch TTS split found only {len(segments)} segments for {len(missing)} names "
                f"(try lower split silence duration / higher split silence dB)"
            )

        for idx, name in enumerate(missing):
            out = cache_dir / name_cache_filename(name, cache_key)
            start, end = segments[idx]
            dur = max(0.05, end - start)
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(batch_wav),
                    "-ss",
                    f"{start:.3f}",
                    "-t",
                    f"{dur:.3f}",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(out),
                ]
            )
            name_to_wav[name] = out

    return name_to_wav


def ensure_silence_wav(*, silence_seconds: float, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ms = int(round(silence_seconds * 1000))
    out_wav = cache_dir / f"_silence_{ms}ms.wav"
    if out_wav.exists():
        return out_wav

    # Generate a silence WAV with ffmpeg's anullsrc.
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            f"{silence_seconds:.3f}",
            "-acodec",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    return out_wav


def build_names_master_wav(
    *,
    name_wavs: list[Path],
    silence_wav: Path,
    out_master: Path,
) -> Path:
    out_master.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        concat_list = tmp / "concat.txt"
        lines: list[str] = []
        for i, wav in enumerate(name_wavs):
            lines.append(f"file '{wav}'")
            if i != len(name_wavs) - 1:
                lines.append(f"file '{silence_wav}'")
        concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-acodec",
                "pcm_s16le",
                str(out_master),
            ]
        )

    return out_master


def build_personalized_video(
    base_video: Path,
    out_dir: Path,
    person_name: str,
    text_template: str,
    lang: str,
    tts_provider: str,
    tts_cmd: str,
    dry_run: bool,
    silence_db: float,
    silence_dur: float,
    name_position: str,
    name_audio_wav: Path | None = None,
    voice_sample: Path | None = None,
    insert_mode: str = "silver",
    elevenlabs_api_key: str | None = None,
    elevenlabs_voice_id: str | None = None,
    elevenlabs_model_id: str | None = None,
    elevenlabs_speed: float | None = None,
    lip_sync_provider: str = "none",
    wav2lip_repo: str = "",
    wav2lip_checkpoint: str = "",
    wav2lip_pads: str = "0 10 0 0",
    wav2lip_python: str = "python3",
    match_name_loudness: bool = True,
    name_loudness_max_gain_db: float = 8.0,
    silver_replace_seconds: float = 0.45,
    silver_gap_seconds: float = 0.12,
    diamond_natural_name: bool = False,
    diamond_gap_seconds: float = 0.12,
    platinum_mode: bool = False,
    platinum_placeholders: str = "NAME1,NAME2",
    platinum_min_silence_dur: float = 0.20,
    platinum_max_placeholder_seconds: float = 0.90,
    gold_max_name_seconds: float = 0.50,
    gold_detect_silence_dur: float = 0.05,
    gold_end_guard_seconds: float = 0.08,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = safe_slug(person_name)
    output_ext = "mp3" if insert_mode == "silver" else "mp4"
    output = out_dir / f"{slug}.{output_ext}"
    # Silver is audio-only but should still replace the first spoken generic name.
    if insert_mode == "silver" and name_position == "end":
        name_position = "start"

    if dry_run:
        print(f"[dry-run] Would create: {output}")
        return output

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tts_mp3 = tmp / "tts.mp3"
        tts_wav = tmp / "tts.wav"
        base_full_wav = tmp / "base_full.wav"
        base_trim_wav = tmp / "base_trim.wav"
        base_suffix_wav = tmp / "base_suffix.wav"
        base_prefix_wav = tmp / "base_prefix.wav"
        name_fit_wav = tmp / "name_fit.wav"
        name_matched_wav = tmp / "name_matched.wav"
        merged_wav = tmp / "merged.wav"
        base_name_slot_wav = tmp / "base_name_slot.wav"

        if name_audio_wav is None:
            text = text_template.format(name=person_name)
            if tts_provider == "gtts":
                tts_gtts(text=text, lang=lang, out_mp3=tts_mp3)
            elif tts_provider == "elevenlabs":
                tts_elevenlabs(
                    text=text,
                    out_mp3=tts_mp3,
                    api_key=elevenlabs_api_key,
                    voice_id=elevenlabs_voice_id,
                    model_id=elevenlabs_model_id,
                    speed=elevenlabs_speed,
                )
            elif tts_provider == "command":
                tts_command(cmd_template=tts_cmd, text=text, out_mp3=tts_mp3, voice_sample=voice_sample)
            elif tts_provider == "none":
                die("TTS provider is 'none'. Use --dry-run to skip generation.")
            else:
                die(f"Unsupported TTS provider: {tts_provider}")

            tts_duration = ffprobe_duration(tts_mp3)
        else:
            tts_duration = ffprobe_duration(name_audio_wav)
        base_duration = ffprobe_duration(base_video)

        # Extract full base audio (used by Gold tier and also reused for Silver start mode).
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(base_video),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(base_full_wav),
            ]
        )

        if insert_mode == "silver":
            detect_min_silence = min(silence_dur, gold_detect_silence_dur)
            seg = detect_first_speech_segment(
                base_video,
                noise_db=silence_db,
                min_silence=detect_min_silence,
            )
            if seg is None:
                speech_start = 0.0
                speech_end = min(base_duration, max(0.2, silver_replace_seconds))
            else:
                speech_start, speech_end = seg
                if (speech_end - speech_start) < 0.12:
                    speech_end = min(base_duration, speech_start + silver_replace_seconds)

            # Keep generated name at natural pace for Silver.
            if name_audio_wav is None:
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(tts_mp3),
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(tts_wav),
                    ]
                )
            else:
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(name_audio_wav),
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(tts_wav),
                    ]
                )
            name_for_merge = tts_wav

            if match_name_loudness:
                slot_dur = max(0.12, speech_end - speech_start)
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(base_full_wav),
                        "-ss",
                        f"{speech_start:.3f}",
                        "-t",
                        f"{slot_dur:.3f}",
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(base_name_slot_wav),
                    ]
                )
                name_for_merge = match_audio_loudness(
                    source_wav=tts_wav,
                    reference_wav=base_name_slot_wav,
                    out_wav=name_matched_wav,
                    max_gain_db=name_loudness_max_gain_db,
                )

            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(base_full_wav),
                    "-ss",
                    f"{speech_end:.3f}",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(base_suffix_wav),
                ]
            )

            concat_inputs = [str(name_for_merge)]
            if silver_gap_seconds > 0:
                gap_wav = ensure_silence_wav(silence_seconds=silver_gap_seconds, cache_dir=tmp)
                concat_inputs.append(str(gap_wav))
            concat_inputs.append(str(base_suffix_wav))

            n = len(concat_inputs)
            filter_parts = "".join(f"[{i}:0]" for i in range(n))
            run(
                [
                    "ffmpeg",
                    "-y",
                    *sum([["-i", p] for p in concat_inputs], []),
                    "-filter_complex",
                    f"{filter_parts}concat=n={n}:v=0:a=1[a]",
                    "-map",
                    "[a]",
                    str(merged_wav),
                ]
            )

            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(merged_wav),
                    "-af",
                    "loudnorm=I=-18:TP=-1.5:LRA=11",
                    "-codec:a",
                    "libmp3lame",
                    "-q:a",
                    "2",
                    str(output),
                ]
            )
            return output

        if insert_mode == "gold" and name_position == "start":
            if platinum_mode:
                placeholders = [p.strip() for p in (platinum_placeholders or "").split(",") if p.strip()]
                if not placeholders:
                    placeholders = ["NAME1"]
                target_count = len(placeholders)

                segments = detect_nonsilent_segments(
                    audio_wav=base_full_wav,
                    noise_db=silence_db,
                    min_silence=platinum_min_silence_dur,
                )
                marker_segments = [
                    (s, e)
                    for (s, e) in segments
                    if (e - s) <= platinum_max_placeholder_seconds
                ]
                if len(marker_segments) < target_count:
                    raise RuntimeError(
                        f"Platinum mode expected {target_count} placeholder segments but detected {len(marker_segments)}. "
                        "Record placeholders as standalone marker words with short pauses."
                    )
                marker_segments = marker_segments[:target_count]

                if name_audio_wav is None:
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(tts_mp3),
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(tts_wav),
                        ]
                    )
                    name_src = tts_wav
                else:
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(name_audio_wav),
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(tts_wav),
                        ]
                    )
                    name_src = tts_wav

                name_for_merge = name_src
                if match_name_loudness:
                    ref_s, ref_e = marker_segments[0]
                    ref_dur = max(0.12, ref_e - ref_s)
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(base_full_wav),
                            "-ss",
                            f"{ref_s:.3f}",
                            "-t",
                            f"{ref_dur:.3f}",
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(base_name_slot_wav),
                        ]
                    )
                    name_for_merge = match_audio_loudness(
                        source_wav=name_src,
                        reference_wav=base_name_slot_wav,
                        out_wav=name_matched_wav,
                        max_gain_db=name_loudness_max_gain_db,
                    )

                concat_inputs: list[str] = []
                cursor = 0.0
                for seg_start, seg_end in marker_segments:
                    if seg_start > cursor:
                        part = tmp / f"pre_{len(concat_inputs)}.wav"
                        run(
                            [
                                "ffmpeg",
                                "-y",
                                "-i",
                                str(base_full_wav),
                                "-ss",
                                f"{cursor:.3f}",
                                "-t",
                                f"{max(0.01, seg_start - cursor):.3f}",
                                "-acodec",
                                "pcm_s16le",
                                "-ar",
                                "48000",
                                "-ac",
                                "2",
                                str(part),
                            ]
                        )
                        concat_inputs.append(str(part))
                    concat_inputs.append(str(name_for_merge))
                    if diamond_gap_seconds > 0:
                        gap_wav = ensure_silence_wav(silence_seconds=diamond_gap_seconds, cache_dir=tmp)
                        concat_inputs.append(str(gap_wav))
                    cursor = seg_end

                if base_duration > cursor:
                    tail = tmp / "tail.wav"
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(base_full_wav),
                            "-ss",
                            f"{cursor:.3f}",
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(tail),
                        ]
                    )
                    concat_inputs.append(str(tail))

                n = len(concat_inputs)
                filter_parts = "".join(f"[{i}:0]" for i in range(n))
                run(
                    [
                        "ffmpeg",
                        "-y",
                        *sum([["-i", p] for p in concat_inputs], []),
                        "-filter_complex",
                        f"{filter_parts}concat=n={n}:v=0:a=1[a]",
                        "-map",
                        "[a]",
                        str(merged_wav),
                    ]
                )

                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(base_video),
                        "-i",
                        str(merged_wav),
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-af",
                        "loudnorm=I=-18:TP=-1.5:LRA=11",
                        str(output),
                    ]
                )
                return apply_lip_sync(
                    video_path=output,
                    provider=lip_sync_provider,
                    wav2lip_repo=wav2lip_repo,
                    wav2lip_checkpoint=wav2lip_checkpoint,
                    wav2lip_pads=wav2lip_pads,
                    wav2lip_python=wav2lip_python,
                )

            if diamond_natural_name:
                seg = detect_first_speech_segment(
                    base_video,
                    noise_db=silence_db,
                    min_silence=min(silence_dur, gold_detect_silence_dur),
                )
                if seg is None:
                    raise RuntimeError(
                        "Diamond natural mode requires a detectable first speech segment."
                    )
                speech_start, speech_end = seg
                if (speech_end - speech_start) < 0.12:
                    speech_end = min(base_duration, speech_start + 0.35)

                if name_audio_wav is None:
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(tts_mp3),
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(tts_wav),
                        ]
                    )
                    name_src = tts_wav
                else:
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(name_audio_wav),
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(tts_wav),
                        ]
                    )
                    name_src = tts_wav

                name_for_merge = name_src
                if match_name_loudness:
                    slot_dur = max(0.12, speech_end - speech_start)
                    run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(base_full_wav),
                            "-ss",
                            f"{speech_start:.3f}",
                            "-t",
                            f"{slot_dur:.3f}",
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(base_name_slot_wav),
                        ]
                    )
                    name_for_merge = match_audio_loudness(
                        source_wav=name_src,
                        reference_wav=base_name_slot_wav,
                        out_wav=name_matched_wav,
                        max_gain_db=name_loudness_max_gain_db,
                    )

                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(base_full_wav),
                        "-t",
                        f"{speech_start:.3f}",
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(base_prefix_wav),
                    ]
                )
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(base_full_wav),
                        "-ss",
                        f"{speech_end:.3f}",
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(base_suffix_wav),
                    ]
                )

                concat_inputs = [str(base_prefix_wav), str(name_for_merge)]
                if diamond_gap_seconds > 0:
                    gap_wav = ensure_silence_wav(silence_seconds=diamond_gap_seconds, cache_dir=tmp)
                    concat_inputs.append(str(gap_wav))
                concat_inputs.append(str(base_suffix_wav))

                n = len(concat_inputs)
                filter_parts = "".join(f"[{i}:0]" for i in range(n))
                run(
                    [
                        "ffmpeg",
                        "-y",
                        *sum([["-i", p] for p in concat_inputs], []),
                        "-filter_complex",
                        f"{filter_parts}concat=n={n}:v=0:a=1[a]",
                        "-map",
                        "[a]",
                        str(merged_wav),
                    ]
                )

                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(base_video),
                        "-i",
                        str(merged_wav),
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-af",
                        "loudnorm=I=-18:TP=-1.5:LRA=11",
                        str(output),
                    ]
                )
                return apply_lip_sync(
                    video_path=output,
                    provider=lip_sync_provider,
                    wav2lip_repo=wav2lip_repo,
                    wav2lip_checkpoint=wav2lip_checkpoint,
                    wav2lip_pads=wav2lip_pads,
                    wav2lip_python=wav2lip_python,
                )
            detect_min_silence = min(silence_dur, gold_detect_silence_dur)
            seg = detect_first_speech_segment(
                base_video,
                noise_db=silence_db,
                min_silence=detect_min_silence,
            )
            if seg is None:
                if insert_mode == "silver":
                    # Audio-only inputs can have weak silence boundaries.
                    speech_start = 0.0
                    speech_end = min(base_duration, max(0.2, silver_replace_seconds))
                else:
                    raise RuntimeError(
                        "Gold mode requires a detectable first speech segment (generic name). "
                        "Try adjusting --silence-db/--silence-dur or use Silver."
                    )
            else:
                speech_start, speech_end = seg
            target_dur = max(0.0, speech_end - speech_start)
            if insert_mode == "silver":
                # Silver should clearly replace the generic name at the start.
                target_dur = max(target_dur, silver_replace_seconds)
                target_dur = min(target_dur, max(0.12, base_duration - speech_start - 0.05))
            if target_dur > gold_max_name_seconds:
                target_dur = gold_max_name_seconds
            # Keep a small safety margin so the next words don't get consumed.
            target_dur = max(0.12, target_dur - max(0.0, gold_end_guard_seconds))
            speech_end = speech_start + target_dur
            if target_dur <= 0.05:
                raise RuntimeError("Gold mode detected too-short generic-name segment.")

            # Prepare name audio input (WAV) and fit it to the original generic-name duration
            if name_audio_wav is None:
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(tts_mp3),
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(tts_wav),
                    ]
                )
                name_src = tts_wav
            else:
                name_src = name_audio_wav

            # Fit to slot duration. Prefer time-stretch over hard trim for naturalness.
            source_dur = ffprobe_duration(name_src)
            speed = source_dur / target_dur if target_dur > 0 else 1.0
            audio_filter = f"{build_atempo_filter(speed)},apad"
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(name_src),
                    "-af",
                    audio_filter,
                    "-t",
                    f"{target_dur:.3f}",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(name_fit_wav),
                ]
            )
            name_for_merge = name_fit_wav
            if match_name_loudness:
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(base_full_wav),
                        "-ss",
                        f"{speech_start:.3f}",
                        "-t",
                        f"{target_dur:.3f}",
                        "-acodec",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "2",
                        str(base_name_slot_wav),
                    ]
                )
                name_for_merge = match_audio_loudness(
                    source_wav=name_fit_wav,
                    reference_wav=base_name_slot_wav,
                    out_wav=name_matched_wav,
                    max_gain_db=name_loudness_max_gain_db,
                )

            # Prefix: base audio before speech_start (includes any initial silence)
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(base_full_wav),
                    "-t",
                    f"{speech_start:.3f}",
                    "-acodec",
                    "pcm_s16le",
                    str(base_prefix_wav),
                ]
            )
            # Suffix: base audio after speech_end
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(base_full_wav),
                    "-ss",
                    f"{speech_end:.3f}",
                    "-acodec",
                    "pcm_s16le",
                    str(base_suffix_wav),
                ]
            )

            # Concatenate prefix + fitted name + suffix
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(base_prefix_wav),
                    "-i",
                    str(name_for_merge),
                    "-i",
                    str(base_suffix_wav),
                    "-filter_complex",
                    "[0:0][1:0][2:0]concat=n=3:v=0:a=1[a]",
                    "-map",
                    "[a]",
                    str(merged_wav),
                ]
            )

            # Mux back with video; keep timing aligned (we preserved original duration).
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(base_video),
                    "-i",
                    str(merged_wav),
                    "-c:v",
                    "copy",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-t",
                    f"{base_duration:.3f}",
                    str(output),
                ]
            )

            return apply_lip_sync(
                video_path=output,
                provider=lip_sync_provider,
                wav2lip_repo=wav2lip_repo,
                wav2lip_checkpoint=wav2lip_checkpoint,
                wav2lip_pads=wav2lip_pads,
                wav2lip_python=wav2lip_python,
            )

        if insert_mode not in ("silver", "gold"):
            die(f"Unsupported insert mode: {insert_mode}")

        if name_position == "end":
            speech_end = detect_speech_end(base_video, noise_db=silence_db, min_silence=silence_dur)
            if speech_end is None:
                keep_duration = max(0.0, base_duration - tts_duration)
            else:
                keep_duration = max(0.0, min(base_duration, speech_end))
            # Extract base audio up to keep_duration
            run([
                "ffmpeg",
                "-y",
                "-i",
                str(base_video),
                "-t",
                f"{keep_duration:.3f}",
                "-vn",
                "-acodec",
                "pcm_s16le",
                str(base_trim_wav),
            ])
        elif name_position == "start":
            # Extract full base audio (to be concatenated after the name)
            shutil.copyfile(base_full_wav, base_trim_wav)
        else:
            die(f"Unsupported name position: {name_position}")

        if name_audio_wav is None:
            # Convert TTS to WAV
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(tts_mp3),
                    "-acodec",
                    "pcm_s16le",
                    str(tts_wav),
                ]
            )
            tts_input_path = tts_wav
        else:
            tts_input_path = Path(name_audio_wav)

        if match_name_loudness:
            tts_input_path = match_audio_loudness(
                source_wav=tts_input_path,
                reference_wav=base_trim_wav,
                out_wav=name_matched_wav,
                max_gain_db=name_loudness_max_gain_db,
            )
        tts_input = str(tts_input_path)

        # Concatenate audio in the chosen order
        if name_position == "start":
            concat_inputs = [tts_input, str(base_trim_wav)]
            concat_filter = "[0:0][1:0]concat=n=2:v=0:a=1[a]"
        else:
            concat_inputs = [str(base_trim_wav), tts_input]
            concat_filter = "[0:0][1:0]concat=n=2:v=0:a=1[a]"

        run([
            "ffmpeg",
            "-y",
            "-i",
            concat_inputs[0],
            "-i",
            concat_inputs[1],
            "-filter_complex",
            concat_filter,
            "-map",
            "[a]",
            str(merged_wav),
        ])

        if insert_mode == "silver":
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(merged_wav),
                    "-codec:a",
                    "libmp3lame",
                    "-q:a",
                    "2",
                    str(output),
                ]
            )
            return output

        # Mux audio back with video
        mux_cmd = ["ffmpeg", "-y", "-i", str(base_video), "-i", str(merged_wav)]
        if name_position == "start":
            # Pad video with a frozen first frame so name audio plays before the speaker starts.
            mux_cmd += [
                "-filter_complex",
                f"[0:v]tpad=start_duration={tts_duration:.3f}:start_mode=clone[v]",
                "-map",
                "[v]",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-preset",
                "medium",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
            ]
        else:
            mux_cmd += [
                "-c:v",
                "copy",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-t",
                f"{base_duration:.3f}",
            ]
        mux_cmd.append(str(output))
        run(mux_cmd)

    return apply_lip_sync(
        video_path=output,
        provider=lip_sync_provider,
        wav2lip_repo=wav2lip_repo,
        wav2lip_checkpoint=wav2lip_checkpoint,
        wav2lip_pads=wav2lip_pads,
        wav2lip_python=wav2lip_python,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Personalized video generator")
    parser.add_argument("--video", required=True, help="Path to base video (MP4)")
    parser.add_argument("--recipients", required=True, help="Path to recipients CSV or XLSX")
    parser.add_argument("--outdir", default="output", help="Output directory")
    parser.add_argument("--name-col", default="name", help="Column name for person name")
    parser.add_argument("--phone-col", default="phone", help="Column name for phone")
    parser.add_argument(
        "--text",
        default="{name}",
        help="Text template. Use {name} placeholder",
    )
    parser.add_argument("--lang", default="hi", help="TTS language code (gTTS)")
    parser.add_argument(
        "--tts-provider",
        default="gtts",
        choices=["gtts", "elevenlabs", "command", "none"],
        help="TTS provider: gtts, elevenlabs, command, or none",
    )
    parser.add_argument(
        "--tts-cmd",
        default="",
        help="Command template for TTS provider 'command'. Use {text}, {out}, and optional {voice} placeholders.",
    )
    parser.add_argument("--elevenlabs-api-key", default="", help="Optional ElevenLabs API key (otherwise uses ELEVENLABS_API_KEY).")
    parser.add_argument("--elevenlabs-voice-id", default="", help="Optional ElevenLabs voice id (otherwise uses ELEVENLABS_VOICE_ID).")
    parser.add_argument("--elevenlabs-model-id", default="", help="Optional ElevenLabs model id (otherwise uses ELEVENLABS_MODEL_ID).")
    parser.add_argument(
        "--elevenlabs-speed",
        type=float,
        default=1.0,
        help="ElevenLabs voice speed (0.7 to 1.2).",
    )
    parser.add_argument(
        "--voice-sample",
        default="",
        help="Optional voice sample path for external TTS providers (available as {voice} in --tts-cmd).",
    )
    parser.add_argument(
        "--insert-mode",
        choices=["silver", "gold"],
        default="silver",
        help="Insert mode: silver (pad video for start) or gold (replace generic-name audio at start).",
    )
    parser.add_argument(
        "--lip-sync-provider",
        choices=["none", "wav2lip", "sync_api"],
        default="none",
        help="Optional lip-sync pass after video generation.",
    )
    parser.add_argument(
        "--wav2lip-repo",
        default="",
        help="Path to Wav2Lip repo (contains inference.py).",
    )
    parser.add_argument(
        "--wav2lip-checkpoint",
        default="",
        help="Path to Wav2Lip checkpoint (.pth).",
    )
    parser.add_argument(
        "--wav2lip-pads",
        default="0 10 0 0",
        help="Wav2Lip face padding as 'top bottom left right'.",
    )
    parser.add_argument(
        "--wav2lip-python",
        default="python3",
        help="Python executable used to run Wav2Lip inference.py.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned outputs only")
    parser.add_argument(
        "--silence-db",
        type=float,
        default=-30.0,
        help="Silence threshold in dB for speech end detection",
    )
    parser.add_argument(
        "--silence-dur",
        type=float,
        default=0.3,
        help="Minimum silence duration in seconds to detect speech end",
    )
    parser.add_argument(
        "--name-position",
        choices=["start", "end"],
        default="start",
        help="Where to insert the spoken name: start or end",
    )
    parser.add_argument(
        "--match-name-loudness",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Match inserted name loudness to nearby/base audio.",
    )
    parser.add_argument(
        "--name-loudness-max-gain-db",
        type=float,
        default=8.0,
        help="Max gain/attenuation applied for name loudness matching (dB).",
    )
    parser.add_argument(
        "--silver-replace-seconds",
        type=float,
        default=0.45,
        help="Silver mode: minimum duration to replace at first spoken name.",
    )
    parser.add_argument(
        "--silver-gap-seconds",
        type=float,
        default=0.12,
        help="Silver mode: silence gap after generated name before rest of audio.",
    )
    parser.add_argument(
        "--diamond-natural-name",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Diamond mode: keep generated name at natural speed (no forced fit).",
    )
    parser.add_argument(
        "--diamond-gap-seconds",
        type=float,
        default=0.12,
        help="Diamond mode: silence gap after generated name before rest of audio.",
    )
    parser.add_argument(
        "--platinum-mode",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Platinum mode: replace multiple placeholder marker words.",
    )
    parser.add_argument(
        "--platinum-placeholders",
        default="NAME1,NAME2",
        help="Comma-separated placeholder markers spoken in base media, in order.",
    )
    parser.add_argument(
        "--platinum-min-silence-dur",
        type=float,
        default=0.20,
        help="Platinum mode: silence duration used to split placeholder marker segments.",
    )
    parser.add_argument(
        "--platinum-max-placeholder-seconds",
        type=float,
        default=0.90,
        help="Platinum mode: max duration of each placeholder marker segment.",
    )
    parser.add_argument(
        "--gold-max-name-seconds",
        type=float,
        default=0.50,
        help="Gold mode: hard cap for replaced name slot duration at start.",
    )
    parser.add_argument(
        "--gold-detect-silence-dur",
        type=float,
        default=0.05,
        help="Gold mode: silence duration for first-name boundary detection.",
    )
    parser.add_argument(
        "--gold-end-guard-seconds",
        type=float,
        default=0.08,
        help="Gold mode: keeps this much of detected segment at the end untouched.",
    )
    parser.add_argument(
        "--build-name-cache",
        action="store_true",
        help="Build per-name audio clips once and reuse them for all outputs",
    )
    parser.add_argument(
        "--batch-name-tts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When building cache, synthesize all names in one TTS call and split by silence.",
    )
    parser.add_argument(
        "--batch-split-silence-db",
        type=float,
        default=-40.0,
        help="Silence threshold for splitting batch-generated names audio.",
    )
    parser.add_argument(
        "--batch-split-silence-dur",
        type=float,
        default=0.18,
        help="Minimum silence duration used while splitting batch-generated names audio.",
    )
    parser.add_argument(
        "--batch-gap-hint",
        default="ठहराव",
        help="Prompt hint inserted between names in batch TTS to encourage short pauses.",
    )
    parser.add_argument(
        "--name-cache-dir",
        default="",
        help="Directory to store per-name audio clips (WAV). Default: <outdir>/_name_audio",
    )
    parser.add_argument(
        "--names-master-out",
        default="",
        help="Optional path to write a master names WAV (all names one after another).",
    )
    parser.add_argument(
        "--name-gap",
        type=float,
        default=0.4,
        help="Seconds of silence between names in the master track (when building cache).",
    )

    args = parser.parse_args()

    if not args.dry_run and (shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None):
        die("ffmpeg/ffprobe not found. Install ffmpeg first.")

    base_video = Path(args.video)
    recipients = Path(args.recipients)
    out_dir = Path(args.outdir)

    if not base_video.exists():
        die(f"Base video not found: {base_video}")
    if not recipients.exists():
        die(f"Recipients file not found: {recipients}")

    df = read_recipients(recipients, args.name_col, args.phone_col)

    name_cache_dir = Path(args.name_cache_dir) if args.name_cache_dir else (out_dir / "_name_audio")
    voice_sample = Path(args.voice_sample) if args.voice_sample else None
    elevenlabs_api_key = args.elevenlabs_api_key or None
    elevenlabs_voice_id = args.elevenlabs_voice_id or None
    elevenlabs_model_id = args.elevenlabs_model_id or None

    name_to_wav: dict[str, Path] = {}
    if args.build_name_cache and not args.dry_run:
        unique_names = []
        seen = set()
        for n in df[args.name_col].astype(str).map(lambda s: s.strip()):
            if not n or n in seen:
                continue
            seen.add(n)
            unique_names.append(n)

        print(f"Building name audio cache for {len(unique_names)} unique names...")
        batch_enabled = args.batch_name_tts and args.tts_provider in ("elevenlabs", "command", "gtts")
        if batch_enabled:
            try:
                name_to_wav.update(
                    ensure_name_clips_batch_tts(
                        names=unique_names,
                        text_template=args.text,
                        lang=args.lang,
                        tts_provider=args.tts_provider,
                        tts_cmd=args.tts_cmd,
                        cache_dir=name_cache_dir,
                        voice_sample=voice_sample,
                        elevenlabs_api_key=elevenlabs_api_key,
                        elevenlabs_voice_id=elevenlabs_voice_id,
                        elevenlabs_model_id=elevenlabs_model_id,
                        elevenlabs_speed=args.elevenlabs_speed,
                        split_silence_db=args.batch_split_silence_db,
                        split_silence_dur=args.batch_split_silence_dur,
                        batch_gap_hint=args.batch_gap_hint,
                    )
                )
            except Exception as exc:
                print(f"Batch name TTS split failed, falling back to per-name synthesis: {exc}")

        for n in unique_names:
            if n in name_to_wav and name_to_wav[n].exists():
                continue
            name_to_wav[n] = ensure_name_clip_wav(
                name=n,
                text_template=args.text,
                lang=args.lang,
                tts_provider=args.tts_provider,
                tts_cmd=args.tts_cmd,
                cache_dir=name_cache_dir,
                voice_sample=voice_sample,
                elevenlabs_api_key=elevenlabs_api_key,
                elevenlabs_voice_id=elevenlabs_voice_id,
                elevenlabs_model_id=elevenlabs_model_id,
                elevenlabs_speed=args.elevenlabs_speed,
            )

        if args.names_master_out:
            silence = ensure_silence_wav(silence_seconds=args.name_gap, cache_dir=name_cache_dir)
            master_out = Path(args.names_master_out)
            build_names_master_wav(
                name_wavs=[name_to_wav[n] for n in unique_names],
                silence_wav=silence,
                out_master=master_out,
            )
            print(f"Created names master: {master_out}")

    print(f"Generating {len(df)} videos...")
    for _, row in df.iterrows():
        name = str(row[args.name_col]).strip()
        if not name:
            continue
        try:
            name_audio_wav = None
            if args.build_name_cache and not args.dry_run:
                name_audio_wav = name_to_wav.get(name)
                if name_audio_wav is None:
                    name_audio_wav = ensure_name_clip_wav(
                        name=name,
                        text_template=args.text,
                        lang=args.lang,
                        tts_provider=args.tts_provider,
                        tts_cmd=args.tts_cmd,
                        cache_dir=name_cache_dir,
                        voice_sample=voice_sample,
                        elevenlabs_api_key=elevenlabs_api_key,
                        elevenlabs_voice_id=elevenlabs_voice_id,
                        elevenlabs_model_id=elevenlabs_model_id,
                        elevenlabs_speed=args.elevenlabs_speed,
                    )
                    name_to_wav[name] = name_audio_wav

            output = build_personalized_video(
                base_video=base_video,
                out_dir=out_dir,
                person_name=name,
                text_template=args.text,
                lang=args.lang,
                tts_provider=args.tts_provider,
                tts_cmd=args.tts_cmd,
                dry_run=args.dry_run,
                silence_db=args.silence_db,
                silence_dur=args.silence_dur,
                name_position=args.name_position,
                name_audio_wav=name_audio_wav,
                voice_sample=voice_sample,
                insert_mode=args.insert_mode,
                lip_sync_provider=args.lip_sync_provider,
                wav2lip_repo=args.wav2lip_repo,
                wav2lip_checkpoint=args.wav2lip_checkpoint,
                wav2lip_pads=args.wav2lip_pads,
                wav2lip_python=args.wav2lip_python,
                elevenlabs_api_key=elevenlabs_api_key,
                elevenlabs_voice_id=elevenlabs_voice_id,
                elevenlabs_model_id=elevenlabs_model_id,
                elevenlabs_speed=args.elevenlabs_speed,
                match_name_loudness=args.match_name_loudness,
                name_loudness_max_gain_db=args.name_loudness_max_gain_db,
                silver_replace_seconds=args.silver_replace_seconds,
                silver_gap_seconds=args.silver_gap_seconds,
                diamond_natural_name=args.diamond_natural_name,
                diamond_gap_seconds=args.diamond_gap_seconds,
                platinum_mode=args.platinum_mode,
                platinum_placeholders=args.platinum_placeholders,
                platinum_min_silence_dur=args.platinum_min_silence_dur,
                platinum_max_placeholder_seconds=args.platinum_max_placeholder_seconds,
                gold_max_name_seconds=args.gold_max_name_seconds,
                gold_detect_silence_dur=args.gold_detect_silence_dur,
                gold_end_guard_seconds=args.gold_end_guard_seconds,
            )
            print(f"Created: {output}")
        except Exception as exc:
            print(f"Failed for {name}: {exc}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
