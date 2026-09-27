"""Repair .mp4 files whose sound is described wrongly - no re-encoding.

Old anime .ts -> .mp4 conversions (PyAV) wrote the audio encoder's
defaults (48000 Hz stereo) in the file instead of the real sound settings
(e.g. 44100 Hz). Some players coped, but a remux (faststart) carried the
wrong description over: silence or crackles.

Such a file is recognisable from its own index: frames x 1024 samples at
the declared rate no longer add up to the track's duration. The real rate
follows from that ratio, and the real channel count from the first element
of the AAC frames, so the frames are copied as-is under a correct header.
"""
import os
import subprocess

from src.var import print_status

_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000)


def _probe_audio(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                              "stream=codec_name,sample_rate,nb_frames,duration,start_time",
                              "-of", "default=noprint_wrappers=1", path],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        return (info["codec_name"], int(info["sample_rate"]), int(info["nb_frames"]),
                float(info["duration"]), float(info.get("start_time") or 0))
    except (KeyError, ValueError):
        return None


def real_sample_rate(path):
    """The rate the sound really has when the file declares another one,
    else None (sound fine, not AAC, or can't tell)."""
    info = _probe_audio(path)
    if not info or info[0] != "aac" or info[2] <= 0 or info[3] <= 0:
        return None
    _, declared, frames, duration, _ = info
    ratio = frames * 1024 / declared / duration
    if abs(ratio - 1) < 0.03 or abs(ratio - 0.5) < 0.015:  # plain AAC / HE-AAC
        return None
    rate = frames * 1024 / duration
    best = min(_RATES, key=lambda r: abs(r - rate))
    if best == declared or abs(best - rate) / best > 0.02:
        return None
    return best


class _Bits:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n):
        value = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value


def _frame_channels(frame):
    """1 or 2 from the first audio element of a raw AAC frame, else None."""
    bits = _Bits(frame)
    try:
        for _ in range(16):
            kind = bits.read(3)
            if kind == 0:        # single channel element
                return 1
            if kind == 1:        # channel pair element
                return 2
            if kind == 6:        # fill element (encoder tag)
                count = bits.read(4)
                if count == 15:
                    count += bits.read(8) - 1
                bits.pos += 8 * count
            elif kind == 4:      # data stream element
                bits.read(4)
                align = bits.read(1)
                count = bits.read(8)
                if count == 255:
                    count += bits.read(8)
                if align:
                    bits.pos = (bits.pos + 7) & ~7
                bits.pos += 8 * count
            else:
                return None
    except IndexError:
        return None
    return None


def _adts_header(length, rate_index, channels, profile=1):
    size = length + 7
    return bytes([
        0xFF, 0xF1,
        (profile << 6) | (rate_index << 2) | (channels >> 2),
        ((channels & 3) << 6) | (size >> 11),
        (size >> 3) & 0xFF,
        ((size & 7) << 5) | 0x1F,
        0xFC,
    ])


def repair_audio(path, rate):
    """Rewrite path with its AAC frames under a correct header. True if done."""
    import av
    from src.utils.ffmpeg_progress import run_ffmpeg, media_duration

    info = _probe_audio(path)
    aac = path + ".audio.aac"
    tmp = path + ".repair.part"
    try:
        with av.open(path) as container:
            found = []
            for packet in container.demux(container.streams.audio[0]):
                data = bytes(packet)
                if data:
                    found.append(_frame_channels(data))
                if len(found) >= 50:
                    break
        found = [c for c in found if c]
        if not found:
            return False
        count = max(set(found), key=found.count)
        # Same frames, each behind an ADTS header that states the real settings.
        rate_index = _RATES.index(rate)
        with av.open(path) as container, open(aac, "wb") as out:
            for packet in container.demux(container.streams.audio[0]):
                data = bytes(packet)
                if data:
                    out.write(_adts_header(len(data), rate_index, count) + data)
        args = ["-i", path, "-itsoffset", f"{info[4] if info else 0:.6f}", "-i", aac,
                "-map", "0:v:0?", "-map", "1:a:0", "-c", "copy",
                "-movflags", "+faststart", "-f", "mp4", tmp]
        if not run_ffmpeg(args, "🔧 Réparation du son", media_duration(path)):
            return False
        if real_sample_rate(tmp) is not None or not os.path.getsize(tmp):
            return False
        os.replace(tmp, path)
        return True
    except Exception as e:
        print_status(f"Réparation du son impossible ({e})", "warning")
        return False
    finally:
        for p in (aac, tmp):
            try:
                os.remove(p)
            except OSError:
                pass
