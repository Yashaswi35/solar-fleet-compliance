{% test between(model, column_name, min_value, max_value) %}
-- Fails for rows whose value falls outside [min_value, max_value]. Nulls pass.
select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})
{% endtest %}
