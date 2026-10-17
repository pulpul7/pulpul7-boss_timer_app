"""File-level distribution choices, shared by the master UI and runtime."""
OPTIONS = (
    ('settings', '프로그램', '일반 환경설정', 'init/default_settings.ini', '화면·글꼴·공통 표시 및 입력 옵션'),
    ('boss_definition', '보스 스케줄', '보스 설정', 'init/schedule_boss_definitions.txt', '보스 이름·약칭·사용·별·막타·색상과 색상 표시 옵션'),
    ('areas', '보스 스케줄', '지역 / 챕터 설정', 'init/schedule_area_definitions.txt', '지역 이름·챕터 구분과 순서'),
    ('fixed', '보스 스케줄', '고정보스 설정', 'init/schedule_fixed_bosses.txt', '이름·요일·시간·반복·사용·색상·연결 ID와 색상 표시 옵션'),
    ('metrics', '보스 스케줄', '보스 시간 / 점수 설정', 'init/schedule_boss_metrics.json', '보스별 소요시간·점수·쟁 점수'),
    ('breaks', '보스 스케줄', '휴식 설정', 'init/schedule_break_rules.json', '휴식 구간·문구·길이·글꼴·색상'),
    ('alarm', '알림 / 음성', '보스 알람 설정', 'init/default_schedule_alarm_settings.json', '알람 사용·초읽기·시각·보스별 알람·안내음'),
    ('voice', '알림 / 음성', '음성 합성 설정', 'init/default_edge_tts.ini', '음성 종류·속도·높낮이·음량'),
    ('notice', '알림 / 음성', '알리미 설정 / 안내 문구', 'init/default_notice_settings.json', '알리미 사용·유형·시간대·문구만 적용. 공지와 송출 이력 유지'),
    ('notice_module', '알림 / 음성', '알리미 실행 코드 · 이번 배포본 사용', 'init/default_notice_module.json', 'AppData에 설치한 알리미의 우선 선택을 1회 해제하고 내장 코드를 사용. 개인 설정·공지·송출 이력·설치 파일 보존'),
    ('music', '알림 / 음성', '음악 검색 설정', 'init/default_background_music_settings.json', '기본 음악 검색어 목록'),
    ('spectrum', '웹', '웹 보스 스펙트럼', 'web_assets/boss_spectrum.json', '보스별 공통 테마 색상 번호'),
    ('font_sizes', '웹', '웹 글꼴별 기본 크기', 'web_assets/fonts/size_defaults.json', '글꼴마다 저장한 기본 크기. 개인 글꼴과 선택 유지'),
)
OPTION_IDS = {row[0] for row in OPTIONS}
PROFILE_GROUPS = {'boss', 'boss_definition', 'areas', 'fixed', 'metrics', 'breaks', 'alarm', 'voice', 'notice'}
ROLLBACK_GROUPS = {'boss_definition':'boss', 'areas':'boss', 'voice':'alarm'}


def file_selection(groups):
    result = set(groups)
    if 'boss' in result:
        result.remove('boss');result.update(('boss_definition', 'areas'))
    return result & OPTION_IDS
