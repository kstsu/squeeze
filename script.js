/* @ts-check */

const MAX_TARGET_MB = 4096;
const MIN_TARGET_MB = 1;
const ACCEPTED_EXTENSIONS = new Set(["mp4", "mov", "webm", "mkv", "avi", "m4v"]);

if (new URLSearchParams(window.location.search).get("desktop") === "1") {
  document.documentElement.dataset.desktop = "true";
}

const elements = {
  dropzone: document.querySelector("#dropzone"),
  dropzoneTitle: document.querySelector("#dropzone-title"),
  fileInput: document.querySelector("#video-file"),
  fileSummary: document.querySelector("#file-summary"),
  fileName: document.querySelector("#file-name"),
  fileMeta: document.querySelector("#file-meta"),
  removeFile: document.querySelector("#remove-file"),
  form: document.querySelector("#compress-form"),
  targetSize: document.querySelector("#target-size"),
  targetError: document.querySelector("#target-error"),
  outputFormat: document.querySelector("#output-format"),
  compressButton: document.querySelector("#compress-button"),
  progressArea: document.querySelector("#progress-area"),
  progressLabel: document.querySelector("#progress-label"),
  progressValue: document.querySelector("#progress-value"),
  progressFill: document.querySelector("#progress-fill"),
  progressTrack: document.querySelector(".progress-track"),
  progressDetail: document.querySelector("#progress-detail"),
  cancelJob: document.querySelector("#cancel-job"),
  message: document.querySelector("#message"),
  result: document.querySelector("#result"),
  originalSize: document.querySelector("#original-size"),
  compressedSize: document.querySelector("#compressed-size"),
  savedSize: document.querySelector("#saved-size"),
  downloadButton: document.querySelector("#download-button"),
  downloadLabel: document.querySelector("#download-label"),
  startOver: document.querySelector("#start-over"),
};

/** @type {File | null} */
let selectedFile = null;
/** @type {AbortController | null} */
let activeRequest = null;
let isCompressing = false;
let desktopMode = false;
/** @type {{job_id: string, filename: string} | null} */
let latestResult = null;
/** @type {XMLHttpRequest | null} */
let activeXhr = null;

function getExtension(fileName) {
  const segments = fileName.toLowerCase().split(".");
  return segments.length > 1 ? segments.at(-1) : "";
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "Duration unavailable";
  const wholeSeconds = Math.round(seconds);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60).toString().padStart(2, "0");
  const remainder = (wholeSeconds % 60).toString().padStart(2, "0");
  return hours > 0 ? `${hours}:${minutes}:${remainder}` : `${minutes}:${remainder}`;
}

function setMessage(message, kind = "error") {
  elements.message.textContent = message;
  elements.message.dataset.kind = kind;
  elements.message.hidden = !message;
}

function validateTargetSize() {
  const value = Number(elements.targetSize.value);
  const fileSizeMb = selectedFile ? selectedFile.size / (1024 * 1024) : null;
  let error = "";

  if (!Number.isFinite(value) || value < MIN_TARGET_MB) {
    error = "Enter a target of at least 1 MB.";
  } else if (value > MAX_TARGET_MB) {
    error = `Keep the target below ${MAX_TARGET_MB.toLocaleString()} MB.`;
  } else if (fileSizeMb !== null && value >= fileSizeMb) {
    error = "Choose a target smaller than the original file.";
  }

  elements.form.querySelector(".field-group").classList.toggle("has-error", Boolean(error));
  elements.targetError.textContent = error;
  elements.targetError.hidden = !error;
  return { valid: !error, value };
}

function updateSubmitState() {
  const target = validateTargetSize();
  elements.compressButton.disabled = !selectedFile || !target.valid || isCompressing;
}

function getVideoMetadata(file) {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    const objectUrl = URL.createObjectURL(file);
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(Number.isFinite(video.duration) ? video.duration : 0);
    };
    video.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(0);
    };
    video.src = objectUrl;
  });
}

