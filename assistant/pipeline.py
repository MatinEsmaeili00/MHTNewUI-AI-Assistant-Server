"""
text + context + catalog -> the answer of PROTOCOL.md. Used by /v1/chat and /v1/voice.

    1. remember the catalog if one was sent, look it up by catalog_id otherwise
    2. rules (rules.py)
    3. if the rules do not understand: LLM (llm.py)
    4. every action through actions.sanitize
    5. remember the turn for the session
"""

from __future__ import annotations

import re

from . import llm, log, rules
from .actions import sanitize
from .catalog import Catalog, NoMatch, mentions_anatomy, store
from .config import settings
from .context import Context
from .sessions import sessions

EXAMPLES = (
    "Try: show me the left biceps, switch to the muscle layer, zoom in, play it faster, "
    "start the pose estimation."
)
NOTHING_SAID = "I did not get any text. " + EXAMPLES
LLM_DOWN = "The language model is not answering right now, but the direct commands still work. " + EXAMPLES
NO_CATALOG = (
    "I do not have this avatar's list of muscles and bones yet, so I cannot show a single part. "
    "The other commands still work."
)
NOT_UNDERSTOOD = "I am not sure how to do that. " + EXAMPLES


def clean_text(text: object) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[: settings.max_text_chars]


def _answer(transcript: str, reply: str, actions: list[dict], source: str, need_catalog: bool = False) -> dict:
    return {
        "transcript": transcript,
        "reply": reply,
        "actions": actions,
        "source": source,
        "need_catalog": need_catalog,
    }


def _why_dropped(dropped: list, ctx: Context, catalog: Catalog | None) -> str:
    """One sentence for the user about the first LLM action that had to be thrown away."""
    for raw, _reason in dropped:
        if not isinstance(raw, dict):
            continue
        if raw.get("type") == "show_part" and isinstance(raw.get("name"), str):
            if catalog is None:
                return NO_CATALOG
            found = catalog.find(raw["name"])
            if isinstance(found, NoMatch):
                return rules.no_part_reply(found)
        if raw.get("type") == "play_action" and isinstance(raw.get("name"), str):
            return rules.no_such_action(raw["name"][:40], ctx).reply
    return ""


def handle(
    text: object,
    context: object = None,
    catalog_id: object = "",
    catalog_raw: object = None,
    session_id: object = "",
) -> dict:
    """Never raises for bad input: missing or odd fields are treated as "not there"."""
    text = clean_text(text)
    ctx = Context(context)

    catalog = store.put(catalog_id, catalog_raw) if catalog_raw is not None else None
    if catalog is None:
        catalog = store.get(catalog_id)

    if not text:
        return _answer("", NOTHING_SAID, [], "rules")

    result = rules.parse(text, ctx, catalog)
    wants_catalog = bool(result and result.need_catalog) or (
        result is None and catalog is None and mentions_anatomy(text)
    )
    if wants_catalog and store.may_ask(catalog_id):
        return _answer(text, "", [], "rules", need_catalog=True)
    if result and result.need_catalog:
        return _answer(text, NO_CATALOG, [], "rules")        # asked once already, do not ask twice in a row

    if result is not None:
        actions, dropped = sanitize(result.actions, ctx, catalog)
        for raw, reason in dropped:
            log.warning("rules produced an invalid action %r: %s", raw, reason)
        reply, source = result.reply, "rules"
    else:
        try:
            raw_answer = llm.ask(text, ctx, catalog, sessions.history(session_id))
        except llm.LLMError as error:
            log.warning("LLM error: %s", error)
            return _answer(text, LLM_DOWN, [], "error")
        except Exception:                      # a bug in the LLM path must not take the request down
            log.exception("LLM path failed")
            return _answer(text, LLM_DOWN, [], "error")
        actions, dropped = sanitize(raw_answer["actions"], ctx, catalog)
        reply, source = raw_answer["reply"], "llm"
        if dropped:
            for raw, reason in dropped:
                log.info("LLM action dropped %r: %s", raw, reason)
            # The model wrote its reply as if these would work; say what is really the case.
            why = _why_dropped(dropped, ctx, catalog)
            reply = (why or NOT_UNDERSTOOD) if not actions else f"{reply} {why}".strip()
        if not reply:
            reply = "Done." if actions else NOT_UNDERSTOOD

    sessions.add(session_id, text, reply, actions)
    return _answer(text, reply, actions, source)
