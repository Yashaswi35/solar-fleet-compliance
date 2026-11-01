-- One row per inverter per 5-minute bucket. Night-time tare (small negative
-- AC power) is floored at zero.
select
    cast(site_id as integer)                     as site_id,
    inverter_id,
    cast(site_id as varchar) || '-' || inverter_id as asset_id,
    cast(ts as timestamp)                        as ts,
    greatest(ac_kw, 0)                           as ac_kw
from {{ source('pvdaq', 'inverter_power') }}
where ts is not null
  and ac_kw is not null
