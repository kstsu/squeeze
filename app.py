from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import imageio_ffmpeg
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask


BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
TEMP_ROOT = Path(tempfile.gettempdir()) / "squeeze-video-jobs"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
MAX_COMPRESSION_SECONDS = 2 * 60 * 60
MIN_TARGET_MB = 1.0
MAX_TARGET_MB = 4096.0
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
OUTPUT_FORMATS = {
    "mp4": {"extension": "mp4", "mime": "video/mp4", "video_codec": "libx264", "audio_codec": "aac", "pixel_format": "yuv420p", "format_flags": ["-movflags", "+faststart"]},
    "mov": {"extension": "mov", "mime": "video/quicktime", "video_codec": "libx264", "audio_codec": "aac", "pixel_format": "yuv420p", "format_flags": []},
    "webm": {"extension": "webm", "mime": "video/webm", "video_codec": "libvpx-vp9", "audio_codec": "libopus", "pixel_format": "yuv420p", "format_flags": []},
}
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
DURATION_PATTERN = re.compile(r"Duration: (\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("squeeze")
cleanup_tasks: set[asyncio.Task[None]] = set()


@dataclass(frozen=True, slots=True)
class CompressionJob:
    job_id: str
    source: Path
    output: Path
    target_mb: float
    original_name: str
    output_format: str = "mp4"
    trim_start: float = 0.0
    trim_end: float | None = None


class CompressionError(Exception):
    """Raised when a video cannot be validated or compressed."""


app = FastAPI(title="Squeeze Video Compressor", docs_url=None, redoc_url=None)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in security_headers().items():
        response.headers.setdefault(name, value)
    return response


def security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def safe_name(filename: str | None) -> str:
    original = Path(filename or "video.mp4").name
    cleaned = SAFE_FILENAME.sub("-", original).strip(".-") or "video.mp4"
    extension = Path(cleaned).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise CompressionError("Use an MP4, MOV, WEBM, MKV, AVI, or M4V video file.")
    return cleaned[:180]


def safe_download_filename(filename: str) -> str:
    candidate = Path(filename).name
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", candidate).strip(".")
    if Path(cleaned).suffix.lower().lstrip(".") not in OUTPUT_FORMATS:
        cleaned = f"{Path(cleaned).stem}.mp4"
    return cleaned[:180] or "compressed-video.mp4"


def parse_target(target_mb: str) -> float:
    try:
        target = float(target_mb)
    except (TypeError, ValueError) as error:
        raise CompressionError("Enter a valid target size in MB.") from error
    if not MIN_TARGET_MB <= target <= MAX_TARGET_MB:
        raise CompressionError(f"Target size must be between {MIN_TARGET_MB:g} and {MAX_TARGET_MB:g} MB.")
    return target


def parse_output_format(output_format: str) -> str:
    normalized = output_format.strip().lower()
    if normalized not in OUTPUT_FORMATS:
        raise CompressionError("Choose MP4, MOV, or WebM as the output format.")
    return normalized


def parse_trim_seconds(value: str | None, label: str) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        seconds = float(value)
    except ValueError as error:
        raise CompressionError(f"Enter a valid {label} time in seconds.") from error
    if not math.isfinite(seconds) or seconds < 0:
        raise CompressionError(f"{label.capitalize()} time must be zero or greater.")
    return seconds


def require_binary(binary: str) -> str:
    path = shutil.which(binary)
    if path:
        return path
    if binary == "ffmpeg":
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except RuntimeError as error:
            raise CompressionError("FFmpeg is not available. Install the project dependencies again and restart the server.") from error
    raise CompressionError(f"{binary} is not installed or is not available on PATH.")


def run_probe(path: Path) -> tuple[float, bool]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        command = [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
            payload = json.loads(result.stdout)
            duration = float(payload.get("format", {}).get("duration", 0) or 0)
            has_video = any(stream.get("codec_type") == "video" for stream in payload.get("streams", []))
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
            raise CompressionError("FFmpeg could not read this video. Try exporting it as MP4 and upload it again.") from error
    else:
        ffmpeg = require_binary("ffmpeg")
        try:
            result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60)
            metadata = f"{result.stdout}\n{result.stderr}"
            match = DURATION_PATTERN.search(metadata)
            duration = 0.0 if not match else int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
            has_video = bool(re.search(r"Stream #[^\n]+: Video:", metadata))
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise CompressionError("FFmpeg could not read this video. Try exporting it as MP4 and upload it again.") from error
    if not has_video or duration <= 0:
        raise CompressionError("The uploaded file does not contain a readable video stream.")
    return duration, has_video


def calculate_bitrate_kbps(target_mb: float, duration_seconds: float) -> int:
    target_bits = target_mb * 1024 * 1024 * 8 * 0.92
    audio_bits = 64_000
    video_bits = (target_bits / max(duration_seconds, 1.0)) - audio_bits
    return max(160, int(video_bits / 1000))


def build_ffmpeg_command(ffmpeg: str, job: CompressionJob, duration: float, bitrate: int, trim_duration: float | None = None) -> list[str]:
    settings = OUTPUT_FORMATS[job.output_format]
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-stats_period", "0.25"]
    if job.trim_start > 0:
        command.extend(["-ss", f"{job.trim_start:.3f}"])
    command.extend(["-i", str(job.source)])
    if trim_duration is not None:
        command.extend(["-t", f"{trim_duration:.3f}"])
    command.extend([
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", settings["video_codec"],
        "-threads", "0",
        "-b:v", f"{bitrate}k",
        "-maxrate", f"{int(bitrate * 1.15)}k",
        "-bufsize", f"{bitrate * 2}k",
        "-pix_fmt", settings["pixel_format"],
        "-c:a", settings["audio_codec"],
        "-b:a", "64k",
    ])
    if job.output_format in {"mp4", "mov"}:
        command[command.index("-threads"):command.index("-threads")] = ["-preset", "superfast"]
    else:
        command[command.index("-threads"):command.index("-threads")] = [
            "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-tile-columns", "2"
        ]
    command.extend(settings["format_flags"])
    command.extend(["-y", str(job.output)])
    return command


def compressed_filename(original_name: str, output_format: str = "mp4") -> str:
    return f"{Path(original_name).stem}-sqzd.{parse_output_format(output_format)}"


def progress_event(percent: int, label: str, detail: str) -> bytes:
    payload = json.dumps({"type": "progress", "percent": percent, "label": label, "detail": detail})
    return f"data: {payload}\n\n".encode()


def complete_event(job: CompressionJob, compressed_bytes: int) -> bytes:
    original_bytes = job.source.stat().st_size
    payload = {
        "type": "complete",
        "original_bytes": original_bytes,
        "compressed_bytes": compressed_bytes,
        "saved_bytes": max(0, original_bytes - compressed_bytes),
        "filename": compressed_filename(job.original_name, job.output_format),
        "job_id": job.job_id,
        "download_url": f"/api/download/{job.job_id}",
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


async def save_upload(upload: UploadFile, destination: Path) -> int:
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise CompressionError("That video is larger than the 4 GB upload limit.")
                output.write(chunk)
    finally:
        await upload.close()
    if total == 0:
        raise CompressionError("The uploaded file is empty.")
    return total


async def run_compression(job: CompressionJob) -> AsyncIterator[bytes]:
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(MAX_COMPRESSION_SECONDS):
            yield progress_event(5, "Checking video", "Reading duration and stream information.")
            source_duration, _ = await asyncio.to_thread(run_probe, job.source)
            trim_end = source_duration if job.trim_end is None else min(job.trim_end, source_duration)
            if job.trim_start >= source_duration or trim_end <= job.trim_start:
                raise CompressionError("Choose a trim range inside the video duration.")
            encode_duration = trim_end - job.trim_start
            bitrate = calculate_bitrate_kbps(job.target_mb, encode_duration)
            ffmpeg = require_binary("ffmpeg")
            trim_duration = encode_duration if job.trim_start > 0 or job.trim_end is not None else None
            command = build_ffmpeg_command(ffmpeg, job, source_duration, bitrate, trim_duration)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            stderr_task = asyncio.create_task(process.stderr.read())
            yield progress_event(8, "Compressing video", f"Native FFmpeg encoding has started for {job.output_format.upper()}.")
            last_percent = 8
            encode_started = time.monotonic()
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=0.75)
                except asyncio.TimeoutError:
                    elapsed = int(time.monotonic() - encode_started)
                    yield progress_event(
                        last_percent,
                        "Compressing video",
                        f"FFmpeg is still encoding {job.output_format.upper()} · {elapsed}s elapsed.",
                    )
                    continue
                if not line:
                    break
                text = line.decode(errors="replace").strip()
                if text.startswith("out_time_ms="):
                    try:
                        encoded_seconds = int(text.split("=", 1)[1]) / 1_000_000
                        percent = min(97, 8 + int((encoded_seconds / encode_duration) * 89))
                        if percent > last_percent:
                            last_percent = percent
                            yield progress_event(percent, "Compressing video", f"FFmpeg is writing a smaller {job.output_format.upper()} video.")
                    except ValueError:
                        continue
            stderr = (await stderr_task).decode(errors="replace").strip()
            return_code = await process.wait()
            if return_code != 0 or not job.output.exists():
                LOGGER.warning("FFmpeg failed for job %s: %s", job.job_id, stderr[-500:])
                raise CompressionError("FFmpeg could not compress this video. Try a different file or target size.")
            yield progress_event(99, "Preparing download", f"The compressed {job.output_format.upper()} video is ready.")
            yield complete_event(job, job.output.stat().st_size)
    except asyncio.TimeoutError as error:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        raise CompressionError("This video took too long to compress. Try a shorter video or a larger target size.") from error
    except asyncio.CancelledError:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        raise
    finally:
        if job.source.exists():
            job.source.unlink(missing_ok=True)


async def remove_job_directory(job_dir: Path, delay_seconds: int = 900) -> None:
    try:
        await asyncio.sleep(delay_seconds)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def schedule_cleanup(job_dir: Path) -> None:
    task = asyncio.create_task(remove_job_directory(job_dir))
    cleanup_tasks.add(task)
    task.add_done_callback(cleanup_tasks.discard)


def remove_download_directory(job_dir: Path) -> None:
    shutil.rmtree(job_dir, ignore_errors=True)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = (BASE_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers=security_headers())


@app.get("/static/styles.css")
async def styles() -> FileResponse:
    return FileResponse(BASE_DIR / "styles.css", media_type="text/css")


@app.get("/static/script.js")
async def script() -> FileResponse:
    return FileResponse(BASE_DIR / "script.js", media_type="text/javascript")


@app.get("/static/favicon.svg")
async def favicon() -> FileResponse:
    return FileResponse(BASE_DIR / "favicon.svg", media_type="image/svg+xml")


@app.post("/api/compress", response_model=None)
async def compress(video: UploadFile = File(...), target_mb: str = Form(...), output_format: str = Form("mp4"), trim_start: str = Form("0"), trim_end: str = Form("")) -> StreamingResponse | JSONResponse:
    job_id = uuid.uuid4().hex
    job_dir = TEMP_ROOT / job_id
    job_dir.mkdir(mode=0o700)
    source = job_dir / "source-upload"
    output = job_dir / "compressed.mp4"
    try:
        original_name = safe_name(video.filename)
        target = parse_target(target_mb)
        selected_format = parse_output_format(output_format)
        selected_trim_start = parse_trim_seconds(trim_start, "start") or 0.0
        selected_trim_end = parse_trim_seconds(trim_end, "end")
        await save_upload(video, source)
        output = job_dir / f"compressed.{selected_format}"
        (job_dir / "download-name.txt").write_text(compressed_filename(original_name, selected_format), encoding="utf-8")
        job = CompressionJob(job_id, source, output, target, original_name, selected_format, selected_trim_start, selected_trim_end)

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for event in run_compression(job):
                    yield event
            except CompressionError as error:
                yield f"data: {json.dumps({'type': 'error', 'detail': str(error)})}\n\n".encode()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Compression job %s failed unexpectedly", job_id)
                yield f"data: {json.dumps({'type': 'error', 'detail': 'Compression failed unexpectedly. Try again with another video.'})}\n\n".encode()
            finally:
                if output.exists():
                    schedule_cleanup(job_dir)
                else:
                    shutil.rmtree(job_dir, ignore_errors=True)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={**security_headers(), "X-Accel-Buffering": "no"})
    except CompressionError as error:
        shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse({"detail": str(error)}, status_code=422, headers=security_headers())
    except (OSError, ValueError) as error:
        LOGGER.exception("Failed to start compression job %s", job_id)
        shutil.rmtree(job_dir, ignore_errors=True)
        return JSONResponse({"detail": "The server could not start this compression job."}, status_code=500, headers=security_headers())


