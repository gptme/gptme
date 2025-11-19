#!/usr/bin/env python3
"""
Collect conversation logs from gptme for fine-tuning.

Output: convs.csv with "text" column
"""

import csv
import json
import logging
from pathlib import Path

import click
import torch  # type: ignore
from transformers import pipeline  # type: ignore

from gptme.util.generate_name import is_generated_name

logger = logging.getLogger(__name__)


def list_conversations() -> list[Path]:
    """List all conversations from gptme in ~/.local/share/gptme/logs/"""

    logs_dir = Path.home() / ".local/share/gptme/logs/"
    logs = list(logs_dir.glob("*/*.jsonl"))

    # filter out logs with default-generated names
    logs = [
        log
        for log in logs
        if not is_generated_name(log.parent.name[11:])
        and "-test-" not in log.parent.name
    ]

    return logs


def load_conversations() -> tuple[list[str], list[list[dict]]]:
    logs = list_conversations()
    names = []
    logs_new = []
    for log in logs:
        msgs = []
        with log.open() as f:
            for line in f:
                msgs.append(json.loads(line))
        names.append(log.parent.name)
        logs_new.append(msgs)
    return names, logs_new


# UNUSED
def generate_training_conversations(msgs) -> str:
    """Generate training conversations from gptme logs"""
    # get all messages until the first "assistant" role
    instruction_msgs = []
    response_msgs = []
    for msg in msgs:
        if msg["role"] == "assistant":
            response_msgs.append(msg)
            break
        instruction_msgs.append(msg)

    instruction = "\n".join([msg["content"] for msg in instruction_msgs])
    response = "\n".join([msg["content"] for msg in response_msgs])
    msg_str = f"""## Instruction: {instruction}

## Response: {response}"""

    return msg_str


confusion_indicators = [
    "confusion",
    "confused",
    "I apologize",
]


def quality_check_convo(msgs: list[dict], name: str) -> bool:
    """Check if a conversation is high-quality"""
    # check for duplicate messages (where msg["content"] is the same)
    if len(msgs) != len({msg["content"] for msg in msgs}):
        logger.info(f"Found duplicate messages, filtering '{name}'")
        return False
    # check for messages including "confusion", "repetition", etc.
    for msg in msgs:
        for indicator in confusion_indicators:
            if indicator in msg["content"]:
                logger.info(
                    f"Found confusion indicator '{indicator}', filtering '{name}'"
                )
                return False
    return True


def _filter_leading_system(msgs: list[dict]) -> list[dict]:
    """Filter out leading system messages (system prompt)"""
    msgs_new = []
    user_msg_seen = False
    for msg in msgs:
        if msg["role"] == "user":
            user_msg_seen = True
        elif not user_msg_seen:
            continue
        msgs_new.append(msg)
    return msgs_new


def collect(model: str):
    logger.info("Loading conversations...")
    names, convs = load_conversations()
    logger.info("Got %d conversations", len(convs))
    names, convs = zip(
        *[
            (name, convs)
            for name, convs in zip(names, convs)
            if quality_check_convo(convs, name)
        ],
    )
    logger.info("Got %d conversations after filtering", len(convs))

    logger.info("Loading pipeline...")
    pipe = pipeline(
        "text-generation",
        model=model,
        torch_dtype=torch.bfloat16,  # was bfloat16, but erring on MPS
        trust_remote_code=True,
        # device_map="auto",
        # offload="offload",
        # offload_folder="offload",
    )

    convs_strs = []
    convs_dicts = []
    for name, msgs in zip(names, convs):
        logger.info("Generating prompt for '%s'", name)
        # skip first system messages (due to verbosity)
        # TODO: replace with system message in latest version of gptme?
        #       wouldn't quite work, since responses depend on context such as which tools/packages are installed etc.
        msgs = _filter_leading_system(msgs)
        assert msgs[0]["role"] == "user"
        if pipe.tokenizer is None:
            raise ValueError("Pipeline tokenizer is None")
        prompt = pipe.tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True
        )
        if not isinstance(prompt, str):
            raise TypeError(f"Expected string prompt, got {type(prompt)}")
        convs_strs.append(prompt.replace("\n", "\\n"))
        convs_dicts.append({"text": prompt})

    # write to csv with "text" column
    # needs escaping for newlines etc.
    with open("train.csv", "w") as f:
        writer = csv.writer(f)
        writer.writerow(["text"])
        for conv in convs_strs:
            writer.writerow([conv])
    print("Wrote train.csv")

    # write to jsonl
    with open("train.jsonl", "w") as f:
        for conv_dict in convs_dicts:
            f.write(json.dumps(conv_dict) + "\n")
    print("Wrote train.jsonl")

    # outputs = pipe(
    #     prompt,
    #     max_new_tokens=256,
    #     do_sample=True,
    #     temperature=0.7,
    #     top_k=50,
    #     top_p=0.95,
    # )
    # print(outputs[0]["generated_text"])


@click.command()
@click.option(
    "--model",
    required=True,
    type=str,
    help='Model name, e.g. "HuggingFaceH4/zephyr-7b-beta"',
)
def main(model: str):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    collect(model)


if __name__ == "__main__":
    main()
