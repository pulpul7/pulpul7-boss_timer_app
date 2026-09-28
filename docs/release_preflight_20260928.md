# v5.5.0 배포 전 점검 (2026-09-28)

## 버전

- 마지막 실제 배포: `v5.3.1.fix` (사용자 확인).
- 이번 준비 버전: `v5.5.0`. GUI 표시, 빌드 spec, ZIP 이름 산출, seed 표식 일치.
- 업데이트 센터에는 `v5.5.0 · 배포 준비 중`으로 패치내역을 표시한다.
- 공지/AI 모듈 버전은 본체와 별개인 `1.0.0`을 유지한다.
- 이번 작업에서는 빌드·ZIP 생성·업로드·실제 TTS 송출을 하지 않았다.

## 기본값과 롤백

- 환경설정/시즌/로컬 데이터 AppData 이관과 현재 차임벨 기본값·롤백 반영: `docs/persistent_user_data.md`.
- 구형 배포본 사용자는 최초 한 번 기존 사용 폴더에서 수정본을 실행해야 구형 폴더의 시즌/길드 정보가 이관된다.
- 현재 프로필: `%APPDATA%/BossTimer/server_profiles/season_18/9`.
- 보스 목록과 지역/보스 색 설정: `init/schedule_boss_definitions.txt`, `init/schedule_area_definitions.txt`.
- 알리미 설정: `init/default_notice_settings.json`과 `notice_module/payload/notice_defaults.json`이 동일하다.
- 알리미 배포본에는 수집/사용 설정·문구·유형별 사용 여부만 포함한다. 공지 원문, 서버 ID, 실제 알림, 송출 이력은 복제하지 않는다.
- 현재 프로필 `settings_rollback/baseline`에도 보스 파일과 `notice_settings.json` 저장 및 해시 일치 확인.
- 변경 전 사본: `settings_seed_backups/20260928_notice_boss/init_before`, `rollback_before`. 배포 대상이 아니다.
- 기존 서버 설정은 실행만으로 덮어쓰지 않는다. 새 서버에서 기본값을 사용한다.
- 알리미 롤백은 별도 설정 백업 후 설정만 교체하며 이미 송출한 공지를 다시 재생하지 않는다.
- 과거 기준점에 알리미 사본이 없으면 롤백 창의 알리미 항목을 기본 선택하지 않는다. 필요하면 항목을 선택하고 `배포 기본값`으로 복구한다.

## 아침 보스 문구

- 시간순 정렬 후 첫 보스 기준 1시간 미만을 한 묶음으로 안내한다. 정확히 1시간부터 별도 묶음이다.
- 두 마리: `내일 아침 6시 43분 수르트, 미미르 일정이 있습니다. 많은 참여 부탁드립니다.`
- 세 마리: `내일 아침 6시 43분 수르트 외 2개 일정이 있습니다. 많은 참여 부탁드립니다.`
- 원본 보스별 시각과 새벽 안내 방식은 변경하지 않는다.

## 로그와 배포 포함 범위

- 배포 EXE의 상세 `boss_timer_debug.log`는 기본 비활성화. 조사 시 `BOSS_TIMER_DEBUG_LOG=1`로 켤 수 있다. 소스 실행은 기존처럼 활성화한다.
- 오류 로그와 Discord 운영 로그는 유지한다. 기존 로그는 삭제하지 않았다.
- `.log`, `.jsonl`, `.tmp`, `.pyc`, 캡처 디버그 JSON은 spec 수집에서 제외한다.
- `notice_data`, `server_profiles`, `settings_seed_backups`는 배포 수집 대상이 아니다.
- `discord_voice_queue.jsonl`은 통신용 파일이므로 불필요한 로그로 취급하여 삭제하면 안 된다.
- 새 알리미 기본값 JSON과 모듈 소스는 기존 `init`/`notice_module` 수집 경로에 포함된다.

## 빌드 전 확인사항 / 미완료

- 점검에 사용한 Python 3.12.10에서는 `truststore`를 찾지 못했다. GUI spec은 이 의존성이 없으면 빌드를 거부한다.
- 실제 빌드에 사용하는 환경에서 `python -m pip install -r requirements-gui.txt`를 먼저 실행할 필요가 있다. 이번 작업에서는 설치하지 않았다.
- PyInstaller, Discord, PyNaCl, FFmpeg, Tcl 리소스 존재는 확인했다. 실제 EXE 검증을 대신하는 결과는 아니다.
- 수동 빌드는 `python build_release.py` 경로로 GUI와 봇을 함께 생성한다. GUI만 교체하면 봇 명령 수정이 반영되지 않을 수 있다.
- **알리미 자동 송출 host 연결은 아직 미완료**다. 등록·문구 생성·로컬 미리듣기가 자동 Discord 송출 완료를 의미하지 않는다. `docs/notice_implementation_steps.md`의 후속 단계를 별도로 진행해야 한다.
- 인계 중에는 접속과 스케줄 적용이 모두 끝나기 전 송출을 금지하는 기존 조건을 유지한다.
- 새 PC 실제 실행, 게임 OCR, Discord 음성 및 GUI 시각 확인은 사용자의 수동 테스트가 필요하다.
