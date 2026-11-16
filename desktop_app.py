from __future__ import annotations

import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn
import webview

from app import TEMP_ROOT, app
from app import OUTPUT_FORMATS


JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(url: str, timeout_seconds: float = 10.0) -> None:
    deadline = threading.Event()
    end_time = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < end_time:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        deadline.wait(0.1)
    raise RuntimeError("The local Squeeze service did not start.") from last_error


def safe_download_name(filename: str) -> str:
    name = Path(filename).name
    cleaned = SAFE_FILENAME_PATTERN.sub("-", name).strip(".-") or "compressed-video.mp4"
    extension = Path(cleaned).suffix.lower().lstrip(".")
    if extension not in OUTPUT_FORMATS:
        cleaned = f"{Path(cleaned).stem}.mp4"
    return cleaned[:180]


class DesktopApi:
    def __init__(self) -> None:
        self.window: webview.Window | None = None

    def save_compressed_file(self, job_id: str, suggested_name: str) -> dict[str, Any]:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            return {"ok": False, "message": "This download is no longer available."}

        job_dir = TEMP_ROOT / job_id
        candidates = list(job_dir.glob("compressed.*"))
        source = candidates[0] if candidates else job_dir / "compressed.mp4"
        if not source.is_file():
            return {"ok": False, "message": "This download expired. Compress the video again."}
        if self.window is None:
            return {"ok": False, "message": "The desktop save window is not available."}

        filename = safe_download_name(suggested_name)
        extension = Path(filename).suffix.lower().lstrip(".")
        destination_result = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            directory=str(Path.home() / "Downloads"),
            save_filename=filename,
            file_types=(f"{extension.upper()} video (*.{extension})",),
        )
        if not destination_result:
            return {"ok": False, "cancelled": True}

        destination = Path(destination_result[0]).expanduser()
        if destination.suffix.lower() != f".{extension}":
            destination = destination.with_suffix(f".{extension}")
        try:
            shutil.copyfile(source, destination)
        except OSError:
            return {"ok": False, "message": "Squeeze could not save the MP4 to that location."}
        finally:
            shutil.rmtree(source.parent, ignore_errors=True)
        return {"ok": True, "path": str(destination)}


def show_startup_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Squeeze could not start", message)
        root.destroy()
    except Exception:
        print(message)


def main() -> None:
    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    server_thread = threading.Thread(target=server.run, name="squeeze-server", daemon=True)
    api = DesktopApi()
    url = f"http://127.0.0.1:{port}/?desktop=1"

    try:
        server_thread.start()
        wait_for_server(f"http://127.0.0.1:{port}/health")
        api_window = webview.create_window(
            "Squeeze — Video compressor",
            url,
            js_api=api,
            width=1280,
            height=820,
            min_size=(1000, 700),
            resizable=True,
            text_select=True,
        )
        api.window = api_window
        webview.start(gui="edgechromium", debug=False)
    except Exception as error:
        show_startup_error(str(error))
        raise
    finally:
        server.should_exit = True
        server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