async function updateFileSummary(file) {
  selectedFile = file;
  const extension = getExtension(file.name).toUpperCase() || "VIDEO";
  const fileType = elements.fileSummary.querySelector(".file-type");
  fileType.textContent = extension;
  elements.fileName.textContent = file.name;
  elements.fileMeta.textContent = `${formatBytes(file.size)} · Reading duration…`;
  elements.fileSummary.hidden = false;
  elements.dropzone.hidden = true;
  elements.result.hidden = true;
  elements.progressArea.hidden = true;
  setMessage("");
  updateSubmitState();

  const duration = await getVideoMetadata(file);
  if (selectedFile !== file) return;
  elements.fileMeta.textContent = `${formatBytes(file.size)} · ${formatDuration(duration)}`;
}

function clearFile() {
  activeRequest?.abort();
  activeRequest = null;
  selectedFile = null;
  isCompressing = false;
  elements.fileInput.value = "";
  elements.fileSummary.hidden = true;
  elements.dropzone.hidden = false;
  elements.dropzoneTitle.textContent = "Drop your video here";
  elements.progressArea.hidden = true;
  elements.result.hidden = true;
  setMessage("");
  updateSubmitState();
}

function setProgress(percent, label, detail) {
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
  elements.progressLabel.textContent = label;
  elements.progressValue.textContent = `${safePercent}%`;
  elements.progressFill.style.width = `${safePercent}%`;
  elements.progressTrack.setAttribute("aria-valuenow", String(safePercent));
  elements.progressDetail.textContent = detail;
}

function validateFile(file) {
  if (!file) return "Choose a video file to continue.";
  if (!ACCEPTED_EXTENSIONS.has(getExtension(file.name))) {
    return "Use an MP4, MOV, WEBM, MKV, AVI, or M4V video file.";
  }
  if (file.size === 0) return "That file is empty. Choose another video.";
  return "";
}

async function readError(response) {
  try {
    const payload = await response.json();
    return typeof payload.detail === "string" ? payload.detail : "The server could not compress this video.";
  } catch {
    return "The server could not compress this video. Check that FFmpeg is installed and try again.";
  }
}

function handleProgressPayload(payload) {
  if (payload.type === "progress") {
    setProgress(payload.percent, payload.label, payload.detail);
  } else if (payload.type === "complete") {
    showResult(payload);
  } else if (payload.type === "error") {
    throw new Error(payload.detail);
  }
}

function consumeProgressText(text, state, isFinal = false) {
  const newText = text.slice(state.readOffset);
  state.readOffset = text.length;
  state.buffer += newText;
  const events = state.buffer.split("\n\n");
  state.buffer = isFinal ? "" : (events.pop() ?? "");
  for (const event of events) {
    const dataLine = event.split("\n").find((line) => line.startsWith("data:"));
    if (!dataLine) continue;
    handleProgressPayload(JSON.parse(dataLine.slice(5).trim()));
  }
}

function compressWithProgress(formData) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const state = { buffer: "", readOffset: 0, completed: false };
    activeXhr = xhr;
    xhr.open("POST", "/api/compress");
    xhr.responseType = "text";

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const uploadPercent = Math.max(1, Math.min(5, Math.round((event.loaded / event.total) * 4) + 1));
      setProgress(uploadPercent, "Uploading video", `${formatBytes(event.loaded)} of ${formatBytes(event.total)} sent to the local compressor.`);
    };

    xhr.onprogress = () => {
      try {
        consumeProgressText(xhr.responseText, state);
      } catch (error) {
        xhr.abort();
        reject(error);
      }
    };

    xhr.onload = () => {
      try {
        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = "The server could not compress this video.";
          try {
            const payload = JSON.parse(xhr.responseText);
            if (typeof payload.detail === "string") detail = payload.detail;
          } catch {
            detail = "The server could not compress this video. Check the local service and try again.";
          }
          throw new Error(detail);
        }
        consumeProgressText(xhr.responseText, state, true);
        if (!state.completed && xhr.responseText.includes('"type": "complete"')) state.completed = true;
        resolve();
      } catch (error) {
        reject(error);
      }
    };

    xhr.onerror = () => reject(new Error("The local compressor could not be reached."));
    xhr.ontimeout = () => reject(new Error("The compression request timed out. Try a shorter video."));
    xhr.onabort = () => reject(new DOMException("Compression cancelled.", "AbortError"));

    if (activeRequest) {
      activeRequest.signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.send(formData);
  }).finally(() => {
    activeXhr = null;
  });
}

/*
 * Kept separate from the upload transport so the same SSE payloads work in
 * both browser and desktop WebView environments.
 */
