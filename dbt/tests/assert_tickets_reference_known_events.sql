-- Every outage ticket must trace back to an incident, and every incident to
-- at least one outage event on the same site.
select t.ticket_id
from {{ ref('fct_service_tickets') }} t
left join {{ ref('int_outage_incidents') }} i
  on t.ticket_id = 'OUT-' || i.incident_id
left join {{ ref('int_outage_events') }} e
  on e.site_id = i.site_id
 and e.started_at = i.started_at
where t.ticket_type = 'outage'
  and (i.incident_id is null or e.event_id is null)
