"""Signs that host text tries to give Bleep orders, found by code alone (#271).

Each line is folded as the tag check folds it (NFKC, no invisible characters, look-alike letters
mapped to Latin, lower case) and read for four signs:

- `addressed`: it speaks to an AI reader ("Note for AI agents", "Assistant,", "Agent:",
  "[bleep]", "Bleep must", "to the Bleep assistant:", "in your diagnosis");
- `authority`: it poses as a system message, an override or an approval ("SYSTEM:", "operator
  override", "approved by the human", `"approved_by"`), says to ignore the rules or gives
  orders from the human;
- `decode`: it asks for something to be decoded and followed;
- `verb`: it holds a verb of action (run, stop, restart, write, include, ...).

A line gives orders when it has `authority` or `decode`, or when it is `addressed` and has a
`verb`. An address or a verb alone is everyday text. The signs only tell the human; they decide
nothing. Every pattern is linear: gaps are bounded and nothing nests.
"""

import re

from bleep.guard.untrusted import fold

_GAP = r"\s{1,4}"
_START = r"(?:^\s{0,8}|[.:;!?>]\s{1,4}|[\"'(\[{]\s{0,4})"  # a clause or a quote starts here

_EARLIER = r"(?:previous|prior|earlier)"

_ADDRESSED = re.compile(
    rf"\b(?:ai|llm)[\s-]{{1,4}}(?:agent|assistant|model|bot)s?\b"
    rf"|\b(?:to|for){_GAP}(?:(?:the|any){_GAP})?(?:bleep{_GAP})?(?:assistant|agent|bot|llm|model)s?"
    rf"(?:{_GAP}(?:reading|running)\b|\s{{0,4}}:)"
    rf"|{_START}(?:assistant|agent|bleep)s?\s{{0,4}}[,:]"
    r"|\[bleep\]"
    rf"|\bbleep(?:['’]s)?{_GAP}(?:operator|must|assistant|agent)\b"
    rf"|\byour{_GAP}(?:diagnosis|root{_GAP}cause)\b"
)
_AUTHORITY = re.compile(
    rf"{_START}system(?:{_GAP}prompt)?(?:{_GAP}(?:update|message|notice|override))?\s{{0,4}}:"
    r"|\[system\]"
    rf"|\bsystem{_GAP}(?:override|prompt{_GAP}(?:update|override))\b"
    rf"|\b(?:operator|admin(?:istrator)?){_GAP}override\b"
    rf"|\b(?:approved|granted|authori[sz]ed){_GAP}by{_GAP}(?:the{_GAP})?"
    rf"(?:human|operator|admin(?:istrator)?|on-call)\b"
    r"|[\"']approved_by[\"']"
    rf"|\b(?:new|updated){_GAP}instructions\b"
    rf"|\bignore{_GAP}(?:(?:all|any){_GAP})?(?:your{_GAP}(?:{_EARLIER}{_GAP})?|(?:the{_GAP})?"
    rf"(?:{_EARLIER}|above){_GAP})(?:rules|instructions|prompt)"
    rf"|\b(?:orders?|instructions?){_GAP}from{_GAP}the{_GAP}(?:human|operator)\b"
)
_DECODE = re.compile(
    rf"\bdecod(?:e|ed|ing){_GAP}(?:(?:it|this){_GAP})?and{_GAP}(?:follow|run|execute|obey|apply)"
)
_VERB = re.compile(
    r"\b(?:run|execute|call|stop|restart|start|kill|delete|remove|revert|print|paste|include|"
    r"write|reply|answer|propose|copy|ignore|say|put|confirm|report|skip)\b"
)
SIGNS: dict[str, re.Pattern[str]] = {
    "addressed": _ADDRESSED,
    "authority": _AUTHORITY,
    "decode": _DECODE,
    "verb": _VERB,
}


def line_signs(line: str) -> frozenset[str]:
    """The signs one line carries."""
    folded = fold(line)
    return frozenset(name for name, pattern in SIGNS.items() if pattern.search(folded))


def orders(signs: frozenset[str]) -> bool:
    """Whether a line with these signs gives orders."""
    return "authority" in signs or "decode" in signs or {"addressed", "verb"} <= signs


def gives_orders(text: str) -> bool:
    """Whether a line of `text` gives orders by these signs."""
    return any(orders(line_signs(line)) for line in text.splitlines())
