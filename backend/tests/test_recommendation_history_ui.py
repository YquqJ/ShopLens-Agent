from pathlib import Path


def test_recommendation_summary_is_only_rendered_without_candidate_cards():
    source = Path(__file__).parents[2].joinpath(
        "frontend", "src", "components", "ChatView.vue"
    ).read_text(encoding="utf-8")

    assert 'v-if="!turn.recommendation?.items.length && turn.recommendation?.summary"' in source
    assert 'v-else-if="turn.recommendation?.summary"' not in source
