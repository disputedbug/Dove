#!/usr/bin/env python3
import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

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

def tts_command(cmd_template: str, text: str, out_mp3: Path) -> None:
    if not cmd_template:
        die("TTS command template is empty. Provide --tts-cmd.")
    cmd = cmd_template.format(text=text, out=str(out_mp3))
    args = shlex.split(cmd)
    if not args:
        die("TTS command template produced an empty command.")
    run(args)


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
        base_trim_wav = tmp / "base_trim.wav"
        merged_wav = tmp / "merged.wav"

        text = text_template.format(name=person_name)
        if tts_provider == "gtts":
            tts_gtts(text=text, lang=lang, out_mp3=tts_mp3)
        elif tts_provider == "command":
            tts_command(cmd_template=tts_cmd, text=text, out_mp3=tts_mp3)
        elif tts_provider == "none":
            die("TTS provider is 'none'. Use --dry-run to skip generation.")
        else:
            die(f"Unsupported TTS provider: {tts_provider}")

        tts_duration = ffprobe_duration(tts_mp3)
        base_duration = ffprobe_duration(base_video)
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
            run([
                "ffmpeg",
                "-y",
                "-i",
                str(base_video),
                "-vn",
                "-acodec",
                "pcm_s16le",
                str(base_trim_wav),
            ])
        else:
            die(f"Unsupported name position: {name_position}")

        # Convert TTS to WAV
        run([
            "ffmpeg",
            "-y",
            "-i",
            str(tts_mp3),
            "-acodec",
            "pcm_s16le",
            str(tts_wav),
        ])

        # Concatenate audio in the chosen order
        if name_position == "start":
            concat_inputs = [str(tts_wav), str(base_trim_wav)]
            concat_filter = "[0:0][1:0]concat=n=2:v=0:a=1[a]"
        else:
            concat_inputs = [str(base_trim_wav), str(tts_wav)]
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
        choices=["gtts", "command", "none"],
        help="TTS provider: gtts, command, or none",
    )
    parser.add_argument(
        "--tts-cmd",
        default="",
        help="Command template for TTS provider 'command'. Use {text} and {out} placeholders.",
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

    print(f"Generating {len(df)} videos...")
    for _, row in df.iterrows():
        name = str(row[args.name_col]).strip()
        if not name:
            continue
        try:
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
            )
            print(f"Created: {output}")
        except Exception as exc:
            print(f"Failed for {name}: {exc}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
