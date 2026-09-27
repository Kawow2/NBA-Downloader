import os
import av

from src.var import print_status

# fix_ts() used to print nothing at all while remuxing/encoding - which
# left a long silent stretch after all .ts downloads finished. A live tqdm
# bar was tried here too, but mixing bars created at different times across
# threads (download bars finishing/freeing their position while a
# conversion bar claims it mid-batch, with plain print_status lines
# interleaved in between) broke tqdm's row bookkeeping and produced
# garbled, overlapping output. Kept simple instead: one line when a
# conversion starts, one when it finishes - no live in-between updates.


def sanitize_ts_file(filepath):

    if not os.path.exists(filepath):
        return False

    file_size = os.path.getsize(filepath)
    if file_size < 188:
        return False

    try:
        with open(filepath, "rb") as f:
            head = f.read(min(file_size, 524288))

        if not head:
            return False

        if head[0] == 0x47 and len(head) >= 377 and head[188] == 0x47 and head[376] == 0x47:
            return False

        sync_offset = -1
        for i in range(min(len(head) - 376, 262144)):
            if head[i] == 0x47 and head[i + 188] == 0x47 and head[i + 376] == 0x47:
                sync_offset = i
                break

        if sync_offset > 0:
            with open(filepath, "rb") as f_in:
                f_in.seek(sync_offset)
                clean_data = f_in.read()
            with open(filepath, "wb") as f_out:
                f_out.write(clean_data)
            return True
    except Exception:
        pass

    return False


# Index at the start of the file (Plex starts playing at once).
_MP4_OPTIONS = {"movflags": "+faststart"}


def _copy_stream(output_container, in_stream, codec_name):
    """Output stream carrying the input's codec parameters (sample rate,
    channels, AAC config...). A bare add_stream(codec_name) gets the
    encoder defaults instead (48000 Hz stereo): the .mp4 then describes
    the sound wrongly, and players - or any later remux - give silence or
    crackles."""
    try:
        out = output_container.add_stream_from_template(in_stream)  # PyAV >= 14
    except AttributeError:
        try:
            out = output_container.add_stream(template=in_stream)
        except Exception:
            out = output_container.add_stream(
                getattr(getattr(in_stream, "codec_context", None), "name", None) or codec_name)
    if getattr(in_stream, "time_base", None):
        out.time_base = in_stream.time_base
    return out


def _start_shift(*streams):
    """{stream index: ticks to subtract} so the earliest stream starts at 0
    and the others keep their offset to it (audio/video sync)."""
    starts = [(st, st.start_time * st.time_base) for st in streams
              if st is not None and st.start_time is not None and st.time_base]
    if not starts:
        return {}
    base = min(start for _, start in starts)
    return {st.index: int(round(base / st.time_base)) for st, _ in starts}


def fix_ts(infile, outfile):
    label = os.path.basename(outfile)
    print_status(f"Converting to mp4: {label}", "loading")
    return _fix_ts_impl(infile, outfile)


