"""Resource allocation linear programme. Implements AN-7 (LP + population-proportional baseline) and AN-8
(efficiency-equity curve and the recommended alpha).

Modules:
    assumptions   unit costs, transport volumes and capacity rule, scenario grid (each with its source)
    lp            PuLP/CBC formulation, the baseline, and scenario metrics
    curve         budget x alpha grid, curve data, alpha recommendation
    run           Spark entry point: gold.demand + gold.stock_latest -> gold.allocation + curve CSV/JSON
"""
