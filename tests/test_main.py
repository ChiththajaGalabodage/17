from main import pipeline_status


def test_pipeline_status_reflects_test_outcome() -> None:
    assert pipeline_status(True) == "PASSED"
    assert pipeline_status(False) == "FAILED"