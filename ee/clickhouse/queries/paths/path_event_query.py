from typing import Any, Dict, Tuple

from ee.clickhouse.models.property import get_property_string_expr
from ee.clickhouse.queries.event_query import ClickhouseEventQuery
from posthog.constants import AUTOCAPTURE_EVENT, PAGEVIEW_EVENT, SCREEN_EVENT
from posthog.models.filters.path_filter import PathFilter


class PathEventQuery(ClickhouseEventQuery):
    FUNNEL_PERSONS_ALIAS = "funnel_persons"
    _filter: PathFilter

    def __init__(
        self,
        filter: PathFilter,
        team_id: int,
        round_interval=False,
        should_join_distinct_ids=False,
        should_join_persons=False,
        **kwargs,
    ) -> None:
        super().__init__(filter, team_id, round_interval, should_join_distinct_ids, should_join_persons, **kwargs)

    def get_query(self) -> Tuple[str, Dict[str, Any]]:
        # TODO: ColumnOptimizer with options like self._filter.include_pageviews, self._filter.include_screenviews,

        funnel_paths_timestamp = ""
        funnel_paths_join = ""
        funnel_paths_filter = ""

        if self._filter.funnel_paths:
            funnel_paths_timestamp = f"{self.FUNNEL_PERSONS_ALIAS}.timestamp as min_timestamp"
            funnel_paths_join = f"JOIN {self.FUNNEL_PERSONS_ALIAS} ON {self.FUNNEL_PERSONS_ALIAS}.person_id = {self.DISTINCT_ID_TABLE_ALIAS}.person_id"
            funnel_paths_filter = f"AND {self.EVENT_TABLE_ALIAS}.timestamp >= min_timestamp"

        _fields = [
            f"{self.EVENT_TABLE_ALIAS}.timestamp AS timestamp",
            (
                f"if(event = '{SCREEN_EVENT}', {self._get_screen_name_parsing()}, "
                f"if({self.EVENT_TABLE_ALIAS}.event = '{PAGEVIEW_EVENT}', {self._get_current_url_parsing()}, "
                f"if({self.EVENT_TABLE_ALIAS}.event = '{AUTOCAPTURE_EVENT}', concat('autocapture:', {self.EVENT_TABLE_ALIAS}.elements_chain), "
                f"{self.EVENT_TABLE_ALIAS}.event))) AS path_item"
            ),
            f"{self.DISTINCT_ID_TABLE_ALIAS}.person_id as person_id" if self._should_join_distinct_ids else "",
            funnel_paths_timestamp,
        ]

        _fields = list(filter(None, _fields))

        date_query, date_params = self._get_date_filter()
        self.params.update(date_params)

        prop_filters = self._filter.properties
        prop_query, prop_params = self._get_props(prop_filters)
        self.params.update(prop_params)

        event_query, event_params = self._get_event_query()
        self.params.update(event_params)

        query = f"""
            SELECT {','.join(_fields)} FROM events {self.EVENT_TABLE_ALIAS}
            {self._get_disintct_id_query()}
            {self._get_person_query()}
            {funnel_paths_join}
            WHERE team_id = %(team_id)s
            {event_query}
            {date_query}
            {prop_query}
            {funnel_paths_filter}
            ORDER BY {self.DISTINCT_ID_TABLE_ALIAS}.person_id, {self.EVENT_TABLE_ALIAS}.timestamp
        """
        return query, self.params

    def _determine_should_join_distinct_ids(self) -> None:
        self._should_join_distinct_ids = True

    def _get_current_url_parsing(self):
        path_type, _ = get_property_string_expr("events", "$current_url", "'$current_url'", "properties")
        return f"if(length({path_type}) > 1, trim( TRAILING '/' FROM {path_type}), {path_type})"

    def _get_screen_name_parsing(self):
        path_type, _ = get_property_string_expr("events", "$screen_name", "'$screen_name'", "properties")
        return path_type

    def _get_event_query(self) -> Tuple[str, Dict[str, Any]]:
        params: Dict[str, Any] = {}

        conditions = []
        or_conditions = []
        if self._filter.include_pageviews:
            or_conditions.append(f"event = '{PAGEVIEW_EVENT}'")

        if self._filter.include_screenviews:
            or_conditions.append(f"event = '{SCREEN_EVENT}'")

        if self._filter.include_autocaptures:
            or_conditions.append(f"event = '{AUTOCAPTURE_EVENT}'")

        if self._filter.include_all_custom_events:
            or_conditions.append(f"NOT event LIKE '$%%'")

        if self._filter.custom_events:
            or_conditions.append(f"event IN %(custom_events)s")
            params["custom_events"] = self._filter.custom_events

        if or_conditions:
            conditions.append(f"({' OR '.join(or_conditions)})")

        if self._filter.exclude_events:
            conditions.append(f"NOT event IN %(exclude_events)s")
            params["exclude_events"] = self._filter.exclude_events

        if conditions:
            return f" AND {' AND '.join(conditions)}", params

        return "", {}
