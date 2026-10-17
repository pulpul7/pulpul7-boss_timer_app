# 수동 빌드 전 확인

현재 빌드 점검의 기준 문서다. AI 탐색 진입점은 `CODEMAP.json`의 `release_preflight`이며,
업데이트·복구 기준은 `UPDATE_POLICY.md`를 따른다. 아래 날짜가 붙은 과거 기록은 현재 통과 판정이 아니다.

사용자 지시 전에는 자동으로 EXE/ZIP/테스트 빌드를 생성하지 않는다.
새 폴더에서 검증해야 할 경우에도 먼저 필요성만 알린다.

## 확정 전 확인 순서

1. 변경 범위: `git status`/차이를 읽고 이번에 포함할 변경·알려진 미완료를 구분한다.
   체크아웃에 새로 추가한 소스·리소스가 빠지지 않았는지 확인한다. 정리 목적으로 사용자 파일을 삭제하지 않는다.
2. 버전: `release_version.resolve_release_version`의 실제 결과와 전체 패키지의 `version_key` 지원 여부를 확인한다.
   spec fallback만 보고 확정하지 않는다. Git 태그는 확인만 하며 임의 생성·이동하지 않는다.
3. 배포선언: `distribution_defaults.read_declaration`으로 선택 원본·해시를 검증한다.
   이번에 덮어쓸 항목만 선택됐는지, 현재 값·배포 기본값·시즌 롤백 기준점을 혼동하지 않았는지 확인한다.
4. 알리미: 내장 모듈 버전과 별도 설치 버전의 우선순위를 확인한다.
   이번 내장 코드를 쓰게 할 배포라면 `알리미 실행 코드 · 이번 배포본 사용` 선언이 필요하다.
   `알리미 설정 / 안내 문구` 덮어쓰기는 별개다. 세부 기준은 `docs/ai_module_updates.md`를 따른다.
5. 패키징: 본체/봇 양쪽 spec, FFmpeg, Python 의존성과 Tcl/Tk 리소스를 확인한다.
   동적 알림 모듈·웹 파일·폰트·음성·`UPDATE_POLICY.md` 포함 여부와 사용자 자료 제외를 점검한다.
   현재 GUI spec은 `user_voice`도 수집한다. 음성·TTS 캐시의 실제 배포 허용 여부는 별도로 확인한다.
   파일 이름 기반 제외 검사는 음성 내용 검토를 대신하지 않는다. 확정 버전의 ZIP이 이미 있는지도 빌드 전에 읽기 전용으로 확인한다.
6. 웹: `web_assets` 원본과 Python 내장 복사본, `metadata.json`과 fallback 차수,
   선택된 페이지 버전·필요한 불변 리소스 해시의 일치를 확인한다.
   원본을 수정하지 않고 생성기/spec을 실행해 보는 방식은 검사로 사용하지 않는다.
7. 변경 기능 검증: 아래 비빌드 검사 중 관련된 묶음을 선택하고 실패·생략 이유를 기록한다.
   입력 순서는 GUI/Discord 공통 적용 → 저장/재읽기 → 재정규화/주기 연장 → 웹 검증/화면/복사까지 확인한다.
8. 알려진 차이: `UPDATE_POLICY.md` 8절과 `DEBUG_NOTES.json`의 미해결 항목을 확인한다.
   과거 통과 기록을 이번 소스의 검증 결과로 복사하지 않는다.

비빌드 검증 통과, 사용자 수동 빌드 완료, 배포 EXE 실측 완료는 각각 별도 단계다.
검사를 통과해도 EXE가 정상 동작하거나 자동 업데이트까지 실측됐다고 표시하지 않는다.

## 비빌드 검사 선택표

기존 테스트를 필요한 범위만 실행한다. spec 전체 실행·빌드·설정 승격·배포선언 저장·실제 업데이트는 포함하지 않는다.
경로·명령을 바꾸었으면 해당 검사와 관련 회귀 검사까지 확인한다. 단순 문서 수정에 전체 검사를 반복할 필요는 없다.

