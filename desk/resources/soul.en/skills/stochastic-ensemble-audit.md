# Auditing a stochastic ensemble and OAT

For Monte Carlo/ensemble results, inspect the comparison's causality as well as formulas and percentiles.

1. Before calculating, record the literal problem and assumptions converted into parameters. Do not silently replace “hurricane” with stationary flow or “fall” with one orientation. Separate alternative readings.
2. Retain original code, hash and complete output; execute only with suitable isolation and authority. Reproducible stdout does not establish physical validity.
3. Separate within-model variability, between-family variability and uncertainty in input priors. Implementations of one family are not independent evidence about the world.
4. OAT needs a paired random world. A seed alone is insufficient if disabling a factor changes RNG ordering. Sample primitives unconditionally, then vary their physical action.
5. Estimate Monte Carlo uncertainty through repeated seeds, bootstrap or SE. Do not call a small median shift an established effect without estimating noise.
6. OAT shifts are conditional on the baseline, hide interactions and need not add up. For global contributions choose a suitable factorial/Sobol design or state the limitation.
7. Check censoring: does `TMAX` leave active trajectories uncounted? Inspect coordinates, radius, p95/p99 and relevant threshold probabilities.
8. Check sources for `Cd(Re)`, drag crisis, spin law, wake, mass and size. An object's name does not provide physical parameters; test disputed priors through sensitivity.
9. Separate code reproducibility, the model's conditional result and unverified claims about the real object.
10. For vector effects inspect paired `with−without`, axis, sign, tumbling and tails. A zero median can hide opposing shifts; symmetric priors do not prove body symmetry.
11. Add the mechanism's cost to “physically possible”: required force, duration or integrated action. A separate reproducible probe with model dependence beats bare possibility; `L/W≈0.58` is an example condition in a particular model, not a universal constant.
