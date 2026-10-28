"""Synthesize sampled, ideal L matches with SciPy and scikit-rf, not an EM solver."""

from __future__ import annotations

import math
from importlib.metadata import version
from itertools import product
from typing import Annotated, Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from .contracts import Contract, Impedance, Positive
from .evaluation import EvaluationInput, EvaluationResult

type Topology = Literal["source_shunt_then_series", "series_then_load_shunt"]


class MatchingOptions(Contract):
    mode: Literal["per_frequency_l_network"]
    reference_plane: Literal["antenna_feed"] = "antenna_feed"


class MatchingInput(Contract):
    evaluation: EvaluationInput
    options: MatchingOptions


class Component(Contract):
    connection: Literal["series", "shunt"]
    kind: Literal["inductor", "capacitor", "omit"]
    value_si: Annotated[FiniteFloat, Field(ge=0)]
    unit: Literal["H", "F", "none"]

    @model_validator(mode="after")
    def consistent_units(self) -> Self:
        expected = {"inductor": "H", "capacitor": "F", "omit": "none"}
        if self.unit != expected[self.kind] or (self.value_si == 0) != (self.kind == "omit"):
            raise ValueError("Use positive H/F values, or omit with zero and unit none")
        return self


class FixedNetwork(Contract):
    topology: Topology
    components: tuple[Component, Component]
    reference_plane: Literal["antenna_feed"] = "antenna_feed"

    @model_validator(mode="after")
    def series_and_shunt(self) -> Self:
        if tuple(component.connection for component in self.components) != ("series", "shunt"):
            raise ValueError("Components must contain one series then one shunt entry")
        return self


class FixedMatchingInput(Contract):
    evaluation: EvaluationInput
    network: FixedNetwork
    max_swr: Annotated[FiniteFloat | None, Field(ge=1)] = None


class FixedMatchingPoint(Contract):
    frequency_hz: Positive
    run_id: str
    native_impedance_ohm: Impedance
    input_impedance_ohm: Impedance
    reflection_magnitude: Annotated[FiniteFloat, Field(ge=0, lt=1)]
    swr: Annotated[FiniteFloat, Field(ge=1)]


class FixedMatchingResult(Contract):
    status: Literal["FIXED_IDEAL_NETWORK_SAMPLED_NOT_VALIDATED"] = "FIXED_IDEAL_NETWORK_SAMPLED_NOT_VALIDATED"
    network: FixedNetwork
    system_impedance_ohm: Positive
    design_sha256: str | None
    physical_design_sha256: str | None = None
    points: tuple[FixedMatchingPoint, ...]
    max_swr: Annotated[FiniteFloat | None, Field(ge=1)]
    worst_sample_swr: Annotated[FiniteFloat, Field(ge=1)]
    target_status: Literal["not_specified", "sampled_target_met", "sampled_target_missed"]
    circuit_library: str
    not_verified: tuple[str, ...] = (
        "between_sample_bandwidth",
        "component_losses_q_parasitics_tolerance_and_power_ratings",
        "balun_common_mode_feedline_and_installation",
    )
    em_solver_runs_this_call: Literal[0] = 0
    llm_calls: Literal[0] = 0


class MatchingSolution(Contract):
    topology: Topology
    components: tuple[Component, Component]
    input_impedance_ohm: Impedance
    reflection_magnitude: Annotated[FiniteFloat, Field(ge=0, lt=1)]
    swr: Positive


class MatchingPoint(Contract):
    frequency_hz: Positive
    run_id: str
    native_impedance_ohm: Impedance
    status: Literal["matched_at_sample", "no_solution_found"]
    solutions: tuple[MatchingSolution, ...]


class MatchingResult(Contract):
    status: Literal["IDEAL_PER_FREQUENCY_MATCH_NOT_VALIDATED"] = "IDEAL_PER_FREQUENCY_MATCH_NOT_VALIDATED"
    options: MatchingOptions
    system_impedance_ohm: Positive
    design_sha256: str | None
    physical_design_sha256: str | None = None
    points: tuple[MatchingPoint, ...]
    requires_retuning_or_switching: bool
    circuit_library: str
    optimizer_library: str
    not_verified: tuple[str, ...] = (
        "one_fixed_network_across_all_bands",
        "between_sample_bandwidth",
        "component_losses_q_parasitics_tolerance_and_power_ratings",
        "balun_common_mode_feedline_and_installation",
    )
    em_solver_runs_this_call: Literal[0] = 0
    llm_calls: Literal[0] = 0


