#!/usr/bin/env python3
"""
Hybrid RTSP check via OpenCV + GStreamer + FFprobe.
"""

import os
import re
import sys
import time
import json
import signal
import argparse
import subprocess
import contextlib
from datetime import datetime, timezone

os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"
os.environ["OPENCV_LOG_LEVEL"] = "SILENT"

import cv2
import numpy as np

LOGFILE = "/var/log/check_rtsp.log"
FFMPEG_LOGFILE = "/var/log/check_rtsp_ffmpeg.log"
MAX_LOG_SIZE_MB = 15

STATUS_MAP = {
    "ok": 1,
    "no_frames": 2,
    "error": 0,
}


class CaptureTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise CaptureTimeout("operation timed out")


@contextlib.contextmanager
def time_limit(seconds):
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def log(msg):
    if os.path.exists(LOGFILE) and os.path.getsize(LOGFILE) > MAX_LOG_SIZE_MB * 1024 * 1024:
        with open(LOGFILE, "w") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] Log rotated due to size\n")
    with open(LOGFILE, "a") as f:
        f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")


def mask_url(url: str) -> str:
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url)


@contextlib.contextmanager
def suppress_stderr(log_path=FFMPEG_LOGFILE):
    try:
        log_file = open(log_path, "a")
    except Exception:
        log_file = open(os.devnull, "w")
    old_stderr_fd = os.dup(2)
    os.dup2(log_file.fileno(), 2)
    try:
        yield
    finally:
        os.dup2(old_stderr_fd, 2)
        os.close(old_stderr_fd)
        try:
            log_file.close()
        except Exception:
            pass


def parse_args():
    p = argparse.ArgumentParser(description="Hybrid RTSP check")
    p.add_argument("login")
    p.add_argument("password")
    p.add_argument("ip")
    p.add_argument("port")
    p.add_argument("path")
    p.add_argument("--timeout", "-t", type=int, default=5)
    p.add_argument("--capture", "-c", type=float, default=3.0)
    return p.parse_args()


def capture_rtsp(cap, duration=3.0):
    log(f"capture_rtsp: starting capture for {duration}s")
    start_time = time.time()
    deadline = start_time + duration
    frames = 0
    sizes = []
    brightness = []
    change_levels = []
    prev_gray = None
    width = height = 0

    while time.time() < deadline:
        ret, frame = cap.read()
        if not ret or frame is None:
            continue
        frames += 1
        sizes.append(frame.nbytes)
        height, width = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness.append(np.mean(gray))
        if prev_gray is not None:
            delta = np.mean(np.abs(gray.astype("int16") - prev_gray.astype("int16")))
            change_levels.append(delta)
        prev_gray = gray

    cap.release()
    log(f"capture_rtsp: captured {frames} frames")

    if frames == 0:
        note = "Connected, but no valid frames"
        log(f"capture_rtsp: {note}")
        return "no_frames", 0, 0.0, (0, 0), 0.0, 0.0, 0.0, note

    avg_kb = round(sum(sizes) / len(sizes) / 1024, 2)
    avg_brightness = round(float(np.mean(brightness)), 2)
    change_level = round(float(np.mean(change_levels)), 2) if change_levels else 0.0
    fps = round(frames / duration, 2)

    log(f"capture_rtsp: avg_kb={avg_kb}, brightness={avg_brightness}, change={change_level}, fps={fps}, size={width}x{height}")
    return "ok", frames, avg_kb, (width, height), avg_brightness, change_level, fps, ""


