-- Status of every inverter in every production window.
--   available  producing in line with the site
--   down       near zero while the site (or its typical profile) says it should produce
--   no_data    no reading received; a monitoring gap, not counted as downtime
select
    w.site_id,
    c.inverter_id,
    c.asset_id,
    w.ts,
    w.month,
    w.window_type,
    c.capacity_kw,
    p.ac_kw,
    case
        when p.ac_kw is null then 'no_data'
        when p.ac_kw / c.capacity_kw < {{ var('down_threshold') }} then 'down'
        else 'available'
    end as status,
    c.capacity_kw * w.reference_norm_power                  as expected_kw,
    c.capacity_kw * w.reference_norm_power * 5 / 60.0       as expected_kwh,
    coalesce(p.ac_kw, 0) * 5 / 60.0                         as actual_kwh
from {{ ref('int_site_intervals') }} w
join {{ ref('int_inverter_capacity') }} c
  on c.site_id = w.site_id
 and w.ts between c.first_seen_ts and c.last_seen_ts
left join {{ ref('stg_inverter_power') }} p
  on p.site_id = w.site_id
 and p.inverter_id = c.inverter_id
 and p.ts = w.ts
where w.window_type is not null
