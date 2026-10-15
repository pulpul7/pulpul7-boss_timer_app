# 히든메뉴 폰트

웹 → 폰트에서 관리자는 공통 폰트 추가·삭제와 기본값을 지정합니다.
`기본값 저장`은 현재 PC 웹서비스에 적용하며, `배포용 저장`은 다음 빌드에 넣을
`web_assets/fonts` 폴더에 선택한 기본값·공통 목록·추가 파일을 저장합니다.
이후 사용자가 직접 GUI를 빌드합니다. 새 방문자는 배포 기본값을 사용하고,
직접 골랐던 사용자는 자신의 브라우저 선택을 유지합니다.

웹의 이스터에그로 여는 메뉴 명칭은 **히든메뉴**입니다. 일반 사용자는 번호가 있는
폰트 목록과 추가·삭제 버튼을 사용합니다. 배포 폰트와 `00. 기기 기본 폰트`는 삭제되지
않으며 직접 추가한 개인 폰트만 삭제합니다. 개인 폰트 번호는 1001부터 붙여 공통
목록 번호와 구분합니다. 번호는 삭제 후 다른 폰트에 재사용하지 않습니다.

폰트를 OS에 설치하지 않습니다. 파일은 해당 사이트의 브라우저 IndexedDB에 저장하고
선택은 localStorage에 닉네임별로 저장합니다. PC의 BossTimer AppData 폴더에 직접
쓰기·업로드하거나 다른 관리자의 PC에 전송하지 않습니다. PC·폰·브라우저마다 따로
저장하며, 브라우저 사이트 데이터를 삭제하면 개인 파일과 선택도 사라집니다.
폰의 시스템 글꼴 옵션은 브라우저가 제공하는 시스템 글꼴을 사용하므로 휴대폰 설정의
모든 사용자 글꼴이 동일하게 노출된다고 보장하지는 않습니다.

개인 폰트는 TTF·OTF·WOFF·WOFF2, 파일당 8MB 이하, 최대 20개·총 64MB입니다.
추가 전 실제 브라우저 FontFace로 읽을 수 있는지 검사하고 저장 트랜잭션이 끝난 뒤
선택합니다. 닉네임 전환이나 빠른 선택 중 오래된 로딩 결과가 새 선택을 덮지 못하게 합니다.
선택한 폰트만 읽으며 활성 FontFace 캐시는 최대 세 개입니다. 기본 폰트 목록은 페이지당
한 번만 조회하고, 폰트 선택으로 스케줄 API 요청·음성·Discord 동작을 변경하지 않습니다.
이미지 복사에도 선택한 폰트를 사용합니다.

## 공통 폰트 20개 · 8개 사이트

순위가 아니라 삭제·변경 시 식별하기 위한 고정 번호입니다. 한글·숫자 지원과
스케줄 가독성을 고려해 손글씨·둥근 글씨와 본문용 폰트를 함께 골랐습니다.

