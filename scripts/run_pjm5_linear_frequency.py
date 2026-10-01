"""Connect the ANDES PJM 5-bus operating point to the first linear model."""

from functional_incentives.grid import (
    LinearFrequencyParameters,
    damping_from_load_sensitivity,
    frequency_derivative_hz_per_s,
    simulate_power_step,
    solve_pjm5_operating_point,
)

# Solve the power flow using ANDES in the case of the PJM 5-bus system.
operating_point = solve_pjm5_operating_point()

# print(operating_point)
# print("Frequency:", operating_point.nominal_frequency_hz)
# print("Load:", operating_point.total_load_mw)
# print("Generation:", operating_point.total_generation_mw)

# Assumption: 1% change in load for a 1% change in frequency. So sensitivity = 1.0
load_sensitivity = 1.0
load_damping = damping_from_load_sensitivity(
    load_mw=operating_point.total_load_mw,
    nominal_frequency_hz=operating_point.nominal_frequency_hz,
    per_unit_load_change_per_unit_frequency_change=load_sensitivity,
)

parameters = LinearFrequencyParameters(
    nominal_frequency_hz=operating_point.nominal_frequency_hz,
    equivalent_inertia_s=operating_point.equivalent_inertia_s,
    synchronous_rating_mva=operating_point.total_synchronous_rating_mva,
    load_damping_mw_per_hz=load_damping,
)

# Demonstration event: at t=1 s, demand increases by 10 MW and stays there.
# Positive "power_deficit_mw" therefore means that frequency must decrease.
power_deficit_mw = 10.0
event_time_s = 1.0
trajectory = simulate_power_step(
    parameters,
    power_deficit_mw=power_deficit_mw,
    start_time_s=event_time_s,
    final_time_s=40.0,
    time_step_s=0.05,
)


#print(trajectory.time_s[:])
print(trajectory.frequency_hz[:])

initial_rocof = frequency_derivative_hz_per_s(
    0.0,
    power_deficit_mw=power_deficit_mw,
    power_response_mw=0.0,
    parameters=parameters,
)
steady_state_delta_hz = -power_deficit_mw / load_damping

print("ANDES PJM 5-bus operating point")
print(f"  Nominal frequency:       {operating_point.nominal_frequency_hz:.1f} Hz")
print(f"  Total load:              {operating_point.total_load_mw:.3f} MW")
print(f"  Total generation:        {operating_point.total_generation_mw:.3f} MW")
print(f"  Network losses:          {operating_point.network_losses_mw:.3f} MW")
print(f"  Equivalent inertia H:    {operating_point.equivalent_inertia_s:.3f} s")
print(f"  Synchronous rating:      {operating_point.total_synchronous_rating_mva:.1f} MVA")

print("\nLinear-model assumptions and result")
print(f"  Load-frequency damping:  {load_damping:.3f} MW/Hz")
print(f"  Power deficit at t=1 s:  {power_deficit_mw:.1f} MW")
print(f"  Initial RoCoF:           {initial_rocof:.3f} Hz/s")
print(f"  Expected steady delta f: {steady_state_delta_hz:.3f} Hz")
print(f"  Frequency at t=20 s:     {trajectory.frequency_hz[-1]:.3f} Hz")
