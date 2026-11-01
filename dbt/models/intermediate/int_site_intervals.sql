-- One row per site per 5-minute slot on a complete time grid, so intervals
-- where the data logger sent nothing still exist and count as data gaps.
--
-- window_type (when is the site expected to produce?)
--   irradiance  plane-of-array irradiance at or above the contract threshold
--   peer        no irradiance reading: the median of reporting inverters says
--               the site is producing
--   null        not a production window (night, low sun, dark weather)
--
-- is_site_outage: irradiance window, enough inverters reporting, and all of
-- them near zero. Only detectable where an irradiance sensor exists.
with readings as (
    select p.site_id, p.ts,
           least(p.ac_kw / c.capacity_kw, 1.2) as norm_power
    from {{ ref('stg_inverter_power') }} p
    join {{ ref('int_inverter_capacity') }} c using (site_id, inverter_id)
),

per_ts as (
    select site_id, ts,
           count(*)           as reporting_inverters,
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
           -- carry a reading forward up to 10 minutes so 15-minute sensors fill the 5-minute grid
           last_value(i.poa_wm2 ignore nulls) over (
               partition by g.site_id order by g.ts
               range between interval 10 minute preceding and current row
           )                                    as poa_wm2,
           date_trunc('month', g.ts)            as month,
           hour(g.ts) * 12 + minute(g.ts) // 5  as slot
    from grid g
    left join per_ts p using (site_id, ts)
    left join {{ ref('stg_site_irradiance') }} i using (site_id, ts)
),

typical as (
    -- Typical output for the site in that month at that time of day.
    select site_id, month, slot,
           quantile_cont(site_norm_power, 0.75) as typical_norm_power
    from filled
    where reporting_inverters >= {{ var('min_peers') }}
    group by all
),

sun_ratio as (
    -- Normalized output per kW/m2 of irradiance while the site runs normally.
    -- Used to estimate what the site should have produced during a site-wide outage.
    select site_id, month,
           median(site_norm_power / (poa_wm2 / 1000.0)) as norm_per_sun
    from filled
    where reporting_inverters >= {{ var('min_peers') }}
      and poa_wm2 >= {{ var('irradiance_threshold_wm2') }}
      and site_norm_power >= {{ var('sun_up_threshold') }}
    group by all
)

select
    f.site_id,
    f.ts,
    f.month,
    f.reporting_inverters,
    f.site_norm_power,
    f.poa_wm2,
    t.typical_norm_power,
    case
        when f.poa_wm2 is not null then
            case when f.poa_wm2 >= {{ var('irradiance_threshold_wm2') }} then 'irradiance' end
        when f.reporting_inverters >= {{ var('min_peers') }}
             and f.site_norm_power >= {{ var('sun_up_threshold') }} then 'peer'
    end as window_type,
    coalesce(
        f.poa_wm2 >= {{ var('irradiance_threshold_wm2') }}
        and f.reporting_inverters >= {{ var('min_peers') }}
        and f.site_norm_power < {{ var('site_outage_norm') }},
        false
    ) as is_site_outage,
    case
        when f.reporting_inverters >= {{ var('min_peers') }}
             and f.site_norm_power >= {{ var('sun_up_threshold') }}
            then f.site_norm_power
        when f.poa_wm2 is not null and s.norm_per_sun is not null
            then least(s.norm_per_sun * f.poa_wm2 / 1000.0, 1.2)
        else t.typical_norm_power
    end as reference_norm_power
from filled f
left join typical t using (site_id, month, slot)
left join sun_ratio s using (site_id, month)
