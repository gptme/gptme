import random

# Name generation lists
actions = [
    "running",
    "jumping",
    "walking",
    "skipping",
    "hopping",
    "flying",
    "swimming",
    "crawling",
    "sneaking",
    "sprinting",
    "climbing",
    "dancing",
    "singing",
    "laughing",
]
adjectives = [
    "funny",
    "happy",
    "sad",
    "angry",
    "silly",
    "crazy",
    "sneaky",
    "sleepy",
    "hungry",
    # colors
    "red",
    "blue",
    "green",
    "pink",
    "purple",
    "yellow",
    "orange",
]
nouns = [
    "cat",
    "dog",
    "rat",
    "mouse",
    "fish",
    "elephant",
    "dinosaur",
    # birds
    "bird",
    "pelican",
    # fictional
    "dragon",
    "unicorn",
    "mermaid",
    "monster",
    "alien",
    "robot",
    # sea creatures
    "whale",
    "shark",
    "walrus",
    "octopus",
    "squid",
    "jellyfish",
    "starfish",
    "penguin",
    "seal",
]


def generate_name():
    action = random.choice(actions)
    adjective = random.choice(adjectives)
    noun = random.choice(nouns)
    return f"{action}-{adjective}-{noun}"


def is_generated_name(name: str) -> bool:
    """Check whether a name has the action-adjective-noun form of generate_name."""
    parts = name.split("-")
    return (
        len(parts) == 3
        and parts[0] in actions
        and parts[1] in adjectives
        and parts[2] in nouns
    )
