"""Summary completion with a bounded fallback for unavailable models."""

import logging

from ..message import Message

logger = logging.getLogger(__name__)


def complete_summary(messages: list[Message], main_model: str) -> str:
    """Retry a missing summary model once with the caller's main model.

    Only a model-not-found response warrants fallback. Authentication, rate
    limits, and network failures retain their original error and retry policy.
    No process-global negative cache: availability can differ by credentials.
    """
    from openai import NotFoundError

    from ..llm import _chat_complete
    from ..llm.models.resolution import get_model, get_summary_model_for

    main_model = get_model(main_model).full
    summary_model = get_summary_model_for(main_model)
    try:
        response, _ = _chat_complete(messages, summary_model, None)
    except NotFoundError:
        if summary_model == main_model:
            logger.warning(
                "Summary model %s is unavailable. Set [models].summary to an "
                "accessible provider/model in your config.",
                summary_model,
            )
            raise
        logger.warning(
            "Summary model %s is unavailable (404); retrying once with %s. "
            "Set [models].summary to an accessible provider/model in your config.",
            summary_model,
            main_model,
        )
        response, _ = _chat_complete(messages, main_model, None)
    return response
