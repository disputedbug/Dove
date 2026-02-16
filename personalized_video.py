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
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    voice_hash = ""
    if voice_sample and voice_sample.exists():
        voice_hash = file_hash(voice_sample)
    cache_key = "|".join(
        [
            tts_provider or "",
            lang or "",
            text_template or "",
            tts_cmd or "",
            elevenlabs_voice_id or "",
            elevenlabs_model_id or "",
            voice_hash,
        ]
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
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = safe_slug(person_name)
    output = out_dir / f"{slug}.mp4"

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
        merged_wav = tmp / "merged.wav"

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

        if insert_mode == "gold" and name_position == "start":
            seg = detect_first_speech_segment(base_video, noise_db=silence_db, min_silence=silence_dur)
            if seg is None:
                raise RuntimeError(
                    "Gold mode requires a detectable first speech segment (generic name). "
                    "Try adjusting --silence-db/--silence-dur or use Silver."
                )
            speech_start, speech_end = seg
            target_dur = max(0.0, speech_end - speech_start)
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

            # Fit: pad or trim while keeping sample rate/channel layout consistent.
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(name_src),
                    "-filter_complex",
                    "apad",
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
                    str(name_fit_wav),
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

            return output

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
            tts_input = str(tts_wav)
        else:
            tts_input = str(name_audio_wav)

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

    return output


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
        "--build-name-cache",
        action="store_true",
        help="Build per-name audio clips once and reuse them for all outputs",
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
        for n in unique_names:
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
                elevenlabs_api_key=elevenlabs_api_key,
                elevenlabs_voice_id=elevenlabs_voice_id,
                elevenlabs_model_id=elevenlabs_model_id,
            )
            print(f"Created: {output}")
        except Exception as exc:
            print(f"Failed for {name}: {exc}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
