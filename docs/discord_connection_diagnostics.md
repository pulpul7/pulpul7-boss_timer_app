# Discord 연결 진단 기록

`discord_bot.log`에 음성 연결의 종료 코드·예외 종류·SDK 연결 상태를 기록합니다.
기록 추가는 기존 승계, 재시도 횟수, 재접속 타이밍과 음성 송출 정책을 변경하지 않습니다.
Discord 채팅이나 보탐 로그 팝업으로 진단 이벤트를 전송하지 않습니다.

| 기록 | 의미 |
| --- | --- |
| `voice_sdk_websocket_interrupted` | 음성 소켓의 원래 종료/시간 초과 예외. `close_code`, `exception_type`, `failure_kind` 확인 |
| `voice_sdk_websocket_connect_start` / `opened` / `connect_failed` | SDK 소켓 접속 시도·성공·실패. `resume=1`은 세션 재개 시도이며 `opened`는 음성 입장 완료와 구분 |
| `voice_sdk_channel_changed` | SDK에 전달된 채널 변경. `left_channel=1`이면 채널 없음 이벤트 |
| `voice_sdk_server_update` | SDK에 음성 서버 업데이트가 전달됨. 단독으로 장애를 뜻하지 않음 |
| `voice_program_disconnect_requested` / `finished` | 프로그램에서 종료 요청. `program_reason`에 승계·재접속 전 정리 등 기존 내부 사유 |
| `voice_client_disconnect_requested` / `voice_guild_leave_requested` | 실제 음성 클라이언트 종료 요청과 서버에 퇴장 요청을 보내는 시점 |
| `voice_sdk_reader_cancelled` | 소켓 읽기 작업 취소. 원래 취소 예외를 그대로 전달 |
| `voice_disconnect_detected` | 기존 2초 간격 감시가 음성 연결 없음 상태를 처음 발견한 시점 |
| `voice_connection_recovered` | 감지 이후 복구까지의 시간. 실제 단절 시작 시각/누락 음성 길이와 같다고 단정하지 않음 |
| `voice_diagnostic_unavailable` | 사용 중인 SDK에서 관찰 지점을 사용할 수 없음. 기존 연결 동작은 계속 진행 |

`sdk_state`, `sdk_reader_active`, `sdk_connector_active`, `sdk_reconnect`,
`expected_disconnect`로 SDK의 연결·복구 진행 여부를 함께 확인합니다.
예외 메시지, 원문 패킷, 토큰, 세션 ID, 서버 주소는 이 진단에서 기록하지 않습니다.

Gateway의 `last_heartbeat_latency_sec`는 SDK가 계산한 왕복 시간이며
`latency_source=sdk`로 표시합니다. 아직 측정값이 없으면 `unknown`입니다.
`last_ack_age_sec`는 마지막 하트비트 응답을 관찰한 이후의 시간으로, 왕복 지연과 구분합니다.

SDK 연결 객체의 기존 메서드에 관찰만 추가합니다. 반환값과 원래 예외를 그대로 전달하며,
진단 기록 실패가 연결 처리를 막지 않습니다. 별도 네트워크 요청이나 폴링을 추가하지 않습니다.
프로그램·봇 재시작 후 적용되며 `discord_runtime_loaded ... voice_diagnostics=1`로 확인합니다.

## 음성 퇴장과 재입장 순서

서버별 퇴장 확인 상태는 SDK 음성 객체가 정리된 뒤에도 봇에 남겨 둡니다.
퇴장 요청 전 대기를 등록하고, 봇 자신의 채널 없음 이벤트를 받은 뒤에만 새 입장을 허용합니다.
기존 SDK 객체가 제거된 경우에는 전역 음성 상태 이벤트에서 확인합니다.
대기는 확인되는 즉시 끝나며 고정 지연을 넣지 않습니다.
3초 안에 확인되지 않으면 이번 입장을 보류하고 기존 감시 루프에서 다시 확인합니다.
이 대기만으로 실제 연결 재시도 횟수를 소모하지 않습니다.
시간 초과·작업 취소를 퇴장 확인으로 취급하지 않습니다.

같은 음성 연결의 중복 퇴장 요청과, 이미 교체된 연결의 뒤늦은 퇴장 요청은 전송하지 않습니다.
승계로 권한을 잃은 PC가 새 담당자의 연결을 끊지 않도록 기존 권한 검사도 유지합니다.

| 기록 | 의미 |
| --- | --- |
| `voice_join_waiting_for_leave` | 이전 퇴장 확인을 기다림 |
| `voice_leave_confirmed` | 봇 자신의 퇴장 이벤트 수신 |
| `voice_join_leave_wait_finished` | 확인 결과와 대기 시간. `confirmed=0`은 새 입장 보류 |
| `voice_guild_leave_duplicate_suppressed` | 같은 연결 또는 진행 중 요청의 중복 퇴장 차단 |
| `voice_guild_leave_suppressed` | 권한 없음 또는 이미 교체된 객체의 퇴장 차단 |
