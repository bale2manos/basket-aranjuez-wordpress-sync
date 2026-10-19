from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


SectionName = Literal["senior", "alevin", "other"]
EntryTargetMode = Literal["url", "title", "latest"]


@dataclass(slots=True)
class PartialScore:
    label: str
    home_score: int | None
    away_score: int | None
    raw_value: str = ""


@dataclass(slots=True)
class ParsedChronicle:
    raw_heading: str
    normalized_heading: str
    section: str
    competition_lines: list[str]
    result_raw: str | None
    home_team: str | None
    away_team: str | None
    home_score: int | None
    away_score: int | None
    basket_is_home: bool | None
    basket_score: int | None
    rival_team: str | None
    rival_score: int | None
    partials: list[PartialScore] = field(default_factory=list)
    players_text: str | None = None
    body_paragraphs: list[str] = field(default_factory=list)
    is_complete: bool = False
    warnings: list[str] = field(default_factory=list)
    source_paragraphs: list[str] = field(default_factory=list)
    order_index: int = 0

    @property
    def competition_title(self) -> str:
        return self.competition_lines[0] if self.competition_lines else ""

    @property
    def group_title(self) -> str:
        if len(self.competition_lines) >= 2:
            return self.competition_lines[1]
        return ""


@dataclass(slots=True)
class TeamConfig:
    section: str
    web_block_name: str
    heading_aliases: list[str] = field(default_factory=list)
    competition_aliases: list[str] = field(default_factory=list)
    result_aliases: list[str] = field(default_factory=list)
    photo_aliases: list[str] = field(default_factory=list)
    display_competition: str | None = None
    display_group: str | None = None
    expected_order: int = 0
    match_rules: list[dict[str, str]] = field(default_factory=list)
    anchor_aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MappedChronicle:
    team_config: TeamConfig
    parsed_chronicle: ParsedChronicle | None
    confidence: float
    warnings: list[str] = field(default_factory=list)
    chronicles: list[ParsedChronicle] = field(default_factory=list)

    @property
    def all_chronicles(self) -> list[ParsedChronicle]:
        if self.chronicles:
            return list(self.chronicles)
        if self.parsed_chronicle is not None:
            return [self.parsed_chronicle]
        return []


@dataclass(slots=True)
class DeletionPlan:
    team_config: TeamConfig
    delete_mode: str
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EntryTarget:
    mode: EntryTargetMode
    value: str


@dataclass(slots=True)
class RunEvent:
    level: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunPlan:
    section: str
    entry_target: EntryTarget | None
    updates: list[MappedChronicle] = field(default_factory=list)
    deletions: list[DeletionPlan] = field(default_factory=list)
    ignored: list[ParsedChronicle] = field(default_factory=list)
    errors: list[RunEvent] = field(default_factory=list)

    @property
    def expected_count(self) -> int:
        return len(self.updates) + len(self.deletions)
