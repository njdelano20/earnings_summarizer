"""
segments.py
The business map for one company: which segments / sub-segments / geographies / customer groups /
initiatives / cost drivers exist, how they nest, and how to recognise them.

Two things are resolved through it:
  * a ledger fact's `segment` string  -> node id   (exact, normalised match against node["facts"])
  * a management sentence             -> node ids  (regex match against node["text"])

The registry is hand-maintained reference data in segments/<TICKER>.json (like the symbol tables in the
MarketDataLibrary). A ticker without a file gets an auto-built flat registry from the segments that appear
in its facts, and the snapshot flags that so nobody mistakes it for a curated map.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REGISTRY_FOLDER = Path(__file__).resolve().parent / "segments"

DIMENSIONS = ("segment", "geography", "customer", "initiative", "driver")
DIMENSION_TITLES = {
    "segment": "Business segments",
    "geography": "Geographies",
    "customer": "Customer groups",
    "initiative": "Strategic initiatives",
    "driver": "Cost, supply, currency and regulatory drivers",
}

TOTAL = "total"   # the consolidated company; not a registry node

# Words the fact extractor sometimes lifts as a "segment" ("In addition, we expect...", "Segment revenue grew 6%").
# An auto-built registry must not turn them into business areas; those facts show up as unmapped instead.
_NOT_A_SEGMENT = {"segment", "segments", "addition", "business", "company", "overall", "consolidated", "other",
                  "quarter", "year", "results", "revenue"}


@dataclass
class Node:
    id: str
    name: str
    dimension: str
    parent: str | None = None
    topic: str | None = None      # topic every sentence about this node also gets (e.g. product initiatives)
    facts: list[str] = field(default_factory=list)
    patterns: list[re.Pattern] = field(default_factory=list)


@dataclass
class Registry:
    ticker: str | None
    nodes: list[Node]
    curated: bool

    def __post_init__(self):
        self._by_id = {n.id: n for n in self.nodes}
        self._by_fact = {}
        for n in self.nodes:
            for alias in n.facts:
                self._by_fact.setdefault(_norm(alias), n.id)

    def get(self, node_id: str) -> Node:
        return self._by_id[node_id]

    def ids(self) -> list[str]:
        return [n.id for n in self.nodes]

    def children(self, node_id: str) -> list[Node]:
        return [n for n in self.nodes if n.parent == node_id]

    def depth(self, node_id: str) -> int:
        d, cur = 0, self._by_id[node_id]
        while cur.parent and cur.parent in self._by_id:
            d, cur = d + 1, self._by_id[cur.parent]
        return d

    def ancestors(self, node_id: str) -> list[str]:
        out, cur = [], self._by_id[node_id]
        while cur.parent and cur.parent in self._by_id:
            out.append(cur.parent)
            cur = self._by_id[cur.parent]
        return out

    def node_for_fact_segment(self, segment: str | None) -> str | None:
        """Ledger segment string -> node id ('total' for the consolidated company, None if unmapped)."""
        if segment is None:
            return None
        s = _norm(segment)
        if s == TOTAL:
            return TOTAL
        return self._by_fact.get(s)

    def nodes_in_text(self, text: str) -> list[str]:
        """Node ids whose text patterns match `text`, in order of first mention (the grammatical subject comes first)."""
        spans = {n.id: [m.span() for p in n.patterns for m in p.finditer(text)] for n in self.nodes}
        spans = {i: s for i, s in spans.items() if s}

        def inside_a_child(node_id: str, span: tuple[int, int]) -> bool:
            # "Technology Consulting" also matches "consulting"; that parent hit is not a separate mention.
            return any(node_id in self.ancestors(d) and c[0] <= span[0] and span[1] <= c[1]
                       for d, cs in spans.items() if d != node_id for c in cs)

        found = []
        for n in self.nodes:
            own = [s for s in spans.get(n.id, []) if not inside_a_child(n.id, s)]
            if own:
                found.append((min(s[0] for s in own), -self.depth(n.id), self.ids().index(n.id), n.id))
        return [f[-1] for f in sorted(found)]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def load_registry(ticker: str | None, fact_segments: list[str] | None = None) -> Registry:
    """Curated registry if segments/<TICKER>.json exists, else an auto-built flat one."""
    path = REGISTRY_FOLDER / f"{(ticker or '').upper()}.json"
    if ticker and path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        nodes = [Node(id=n["id"], name=n["name"], dimension=n.get("dimension", "segment"),
                      parent=n.get("parent"), topic=n.get("topic"), facts=list(n.get("facts", [])),
                      patterns=_compile(n.get("text", []))) for n in raw["nodes"]]
        return Registry(ticker=ticker.upper(), nodes=nodes, curated=True)

    nodes, seen = [], set()
    for seg in fact_segments or []:
        if not seg or _norm(seg) == TOTAL or _norm(seg) in seen or _norm(seg) in _NOT_A_SEGMENT:
            continue
        seen.add(_norm(seg))
        node_id = re.sub(r"[^a-z0-9]+", "_", _norm(seg)).strip("_") or "segment"
        nodes.append(Node(id=node_id, name=seg.title(), dimension="segment", facts=[seg],
                          patterns=_compile([rf"\b{re.escape(seg)}\b"])))
    return Registry(ticker=(ticker or None), nodes=nodes, curated=False)
