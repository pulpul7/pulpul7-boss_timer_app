"""Authenticated, seekable local cinematic media. Never reads user settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import hashlib
import json
import re
import struct
import sys


def bundle_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def media_folder():
    if getattr(sys, 'frozen', False):
        external = Path(sys.executable).resolve().parent / '시네마틱'
        if external.is_dir():
            return external
    return bundle_root() / '시네마틱'


def atoms(data, start=0, end=None):
    end = len(data) if end is None else end
    while start + 8 <= end:
        size, kind = struct.unpack_from('>I4s', data, start)
        header = 8
        if size == 1:
            if start + 16 > end:
                raise ValueError('truncated atom')
            size, header = struct.unpack_from('>Q', data, start + 8)[0], 16
        if size == 0:
            size = end - start
        if size < header or start + size > end:
            raise ValueError('invalid atom')
        yield kind, start + header, start + size
        start += size


def video_metadata(path):
    """Read only moov; use video-track timebase, not audio/movie duration."""
    with Path(path).open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(0)
        while stream.tell() + 8 <= size:
            start = stream.tell()
            count, kind = struct.unpack('>I4s', stream.read(8))
            header = 8
            if count == 1:
                count, header = struct.unpack('>Q', stream.read(8))[0], 16
            if count == 0:
                count = size - start
            if count < header or start + count > size:
                raise ValueError('invalid MP4')
            if kind == b'moov':
                if count > 2 * 1024 * 1024:
                    raise ValueError('oversized metadata')
                data = stream.read(count - header)
                break
            stream.seek(start + count)
        else:
            raise ValueError('no movie metadata')
    for kind, a, z in atoms(data):
        if kind != b'trak':
            continue
        for kind, a, z in atoms(data, a, z):
            if kind != b'mdia':
                continue
            parts = list(atoms(data, a, z))
            if not any(kind == b'hdlr' and data[a + 8:a + 12] == b'vide' for kind, a, z in parts):
                continue
            duration, frames, width, height = 0, 0, 0, 0
            for kind, a, z in parts:
                if kind == b'mdhd':
                    v = data[a]
                    scale = struct.unpack_from('>I', data, a + (20 if v else 12))[0]
                    ticks = struct.unpack_from('>Q' if v else '>I', data, a + (24 if v else 16))[0]
                    duration = ticks / scale if scale else 0
                if kind == b'minf':
                    for kind, a, z in atoms(data, a, z):
                        if kind != b'stbl':
                            continue
                        for kind, a, z in atoms(data, a, z):
                            if kind == b'stsz':
                                frames = struct.unpack_from('>I', data, a + 8)[0]
                            if kind == b'stsd' and z - a >= 44:
                                width, height = struct.unpack_from('>HH', data, a + 40)
            if not 0 < duration <= 600 or not frames or not width or not height:
                raise ValueError('unsupported video metadata')
            return dict(duration=duration, fps=frames / duration, width=width, height=height)
    raise ValueError('no video track')


@lru_cache(maxsize=64)
def inspected(path, modified, size):
    metadata = video_metadata(path)
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(256 * 1024), b''):
            digest.update(block)
    return digest.hexdigest(), metadata


def clips():
    folder = media_folder().resolve()
    result = {}
    for path in sorted(folder.glob('*.mp4')):
        try:
            if path.is_symlink() or path.resolve().parent != folder:
                continue
            stat = path.stat()
            if not 0 < stat.st_size <= 40 * 1024 * 1024:
                continue
            key, metadata = inspected(str(path), stat.st_mtime_ns, stat.st_size)
            result[key] = (path, stat.st_size, metadata)
        except (OSError, ValueError, struct.error, OverflowError):
            continue
    return result


def cinematic_response(path, range_header='', if_none_match='', if_range=''):
    """Return (status, bytes, MIME, headers), with bounded single byte ranges."""
    if path in ('/cinematic/player.js', '/cinematic/player.css'):
        script = path.endswith('.js')
        body = (bundle_root() / ('web_assets/avengers/cinematic.js' if script else 'web_assets/avengers/cinematic.css')).read_bytes()
        mime = 'text/javascript' if script else 'text/css'
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        headers = (('ETag', etag), ('Cache-Control', 'private, no-cache'))
        return (304, b'', mime, headers) if etag in if_none_match.split(',') else (200, body, mime + '; charset=utf-8', headers)
    if path == '/cinematic/catalog.json':
        body = json.dumps(dict(clips=[dict(url='/cinematic/' + key + '.mp4', name=file.stem, **meta)
            for key, (file, _, meta) in clips().items()]), separators=(',', ':')).encode()
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        headers = (('Cache-Control', 'private, no-cache'), ('ETag', etag))
        return (304, b'', 'application/json', headers) if etag in {v.strip() for v in if_none_match.split(',')} else (200, body, 'application/json', headers)
    match = re.fullmatch(r'/cinematic/([0-9a-f]{64})\.mp4', path)
    record = clips().get(match[1]) if match else None
    if record is None:
        return 404, b'', 'video/mp4', ()
    file, size, _ = record
    etag = '"' + match[1] + '"'
    headers = (('ETag', etag), ('Accept-Ranges', 'bytes'), ('Cache-Control', 'private, max-age=86400, immutable'))
    if etag in {v.strip() for v in if_none_match.split(',')}:
        return 304, b'', 'video/mp4', headers
    start, end, code = 0, size - 1, 200
    if range_header and (not if_range or if_range == etag):
        requested = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
        try:
            if not requested or not any(requested.groups()):
                raise ValueError('invalid range')
            first, last = requested.groups()
            if first:
                start, end = int(first), min(int(last), size - 1) if last else size - 1
            else:
                count = int(last)
                if count <= 0:
                    raise ValueError('invalid suffix')
                start = max(0, size - count)
            if start > end or start >= size:
                raise ValueError('outside file')
        except ValueError:
            return 416, b'', 'video/mp4', headers + (('Content-Range', f'bytes */{size}'),)
        code = 206
        headers += (('Content-Range', f'bytes {start}-{end}/{size}'),)
    with file.open('rb') as stream:
        stream.seek(start)
        body = stream.read(end - start + 1)
    return code, body, 'video/mp4', headers
