"""Stable per-server season-message rotation; never changes the game season."""
CHEERS = (
    "한 시즌 모두 수고하셨습니다. 다음 시즌도 함께 힘내 봐요!",
    "이번 시즌도 함께해 주셔서 감사합니다. 다음 시즌에도 즐겁게 달려 봅시다!",
    "모두 고생 많으셨습니다. 다음 시즌에는 더 좋은 추억을 함께 만들어요!",
    "함께해 주신 길드원 여러분, 감사합니다. 다음 시즌도 우리 길드 화이팅!",
    "한 시즌 든든하게 함께해 주셔서 감사합니다. 다음 시즌에도 건강하고 즐겁게 만나요!",
)


def season_text(state, number):
    from .notice_templates import render_template
    return render_template(state, season_template_key(state, number), {"차수": str(number)})


def season_template_key(state, number):
    # Season-number distance makes collection order/restarts irrelevant after
    # the first reference season. Only this tiny rotation anchor is retained.
    base = state.setdefault("season_message_base", number)
    return f"season.close.{(number - base) % len(CHEERS) + 1}"
