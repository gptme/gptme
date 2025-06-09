#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "gradio-client",
# ]
# ///

import os
import sys
from pathlib import Path

from gradio_client import Client, handle_file

# TODO: support local inference

if len(sys.argv) < 2:
    print("Usage: ./chatterbox.py PROMPT VOICE_SAMPLE_PATH")
    sys.exit(1)

hf_token = os.getenv("HF_TOKEN")
if not hf_token:
    print("Please set the HF_TOKEN environment variable.")
    sys.exit(1)

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

prompt = sys.argv[1]
voice_sample_path = SCRIPT_DIR / sys.argv[2]

client = Client("ResembleAI/Chatterbox", hf_token=hf_token)
result = client.predict(
    text_input=prompt,
    audio_prompt_path_input=handle_file(voice_sample_path),
    exaggeration_input=0.5,
    temperature_input=0.8,
    seed_num_input=0,
    cfgw_input=0.5,
    api_name="/generate_tts_audio",
)
print(result)
