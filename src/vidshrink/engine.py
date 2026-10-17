"""FFmpeg engine: locating ffmpeg, probing videos and compressing them."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv",
    ".ts", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".ogv", ".vob",
}

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_BELOW_NORMAL = 0x00004000 if os.name == "nt" else 0   # keep the UI & the PC responsive while encoding


# --------------------------------------------------------------------------- helpers
def human_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_time(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def find_ffmpeg() -> Optional[str]:
    """Priority: next to the exe -> bundled (PyInstaller) -> imageio-ffmpeg -> PATH."""
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / name if hasattr(sys, "_MEIPASS") else None,
        Path(sys.executable).resolve().parent / name,
        Path(__file__).resolve().parent / name,
        Path.cwd() / name,
    ]
    for c in candidates:
        if c and c.exists():
            return str(c)
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


# --------------------------------------------------------------------------- probing
@dataclass
class VideoInfo:
    size: int = 0
    duration: float = 0.0
    width: int = 0
    height: int = 0
    codec: str = ""


_RE_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_RE_VID = re.compile(r"Video:\s*([\w]+).*?,\s*(\d{2,5})x(\d{2,5})")


def probe(ffmpeg: str, path: str) -> Optional[VideoInfo]:
    """Returns VideoInfo or None if the file has no decodable video stream."""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=_NO_WINDOW, timeout=25,
        )
    except Exception:
        return None
    err = proc.stderr or ""
    v = _RE_VID.search(err)
    if not v:
        return None
    info = VideoInfo(size=os.path.getsize(path))
    info.codec, info.width, info.height = v.group(1), int(v.group(2)), int(v.group(3))
    d = _RE_DUR.search(err)
    if d:
        info.duration = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    return info


# --------------------------------------------------------------------------- options
RES_TARGETS = [None, 1080, 720, 480]

# (cpu crf h265, cpu crf h264, nvenc cq h265, nvenc cq h264, audio kbps, cpu preset h265, preset h264)
PRESETS = [
    dict(crf265=30, crf264=26, cq265=34, cq264=30, abr=64, p265="medium", p264="slow"),     # Tiny
    dict(crf265=27, crf264=23, cq265=30, cq264=26, abr=96, p265="fast", p264="medium"),      # Balanced
    dict(crf265=23, crf264=19, cq265=26, cq264=22, abr=128, p265="fast", p264="medium"),     # Quality
]


@dataclass
class Options:
    preset: int = 1          # index in PRESETS
    codec: int = 0           # 0 = H.265, 1 = H.264
    res: int = 0             # index in RES_TARGETS
    gpu: bool = False
    mute: bool = False


@dataclass
class Job:
    jid: int
    src: str
    dst: str
    info: VideoInfo


def build_cmd(ffmpeg: str, job: Job, o: Options, use_gpu: bool) -> list[str]:
    P = PRESETS[o.preset]
    h265 = o.codec == 0
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-i", job.src, "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn"]

    vf = []
    target = RES_TARGETS[o.res]
    if target and job.info.height > target:
        vf.append(f"scale=-2:{target},setsar=1")
    vf.append("format=yuv420p")
    cmd += ["-vf", ",".join(vf)]

    if use_gpu:
        enc = "hevc_nvenc" if h265 else "h264_nvenc"
        cq = P["cq265"] if h265 else P["cq264"]
        cmd += ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
                "-spatial-aq", "1"]
    else:
        if h265:
            cmd += ["-c:v", "libx265", "-preset", P["p265"], "-crf", str(P["crf265"]),
                    "-x265-params", "log-level=error"]
        else:
            cmd += ["-c:v", "libx264", "-preset", P["p264"], "-crf", str(P["crf264"])]
    if h265:
        cmd += ["-tag:v", "hvc1"]  # makes Apple devices happy

    if o.mute:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", f"{P['abr']}k"]
        if o.preset == 0:
            cmd += ["-ac", "2"]

    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", job.dst]
    return cmd


# --------------------------------------------------------------------------- worker
class Worker(QThread):
    started_file = Signal(int)
    progress = Signal(int, float, float, float)   # jid, fraction, speed, eta seconds
    finished_file = Signal(int, str, int, int)    # jid, out_path ("" = kept original), in, out
    failed_file = Signal(int, str)
    gpu_fallback = Signal(int)
    all_done = Signal(bool)                       # cancelled?

    def __init__(self, ffmpeg: str, jobs: list[Job], opts: Options, parent=None):
        super().__init__(parent)
        self.ffmpeg, self.jobs, self.opts = ffmpeg, jobs, opts
        self._cancel = False
        self._proc: Optional[subprocess.Popen] = None

    def cancel(self):
        self._cancel = True
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    # ---- main loop
    def run(self):
        for job in self.jobs:
            if self._cancel:
                break
            self.started_file.emit(job.jid)
            ok, msg = self._encode(job, self.opts.gpu)
            if not ok and self.opts.gpu and not self._cancel:
                self.gpu_fallback.emit(job.jid)
                ok, msg = self._encode(job, False)
            if self._cancel:
                self._cleanup(job.dst)
                break
            if not ok:
                self._cleanup(job.dst)
                self.failed_file.emit(job.jid, msg)
                continue
            in_size = job.info.size
            out_size = os.path.getsize(job.dst) if os.path.exists(job.dst) else 0
            if out_size <= 0:
                self.failed_file.emit(job.jid, "empty output")
            elif out_size >= in_size * 0.98:      # no real gain -> keep the original only
                self._cleanup(job.dst)
                self.finished_file.emit(job.jid, "", in_size, in_size)
            else:
                self.finished_file.emit(job.jid, job.dst, in_size, out_size)
        self.all_done.emit(self._cancel)

    def _encode(self, job: Job, use_gpu: bool) -> tuple[bool, str]:
        cmd = build_cmd(self.ffmpeg, job, self.opts, use_gpu)
        tail: deque[str] = deque(maxlen=8)
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=_NO_WINDOW | _BELOW_NORMAL,
            )
        except Exception as e:  # ffmpeg missing, permissions...
            return False, str(e)

        dur = max(job.info.duration, 0.001)
        t0 = time.monotonic()
        speed = 0.0
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if "=" not in line:
                if line:
                    tail.append(line)
                continue
            key, _, val = line.partition("=")
            if key in ("out_time_us", "out_time_ms"):
                try:
                    cur = int(val) / 1_000_000
                except ValueError:
                    continue
                if cur < 0:
                    continue
                frac = min(max(cur / dur, 0.0), 1.0)
                elapsed = max(time.monotonic() - t0, 1e-3)
                speed = cur / elapsed
                eta = (dur - cur) / speed if speed > 0.01 else 0.0
                self.progress.emit(job.jid, frac, speed, eta)
            elif key == "progress":
                continue
            elif key in ("speed", "fps", "bitrate", "total_size", "frame", "stream_0_0_q",
                         "drop_frames", "dup_frames", "out_time", "elapsed"):
                continue
            else:
                tail.append(line)
        rc = self._proc.wait()
        self._proc = None
        if rc != 0:
            return False, (tail[-1] if tail else f"ffmpeg exited with code {rc}")
        return True, ""

    @staticmethod
    def _cleanup(path: str):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
