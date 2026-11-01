-- Consecutive 'down' production intervals per inverter, grouped into events
-- with a gaps-and-islands pattern. Night hours are not production windows, so
-- an inverter that is still down the next morning stays in the same event.
-- Monitoring gaps (no_data) neither start nor break an event.
with judged as (
    select *
    from {{ ref('int_inverter_intervals') }}
    where status in ('available', 'down')
),

sequenced as (
    select *,
           row_number() over (partition by asset_id order by ts)
         - row_number() over (partition by asset_id, status order by ts) as island,
           -- first interval after this one where the inverter was producing again
           first_value(case when status = 'available' then ts end ignore nulls) over (
               partition by asset_id order by ts
               rows between 1 following and unbounded following
           ) as next_available_ts
    from judged
),

events as (
    select
        site_id, inverter_id, asset_id,
        min(ts)                                        as started_at,
        max(ts) + interval 5 minute                    as last_down_at,
        min(next_available_ts)                         as restored_at,
        count(*)                                       as down_intervals,
        avg(is_site_outage::int) >= 0.5       as site_wide,
        sum(greatest(expected_kwh - actual_kwh, 0))    as lost_kwh
    from sequenced
    where status = 'down'
    group by site_id, inverter_id, asset_id, island
)

select
    cast(site_id as varchar) || '-' || inverter_id || '-' || strftime(started_at, '%Y%m%d%H%M') as event_id,
    site_id,
    inverter_id,
    asset_id,
    started_at,
    restored_at,
    restored_at is null                                           as is_open,
    last_down_at,
    date_diff('minute', started_at, coalesce(restored_at, last_down_at)) / 60.0
                                                                  as duration_hours,
    down_intervals * 5 / 60.0                                     as production_hours_down,
    down_intervals,
    site_wide,
    lost_kwh
from events
