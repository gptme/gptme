#!/usr/bin/env python3
"""Helper functions for Python REPL skill."""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any


def inspect_df(df: Any) -> None:
    """Quick overview of a pandas DataFrame.

    Args:
        df: Pandas DataFrame to inspect
    """
    try:
        import pandas as pd

        if not isinstance(df, pd.DataFrame):
            print(f"Not a DataFrame: {type(df)}")
            return

        print(f"Shape: {df.shape}")
        print(f"\nColumns: {list(df.columns)}")
        print(f"\nData types:\n{df.dtypes}")
        print(f"\nMemory usage: {df.memory_usage(deep=True).sum() / 1024**2:.2f} MB")
        print(f"\nFirst 5 rows:\n{df.head()}")
        print(f"\nMissing values:\n{df.isnull().sum()}")
    except ImportError:
        print("pandas not installed")


def describe_object(obj: Any) -> None:
    """Detailed object introspection.

    Args:
        obj: Object to inspect
    """
    print(f"Type: {type(obj)}")
    print(f"Dir: {[x for x in dir(obj) if not x.startswith('_')]}")

    if hasattr(obj, "__dict__"):
        print(f"Attributes: {obj.__dict__}")

    if hasattr(obj, "__doc__") and obj.__doc__:
        print(f"Doc: {obj.__doc__[:200]}...")


def time_function(func: Callable) -> Callable:
    """Decorator to time function execution.

    Args:
        func: Function to time

    Returns:
        Wrapped function that prints execution time
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"{func.__name__} took {elapsed:.4f}s")
        return result

    return wrapper


def quick_plot(data: Any, kind: str = "line", **kwargs) -> None:
    """Quick matplotlib plot.

    Args:
        data: Data to plot (list, array, or DataFrame)
        kind: Plot type ('line', 'bar', 'scatter', 'hist')
        **kwargs: Additional plotting arguments
    """
    try:
        import matplotlib.pyplot as plt  # type: ignore

        if kind == "line":
            plt.plot(data, **kwargs)
        elif kind == "bar":
            plt.bar(range(len(data)), data, **kwargs)
        elif kind == "scatter":
            plt.scatter(range(len(data)), data, **kwargs)
        elif kind == "hist":
            plt.hist(data, **kwargs)
        else:
            print(f"Unknown plot type: {kind}")
            return

        plt.show()
    except ImportError:
        print("matplotlib not installed")


def setup_common_imports() -> None:
    """Load common libraries (numpy, pandas) into the global namespace.

    Call this function at the start of your REPL session to
    automatically import numpy as np and pandas as pd.
    """
    import sys

    try:
        import numpy as np
        import pandas as pd

        # Add to caller's global namespace
        frame = sys._getframe(1)
        frame.f_globals["np"] = np
        frame.f_globals["pd"] = pd

        print("Loaded numpy as np, pandas as pd")
    except ImportError as e:
        print(f"Failed to import: {e}")
