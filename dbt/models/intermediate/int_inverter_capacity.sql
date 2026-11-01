-- Capacity proxy per inverter: a high quantile of observed AC power. Public
-- data does not always carry nameplate ratings, and this keeps the method the
-- same for every site.
select
    site_id,
    inverter_id,
    asset_id,
    quantile_cont(ac_kw, {{ var('capacity_quantile') }}) as capacity_kw,
    min(ts)                                              as first_seen_ts,
    max(ts)                                              as last_seen_ts,
    count(*)                                             as readings
from {{ ref('stg_inverter_power') }}
where ac_kw > 0
group by all
having quantile_cont(ac_kw, {{ var('capacity_quantile') }}) > 0
