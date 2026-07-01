import pickle

from gptme.eval.suites.computer import tests as computer_evals


def test_computer_eval_specs_are_picklable():
    """run_evals() submits each EvalSpec through a ProcessPoolExecutor, which
    pickles the submitted args even at --parallel 1. A lambda in
    `expect`/`check_log` crashes every run with a PicklingError before the
    model is ever invoked. Guard against reintroducing inline lambdas here."""
    for spec in computer_evals:
        for checks in (spec.get("expect", {}), spec.get("check_log", {})):
            for check in checks.values():
                pickle.dumps(check)
