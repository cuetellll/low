import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vidshrink.engine import Job, Options, VideoInfo, build_cmd, fmt_time, human_size  # noqa: E402


def _job(h=1080):
    return Job(0, "in.mkv", "out.mp4", VideoInfo(size=1000, duration=10, width=1920, height=h))


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024 ** 3) == "5.0 GB"


def test_fmt_time():
    assert fmt_time(75) == "01:15"
    assert fmt_time(3661) == "1:01:01"


def test_h265_cpu_defaults():
    cmd = build_cmd("ffmpeg", _job(), Options(preset=1, codec=0), use_gpu=False)
    assert "libx265" in cmd and "hvc1" in cmd and "27" in cmd
    assert cmd[-1] == "out.mp4"


def test_downscale_only_when_larger():
    big = build_cmd("ffmpeg", _job(1080), Options(res=2), False)
    small = build_cmd("ffmpeg", _job(480), Options(res=2), False)
    assert any("scale=-2:720" in c for c in big)
    assert not any("scale=" in c for c in small)


def test_mute_and_gpu():
    cmd = build_cmd("ffmpeg", _job(), Options(codec=1, gpu=True, mute=True), use_gpu=True)
    assert "h264_nvenc" in cmd and "-an" in cmd and "aac" not in cmd
