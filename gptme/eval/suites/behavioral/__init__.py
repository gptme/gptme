"""Behavioral eval suite — multi-step workflow tasks.

Tests agent workflow behaviors (git ops, debugging loops, multi-file edits)
rather than single-function coding ability.  These are the scenarios where
lessons about *how to work* should have a measurable effect on outcomes.

Each scenario lives in its own module and exports a ``test`` dict (EvalSpec).
This package auto-discovers all scenario modules and collects them into the
``tests`` list that the eval runner expects.
"""

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _discover_tests() -> "list[EvalSpec]":
    """Auto-discover scenario modules; wrapped to avoid leaking loop vars."""
    package_dir = Path(__file__).parent
    result: list[EvalSpec] = []
    for info in sorted(pkgutil.iter_modules([str(package_dir)]), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f".{info.name}", __package__)
        t = getattr(mod, "test", None)
        if t is not None:
            result.append(t)
    return result


tests: list["EvalSpec"] = _discover_tests()

# Re-export all checker functions for backward compatibility.
# Tests and external code may import them directly from this package.
from .add_feature_preserve_default import (  # noqa: F401
    check_compat_has_default_param,
    check_compat_new_tests_exist,
    check_compat_original_tests_intact,
    check_compat_tests_pass,
)
from .add_logging import (  # noqa: F401
    check_logging_debug_or_info_used,
    check_logging_error_level_used,
    check_logging_module_imported,
    check_logging_no_print,
    check_logging_tests_pass,
)
from .add_type_hints import (  # noqa: F401
    check_typehints_class_attribute_annotated,
    check_typehints_function_params_annotated,
    check_typehints_function_returns_annotated,
    check_typehints_mypy_passes,
    check_typehints_uses_generic_collection,
)
from .debug_data_pipeline import (  # noqa: F401
    check_pipeline_extract_emails_fixed,
    check_pipeline_source_unchanged,
    check_pipeline_tests_pass,
)
from .extract_function_refactor import (  # noqa: F401
    check_extract_callers_import,
    check_extract_no_duplication,
    check_extract_shared_module_exists,
    check_extract_tests_pass,
)
from .fix_data_mutation import (  # noqa: F401
    check_apply_updates_returns_new_dict,
    check_mutation_tests_pass,
    check_tag_records_no_in_place_append,
    check_test_file_unchanged,
)
from .fix_security_path_traversal import (  # noqa: F401
    check_security_blocks_traversal,
    check_security_has_traversal_test,
    check_security_no_direct_join,
    check_security_tests_pass,
    check_security_uses_realpath,
)
from .git_selective_commit import (  # noqa: F401
    check_git_selective_commit_msg,
    check_git_selective_config_not_committed,
    check_git_selective_tests_pass,
)
from .handle_specific_exception import (  # noqa: F401
    check_config_catches_json_error,
    check_config_no_bare_except,
    check_config_propagates_file_error,
    check_config_tests_pass,
)
from .iterative_debug import (  # noqa: F401
    check_debug_fix_in_file,
    check_debug_no_syntax_error,
    check_debug_tests_pass,
)
from .merge_conflict_resolution import (  # noqa: F401
    check_merge_commit_completed,
    check_merge_no_conflict_markers,
    check_merge_null_safety,
    check_merge_tests_pass,
    check_merge_upper_function,
)
from .multi_file_rename import (  # noqa: F401
    check_rename_new_name_in_geometry,
    check_rename_no_old_name,
    check_rename_test_uses_new_name,
    check_rename_tests_pass,
)
from .noisy_worktree_fix import (  # noqa: F401
    check_noisy_worktree_api_not_committed,
    check_noisy_worktree_auth_committed,
    check_noisy_worktree_config_not_committed,
    check_noisy_worktree_fix_correct,
    check_noisy_worktree_tests_pass,
)
from .refactor_for_testability import (  # noqa: F401
    check_testability_generate_report_preserved,
    check_testability_has_pure_function,
    check_testability_no_file_io_in_unit_tests,
    check_testability_pure_function_tested,
    check_testability_tests_pass,
)
from .scope_discipline_bugfix import (  # noqa: F401
    check_scope_mean_fixed,
    check_scope_median_preserved,
    check_scope_mode_preserved,
    check_scope_no_new_functions,
    check_scope_tests_pass,
)
from .stage_new_files import (  # noqa: F401
    check_stage_file_has_double,
    check_stage_new_file_committed,
    check_stage_two_commits,
)
from .test_driven_error_handling import (  # noqa: F401
    check_error_handling_parse_csv,
    check_error_handling_safe_divide,
    check_error_handling_source_unchanged,
    check_error_handling_tests_pass,
    check_error_handling_to_int,
)
from .use_existing_helper import (  # noqa: F401
    check_reuse_imports_utils,
    check_reuse_no_inline_strip,
    check_reuse_tests_pass,
    check_reuse_uses_normalize,
    check_reuse_utils_unchanged,
)
from .write_test_suite import (  # noqa: F401
    check_write_tests_covers_extract_emails,
    check_write_tests_covers_truncate,
    check_write_tests_covers_word_count,
    check_write_tests_file_exists,
    check_write_tests_pass,
    check_write_tests_sufficient_count,
)
