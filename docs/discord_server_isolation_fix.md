# 디스코드 서버 간 연결 간섭 수정 (2026-09-15)

## 확인된 원인

`on_voice_state_update`가 봇 사용자 ID만 검사하고 이벤트의 서버를 검사하지 않았다.
같은 봇을 서로 다른 디스코드 서버에서 사용하면 다른 서버의 입장·이동 이벤트가
이 PC의 `voice_channel_id`에 저장되고, 다른 서버의 퇴장도 이 PC의 연결 끊김으로 처리됐다.
서버 ID는 그대로여서 다음 접속은 서버/채널 불일치로 실패하며 자동 재접속을 반복했다.

로컬 로그 2026-09-15 04:46:46의 `voice_channel_moved_externally` 직후
04:46:49에 서버 ID와 음성채널 서버가 다르다는 `voice_connect_failed`가 기록되어 있다.
이 로그는 채널 오염 경로의 근거이며, 별도 봇으로 시험한 상대 PC의 토큰 변경 여부까지
확인한 것은 아니다.

## 변경

- 본인 봇 + 담당 서버의 이벤트만 처리한다. 퇴장도 `member.guild`로 판별한다.
- 같은 서버 내 정상적인 채널 이동 저장은 유지한다.
- Discord 로그인 후, Gateway 접속 전 `setup_hook`에서 인증된 Application ID를
  설정과 비교한다. ID만 바꾸고 기존 토큰을 사용하는 경우 음성 접속 전 차단한다.
- 토큰은 로그에 남기지 않는다. 인증 검사는 discord.py의 기존 로그인 결과를 사용한다.
- 설정 오류는 상태 응답의 `configuration_error`로 전달한다. 잘못된 설정으로
  봇과 GUI가 자동 재접속을 반복하지 않는다. 수정 후 사용자가 봇을 다시 실행한다.

## 배포 및 사용자 확인

실행 파일/ZIP은 생성하지 않았다. 사용자가 빌드할 때 GUI와 디스코드 봇 실행 파일을
함께 갱신해야 한다. 이미 오염된 설정은 임의로 추측해 덮어쓰지 않는다.
봇 설정에서 담당 서버 ID와 그 서버의 음성채널 ID를 확인해 다시 저장하고 재실행한다.
새 봇을 사용한다면 새 봇의 토큰과 Application ID를 함께 입력한다.

서로 다른 PC·서로 다른 Discord 서버의 음성 이벤트 격리 수정이다.
같은 PC에서 여러 GUI를 동시에 실행하는 경우의 공용 로컬 포트/파일 분리는
이번 수정 범위에 포함하지 않는다. 같은 봇을 같은 Discord 서버에서 동시에
운영하는 충돌 방지도 별도의 소유권 조정이 필요하다.

## 비빌드 검증

`python -B -m unittest test_discord_server_isolation test_discord_voice_commands test_discord_notice_output test_discord_connection_visual test_voice_bridge_receipts`

실제 Discord 로그인, 음성 송출, 사용자 설정 쓰기 없이 가짜 이벤트와 인증 결과로 검증한다.
