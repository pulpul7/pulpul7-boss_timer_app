# 실제 UI 인터랙티브 튜토리얼

스케줄 메인 화면 우측 하단 **튜토리얼**에서 13개 항목을 선택합니다.
처음에는 **1. 빠른 시작 → 3. 이후 갱신 → 12. 복구**를 권합니다.
모든 설정을 외우는 대신 평소 작업인 `스샷 → OCR → 확인 → 적용`을 알려줍니다.

## 화면과 진행

- 별도 연습 화면을 만들지 않습니다. 현재 프로그램의 실제 버튼·입력창·설정창·스케줄표를 강조합니다.
- 빨간 테두리 안쪽과 테두리 클릭은 설명창의 **다음**과 같은 이동 함수를 호출합니다. 기존 버튼 동작을 호출하지 않습니다.
- 강조한 컨트롤은 밝게 남기고 주변은 반투명 가림막으로 어둡게 표시합니다. Windows에서는 기존 감지영역 표시와 같은 투명 Canvas에 사각형을 그립니다. 테두리는 빨간색 4픽셀이며 약 0.6초 간격으로 표시 / 숨김을 반복합니다. 버튼·글자·주변 가림막은 점멸하지 않습니다.
- 창 열기 버튼을 누르면 실제 창이 열리고 그 창의 다음 대상에 안내가 이어집니다.
- 강조 영역 클릭 / 이전 / 다음 / 종료 / 처음부터 / 목록 / 단계 선택을 지원합니다.
- 설명창의 **다음**은 해당 작업을 실행하지 않고 다음 설명으로 이동합니다.
- 실제 화면 이동이나 컨트롤 재생성에 따라 위치를 다시 계산합니다. 기존 위젯의 배치·색·부모는 변경하지 않습니다. 표시창은 위쪽에 유지하되 다른 프로그램으로 전환하면 숨깁니다.
- 메뉴 관리 항목은 메인 버튼 안내 다음에 실제 열린 창의 개요를 보여줍니다.
- 선택한 알림·복구 기록 등 필요한 자료가 없으면 임의의 샘플을 만들지 않고 다음으로 진행하도록 안내합니다.

## 실제로 실행하는 작업과 보호 범위

`tutorial_live_ui.py`의 허용 목록에 있는 **창 열기·탭 이동**만 단계 준비 과정에서 실행합니다.
빨간 표시를 클릭하면 설명창의 **다음**과 똑같이 다음 설명으로 이동합니다.
캡처·OCR·적용·삭제·복구 실행·등록·저장·초기화·동기화·업로드·송출 권한 변경은 실행하지 않습니다.
행 선택이나 체크 값 변경도 하지 않습니다. 원래 작성 중인 입력과 스샷은 유지합니다.
입력창은 초기화하는 일반 진입 경로 대신 기존 UI 생성 함수를 사용해 엽니다.
컷 안내는 실제 표의 선택한 행 또는 보이는 행의 컷 칸을 강조합니다.

- `_apply_schedule_input_batch`도 안내 중에는 스케줄 적용을 차단합니다.
- 초단위 캡처는 튜토리얼에서 시작한 작업인지 기억하고 자동 적용을 사용하지 않습니다.
  기존 자동 적용 기본 설정을 바꾸거나 저장하지 않습니다. 진행창에서 체크해도 실제 적용하지 않습니다.
- 전역 F2는 안내 중 차단하고, 캡처 도구의 자동 표시·전역 단축키 처리는 잠시 멈춥니다.
- 기본 앱 키·마우스 이벤트 앞에 안내 전용 bindtag를 붙이고 종료 시 그 태그만 제거합니다.
  기존 바인딩과 가상 선택 이벤트, 백그라운드 알람·봇 연결·자동 수집은 유지합니다.

## 실제 창 수명과 모달 창

창 열기 호출은 Tk의 예약 콜백으로 실행합니다. 기존 `wait_window`가 실행 중이어도
안내의 위치 확인이 계속 동작합니다. 알려진 실제 안내 대상 창의 grab만 잠시 해제하고,
다른 확인 / 오류 / 측정 진행창이 grab을 얻으면 안내를 가리고 그 창에 먼저 응답하도록 합니다.

이번 안내에서 연 모달 창은 해당 설명을 떠날 때 확인 버튼을 호출하지 않고 닫습니다.
따라서 기존 대화상자는 취소 결과로 반환하며 스케줄·설정 반영을 수행하지 않습니다.
기존 비모달 창은 종료 시 시작 전 표시 상태로 돌립니다. 기존 알리미 탭도 되돌립니다.
새로 생성한 재사용 가능한 비모달 창은 숨겨 두며 정상 UI에서 다시 열 수 있습니다.