function consumeProgress(response) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("The server returned no progress stream.");
  const decoder = new TextDecoder();
  let buffer = "";

  return (async () => {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? "";
      for (const event of events) {
        const dataLine = event.split("\n").find((line) => line.startsWith("data:"));
        if (!dataLine) continue;
        const payload = JSON.parse(dataLine.slice(5).trim());
        handleProgressPayload(payload);
      }
    }
  })();
}

async function compressVideo() {
  if (!selectedFile || isCompressing) return;
  const target = validateTargetSize();
  if (!target.valid) {
    elements.targetSize.focus();
    return;
  }

  isCompressing = true;
  activeRequest = new AbortController();
  updateSubmitState();
  elements.progressArea.hidden = false;
  elements.result.hidden = true;
  setMessage("");
  setProgress(1, "Uploading video", "Sending the file to the local compressor.");

  const formData = new FormData();
  formData.append("video", selectedFile, selectedFile.name);
  formData.append("target_mb", String(target.value));
  formData.append("output_format", elements.outputFormat.value);

  try {
    await compressWithProgress(formData);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      setMessage("Compression cancelled.", "info");
    } else {
      const message = error instanceof Error ? error.message : "Compression failed. Try another video.";
      setMessage(message);
      elements.progressArea.hidden = true;
    }
  } finally {
    activeRequest = null;
    activeXhr = null;
    isCompressing = false;
    updateSubmitState();
  }
}

function showResult(payload) {
  latestResult = { job_id: payload.job_id, filename: payload.filename };
  elements.originalSize.textContent = formatBytes(payload.original_bytes);
  elements.compressedSize.textContent = formatBytes(payload.compressed_bytes);
  elements.savedSize.textContent = `${formatBytes(payload.saved_bytes)} saved`;
  elements.downloadButton.href = payload.download_url;
  elements.downloadButton.download = payload.filename;
  elements.downloadLabel.textContent = desktopMode ? `Save ${payload.filename.split(".").at(-1).toUpperCase()}` : `Download ${payload.filename.split(".").at(-1).toUpperCase()}`;
  const format = payload.filename.split(".").at(-1).toUpperCase();
  setProgress(100, "Compression complete", `The compressed ${format} file is ready to download.`);
  elements.result.hidden = false;
  elements.progressArea.hidden = false;
  elements.result.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function saveDesktopResult(event) {
  if (!desktopMode || !latestResult || !window.pywebview?.api?.save_compressed_file) return;
  event.preventDefault();
  const result = await window.pywebview.api.save_compressed_file(latestResult.job_id, latestResult.filename);
  if (result.ok) {
    setMessage(`Saved to ${result.path}`, "info");
  } else if (!result.cancelled) {
    setMessage(result.message || "Squeeze could not save the MP4.");
  }
}

function handleFile(file) {
  const error = validateFile(file);
  if (error) {
    setMessage(error);
    return;
  }
  void updateFileSummary(file);
}

elements.fileInput.addEventListener("change", () => handleFile(elements.fileInput.files?.[0] ?? null));
elements.removeFile.addEventListener("click", clearFile);
elements.startOver.addEventListener("click", clearFile);
elements.cancelJob.addEventListener("click", () => activeRequest?.abort());
elements.downloadButton.addEventListener("click", (event) => {
  void saveDesktopResult(event);
});
elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  void compressVideo();
});
elements.targetSize.addEventListener("input", updateSubmitState);

document.querySelectorAll("[data-size]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.targetSize.value = button.dataset.size ?? "20";
    document.querySelectorAll("[data-size]").forEach((item) => item.classList.toggle("is-selected", item === button));
    updateSubmitState();
  });
});

elements.dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    elements.fileInput.click();
  }
});

["dragenter", "dragover"].forEach((eventName) => {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.add("is-dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.remove("is-dragging");
  });
});

elements.dropzone.addEventListener("drop", (event) => {
  handleFile(event.dataTransfer?.files?.[0] ?? null);
});

window.addEventListener("pagehide", () => activeRequest?.abort());

window.addEventListener("pywebviewready", () => {
  desktopMode = true;
  document.documentElement.dataset.desktop = "true";
  elements.downloadLabel.textContent = "Save file";
});

updateSubmitState();
