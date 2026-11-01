-- One row per site per 5-minute slot on a complete time grid, so that intervals
-- where the data logger sent nothing still exist and count as data gaps.
--
-- window_type
--   peer         enough inverters reported and the site was producing; each
--                inverter is judged against the median of its peers
--   site_outage  every reporting inverter near zero at a time of day when the
--                site normally produces (peers cannot be used)
--   no_peers     too few inverters reported at a time the site normally produces
--   null         not a production window (night, dark weather)
with readings as (
    select p.site_id, p.ts,
           least(p.ac_kw / c.capacity_kw, 1.2) as norm_power
    from {{ ref('stg_inverter_power') }} p
    join {{ ref('int_inverter_capacity') }} c using (site_id, inverter_id)
),

per_ts as (
    select site_id, ts,
           count(*)          as reporting_inverters,
           median(norm_power) as site_norm_power
    from readings
    group by all
),

bounds as (
    select site_id, min(ts) as min_ts, max(ts) as max_ts
    from per_ts
    group by all
),

grid as (
    select site_id, unnest(generate_series(min_ts, max_ts, interval 5 minute)) as ts
    from bounds
),

filled as (
    select g.site_id, g.ts,
           coalesce(p.reporting_inverters, 0) as reporting_inverters,
           p.site_norm_power,
           date_trunc('month', g.ts)           as month,
           hour(g.ts) * 12 + minute(g.ts) // 5 as slot
    from grid g
    left join per_ts p using (site_id, ts)
),

typical as (
    -- Typical production for the site in that month at that time of day,
    -- from intervals with enough reporting peers.
    select site_id, month, slot,
           quantile_cont(site_norm_power, 0.75) as typical_norm_power
    from filled
    where reporting_inverters >= {{ var('min_peers') }}
    group by all
)

select
    f.site_id,
    f.ts,
    f.month,
    f.reporting_inverters,
    f.site_norm_power,
    t.typical_norm_power,
    case
        when f.reporting_inverters >= {{ var('min_peers') }}
             and f.site_norm_power >= {{ var('sun_up_threshold') }}
            then 'peer'
        when f.reporting_inverters >= {{ var('min_peers') }}
             and f.site_norm_power < {{ var('site_outage_norm') }}
             and t.typical_norm_power >= {{ var('typical_production_threshold') }}
            then 'site_outage'
        when f.reporting_inverters < {{ var('min_peers') }}
             and t.typical_norm_power >= {{ var('typical_production_threshold') }}
            then 'no_peers'
    end as window_type,
    case
        when f.reporting_inverters >= {{ var('min_peers') }}
             and f.site_norm_power >= {{ var('sun_up_threshold') }}
            then f.site_norm_power
        else t.typical_norm_power
    end as reference_norm_power
from filled f
left join typical t using (site_id, month, slot)
