-- Service tickets generated from the telemetry, in a shape that can be pushed
-- to a ticketing system such as Jira (see integrations/jira_push.py).
with outages as (
    select
        'OUT-' || e.event_id                       as ticket_id,
        'outage'                                   as ticket_type,
        e.site_id, e.inverter_id, e.asset_id,
        e.started_at                               as opened_at,
        e.restored_at                              as closed_at,
        e.is_open,
        e.duration_hours,
        e.lost_kwh,
        c.max_restore_hours                        as sla_hours,
        e.duration_hours > c.max_restore_hours     as sla_breached,
        case when e.site_wide then 'Site-wide outage' else 'Inverter outage' end as summary_prefix,
        null::double                               as performance_index
    from {{ ref('int_outage_events') }} e
    join {{ ref('contract_terms') }} c using (site_id)
    where e.down_intervals >= {{ var('min_event_intervals') }}
),

underperformance as (
    select
        'UND-' || m.asset_id || '-' || strftime(m.month, '%Y%m') as ticket_id,
        'underperformance'                         as ticket_type,
        m.site_id, m.inverter_id, m.asset_id,
        m.month                                    as opened_at,
        null::timestamp                            as closed_at,
        false                                      as is_open,
        null::double                               as duration_hours,
        greatest(m.available_expected_kwh - m.available_kwh, 0) as lost_kwh,
        null::double                               as sla_hours,
        false                                      as sla_breached,
        'Underperformance vs peers'                as summary_prefix,
        m.performance_index
    from {{ ref('fct_inverter_monthly') }} m
    where m.is_underperforming
)

select
    *,
    case
        when sla_breached or lost_kwh >= 1000 then 'High'
        when lost_kwh >= 100 then 'Medium'
        else 'Low'
    end as priority
from (
    select * from outages
    union all
    select * from underperformance
)
