select
    cast(site_id as integer)   as site_id,
    cast(ts as timestamp)      as ts,
    greatest(meter_kw, 0)      as meter_kw
from {{ source('pvdaq', 'site_meter') }}
where ts is not null
  and meter_kw is not null
