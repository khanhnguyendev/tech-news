import pytest

from dispatchers.telegram import Chunk, TelegramError, dispatch, send_message, send_video


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True}

    def json(self):
        return self._payload


class FakeSession:
    """Queued outcomes; records every POST."""

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.posts = []

    def post(self, url, data=None, files=None, timeout=None):
        self.posts.append(
            {"url": url, "data": data or {}, "files": files, "timeout": timeout}
        )
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return FakeResponse()


def chunks():
    return [Chunk("first", ["id-1", "id-2"]), Chunk("second", ["id-3"])]


def test_send_message_posts_to_the_right_endpoint():
    session = FakeSession()
    send_message(session, "TOKEN", "CHAT", "<b>hi</b>")
    assert session.posts[0]["url"].endswith("/botTOKEN/sendMessage")
    assert session.posts[0]["data"]["chat_id"] == "CHAT"
    assert session.posts[0]["data"]["parse_mode"] == "HTML"
    assert session.posts[0]["data"]["disable_web_page_preview"] is True


def test_send_message_raises_on_api_error():
    session = FakeSession(
        [FakeResponse(400, {"ok": False, "description": "Bad Request: bad entity"})]
    )
    with pytest.raises(TelegramError, match="bad entity"):
        send_message(session, "T", "C", "x")


def test_send_message_honors_retry_after_then_succeeds():
    slept = []
    session = FakeSession(
        [
            FakeResponse(429, {"ok": False, "parameters": {"retry_after": 7}}),
            FakeResponse(200, {"ok": True}),
        ]
    )
    send_message(session, "T", "C", "x", sleeper=slept.append)
    assert slept == [7]
    assert len(session.posts) == 2


def test_send_message_stops_retrying_after_second_429():
    """A 429 on the *second* attempt must not trigger a third post -- it
    terminates as a failure instead of looping forever."""
    slept = []
    session = FakeSession(
        [
            FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
            FakeResponse(429, {"ok": False, "parameters": {"retry_after": 1}}),
        ]
    )
    with pytest.raises(TelegramError):
        send_message(session, "T", "C", "x", sleeper=slept.append)
    assert slept == [1]
    assert len(session.posts) == 2


def test_send_message_bounds_a_huge_retry_after():
    """A hostile/garbled retry_after must not be able to hang the run --
    it is capped rather than honored verbatim."""
    slept = []
    session = FakeSession(
        [
            FakeResponse(429, {"ok": False, "parameters": {"retry_after": 10_000}}),
            FakeResponse(200, {"ok": True}),
        ]
    )
    send_message(session, "T", "C", "x", sleeper=slept.append)
    assert slept == [60]


def test_send_message_falls_back_on_non_numeric_retry_after():
    """A garbled, non-numeric retry_after must degrade to a safe default
    instead of raising out of the retry path."""
    slept = []
    session = FakeSession(
        [
            FakeResponse(429, {"ok": False, "parameters": {"retry_after": "soon"}}),
            FakeResponse(200, {"ok": True}),
        ]
    )
    send_message(session, "T", "C", "x", sleeper=slept.append)
    assert slept == [5]


def test_send_video_posts_to_the_right_endpoint(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-video-bytes")
    session = FakeSession()
    send_video(session, "TOKEN", "CHAT", video_path, "a caption")
    post = session.posts[0]
    assert post["url"].endswith("/botTOKEN/sendVideo")
    assert post["data"]["chat_id"] == "CHAT"
    assert post["data"]["caption"] == "a caption"
    assert "video" in post["files"]
    assert post["timeout"] > 20  # longer than the plain-message timeout


def test_send_video_raises_on_api_error(tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-video-bytes")
    session = FakeSession(
        [FakeResponse(400, {"ok": False, "description": "file too large"})]
    )
    with pytest.raises(TelegramError, match="file too large"):
        send_video(session, "T", "C", video_path, "caption")


def test_send_video_logs_the_delivery_with_its_size(tmp_path, caplog):
    """send_message's delivery is visible in the log through dispatch(), but
    send_video had no success line at all -- a run could generate and send
    a video with the log showing only that the file was written, so the
    only way to know it arrived was to open Telegram. The size belongs in
    the line because Telegram rejects bot uploads over 50 MB, and that is
    the failure this log is most likely to be read about.
    """
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"x" * 2048)

    with caplog.at_level("INFO", logger="technews"):
        send_video(FakeSession(), "T", "C", video_path, "caption")

    assert "clip.mp4" in caplog.text
    assert "2.0 KB" in caplog.text


def test_send_video_logs_nothing_when_the_api_rejects_it(tmp_path, caplog):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"x" * 2048)
    session = FakeSession(
        [FakeResponse(400, {"ok": False, "description": "file too large"})]
    )

    with caplog.at_level("INFO", logger="technews"):
        with pytest.raises(TelegramError):
            send_video(session, "T", "C", video_path, "caption")

    assert "clip.mp4" not in caplog.text


def test_dispatch_returns_all_ids_on_success():
    delivered, error = dispatch(FakeSession(), "T", "C", chunks())
    assert delivered == ["id-1", "id-2", "id-3"]
    assert error is None


def test_dispatch_returns_only_delivered_ids_on_partial_failure():
    session = FakeSession(
        [FakeResponse(200, {"ok": True}), FakeResponse(400, {"ok": False, "description": "nope"})]
    )
    delivered, error = dispatch(session, "T", "C", chunks())
    assert delivered == ["id-1", "id-2"]
    assert isinstance(error, TelegramError)


def test_dispatch_stops_after_the_first_failure():
    session = FakeSession([FakeResponse(400, {"ok": False, "description": "nope"})])
    delivered, error = dispatch(session, "T", "C", chunks())
    assert delivered == []
    assert len(session.posts) == 1
    assert error is not None


def test_dispatch_never_raises():
    session = FakeSession([RuntimeError("socket died")])
    delivered, error = dispatch(session, "T", "C", chunks())
    assert delivered == []
    assert isinstance(error, TelegramError)


def test_dispatch_never_raises_on_second_chunk():
    """A non-TelegramError exception on a *later* chunk (after some ids
    already delivered) must still be converted, not propagated -- and the
    already-delivered ids must be preserved."""
    session = FakeSession([FakeResponse(200, {"ok": True}), RuntimeError("boom")])
    delivered, error = dispatch(session, "T", "C", chunks())
    assert delivered == ["id-1", "id-2"]
    assert isinstance(error, TelegramError)
