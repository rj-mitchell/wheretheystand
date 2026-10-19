"""Briefings are streamed with a comment line during long silences, so a proxy does not close the connection."""

import threading
import time

import pytest

from hansard.api.app import sse


def test_a_silence_is_filled_with_comment_lines_and_events_arrive_in_order():
    def events():
        yield {"type": "plan", "n": 1}
        time.sleep(0.35)  # the model thinking
        yield {"type": "final", "n": 2}

    out = list(sse(events(), keep_alive=0.1))
    assert out[0].startswith(b"event: plan\ndata: ")
    assert out[-1].startswith(b"event: final\ndata: ")
    assert out[1:-1] and all(c == b": still working\n\n" for c in out[1:-1])


def test_an_error_is_raised_where_the_response_is_sent():
    def events():
        yield {"type": "plan"}
        raise RuntimeError("boom")

    stream = sse(events(), keep_alive=1)
    next(stream)
    with pytest.raises(RuntimeError):
        next(stream)


def test_the_work_stops_once_the_reader_has_gone():
    closed = threading.Event()

    def events():
        try:
            for i in range(1000):
                yield {"type": "text", "text": str(i)}
                time.sleep(0.01)
        finally:
            closed.set()

    stream = sse(events(), keep_alive=1)
    next(stream)
    stream.close()  # the reader left
    assert closed.wait(2)
