{% test unique_combination_of_columns(model, columns) %}
-- Fails when the combination of columns is not unique (no package dependency needed).
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
