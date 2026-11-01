-- Energy in the site scorecard must equal the sum of its inverter-months.
with site as (
    select site_id, month, actual_kwh, lost_kwh
    from {{ ref('fct_site_monthly_compliance') }}
),
inv as (
    select site_id, month, sum(actual_kwh) as actual_kwh, sum(lost_kwh) as lost_kwh
    from {{ ref('fct_inverter_monthly') }}
    group by all
)
select *
from site s
join inv i using (site_id, month)
where abs(s.actual_kwh - i.actual_kwh) > 0.01
   or abs(s.lost_kwh - i.lost_kwh) > 0.01
