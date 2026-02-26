import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypeAlias

PathLike: TypeAlias = str | Path


@dataclass(frozen=True)
class SWEBenchInfo:
    """Stores metadata about a SWE-bench evaluation run."""

    instance_id: str
    model_name: str
    target: bool
    exit_status: str | None = None
    generated_patch: str | None = None
    eval_logs: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    repo_dir: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary format matching SWE-bench dataset."""
        return {
            "instance_id": self.instance_id,
            "model_name": self.model_name,
            "target": self.target,
            "exit_status": self.exit_status,
            "generated_patch": self.generated_patch,
            "eval_logs": self.eval_logs,
            "timestamp": self.timestamp.isoformat(),
            "artifacts": self.artifacts,
            "repo_dir": self.repo_dir,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SWEBenchInfo":
        """Create from dictionary, handling optional fields."""
        if "timestamp" in d:
            d = d.copy()
            d["timestamp"] = datetime.fromisoformat(d["timestamp"])
        return cls(**d)

    @classmethod
    def load_from_log_dir(cls, log_dir: PathLike) -> "SWEBenchInfo | None":
        log_dir = Path(log_dir)
        swe_bench_info_file = log_dir / "swe_bench_info.json"
        if not swe_bench_info_file.exists():
            return None
        with swe_bench_info_file.open() as f:
            return cls.from_dict(json.load(f))

    def save_to_log_dir(self, log_dir: PathLike) -> None:
        log_dir = Path(log_dir)
        swe_bench_info_file = log_dir / "swe_bench_info.json"
        swe_bench_info_file.parent.mkdir(parents=True, exist_ok=True)
        json.dump(self.to_dict(), swe_bench_info_file.open("w"), indent=2)
