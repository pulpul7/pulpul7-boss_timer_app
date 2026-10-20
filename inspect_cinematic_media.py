"""Read-only MP4 video/audio measurements with optional JSON/Markdown reports."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from web_cinematic import atoms, media_folder, video_metadata


def timing(data, start):
    version = data[start]
    scale = struct.unpack_from('>I', data, start + (20 if version else 12))[0]
    ticks = struct.unpack_from('>Q' if version else '>I', data, start + (24 if version else 16))[0]
    return scale, ticks / scale if scale else 0


def measure(path):
    path = Path(path)
    before = path.stat()
    data = path.read_bytes()
    _, start, end = next(item for item in atoms(data) if item[0] == b'moov')
    movie = list(atoms(data, start, end))
    movie_scale, movie_seconds = next(timing(data, a) for k, a, z in movie if k == b'mvhd')
    tracks = []
    for kind, a, z in movie:
        if kind != b'trak':
            continue
        track = list(atoms(data, a, z))
        media = next((list(atoms(data, b, y)) for k, b, y in track if k == b'mdia'), [])
        handler = next((data[b + 8:b + 12].decode('ascii') for k, b, y in media if k == b'hdlr'), '')
        if handler not in ('vide', 'soun'):
            continue
        raw = next(timing(data, b)[1] for k, b, y in media if k == b'mdhd')
        presented = raw
        for k, b, y in track:
            if k != b'edts':
                continue
            for k, c, x in atoms(data, b, y):
                if k != b'elst':
                    continue
                version = data[c]
                count = struct.unpack_from('>I', data, c + 4)[0]
                stride = 20 if version else 12
                if c + 8 + count * stride > x:
                    raise ValueError('truncated MP4 edit list')
                presented = sum(struct.unpack_from('>Q' if version else '>I', data, c + 8 + i * stride)[0]
                                for i in range(count)) / movie_scale
        tracks.append(dict(kind=handler, encoded_seconds=raw, playback_seconds=presented))
    video = video_metadata(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('File changed during measurement; run again: ' + path.name)
    return dict(file=path.name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                video_seconds=video['duration'], fps=video['fps'], width=video['width'], height=video['height'],
                movie_seconds=movie_seconds, audio=[t for t in tracks if t['kind'] == 'soun'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--markdown', type=Path, help='Write a current-file duration table too.')
    parser.add_argument('--measured-on', help='User-facing measurement date (YYYY-MM-DD).')
    args = parser.parse_args()
    rows = [measure(path) for path in sorted(media_folder().glob('*.mp4'))]
    report = json.dumps(dict(schema=1, measured_on=args.measured_on, files=rows), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_bytes(report.encode('utf-8'))
        print(f'Measured {len(rows)} files; source MP4s unchanged.')
    else:
        print(report)
    if args.markdown:
        lines = ['# 시네마틱 영상·음성 길이 측정', '',
                 f'측정 기준일: {args.measured_on or "미지정"}. 현재 폴더의 MP4 {len(rows)}개를 직접 읽었습니다.', '',
                 '영상은 자동 재생 예약에 사용하는 비디오 트랙 길이, 음성은 MP4 편집 구간을 반영한 재생 길이입니다.',
                 '전체 길이는 컨테이너 기준입니다. 음성 인코더 패딩과 파일 해시는 [JSON 측정 원본](cinematic_media_measurements.json)에 기록합니다.',
                 '이 측정 도구는 파일을 변경하지 않습니다. 이후 파일을 교체하면 다시 측정해야 합니다.', '',
                 '| 파일 | 영상(초) | 음성(초) | 전체(초) | FPS | 크기 |',
                 '|---|---:|---:|---:|---:|---|']
        for row in rows:
            audio = ', '.join(f'{track["playback_seconds"]:.3f}' for track in row['audio']) or '없음'
            lines.append(f'| {row["file"]} | {row["video_seconds"]:.3f} | {audio} | {row["movie_seconds"]:.3f} | {row["fps"]:.3f} | {row["width"]}×{row["height"]} |')
        args.markdown.write_text('\n'.join(lines) + '\n', encoding='utf-8')
