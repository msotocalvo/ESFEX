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

end
