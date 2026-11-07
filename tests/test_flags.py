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
