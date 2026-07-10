"""Gear-based autonomy presets for gptme."""

from dataclasses import dataclass

GEAR_NAMES: dict[int, str] = {
    0: "Observe",
    1: "Review",
    2: "Plan",
    3: "Execute",
    4: "Integrate",
}

GEAR_DESCRIPTIONS: dict[int, str] = {
    0: "Read-only analysis. Safe for auditing untrusted codebases.",
    1: "Interactive. User confirms each tool call. Current default.",
    2: "Supervised. File edits are allowed; shell/network tools are excluded by default.",
    3: "Autonomous. All tools, no confirmation.",
    4: "Orchestrator. Full tools plus subagent delegation, no confirmation.",
}

GEAR_PROFILE: dict[int, str | None] = {
    0: "explorer",
    1: None,
    2: None,
    3: "developer",
    4: "developer",
}

GEAR_TOOLS: dict[int, tuple[str, ...] | None] = {
    0: ("read", "chats"),
    1: None,
    2: ("read", "patch", "save", "append", "ipython"),
    3: None,
    4: None,
}

GEAR_NO_CONFIRM: dict[int, bool] = {
    0: True,
    1: False,
    2: False,
    3: True,
    4: True,
}

GEAR_EXTRA_TOOLS: dict[int, tuple[str, ...]] = {
    4: ("subagent",),
}


@dataclass(frozen=True)
class GearResolution:
    """Resolved settings for a gear preset."""

    gear: int
    name: str
    profile_name: str | None
    tool_allowlist: tuple[str, ...] | None
    no_confirm: bool
    description: str


def resolve_gear(gear: int) -> GearResolution:
    """Resolve a gear number into profile, tool, and confirmation defaults."""
    if gear not in GEAR_NAMES:
        raise ValueError(f"Invalid gear: {gear}. Must be 0-4.")

    tools = GEAR_TOOLS[gear]
    extra_tools = GEAR_EXTRA_TOOLS.get(gear, ())
    if tools is None:
        tool_allowlist = tuple(f"+{tool}" for tool in extra_tools) or None
    else:
        tool_allowlist = (*tools, *extra_tools)

    return GearResolution(
        gear=gear,
        name=GEAR_NAMES[gear],
        profile_name=GEAR_PROFILE[gear],
        tool_allowlist=tool_allowlist,
        no_confirm=GEAR_NO_CONFIRM[gear],
        description=GEAR_DESCRIPTIONS[gear],
    )


def parse_gear(value: int | str | None) -> int | None:
    """Parse a gear value from CLI/config input."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("gear must be an integer from 0 to 4")
    try:
        gear = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("gear must be an integer from 0 to 4") from exc
    if gear not in GEAR_NAMES:
        raise ValueError(f"Invalid gear: {gear}. Must be 0-4.")
    return gear
