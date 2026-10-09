import json

from pelican.graphql.guppy_gql import GuppyGQL


class StubGuppyGQL(GuppyGQL):
    """
    A GuppyGQL that answers from a canned {filter -> ids} map instead of
    talking to guppy, so the multi-filter union can be tested on its own.
    """

    def __init__(self, ids_by_filter, auth_paths_by_filter=None):
        super().__init__(node="subject", hostname="http://stub", access_token="stub")
        self.ids_by_filter = ids_by_filter
        self.auth_paths_by_filter = auth_paths_by_filter or {}
        self.calls = []

    def _key(self, filters):
        return json.dumps(json.loads(filters)["filter"], sort_keys=True)

    def execute(self, filters=None):
        self.calls.append(self._key(filters))
        return self.ids_by_filter.get(self._key(filters), [])

    def _graphql_auth_resource_path(self, filters=None):
        self.calls.append(self._key(filters))
        return self.auth_paths_by_filter.get(self._key(filters), [])


FILTER_A = {"AND": [{"IN": {"consortium": ["INRG"]}}]}
FILTER_B = {"AND": [{"IN": {"consortium": ["INSTRUCT"]}}]}


def key(one_filter):
    return json.dumps(one_filter, sort_keys=True)


def test_execute_many_unions_every_filter_set():
    gql = StubGuppyGQL({key(FILTER_A): ["s1", "s2"], key(FILTER_B): ["s3"]})

    assert gql.execute_many([FILTER_A, FILTER_B]) == ["s1", "s2", "s3"]
    assert len(gql.calls) == 2


def test_execute_many_de_duplicates_overlapping_cohorts():
    """
    Two searches on the same project may select overlapping subjects; the
    exported cohort must be a single set of data, so each subject appears once.
    """
    gql = StubGuppyGQL({key(FILTER_A): ["s1", "s2"], key(FILTER_B): ["s2", "s3"]})

    assert gql.execute_many([FILTER_A, FILTER_B]) == ["s1", "s2", "s3"]


def test_execute_many_preserves_first_seen_order():
    gql = StubGuppyGQL({key(FILTER_A): ["s3", "s1"], key(FILTER_B): ["s1", "s2"]})

    assert gql.execute_many([FILTER_A, FILTER_B]) == ["s3", "s1", "s2"]


def test_execute_many_with_a_single_filter_matches_execute():
    gql = StubGuppyGQL({key(FILTER_A): ["s1", "s2"]})

    assert gql.execute_many([FILTER_A]) == ["s1", "s2"]


def test_execute_many_with_no_matches_returns_empty():
    gql = StubGuppyGQL({})

    assert gql.execute_many([FILTER_A, FILTER_B]) == []


def test_auth_resource_paths_many_collects_distinct_paths():
    gql = StubGuppyGQL(
        {},
        auth_paths_by_filter={
            key(FILTER_A): [
                {"auth_resource_path": "/programs/pcdc/projects/INRG"},
                {"auth_resource_path": "/programs/pcdc/projects/INRG"},
            ],
            key(FILTER_B): [
                {"auth_resource_path": "/programs/pcdc/projects/INSTRUCT"},
                {"auth_resource_path": "/programs/pcdc/projects/INRG"},
            ],
        },
    )

    assert gql.auth_resource_paths_many([FILTER_A, FILTER_B]) == [
        "/programs/pcdc/projects/INRG",
        "/programs/pcdc/projects/INSTRUCT",
    ]
