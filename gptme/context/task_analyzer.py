"""Task complexity analyzer for adaptive context compression.

This module analyzes task characteristics to determine optimal compression ratios
for context selection. It classifies tasks into types (diagnostic, fix, implementation,
exploration, refactor) and adjusts compression based on task complexity.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskFeatures:
    """Features extracted from a task for classification.

    Represents observable characteristics of a task that inform compression decisions.
    """

    # File impact analysis
    files_to_modify: int = 0  # Count of files needing changes
    new_files_count: int = 0  # Count of files to create
    total_file_size: int = 0  # Sum of file sizes in bytes
    file_types: set[str] = field(default_factory=set)  # Extensions (.py, .md, etc.)
    directory_spread: int = 0  # Number of distinct directories

    # Dependency analysis
    import_depth: int = 0  # Max import chain depth
    external_deps: int = 0  # Count of external library dependencies
    internal_coupling: float = 0.0  # Ratio of internal imports (0.0-1.0)
    circular_deps: bool = False  # Presence of circular dependencies

    # Workspace analysis
    has_reference_impl: bool = False  # Similar implementation exists in workspace
    reference_files: list[Path] = field(
        default_factory=list
    )  # Reference implementation paths
    pattern_matches: int = 0  # Count of similar patterns in workspace
    total_workspace_size: int = 0  # Size of relevant workspace files in bytes

    # Prompt analysis
    prompt_signals: dict[str, float] = field(default_factory=dict)  # Keyword scores

    def __post_init__(self) -> None:
        """Initialize prompt_signals with default structure if empty."""
        if not self.prompt_signals:
            self.prompt_signals = {
                "diagnostic_score": 0.0,
                "implementation_score": 0.0,
                "fix_score": 0.0,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            }


@dataclass
class TaskClassification:
    """Classification result for a task.

    Represents the determined task type, confidence level, and supporting metadata.
    """

    primary_type: (
        str  # Main task type: diagnostic, fix, implementation, exploration, refactor
    )
    confidence: float  # Confidence score (0.0-1.0)
    secondary_types: list[str] = field(default_factory=list)  # Other applicable types
    all_scores: dict[str, float] = field(default_factory=dict)  # Scores for all types
    rationale: str = ""  # Human-readable explanation of classification

    def __post_init__(self) -> None:
        """Validate classification fields."""
        valid_types = {"diagnostic", "fix", "implementation", "exploration", "refactor"}
        if self.primary_type not in valid_types:
            raise ValueError(
                f"Invalid primary_type: {self.primary_type}. Must be one of {valid_types}"
            )

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Confidence must be between 0.0 and 1.0, got {self.confidence}"
            )

        for sec_type in self.secondary_types:
            if sec_type not in valid_types:
                raise ValueError(
                    f"Invalid secondary_type: {sec_type}. Must be one of {valid_types}"
                )


def extract_features(
    prompt: str,
    workspace_files: list[Path] | None = None,
    current_context: list[str] | None = None,
) -> TaskFeatures:
    """Extract features from task inputs for classification.

    Analyzes the prompt, workspace files, and current context to extract
    observable characteristics that inform task type classification and
    compression ratio selection.

    Args:
        prompt: The task prompt/query from the user
        workspace_files: List of files in the workspace (optional)
        current_context: Current context items (optional)

    Returns:
        TaskFeatures object containing extracted features
    """
    features = TaskFeatures()

    # Extract prompt signals
    features.prompt_signals = _extract_prompt_signals(prompt)

    # Extract file impact metrics (if workspace files provided)
    if workspace_files:
        _extract_file_impact(features, workspace_files)

    # Extract workspace context (if current context provided)
    if current_context:
        _extract_workspace_context(features, current_context)

    return features


def _extract_prompt_signals(prompt: str) -> dict[str, float]:
    """Extract keyword-based signals from prompt text.

    Analyzes the prompt for indicators of different task types and returns
    normalized scores for each type.

    Args:
        prompt: The task prompt text

    Returns:
        Dictionary mapping signal names to scores (0.0-1.0)
    """
    prompt_lower = prompt.lower()

    # Keyword definitions for each task type
    diagnostic_keywords = [
        "debug",
        "investigate",
        "why",
        "error",
        "failing",
        "problem",
        "issue",
    ]
    implementation_keywords = ["implement", "create", "build", "add", "feature", "new"]
    fix_keywords = ["fix", "resolve", "correct", "repair", "patch", "bug"]
    exploration_keywords = [
        "explore",
        "research",
        "investigate",
        "options",
        "compare",
        "alternatives",
    ]
    refactor_keywords = [
        "refactor",
        "restructure",
        "reorganize",
        "improve",
        "cleanup",
        "simplify",
    ]

    # Count keyword matches
    diagnostic_count = sum(1 for kw in diagnostic_keywords if kw in prompt_lower)
    implementation_count = sum(
        1 for kw in implementation_keywords if kw in prompt_lower
    )
    fix_count = sum(1 for kw in fix_keywords if kw in prompt_lower)
    exploration_count = sum(1 for kw in exploration_keywords if kw in prompt_lower)
    refactor_count = sum(1 for kw in refactor_keywords if kw in prompt_lower)

    # Normalize scores (cap at 1.0)
    return {
        "diagnostic_score": min(1.0, diagnostic_count * 0.3),
        "implementation_score": min(1.0, implementation_count * 0.3),
        "fix_score": min(1.0, fix_count * 0.3),
        "exploration_score": min(1.0, exploration_count * 0.3),
        "refactor_score": min(1.0, refactor_count * 0.3),
    }


def _extract_file_impact(features: TaskFeatures, workspace_files: list[Path]) -> None:
    """Extract file impact metrics from workspace files.

    Analyzes the workspace files to determine the scope and spread of the task.
    Updates the features object in-place.

    Args:
        features: TaskFeatures object to update
        workspace_files: List of paths to workspace files
    """
    if not workspace_files:
        return

    # Count files and track directories
    features.files_to_modify = len(workspace_files)
    directories = set()

    # Analyze each file
    for file_path in workspace_files:
        # Track file type
        if file_path.suffix:
            features.file_types.add(file_path.suffix)

        # Track directory
        directories.add(file_path.parent)

        # Sum file size (if file exists)
        if file_path.exists():
            features.total_file_size += file_path.stat().st_size

    features.directory_spread = len(directories)


def _extract_workspace_context(
    features: TaskFeatures, current_context: list[str]
) -> None:
    """Extract workspace context metrics.

    Analyzes the current context to identify patterns, references, and workspace size.
    Updates the features object in-place.

    Args:
        features: TaskFeatures object to update
        current_context: List of current context items
    """
    if not current_context:
        return

    # Calculate total workspace size
    features.total_workspace_size = sum(len(item) for item in current_context)

    # Look for reference implementation indicators
    # (This is a simplified implementation - can be enhanced with semantic analysis)
    reference_indicators = ["class ", "def ", "function ", "implementation"]

    reference_count = 0
    for item in current_context:
        if any(indicator in item for indicator in reference_indicators):
            reference_count += 1

    # If we find multiple implementation patterns, likely has references
    if reference_count >= 3:
        features.has_reference_impl = True
        features.pattern_matches = reference_count


def classify_task(features: TaskFeatures) -> TaskClassification:
    """Classify task based on extracted features.

    Uses rule-based classification to determine the primary task type,
    confidence level, and any secondary types.

    Args:
        features: Extracted task features

    Returns:
        TaskClassification with type, confidence, and supporting metadata
    """
    scores: dict[str, float] = {
        "diagnostic": 0.0,
        "fix": 0.0,
        "implementation": 0.0,
        "exploration": 0.0,
        "refactor": 0.0,
    }

    # Rule 1: File impact classification
    if features.files_to_modify <= 2 and features.new_files_count == 0:
        scores["fix"] += 0.3
        scores["diagnostic"] += 0.2

    if features.new_files_count >= 3:
        scores["implementation"] += 0.4

    if features.files_to_modify >= 3 and features.new_files_count == 0:
        scores["refactor"] += 0.3

    # Rule 2: Prompt signals
    scores["diagnostic"] += features.prompt_signals.get("diagnostic_score", 0.0) * 0.4
    scores["implementation"] += (
        features.prompt_signals.get("implementation_score", 0.0) * 0.4
    )
    scores["fix"] += features.prompt_signals.get("fix_score", 0.0) * 0.3
    scores["exploration"] += features.prompt_signals.get("exploration_score", 0.0) * 0.3
    scores["refactor"] += features.prompt_signals.get("refactor_score", 0.0) * 0.3

    # Rule 3: Workspace context
    if features.has_reference_impl and features.new_files_count >= 2:
        scores["implementation"] += 0.2

    if features.pattern_matches >= 3:
        scores["fix"] += 0.1

    # Rule 4: Dependencies (placeholder - enhance when dependency analysis added)
    if features.import_depth >= 5:
        scores["implementation"] += 0.2
        scores["refactor"] += 0.1

    # Normalize scores
    max_score = max(scores.values()) if scores.values() else 0.0
    if max_score > 0:
        scores = {k: v / max_score for k, v in scores.items()}

    # Select primary type (highest score)
    primary_type = max(scores, key=scores.get)  # type: ignore
    confidence = scores[primary_type]

    # Secondary types (score > 0.5)
    secondary_types = [k for k, v in scores.items() if k != primary_type and v > 0.5]

    # Generate rationale
    rationale = _generate_classification_rationale(features, primary_type, confidence)

    return TaskClassification(
        primary_type=primary_type,
        confidence=confidence,
        secondary_types=secondary_types,
        all_scores=scores,
        rationale=rationale,
    )


def _generate_classification_rationale(
    features: TaskFeatures, task_type: str, confidence: float
) -> str:
    """Generate human-readable rationale for classification.

    Args:
        features: Extracted task features
        task_type: Classified task type
        confidence: Classification confidence

    Returns:
        Rationale string explaining the classification
    """
    parts = []

    # Classification result
    parts.append(f"Task classified as {task_type} (confidence: {confidence:.2f})")

    # Key supporting features
    if features.files_to_modify <= 2:
        parts.append("Few files to modify suggests focused task")

    if features.new_files_count >= 3:
        parts.append("Multiple new files indicates implementation work")

    if features.has_reference_impl:
        parts.append("Reference implementation available in workspace")

    if features.prompt_signals.get(f"{task_type}_score", 0.0) > 0.5:
        parts.append(f"Prompt contains strong {task_type} indicators")

    return ". ".join(parts) + "." if parts else "Classification based on default rules."


def select_compression_ratio(
    classification: TaskClassification, features: TaskFeatures
) -> float:
    """Select appropriate compression ratio based on classification and features.

    Determines the optimal compression ratio (0.10-0.50) based on the task type,
    classification confidence, and specific feature characteristics.

    Args:
        classification: Task classification result
        features: Extracted task features

    Returns:
        Compression ratio as float between 0.10 and 0.50
    """
    # Compression ratio ranges by task type
    COMPRESSION_RATIOS = {
        "diagnostic": {"min": 0.10, "default": 0.12, "max": 0.15},
        "fix": {"min": 0.15, "default": 0.17, "max": 0.20},
        "implementation": {"min": 0.30, "default": 0.35, "max": 0.50},
        "exploration": {"min": 0.20, "default": 0.25, "max": 0.30},
        "refactor": {"min": 0.25, "default": 0.30, "max": 0.35},
    }

    task_type = classification.primary_type
    ratios = COMPRESSION_RATIOS[task_type]

    # Start with default
    ratio = ratios["default"]

    # Adjust for reference implementations
    if features.has_reference_impl and task_type == "implementation":
        # Use higher ratio (less compression) to preserve references
        ratio = ratios["max"]

    # Adjust for confidence
    if classification.confidence >= 0.9:
        # High confidence → use default
        pass
    elif classification.confidence < 0.6:
        # Low confidence → use moderate compression (safer)
        ratio = 0.25

    # Adjust for workspace size
    if features.total_workspace_size > 1_000_000:  # 1MB
        # Large workspace → compress more to fit in context
        ratio = max(ratios["min"], ratio - 0.05)

    # Safety bounds
    ratio = max(0.10, min(0.50, ratio))

    return ratio


def generate_rationale(
    classification: TaskClassification, features: TaskFeatures, ratio: float
) -> str:
    """Generate human-readable rationale for compression decision.

    Creates a comprehensive explanation of why a specific compression ratio
    was selected based on the classification and features.

    Args:
        classification: Task classification result
        features: Extracted task features
        ratio: Selected compression ratio

    Returns:
        Rationale string explaining the compression decision
    """
    parts = []

    # Classification reasoning
    parts.append(
        f"Task classified as {classification.primary_type} "
        f"(confidence: {classification.confidence:.2f})"
    )

    # Key features
    if features.files_to_modify <= 2:
        parts.append("Few files to modify suggests focused task")
    if features.new_files_count >= 3:
        parts.append("Multiple new files indicates implementation work")
    if features.has_reference_impl:
        parts.append("Reference implementation available in workspace")

    # Ratio selection
    reduction_pct = int((1 - ratio) * 100)
    parts.append(f"Selected compression ratio {ratio:.2f} ({reduction_pct}% reduction)")

    # Expected outcome
    if ratio <= 0.20:
        parts.append("Aggressive compression to focus context")
    elif ratio >= 0.35:
        parts.append("Conservative compression to preserve architecture")
    else:
        parts.append("Moderate compression balancing focus and context")

    return ". ".join(parts) + "."
