-- Two outage events on the same inverter must never overlap in time.
select a.event_id, b.event_id as overlapping_event_id
from {{ ref('int_outage_events') }} a
join {{ ref('int_outage_events') }} b
  on a.asset_id = b.asset_id
 and a.event_id < b.event_id
 and a.started_at < coalesce(b.restored_at, b.started_at + interval 1 day * 3650)
 and b.started_at < coalesce(a.restored_at, a.started_at + interval 1 day * 3650)
