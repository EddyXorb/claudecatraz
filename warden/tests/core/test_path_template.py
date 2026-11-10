"""The shared path-template compiler and the dot-segment guard."""

from __future__ import annotations

import pytest

from warden.core.path_template import compile_template, decode_unreserved, has_dot_segment


@pytest.mark.parametrize(
    "template,path,matches",
    [
        ("/projects/{id}/merge_requests", "/projects/g%2Fp/merge_requests", True),
        ("/projects/{id}/merge_requests", "/projects/g/p/merge_requests", False),
        ("/repos/{o}/{r}/pulls/{n:int}", "/repos/o/r/pulls/12", True),
        ("/repos/{o}/{r}/pulls/{n:int}", "/repos/o/r/pulls/12a", False),
        ("/repos/{o}/{r}/pulls/{n:int}", "/repos/o/r/pulls/", False),
        ("/projects/{id}/repository/{rest}", "/projects/x/repository/tree", True),
        ("/projects/{id}/repository/{rest}", "/projects/x/repository", False),
        ("/projects/{id}{/rest}", "/projects/x", True),
        ("/projects/{id}{/rest}", "/projects/x/a/b", True),
        ("/projects/{id}{/rest}", "/projectsx", False),
        ("/version", "/version", True),
        ("/a.b", "/axb", False),  # literals are escaped
    ],
)
def test_compile_template(template, path, matches):
    assert bool(compile_template(template).fullmatch(path)) is matches


@pytest.mark.parametrize(
    "path,dotted",
    [
        ("/projects/x/issues/..", True),
        ("/projects/x/./issues", True),
        ("/projects/x/issues/%2e%2e", True),
        ("/projects/x/issues/%2E", True),
        ("/projects/g%2F..%2Fother/repository/tree", True),
        ("/projects/g%2Fp/repository/files/a..b", False),
        ("/projects/g%2Fp/repository/files/.gitignore", False),
        ("/repos/o/r/pulls/1", False),
    ],
)
def test_has_dot_segment(path, dotted):
    assert has_dot_segment(path) is dotted


@pytest.mark.parametrize(
    "path,decoded",
    [
        ("/repos/o/r/%63ontents", "/repos/o/r/contents"),
        ("/projects/x/%72epository/files", "/projects/x/repository/files"),
        ("/a%2Db%5Fc%7Ed%2Ee%30", "/a-b_c~d.e0"),
        ("/projects/g%2Fp/x%20y%3F", "/projects/g%2Fp/x%20y%3F"),  # reserved stays encoded
        ("/x%41%5a%61%7A", "/xAZaz"),
    ],
)
def test_decode_unreserved(path, decoded):
    assert decode_unreserved(path) == decoded


def test_gitlab_encoded_repository_segment_cannot_reach_the_project_read_catch_all():
    from warden.guards.git.gitlab.intent import ApiIntent
    from warden.guards.git.gitlab.recognizers import match_request

    path = decode_unreserved("/projects/g%2Fp/%72epository/files/x")
    row = match_request(ApiIntent(_project="g/p", _method="GET", path=path))
    assert row is not None and row.id == "read.repository"
