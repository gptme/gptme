"""Tests for autocompact scoring functions.

Tests the semantic importance and reference potential scoring used in
extractive compression to determine which sentences to preserve.
"""

from gptme.tools.autocompact import (
    _score_reference_potential,
    _score_semantic_importance,
    score_sentence,
)

# --- _score_semantic_importance tests ---


class TestScoreSemanticImportance:
    """Tests for _score_semantic_importance scoring patterns."""

    # Decision patterns (+2.0)

    def test_decision_well_use(self):
        assert (
            _score_semantic_importance("We'll use PostgreSQL for the database") >= 2.0
        )

    def test_decision_we_will_use(self):
        assert _score_semantic_importance("We will use Redis for caching") >= 2.0

    def test_decision_decided_to(self):
        assert _score_semantic_importance("We decided to go with FastAPI") >= 2.0

    def test_decision_going_with(self):
        assert _score_semantic_importance("Going with the simpler approach") >= 2.0

    def test_decision_choosing(self):
        assert _score_semantic_importance("Choosing SQLAlchemy as the ORM") >= 2.0

    def test_decision_solution_is(self):
        assert (
            _score_semantic_importance("The solution is to use connection pooling")
            >= 2.0
        )

    def test_decision_approach_is(self):
        assert _score_semantic_importance("The approach is to batch requests") >= 2.0

    def test_decision_we_chose(self):
        assert _score_semantic_importance("We chose this because of performance") >= 2.0

    def test_decision_case_insensitive(self):
        assert _score_semantic_importance("DECIDED TO use a queue") >= 2.0
        assert _score_semantic_importance("The SOLUTION IS clear") >= 2.0

    # Conclusion patterns (+1.5)

    def test_conclusion_therefore(self):
        score = _score_semantic_importance("Therefore, we should refactor the module")
        assert score >= 1.5

    def test_conclusion_in_summary(self):
        score = _score_semantic_importance("In summary, the migration was successful")
        assert score >= 1.5

    def test_conclusion_the_result_is(self):
        score = _score_semantic_importance("The result is a 3x speedup")
        assert score >= 1.5

    def test_conclusion_this_means(self):
        score = _score_semantic_importance("This means we can scale horizontally")
        assert score >= 1.5

    def test_conclusion_confirmed_that(self):
        score = _score_semantic_importance("Tests confirmed that the fix works")
        assert score >= 1.5

    def test_conclusion_in_conclusion(self):
        score = _score_semantic_importance("In conclusion, the approach is viable")
        assert score >= 1.5

    def test_conclusion_key_finding(self):
        score = _score_semantic_importance("The key finding was the memory leak")
        assert score >= 1.5

    # Commitment patterns (+1.5)

    def test_commitment_ill(self):
        score = _score_semantic_importance("I'll implement the caching layer next")
        assert score >= 1.5

    def test_commitment_i_will_implement(self):
        score = _score_semantic_importance("I will implement the retry logic")
        assert score >= 1.5

    def test_commitment_i_will_fix(self):
        score = _score_semantic_importance("I will fix the race condition")
        assert score >= 1.5

    def test_commitment_next_steps(self):
        score = _score_semantic_importance("Next steps: deploy to staging")
        assert score >= 1.5

    def test_commitment_action_items(self):
        score = _score_semantic_importance("Action items: review the PR")
        assert score >= 1.5

    def test_commitment_todo(self):
        score = _score_semantic_importance("TODO: add error handling")
        assert score >= 1.5

    def test_commitment_will_implement(self):
        score = _score_semantic_importance("We will implement this in phase 2")
        assert score >= 1.5

    def test_commitment_plan_to(self):
        score = _score_semantic_importance("We plan to release next week")
        assert score >= 1.5

    def test_commitment_going_to_create(self):
        score = _score_semantic_importance("Going to create a new module for this")
        assert score >= 1.5

    # Action result patterns (+1.0)

    def test_action_created_file(self):
        score = _score_semantic_importance("Created file src/utils.py")
        assert score >= 1.0

    def test_action_fixed(self):
        score = _score_semantic_importance("Fixed the null pointer issue")
        assert score >= 1.0

    def test_action_updated(self):
        score = _score_semantic_importance("Updated the configuration file")
        assert score >= 1.0

    def test_action_implemented(self):
        score = _score_semantic_importance("Implemented the new caching strategy")
        assert score >= 1.0

    def test_action_completed(self):
        score = _score_semantic_importance("Completed the migration to v2")
        assert score >= 1.0

    def test_action_merged(self):
        score = _score_semantic_importance("Merged the feature branch")
        assert score >= 1.0

    # No pattern matches (should return 0.0)

    def test_no_match_generic(self):
        assert _score_semantic_importance("This is a generic sentence") == 0.0

    def test_no_match_filler(self):
        assert _score_semantic_importance("Let me look at that") == 0.0

    def test_no_match_empty(self):
        assert _score_semantic_importance("") == 0.0

    # Multiple category matches (should accumulate)

    def test_multiple_categories_decision_and_conclusion(self):
        # "decided to" (+2.0 decision) + "therefore" (+1.5 conclusion)
        score = _score_semantic_importance(
            "Therefore, we decided to use the new approach"
        )
        assert score >= 3.5

    def test_multiple_categories_commitment_and_action(self):
        # "I'll" (+1.5 commitment) + "fixed" (+1.0 action)
        score = _score_semantic_importance("I'll verify that I fixed the issue")
        assert score >= 2.5

    def test_no_double_counting_within_category(self):
        # Multiple decision patterns should only count once (+2.0, not +4.0)
        score = _score_semantic_importance(
            "We decided to go with choosing the solution is clear"
        )
        # Should have decision (+2.0) but NOT 2x decision
        assert score < 6.0  # Max possible is 2.0+1.5+1.5+1.0 = 6.0


