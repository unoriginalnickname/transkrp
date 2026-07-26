"""Rate-limit and transport tests against a real local HTTP server.

The rest of the offline suite stubs `_opener`, which means the retry logic is
tested but `urllib` is not: whether an HTTPError actually surfaces the way the
code assumes, whether the timeout fires, whether an empty 200 looks empty when it
arrives as socket bytes rather than a `b""` handed over by a mock.

The alternative — provoking YouTube into really rate-limiting us — costs a home
IP that can't fetch transcripts for the next few hours, in exchange for one test
run. A server on 127.0.0.1 that answers 429 exercises every line of our side of
the exchange, which is the part we can actually get wrong.

No network: binds to localhost on a port the OS picks.
"""

import http.server
import json
import threading
import urllib.error

import pytest

from test_transkrp import ev, payload, tk


class Handler(http.server.BaseHTTPRequestHandler):
    """Replays whatever the test queued up, one response per request."""

    def do_GET(self):
        script = self.server.script
        status, body, headers = script[min(self.server.hits, len(script) - 1)]
        self.server.hits += 1
        self.server.seen_headers.append(dict(self.headers))

        if status == "hang":
            # Longer than any timeout a test will set: exercises the read stall,
            # which is the failure a missing timeout turns into a permanent hang.
            threading.Event().wait(30)
            return

        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # pytest output is not a web server log


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True  # a hung handler must not block interpreter exit


@pytest.fixture
def serve():
    """Start a server that replays a queued script of responses."""
    made = []

    def start(*script):
        srv = Server(("127.0.0.1", 0), Handler)
        srv.script, srv.hits, srv.seen_headers = list(script), 0, []
        # poll_interval well under the 0.5s default: shutdown() waits for the
        # next poll, and at fifteen tests that default is most of the runtime.
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01},
                         daemon=True).start()
        made.append(srv)
        return srv

    yield start
    for srv in made:
        srv.shutdown()
        srv.server_close()


def url_of(srv):
    return f"http://127.0.0.1:{srv.server_address[1]}/timedtext"


def ok(body=b"{}", headers=None):
    return (200, body, headers)


def status(code, headers=None):
    return (code, b"rate limited", headers)


@pytest.fixture
def instant(monkeypatch):
    """Don't actually wait out the backoff; record it instead."""
    slept = []
    monkeypatch.setattr(tk.time, "sleep", slept.append)
    return slept


# --------------------------------------------------------------------------
# the rate-limit path, over a real socket
# --------------------------------------------------------------------------

def test_a_real_429_becomes_rate_limited(serve, instant):
    """Four 429s over HTTP, and the exception the batch loop keys on comes out."""
    srv = serve(status(429))
    with pytest.raises(tk.RateLimited) as caught:
        tk._get(url_of(srv))
    assert srv.hits == 4                      # tried, backed off, tried again
    assert len(instant) == 3                  # slept between, not after the last
    assert "--proxy" in str(caught.value)     # and says what to do about it


def test_a_429_that_clears_is_retried_to_success(serve, instant):
    """The transient case: back off, come back, get the captions."""
    srv = serve(status(429), status(429), ok(b'{"events": []}'))
    assert tk._get(url_of(srv)) == b'{"events": []}'
    assert srv.hits == 3


def test_backoff_grows_between_real_attempts(serve, instant):
    serve_ = serve(status(429))
    with pytest.raises(tk.RateLimited):
        tk._get(url_of(serve_))
    assert instant[0] < instant[1] < instant[2]


def test_retry_after_is_honoured(serve, instant):
    """A server that says when to come back beats our guess."""
    srv = serve(status(429, {"Retry-After": "7"}), ok(b"{}"))
    tk._get(url_of(srv))
    assert instant == [7.0]


def test_absurd_retry_after_is_capped(serve, instant):
    """`Retry-After: 3600` is information, not an instruction to hang for an hour."""
    srv = serve(status(429, {"Retry-After": "3600"}), ok(b"{}"))
    tk._get(url_of(srv))
    assert instant == [float(tk.MAX_RETRY_AFTER)]


def test_unparseable_retry_after_falls_back_to_backoff(serve, instant):
    """The HTTP-date form is legal and deliberately not honoured."""
    srv = serve(status(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), ok(b"{}"))
    tk._get(url_of(srv))
    assert instant and instant[0] != 0  # backoff, not the header


def test_server_errors_are_retried(serve, instant):
    srv = serve(status(503), ok(b"{}"))
    tk._get(url_of(srv))
    assert srv.hits == 2


