-- One row per site per 5-minute slot on a complete time grid, so intervals
-- where the data logger sent nothing still exist and count as data gaps.
--
-- window_type (when is the site expected to produce?)
--   irradiance  plane-of-array irradiance at or above the contract threshold
--   peer        no irradiance reading: the median of reporting inverters says
--               the site is producing
--   null        not a production window (night, low sun, dark weather)
--
-- Inside an irradiance window, if every reporting inverter reads near zero:
--   is_site_outage       the revenue meter agrees (or the site has no meter)
--   is_monitoring_fault  the revenue meter shows the site producing, so the
--                        inverter readings are wrong, not the plant
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

meter_capacity as (
    select site_id, quantile_cont(meter_kw, {{ var('capacity_quantile') }}) as meter_capacity_kw
    from {{ ref('stg_site_meter') }}
    where meter_kw > 0
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
    -- 15-minute irradiance and meter readings are carried forward up to 10 minutes
    -- so they cover every 5-minute slot.
    select g.site_id, g.ts,
           coalesce(p.reporting_inverters, 0) as reporting_inverters,
           p.site_norm_power,
           last_value(i.poa_wm2 ignore nulls) over w10   as poa_wm2,
           last_value(m.meter_kw ignore nulls) over w10  as meter_kw,
           date_trunc('month', g.ts)                     as month,
           hour(g.ts) * 12 + minute(g.ts) // 5           as slot
    from grid g
    left join per_ts p using (site_id, ts)
    left join {{ ref('stg_site_irradiance') }} i using (site_id, ts)
    left join {{ ref('stg_site_meter') }} m using (site_id, ts)
    window w10 as (partition by g.site_id order by g.ts
                   range between interval 10 minute preceding and current row)
),

typical as (
    -- Typical output for the site in that month at that time of day.
    select site_id, month, slot,
           quantile_cont(site_norm_power, 0.75) as typical_norm_power
    from filled
    where reporting_inverters >= 1
    group by all
),

normal_production as (
    select site_id, month, site_norm_power / (poa_wm2 / 1000.0) as norm_per_sun
    from filled
    where reporting_inverters >= 1
      and poa_wm2 >= {{ var('irradiance_threshold_wm2') }}
      and site_norm_power >= {{ var('sun_up_threshold') }}
),

-- Normalized output per kW/m2 of irradiance while the site runs normally, by
-- month and for the whole record. Used to estimate what the site should have
-- produced when its inverters cannot be compared with each other.
sun_ratio_month as (
    select site_id, month, median(norm_per_sun) as norm_per_sun
    from normal_production group by all
),

sun_ratio_site as (
    select site_id, median(norm_per_sun) as norm_per_sun
    from normal_production group by all
),

classified as (
    select
        f.*,
        f.meter_kw / nullif(mc.meter_capacity_kw, 0)            as meter_norm_power,
        t.typical_norm_power,
        coalesce(sm.norm_per_sun, ss.norm_per_sun)              as norm_per_sun,
        f.poa_wm2 >= {{ var('irradiance_threshold_wm2') }}
            and f.reporting_inverters >= {{ var('min_peers') }}
            and f.site_norm_power < {{ var('site_outage_norm') }} as all_inverters_zero
    from filled f
    left join meter_capacity mc using (site_id)
    left join typical t using (site_id, month, slot)
    left join sun_ratio_month sm using (site_id, month)
    left join sun_ratio_site ss using (site_id)
)

select
    site_id,
    ts,
    month,
    reporting_inverters,
    site_norm_power,
    poa_wm2,
    meter_kw,
    meter_norm_power,
    typical_norm_power,
    case
        when poa_wm2 is not null then
            case when poa_wm2 >= {{ var('irradiance_threshold_wm2') }} then 'irradiance' end
        when reporting_inverters >= {{ var('min_peers') }}
             and site_norm_power >= {{ var('sun_up_threshold') }} then 'peer'
    end as window_type,
    coalesce(all_inverters_zero
             and coalesce(meter_norm_power < {{ var('meter_producing_norm') }}, true), false) as is_site_outage,
    coalesce(all_inverters_zero
             and meter_norm_power >= {{ var('meter_producing_norm') }}, false)              as is_monitoring_fault,
    case
        when reporting_inverters >= {{ var('min_peers') }}
             and site_norm_power >= {{ var('sun_up_threshold') }}
            then site_norm_power
        when poa_wm2 is not null and norm_per_sun is not null
            then least(norm_per_sun * poa_wm2 / 1000.0, 1.2)
        else typical_norm_power
    end as reference_norm_power
from classified
