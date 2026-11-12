"""'I see something' flags: issue parsing and the score-reply-close loop."""

import json

import numpy as np
import pytest

from babel_surfer import benchmark as B, flags
from babel_surfer.calibrate import babel_static
from babel_surfer.storage import Store


@pytest.mark.parametrize("title,loc", [
    ("Flag: 123456", "123456"), ("flag 0042", "42"), ("FLAG:  9007199249999999 ", "9007199249999999"),
    ("Flag: 12a", None), ("Bug: 123", None), ("Flag: " + "9" * 41, None), ("", None),
])
def test_parse_location(title, loc):
    assert flags.parse_location(title) == loc


def test_note_skips_template_lines():
    body = "Location: 5\n\nlooks like a cat\n\nSeen on https://x/#5"
    assert flags.note_from(body) == "looks like a cat"


class FakeGitHub:
    def __init__(self, issues):
        self.issues, self.replies, self.closed = issues, {}, []

    def open_flags(self):
        return [i for i in self.issues if flags.parse_location(i["title"])]

    def reply_and_close(self, number, text, completed=True):
        self.replies[number] = text
        self.closed.append(number)

    def comment(self, number, text):
        self.replies[number] = text

    def close(self, number, completed=True):
        self.closed.append(number)


def test_flags_are_scored_kept_and_answered(tmp_path):
    gh = FakeGitHub([
        {"number": 1, "title": "Flag: 777", "body": "Location: 777\n\na face?", "html_url": "u1",
         "reactions": {"+1": 2}},
        {"number": 2, "title": "Flag: 888", "body": "", "html_url": "u2"},
        {"number": 3, "title": "Feature request", "body": "", "html_url": "u3"},
    ])
    frames = {"777": B._face(np.random.default_rng(1), 0.3),
              "888": babel_static(np.random.default_rng(2))}
    store = Store(tmp_path)
    n = flags.process(store, gh, fetch=lambda loc: frames[loc])
    assert n == 2 and sorted(gh.closed) == [1, 2]
    saved = json.loads((tmp_path / "flags.json").read_text())
    assert [f["location"] for f in saved] == ["777", "888"]          # most votes first
    assert saved[0]["votes"] == 3 and saved[0]["note"] == "a face?"
    assert (tmp_path / "images" / "777.jpg").exists()
    assert "777" in gh.replies[1] and "σ" in gh.replies[1]
    assert store.stats["scanned"] == 0                               # not counted as scanned
    assert any(f["location"] == "777" for f in store.finds)          # the face also became a find


def test_votes_merge_and_authors_are_limited():
    mk = lambda n, loc, who, up=0: {"number": n, "title": f"Flag: {loc}", "user": {"login": who},
                                    "reactions": {"+1": up}}
    issues = [mk(1, 5, "a", 2), mk(2, 5, "b"), mk(3, 6, "spam"), mk(4, 7, "spam"),
              mk(5, 8, "spam"), mk(6, 9, "spam"), mk(7, 10, "c")]
    groups = dict(flags.group_flags(issues, limit=20, per_author=3))
    assert [i["number"] for i in groups["5"]] == [1, 2]          # same frame: one entry
    assert "9" not in groups and "10" in groups                  # 4th from one author waits
    assert len(flags.group_flags(issues, limit=2)) == 2


def test_failing_frame_does_not_block_others_and_is_closed_after_retries(tmp_path):
    gh = FakeGitHub([{"number": 1, "title": "Flag: 1", "body": ""},
                     {"number": 2, "title": "Flag: 2", "body": ""}])
    good = babel_static(np.random.default_rng(4))

    def fetch(loc):
        if loc == "1":
            raise RuntimeError("Babelia down")
        return good
    store = Store(tmp_path)
    for attempt in range(flags.MAX_FAILURES):
        gh.issues = [i for i in gh.issues if i["number"] not in gh.closed]
        flags.process(store, gh, fetch)
    assert 2 in gh.closed and 1 in gh.closed
    assert "1" not in store.stats["flag_failures"]


class Recorder:
    """Stands in for urllib.request.urlopen: records each request, replies
    from a queue of JSON bodies."""
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, req, timeout):
        self.requests.append(req)
        body = self.replies.pop(0) if self.replies else None

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(body).encode() if body is not None else b""
        return Resp()


def test_github_client_sends_the_token_only_to_the_api_and_skips_pull_requests(monkeypatch):
    page1 = ([{"number": n, "title": f"Flag: {n}"} for n in range(99)]
             + [{"number": 500, "title": "Flag: 500", "pull_request": {}}])       # a full page
    page2 = [{"number": 600, "title": "Flag: 600"}, {"number": 601, "title": "Bug report"}]
    rec = Recorder([page1, page2])
    monkeypatch.setattr(flags.urllib.request, "urlopen", rec)
    gh = flags.GitHub("tok123", "owner/repo")
    got = gh.open_flags()
    assert [i["number"] for i in got][-1] == 600 and 500 not in [i["number"] for i in got]
    assert len(got) == 100 and len(rec.requests) == 2                       # stopped after a short page
    for req in rec.requests:
        assert req.full_url.startswith("https://api.github.com/repos/owner/repo/issues?")
        assert req.get_header("Authorization") == "Bearer tok123" and req.get_method() == "GET"

    rec = Recorder([{"id": 1}, {"id": 2}])
    monkeypatch.setattr(flags.urllib.request, "urlopen", rec)
    gh.reply_and_close(7, "thanks", completed=False)
    post, patch = rec.requests
    assert post.get_method() == "POST" and post.full_url.endswith("/issues/7/comments")
    assert json.loads(post.data) == {"body": "thanks"}
    assert patch.get_method() == "PATCH" and json.loads(patch.data) == {
        "state": "closed", "state_reason": "not_planned"}


def test_a_flag_on_something_that_is_not_a_babelia_frame_is_not_kept(tmp_path):
    """A flag can name any location; if Babelia answers with an error page,
    that must count as a failed try, never become a find."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 416), (200, 200, 200)).save(buf, "JPEG")
    gh = FakeGitHub([{"number": 1, "title": "Flag: 31", "body": ""}])
    store = Store(tmp_path)
    assert flags.process(store, gh, fetch=lambda loc: buf.getvalue()) == 0
    assert gh.closed == [] and store.finds == [] and store.stats["flag_failures"] == {"31": 1}


def test_one_flag_failing_to_save_or_reply_does_not_stop_the_others(tmp_path, monkeypatch):
    gh = FakeGitHub([{"number": n, "title": f"Flag: {n}", "body": ""} for n in (1, 2, 3)])
    good = babel_static(np.random.default_rng(4))
    store = Store(tmp_path)
    real_add = store.add_flag

    def add_flag(loc, *a, **k):
        if loc == "1":
            raise OSError("disk full")
        return real_add(loc, *a, **k)
    monkeypatch.setattr(store, "add_flag", add_flag)
    real_reply = gh.reply_and_close

    def reply_and_close(number, text, completed=True):
        if number == 2:
            raise RuntimeError("GitHub API rate limited")
        return real_reply(number, text, completed)
    monkeypatch.setattr(gh, "reply_and_close", reply_and_close)
    n = flags.process(store, gh, fetch=lambda loc: good)
    assert n == 2 and gh.closed == [3]                       # 2 was saved but its reply failed
    assert sorted(f["location"] for f in json.loads((tmp_path / "flags.json").read_text())) == ["2", "3"]


def test_flags_do_nothing_without_a_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert flags.main(["--data", str(tmp_path)]) == 0
    assert "skipping flags" in capsys.readouterr().out and not list(tmp_path.iterdir())
