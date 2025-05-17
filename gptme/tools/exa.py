from math import e
from pickle import NONE
from tkinter import N

from gptme.server import api
from ..config import config
from ..utils import get_env_var
from exa_py import Exa


from .base import ToolSpec

exa_apikey = get_env_var("EXA_API_KEY")

def get_answer(query:str):
    exa = Exa(api_key=exa_apikey)
    try:
        response = exa.answer(query, text=True)
    except e:
        pass
    return response


tool: ToolSpec = ToolSpec(
    name="Exa answers",  
    desc="get LLM-summarized answers",
    functions=[get_answer],
    # block_types=[],
    available=bool(exa_apikey),
)
