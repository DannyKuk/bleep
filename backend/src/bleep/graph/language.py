"""The language the person reads Bleep in (#374), in the prompts that write for them.

The prompts are written in English and the model answers in English unless told otherwise. For
any other language, a prompt that writes text the person reads gets that prompt's own rule
(`prompts/<area>/<name>_<language>.md`) in its `{language}` placeholder: which fields to write in
that language, and that names, keys, values, actions and targets stay exactly as they are. In
English the placeholder is empty, so those prompts render byte for byte as before.
"""

from bleep import prompts
from bleep.schemas.settings import DEFAULT_LANGUAGE, Language


def language_rule(prompt: str, language: Language) -> str:
    """The rule for `prompt` (`area/name`) in `language`, set apart from the text before it;
    empty in English."""
    if language == DEFAULT_LANGUAGE:
        return ""
    return "\n\n" + prompts.load(f"{prompt}_{language}").strip()
