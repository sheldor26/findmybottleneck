"""What a verdict is.

Three rules hold for everything in here, and they are what separate this from
the score-ratio sites that dominate the search results:

* Something always sets the pace. "No bottleneck" is not an answer — the
  answer is which component, and by how much.
* A verdict carries the evidence that produced it, in the units it was measured
  in, so it can be argued with.
* A rule with no published source behind it is printed as a heuristic and says
  so. Thresholds are judgement, and judgement gets labelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Verdict:
    limiter: str               # gpu | cpu | frame-cap | stall | unknown
    headline: str
    share: float               # fraction of frames this explains, 0..1
    evidence: List[str] = field(default_factory=list)
    fix: str = ""
    source: Optional[str] = None
    heuristic: bool = False


@dataclass
class Finding:
    """A configuration problem, independent of what paced the frames."""

    severity: str              # high | medium | low | note
    title: str
    evidence: List[str] = field(default_factory=list)
    fix: str = ""
    source: Optional[str] = None
    quote: str = ""
    heuristic: bool = False


@dataclass
class Report:
    notes: str = ""             # whatever the capture was tagged with, e.g. --notes
    verdict: Optional[Verdict] = None
    secondary: List[Verdict] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    frames: dict = field(default_factory=dict)
    hitches: List[dict] = field(default_factory=list)
    not_measured: List[str] = field(default_factory=list)
