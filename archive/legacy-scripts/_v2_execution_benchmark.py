"""PowerShell-friendly entry point for the isolated v2 execution benchmark."""
from pathlib import Path

from experiments.v2_execution_benchmark import write_outputs


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    output = root / "experiments" / "results" / "v2_execution_benchmark"
    data_path, report_path = write_outputs(output)
    print(f"DATA={data_path}")
    print(f"REPORT={report_path}")