def test_a_404_is_not_retried(serve, instant):
    """A settled fact. Retrying spends the backoff to learn it again."""
    srv = serve(status(404))
    with pytest.raises(LookupError, match="HTTP 404"):
        tk._get(url_of(srv))
    assert srv.hits == 1
    assert instant == []


def test_a_403_explains_the_signed_url(serve, instant):
    srv = serve(status(403))
    with pytest.raises(LookupError, match="expired"):
        tk._get(url_of(srv))
    assert srv.hits == 1


# --------------------------------------------------------------------------
# the other transport failures
# --------------------------------------------------------------------------

def test_a_stalled_read_times_out_rather_than_hanging(serve, monkeypatch, instant):
    """Without a timeout this test would never return — which was the bug."""
    monkeypatch.setattr(tk, "TIMEOUT", 0.5)
    srv = serve(("hang", b"", None))
    with pytest.raises(LookupError) as caught:
        tk._get(url_of(srv), tries=2)  # two stalls is enough; four costs 4 seconds
    assert "timed out" in str(caught.value) or "failed" in str(caught.value)
    assert srv.hits == 2  # a stall is transient, so it did come back for another go


def test_an_empty_200_over_a_real_socket_names_the_po_token(serve):
    """The live PO-token failure shape, arriving as bytes off the wire."""
    srv = serve(ok(b""))
    info = {"subtitles": {"en": [{"ext": "json3", "url": url_of(srv)}]}}
    with pytest.raises(LookupError, match="PO token"):
        tk.segments(info, "manual", "en")


def test_a_real_json3_response_parses(serve):
    """The happy path over HTTP, to prove the failure tests aren't passing by luck."""
    srv = serve(ok(payload(ev(0, 1000, "hello"), ev(1000, 500, "there"))))
    info = {"subtitles": {"en": [{"ext": "json3", "url": url_of(srv)}]}}
    assert [s[2] for s in tk.segments(info, "manual", "en")] == ["hello", "there"]


def test_the_user_agent_actually_reaches_the_server(serve):
    """Asserted on the wire, not on opener.addheaders."""
    srv = serve(ok(b"{}"))
    tk._get(url_of(srv))
    assert "transkrp" in srv.seen_headers[0].get("User-Agent", "").lower() or \
           "Mozilla" in srv.seen_headers[0].get("User-Agent", "")


# --------------------------------------------------------------------------
# and the whole batch loop, end to end
# --------------------------------------------------------------------------

def test_a_batch_run_stops_when_the_server_rate_limits(serve, instant, tmp_path,
                                                       monkeypatch, capsys):
    """The full path: HTTP 429 -> RateLimited -> the run gives up and says how
    to resume. Only the metadata probe is stubbed; the caption fetch is real.
    """
    srv = serve(status(429))

    def fake_probe(url, proxy=None):
        return {"title": f"Video {url[-1]}", "id": f"vid1234567{url[-1]}",
                "language": "en", "duration": 60,
                "subtitles": {"en": [{"ext": "json3", "url": url_of(srv)}]},
                "automatic_captions": {}}

    monkeypatch.setattr(tk, "probe", fake_probe)
    rc = tk.main([f"https://youtu.be/vid1234567{i}" for i in range(5)]
                 + ["-o", str(tmp_path)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "429" in err
    assert "4 of 5 not fetched" in err
    assert "--skip-existing" in err
    # Four attempts for the first video, then it stopped. Not 20.
    assert srv.hits == 4


def test_a_batch_run_survives_one_bad_video(serve, instant, tmp_path,
                                            monkeypatch, capsys):
    """Contrast: an ordinary failure is not grounds for giving up."""
    good = serve(ok(payload(ev(0, 1000, "Hello there."))))
    bad = serve(status(404))

    def fake_probe(url, proxy=None):
        which = bad if url.endswith("1") else good
        return {"title": f"Video {url[-1]}", "id": f"vid1234567{url[-1]}",
                "language": "en", "duration": 60,
                "subtitles": {"en": [{"ext": "json3", "url": url_of(which)}]},
                "automatic_captions": {}}

    monkeypatch.setattr(tk, "probe", fake_probe)
    rc = tk.main([f"https://youtu.be/vid1234567{i}" for i in range(3)]
                 + ["-o", str(tmp_path)])

    assert rc == 1                              # something failed
    assert len(list(tmp_path.iterdir())) == 2   # the other two landed
    assert "2 written, 1 failed of 3" in capsys.readouterr().err
