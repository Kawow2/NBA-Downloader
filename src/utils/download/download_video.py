import os
import re
import requests
import threading
from urllib.parse import urlparse
import time
from tqdm import tqdm
from concurrent.futures                 import ThreadPoolExecutor, as_completed

from src.var                            import Colors, print_status, DEFAULT_USER_AGENT
from src.utils.parse.parse_ts_segments  import parse_ts_segments
from src.utils.tqdm_position             import TqdmPosition as _TqdmPosition
from src.utils.download                 import parallel_settings

# In batch mode (several episodes downloading in parallel), printing
# anything per-episode - even one line per completion - while OTHER
# episodes' download bars are still actively redrawing corrupts the
# terminal display (the bars' cursor-position math gets thrown off by
# unrelated output landing mid-redraw, and the bars themselves fight over
# position at high concurrency regardless). So in batch mode nothing is
# printed per episode at all, live bars are disabled too, and completion
# is tracked silently - the caller prints one summary line once the whole
# batch (every bar/episode) is done, via get_batch_progress().
_batch_lock = threading.Lock()
_batch_total = 0
_batch_done = 0


def set_batch_size(total):
    """Call once before starting a batch of parallel downloads."""
    global _batch_total, _batch_done
    with _batch_lock:
        _batch_total = total
        _batch_done = 0


def get_batch_progress():
    with _batch_lock:
        return _batch_done, _batch_total


def _report_episode_done(label):
    global _batch_done
    with _batch_lock:
        _batch_done += 1
        total = _batch_total
    if not total:
        print_status(f"{label} assembled", "success")

_thread_local = threading.local()


