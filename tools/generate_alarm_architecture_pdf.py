from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "schedule_alarm_architecture.html"
OUT = ROOT / "docs" / "schedule_alarm_architecture.pdf"


def pdf_text(value: str) -> str:
    return "<" + value.encode("utf-16-be").hex().upper() + ">"


class Pdf:
    def __init__(self) -> None:
        self.objects: dict[int, bytes] = {}
        self.pages: list[int] = []
        self.next_object_id = 5

    def add_object(self, body: str | bytes, object_id: int | None = None) -> int:
        body_bytes = body.encode("latin-1") if isinstance(body, str) else body
        if object_id is None:
            object_id = self.next_object_id
            self.next_object_id += 1
        self.objects[object_id] = body_bytes
        return object_id

    def add_page(self, stream: str) -> None:
        stream_bytes = stream.encode("latin-1")
        stream_obj = self.add_object(
            b"<< /Length "
            + str(len(stream_bytes)).encode("ascii")
            + b" >>\nstream\n"
            + stream_bytes
            + b"\nendstream"
        )
        page_obj = self.add_object(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_obj} 0 R >>"
        )
        self.pages.append(page_obj)

    def write(self, path: Path) -> None:
        pages_kids = " ".join(f"{page} 0 R" for page in self.pages)
        self.add_object("<< /Type /Catalog /Pages 2 0 R >>", object_id=1)
        self.add_object(f"<< /Type /Pages /Kids [{pages_kids}] /Count {len(self.pages)} >>", object_id=2)
        self.add_object(
            "<< /Type /Font /Subtype /Type0 /BaseFont /HYGoThic-Medium "
            "/Encoding /UniKS-UCS2-H /DescendantFonts [4 0 R] >>",
            object_id=3,
        )
        self.add_object(
            "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HYGoThic-Medium "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (Korea1) /Supplement 2 >> >>",
            object_id=4,
        )

        max_object_id = max(self.objects)
        offsets = [0] * (max_object_id + 1)
        output = bytearray(b"%PDF-1.4\n%\xE2\xE3\xCF\xD3\n")
        for index in range(1, max_object_id + 1):
            body = self.objects[index]
            offsets[index] = len(output)
            output.extend(f"{index} 0 obj\n".encode("ascii"))
            output.extend(body)
            output.extend(b"\nendobj\n")

        xref_at = len(output)
        output.extend(f"xref\n0 {max_object_id + 1}\n".encode("ascii"))
        output.extend(b"0000000000 65535 f \n")
        for offset in offsets[1 : max_object_id + 1]:
            output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        output.extend(
            f"trailer\n<< /Size {max_object_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(output))


def draw_text(x: int, y: int, text: str, size: int = 10) -> str:
    return f"BT /F1 {size} Tf 1 0 0 1 {x} {y} Tm {pdf_text(text)} Tj ET\n"


def rect(x: int, y: int, w: int, h: int, fill: str = "0.96 0.98 1", stroke: str = "0.2 0.45 0.85") -> str:
    return f"q {fill} rg {stroke} RG {x} {y} {w} {h} re B Q\n"


def line(x1: int, y1: int, x2: int, y2: int, stroke: str = "0.15 0.35 0.8") -> str:
    return f"q {stroke} RG 1.4 w {x1} {y1} m {x2} {y2} l S Q\n"


def wrapped_text(x: int, y: int, text: str, max_chars: int, size: int = 9, line_height: int = 14) -> tuple[str, int]:
    parts: list[str] = []
    current = ""
    for word in text.split(" "):
        if len(current) + len(word) + 1 > max_chars:
            if current:
                parts.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        parts.append(current)

    stream = ""
    for part in parts:
        stream += draw_text(x, y, part, size)
        y -= line_height
    return stream, y


def page_number(number: int) -> str:
    return draw_text(520, 28, f"{number} / 4", 8)


RULE_ROWS = [
    ("1", "초확정 즉시젠", "일반", "초확정", "즉시젠", "구현"),
    ("2", "침공 초확정 즉시젠", "침공", "초확정", "즉시젠", "구현"),
    ("3", "침공 초확정 겹침", "침공", "초확정", "충돌", "구현"),
    ("4", "초읽기 시작", "일반", "초확정", "초읽기", "구현"),
    ("5", "초읽기 본문", "일반", "초확정", "초읽기", "구현"),
    ("6", "초확정 젠 완료", "일반", "초확정", "젠완료", "구현"),
    ("7", "초보정 즉시젠", "일반", "분확정", "즉시젠", "구현"),
    ("8", "침공 초보정", "침공", "분확정", "즉시젠", "구현"),
    ("9", "침공 n분전", "침공", "전체", "n분전", "구현"),
    ("10", "일반 n분전", "일반", "전체", "n분전", "구현"),
    ("11", "n분전 초읽기 겹침", "일반", "전체", "충돌", "구현"),
    ("12", "동시간 보스 n분전", "일반", "전체", "묶음", "구현"),
    ("13", "곧 소멸될 1분전", "일반", "전체", "특수", "구현"),
    ("14", "연속보스 감지", "일반", "전체", "특수", "구현"),
    ("15", "고정보스 알람", "고정", "시각고정", "n분전", "구현"),
    ("16", "고정보스 초읽기 겹침", "고정", "시각고정", "충돌", "구현"),
    ("17", "고정보스 초확정 근접", "고정", "시각고정", "충돌", "구현"),
    ("18", "고정보스 재생 직전 보정", "고정", "시각고정", "보정", "구현"),
    ("19", "자가 1분전", "제어", "시각고정", "n분전", "구현"),
    ("20", "자가 도달", "제어", "시각고정", "즉시젠", "구현"),
    ("21", "다음 보스 연속 안내", "일반", "초확정", "연속", "구현"),
    ("22", "차임벨", "공통", "전체", "공통", "구현"),
    ("23", "좌측 채널", "공통", "전체", "채널", "기반"),
    ("24", "음성 규칙 엔진", "공통", "전체", "관리", "보류"),
]


IMPLEMENTATION_CONDITIONS = [
    "현재 실행 모드가 일반 운용인지 테스트인지 먼저 분리한다.",
    "원본 스케쥴을 일반, 침공, 고정보스, 자가 이벤트로 분류한다.",
    "보스 이름과 표시명을 정규화해서 중복 판단 기준을 하나로 맞춘다.",
    "시간 기준을 초확정, 분확정, 시각고정, 초보정으로 분류한다.",
    "초보정은 분확정 이벤트에 초 단위 보정이 붙은 것으로 다룬다.",
    "보스별 알람 설정에서 일반보스와 고정보스의 n분전 목록을 각각 읽는다.",
    "고정보스는 5분과 1분처럼 여러 값을 설정하면 각각 독립 후보를 만든다.",
    "초확정이고 초읽기 OFF이면 해당 시각에 즉시젠 후보를 만든다.",
    "초확정이고 초읽기 ON이면 시작 안내, 숫자 초읽기, 젠 완료 후보를 만든다.",
    "분확정 또는 초보정 이벤트는 도달 시각에 즉시젠 후보를 만든다.",
    "침공 이벤트는 일반 이벤트와 같은 구조를 쓰되 침공 전용 문구와 채널을 적용한다.",
    "동시간 보스는 같은 알람 시각의 후보를 묶어서 한 문장으로 만든다.",
    "동시간 묶음에는 차임벨을 한 번만 붙이고 보스명은 순서대로 이어 붙인다.",
    "연속보스는 현재 젠 안내 직후 다음 보스가 가까울 때 별도 안내 후보를 만든다.",
    "고정보스가 초읽기 또는 초확정 안내와 가까우면 중앙 큐 대기 후보로 분리한다.",
    "고정보스 재생 직전 보정은 너무 늦은 n분전 안내를 현재 상태에 맞게 바꾼다.",
    "곧 소멸될 1분전 안내는 일반 n분전보다 특수 후보로 먼저 식별한다.",
    "자가 이벤트는 보스 이벤트와 섞지 않고 시스템 제어용 후보로 별도 생성한다.",
    "후보 생성 이후에만 우선순위를 정하고, 원본 스케쥴 단계에서는 재생 순서를 정하지 않는다.",
    "우선순위는 즉시젠, 초읽기, 일반 n분전, 고정보스 n분전, 제어 이벤트를 한 곳에서 비교한다.",
    "같은 후보가 여러 tick에서 반복 생성되지 않도록 고유 키를 만든다.",
    "VoiceRequest에는 파일 경로, TTS 대체 문구, 채널, earliest_play_at, priority만 넣는다.",
    "음성 파일이 없으면 같은 VoiceRequest 안에서 TTS로 대체한다.",
    "중앙 채널은 실제 재생 완료 신호를 받은 뒤 다음 요청을 재생한다.",
    "좌측 채널은 중앙 채널을 막지 않는 보조 안내에만 사용한다.",
    "테스트 모드는 선택한 경우 하나만 생성하고 기존 스케쥴 복원 경로를 항상 보존한다.",
    "테스트 종료, 중지, 창 닫기에서는 스케쥴 복원 후 오늘 버튼 트리거를 거친다.",
    "로그는 경우별 1개 파일만 유지하고 48시간 또는 0.5MB 기준으로 정리한다.",
    "24번 규칙 엔진은 tick 내부 조건 추가를 막기 위한 기반으로만 남긴다.",
]


PRIORITY_RULES = [
    ("1", "재생 중인 중앙 음성", "이미 시작된 음성은 끊지 않는다. 다음 요청은 완료 신호 뒤에만 진행한다."),
    ("2", "테스트 제어", "테스트 시작, 중지, 복원, 오늘 버튼 트리거는 실제 스케쥴보다 먼저 처리한다."),
    ("3", "초읽기 숫자", "초확정 초읽기가 진행 중이면 숫자 흐름을 유지한다. 침공 포함 다른 안내는 큐에 대기한다."),
    ("4", "초확정 젠 완료", "0초 도달 또는 보정된 젠 완료 안내는 n분전 안내보다 앞선다."),
    ("5", "침공 초확정 즉시젠", "침공이 초확정으로 들어오면 침공 전용 문구를 쓰고 일반 즉시젠과 같은 급으로 처리한다."),
    ("6", "일반 초확정 즉시젠", "초읽기 OFF인 초확정 일반 이벤트의 즉시젠 안내를 처리한다."),
    ("7", "침공 초보정 즉시젠", "침공 분확정에 초 보정이 붙은 경우 즉시젠 후보로 처리하되 침공 문구를 유지한다."),
    ("8", "일반 초보정 / 분확정 즉시젠", "분확정 또는 초보정 일반 이벤트의 도달 안내를 처리한다."),
    ("9", "동시간 묶음 안내", "같은 알람 시각의 보스들은 하나의 문장으로 묶고 차임벨은 한 번만 사용한다."),
    ("10", "고정보스 재생 직전 보정", "고정보스 n분전이 너무 늦게 잡혔으면 현재 시점에 맞는 문구로 보정한다."),
    ("11", "고정보스 n분전", "사용자가 설정한 5분, 1분 같은 모든 고정보스 알람을 각각 독립 후보로 처리한다."),
    ("12", "침공 n분전", "침공도 보스별 n분전 설정이 있으면 일반 n분전과 같은 방식으로 후보를 만든다."),
    ("13", "일반 n분전", "일반보스의 보스별 알람 설정에 따라 n분전 안내를 처리한다."),
    ("14", "고정보스 겹침 대기", "초읽기나 초확정 안내와 가까운 고정보스는 중앙 큐에서 대기시킨다."),
    ("15", "연속보스 안내", "현재 젠 안내 직후 다음 보스가 가까울 때 추가 안내를 붙인다."),
    ("16", "좌측 채널 보조 안내", "중앙 음성을 방해하지 않아도 되는 침공 예측 등 보조 안내만 좌측으로 보낸다."),
    ("17", "중복 후보 제거", "같은 이벤트와 같은 offset에서 나온 후보는 한 번만 큐에 넣는다."),
    ("18", "TTS 대체", "음성 파일이 없을 때만 같은 우선순위 안에서 TTS로 대체한다."),
]


def build_overview_page(pdf: Pdf) -> None:
    page = ""
    page += draw_text(44, 792, "BossTimer 알람 구조 리뉴얼 스토리보드", 18)
    page += draw_text(
        44,
        766,
        "목표 : 원본 이벤트부터 음성 요청까지 계층별로 판단, 24번 규칙 엔진은 기반만 남겨두고 실제 구현 여부는 마지막에 결정",
        8,
    )

    boxes = [
        (44, 660, "원본 이벤트", "일반 / 침공 / 고정"),
        (154, 660, "표준 이벤트", "source / precision"),
        (264, 660, "알람 후보", "젠 / n분전 / 초읽기"),
        (374, 660, "충돌 정리", "우선순위 / 대기"),
        (484, 660, "음성 요청", "완료 신호 후 다음"),
    ]
    for x, y, title, sub in boxes:
        page += rect(x, y, 92, 58)
        page += draw_text(x + 15, y + 35, title, 10)
        page += draw_text(x + 8, y + 16, sub, 8)
    for x in (136, 246, 356, 466):
        page += line(x, 689, x + 18, 689)
        page += line(x + 18, 689, x + 12, 693)
        page += line(x + 18, 689, x + 12, 685)

    page += draw_text(44, 620, "계층별 책임", 14)
    responsibilities = [
        ("Source", "일반 / 침공 / 고정보스 / 자가 이벤트를 구분한다."),
        ("Precision", "초확정 / 분확정 / 시각고정 / 초보정을 구분한다."),
        ("Purpose", "즉시젠 / n분전 / 초읽기 / 젠완료 / 연속안내를 구분한다."),
        ("Conflict", "동시간 / 연속 / 고정보스 겹침 / 채널 / 대기를 판단한다."),
        ("Voice Request", "재생 파일 / TTS / 채널 / 우선순위만 담는다."),
        ("Playback", "실제 재생 완료 신호를 받은 뒤 다음 요청을 실행한다."),
    ]
    y = 584
    for title, body in responsibilities:
        page += rect(44, y - 8, 506, 28, fill="1 1 1", stroke="0.78 0.84 0.9")
        page += draw_text(56, y + 2, title, 9)
        page += draw_text(160, y + 2, body, 9)
        y -= 36

    page += draw_text(44, 344, "리뉴얼 순서", 14)
    steps = [
        "1. 규칙 카탈로그를 기준으로 UI 표시와 테스트 케이스를 정렬한다.",
        "2. 모든 원본 스케쥴을 표준 RuntimeEvent로 변환한다.",
        "3. RuntimeEvent에서 알람 후보를 생성한다.",
        "4. 충돌 정리 단계에서 우선순위, 채널, 대기 시간만 한 번 결정한다.",
        "5. VoiceRequest만 큐에 넣고 실제 재생 완료 신호를 기준으로 다음 요청을 진행한다.",
        "6. 24번 규칙 엔진은 기반만 남기고 실제 구현 여부는 마지막에 결정한다.",
    ]
    y = 318
    for step in steps:
        page += draw_text(52, y, step, 9)
        y -= 22

    page += rect(44, 94, 506, 58, fill="1 0.95 0.95", stroke="0.85 0.15 0.15")
    page += draw_text(58, 130, "원칙", 12)
    page += draw_text(58, 110, "새 조건은 tick 함수 내부에 직접 넣지 않는다. 먼저 규칙 카탈로그에 등록하고 계층을 결정한다.", 9)
    page += page_number(1)
    pdf.add_page(page)


def build_catalog_page(pdf: Pdf) -> None:
    page = ""
    page += draw_text(44, 792, "규칙 카탈로그 요약", 18)
    page += draw_text(44, 768, "현재 음성설정의 22개 규칙과 이후 기반 항목을 구조 기준으로 정렬한 목록이다.", 9)
    headers = ["No", "규칙", "Source", "Precision", "Purpose", "상태"]
    widths = [32, 150, 78, 78, 88, 58]
    x0, y0 = 44, 735
    x = x0
    for width, header in zip(widths, headers):
        page += rect(x, y0, width, 24, fill="0.88 0.96 1", stroke="0.6 0.72 0.84")
        page += draw_text(x + 4, y0 + 8, header, 8)
        x += width

    y = y0 - 20
    for row in RULE_ROWS:
        x = x0
        for width, value in zip(widths, row):
            fill = "1 0.97 0.9" if row[0] == "24" else "1 1 1"
            page += rect(x, y, width, 20, fill=fill, stroke="0.82 0.86 0.9")
            page += draw_text(x + 4, y + 7, value, 6)
            x += width
        y -= 20

    page += draw_text(44, 74, "24번은 기반만 유지한다. 실제 구현 시에는 3~4페이지 조건과 우선순위에 맞춰 붙인다.", 9)
    page += page_number(2)
    pdf.add_page(page)


def build_condition_page(pdf: Pdf) -> None:
    page = ""
    page += draw_text(44, 792, "실제 구현 조건 상세 목록", 18)
    page += draw_text(44, 768, "음성설정 표에 묶이지 않고, 코드가 판단해야 할 논리 순서대로 다시 정리한 목록이다.", 9)

    columns = [(44, 736), (304, 736)]
    per_column = 15
    for column_index, (x, start_y) in enumerate(columns):
        y = start_y
        start = column_index * per_column
        for index, item in enumerate(IMPLEMENTATION_CONDITIONS[start : start + per_column], start=start + 1):
            text, y = wrapped_text(x, y, f"{index}. {item}", max_chars=33, size=7, line_height=10)
            page += text
            y -= 7

    page += rect(44, 56, 506, 42, fill="0.96 1 0.96", stroke="0.22 0.65 0.36")
    page += draw_text(58, 80, "구현 기준", 10)
    page += draw_text(58, 65, "조건 추가는 후보 생성, 충돌 정리, VoiceRequest 생성 중 어느 단계인지 먼저 정한 뒤 진행한다.", 8)
    page += page_number(3)
    pdf.add_page(page)


def build_priority_page(pdf: Pdf) -> None:
    page = ""
    page += draw_text(44, 792, "전체 조건 우선순위", 18)
    page += draw_text(44, 768, "침공, 일반, 고정보스, 테스트 조건이 동시에 움직일 때 중앙 큐가 비교할 기준이다.", 9)
    page += draw_text(44, 750, "핵심 원칙: 원본 종류보다 재생 목적과 시간 민감도를 먼저 보고, 침공은 문구와 채널만 별도 처리한다.", 9)

    headers = ["순위", "대상", "처리 기준"]
    widths = [36, 122, 348]
    x0, y0 = 44, 718
    x = x0
    for width, header in zip(widths, headers):
        page += rect(x, y0, width, 24, fill="0.88 0.96 1", stroke="0.6 0.72 0.84")
        page += draw_text(x + 5, y0 + 8, header, 8)
        x += width

    y = y0 - 24
    for rank, target, rule in PRIORITY_RULES:
        row_height = 28
        page += rect(x0, y, widths[0], row_height, fill="1 1 1", stroke="0.82 0.86 0.9")
        page += rect(x0 + widths[0], y, widths[1], row_height, fill="1 1 1", stroke="0.82 0.86 0.9")
        page += rect(x0 + widths[0] + widths[1], y, widths[2], row_height, fill="1 1 1", stroke="0.82 0.86 0.9")
        page += draw_text(x0 + 8, y + 10, rank, 7)
        page += draw_text(x0 + widths[0] + 5, y + 10, target, 7)
        text, _ = wrapped_text(x0 + widths[0] + widths[1] + 5, y + 15, rule, max_chars=58, size=7, line_height=9)
        page += text
        y -= row_height

    page += rect(44, 62, 506, 50, fill="1 0.97 0.9", stroke="0.85 0.45 0.12")
    page += draw_text(58, 94, "침공 처리 기준", 10)
    page += draw_text(58, 78, "침공은 별도 예외 덩어리가 아니라 source가 침공인 이벤트다. 즉시젠, n분전, 겹침 규칙은 공통 우선순위 안에서 비교한다.", 7)
    page += page_number(4)
    pdf.add_page(page)


def build_pdf() -> None:
    pdf = Pdf()
    build_overview_page(pdf)
    build_catalog_page(pdf)
    build_condition_page(pdf)
    build_priority_page(pdf)
    pdf.write(OUT)


def find_browser() -> str:
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Naver" / "Naver Whale" / "Application" / "whale.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Naver" / "Naver Whale" / "Application" / "whale.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Naver" / "Naver Whale" / "Application" / "whale.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return os.fspath(candidate)
    for executable in ("chrome.exe", "whale.exe", "chromium.exe"):
        resolved = shutil.which(executable)
        if resolved:
            return resolved
    return ""


def build_pdf_with_browser() -> bool:
    browser = find_browser()
    if not browser or not HTML.is_file():
        return False
    OUT.parent.mkdir(parents=True, exist_ok=True)
    user_data_dir = tempfile.mkdtemp(prefix="boss_timer_pdf_")
    try:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--no-sandbox",
            "--no-first-run",
            f"--user-data-dir={user_data_dir}",
            f"--print-to-pdf={OUT}",
            HTML.as_uri(),
        ]
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        return result.returncode == 0 and OUT.is_file()
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        shutil.rmtree(user_data_dir, ignore_errors=True)


if __name__ == "__main__":
    if not build_pdf_with_browser():
        build_pdf()
    print(OUT)
