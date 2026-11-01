-- Monthly availability and performance per inverter.
-- performance_index compares the energy an inverter produced while available
-- with what its peers imply it should have produced (1.0 = in line with peers).
with monthly as (
    select
        asset_id, site_id, inverter_id, month,
        count(*)                                                    as window_intervals,
        count(*) filter (where status = 'available')                as available_intervals,
        count(*) filter (where status = 'down')                     as down_intervals,
        count(*) filter (where status = 'no_data')                  as no_data_intervals,
        sum(actual_kwh)                                             as actual_kwh,
        sum(greatest(expected_kwh - actual_kwh, 0)) filter (where status = 'down') as lost_kwh,
        sum(actual_kwh) filter (where status = 'available' and window_type = 'peer')   as available_kwh,
        sum(expected_kwh) filter (where status = 'available' and window_type = 'peer') as available_expected_kwh
    from {{ ref('int_inverter_intervals') }}
    group by all
)

select
    asset_id, site_id, inverter_id, month,
    window_intervals, available_intervals, down_intervals, no_data_intervals,
    actual_kwh,
    coalesce(lost_kwh, 0)                                                   as lost_kwh,
    coalesce(available_kwh, 0)                                              as available_kwh,
    coalesce(available_expected_kwh, 0)                                     as available_expected_kwh,
    available_intervals / nullif(available_intervals + down_intervals, 0)   as time_availability,
    (available_intervals + down_intervals) / nullif(window_intervals, 0)    as data_availability,
    available_kwh / nullif(available_expected_kwh, 0)                       as performance_index,
    available_intervals >= {{ var('min_available_intervals_for_performance') }}
        and available_kwh / nullif(available_expected_kwh, 0) < {{ var('underperformance_threshold') }}
                                                                            as is_underperforming
from monthly
