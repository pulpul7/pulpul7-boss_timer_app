# 디코 연결 표시

에셋: `assets/discord_plug_connection.png`.
내장 image_gen 도구로 만든 투명 PNG이며, Tk에서 네 칸을 읽어 애니메이션으로 표시한다.
이미지 생성 스킬의 스프라이트 방식과 투명 배경 지침을 적용했다. 추가 이미지 라이브러리는 필요 없다.

연결 중: 미연결→접근→거의 연결 프레임 반복. 버튼은 480ms마다 파랑/보라/주황으로 변화한다.
연결 완료: 마지막 프레임과 녹색 버튼. 오류: 빨간색. 종료 중: 주황색 고정.
연결 완료 판단은 기존 음성 브리지 상태 로직을 유지한다. 프로세스 실행만으로 완료 표시하지 않는다.
창 닫기 시 타이머 취소, 이미지 누락 시 기존 상태 텍스트로 안전하게 대체한다.
이미지는 기존 assets 수집 규칙으로 배포에 포함된다. EXE/ZIP은 생성하지 않았다.

## 실제 생성 프롬프트

Use case: stylized-concept. Asset type: compact desktop UI animated connection sprite sheet. Create one PNG sprite sheet with a genuine transparent background, precisely 1024 by 1024 pixels, divided into exactly four equal 512x512 quadrants, no visible grid lines. Each quadrant contains the same simple chunky, polished flat illustrated blue electrical plug and pale blue socket, side view, centered in a horizontal band within the quadrant from y=160 to y=352 (relative to quadrant). Same socket fixed at right of each quadrant, identical size and position throughout. Plug has a short cord extending left and two obvious metal prongs facing right. Four sequential animation frames read left-to-right then next row: upper-left unplugged with clear gap; upper-right plug moving halfway toward socket; lower-left plug almost inserted; lower-right plug fully inserted, with a small green connection indicator. All artwork stays within each quadrant, ample transparent margins. No text, no numbers, no labels, no logo, no border, no background scene, no checkerboard painted into transparency. Crisp recognizable silhouette intended for very small status indicator, consistent locked geometry in all four frames.

실제 출력 크기는 1254×1254이며 런타임에서 실제 크기를 기준으로 분할한다.