def _component(value: float, frequency: float, connection: Literal["series", "shunt"]) -> Component:
    # The numerical variables are series reactance (ohm) and shunt susceptance (S).
    omega = 2 * math.pi * frequency
    if value == 0:
        return Component(connection=connection, kind="omit", value_si=0, unit="none")
    if connection == "series":
        inductive, number = value > 0, value / omega if value > 0 else -1 / (omega * value)
    else:
        inductive, number = value < 0, -1 / (omega * value) if value < 0 else value / omega
    return Component(
        connection=connection,
        kind="inductor" if inductive else "capacitor",
        value_si=number,
        unit="H" if inductive else "F",
    )


def _solutions(frequency_hz: float, impedance: Impedance, reference: float) -> tuple[MatchingSolution, ...]:
    import numpy as np
    import skrf as rf
    from numpy.typing import NDArray
    from scipy.optimize import least_squares
    from skrf.circuit import Circuit

    frequency = rf.Frequency(frequency_hz, frequency_hz, 1, unit="Hz")
    load = rf.Network(frequency=frequency, z=np.asarray([[[complex(impedance.r, impedance.x)]]]), z0=reference)
    solutions: list[MatchingSolution] = []
    topologies: tuple[Topology, ...] = ("source_shunt_then_series", "series_then_load_shunt")
    for topology in topologies:

        def network(parameters: NDArray[np.float64], arrangement: Topology = topology) -> rf.Network:
            series = Circuit.SeriesImpedance(frequency, 1j * parameters[0] * reference, "series", z0=reference)
            shunt = Circuit.ShuntAdmittance(frequency, 1j * parameters[1] / reference, "shunt", z0=reference)
            return shunt**series**load if arrangement == "source_shunt_then_series" else series**shunt**load

        def residual(parameters: NDArray[np.float64]) -> NDArray[np.float64]:
            reflection = complex(network(parameters).s[0, 0, 0])
            return np.asarray([reflection.real, reflection.imag])

        seen: list[NDArray[np.float64]] = []
        # Bounded public optimizer calls, normalized to Z0. Each topology may
        # yield multiple LC/LL/CC choices; a failed search is not a physical impossibility.
        for guess in product((-1.0, 1.0), repeat=2):
            fit = least_squares(residual, guess, max_nfev=100, ftol=1e-11, xtol=1e-11, gtol=1e-11)
            parameters = np.asarray(fit.x, dtype=float)
            matched = network(parameters)
            reflection = abs(complex(matched.s[0, 0, 0]))
            if not fit.success or not math.isfinite(reflection) or reflection > 1e-7:
                continue
            if any(np.allclose(parameters, earlier, rtol=1e-6, atol=1e-7) for earlier in seen):
                continue
            seen.append(parameters)
            value = complex(matched.z[0, 0, 0])
            solutions.append(
                MatchingSolution(
                    topology=topology,
                    components=(
                        _component(float(parameters[0]) * reference, frequency_hz, "series"),
                        _component(float(parameters[1]) / reference, frequency_hz, "shunt"),
                    ),
                    input_impedance_ohm=Impedance(r=value.real, x=value.imag),
                    reflection_magnitude=reflection,
                    swr=(1 + reflection) / (1 - reflection),
                )
            )
    return tuple(solutions)


