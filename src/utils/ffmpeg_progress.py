"""Run ffmpeg with a readable progress bar (percentage, position, time
left) instead of its raw status lines, for the long local steps: joining
parts, re-encoding, moving the MP4 index (faststart)."""
import subprocess

from tqdm import tqdm


def media_duration(path):
    """Duration in seconds (ffprobe), or None."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", path], capture_output=True, text=True, timeout=60).stdout
        return float(out.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _fmt(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class _ClockBar(tqdm):
    """tqdm whose counter (seconds of video done) reads as 0:12:34/2:05:00."""
    @property
    def format_dict(self):
        d = super().format_dict
        d["clock"] = f"{_fmt(d['n'])}/{_fmt(d['total'])}" if d["total"] else _fmt(d["n"])
        return d


def run_ffmpeg(args, label, duration=None):
    """ffmpeg -y <args> with a progress bar labelled label; errors are
    still printed. Returns True on success."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"] + list(args)
    total = max(1, int(duration)) if duration else None
    bar = _ClockBar(total=total, desc=label, unit="s", leave=True,
                    bar_format="{desc} {percentage:3.0f}% |{bar:30}| {clock} [{elapsed}<{remaining}]"
                    if total else "{desc} {clock} [{elapsed}]")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, errors="replace")
    except OSError:
        bar.close()
        return False
    try:
        done = 0
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit():
                position = max(0, int(value) // 1_000_000)
                if total:
                    position = min(position, total)
                if position > done:
                    bar.update(position - done)
                    done = position
            elif key == "progress" and value == "end" and total and done < total:
                bar.update(total - done)
                done = total
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        raise
    finally:
        bar.close()
    return proc.returncode == 0
