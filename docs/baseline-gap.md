# Census13 baseline-gap diagnostic

`extstats-research baseline-gap arecel-census13` measures the difference
between the independent AreCELearnedYet full-table PostgreSQL reproduction and
the frozen advisor run. It does not run search, rebuild a Recommendation, or
perform deployment. The paper artifact and canonical run are immutable inputs.

The full-table ladder uses the same 48,842 rows and 10,000 predicates for each
relation. A is the existing paper-schema target-10000 reproduction. B changes
only the ordinary statistics target to 100. C uses the current advisor
`BIGINT`/`TEXT` schema at target 100. D uses that advisor schema at target
10000. Each new relation gets one `ANALYZE`, no extended-statistics definition,
and an exact count check against the frozen GroundTruthSet before EXPLAIN
estimates are collected.

E is different: it counts predicates exactly on the physical 10,000-row frozen
AdvisorSnapshot sample and scales those counts by the frozen population
metadata. E is called the **fixed-sample oracle diagnostic**. It is not a
PostgreSQL estimator and is not an optimization configuration. F and G reuse
the already validated canonical advisor sandbox baseline and final estimates;
G therefore measures selected extended-statistics recovery within the fixed
sample environment.

The diagnostic reports controlled comparisons for statistics target, schema,
sample representation, planner-over-sample error, and extended-statistics
recovery. These comparisons are descriptive. In particular, the paper
full-table baseline is not the advisor sandbox baseline, and no sampling
causality is claimed without the controlled measurements.
