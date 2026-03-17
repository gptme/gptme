#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pandas",  # for easy data handling
# ]
# ///

import json
import sys

import pandas as pd


def process_data(data):
    # Convert to DataFrame for easier filtering
    df = pd.DataFrame(data)

    # Step 1: Filter - age >= 30 AND years_experience >= 5
    df = df[(df["age"] >= 30) & (df["years_experience"] >= 5)]

    # Step 2: Transform - add seniority field based on individual experience
    df["seniority"] = df["years_experience"].apply(
        lambda x: "senior" if x >= 8 else "mid"
    )

    return df.to_dict("records")


def main():
    if len(sys.argv) != 2:
        print("Usage: ./pipeline.py employees.json", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]

    # Read input JSON
    with open(input_file) as f:
        data = json.load(f)

    # Process data through pipeline
    result = process_data(data)

    # Write to stdout as JSON
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
