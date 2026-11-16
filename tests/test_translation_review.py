from pathlib import Path

from main import STAFFED_CALLBACK

REVIEW = Path(__file__).resolve().parents[1] / "docs" / "TRANSLATION_REVIEW.md"


def test_translation_review_lists_every_staffed_callback_line():
    doc = REVIEW.read_text("utf-8")
    assert "STAFFED_CALLBACK" in doc
    for line in STAFFED_CALLBACK.values():
        assert line in doc
