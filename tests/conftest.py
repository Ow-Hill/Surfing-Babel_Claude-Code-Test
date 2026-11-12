"""Shared test setup.

* No test may reach the internet. Babelia is a one-person site and the
  suite runs on every pull request, so a test that fetched from it by
  mistake would load it from CI, and would pass or fail with its mood.
  Opening an internet connection fails the test at once. Local sockets
  (the classifier's forkserver pool) are unaffected.
* Classifying static is the slowest thing the suite does (about 0.9 s a
  frame), and several tests only need "these frames are pure static": they
  share one batch.
"""

import ipaddress
import socket

import numpy as np
import pytest

_real_connect = socket.socket.connect


def _no_internet(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0] if isinstance(address, tuple) else address
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if not local:
            raise RuntimeError(f"a test tried to reach the internet ({address}); "
                               "monkeypatch the fetch instead")
    return _real_connect(self, address)


@pytest.fixture(autouse=True, scope="session")
def no_internet():
    socket.socket.connect = _no_internet
    yield
    socket.socket.connect = _real_connect


@pytest.fixture(scope="session")
def static_classified():
    """25 simulated Babelia frames of pure static, classified once."""
    from babel_surfer.calibrate import babel_static
    from babel_surfer.classifier import classify
    rng = np.random.default_rng(99)
    return [classify(babel_static(rng)) for _ in range(25)]