def _fix_ts_impl(infile, outfile):
    sanitize_ts_file(infile)

    open_options = {
        "probesize": "10000000",
        "analyzeduration": "10000000",
        "err_detect": "ignore_err"
    }

    if os.path.exists(outfile):
        try:
            os.remove(outfile)
        except Exception:
            pass

    try:
        input_container = av.open(infile, mode="r", options=open_options)
    except Exception:
        try:
            input_container = av.open(infile, mode="r", format="mpegts", options=open_options)
        except Exception as final_err:
            raise ValueError(f"Failed to open input file {infile}: {final_err}")

    output_container = av.open(outfile, mode="w", options=_MP4_OPTIONS)

    in_v = input_container.streams.video[0] if input_container.streams.video else None
    in_a = input_container.streams.audio[0] if input_container.streams.audio else None

    out_v = _copy_stream(output_container, in_v, "h264") if in_v else None
    out_a = _copy_stream(output_container, in_a, "aac") if in_a else None

    bsf_v = None
    if in_v:
        try:
            bsf_v = av.BitStreamFilterContext("h264_mp4toannexb", in_v)
        except Exception:
            bsf_v = None

    v_count = 0
    a_count = 0
    last_dts = {}
    shift = _start_shift(in_v, in_a)

    for packet in input_container.demux():
        stype = str(getattr(packet.stream, "type", "")).lower()
        st_idx = packet.stream.index

        if "data" in stype:
            continue

        # Start at 0 like ffmpeg does: HLS streams begin at an arbitrary
        # time (10 s, 1 h...), which some players, Plex included, turn into
        # silence/black or a shifted sound at the start of the episode.
        offset = shift.get(st_idx, 0)
        if offset:
            if packet.pts is not None:
                packet.pts -= offset
            if packet.dts is not None:
                packet.dts -= offset

        if packet.dts is not None:
            if st_idx in last_dts and last_dts[st_idx] != -1 and packet.dts <= last_dts[st_idx]:
                packet.dts = last_dts[st_idx] + 1
                if packet.pts is not None and packet.pts < packet.dts:
                    packet.pts = packet.dts
            last_dts[st_idx] = packet.dts

        if "video" in stype and out_v:
            packets_to_mux = []
            if bsf_v:
                try:
                    packets_to_mux = list(bsf_v.filter(packet))
                except Exception:
                    packets_to_mux = [packet]
            else:
                packets_to_mux = [packet]

            for fp in packets_to_mux:
                fp.stream = out_v
                try:
                    output_container.mux(fp)
                    v_count += 1
                except Exception:
                    pass
        elif "audio" in stype and out_a:
            packet.stream = out_a
            try:
                output_container.mux(packet)
                a_count += 1
            except Exception:
                pass

    output_container.close()
    input_container.close()

    out_size = os.path.getsize(outfile) if os.path.exists(outfile) else 0

    if v_count == 0 or out_size < 10240:
        if os.path.exists(outfile):
            try:
                os.remove(outfile)
            except Exception:
                pass

        try:
            input_container = av.open(infile, mode="r", options=open_options)
        except Exception:
            input_container = av.open(infile, mode="r", format="mpegts", options=open_options)

        output_container = av.open(outfile, mode="w", options=_MP4_OPTIONS)

        in_v = input_container.streams.video[0] if input_container.streams.video else None
        in_a = input_container.streams.audio[0] if input_container.streams.audio else None

        out_v = None
        if in_v:
            rate = getattr(in_v, "average_rate", None) or getattr(in_v, "rate", 24)
            out_v = output_container.add_stream("h264", rate=rate, options={"preset": "ultrafast", "crf": "23"})
            out_v.width = getattr(in_v, "width", 1280) or 1280
            out_v.height = getattr(in_v, "height", 720) or 720
            out_v.pix_fmt = "yuv420p"

        out_a = None
        if in_a:
            rate = getattr(in_a, "rate", 48000) or 48000
            out_a = output_container.add_stream("aac", rate=rate)
            try:
                out_a.layout = getattr(in_a, "layout", "stereo") or "stereo"
            except Exception:
                out_a.layout = "stereo"

        for packet in input_container.demux():
            if in_v and packet.stream == in_v and out_v:
                try:
                    for frame in packet.decode():
                        for out_pkt in out_v.encode(frame):
                            output_container.mux(out_pkt)
                except Exception:
                    pass
            elif in_a and packet.stream == in_a and out_a:
                try:
                    for frame in packet.decode():
                        for out_pkt in out_a.encode(frame):
                            output_container.mux(out_pkt)
                except Exception:
                    pass

        if out_v:
            try:
                for out_pkt in out_v.encode():
                    output_container.mux(out_pkt)
            except Exception:
                pass

        if out_a:
            try:
                for out_pkt in out_a.encode():
                    output_container.mux(out_pkt)
            except Exception:
                pass

        output_container.close()
        input_container.close()

    out_size = os.path.getsize(outfile) if os.path.exists(outfile) else 0
    if out_size < 10240:
        if os.path.exists(outfile):
            try:
                os.remove(outfile)
            except Exception:
                pass
        raise ValueError(f"PyAV conversion produced empty/invalid file ({out_size} bytes).")

    return True