대상 창의 닫기 버튼은 안내 동안 안내 종료로 연결해 기존 입력창 닫기의 초안 삭제를 피합니다.
종료 후 원래 닫기 동작을 복원합니다. 
가림막·설명창·예약된 안내 콜백·전용 이벤트 태그를 정리합니다.

## 소스 구성

| 파일 | 역할 |
| --- | --- |
| `interactive_tutorial.py` | 목록·설명창·가림막·클릭 진행·실제 창 탐색·정리 |
| `tutorial_live_ui.py` | 실제 UI 주소·창 열기 허용 목록·대상 컨트롤 탐색 |
| `tutorial_content.py` | 13개 항목의 설명과 순서·현재 소스에 맞춘 문구 |
| `boss_timer_gui.py` | 하단 버튼·메인 대상 등록·지연 import·캡처/적용 보호 |
| `precision_capture_ui.py` | 튜토리얼 측정의 자동 적용 차단 |

튜토리얼을 누르기 전에는 모듈을 읽거나 창을 준비하지 않습니다.
추가 네트워크 폴링이나 운영 스레드를 만들지 않습니다.
실제 창의 원래 데이터 조회·음성 준비·공지 수집 동작은 해당 기능의 기존 경로를 따릅니다.

## 설명문 근거

분석 기준: 2026-10-06의 작업 소스. 함수 이름은 `boss_timer_gui.py` 기준이며
별도 파일을 함께 기재했다. UI가 바뀌면 튜토리얼 문구와 대상도 함께 검토해야 한다.

| 항목 | 주된 소스 근거 | 학습 내용 |
| --- | --- | --- |
| 1. 빠른 시작 | `_ensure_schedule_input_window`, `_open_schedule_input_window_normal`, `_apply_schedule_input_batch`, `_undo_schedule_last_import`, 스케줄 체인 생성 코드 | 캡처 → OCR_1 → 확인 → 적용, 첫 입력 기준, 체인과 고정보스 자동 표시 |
| 2. 초단위 | `precision_capture_ui.py`, `precision_capture_session.py`, `precision_time_tracker.py`, `schedule_precision.py` | 65초 관찰, 숫자 변화와 확인, 여러 보스 측정, 초확정, 기본 5회/초 |
| 3. 이후 갱신 | `_should_include_schedule_input_ocr_render_entry`, `_on_schedule_input_ocr_filter_toggle` | 24시간이내는 OCR 결과 필터이며 기존 행 잠금 기능은 아님 |
| 4. 메인 옵션 | `_ensure_schedule_window`, `_on_schedule_break_rows_enabled_changed`, `_toggle_schedule_alarm_master`, `_apply_schedule_alarm_global_options` | 휴식 표시, 초읽기, 보스색, 고정보스색, 전체 알람 |
| 5. 표 관리 | `_open_schedule_input_window_for_edit`, `_delete_selected_schedule_rows`, `_restore_deleted_schedule_rows`, `_on_schedule_tree_button_release`, `_on_schedule_tree_*_key`, `_on_schedule_tree_right_click` | 수정·수정취소, 컷·컷취소, 행 삭제·복구, 같은 체인 선택, 우클릭 선택 해제 |
| 6. 공유 | `_show_schedule_share_period_dialog`, `_copy_schedule_share_image_to_clipboard`, `_open_schedule_share_text`, `schedule_share_text.py` | 기간과 복사 옵션, 이미지 붙여넣기, TXT 복사 및 입력창 편집 |
| 7. 관리 버튼 | `_ensure_schedule_window`, `_save_schedule_second_precision_offset`, `_reset_schedule_second_precision_offset` | 상단 관리·공유·표 조작·배경음악·초 보정의 기능/사용 시점/평소 필요 여부 |
| 8. 보스 정보 | `_ensure_schedule_boss_config_window`, 보스 등록/저장 처리, `_ensure_fixed_boss_window` | 이름·약칭·젠주기·지역, 등록과 저장, 고정 시간과 요일 |
| 9. Discord | `open_discord_bot_settings_window`, `discord_command_help.py`, GUI/봇 권한·승계 처리 | 최초 설정, 안내채팅 공용 사용, 봇 연결과 송출 권한 분리, 관리자 이름/ID 요청 |
| 10. 음성 | `_ensure_schedule_alarm_window`, `_open_edge_tts_settings_window`, 초읽기 음성 준비·출력 코드 | 녹음/Edge TTS, 필요할 때만 모듈 설치, 사전 준비된 초읽기, 로컬 미리듣기 |
| 11. 알리미 | `notice_module/payload/notice_management_ui.py`, 문구 기본 설정 UI, `notice_runtime.py`, `notice_output_bridge.py`, Discord 알리미 출력 | 수집 원문, 유효기간, 사용/중지, 문구 편집, 폐기·30일 이력, 일반 알림 우선순위 |
| 12. 복구 | `_undo_schedule_last_import`, `_undo_schedule_last_edit`, `close_schedule_input_window`, `_show_schedule_delete_history_dialog`, `_open_settings_rollback_dialog`, 시즌 이어가기, `_restore_schedule_alarm_voice_test_backup_with_confirm`, `restore_discarded_log_record`, 알리미 문구 복원, `ai_module_updater.py` | 최근 입력/수정 취소, 최근 15개 기록, 설정·시즌·모듈·음성 테스트·로그 후보·문구 복구의 범위 |
| 13. 자동 처리 | 체인/고정보스 렌더링, 알리미 점검 반영, 프로필 저장소, `ai_update_center.py`, `ai_module_updater.py` | 자동 일정·공지·충돌 처리, AppData 보존, 공지/AI 모듈 업데이트 |

