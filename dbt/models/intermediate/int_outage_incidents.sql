-- Groups outage events into incidents, the unit that becomes a service ticket.
--   inverter  events on one inverter less than incident_merge_hours apart. An
--             inverter that trips twenty times on a hot afternoon is one
--             problem for a technician, not twenty.
--   site      site-wide events (every inverter down) that overlap or sit close
--             together become one site ticket instead of one per inverter.
with ev as (
    select *, coalesce(restored_at, last_down_at) as ended_at
    from {{ ref('int_outage_events') }}
),

inverter_flagged as (
    select *,
           case when started_at <= max(ended_at) over (
                    partition by asset_id order by started_at
                    rows between unbounded preceding and 1 preceding
                ) + interval {{ var('incident_merge_hours') }} hour
                then 0 else 1 end as starts_incident
    from ev
    where not site_wide
),

inverter_grouped as (
    select *,
           sum(starts_incident) over (partition by asset_id order by started_at
                                      rows unbounded preceding) as incident_no
    from inverter_flagged
),

inverter_incidents as (
    select
        site_id, inverter_id, asset_id,
        'inverter'                         as scope,
        min(started_at)                    as started_at,
        max(ended_at)                      as ended_at,
        bool_or(is_open)                   as is_open,
        count(*)                           as events,
        sum(down_intervals)                as down_intervals,
        sum(lost_kwh)                      as lost_kwh
    from inverter_grouped
    group by site_id, inverter_id, asset_id, incident_no
),

site_flagged as (
    select *,
           case when started_at <= max(ended_at) over (
                    partition by site_id order by started_at, asset_id
                    rows between unbounded preceding and 1 preceding
                ) + interval {{ var('incident_merge_hours') }} hour
                then 0 else 1 end as starts_incident
    from ev
    where site_wide
),

site_grouped as (
    select *,
           sum(starts_incident) over (partition by site_id order by started_at, asset_id
                                      rows unbounded preceding) as incident_no
    from site_flagged
),

site_incidents as (
    select
        site_id,
        'ALL'                              as inverter_id,
        null::varchar                      as asset_id,
        'site'                             as scope,
        min(started_at)                    as started_at,
        max(ended_at)                      as ended_at,
        bool_or(is_open)                   as is_open,
        count(*)                           as events,
        max(down_intervals)                as down_intervals,
        sum(lost_kwh)                      as lost_kwh
    from site_grouped
    group by site_id, incident_no
),

incidents as (
    select * from inverter_incidents
    union all
    select * from site_incidents
)

select
    cast(site_id as varchar) || '-' || inverter_id || '-' || strftime(started_at, '%Y%m%d%H%M') as incident_id,
    *,
    date_diff('minute', started_at, ended_at) / 60.0 as duration_hours
from incidents
