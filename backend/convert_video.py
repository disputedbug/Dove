#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert MOV to Android-friendly MP4")
    parser.add_argument("--input", required=True, help="Input video path (.MOV or other)")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    parser.add_argument("--crf", type=int, default=20, help="CRF quality (lower = better)")
    parser.add_argument("--preset", default="medium", help="x264 preset")
    parser.add_argument("--audio-bitrate", default="160k", help="Audio bitrate")

    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        die("ffmpeg not found. Install ffmpeg first.")

    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.exists():
        die(f"Input not found: {in_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if in_path.resolve() == out_path.resolve():
        die("Input and output paths must be different.")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(in_path),
        "-c:v",
        "libx264",
        "-preset",
        args.preset,
        "-crf",
        str(args.crf),
        "-c:a",
        "aac",
        "-b:a",
        args.audio_bitrate,
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    run(cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
