"""
Chronos Test Suite Runner.
Executes all test suites and asserts strict invariant verification.
"""

import sys
from pathlib import Path

# Add package root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_dag import test_dag_successful_linear_execution
from tests.test_rollback import (
    test_automatic_rollback_on_failure,
    test_manual_rollback_to_arbitrary_snapshot,
    test_git_tree_content_addressable_rollback,
)
from tests.test_compactor import test_context_compaction_reduces_tokens


def run_all_tests():
    tests = [
        ("test_dag_successful_linear_execution", test_dag_successful_linear_execution),
        ("test_automatic_rollback_on_failure", test_automatic_rollback_on_failure),
        ("test_manual_rollback_to_arbitrary_snapshot", test_manual_rollback_to_arbitrary_snapshot),
        ("test_git_tree_content_addressable_rollback", test_git_tree_content_addressable_rollback),
        ("test_context_compaction_reduces_tokens", test_context_compaction_reduces_tokens),
    ]

    passed = 0
    failed = 0

    print("Running Chronos Test Suite...")
    print("=" * 60)

    for name, test_fn in tests:
        try:
            test_fn()
            print(f"  ✓ {name:<50} PASSED")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name:<50} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed.")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
