-- Every outage ticket must trace back to an outage event.
select t.ticket_id
from {{ ref('fct_service_tickets') }} t
left join {{ ref('int_outage_events') }} e
  on t.ticket_id = 'OUT-' || e.event_id
where t.ticket_type = 'outage'
  and e.event_id is null
