"""One-time source asset download; never runs with BossTimer or installs OS fonts."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
import hashlib
import io
import json
import zipfile

ROOT = Path(__file__).resolve().parent / 'web_font_originals'
SOURCES = [
    ('gaegu', '개구', '손글씨', 'Google Fonts', 'https://fonts.google.com/specimen/Gaegu', 'https://raw.githubusercontent.com/google/fonts/main/ofl/gaegu/Gaegu-Regular.ttf'),
    ('gamja-flower', '감자꽃', '귀여움', 'Google Fonts', 'https://fonts.google.com/specimen/Gamja+Flower', 'https://raw.githubusercontent.com/google/fonts/main/ofl/gamjaflower/GamjaFlower-Regular.ttf'),
    ('hi-melody', '하이멜로디', '손글씨', 'Google Fonts', 'https://fonts.google.com/specimen/Hi+Melody', 'https://raw.githubusercontent.com/google/fonts/main/ofl/himelody/HiMelody-Regular.ttf'),
    ('poor-story', '푸어스토리', '귀여움', 'Google Fonts', 'https://fonts.google.com/specimen/Poor+Story', 'https://raw.githubusercontent.com/google/fonts/main/ofl/poorstory/PoorStory-Regular.ttf'),
    ('dongle', '동글', '둥근 글씨', 'Google Fonts', 'https://fonts.google.com/specimen/Dongle', 'https://raw.githubusercontent.com/google/fonts/main/ofl/dongle/Dongle-Regular.ttf'),
    ('gugi', '구기', '개성', 'Google Fonts', 'https://fonts.google.com/specimen/Gugi', 'https://raw.githubusercontent.com/google/fonts/main/ofl/gugi/Gugi-Regular.ttf'),
    ('nanum-round', '나눔스퀘어라운드', '둥근 글씨', '네이버', 'https://hangeul.naver.com/font', 'https://hangeul.pstatic.net/hangeul_static/webfont/NanumSquareRound/NanumSquareRoundR.woff2'),
    ('nanum-barun', '나눔바른고딕', '가독성', '네이버', 'https://hangeul.naver.com/font', 'https://hangeul.pstatic.net/hangeul_static/webfont/NanumBarunGothic/NanumBarunGothicWeb.woff'),
    ('nanum-pen', '나눔손글씨 펜', '손글씨', '네이버', 'https://hangeul.naver.com/font', 'https://hangeul.pstatic.net/hangeul_static/webfont/NanumBrush/NanumPen.woff'),
    ('nanum-brush', '나눔손글씨 붓', '손글씨', '네이버', 'https://hangeul.naver.com/font', 'https://hangeul.pstatic.net/hangeul_static/webfont/NanumBrush/NanumBrush.woff'),
    ('bm-jua', '배민 주아', '귀여움', '우아한형제들', 'https://www.woowahan.com/fonts', 'https://raw.githubusercontent.com/google/fonts/main/ofl/jua/Jua-Regular.ttf'),
    ('bm-yeonsung', '배민 연성', '손글씨', '우아한형제들', 'https://www.woowahan.com/fonts', 'https://raw.githubusercontent.com/google/fonts/main/ofl/yeonsung/YeonSung-Regular.ttf'),
    ('bm-dohyeon', '배민 도현', '또렷함', '우아한형제들', 'https://www.woowahan.com/fonts', 'https://raw.githubusercontent.com/google/fonts/main/ofl/dohyeon/DoHyeon-Regular.ttf'),
    ('cafe24-ssurround-air', '카페24 써라운드 Air', '둥근 글씨', '카페24', 'https://fonts.cafe24.com/', 'https://raw.githubusercontent.com/projectnoonnu/noonfonts_2105_2/master/Cafe24SsurroundAir.woff'),
    ('cafe24-dongdong', '카페24 동동', '귀여움', '카페24', 'https://fonts.cafe24.com/', 'https://img.cafe24.com/csdstatic/freefonts/download/kr/Cafe24DongdongLight_v3.0.zip'),
    ('cafe24-danjunghae', '카페24 단정해', '손글씨', '카페24', 'https://fonts.cafe24.com/', 'https://img.cafe24.com/csdstatic/freefonts/download/kr/Cafe24Danjunghae-v2.0.zip'),
    ('gmarket-sans', 'G마켓 산스', '가독성', 'G마켓', 'https://corp.gmarket.com/fonts/', 'https://corp.gmarket.com/fonts/GmarketSansTTF.zip'),
    ('ridi-batang', '리디바탕', '본문', '리디', 'https://ridicorp.com/ridibatang/', 'https://raw.githubusercontent.com/projectnoonnu/noonfonts_twelve/master/RIDIBatang.woff'),
    ('pretendard', '프리텐다드', '가독성', 'Pretendard', 'https://cactus.tistory.com/306', 'https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/web/static/woff2/Pretendard-Regular.woff2'),
    ('suit', 'SUIT', '가독성', 'Sun', 'https://sun.fo/suit/', 'https://cdn.jsdelivr.net/gh/sun-typeface/SUIT@2/fonts/static/woff2/SUIT-Regular.woff2'),
]


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 BossTimer-Font-Assets'}), timeout=25) as response:
        raw = response.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError('Download too large')
    return raw


def download(item):
    number, (key, name, category, provider, source, url) = item
    try:
        raw, ext = fetch(url), Path(url).suffix.lower()
        if ext == '.zip':
            archive = zipfile.ZipFile(io.BytesIO(raw))
            names = [n for n in archive.namelist() if n.lower().endswith('.ttf') and not n.startswith('__MACOSX/')]
            if key == 'gmarket-sans':
                names = [n for n in names if 'medium' in n.lower()]
            if not names:
                raise ValueError('No matching TTF in archive')
            chosen = sorted(names, key=lambda n: ('light' not in n.lower(), len(n)))[0]
            raw, ext = archive.read(chosen), '.ttf'
            for index, member in enumerate(archive.namelist()):
                if any(word in member.lower() for word in ('license', 'licence', 'ofl', 'readme', '라이선스', '저작권')):
                    info = archive.getinfo(member)
                    if not info.is_dir() and info.file_size < 2 * 1024 * 1024:
                        suffix = Path(member).suffix.lower()
                        if suffix in {'.txt', '.pdf', '.html', '.md'}:
                            (ROOT / 'licenses' / f'{key}-{index}{suffix}').write_bytes(archive.read(member))
        if raw[:4] not in {b'\x00\x01\x00\x00', b'OTTO', b'wOFF', b'wOF2'} or len(raw) > 16 * 1024 * 1024:
            raise ValueError('Invalid font file')
        filename = key + ext
        (ROOT / filename).write_bytes(raw)
        row = dict(id=key, number=number, name=name, category=category, provider=provider,
                   source=source, download=url, file=filename, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        if 'raw.githubusercontent.com/google/fonts/' in url:
            license_url = url.rsplit('/', 1)[0] + '/OFL.txt'
            (ROOT / 'licenses' / (key + '.txt')).write_bytes(fetch(license_url))
        return row
    except Exception as exc:
        return dict(id=key, error=type(exc).__name__ + ': ' + str(exc))


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / 'licenses').mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(download, enumerate(SOURCES, 1)))
    for row in rows:
        print(json.dumps({key: row[key] for key in ('id', 'bytes', 'error') if key in row}, ensure_ascii=True), flush=True)
    if any('error' in row for row in rows):
        (ROOT / 'download-results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        return 1
    catalog = dict(schema=1, default='system', fonts=rows)
    (ROOT / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