| 확인 대상 | 진입 검사 |
| --- | --- |
| 버전·메타데이터·패치내역·리소스 제외 | `test_release_preflight`, `test_release_version`, `test_build_metadata_display`, `test_app_patch_notes` |
| 선언 선택·AppData 우선순위·모듈 선택 | `test_distribution_selection`, `test_distribution_defaults`, `test_notice_runtime` |
| 같은 시각 입력 순서·소수점·스케줄 복사 | `test_schedule_input_order`, `test_schedule_multi_boss_input`, `test_schedule_fractional_time`, `test_schedule_share_text` |
| 서버/시즌 자료 보존 | `test_schedule_server_profile_switch`, `test_schedule_profile_migration`, `test_new_season_schedule` |
| Discord 실행·종료·권한 | `test_discord_launch_command`, `test_discord_shutdown`, `test_discord_bot_process_recovery`, `test_discord_authority_events` |
| 웹 유지·승계·페이지 적용 | `test_web_persistent_runtime`, `test_web_delegated_hosting`, `test_web_asset_selection`, `test_web_page_update_completion` |
| 전체 업데이트·실패 복구 | `test_program_update_sequence`, `test_web_discord_control` |
| 알림 모듈 시작·복구·설정 보존 | `test_ai_module_updater`, `test_notice_runtime` |
| 웹 화면·테마·알림 | `test_web_schedule_views.js`, `test_web_theme_performance.js`, `test_web_unified_notifications.js` |

Python 묶음 예: `python -B -m unittest test_release_preflight test_release_version test_build_metadata_display`.
JS 파일 일부는 `source` 등의 인자를 받는 함수 본문 또는 비동기 본문이다. 파일을 무조건 단독 실행하지 말고
매개변수·실제 원본/내장 스크립트·오프라인 fixture를 맞춘 검사기로 실행한다. 인터프리터 부재를 통과로 기록하지 않는다.
단위 테스트의 임시 가짜 자료는 실제 배포·사용자 설정·실제 프로세스 조작과 구분한다.

## 수동 빌드 후 배포 확정 전 실측

- 본체·봇이 모두 새 산출물인지, EXE 표시 버전과 ZIP 이름·매니페스트 버전·각 EXE 해시가 일치하는지 확인한다.
- 기존 바로가기와 활성 버전 선택, 첫 실행·재실행·중복 실행, AppData의 서버/시즌/스케줄·음성·인증 정보 보존을 확인한다.
- 같은 시각 복수 보스 입력을 GUI와 Discord 양쪽에서 확인하고 새로고침·재실행·웹·복사에서도 순서를 확인한다.
- 봇 접속·송출권·채팅 안내·음성, 관리자 간 인계와 서로 다른 웹 담당자 환경을 확인한다.
- 본체 종료/재시작 중 기존 웹 공개·로그인 유지와 새 본체의 기존 웹 채택을 확인한다.
  살아 있는 구버전 웹은 새 본체 배포만으로 교체되지 않는다. 웹 코드 변경이 있으면 명시적인 웹 갱신 경로도 확인한다.
- 전체 자동 업데이트를 배포 기능으로 확정하려면 실제 EXE의 정상 전환·새 봇 시작·실패 롤백·중간 종료 복구를 별도로 실측한다.
  설치 성공과 Discord 로그인/인터넷 연결 성공, 알리미 시작 완료를 구분한다.
- 자동 업데이트 ZIP은 `bosstimer_update_버전.zip`이고 표준 이름의 두 EXE와 `program_update.json`을 포함해야 한다.
  웹 ZIP은 아직 현재 업데이터의 입력이 아니므로 이름만 정해졌다는 이유로 지원한다고 안내하지 않는다.

