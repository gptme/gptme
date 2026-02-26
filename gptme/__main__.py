import sys

try:
    from gptme.cli.main import main
except KeyboardInterrupt:
    print("\nInterrupted during startup.", file=sys.stderr)
    sys.exit(130)  # Standard exit code for SIGINT

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
