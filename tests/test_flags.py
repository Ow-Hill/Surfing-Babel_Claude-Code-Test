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
