import subprocess
import sys


def run_diagnostics():
    print("PostgreSQL Installation Check:")
    result = subprocess.run(["which", "psql"], capture_output=True, text=True)
    print(f"PSQL Location: {result.stdout}")

    print("\nPostgreSQL Version:")
    subprocess.run(["psql", "--version"])

    print("\nDatabase Service Status:")
    subprocess.run(["ps", "aux", "|", "grep", "postgres"])


if __name__ == "__main__":
    run_diagnostics()