def synthesize_matching(evaluation: EvaluationResult, options: MatchingOptions) -> MatchingResult:
    if evaluation.targets.matching != "separate_network" or evaluation.physical_design_sha256 is None:
        raise ValueError("Matching requires saved same-design samples and an explicit separate_network choice")
    points = []
    for point in evaluation.points:
        if point.run_id is None or point.model_input_impedance_ohm is None:
            raise ValueError("Matching requires completed numerical impedance evidence at every requested frequency")
        solutions = _solutions(
            point.frequency_hz, point.model_input_impedance_ohm, evaluation.targets.system_impedance_ohm
        )
        points.append(
            MatchingPoint(
                frequency_hz=point.frequency_hz,
                run_id=point.run_id,
                native_impedance_ohm=point.model_input_impedance_ohm,
                status="matched_at_sample" if solutions else "no_solution_found",
                solutions=solutions,
            )
        )
    return MatchingResult(
        options=options,
        system_impedance_ohm=evaluation.targets.system_impedance_ohm,
        design_sha256=evaluation.design_sha256,
        physical_design_sha256=evaluation.physical_design_sha256,
        points=tuple(points),
        requires_retuning_or_switching=len(points) > 1,
        circuit_library="scikit-rf " + version("scikit-rf"),
        optimizer_library="scipy " + version("scipy"),
    )


def _reactive_value(component: Component, frequency_hz: float) -> complex:
    """Return series impedance or shunt admittance for unchanged, ideal parts."""
    if component.kind == "omit":
        return 0j
    omega_value = 2 * math.pi * frequency_hz * component.value_si
    if not math.isfinite(omega_value) or omega_value == 0:
        raise ValueError("Component value is outside finite circuit arithmetic")
    value = 1j * omega_value if component.kind == "inductor" else 1 / (1j * omega_value)
    if component.connection == "shunt":
        value = 1 / value
    if not math.isfinite(value.real) or not math.isfinite(value.imag):
        raise ValueError("Component value is outside finite circuit arithmetic")
    return value


def evaluate_fixed_matching(evaluation: EvaluationResult, query: FixedMatchingInput) -> FixedMatchingResult:
    """Cascade fixed parts with each saved load; never retune or interpolate missing samples."""
    import numpy as np
    import skrf as rf
    from skrf.circuit import Circuit

    if evaluation.targets != query.evaluation or evaluation.targets.matching != "separate_network":
        raise ValueError("Use the same explicit separate_network evaluation")
    if evaluation.physical_design_sha256 is None:
        raise ValueError("Fixed matching requires verified same-design samples")
    reference = evaluation.targets.system_impedance_ohm
    points = []
    for point in evaluation.points:
        impedance = point.model_input_impedance_ohm
        if point.run_id is None or impedance is None or impedance.r <= 0:
            raise ValueError("Every sample needs completed passive impedance evidence")
        frequency = rf.Frequency(point.frequency_hz, point.frequency_hz, 1, unit="Hz")
        load = rf.Network(frequency=frequency, z=np.asarray([[[complex(impedance.r, impedance.x)]]]), z0=reference)
        series = Circuit.SeriesImpedance(
            frequency, _reactive_value(query.network.components[0], point.frequency_hz), "series", z0=reference
        )
        shunt = Circuit.ShuntAdmittance(
            frequency, _reactive_value(query.network.components[1], point.frequency_hz), "shunt", z0=reference
        )
        matched = shunt**series**load if query.network.topology == "source_shunt_then_series" else series**shunt**load
        reflection = abs(complex(matched.s[0, 0, 0]))
        value = complex(matched.z[0, 0, 0])
        if not math.isfinite(reflection) or reflection >= 1:
            raise ValueError("Circuit has no finite passive input match")
        points.append(
            FixedMatchingPoint(
                frequency_hz=point.frequency_hz,
                run_id=point.run_id,
                native_impedance_ohm=impedance,
                input_impedance_ohm=Impedance(r=value.real, x=value.imag),
                reflection_magnitude=reflection,
                swr=(1 + reflection) / (1 - reflection),
            )
        )
    worst = max(point.swr for point in points)
    return FixedMatchingResult(
        network=query.network,
        system_impedance_ohm=reference,
        design_sha256=evaluation.design_sha256,
        physical_design_sha256=evaluation.physical_design_sha256,
        points=tuple(points),
        max_swr=query.max_swr,
        worst_sample_swr=worst,
        target_status=(
            "not_specified"
            if query.max_swr is None
            else "sampled_target_met"
            if worst <= query.max_swr
            else "sampled_target_missed"
        ),
        circuit_library="scikit-rf " + version("scikit-rf"),
    )
