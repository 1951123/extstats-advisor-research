def test_runner_import_surface_is_present() -> None:
    from extstats_advisor_research import runner
    from extstats_advisor_research.runs.layout import (
        RunLayout,
        create_layout,
        update_manifest,
    )

    assert RunLayout is not None
    assert create_layout is not None
    assert update_manifest is not None
    assert runner.run_census13 is not None