# --- _score_reference_potential tests ---


class TestScoreReferencePotential:
    """Tests for _score_reference_potential scoring patterns."""

    # File path patterns (+1.0)

    def test_unix_path(self):
        score = _score_reference_potential("Check the file at /home/user/config.yaml")
        assert score >= 1.0

    def test_unix_path_tilde(self):
        score = _score_reference_potential("Located at ~/projects/myapp/main.py")
        assert score >= 1.0

    def test_unix_path_no_extension(self):
        score = _score_reference_potential("Run /usr/bin/python3")
        assert score >= 1.0

    def test_windows_path_backslash(self):
        score = _score_reference_potential(r"Open C:\Users\dev\project\main.py")
        assert score >= 1.0

    def test_windows_path_forward_slash(self):
        score = _score_reference_potential("Open C:/Users/dev/project/main.py")
        assert score >= 1.0

    # URL patterns (+0.5)

    def test_https_url(self):
        score = _score_reference_potential(
            "See https://docs.python.org/3/library/re.html"
        )
        assert score >= 0.5

    def test_http_url(self):
        score = _score_reference_potential("Visit http://localhost:8080/api/v1")
        assert score >= 0.5

    # Error indicator patterns (+1.5)

    def test_error_keyword(self):
        score = _score_reference_potential("Got an error when connecting to the DB")
        assert score >= 1.5

    def test_exception_keyword(self):
        score = _score_reference_potential("Got an exception in module handler")
        assert score >= 1.5

    def test_traceback_keyword(self):
        score = _score_reference_potential("Traceback (most recent call last):")
        assert score >= 1.5

    def test_failed_keyword(self):
        score = _score_reference_potential("Test suite failed with 3 failures")
        assert score >= 1.5

    def test_failure_keyword(self):
        score = _score_reference_potential("Build failure on CI pipeline")
        assert score >= 1.5

    def test_error_case_insensitive(self):
        score = _score_reference_potential("ERROR: connection refused")
        assert score >= 1.5
        score2 = _score_reference_potential("TRACEBACK in module")
        assert score2 >= 1.5

    # No match (should return 0.0)

    def test_no_match_generic(self):
        assert _score_reference_potential("This is a plain sentence") == 0.0

    def test_no_match_empty(self):
        assert _score_reference_potential("") == 0.0

    # Combined patterns

    def test_file_path_and_error(self):
        # File path (+1.0) + error (+1.5) = 2.5
        score = _score_reference_potential(
            "Error reading /etc/config.yml: permission denied"
        )
        assert score >= 2.5

    def test_url_and_error(self):
        # URL (+0.5) + error (+1.5) = 2.0
        score = _score_reference_potential(
            "Failed to fetch https://api.example.com/data"
        )
        assert score >= 2.0

    def test_file_path_and_url(self):
        # File path (+1.0) + URL (+0.5) = 1.5
        score = _score_reference_potential(
            "Saved from https://example.com to /tmp/output.json"
        )
        assert score >= 1.5

    def test_all_three_combined(self):
        # File path (+1.0) + URL (+0.5) + error (+1.5) = 3.0
        score = _score_reference_potential(
            "Error downloading https://api.example.com to /tmp/data.json"
        )
        assert score >= 3.0

    def test_no_double_counting_error(self):
        # Multiple error keywords should only match once (+1.5 not +3.0)
        score = _score_reference_potential(
            "Error: exception caused failure in the module"
        )
        # error + exception + failure are all error indicators, but break after first
        # Plus no file path or URL, so max from errors is 1.5
        assert score == 1.5


# --- Integration: score_sentence with semantic/reference scoring ---


class TestScoreSentenceIntegration:
    """Test how semantic and reference scoring integrates with score_sentence."""

    def test_decision_boosts_middle_sentence(self):
        """A decision in middle position should score higher than generic middle."""
        decision = score_sentence("We decided to use PostgreSQL", 3, 7)
        generic = score_sentence("The database is running fine", 3, 7)
        assert decision > generic

    def test_error_with_path_high_score(self):
        """Error + file path should produce high score even in middle position."""
        score = score_sentence(
            "Error in /home/user/app/server.py: connection timeout", 5, 10
        )
        generic = score_sentence("Things are going well today", 5, 10)
        assert score > generic + 2.0  # Should be significantly higher

    def test_conclusion_at_end_very_high(self):
        """Conclusion at end position should combine positional and semantic bonuses."""
        score = score_sentence("In conclusion, the refactor was successful", 9, 10)
        # Should get: last position (+1.5) + conclusion (+1.5) + action "successful" doesn't match
        assert score >= 3.0

    def test_commitment_with_todo(self):
        """Commitment with TODO keyword should score well."""
        score = score_sentence("TODO: implement the retry logic", 4, 10)
        # commitment (+1.5) + key term "TODO" (+0.5)
        assert score >= 2.0

    def test_generic_filler_low_score(self):
        """Generic filler in middle should score low."""
        score = score_sentence("Let me take a look at that for you", 5, 10)
        assert score < 1.0
