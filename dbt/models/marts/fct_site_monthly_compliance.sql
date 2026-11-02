-- Site-month scorecard against the O&M contract terms in seeds/contract_terms.csv.
-- Contract terms are illustrative; see the README.
-- A month is assessable only if readings cover at least half its production
-- windows (data_availability) and the month has at least half the production
-- windows the same calendar month usually has at that site (window_coverage).
-- The second check catches months where the sensor and inverters both went
-- silent, so there are almost no windows to measure against.
with site_month as (
    select
        site_id, month,
        count(distinct asset_id)                    as inverters,
        sum(window_intervals)                       as window_intervals,
        sum(available_intervals)                    as available_intervals,
        sum(down_intervals)                         as down_intervals,
        sum(no_data_intervals)                      as no_data_intervals,
        sum(actual_kwh)                             as actual_kwh,
        sum(lost_kwh)                               as lost_kwh,
        count(*) filter (where is_underperforming)  as underperforming_inverters
    from {{ ref('fct_inverter_monthly') }}
    group by all
),

events as (
    select i.site_id, date_trunc('month', i.started_at) as month,
           count(*)                                                    as outage_events,
           count(*) filter (where i.duration_hours > c.max_restore_hours) as restore_sla_breaches
    from {{ ref('int_outage_incidents') }} i
    join {{ ref('contract_terms') }} c using (site_id)
    where i.down_intervals >= {{ var('min_event_intervals') }}
    group by all
),

scored as (
    select
        s.*,
        c.contract_id,
        c.min_time_availability,
        c.min_energy_availability,
        c.min_data_availability,
        c.max_restore_hours,
        c.ld_rate_usd_per_kwh,
        coalesce(e.outage_events, 0)          as outage_events,
        coalesce(e.restore_sla_breaches, 0)   as restore_sla_breaches,
        s.window_intervals / nullif(s.inverters, 0)
            / nullif(median(s.window_intervals / nullif(s.inverters, 0))
                     over (partition by s.site_id, month(s.month)), 0)          as window_coverage,
        s.available_intervals / nullif(s.available_intervals + s.down_intervals, 0) as time_availability,
        s.actual_kwh / nullif(s.actual_kwh + s.lost_kwh, 0)                         as energy_availability,
        (s.available_intervals + s.down_intervals) / nullif(s.window_intervals, 0)  as data_availability
    from site_month s
    join {{ ref('contract_terms') }} c using (site_id)
    left join events e using (site_id, month)
)

select
    *,
    data_availability >= {{ var('min_month_data_coverage') }}
        and window_coverage >= {{ var('min_month_data_coverage') }}     as is_assessable,
    time_availability >= min_time_availability                          as meets_time_availability,
    energy_availability >= min_energy_availability                      as meets_energy_availability,
    data_availability >= min_data_availability                          as meets_data_availability,
    restore_sla_breaches = 0                                            as meets_restore_sla,
    greatest(min_energy_availability * (actual_kwh + lost_kwh) - actual_kwh, 0) as shortfall_kwh,
    greatest(min_energy_availability * (actual_kwh + lost_kwh) - actual_kwh, 0)
        * ld_rate_usd_per_kwh                                           as illustrative_ld_usd,
    case
        when data_availability < {{ var('min_month_data_coverage') }}
          or window_coverage < {{ var('min_month_data_coverage') }} then 'insufficient_data'
        when time_availability >= min_time_availability
         and energy_availability >= min_energy_availability
         and data_availability >= min_data_availability
         and restore_sla_breaches = 0 then 'compliant'
        else 'breach'
    end as compliance_status
from scored
