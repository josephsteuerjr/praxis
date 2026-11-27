"""Attach the window's read-only routes to her channel with the same two lines she uses for her own modules."""

OLD = "from deskd import praxis_phone\nROUTES += praxis_phone.routes(Route, Fail)\n"
NEW = OLD + ("# Owner window instruments: read-only, owner-only (not in the channel's device list).\n"
             "from deskd import praxis_window\nROUTES += praxis_window.routes(Route)\n")


def patch(text: str) -> str:
    assert text.count(OLD) == 1, "the channel's route table tail changed"
    if "praxis_window.routes(Route)" in text:
        return text
    return text.replace(OLD, NEW)
