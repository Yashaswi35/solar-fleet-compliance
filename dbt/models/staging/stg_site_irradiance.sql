select
    cast(site_id as integer)   as site_id,
    cast(ts as timestamp)      as ts,
    greatest(poa_wm2, 0)       as poa_wm2
from {{ source('pvdaq', 'site_irradiance') }}
where ts is not null
  and poa_wm2 is not null
