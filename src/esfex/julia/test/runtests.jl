# Native Julia test suite for the ESFEX optimization core.
#
# Run with:  julia --project=src/esfex/julia -e 'using Pkg; Pkg.test(coverage=true)'
# (or `using ESFEX` after activating + instantiating the project).
#
# These are unit tests for the pure / self-contained pieces of the Julia core:
# linear-algebra builders (Y-bus, incidence matrix), AC power-flow flow
# equations, the solver factory, temporal/period mappings, NPV finance, RE-target
# interpolation, representative-day selection, and the plain data structs.
#
# Integration tests that build and solve full optimization models live in the
# Python `julia`-marked suite (tests/test_power_system_parity.py, etc.); those
# exercise create_power_system / build_*! / add_*! / extract_* end to end, so
# they are intentionally NOT duplicated here.

using Test
using ESFEX
using JuMP: optimize!, termination_status
# Pre-import Ipopt into Main so create_optimizer's lazy ("ipopt") path resolves
# the optimizer type without the world-age @eval import (see _load_solver_module).
import Ipopt

@testset "ESFEX" begin

    @testset "version" begin
        @test ESFEX.version() == "0.1.0"
        @test ESFEX.version() isa String
    end

    @testset "hours_in_year" begin
        @test ESFEX.HOURS_STD_YEAR == 8760
        @test ESFEX.hours_in_year(2025) == 8760  # common year
        @test ESFEX.hours_in_year(2024) == 8784  # leap year
        @test ESFEX.hours_in_year(2000) == 8784  # divisible by 400 -> leap
        @test ESFEX.hours_in_year(1900) == 8760  # divisible by 100 not 400 -> common
    end

    @testset "parse_acopf_formulation" begin
        @test parse_acopf_formulation("acopf_soc") isa ESFEX.SOCFormulation
        @test parse_acopf_formulation("acopf_qc") isa ESFEX.QCFormulation
        @test parse_acopf_formulation("acopf_sdp") isa ESFEX.SDPFormulation
        @test parse_acopf_formulation("acopf_polar") isa ESFEX.PolarNLPFormulation
        @test parse_acopf_formulation("acopf_rect") isa ESFEX.RectNLPFormulation
        @test_throws ErrorException parse_acopf_formulation("not_a_mode")
        # Every result is a concrete subtype of the formulation abstract type.
        for m in ("acopf_soc", "acopf_qc", "acopf_sdp", "acopf_polar", "acopf_rect")
            @test parse_acopf_formulation(m) isa ESFEX.ACOPFFormulation
        end
    end

    @testset "build_incidence_matrix" begin
        # Two lines on a 3-bus path: 1->2, 2->3.
        K = build_incidence_matrix(3, [(1, 2), (2, 3)])
        @test size(K) == (3, 2)
        # +1 leaving the "from" bus, -1 entering the "to" bus.
        @test K[1, 1] == 1.0 && K[2, 1] == -1.0
        @test K[2, 2] == 1.0 && K[3, 2] == -1.0
        # Bus not on a line has no entry; every column is balanced.
        @test K[3, 1] == 0.0
        @test all(==(0.0), sum(K, dims = 1))
        # No lines -> an (n, 0) matrix.
        @test size(build_incidence_matrix(4, Tuple{Int,Int}[])) == (4, 0)

        # A small mesh: every column has exactly one +1 and one -1, all
        # entries are in {-1, 0, +1}, and there are exactly 2 nnz per line.
        K2 = build_incidence_matrix(4, [(1, 2), (2, 3), (1, 4), (3, 4)])
        @test size(K2) == (4, 4)
        @test all(x -> x in (-1.0, 0.0, 1.0), K2)
        @test count(!=(0.0), K2) == 2 * 4
        for col in 1:4
            @test count(==(1.0), K2[:, col]) == 1
            @test count(==(-1.0), K2[:, col]) == 1
        end
    end

    # A helper to spell out the 12 positional fields of TransmissionLineData
    # once; keeps the flow/Ybus tests readable.
    line(; from = 1, to = 2, x = 0.1, r = 0.01, b = 0.0, circuits = 1) =
        TransmissionLineData("L", from, to, 100.0, x, r, b, 10.0, 220.0,
                             circuits, 50.0, "AC")

    @testset "build_ybus" begin
        # Single lossy line, no shunt: Y is the textbook 2x2 series-admittance
        # stamp. Diagonals carry +y_series, off-diagonals -y_series.
        ys = 1.0 / complex(0.01, 0.1)
        Y = build_ybus([line()], TransformerData[], 2)
        @test size(Y) == (2, 2)
        @test Y[1, 1] ≈ ys
        @test Y[2, 2] ≈ ys
        @test Y[1, 2] ≈ -ys
        @test Y[2, 1] ≈ -ys
        @test Y ≈ permutedims(Y)              # symmetric for a passive line
        @test imag(Y[1, 1]) < 0               # inductive series branch

        # Line shunt susceptance adds +j*b/2 to each diagonal only.
        Yb = build_ybus([line(b = 0.06)], TransformerData[], 2)
        @test Yb[1, 1] ≈ ys + im * 0.03
        @test Yb[2, 2] ≈ ys + im * 0.03
        @test Yb[1, 2] ≈ -ys                  # off-diagonals unaffected by shunt

        # Parallel circuits halve the effective series impedance, so the
        # series admittance doubles relative to a single circuit.
        Yp = build_ybus([line(x = 0.2, r = 0.02, circuits = 2)],
                        TransformerData[], 2)
        @test Yp[1, 2] ≈ -1.0 / complex(0.01, 0.1)

        # Zero-impedance lines are skipped (matrix stays all-zero).
        Yz = build_ybus([line(x = 0.0, r = 0.0)], TransformerData[], 2)
        @test all(==(0.0 + 0.0im), Yz)

        # No branches at all -> an explicit zero matrix of the right size.
        @test all(==(0.0 + 0.0im), build_ybus(TransmissionLineData[],
                                              TransformerData[], 3))
    end

    @testset "classify_buses" begin
        # With no generators, every bus is PQ (type 1) except the slack (3).
        types = classify_buses(zeros(Float64, 0, 4), 4, GeneratorConfig[], 2)
        @test types == [1, 3, 1, 1]
        @test types[2] == 3
        @test all(t -> t in (1, 2, 3), types)
        # Slack on the last bus.
        @test classify_buses(zeros(Float64, 0, 3), 3, GeneratorConfig[], 3) ==
              [1, 1, 3]
    end

    @testset "calculate_line_flows" begin
        # Flat voltage, zero angle difference: no active flow and no losses.
        pf, pt, qf, qt, pl, ql =
            calculate_line_flows([1.0, 1.0], [0.0, 0.0], [line()],
                                 TransformerData[], 100.0)
        @test length(pf) == length(pt) == length(pl) == 1
        @test pf[1] ≈ 0.0 atol = 1e-9
        @test pt[1] ≈ 0.0 atol = 1e-9
        @test pl[1] ≈ 0.0 atol = 1e-9

        # Lossless line (r = 0) at a positive angle difference: power leaves
        # the leading-angle bus and the two ends cancel (zero real loss).
        llf = calculate_line_flows([1.0, 1.0], [0.1, 0.0], [line(r = 0.0)],
                                   TransformerData[], 100.0)
        p_from, p_to, p_loss = llf[1][1], llf[2][1], llf[5][1]
        @test p_from > 0.0                    # flows from bus 1 (higher θ)
        @test p_loss ≈ 0.0 atol = 1e-6        # no resistance -> no loss

        # Resistive line dissipates positive real power.
        lossy = calculate_line_flows([1.0, 1.0], [0.1, 0.0], [line(r = 0.01)],
                                     TransformerData[], 100.0)
        @test lossy[5][1] > 0.0

        # Results scale linearly with the MVA base.
        a = calculate_line_flows([1.0, 1.0], [0.1, 0.0], [line(r = 0.01)],
                                 TransformerData[], 100.0)
        b = calculate_line_flows([1.0, 1.0], [0.1, 0.0], [line(r = 0.01)],
                                 TransformerData[], 200.0)
        @test b[1][1] ≈ 2 * a[1][1]
        @test b[5][1] ≈ 2 * a[5][1]
    end

    @testset "create_optimizer" begin
        # Default + named solvers return an MOI optimizer factory (no solve).
        @test create_optimizer() isa ESFEX.MOI.OptimizerWithAttributes
        @test create_optimizer(solver_name = "highs") isa
              ESFEX.MOI.OptimizerWithAttributes
        # Solver name is case-insensitive (lowercased internally).
        @test create_optimizer(solver_name = "HIGHS") isa
              ESFEX.MOI.OptimizerWithAttributes
        # User options merge in without error.
        @test create_optimizer(solver_name = "highs", threads = 1, gap = 1e-4,
                               solver_options = Dict{String,Any}("solver" => "simplex")) isa
              ESFEX.MOI.OptimizerWithAttributes
        # Unknown solver names are rejected.
        @test_throws ErrorException create_optimizer(solver_name = "no_such_solver")
    end

    @testset "_compute_year_target" begin
        f = ESFEX._compute_year_target
        # First year always returns the initial penetration.
        @test f(1, 5, 0.2, 0.8, 0.0, 1.0, 0.0) == 0.2
        # Single-year horizon (reached only when y_idx != 1) jumps to target.
        @test f(2, 1, 0.2, 0.8, 0.0, 1.0, 0.0) == 0.8
        # Unconstrained linear interpolation: midpoint of 0.2 -> 0.8 over 5y.
        @test f(3, 5, 0.2, 0.8, 0.0, 1.0, 0.35) ≈ 0.5
        # Minimum-increment floor binds (raw step 0.05 < min 0.2).
        @test f(3, 5, 0.2, 0.8, 0.2, 1.0, 0.45) ≈ 0.65
        # Maximum-increment cap binds (raw step 0.5 > max 0.1).
        @test f(3, 5, 0.2, 0.8, 0.0, 0.1, 0.0) ≈ 0.1
    end

    @testset "select_representative_days" begin
        # Three days of flat demand with a single spike inside day 2 (the peak).
        demand = ones(Float64, 72, 1)
        demand[30, 1] = 100.0  # hour 30 lies in day 2 (hours 25:48)

        # num_days = 1 -> exactly the peak day's start index (1 + 1*24 = 25).
        @test select_representative_days(demand, 1, 1, 1, 24, 72) == [25]

        # num_days = 3 -> peak day plus one per remaining segment; all three
        # day-start indices, peak first.
        sel = select_representative_days(demand, 1, 3, 1, 24, 72)
        @test length(sel) == 3
        @test sel[1] == 25                       # peak day always first
        @test Set(sel) == Set([1, 25, 49])
        @test all(i -> (i - 1) % 24 == 0, sel)   # all are day boundaries

        # Fewer timesteps than a full day -> nothing selectable.
        @test select_representative_days(ones(10, 1), 1, 3, 1, 24, 10) == Int[]
        # year_idx past the end of the demand matrix -> empty.
        @test select_representative_days(demand, 5, 1, 1, 24, 72) == Int[]
    end

    @testset "compute_upscaling_indices" begin
        # points_per_day <= 0 disables upscaling: every hour is its own point.
        idx, tmap = ESFEX.compute_upscaling_indices(24, 0)
        @test idx == collect(0:24)
        @test tmap[0] == 1 && tmap[24] == 25
        # points_per_day >= 24 also means full resolution.
        @test ESFEX.compute_upscaling_indices(24, 24)[1] == collect(0:24)

        # 6 points/day over 48h: 4-hourly sampling, endpoints always present,
        # strictly increasing, and the map sends every hour to a valid slot.
        idx6, tmap6 = ESFEX.compute_upscaling_indices(48, 6)
        @test first(idx6) == 0
        @test last(idx6) == 48                   # final hour forced in
        @test issorted(idx6)
        @test allunique(idx6)
        @test length(tmap6) == 49                # one entry per hour 0:48
        @test all(p -> 1 <= p <= length(idx6), values(tmap6))
        @test tmap6[0] == 1                       # hour 0 maps to the first slot
    end

    @testset "calculate_unit_npv" begin
        # End-of-life unit: NPV is just the (negative) decommissioning cost.
        @test calculate_unit_npv(100.0, 20.0, 5.0, 5.0, 0.0, 0.0, 50.0,
                                 0.05, 0.6, 100.0) == -50.0
        # Zero capacity contributes nothing.
        @test calculate_unit_npv(0.0, 20.0, 5.0, 5.0, 25.0, 0.0, 50.0,
                                 0.05, 0.6, 100.0) == 0.0

        # One year, no degradation/discount/decommissioning: NPV equals the
        # single-year net cash flow = generation * (lcoe - total_unit_cost).
        gen = 100.0 * 0.5 * Float64(ESFEX.HOURS_STD_YEAR)  # MWh
        expected = gen * (100.0 - (20.0 + 5.0 + 5.0))
        @test calculate_unit_npv(100.0, 20.0, 5.0, 5.0, 1.0, 0.0, 0.0,
                                 0.0, 0.5, 100.0) ≈ expected

        # A unit whose price exceeds its costs has positive NPV; flip the price
        # below cost and the NPV goes negative.
        @test calculate_unit_npv(100.0, 20.0, 5.0, 5.0, 10.0, 0.0, 0.0,
                                 0.05, 0.6, 120.0) > 0
        @test calculate_unit_npv(100.0, 60.0, 10.0, 10.0, 10.0, 0.0, 0.0,
                                 0.05, 0.6, 50.0) < 0

        # Adding a decommissioning cost can only lower NPV, never raise it.
        without = calculate_unit_npv(100.0, 20.0, 5.0, 5.0, 10.0, 0.0, 0.0,
                                     0.05, 0.6, 120.0)
        with = calculate_unit_npv(100.0, 20.0, 5.0, 5.0, 10.0, 0.0, 1.0e6,
                                  0.05, 0.6, 120.0)
        @test with < without
    end

    @testset "create_temporal_mapping" begin
        # 48 hours in 24-hour primary periods, one investment period spanning all.
        tm = create_temporal_mapping(48, 24, 48)
        @test tm.num_primary_periods == 2
        @test tm.num_investment_periods == 1
        @test tm.hours_in_primary_period[1] == collect(1:24)
        @test tm.hours_in_primary_period[2] == collect(25:48)
        @test tm.hour_to_primary_period[1] == 1
        @test tm.hour_to_primary_period[24] == 1
        @test tm.hour_to_primary_period[25] == 2
        @test tm.hour_to_primary_period[48] == 2
        @test tm.primary_to_investment_period[1] == 1
        @test tm.primary_to_investment_period[2] == 1
        @test Set(tm.primary_periods_in_investment_period[1]) == Set([1, 2])

        # The primary periods partition the hours exactly: every hour mapped,
        # no hour mapped twice.
        all_hours = vcat((tm.hours_in_primary_period[p] for p in 1:tm.num_primary_periods)...)
        @test sort(all_hours) == collect(1:48)
        @test length(tm.hour_to_primary_period) == 48

        # Non-divisible horizon uses ceiling division for the period count.
        tm2 = create_temporal_mapping(10, 3, 10)
        @test tm2.num_primary_periods == 4          # cld(10, 3)
        @test tm2.hours_in_primary_period[4] == [10] # short tail period
        @test tm2.hour_to_primary_period[10] == 4

        # Non-positive resolution collapses to a single full-horizon period.
        tm3 = create_temporal_mapping(12, 0, 0)
        @test tm3.num_primary_periods == 1
        @test tm3.hours_in_primary_period[1] == collect(1:12)
    end

    @testset "data structs" begin
        # Plain immutable data carriers: field round-trip on construction.
        @testset "TransmissionLineData" begin
            l = TransmissionLineData("L7", 3, 9, 250.0, 0.05, 0.005, 0.02,
                                     120.0, 380.0, 2, 50.0, "AC")
            @test l.line_id == "L7"
            @test l.from_node == 3 && l.to_node == 9
            @test l.capacity_mw == 250.0
            @test l.num_circuits == 2
            @test l.current_type == "AC"
        end

        @testset "BusData" begin
            b = BusData(5, 2, 220.0, 50.0, "AC", "PV", "load", 0.4)
            @test b.bus_id == 5 && b.parent_node == 2
            @test b.bus_type == "PV" && b.role == "load"
            @test b.demand_fraction == 0.4
        end

        @testset "TransformerData" begin
            t = TransformerData("T1", 1, 2, 380.0, 220.0, 500.0, 0.12, 0.01,
                                0.1196, 1.05, 0.004)
            @test t.name == "T1"
            @test t.from_voltage_kv == 380.0 && t.to_voltage_kv == 220.0
            @test t.tap_ratio == 1.05
        end

        @testset "CostSegment" begin
            cs = CostSegment(0.25, 42.0)
            @test cs.fraction == 0.25
            @test cs.marginal_cost == 42.0
        end

        @testset "TemporalConfig / PenaltyConfig / TargetConfig / SolverSettings" begin
            tc = TemporalConfig(8760, 1, 24, 0, 8760, 168, 6, 6, 4)
            @test tc.hours == 8760 && tc.reserve_resolution == 4
            pc = PenaltyConfig(1.0, 2e-5, 0.01, 0.01, 0.05)
            @test pc.loss_of_load == 1.0 && pc.co2_cost == 0.05
            tg = TargetConfig(0.8, 1.0e6, 5000.0)
            @test tg.re_penetration_target == 0.8 && tg.inertia_limit == 5000.0
            ss = SolverSettings(4, 3600.0, 0.01, false)
            @test ss.threads == 4 && ss.verbose == false
        end

        @testset "SystemNodeRange" begin
            s = SystemNodeRange("North", 1, 5, 0.3)
            @test s.name == "North" && s.first_bus == 1
            @test s.num_buses == 5 && s.initial_re == 0.3
        end

        @testset "ScenarioMultipliers / Scenario" begin
            m = ScenarioMultipliers()  # all-1.0 default
            @test all(getfield(m, f) == 1.0 for f in fieldnames(ScenarioMultipliers))
            @test m.invest_cost_renewables == 1.0 && m.carbon_price == 1.0
            sc = Scenario("base", 0.5, m)
            @test sc.name == "base" && sc.probability == 0.5
            @test sc.multipliers === m
        end

        @testset "InterSystemLink back-compat constructors" begin
            # 9-arg form fills cost_per_mw_km=1.0, reactance=0.01, resistance=0.001.
            l9 = InterSystemLink("A", "B", 1, 2, 100.0, 500.0, 50.0, 0.05, 300.0)
            @test l9.from_system == "A" && l9.to_system == "B"
            @test l9.cost_per_mw_km == 1.0
            @test l9.reactance_pu == 0.01
            @test l9.resistance_pu == 0.001
            # 10-arg form takes cost_per_mw_km explicitly, keeps the impedance defaults.
            l10 = InterSystemLink("A", "B", 1, 2, 100.0, 500.0, 50.0, 0.05, 300.0, 0.02)
            @test l10.cost_per_mw_km == 0.02
            @test l10.reactance_pu == 0.01
            @test l10.resistance_pu == 0.001
        end

        @testset "MGAResult back-compat constructor" begin
            # Labels auto-fill to "hsj_diversity", one per diversity objective.
            mga = MGAResult(MasterProblemResult[], 2, 0.1, 500.0,
                            [500.0, 510.0], [0.0, 5.0])
            @test mga.slack_fraction == 0.1
            @test mga.optimal_cost == 500.0
            @test mga.objective_labels == ["hsj_diversity", "hsj_diversity"]
            # Empty objective list -> empty (typed) label vector.
            mga0 = MGAResult(MasterProblemResult[], 0, 0.05, 1.0,
                             Float64[], Float64[])
            @test mga0.objective_labels == String[]
        end
    end

    # =======================================================================
    # Integration: build and SOLVE small models end to end in pure Julia.
    # Unlike the pure-function tests above, these drive the model-construction
    # core (create_power_system -> build_variables!/build_objective!/
    # add_*_constraints! -> extract_solution) and the standalone DCOPF/ACOPF
    # benchmark solvers. HiGHS solves the LPs; Ipopt solves the ACOPF NLP.
    # =======================================================================
    @testset "integration" begin

        # GeneratorConfig has 39 positional fields; this keeps the boilerplate
        # in one place. Per-bus vectors are sized to `nb`; `avail` is the
        # [hours x buses] availability matrix. All reservoir fields are zeroed
        # (no hydro), reservable=false.
        function mkgen(name, type, fuel, rated, fcost, avail; nb, H)
            z = zeros(nb)
            GeneratorConfig(
                name, type, fuel, rated, zeros(nb),
                fill(0.5, nb), fill(0.4, nb), ones(nb), ones(nb), z, z, z,
                fcost, z, z, z, z, z, avail, false,
                fill(30.0, nb), z, z, z, 50.0, "AC",
                z, z, z, z, zeros(H, nb), z, z, z, z, false, z, z, z,
            )
        end

        @testset "economic dispatch (single bus)" begin
            H = 2
            gen = mkgen("GasCC", "Non-renewable", "Gas", [100.0],
                        [3.0e-5], ones(H, 1); nb = 1, H = H)
            bus = BusData(1, 1, 220.0, 50.0, "AC", "slack", "mixed", 1.0)
            net = NetworkConfig(1, 1, [bus], [1], zeros(1, 1), zeros(1, 1),
                                100.0, 0.4, 220.0, 0.5, 1, [0.0], [0.0],
                                TransmissionLineData[], TransformerData[],
                                ACDCConverterData[], FrequencyConverterData[], 0.1)
            input = PowerSystemInput(
                name = "ed", year = 2025, network = net, generators = [gen],
                batteries = BatteryConfig[], demand = reshape([50.0, 60.0], H, 1),
                temporal = TemporalConfig(H, 1, H, 0, H, H, 1, 1, 1),
                mode = "economic_dispatch", solver_name = "highs", verbose = false)

            model, vars = create_power_system(input)
            optimize!(model)
            @test string(termination_status(model)) == "OPTIMAL"
            res = extract_solution(model, vars, input)
            @test res.total_demand ≈ 110.0
            @test res.total_generation ≈ 110.0 atol = 1e-6   # gen serves all load
            @test res.load_shed_total ≈ 0.0 atol = 1e-6      # nothing shed
            @test isfinite(res.objective) && res.objective > 0
            @test size(res.gen_output) == (1, 1, H)
        end

        @testset "custom constraints (linear cap binds)" begin
            H = 2
            gen = mkgen("GasCC", "Non-renewable", "Gas", [100.0],
                        [3.0e-5], ones(H, 1); nb = 1, H = H)
            bus = BusData(1, 1, 220.0, 50.0, "AC", "slack", "mixed", 1.0)
            net = NetworkConfig(1, 1, [bus], [1], zeros(1, 1), zeros(1, 1),
                                100.0, 0.4, 220.0, 0.5, 1, [0.0], [0.0],
                                TransmissionLineData[], TransformerData[],
                                ACDCConverterData[], FrequencyConverterData[], 0.1)
            mkinput() = PowerSystemInput(
                name = "cc", year = 2025, network = net, generators = [gen],
                batteries = BatteryConfig[], demand = reshape([50.0, 60.0], H, 1),
                temporal = TemporalConfig(H, 1, H, 0, H, H, 1, 1, 1),
                mode = "economic_dispatch", solver_name = "highs", verbose = false)

            # A linear cap on total generator output forces 30 MWh of shedding.
            cc = [Dict("name" => "cap_gen", "type" => "linear", "sense" => "<=",
                       "rhs" => 80.0,
                       "terms" => [Dict("variable" => "gen_output",
                                        "index" => [1, -1], "coefficient" => 1.0)])]
            inp = mkinput()
            model, vars = create_power_system(inp; custom_constraints = cc)
            optimize!(model)
            @test string(termination_status(model)) == "OPTIMAL"
            res = extract_solution(model, vars, inp)
            @test res.total_generation ≈ 80.0 atol = 1e-4
            @test res.load_shed_total ≈ 30.0 atol = 1e-4

            # Unknown constraint type errors clearly.
            @test_throws ErrorException create_power_system(
                mkinput();
                custom_constraints = [Dict("name" => "x", "type" => "bogus")])
        end

        @testset "reservoir minimum release binds" begin
            # A single reservoir-hydro unit on a flat, low demand. Without a
            # minimum release it simply turbines to meet load (no spill). A
            # min release above the natural turbined flow must FORCE extra
            # water out — turbined power is capped by demand, so the surplus
            # is spilled. The constraint is gen_output/η + spill >= min_release.
            H = 4
            # 40-field GeneratorConfig: a dispatchable reservoir unit. Plenty of
            # stored water, no inflow (pure draw-down), spillage allowed.
            mkhydro(minrel) = GeneratorConfig(
                "Hydro", "Renewable", "Water", [100.0], [0.0],
                [1.0], [1.0], [100.0], [100.0], [0.0], [0.0], [0.0],
                [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], ones(H, 1), true,
                [60.0], [0.0], [0.0], [0.0], 50.0, "AC",
                [500.0], [200.0], [0.0], [500.0], zeros(H, 1),
                [1.0], [0.0], [0.0], [1.0], true, [0.0], [0.0], [0.0],
                [minrel],
            )
            bus = BusData(1, 1, 220.0, 50.0, "AC", "slack", "mixed", 1.0)
            net = NetworkConfig(1, 1, [bus], [1], zeros(1, 1), zeros(1, 1),
                                100.0, 0.4, 220.0, 0.5, 1, [0.0], [0.0],
                                TransmissionLineData[], TransformerData[],
                                ACDCConverterData[], FrequencyConverterData[], 0.1)
            demand = reshape(fill(10.0, H), H, 1)
            mkinput(g) = PowerSystemInput(
                name = "hyd", year = 2025, network = net, generators = [g],
                batteries = BatteryConfig[], demand = demand,
                temporal = TemporalConfig(H, 1, H, 0, H, H, 1, 1, 1),
                mode = "economic_dispatch", solver_name = "highs", verbose = false)

            # Baseline: no minimum release.
            m0, v0 = create_power_system(mkinput(mkhydro(0.0)))
            optimize!(m0)
            @test string(termination_status(m0)) == "OPTIMAL"
            r0 = extract_solution(m0, v0, mkinput(mkhydro(0.0)))
            @test r0.reservoir_spillage !== nothing
            spill0 = sum(r0.reservoir_spillage)
            @test spill0 ≈ 0.0 atol = 1e-6          # nothing forced out

            # Floor of 20 MW-eq > the 10 MW it would naturally turbine.
            minrel = 20.0
            m1, v1 = create_power_system(mkinput(mkhydro(minrel)))
            optimize!(m1)
            @test string(termination_status(m1)) == "OPTIMAL"
            r1 = extract_solution(m1, v1, mkinput(mkhydro(minrel)))
            # Generation is unchanged — the unit still serves only the load
            # (plus network losses) each hour; the floor forces extra water out
            # as spill, it does not raise output.
            for t in 1:H
                @test isapprox(r1.gen_output[1, 1, t], r0.gen_output[1, 1, t];
                               atol = 1e-6)
            end
            # The constraint binds every hour: gen/η_turbine + spill >= min_release.
            for t in 1:H
                @test r1.gen_output[1, 1, t] / 1.0 +
                      r1.reservoir_spillage[1, 1, t] >= minrel - 1e-6
            end
            # And it genuinely changed the solution: spillage appears only when
            # the floor is imposed.
            @test sum(r1.reservoir_spillage) > spill0 + 1.0
        end

        @testset "hydraulic cascade feeds downstream reservoir" begin
            # Two reservoirs in series on a single bus. "Up" has inflow and is
            # forced (min_release) to pass 40 MW-eq of water each hour; its low
            # turbine cap means most of that leaves as spill. "Down" has no
            # inflow of its own — only the cascade can give it water. With the
            # link Down generates and displaces the expensive gas; without it
            # Down stays dry.
            H = 4
            # 42-field reservoir generator with a cascade target.
            mkres(name, rated, init_frac, inflow_mw, minrel, downstream) =
                GeneratorConfig(
                    name, "Renewable", "Water", [rated], [0.0],
                    [1.0], [1.0], [100.0], [100.0], [0.0], [0.0], [0.0],
                    [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], ones(H, 1), true,
                    [60.0], [0.0], [0.0], [0.0], 50.0, "AC",
                    [1000.0], [init_frac], [0.0], [1.0], fill(inflow_mw, H, 1),
                    [1.0], [0.0], [0.0], [1.0], true, [0.0], [0.0], [0.0],
                    [minrel], downstream, 0,
                )
            gas = mkgen("Gas", "Non-renewable", "Gas", [200.0],
                        [1.0], ones(H, 1); nb = 1, H = H)  # expensive backup
            bus = BusData(1, 1, 220.0, 50.0, "AC", "slack", "mixed", 1.0)
            net = NetworkConfig(1, 1, [bus], [1], zeros(1, 1), zeros(1, 1),
                                100.0, 0.4, 220.0, 0.5, 1, [0.0], [0.0],
                                TransmissionLineData[], TransformerData[],
                                ACDCConverterData[], FrequencyConverterData[], 0.1)
            demand = reshape(fill(50.0, H), H, 1)
            mkinput(up, down) = PowerSystemInput(
                name = "casc", year = 2025, network = net,
                generators = [up, down, gas],
                batteries = BatteryConfig[], demand = demand,
                temporal = TemporalConfig(H, 1, H, 0, H, H, 1, 1, 1),
                mode = "economic_dispatch", solver_name = "highs", verbose = false)

            down = mkres("Down", 100.0, 0.0, 0.0, 0.0, "")  # dry, terminal
            # Up passes 40 MW-eq/h (turbine cap 10 -> ~30 spilled), inflow 40 keeps
            # it water-balanced over the cyclic period.
            up_linked = mkres("Up", 10.0, 0.5, 40.0, 40.0, "Down")
            up_isolated = mkres("Up", 10.0, 0.5, 40.0, 40.0, "")

            mc, vc = create_power_system(mkinput(up_linked, down))
            optimize!(mc)
            @test string(termination_status(mc)) == "OPTIMAL"
            rc = extract_solution(mc, vc, mkinput(up_linked, down))
            down_gen_linked = sum(rc.gen_output[2, 1, :])

            mi, vi = create_power_system(mkinput(up_isolated, down))
            optimize!(mi)
            @test string(termination_status(mi)) == "OPTIMAL"
            ri = extract_solution(mi, vi, mkinput(up_isolated, down))
            down_gen_isolated = sum(ri.gen_output[2, 1, :])

            # The cascade is the only water source for Down: it generates with
            # the link and is essentially idle without it.
            @test down_gen_linked > 100.0
            @test down_gen_isolated < 1.0
            # Letting Down run on cascade water serves load that is otherwise
            # unmet/expensive -> the linked case is strictly cheaper.
            @test rc.objective < ri.objective - 0.5
        end

        @testset "head dependence limits power at low reservoir level" begin
            # A reservoir held at a low level (10% full) with ample inflow to
            # meet demand. With no head effect the turbine reaches nameplate
            # power; with head_min_factor = 0.3 the low head caps output near
            # 0.3 + 0.7*0.1 = 0.37 of rated, so the unit cannot serve the load
            # and must spill water / lean on gas.
            H = 2
            mkhead(factor) = GeneratorConfig(
                "Hydro", "Renewable", "Water", [100.0], [0.0],
                [1.0], [1.0], [100.0], [100.0], [0.0], [0.0], [0.0],
                [0.0], [0.0], [0.0], [0.0], [0.0], [0.0], ones(H, 1), true,
                [60.0], [0.0], [0.0], [0.0], 50.0, "AC",
                [1000.0], [0.1], [0.0], [1.0], fill(80.0, H, 1),
                [1.0], [0.0], [0.0], [1.0], true, [0.0], [0.0], [0.0],
                [0.0], "", 0, [factor],  # min_release, cascade_*, head_min_factor
            )
            gas = mkgen("Gas", "Non-renewable", "Gas", [200.0],
                        [1.0], ones(H, 1); nb = 1, H = H)
            bus = BusData(1, 1, 220.0, 50.0, "AC", "slack", "mixed", 1.0)
            net = NetworkConfig(1, 1, [bus], [1], zeros(1, 1), zeros(1, 1),
                                100.0, 0.4, 220.0, 0.5, 1, [0.0], [0.0],
                                TransmissionLineData[], TransformerData[],
                                ACDCConverterData[], FrequencyConverterData[], 0.1)
            demand = reshape(fill(80.0, H), H, 1)
            mkinput(h) = PowerSystemInput(
                name = "head", year = 2025, network = net,
                generators = [h, gas],
                batteries = BatteryConfig[], demand = demand,
                temporal = TemporalConfig(H, 1, H, 0, H, H, 1, 1, 1),
                mode = "economic_dispatch", solver_name = "highs", verbose = false)

            mf, vf = create_power_system(mkinput(mkhead(1.0)))   # no head effect
            optimize!(mf)
            @test string(termination_status(mf)) == "OPTIMAL"
            rf = extract_solution(mf, vf, mkinput(mkhead(1.0)))
            hydro_full = sum(rf.gen_output[1, 1, :])

            md, vd = create_power_system(mkinput(mkhead(0.3)))   # head-limited
            optimize!(md)
            @test string(termination_status(md)) == "OPTIMAL"
            rd = extract_solution(md, vd, mkinput(mkhead(0.3)))
            hydro_derated = sum(rd.gen_output[1, 1, :])

            # Without the head limit the unit serves the full 80 MW load each
            # hour; with it, output is throttled by the low head.
            @test hydro_full > 150.0
            # Hour 1 starts at the fixed initial level (10% full), so the cap is
            # deterministic: 0.3 + 0.7*0.1 = 0.37 of the 100 MW rating.
            @test rd.gen_output[1, 1, 1] <= 37.0 + 1e-3
            # Aggregate: head-limited hydro produces far less over the window.
            @test hydro_full > hydro_derated * 1.5
            # The throttled hydro is replaced by gas -> more expensive.
            @test rd.objective > rf.objective + 0.5
        end

        @testset "two-bus transmission + renewable + battery" begin
            H = 2; N = 2
            gas  = mkgen("GasCC", "Non-renewable", "Gas", [100.0, 0.0],
                         [3.0e-5, 3.0e-5], ones(H, N); nb = N, H = H)
            wind = mkgen("Wind", "Renewable", "Wind", [0.0, 80.0],
                         [0.0, 0.0], [0.0 0.6; 0.0 0.9]; nb = N, H = H)
            buses = [BusData(1, 1, 220.0, 50.0, "AC", "slack", "mixed", 1.0),
                     BusData(2, 2, 220.0, 50.0, "AC", "PQ", "load", 1.0)]
            line = TransmissionLineData("L12", 1, 2, 150.0, 0.1, 0.01, 0.0,
                                        100.0, 220.0, 1, 50.0, "AC")
            net = NetworkConfig(N, N, buses, [1, 2],
                                [0.0 150.0; 150.0 0.0], [0.0 100.0; 100.0 0.0],
                                100.0, 0.4, 220.0, 0.6, 1, [0.0, 0.0], [0.0, 0.0],
                                [line], TransformerData[], ACDCConverterData[],
                                FrequencyConverterData[], 0.1)
            bat = BatteryConfig("Li1", [0.0, 40.0], [0.0, 20.0], [0.0, 20.0],
                                [0.95, 0.95], [0.95, 0.95], [0.0, 0.1], [1.0, 1.0],
                                [0.5, 0.5], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0],
                                [0.0, 0.0], [0.0, 0.0], [10.0, 10.0], [0.0, 0.0],
                                [0.0, 0.0], 0.0, 100.0, [0.0, 0.0], [0.0, 0.0],
                                false, "AC", [0.0, 0.0], [0.0, 0.0], [0.0, 0.0])
            input = PowerSystemInput(
                name = "2bus", year = 2025, network = net,
                generators = [gas, wind], batteries = [bat],
                demand = [30.0 70.0; 40.0 90.0],
                temporal = TemporalConfig(H, 1, H, 0, H, H, 1, 1, 1),
                mode = "economic_dispatch", solver_name = "highs", verbose = false)

            model, vars = create_power_system(input)
            optimize!(model)
            @test string(termination_status(model)) == "OPTIMAL"
            res = extract_solution(model, vars, input)
            @test res.total_demand ≈ 230.0
            @test res.load_shed_total ≈ 0.0 atol = 1e-6   # demand fully served
            # Generation need not equal demand: the battery's initial SOC can
            # supply net energy, and the resistive line adds losses.
            @test isfinite(res.total_generation) && res.total_generation > 0.0
            @test 0.0 < res.re_penetration ≤ 1.0          # wind contributes
            @test haskey(res.power_flow, (1, 2))          # line flow extracted
        end

        @testset "solve_dcopf benchmark" begin
            # Cheap gen at bus 1 supplies a bus-2 load across one line.
            r = solve_dcopf(num_buses = 2, demand = [0.0, 100.0],
                            gen_bus = [1, 2], gen_cost = [10.0, 50.0],
                            gen_max = [200.0, 200.0], line_from = [1],
                            line_to = [2], line_x = [0.1], line_cap = [150.0],
                            slack_bus = 1, base_impedance = 100.0)
            @test r["status"] == "OPTIMAL"
            @test r["total_cost"] ≈ 1000.0           # 100 MW * $10
            @test r["line_flows_mw"][1] ≈ 100.0      # all load flows over the line
            @test r["gen_dispatch_list"][1] ≈ 100.0  # cheap unit covers it
            @test r["gen_dispatch_list"][2] ≈ 0.0 atol = 1e-6
        end

        @testset "solve_acopf benchmark" begin
            r = solve_acopf(num_buses = 2, demand_p = [0.0, 80.0],
                            demand_q = [0.0, 20.0], shunt_g = [0.0, 0.0],
                            shunt_b = [0.0, 0.0], gen_bus = [1],
                            gen_cost = [20.0], gen_pmax = [200.0],
                            gen_pmin = [0.0], gen_qmax = [100.0],
                            gen_qmin = [-100.0], line_from = [1], line_to = [2],
                            line_r = [0.01], line_x = [0.1], line_b = [0.0],
                            line_cap = [200.0], slack_bus = 1, base_mva = 100.0)
            @test r["status"] in ("LOCALLY_SOLVED", "OPTIMAL")
            @test r["total_cost"] > 0
            @test length(r["vm_pu"]) == 2
            @test all(0.85 .≤ r["vm_pu"] .≤ 1.15)    # within voltage bounds
            @test r["gen_dispatch_list"][1] > 0       # slack gen supplies load
        end
    end

end
