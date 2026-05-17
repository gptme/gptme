Finetuning
==========

This page documents the experimental fine-tuning support that still exists in
the repository. It is not a maintained end-to-end training guide.

## Current scope

Today `gptme` ships one helper for this workflow:
`scripts/train/collect.py`.

That script:

- reads local conversation logs from `~/.local/share/gptme/logs/`
- filters out generated-name/test conversations and low-quality chats
- strips leading system prompts
- renders each conversation with the selected Hugging Face chat template
- writes `train.csv` and `train.jsonl`

The files are written to your current working directory, not to a `train/`
subdirectory in the repo.

## Collect local conversations

From the repository root, run:

```bash
./scripts/train/collect.py --model "HuggingFaceH4/zephyr-7b-beta"
```

Pick a model whose chat template matches the format you want in the resulting
training data. The script uses `transformers.pipeline(...)` and
`tokenizer.apply_chat_template(...)` to turn stored chats into prompt text.
Run it in an environment where `torch` and `transformers` are already
installed.

## What is not maintained here

This page intentionally does **not** claim a supported workflow for:

- mixing in OpenAssistant or other public datasets
- importing exported ChatGPT conversations
- splitting train and validation sets
- training with a specific stack such as Axolotl, Transformers, or OpenPipe

Older revisions of this page sketched those steps, but they were incomplete and
had drifted out of sync with the repo. Until `gptme` grows a maintained
fine-tuning pipeline again, treat this document as a narrow note about the
collector script rather than a full recipe.

## Training stack pointers

If you want to take the exported data further, start with upstream docs for the
training stack you actually plan to use:

- [Axolotl][axolotl]
- [Hugging Face Transformers][hf-transformers]
- [Examples for Llama fine-tuning][llama-finetuning]
- [OpenPipe][openpipe]

## Model suggestions

- `HuggingFaceH4/zephyr-7b-beta`
- `teknium/Replit-v2-CodeInstruct-3B`
  - This was previously used for testing/debugging, but availability and local
    hardware support may vary.

[axolotl]: https://github.com/OpenAccess-AI-Collective/axolotl
[hf-transformers]: https://huggingface.co/docs/transformers/training
[llama-finetuning]: https://ai.meta.com/llama/get-started/#fine-tuning
[openpipe]: https://openpipe.ai/
