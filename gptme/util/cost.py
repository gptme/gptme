from ..message import Message, len_tokens
from . import console


def _tokens_inout(msgs: list[Message]) -> tuple[int, int]:
    from ..llm.models import get_default_model  # fmt: skip

    model = get_default_model()
    assert model is not None, "No model loaded"
    tokens_in, tokens_out = len_tokens(msgs[:-1], model.model), 0
    if msgs[-1].role == "assistant":
        tokens_out = len_tokens(msgs[-1], model.model)
    else:
        tokens_in += len_tokens(msgs[-1], model.model)
    return tokens_in, tokens_out


def _cost(msgs: list[Message]) -> float:
    return sum(msg.cost() for msg in msgs[:-1]) + msgs[-1].cost(
        output=msgs[-1].role == "assistant"
    )


def log_costs(msgs: list[Message]) -> None:
    """
    Infer session costs from conversation.
    NOTE: doesn't account for context enhancement, assumes conversation is append-only.
    """
    # split msgs when assistant role occurs (request/turn)
    requests: list[list[Message]] = []
    for i, msg in enumerate(msgs):
        if msg.role == "assistant":
            requests.append(msgs[: i + 1])
    if not requests or requests[-1] != msgs:
        requests.append(msgs)

    # calculate tokens and cost of each request
    costs = []
    tokens = []
    for req in requests:
        costs.append(_cost(req))
        tokens.append(_tokens_inout(req))

    # turns for the assistant
    turns = len([msg for msg in msgs if msg.role == "assistant"])

    # print tokens and cost
    tok_in, tok_out = tokens[-1]
    tokens_msg = f"Tokens: {tok_in}/{tok_out} in/out"
    tokens_lead_len = len(tokens_msg)
    if turns > 1:
        tok_in_total = sum(t[0] for t in tokens)
        tok_out_total = sum(t[1] for t in tokens)
        tokens_msg += f" (session: {tok_in_total}/{tok_out_total}, turns: {turns})"
    console.log(tokens_msg)

    # costs will be 0 for models lacking price metadata
    if sum(costs) > 0:
        cost_msg = f"Cost:   ${costs[-1]:.2f}"
        cost_msg += " " * (tokens_lead_len - len(cost_msg))
        if turns > 1:
            cost_msg += f" (session: ${sum(costs):.2f})"
        console.log(cost_msg)
