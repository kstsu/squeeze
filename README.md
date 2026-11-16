# Squeeze video compressor

Squeeze is a local-first desktop video compressor. It opens a native desktop window, starts a private FastAPI service on an ephemeral localhost port, and uses the native FFmpeg binary on the same computer to create a target-size MP4.

## Why this architecture

Browser-only FFmpeg WebAssembly is useful for small demos, but large video jobs are limited by browser memory and worker loading. This project uses native FFmpeg through a Python process instead:

- video data is sent only to the local service inside the desktop app;
- FFmpeg runs outside the browser, using the computer's native encoder;
- progress is streamed back over Server-Sent Events;
- the finished MP4 is available as a temporary download;
- temporary upload and output files are removed after the stream ends.

## Requirements

- Python 3.12 or newer
- FFmpeg available through the project dependency or on `PATH`
- Windows 10/11 with Microsoft Edge WebView2 Runtime for the native window

Check the optional system installation:

```powershell
python --version
```

The application exposes its own readiness check at `/health` after startup.

The pinned `imageio-ffmpeg` dependency includes an FFmpeg binary for common platforms, so a separate system install is not required. If you prefer a system build, install FFmpeg with one of these options:

```powershell
winget install Gyan.FFmpeg.Shared
```

Or download a current build from [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/), extract it, and add its `bin` folder to `PATH`. Restart PowerShell after changing `PATH`.

Windows 10 and Windows 11 normally include the WebView2 Runtime. If Squeeze reports that its native window cannot start, install the Evergreen WebView2 Runtime from [Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/).

## Run as a desktop app during development

Run the desktop launcher from PowerShell:

```powershell
.\run-desktop.ps1
```

The launcher creates `.venv` once, installs the pinned dependencies, starts the local service on an available port, and opens the native Squeeze window at a comfortable `1280 × 820` starting resolution. The window can still be resized down to `1000 × 700`. Close the Squeeze window to stop the service.

The browser-only development server is still available when you want to inspect the HTML directly:

```powershell
.\start.ps1
```

Do not run `python -m venv .venv` while the environment is active. Recreating an active virtual environment on Windows can fail because `.venv\Scripts\python.exe` is in use.

## Build the Windows desktop app

The default build uses a portable folder because it opens much faster than a single-file executable. A single-file build must unpack the entire application and FFmpeg into a temporary directory every time it starts.

```powershell
.\build.ps1
```

The result is created at:

```text
release\Squeeze\Squeeze.exe
```

Users open `release\Squeeze\Squeeze.exe`. They do not need to run Python commands or install FFmpeg separately. Keep the entire `release\Squeeze` folder together because `_internal` contains the application runtime. In desktop mode, the result action opens a native Save dialog instead of downloading through a browser.

The build also creates a GitHub-ready archive:

```text
release\Squeeze-windows.zip
```

Upload that ZIP as a GitHub Release asset. Users extract it and run `Squeeze.exe`. The portable folder starts faster because Windows does not need to unpack the application on every launch.

## Browser-only server

For development or a future hosted deployment, the API can still run in a browser:

```powershell
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Desktop mode uses an ephemeral port and does not depend on this fixed port.

## Compression behavior

The target size is treated as an approximate upper bound. The server reserves space for the MP4 container and audio stream, calculates a video bitrate from the source duration, and encodes H.264 video with AAC audio. A very short video or an extremely small target may not be able to reach the requested size without making the result impractically low quality.

Output files preserve the uploaded filename and add the `-sqzd` watermark before the `.mp4` extension. For example, `holiday clip.mov` becomes `holiday clip-sqzd.mp4`.

The output format can be selected independently: MP4 is recommended for general sharing, MOV is suited to Apple and editing workflows, and WebM is suited to web and browser playback. The selected format is used for encoding, the filename extension, and the desktop Save dialog.

## Project structure

```text
app.py            FastAPI server, validation, FFmpeg runner, downloads
index.html        Accessible interface
styles.css        Tokenized responsive styling
script.js         Upload flow, progress stream, result state
favicon.svg       Small local brand mark
squeeze.ico       Windows executable icon generated from the Squeeze mark
requirements.txt  Pinned Python dependencies
pyproject.toml    Project metadata and lint/type-check settings
tests/test_app.py Pure validation and bitrate tests
start.ps1         Safe Windows setup and server launcher
desktop_app.py    Native desktop shell and Save dialog bridge
run-desktop.ps1   Development desktop launcher
build.ps1         Single-file PyInstaller Windows build script
```

## Safety and privacy notes

- Uploads are generated with random job IDs and stored in the system temporary directory.
- User filenames are reduced to a safe basename and are never used as a filesystem path.
- FFmpeg is executed with an argument list, never through a shell.
- No URL import, remote file fetch, account system, analytics, or persistent storage is included.
- A download is temporary and is removed when the compression stream closes. Do not use this unmodified as a public multi-user service without authentication, rate limiting, quotas, a background job store, and durable download authorization.

## Checks

```powershell
python -m py_compile app.py
python -m unittest discover -s tests -v
python -m pip install ruff pyright
ruff check app.py
pyright app.py
```