실제 운영 자료를 테스트용으로 지우거나 새 테스트 배포 폴더를 임의로 만들지 않는다.
이번 확정 작업의 날짜·검사 범위·결과·생략·남은 실측은 `DEBUG_NOTES.json`에 남긴다.

## 현재 반영된 기본값

- 초당 정밀 캡처 기본값: 5회. 허용 범위: 2~10회.
- `boss_timer_settings.ini`, `init/default_settings.ini`: `precision_capture_rate = 5` 확인.
- `precision_debug_logging = false`: 상세 샘플 로그의 파일 출력과
  `precision_capture_latest.json` 신규 저장을 끈다. 메모리 계측 결과와 소수점 원본은 유지한다.
- 오류/완료 요약 로그는 유지한다. 상세 진단이 필요할 때만 로컬 INI의 해당 값을 `true`로 바꾸고 재시작한다.
- 배포 기본값은 5회, 상세 로그 OFF, 영역 미리보기 OFF로 지정했다.
  기존 사용자가 저장한 설정은 자동으로 덮어쓰지 않는다.

## 빌드 전 사용자 확인

1. 배포할 설정은 관련 설정창의 저장을 완료하고 배포선언에서 대상과 원본을 확인한다.
   빌드 전 점검만을 위해 실행 중인 본체·봇·웹을 임의 종료하지 않는다. 실제 업데이트의 종료·복원은 정책 순서를 따른다.
2. `boss_timer_gui.spec`의 `BUILD_VERSION`, `BUILD_LAST_UPDATED`를 확인한다.
   배포 폴더명은 화면 버전을 결정하지 않는다. 소스에 연결된 가장 가까운 `v숫자.숫자.숫자`
   프로그램 Git 태그를 GUI 빌드와 ZIP 이름에 함께 사용한다. 다른 브랜치나 모듈 태그는 사용하지 않는다.
   Git 정보가 없는 소스에서는 `BUILD_VERSION`을 사용하고 `DEFAULT_APP_VERSION`도 같은 번호로 맞춘다.
   `build_metadata.json`은 GUI 빌드 과정에서 생성되어 EXE 안에 포함된다.
   옆에 둔 JSON이나 폴더명만 바꿔도 기존 EXE 안의 버전은 바뀌지 않는다.
3. 루트의 `ffmpeg.exe`와 빌드용 Python 의존성이 준비됐는지 확인한다.
4. 차임벨/음성/보스 기본값은 `init`에 명시적으로 확정한 파일을 사용한다.
   선언하지 않은 항목의 빌드용 기본값 수집은 활성 서버 프로필을 참조할 수 있으므로 확인한다.
   선언한 대상은 검증된 배포 원본을 사용한다.
   실제 서버별 스케줄/토큰을 배포 폴더에 복사하지 않는다.
5. 사용자가 직접 실행할 통합 명령: `python build_release.py`.
   Discord 봇 → GUI 순서로 빌드하고 `dist`에 두 EXE와 `program_update.json`을 포함한 ZIP을 만든다.
   EXE 산출물은 다시 생성될 수 있으므로 사용자가 기존 배포본을 보관한다.
   같은 이름 ZIP은 덮어쓰지 않고 생성에 실패한다. 임의 삭제·이름 변경으로 검사나 버전 규칙을 우회하지 않는다.
6. 배포는 두 EXE 또는 생성된 ZIP만 전달한다. 저장소 전체를 ZIP으로 묶지 않는다.
   다른 폴더로 옮길 때도 GUI EXE와 `boss_timer_discord_bot.exe`를 같은 폴더에 함께 둔다.
   GUI EXE 이름에 버전을 붙여도 되지만, 봇 EXE 이름은 그대로 유지한다.
   이는 수동 실행 폴더의 진입 EXE 이름에 관한 설명이다. 자동 업데이트 ZIP 내부 EXE 이름은 바꾸지 않는다.
   GUI EXE만 복사하면 봇 자동 연결과 수동 연결 모두 실행 파일 누락으로 실패한다.