Discord 설정값을 가져오는 방법은 공식 자료도 확인했다:
[ID 복사](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID),
[앱·토큰 설정](https://github.com/discord/discord-api-docs/blob/main/developers/quick-start/getting-started.mdx).
공식 링크는 해당 단계의 자세히 보기에도 안내한다.

## 사용자가 확인할 항목

실제 화면 테스트와 빌드는 사용자가 진행합니다.

1. 스케줄 입력 버튼 강조 → 실제 입력창 → 캡처 도구 → OCR → 결과 확인 설명 순서가 이어지는지.
2. 이미 작성 중인 입력과 스샷이 있으면 유지되고 캡처 / OCR이 안내 중 차단되는지.
3. 실제 창을 이동하거나 최소화 / 복원할 때 빨간 테두리와 밝은 영역이 대상을 따라가는지.
4. 적용 / 삭제 / 컷 / 복구 / 저장 / 초기화 / Discord 실행을 눌러도 설명만 진행하는지.
5. 초단위 캡처의 자동 적용 기본값이 켜져 있어도 스케줄이 바뀌지 않는지.
6. 복사·시즌·기록 불러오기 같은 모달 창에서도 설명창의 이전 / 다음 / 종료가 가능한지.
7. 빨간 테두리 안쪽 / 테두리 / 설명창의 다음이 동일하게 진행하며 테두리만 점멸하는지.
8. 종료 / 목록 / 처음부터 / 창 닫기 후 가림막과 입력 차단이 남지 않는지.
9. 종료 후 기존 창 상태와 알리미 탭·단축키가 복원되고 작성 중인 입력 / 스샷이 유지되는지.
10. 반복 실행해도 캡처 도구나 이전 안내 대상 창이 다시 튀어나오지 않는지.

비빌드 검증은 Python 구문, 실제 UI 연결 경로, 허용 목록, 보호 조건과 가림막 기하 계산을 확인합니다.
소스 검증을 실제 Tk 화면 또는 배포 EXE의 실행 검증으로 간주하지 않습니다.

배경 창을 어둡게 하는 가림막에서는 현재 안내 창과 설명창의 전체 영역(제목줄·테두리 포함)을
제외합니다. 가림막이 최상위 창이어도 겹친 현재 안내 영역을 회색으로 덮지 않습니다.
단계 전환 시 이전 가림막을 모두 정리하고, 비활성 창이 설명 대상이 되면 그 창의 전체
가림막을 즉시 제거합니다. 시작 전에 숨겨져 있던 재사용 창은 해당 단계를 떠날 때 다시 숨기며
처음부터 열려 있던 창의 상태는 종료 시 복원합니다. 컨트롤을 찾지 못한 경우에는 해당 안내
창 전체를 밝게 표시하고 설명창에 그 사실을 알립니다.

테두리 확인용 내부 로그: `tutorial_target view=... key=... found=... fallback_window=... bounds=... target=...`. 대상 이름·좌표만 기록하고 입력 값과 토큰은 읽지 않습니다.
