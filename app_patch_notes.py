"""Bundled app notes stay readable offline; unreleased changes have no version."""

UNRELEASED_NOTES = (
    "디스코드 사운드보드 명령·음성 목록을 전용 채팅채널로 분리했습니다.",
    "보탐 메시지 보관 수를 /보탐 0~50으로 설정합니다. 0은 무제한이며 설정을 저장합니다.",
    "/보탐 초읽기, /보탐 초읽기해제로 프로그램의 초읽기 사용 설정을 제어합니다.",
    "/보탐 ?에 현재 지원하는 전체 명령어와 입력 예시를 추가했습니다.",
    "소스 실행 시 예전 배포 EXE 대신 최신 디스코드 봇 소스를 사용하도록 수정했습니다.",
    "스케줄 입력·추가에서 '2120 그로아, 헤이드, 티르'처럼 같은 시간에 여러 보스를 입력합니다.",
    "여러 보스 입력에서도 소수점 초를 보존하고 동일 보스 중복을 경고합니다.",
    "업데이트 확인 창에 현재 프로그램 버전과 패치내역 버튼을 추가했습니다.",
    "업데이트 센터 탭을 전용 색상 버튼으로 바꿔 Windows 테마에서 선택한 탭의 글씨가 사라지는 문제를 수정했습니다.",
)

# When a version is confirmed, move its notes here; never guess a release number.
RELEASE_NOTES = ()


def patch_history_text(app_version: str, catalog=()) -> str:
    sections = ["다음 버전 준비 중\n버전 미확정 · 아직 배포하지 않은 변경사항\n\n"
                + "\n".join(f"• {note}" for note in UNRELEASED_NOTES)]
    sections.append(f"현재 프로그램: {app_version}\n아래 공지 / AI 모듈의 버전은 프로그램 본체 버전과 별개입니다.")
    for release in RELEASE_NOTES:
        sections.append(f"프로그램 {release['version']}\n" + "\n".join(f"• {note}" for note in release['notes']))
    if not RELEASE_NOTES:
        sections.append("프로그램 확정 버전별 상세 패치내역은 아직 등록되지 않았습니다.")
    for row in catalog:
        sections.append(f"공지 / AI 모듈 {row.get('version', '')} · {row.get('date', '')}\n"
                        f"{row.get('title', '')}\n\n{row.get('description') or '등록된 패치 설명이 없습니다.'}")
    return ("\n\n" + "─" * 44 + "\n\n").join(sections)