def _session():
    """One keep-alive HTTP session per thread: hundreds of segments reuse
    the same connection instead of a new TLS handshake each."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=4)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def _fetch_segment(segment_url, headers, index, attempts=5):
    """Download one segment, retrying with a growing pause (1, 2, 4, 8 s)
    so a busy CDN under parallel requests gets time to recover."""
    for attempt in range(attempts):
        try:
            response = _session().get(segment_url, headers=headers, timeout=20)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            if attempt == attempts - 1:
                print_status(f"Failed to download segment {index+1}: {str(e)}", "error")
                return None
            time.sleep(2 ** attempt)
    return None


_RANGE_PIECE = 8 * 1024 * 1024


def _download_ranges(url, path, headers, position):
    """Download a single-file video (Sibnet, Sendvid, direct .mp4) over
    several connections at once, one byte range each: these hosts throttle
    every connection. Returns True when done; False when the server doesn't
    support ranges or a piece keeps failing - the caller then downloads it
    over one connection as before."""
    connections = parallel_settings.range_connections()
    if connections < 2:
        return False
    try:
        probe = requests.get(url, headers={**headers, "Range": "bytes=0-0"}, stream=True, timeout=20)
        probe.close()
    except requests.RequestException:
        return False
    match = re.match(r"bytes 0-0/(\d+)", probe.headers.get("Content-Range", ""))
    if probe.status_code != 206 or not match:
        return False
    total = int(match.group(1))
    if total < 2 * _RANGE_PIECE:
        return False
    final_url = probe.url  # after redirects (signed CDN links)
    pieces = [(start, min(start + _RANGE_PIECE, total) - 1) for start in range(0, total, _RANGE_PIECE)]
    lock = threading.Lock()
    failed = threading.Event()

    with open(path, "wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=f"📥 {os.path.basename(path)}",
                                     bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]',
                                     position=position, leave=False) as pbar:
        f.truncate(total)

        def fetch(start, end):
            for attempt in range(4):
                if failed.is_set():
                    return
                received = bytearray()
                try:
                    with _session().get(final_url, headers={**headers, "Range": f"bytes={start}-{end}"},
                                        stream=True, timeout=30) as response:
                        if response.status_code != 206:
                            # The host refuses parallel ranges (403, 429...):
                            # give up at once, one connection will do.
                            failed.set()
                            return
                        for chunk in response.iter_content(chunk_size=256 * 1024):
                            received += chunk
                            pbar.update(len(chunk))
                    if len(received) == end - start + 1:
                        with lock:
                            f.seek(start)
                            f.write(received)
                        return
                except requests.RequestException:
                    pass
                pbar.update(-len(received))
                time.sleep(2 ** attempt)
            failed.set()

        with ThreadPoolExecutor(max_workers=connections) as executor:
            list(executor.map(lambda piece: fetch(*piece), pieces))

    if failed.is_set() or os.path.getsize(path) != total:
        print_status("Multi-connection download refused by the host, retrying over one connection...", "warning")
        return False
    return True


def download_video(video_url, save_path, use_ts_threading=False, url='',automatic_mp4=False, threaded_mp4=False, interactive=True):
    # "Starting download" is printed by the caller (download_episode.py) as
    # part of its single atomic per-episode header block, not here.
    ua = DEFAULT_USER_AGENT

    target = url if url else video_url
    if target and not target.startswith(('http://', 'https://')):
        target = 'https://' + target

    if target and 'tnmr.org' not in target:
        parsed = urlparse(target)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        referer = f"{origin}/"
    else:
        referer = ''
        origin = ''

    headers = {
        'User-Agent': ua,
        'Accept': 'video/webm,video/mp4,video/*;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }
    if referer:
        headers['Referer'] = referer
    if origin:
        headers['Origin'] = origin

   
    if video_url.startswith("LULU_DEFERRED:"):
        embed_url = video_url[len("LULU_DEFERRED:"):]
        from src.utils.extract.extract_luluvdo_video_source import extract_luluvdo_video_source
        resolved = extract_luluvdo_video_source(embed_url)
        if not resolved:
            print_status(f"Download failed: could not resolve LuluStream token for {embed_url[:60]}", "error")
            return False, None
        video_url = resolved
        from urllib.parse import urlparse as _up
        _p = _up(embed_url)
        headers['Referer'] = f"{_p.scheme}://{_p.netloc}/"
        headers.pop('Origin', None)
        headers.pop('Accept-Language', None)

    position_ctx = _TqdmPosition()
    tqdm_position = position_ctx.__enter__()
    try:
        if 'm3u8' in video_url:
            from urllib.parse import urljoin

            response = requests.get(video_url, headers=headers, timeout=10)
            response.raise_for_status()
            content = response.text

            if "#EXT-X-STREAM-INF" in content:
                best_bandwidth = -1
                best_url = None
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if line.startswith("#EXT-X-STREAM-INF"):
                        bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                        bw = int(bw_match.group(1)) if bw_match else 0
                        candidate = lines[i+1].strip() if i+1 < len(lines) else None
                        if candidate:
                            if bw > best_bandwidth:
                                best_bandwidth = bw
                                best_url = candidate
                if best_url:
                    if not best_url.startswith('http'):
                        best_url = urljoin(response.url, best_url)
                    variant_resp = requests.get(best_url, headers=headers, timeout=10)
                    variant_resp.raise_for_status()
                    content = variant_resp.text
            base_for_join = response.url
            try:
                if 'variant_resp' in locals():
                    base_for_join = variant_resp.url
            except Exception:
                pass

            init_segment_url = None
            map_match = re.search(r'#EXT-X-MAP:URI=["\']?([^"\',\s]+)["\']?', content)
            if map_match:
                init_uri = map_match.group(1)
                init_segment_url = init_uri if init_uri.startswith('http') else urljoin(base_for_join, init_uri)

            segments = []
            if init_segment_url:
                segments.append(init_segment_url)

            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if not line.startswith('http'):
                    seg_url = urljoin(base_for_join, line)
                else:
                    seg_url = line
                segments.append(seg_url)
            if not segments:
                print_status("No .ts segments found in M3U8 playlist", "error")
                return False, None
            
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            temp_ts_path = save_path.replace('.mp4', '.ts')
            random_string = os.path.basename(save_path).replace('.mp4', '.ts')

            if automatic_mp4 is False and use_ts_threading is False:
                if interactive:
                    tqdm.write(f"\n{Colors.BOLD}{Colors.OKCYAN}Threaded Download Option{Colors.ENDC}")
                    print_status("Threaded downloading is faster but should not be used on weak Wi-Fi.", "info")
                    use_threads = input(f"{Colors.BOLD}Use threaded download for faster performance? (y/n, default: n): {Colors.ENDC}").strip().lower()
                    use_threads = use_threads in ['y', 'yes', '1']
                else:
                    use_threads = False
            else:
                use_threads = use_ts_threading
            
            if use_threads:
                # Segments are written in order as soon as they're contiguous,
                # instead of holding the whole episode in memory until the end.
                pending, next_index, failed = {}, 0, False
                with open(temp_ts_path, 'wb') as f, \
                        ThreadPoolExecutor(max_workers=parallel_settings.segment_workers()) as executor, \
                        tqdm(total=len(segments), desc=f"📥 {random_string}", unit="segment", position=tqdm_position, leave=False) as pbar:
                    future_to_segment = {executor.submit(_fetch_segment, seg_url, headers, i): i for i, seg_url in enumerate(segments)}
                    for future in as_completed(future_to_segment):
                        index = future_to_segment[future]
                        content = future.result()
                        if content is None:
                            print_status(f"Aborting download due to failure in segment {index+1}", "error")
                            for other in future_to_segment:
                                other.cancel()
                            failed = True
                            break
                        pending[index] = content
                        while next_index in pending:
                            f.write(pending.pop(next_index))
                            next_index += 1
                        pbar.update(1)
                if failed:
                    try:
                        os.remove(temp_ts_path)
                    except OSError:
                        pass
                    return False, None
            else:
                with open(temp_ts_path, 'wb') as f:
                    for i, segment_url in enumerate(tqdm(segments, desc=f"📥 {random_string}", unit="segment", position=tqdm_position, leave=False)):
                        content = _fetch_segment(segment_url, headers, i)
                        if content is None:
                            return False, None
                        f.write(content)

            if _batch_total == 0:
                print_status(f"Combined {len(segments)} segments into {temp_ts_path}", "success")
            _report_episode_done(random_string)
            return True, temp_ts_path
        else:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            if _download_ranges(video_url, save_path, headers, tqdm_position):
                if _batch_total == 0:
                    print_status(f"Download completed successfully!", "success")
                _report_episode_done(os.path.basename(save_path))
                return True, save_path

            response = requests.get(video_url, stream=True, headers=headers, timeout=30)
            total_size = int(response.headers.get('content-length', 0))
            
            if response.status_code != 200:
                print_status(f"Download failed with status code: {response.status_code}", "error")
                return False, None

            with open(save_path, 'wb') as f:
                with tqdm(
                    total=total_size,
                    unit='B',
                    unit_scale=True,
                    desc=f"📥 {os.path.basename(save_path)}",
                    bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]',
                    position=tqdm_position,
                    leave=False
                ) as pbar:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            pbar.update(len(chunk))

            if _batch_total == 0:
                print_status(f"Download completed successfully!", "success")
            _report_episode_done(os.path.basename(save_path))
            return True, save_path
    except Exception as e:
        print_status(f"Download failed: {str(e)}", "error")
        return False, None
    finally:
        position_ctx.__exit__(None, None, None)