@app.get("/api/download/{job_id}", response_model=None)
async def download(job_id: str) -> FileResponse | JSONResponse:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return JSONResponse({"detail": "Download not found."}, status_code=404, headers=security_headers())
    job_dir = TEMP_ROOT / job_id
    name_file = job_dir / "download-name.txt"
    output_candidates = list(job_dir.glob("compressed.*"))
    output = output_candidates[0] if output_candidates else job_dir / "compressed.mp4"
    if not output.is_file():
        return JSONResponse({"detail": "Download expired or was already removed."}, status_code=404, headers=security_headers())
    download_name = "compressed-video.mp4"
    if name_file.is_file():
        try:
            download_name = safe_download_filename(name_file.read_text(encoding="utf-8"))
        except OSError:
            download_name = "compressed-video.mp4"
    output_format = output.suffix.lower().lstrip(".")
    output_settings = OUTPUT_FORMATS.get(output_format, OUTPUT_FORMATS["mp4"])
    return FileResponse(
        output,
        media_type=output_settings["mime"],
        filename=download_name,
        headers={**security_headers(), "Content-Disposition": f'attachment; filename="{download_name}"'},
        background=BackgroundTask(remove_download_directory, output.parent),
    )


@app.get("/health", response_model=None)
async def health() -> JSONResponse:
    try:
        require_binary("ffmpeg")
        ffmpeg_ready = True
    except CompressionError:
        ffmpeg_ready = False
    return JSONResponse({"status": "ok", "ffmpeg_ready": ffmpeg_ready}, headers=security_headers())
