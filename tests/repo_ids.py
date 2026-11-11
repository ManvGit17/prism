"""Repo ids for tests.

Every id starts with a prefix unique to this test session, so tests can run
against a database that also holds real indexed repos and clean up only their
own data.
"""

import uuid

TEST_PREFIX = f"prism-test-{uuid.uuid4().hex[:8]}"


def repo_id(name: str) -> str:
    return f"{TEST_PREFIX}-{name}"
