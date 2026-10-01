# GitHub 데이터 연결 인증서 처리 (2026-10-01)

## 원인과 수정

`CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`는 HTTPS 인증서 체인을
검증하지 못한 오류다. HTTP 토큰 인증 결과(401/403)와 구분한다. 기존 GitHub 데이터 연결은
`urllib.request.urlopen`에 명시적 SSL 컨텍스트를 전달하지 않았고, TTS 모듈 다운로드에만
Windows 인증서 저장소를 쓰는 `truststore.SSLContext`가 적용되어 있었다.

- `https_transport.py`의 `create_verified_ssl_context()`를 TTS와 GitHub 데이터 연결에서 공유한다.
- Windows에서는 `truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)`를 사용하며
  `CERT_REQUIRED`와 호스트 이름 검증을 유지한다. 다른 OS에서는 기본 검증 컨텍스트를 사용한다.
- 토큰 확인, 저장소 확인, 데이터 GET/PUT/DELETE, raw JSON/blob 다운로드에 컨텍스트를 전달한다.
- 인증서 오류에는 Windows 인증서 저장소 사용 여부와 PC 날짜/시간·Windows 업데이트·
  HTTPS 검사 환경의 인증서 확인 안내를 표시한다. 연결 단계의 오류를 토큰 오입력으로 표시하지 않는다.
- Bearer/token 재시도와 익명 GET 폴백도 동일한 인증서 검증을 사용한다.
  재시도 중 네트워크/인증서 오류가 나면 해당 연결 오류를 반환한다.
- 전역 SSL 패치, 인증서 검증 해제, 토큰 변경/삭제, Windows 인증서 설치는 하지 않는다.

## 빌드와 확인

`requirements-gui.txt`와 GUI spec에 기존 TTS 수정으로 truststore 의존성이 이미 포함돼 있다.
새 모듈은 소스 import를 통해 포함된다. 기존에 만든 EXE에는 이번 변경이 없으므로 재빌드가 필요하다.
Windows 자체에서도 인증서가 신뢰되지 않으면 여전히 실패할 수 있으며, 검증을 우회하지 않는다.

이번 작업에서는 소스 문법과 변경사항만 확인했다. 프로그램·테스트·빌드는 실행하지 않았으며,
실제 GitHub 요청, 데이터 업로드, 사용자 설정 변경도 수행하지 않았다.

참고: [truststore 공식 문서](https://truststore.readthedocs.io/en/latest/)
