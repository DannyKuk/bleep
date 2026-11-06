"""Signs that host text tries to give Bleep orders, found by code alone (#271).

Each line is folded as the tag check folds it (NFKC, no invisible characters, look-alike letters
mapped to Latin, lower case) and read for five signs:

- `addressed`: it speaks to an AI reader ("Note for AI agents", "Assistant,", "Agent:",
  "Hey GPT", "[bleep]", "Bleep must", "in your diagnosis", "in your report"), or opens like a
  message to one ("SYSTEM:", "[system]", "new instructions", "operator override");
- `authority`: it overrides the agent's own rules ("system prompt update", "ignore the previous
  instructions", also in German, French, Spanish and Italian), carries an `"approved_by"` key or
  relays orders from the human;
- `claim`: it says the human or an operator approved something or gave instructions;
- `decode`: it asks for something to be decoded and followed;
- `verb`: it holds a verb of action (run, stop, restart, write, tell, approve, ...).

A line gives orders when it has `authority` or `decode`, a `claim` in host text, or is
`addressed` and has a `verb`. A header, an address or a verb alone is everyday text. Bleep's own
records (past incidents) say who approved what and what the human instructed, so a `claim` is not
read there (`own_record`). The signs only tell the human; they decide nothing. Every pattern is
linear: gaps are bounded and nothing nests.
"""

import re

from bleep.guard.untrusted import fold

_GAP = r"\s{1,4}"
_START = r"(?:^\s{0,8}|[.:;!?>]\s{1,4}|[\"'(\[{]\s{0,4})"  # a clause or a quote starts here
_LEVEL = r"\b(?:debug|info|notice|warn(?:ing)?|error|crit(?:ical)?|fatal)\s{1,4}"  # a log level
_AI = r"(?:ai|llm|assistant|agent|bot|model|bleep|claude|gpt)"
_EARLIER = r"(?:previous|prior|earlier)"

_ADDRESSED = re.compile(
    rf"\b(?:ai|llm)[\s-]{{1,4}}(?:agent|assistant|model|bot)s?\b"
    rf"|\blanguage{_GAP}models?\b"
    rf"|\b(?:to|for){_GAP}(?:(?:the|any){_GAP})?(?:bleep{_GAP})?(?:assistant|agent|bot|llm|model)s?"
    rf"(?:{_GAP}(?:reading|running)\b|\s{{0,4}}:)"
    rf"|{_START}(?:assistant|agent|bleep)s?\s{{0,4}}[,:]"
    rf"|(?:^\s{{0,8}}|[.:;!?>\"'(\[{{]\s{{0,4}}|{_LEVEL}|[#@]\s{{0,4}}|<!--\s{{0,4}})"
    r"(?:ai|llm|model|claude|gpt|bleep)\s{0,4}[,:!]"
    rf"|\b(?:dear|hey|hello|hi|attention|note{_GAP}(?:to|for)|message{_GAP}for)"
    rf"{_GAP}(?:(?:the|any){_GAP})?{_AI}\b"
    rf"|\b(?:assistant|agent|bot|llm|model|ai)s?{_GAP}(?:that|who){_GAP}"
    r"(?:reads?|sees?|process(?:es)?)\b"
    rf"|\b(?:agents?|models?|assistants?){_GAP}(?:reading|processing){_GAP}this\b"
    r"|\[bleep\]"
    rf"|\bbleep(?:['’]s)?{_GAP}(?:operator|must|assistant|agent)\b"
    rf"|\byour{_GAP}(?:diagnosis|root{_GAP}cause)\b"
    rf"|\b(?:in|into|to){_GAP}your{_GAP}(?:report|summary|answer|reply)\b"
    # message headers: an order needs a verb as well, a syslog "System:" line has none
    rf"|{_START}system(?:{_GAP}prompt)?(?:{_GAP}(?:update|message|notice|override))?\s{{0,4}}:"
    r"|\[system\]"
    rf"|\b(?:operator|admin(?:istrator)?){_GAP}override\b"
    rf"|\b(?:new|updated){_GAP}instructions\b"
)
_AUTHORITY = re.compile(
    rf"\bsystem{_GAP}(?:override|prompt{_GAP}(?:update|override))\b"
    r"|[\"']approved_by[\"']"
    rf"|\bignore{_GAP}(?:(?:all|any){_GAP})?(?:your{_GAP}(?:{_EARLIER}{_GAP})?|(?:the{_GAP})?"
    rf"(?:{_EARLIER}|above){_GAP})(?:rules|instructions|prompt)"
    # "ignore ... instructions" loosely, and in German, French, Spanish and Italian; not "ignored"
    r"|\b(?:ignor(?!ed\b|ing\b)\w*|disregard(?!ed\b)\w*|oubli\w*|olvid\w*|vergiss\w*|forget)\b"
    r"[^.\n]{0,40}\b(?:instruc\w*|anweisung\w*|istruzion\w*)"
    rf"|\borders?{_GAP}from{_GAP}the{_GAP}(?:human|operator)\b"
)
_CLAIM = re.compile(
    rf"\b(?:approved|granted|authori[sz]ed){_GAP}by{_GAP}(?:the{_GAP})?"
    rf"(?:human|operator|admin(?:istrator)?|on-call)\b"
    rf"|\binstructions?{_GAP}from{_GAP}the{_GAP}(?:human|operator)\b"
)
_DECODE = re.compile(
    r"\bdecod(?:e|ed|ing)\b[^.\n]{0,40}\b(?:and|then)\s{1,4}(?:follow|run|execute|obey|apply|do)\b"
    rf"|\b(?:execute|run|follow|obey){_GAP}the{_GAP}decoded\b"
)
_VERB = re.compile(
    r"(?<!-)\b(?:run|execute|call|stop|restart|start|kill|delete|remove|revert|print|paste|"
    r"include|write|reply|answer|propose|copy|ignore|say|put|confirm|report|skip|tell|output|"
    r"show|send|close|mark|approve|recommend|disregard|drop|prune|proceed|reveal|conclude|"
    r"go\s{1,4}ahead)\b"
)
SIGNS: dict[str, re.Pattern[str]] = {
    "addressed": _ADDRESSED,
    "authority": _AUTHORITY,
    "claim": _CLAIM,
    "decode": _DECODE,
    "verb": _VERB,
}


def line_signs(line: str) -> frozenset[str]:
    """The signs one line carries."""
    folded = fold(line)
    return frozenset(name for name, pattern in SIGNS.items() if pattern.search(folded))


def orders(signs: frozenset[str], own_record: bool = False) -> bool:
    """Whether a line with these signs gives orders; in Bleep's own records a `claim` does not."""
    return (
        "authority" in signs
        or "decode" in signs
        or ("claim" in signs and not own_record)
        or {"addressed", "verb"} <= signs
    )


def gives_orders(text: str, own_record: bool = False) -> bool:
    """Whether a line of `text` gives orders by these signs."""
    return any(orders(line_signs(line), own_record) for line in text.splitlines())