def try_opencv_backends(url, timeout, capture_duration, use_gstreamer=False):
    backend_name = "OpenCV"
    if use_gstreamer:
        backend_name = "GStreamer"
        log(f"{backend_name}: trying to open stream")
        url = f"rtspsrc location={url} latency=0 ! decodebin ! videoconvert ! appsink"
        try:
            with time_limit(timeout):
                cap = cv2.VideoCapture(url, cv2.CAP_GSTREAMER)
        except CaptureTimeout:
            msg = f"{backend_name} open timed out after {timeout}s"
            log(f"{backend_name}: {msg}")
            return "error", 0, 0.0, (0, 0), 0.0, 0.0, 0.0, msg
    else:
        log(f"{backend_name}: trying to open stream")
        try:
            with time_limit(timeout):
                cap = cv2.VideoCapture(url)
        except CaptureTimeout:
            msg = f"{backend_name} open timed out after {timeout}s"
            log(f"{backend_name}: {msg}")
            return "error", 0, 0.0, (0, 0), 0.0, 0.0, 0.0, msg

    if not cap.isOpened():
        msg = f"{backend_name} failed to open stream"
        log(f"{backend_name}: {msg}")
        cap.release()
        return "error", 0, 0.0, (0, 0), 0.0, 0.0, 0.0, msg

    log(f"{backend_name}: stream opened, starting capture")
    try:
        with time_limit(capture_duration + 5):
            return capture_rtsp(cap, duration=capture_duration)
    except CaptureTimeout:
        msg = f"{backend_name} capture timed out after {capture_duration + 5}s"
        log(f"{backend_name}: {msg}")
        cap.release()
        return "error", 0, 0.0, (0, 0), 0.0, 0.0, 0.0, msg


def fallback_ffprobe(url, timeout):
    log("FFprobe: trying to get stream metadata")
    cmd = [
        "ffprobe", "-v", "error",
        "-rtsp_transport", "tcp",
        "-timeout", str(int(timeout * 1e6)),
        "-i", url,
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name",
        "-of", "json"
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        streams = json.loads(p.stdout).get("streams") or []
        if not streams:
            log("FFprobe: no streams found")
            return None
        info = streams[0]
        result = {"width": info.get("width") or 0, "height": info.get("height") or 0}
        log(f"FFprobe: got metadata {result}")
        return result
    except Exception as e:
        log(f"FFprobe fallback failed: {e}")
        return None


def main():
    args = parse_args()
    base_url = f"rtsp://{args.login}:{args.password}@{args.ip}:{args.port}/{args.path}"
    log(f"=== Check started: {mask_url(base_url)} ===")
    log(f"Parameters: timeout={args.timeout}s, capture={args.capture}s")

    with suppress_stderr():
        latency_start = time.time()
        status, frames, avg_kb, res, brightness, change_level, fps, note = \
            try_opencv_backends(base_url, args.timeout, args.capture)
        latency_ms = int((time.time() - latency_start) * 1000)
        log(f"OpenCV result: status={status}, latency={latency_ms}ms, note='{note}'")

        if status == "error":
            log("OpenCV failed, trying GStreamer fallback...")
            latency_start = time.time()
            status, frames, avg_kb, res, brightness, change_level, fps, note = \
                try_opencv_backends(base_url, args.timeout, args.capture, use_gstreamer=True)
            latency_ms = int((time.time() - latency_start) * 1000)
            log(f"GStreamer result: status={status}, latency={latency_ms}ms, note='{note}'")

    width_val = res[0] if res else 0
    height_val = res[1] if res else 0
    resolution_pixels = width_val * height_val

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status_code": STATUS_MAP.get(status, 0),
        "is_available": 1 if status == "ok" else 0,
        "latency_ms": latency_ms,
        "frames_read": frames,
        "avg_frame_size_kb": avg_kb if avg_kb is not None else 0,
        "width": width_val,
        "height": height_val,
        "resolution_pixels": resolution_pixels,
        "avg_brightness": brightness,
        "frame_change_level": change_level,
        "real_fps": fps,
        "note": note or "",
    }

    if status == "error":
        log("All OpenCV backends failed, trying ffprobe fallback...")
        meta = fallback_ffprobe(base_url, args.timeout)
        if meta:
            result["status_code"] = STATUS_MAP["no_frames"]
            result["is_available"] = 0
            result["width"] = meta.get("width") or 0
            result["height"] = meta.get("height") or 0
            result["resolution_pixels"] = result["width"] * result["height"]
            suffix = "Metadata via ffprobe"
            result["note"] = f"{result['note']} | {suffix}" if result["note"] else suffix
            log(f"FFprobe metadata applied: {meta}")

    log(f"=== Final result: {result} ===")
    print(json.dumps(result))
    sys.exit(1 if result["status_code"] == 0 else 0)


if __name__ == "__main__":
    main()
