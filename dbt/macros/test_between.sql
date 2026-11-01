{% test between(model, column_name, min_value, max_value) %}
-- Plausibility check: warns (does not fail the build) for rows whose value
-- falls outside [min_value, max_value]. Nulls pass. Integrity tests (keys,
-- relationships, reconciliation) stay at error severity.
{{ config(severity = 'warn') }}
select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})
{% endtest %}
