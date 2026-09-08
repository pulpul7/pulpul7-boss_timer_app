"""Repeatable voice-test schedules, independent of players, Tk and persistence.

Times are seconds from the first test cue (not from program startup). The UI
switches determine precision for every ordinary/invasion event in every case.
"""
from datetime import datetime, timedelta


def scenario(number, title, events=(), *, fixed=(), offsets=(), fixed_offsets=(),
             watch=16, detail="", due=False):
    return dict(display_no=number, title=title, events=events, fixed=fixed,
                normal_offsets=offsets, fixed_offsets=fixed_offsets, watch=watch,
                fixed_due=due, condition=detail, detail=detail,
                lane="실제 재생 큐", message="초확정·초읽기 체크 상태 적용",
                status="테스트")


VOICE_TEST_SCENARIOS = (
    scenario("1", "일반 보스 젠 / 초읽기", (("라이노르", 0, False),), watch=15,
             detail="초확정 OFF: 타임 안내 / ON: 젠 안내 / 초읽기도 ON: 시작 안내부터 15~1·젠까지."),
    scenario("2", "침공 보스 젠", (("라이노르", 0, True),), watch=12,
             detail="침공 접두사 한 번, 젠 또는 타임 안내. 침공 자체는 초읽기하지 않는 기존 규칙 유지."),
    scenario("3", "일반·침공 젠 충돌", (("라이노르", 0, False), ("브륀힐드", -5, True)),
             detail="일반 젠 5초 전에 침공 젠. 초읽기 ON이면 숫자 재생 중 침공 안내가 겹침."),
    scenario("4", "일반 1분전·침공 5분전", (("라이노르", 60, False), ("브륀힐드", 305, True)),
             offsets=(60, 300), watch=24, detail="일반 1분전 뒤 5초 후 침공 5분전. 두 알림의 큐·음량·침공 중복 확인."),
    scenario("5", "일반 5분전·침공 1분전", (("라이노르", 300, False), ("브륀힐드", 65, True)),
             offsets=(60, 300), watch=24, detail="일반 5분전 뒤 5초 후 침공 1분전. 앞 테스트와 서로 다른 안내 조합."),
    scenario("6", "2개·3개 동시간 사전 안내", (("라이노르", 300, False), ("브륀힐드", 300, False),
             ("니드호그", 95, False), ("셀로비아", 95, False), ("라타토스크", 95, False)),
             offsets=(60, 300), watch=55,
             detail="2개 그룹 5분전은 이름 둘 다, 35초 뒤 3개 그룹 1분전은 대표 이름 외 2개."),
    scenario("7", "연속보스 10분 사전 안내", (("라이노르", 610, False), ("브륀힐드", 650, False),
             ("니드호그", 690, False)), offsets=(60,), watch=22,
             detail="연속보스감지·10분전·축작업 안내. 첫 보스의 개별 사전 안내가 먼저 끼어들지 않는지 확인."),
    scenario("8", "젠 도중 일반·침공 사전 안내", (("라이노르", 0, False), ("브륀힐드", 50, False),
             ("니드호그", 294, True)), offsets=(60, 300), watch=72,
             detail="일반 젠 10초 전 일반 1분전, 6초 전 침공 5분전. 대기 알림과 50초 뒤 다음 젠까지 관찰."),
    scenario("9-1", "일반·침공 복합 큐 (7분)", (("라이노르", 0, False), ("브륀힐드", 0, False),
             ("니드호그", 60, False), ("셀로비아", 60, False), ("라타토스크", 60, True),
             ("비요른", 60, True), ("헤르모드", 60, True), ("페티", 360, False), ("라이노르", 420, True)),
             offsets=(60, 300), watch=445,
             detail="기존 9-1/9-2 통합. 22:59~23:06 간격 유지. 앞 젠·다음 그룹 1분전·페티 5분전·마지막 침공 젠."),
    scenario("9-3", "동시간 2개·3개·단일 젠", (("라이노르", 0, False), ("브륀힐드", 0, False),
             ("니드호그", 60, False), ("셀로비아", 60, False), ("라타토스크", 60, False), ("페티", 120, False)),
             offsets=(60,), watch=145, detail="기존 06:08~06:10 간격 유지. 젠 그룹과 다음 그룹 1분전 분리, 이름 수에 따른 축약 확인."),
    scenario("9-4", "1초·2초 차이와 동시간 젠", (("라이노르", 0, False), ("브륀힐드", 2, False),
             ("니드호그", 60, False), ("셀로비아", 60, False), ("페티", 60, False), ("라타토스크", 61, False)),
             offsets=(60,), watch=26,
             detail="기존 9-4 유지. 초확정 ON에서 1초 이내 묶음, 2초 차이 별도 젠·차임벨 생략, 라타토스크 이름 중복 확인."),
    scenario("10", "고정보스 1분전·5분전", fixed=(("지옥성채 정예", 60), ("핏빛고블린", 335)),
             fixed_offsets=(60, 300), watch=55, detail="첫 고정 1분전, 35초 뒤 두 번째 고정 5분전. 차임벨부터 문장 끝까지 관찰."),
    scenario("11", "동시간 고정보스·다음 보스", (("라이노르", 780, False),),
             fixed=(("지옥성채 정예", 60), ("핏빛고블린", 60)), fixed_offsets=(60,), watch=15,
             detail="고정보스 2개는 이름을 모두 읽고, 다음 보스 안내는 한 번만 확인."),
    scenario("11-1", "발할라·월드보스 다음 보스 안내", (("라이노르", 780, False),),
             fixed=(("발할라 대전", 60), ("월드보스", 70)), fixed_offsets=(60,), watch=24,
             detail="발할라 대전 1분전과 월드보스 다음 보스 안내를 각각 확인. 발할라 종료/다음 보스 규칙도 동일 경로로 검증."),
    scenario("12", "고정보스 정시 안내 (녹음파일)", fixed=(("월드보스", 0),), fixed_offsets=(60,), due=True,
             watch=10, detail="고정보스 정시 알림은 녹음 파일과 차임벨을 우선 사용. 파일이 없을 때만 TTS로 대체."),
    scenario("13", "고정 안내 지연: 40초 이하", (("라이노르", 15, False),),
             fixed=(("지옥성채 정예", 55),), fixed_offsets=(60,), watch=40,
             detail="초읽기 ON에서 1분전 안내가 젠 뒤 40초 이하로 밀리는 보정 경계. OFF에서는 원래 1분전 안내를 유지."),
    scenario("14", "고정 안내 지연: 40초 초과", (("라이노르", 15, False),),
             fixed=(("지옥성채 정예", 60),), fixed_offsets=(60,), watch=40,
             detail="초읽기 ON에서 8초 전에 고정 1분전 제출. 젠 뒤에도 40초 초과이면 원래 안내 유지."),
    scenario("15", "가혹: 연속 젠·침공·고정·사전 안내", (("라이노르", 0, False), ("브륀힐드", 2, False),
             ("니드호그", 8, False), ("셀로비아", 9, False), ("라타토스크", -7, True),
             ("비요른", 4, True), ("헤르모드", 65, False), ("페티", 305, True)),
             fixed=(("지옥성채 정예", 60), ("핏빛고블린", 60)), offsets=(60, 300), fixed_offsets=(60,), watch=70,
             detail="초읽기 도중 침공 젠·일반 1분전·침공 5분전·동시 고정 2개가 유입. 2/8/9초 연속 젠과 대기 큐·중복·유실 확인."),
    scenario("16", "가혹: 다수 동시간·침공", tuple(
             (name, 0, False) for name in
                 ("라이노르", "브륀힐드", "니드호그", "셀로비아", "라타토스크", "비요른",
                  "헤르모드", "페티", "파르바", "흐니르", "바우티", "야른"))
             + (("라이노르", 0, True), ("브륀힐드", 0, True)),
             offsets=(60,), watch=25, detail="동시간 일반 12개·침공 2개를 하나의 복합 그룹으로 처리해 침공이 별도로 반복되지 않는지 확인."),
    scenario("17", "가혹: 1·2·10·11초 경계", (("라이노르", 0, False), ("브륀힐드", 1, False),
             ("니드호그", 3, False), ("셀로비아", 13, False), ("라타토스크", 24, False),
             ("비요른", 11, True)), watch=40,
             detail="초확정 ON에서 묶음 경계 1초, 개별 젠 2초, 차임벨 10/11초 경계 및 중간 침공 충돌 확인."),
)


def build_voice_test_plan(index: int, now: datetime, delay_seconds: int,
                          second_precision: bool, countdown: bool) -> dict:
    spec = VOICE_TEST_SCENARIOS[index]
    countdown = bool(second_precision and countdown)
    # Keep the earliest collision cue in the future even with countdown OFF.
    # These cases include notices 8~15 seconds before their main spawn.
    cue_lead = {"3": 13, "8": 20, "15": 20}.get(spec["display_no"], 6)
    anchor = now.replace(microsecond=0) + timedelta(seconds=max(cue_lead, int(delay_seconds)))
    if countdown:
        normal_times = [seconds for _name, seconds, invasion in spec["events"] if not invasion]
        if normal_times:
            # Full 15-second countdown plus its start notice and player lead.
            anchor += timedelta(seconds=max(0, 21 - (anchor - now).total_seconds() - min(normal_times)))
            anchor = anchor.replace(microsecond=0)
    return dict(spec, anchor=anchor, finish_at=anchor + timedelta(seconds=spec["watch"]),
                precision="second" if second_precision else "minute", countdown=countdown)