## 로그와 정리 범위

- GUI/봇 패키징 모두 리소스 내부의 `.log`, `.jsonl`, `.tmp`, `.pyc`,
  `precision_capture_latest.json`을 제외한다.
- 기존 로그를 삭제해야 빌드되는 구조는 아니다. 과거 진단 로그와 계측 JSON은 보존했다.
  음성 기록/서버 기록 등 사용자 데이터는 로그처럼 보여도 임의 삭제하지 않는다.
- 정밀 캡처 테스트 EXE 폴더 5개, TTS/음성 명령 테스트 배포 폴더 2개와
  관련 기능별 중간 빌드 폴더를 포함해 총 20개 폴더(1755.9MiB)를 영구 삭제했다.
- 기존 `dist`, 일반 `build`/`build_current`/`build_release_*`, 소스, 설정, 음성은 유지했다.
  `build_precision_capture`에는 계측 기록과 참고용 스크립트만 남겼다.
- 테스트 빌드 폴더에 대한 예전 대화의 다운로드 링크는 더 이상 유효하지 않다.

## 새 폴더 테스트의 한계

새 폴더에 EXE를 복사해도 `%APPDATA%/BossTimer`의 기존 설정을 공유할 수 있다.
따라서 새 폴더 실행만으로 최초 설치 테스트가 되지는 않는다. 필요 시 별도 사용자 환경을
권장하되, 현재 설정 삭제/초기화나 새로운 테스트 환경 생성은 별도 지시 없이 하지 않는다.

## 2026-09-11 현재 음성/보스 기준점 갱신

오9/season_17의 현재 알람, Edge TTS 음성, 보스/지역 정의, 고정보스, 보스 시간 보정을
롤백 기준점 및 `init` 배포 기본값에 반영했다. 알람과 보스 데이터의 원본 일치를 확인했다.
일반=비행기1, 고정=비행기3, 연속=비행기2이며 파일은 `wave`에 모두 존재한다.
`skip_countdown=true`는 사용자가 설정한 그대로 보존했다.

이전 롤백: `%APPDATA%/BossTimer/server_profiles/season_17/9/settings_rollback/before_baseline_refresh_20260911_034430_692628`.
이전 배포 기본값: `settings_seed_backups/20260911_034430_692628`.
보스/음성 관련 기본값은 빌드 시 다른 서버/오래된 런타임 미러로 덮어쓰지 않는다.
향후 현재 설정을 다시 기준점으로 만들 때만 `python promote_current_defaults.py`를 명시적으로 실행한다.
이 스크립트는 빌드하지 않으며 기존 기준점을 먼저 백업한다. 일반 앱 실행에서 자동 호출하지 않는다.

연결 상태 이미지 `assets/discord_plug_connection.png`는 assets 패키징에 자동 포함된다.

## 2026-10-03 시작 로딩·중복 실행 방지

- GUI 소스 실행은 초기화 전에 단계별 로딩창을 띄운다. 같은 사용자의 중복 실행은 기존 창을
  복원하고 종료한다. 최소화 상태와 일반/관리자 실행 조합도 사용자가 확인한다.
- GUI는 기존 `boss_timer_gui.spec`으로 수동 빌드한다. 새 Splash 대상과
  `assets/startup_loading.png`가 EXE 압축 해제 구간의 시작 안내를 제공한다.
  별도 Pillow 설치나 보조 EXE는 필요하지 않다. 이번 작업에서 빌드는 수행하지 않았다.
- TTS 캐시·프로필 검증 중복과 서버 표시 재호출을 줄였다. 기존 설정·스케줄·음성은 보존한다.
- 실제 첫 실행/재실행 시간, 로딩창, 중복 실행 복원과 첫 음성 알림은 사용자가 확인한다.
  세부 소스 변경은 `docs/startup_loading_analysis.md`에 기록했다.
