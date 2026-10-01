"""Verified HTTPS contexts for application-owned Windows connections."""
import ssl
import sys
import urllib.error
import urllib.request


def create_verified_ssl_context() -> ssl.SSLContext:
    """Use native Windows trust without altering global SSL behavior."""
    if sys.platform != 'win32':
        return ssl.create_default_context()
    try:
        import truststore
    except ImportError as exc:
        raise RuntimeError(
            'Windows 인증서 저장소를 사용하는 truststore가 프로그램에 포함되지 않았습니다. '
            '수정된 보탐매니저 배포본을 사용해 주세요. 소스 실행/빌드 환경에서는 '
            'python -m pip install -r requirements-gui.txt 를 먼저 실행해 주세요.'
        ) from exc
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


def urlopen_verified(request, *, timeout, service='GitHub 데이터 연결'):
    """Keep HTTP/auth errors intact; explain certificate failures separately."""
    context = create_verified_ssl_context()
    try:
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    except (urllib.error.URLError, ssl.SSLCertVerificationError) as exc:
        reason = getattr(exc, 'reason', exc)
        if not (isinstance(reason, ssl.SSLCertVerificationError)
                or 'CERTIFICATE_VERIFY_FAILED' in str(reason)):
            raise
        store = 'Windows 인증서 저장소' if sys.platform == 'win32' else '시스템 인증서 저장소'
        raise RuntimeError(
            f'{service}: {store}를 사용했지만 서버 인증서를 검증하지 못했습니다.\n'
            'GitHub 토큰 오입력이 아닌 HTTPS 인증서 검증 문제입니다.\n'
            'PC 날짜/시간과 Windows 업데이트 상태를 확인해 주세요. '
            '백신·회사 프록시가 HTTPS를 검사한다면 해당 인증서 설정도 확인해 주세요.\n'
            f'상세: {reason}'
        ) from exc
