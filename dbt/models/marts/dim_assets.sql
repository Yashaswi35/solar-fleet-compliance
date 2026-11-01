select
    asset_id,
    site_id,
    inverter_id,
    round(capacity_kw, 2)                                   as capacity_kw,
    first_seen_ts,
    last_seen_ts,
    round(date_diff('day', first_seen_ts, last_seen_ts) / 365.25, 2) as years_observed,
    readings
from {{ ref('int_inverter_capacity') }}