| 번호 | 폰트 | 제작·배포처 / 공식 안내 |
| --- | --- | --- |
| 01 | 개구 | [Google Fonts](https://fonts.google.com/specimen/Gaegu) |
| 02 | 감자꽃 | [Google Fonts](https://fonts.google.com/specimen/Gamja+Flower) |
| 03 | 하이멜로디 | [Google Fonts](https://fonts.google.com/specimen/Hi+Melody) |
| 04 | 푸어스토리 | [Google Fonts](https://fonts.google.com/specimen/Poor+Story) |
| 05 | 동글 | [Google Fonts](https://fonts.google.com/specimen/Dongle) |
| 06 | 구기 | [Google Fonts](https://fonts.google.com/specimen/Gugi) |
| 07 | 나눔스퀘어라운드 | [네이버](https://hangeul.naver.com/font) |
| 08 | 나눔바른고딕 | [네이버](https://hangeul.naver.com/font) |
| 09 | 나눔손글씨 펜 | [네이버](https://hangeul.naver.com/font) |
| 10 | 나눔손글씨 붓 | [네이버](https://hangeul.naver.com/font) |
| 11 | 배민 주아 | [우아한형제들](https://www.woowahan.com/fonts) |
| 12 | 배민 연성 | [우아한형제들](https://www.woowahan.com/fonts) |
| 13 | 배민 도현 | [우아한형제들](https://www.woowahan.com/fonts) |
| 14 | 카페24 써라운드 Air | [카페24](https://fonts.cafe24.com/) |
| 15 | 카페24 동동 | [카페24](https://fonts.cafe24.com/) |
| 16 | 카페24 단정해 | [카페24](https://fonts.cafe24.com/) |
| 17 | G마켓 산스 | [G마켓](https://corp.gmarket.com/fonts/) |
| 18 | 리디바탕 | [리디](https://ridicorp.com/ridibatang/) |
| 19 | 프리텐다드 | [제작자 안내](https://cactus.tistory.com/306) |
| 20 | SUIT | [Sun](https://sun.fo/suit/) |

## 소스와 배포

`web_assets/fonts/catalog.json`은 원본 출처·실제 다운로드 URL·파일 크기·SHA256과
라이선스 파일을 기록합니다. 폰트 저작권과 OFL 전문, 제공된 원본 PDF는
`web_assets/fonts/licenses`에 포함합니다. 변환 전 원본은 `web_font_originals`에
보관하고 실행 파일에는 웹용 WOFF2만 넣습니다. 폰트 20개 합계는 10,596,692바이트입니다.
폰트 이름 메타데이터·한글 cmap·글자 폭이 원본과 같은지 확인했습니다.
WOFF2 형식만 변경하는 조건은 [OFL 공식 FAQ 2.2](https://openfontlicense.org/ofl-faq/)를 참고했습니다.

폰트는 `web_font_library.py`의 별도 읽기 전용 `/fonts/` 경로에서 제공합니다.
로그인·차단 정책을 적용하고 파일명 허용 목록·크기·서명을 검사하며 ETag로 재검증합니다.
HTTP·HTTPS 모두 동작합니다. 공개 웹에서는 공통 목록이나 배포 기본값을 수정할 수 없습니다.
공통 목록의 삭제는 숨김 처리이며 원본 파일을 보존합니다. 관리자 설정은
`web_service/font_settings.json`, 관리자 추가 파일은 `web_service/fonts`에 보관합니다.
폰트 오류는 기본 글꼴로 복귀하며 스케줄 조회는 계속 사용할 수 있습니다.

기존 여섯 화면 파일의 웹 업데이트 계약은 유지합니다. 이번 첫 도입과 공통 폰트 파일 변경은
GUI 재빌드가 필요합니다. GUI spec은 이미 `web_assets` 전체를 포함하므로 별도 복사 규칙은
추가하지 않았습니다. 이후 화면 코드만 바꿀 때에는 로컬 웹 적용을 사용할 수 있습니다.
`web_font_frontend.js`는 `schedule.js`의 `BEGIN WEB FONT MODULE` 블록으로 포함되어 있습니다.
이 모듈을 수정하면 해당 블록도 갱신합니다. Python의 PAGE·STYLE·SCRIPT 폴백은 동일한 바이트를 유지합니다.

초기 배포 기본값은 기존 화면을 유지하는 `system`입니다. 관리자가 웹 → 폰트에서
원하는 번호를 골라 기본값 저장 → 배포용 저장 → 직접 재빌드하면 그 선택이 다음 배포 기본값이 됩니다.

기술 참고: [IndexedDB](https://developer.mozilla.org/en-US/docs/Web/API/IndexedDB_API),
[FontFace](https://developer.mozilla.org/en-US/docs/Web/API/FontFace).

웹 배포 기본 글꼴은 15번 카페24 동동(cafe24-dongdong)입니다. 기기·브라우저에 저장한 개인 선택이 있으면 그 선택을 우선합니다.
