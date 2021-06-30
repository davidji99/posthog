from typing import List

from ee.clickhouse.queries.funnels.base import ClickhouseFunnelBase


class ClickhouseFunnelUnordered(ClickhouseFunnelBase):
    """
    Unordered Funnel is a funnel where the order of steps doesn't matter.

    ## Query Intuition

    Imagine a funnel with three events: A, B, and C.
    This query splits the problem into two parts:
    1. Given the first event is A, find the furthest everyone went starting from A.
       This finds any B's and C's that happen after A (without ordering them)
    2. Repeat the above, assuming first event to be B, and then C.
    
    Then, the outer query unions the result of (2) and takes the maximum of these.

    ## Results

    The result format is the same as the basic funnel, i.e. [step, count].
    Here, `step_i` (0 indexed) signifies the number of people that did at least `i+1` steps.
    """

    def get_query(self, format_properties):
        return self.get_step_counts_query()

    def get_step_counts_query(self):

        max_steps = len(self._filter.entities)
        union_queries = []
        entities_to_use = list(self._filter.entities)

        partition_select = self.get_partition_cols(1, max_steps)
        sorting_condition = self.get_sorting_condition(max_steps)

        for i in range(max_steps):
            inner_query = f"""
                SELECT 
                person_id,
                timestamp,
                {partition_select}
                FROM ({self._get_inner_event_query(entities_to_use, f"events_{i}")})
            """

            formatted_query = f"""
                SELECT *, {sorting_condition} AS steps FROM (
                        {inner_query}
                    ) WHERE step_0 = 1"""

            #  rotate entities by 1 to get new first event
            entities_to_use.append(entities_to_use.pop(0))
            union_queries.append(formatted_query)

        union_formatted_query = " UNION ALL ".join(union_queries)

        return f"""
        SELECT furthest, count(1), groupArray(100)(person_id) FROM (
            SELECT person_id, max(steps) AS furthest FROM (
                {union_formatted_query}
            ) GROUP BY person_id
        ) GROUP BY furthest SETTINGS allow_experimental_window_functions = 1
        """

    def get_sorting_condition(self, max_steps: int):

        basic_conditions: List[str] = []
        for i in range(1, max_steps):
            basic_conditions.append(
                f"if(latest_0 < latest_{i} AND latest_{i} <= latest_0 + INTERVAL {self._filter.funnel_window_days} DAY, 1, 0)"
            )

        return f"arraySum([{','.join(basic_conditions)}, 1])"