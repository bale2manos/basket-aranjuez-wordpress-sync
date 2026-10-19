from __future__ import annotations

from dataclasses import dataclass
import re

from ..models import MappedChronicle, ParsedChronicle, RunEvent, TeamConfig
from ..normalizer.text import normalize_lookup_key


MIN_CONFIDENCE = 0.60
AMBIGUITY_DELTA = 0.05


@dataclass(slots=True)
class CandidateScore:
    team_config: TeamConfig
    confidence: float
    warnings: list[str]
    direct_evidence: bool = False
    result_evidence_score: float = 0.0


class TeamMapper:
    def __init__(self, teams: list[TeamConfig]) -> None:
        self.teams = list(teams)

    def map_section(
        self,
        section: str,
        chronicles: list[ParsedChronicle],
    ) -> tuple[list[MappedChronicle], list[RunEvent]]:
        section_teams = [team for team in self.teams if team.section == section]
        provisional: list[tuple[ParsedChronicle, CandidateScore]] = []
        errors: list[RunEvent] = []
        chronicle_candidates: list[tuple[ParsedChronicle, list[CandidateScore]]] = []

        for relative_order, chronicle in enumerate(chronicles, start=1):
            candidates = sorted(
                (self._score_candidate(team, chronicle, relative_order) for team in section_teams),
                key=lambda candidate: candidate.confidence,
                reverse=True,
            )
            chronicle_candidates.append((chronicle, candidates))

        assigned_counts: dict[str, int] = {}
        ordered_candidates = sorted(
            chronicle_candidates,
            key=lambda item: (
                0 if item[1] and item[1][0].direct_evidence else 1,
                -(item[1][0].confidence if item[1] else 0.0),
                item[0].order_index,
            ),
        )
        for chronicle, candidates in ordered_candidates:
            top_candidate = candidates[0] if candidates else None
            second_candidate = candidates[1] if len(candidates) > 1 else None
            if top_candidate is None or top_candidate.confidence < MIN_CONFIDENCE:
                errors.append(
                    RunEvent(
                        level="error",
                        code="mapping_error",
                        message=f"No se pudo mapear el bloque '{chronicle.raw_heading}'.",
                        details={"heading": chronicle.raw_heading},
                    )
                )
                continue
            chosen_candidate = self._select_candidate(candidates, assigned_counts)
            if chosen_candidate is None:
                chosen_candidate = top_candidate
            if (
                chosen_candidate is top_candidate
                and second_candidate is not None
                and top_candidate.confidence - second_candidate.confidence <= AMBIGUITY_DELTA
            ):
                # If both candidates are close but one has clearly stronger result evidence,
                # trust that specific match and avoid a false ambiguity.
                if (
                    top_candidate.result_evidence_score - second_candidate.result_evidence_score
                    >= 0.05
                ):
                    provisional.append((chronicle, chosen_candidate))
                    assigned_counts[chosen_candidate.team_config.web_block_name] = (
                        assigned_counts.get(chosen_candidate.team_config.web_block_name, 0) + 1
                    )
                    continue
                errors.append(
                    RunEvent(
                        level="error",
                        code="ambiguous_block",
                        message=(
                            f"Mapeo ambiguo para '{chronicle.raw_heading}' entre "
                            f"'{top_candidate.team_config.web_block_name}' y '{second_candidate.team_config.web_block_name}'."
                        ),
                        details={
                            "heading": chronicle.raw_heading,
                            "first": top_candidate.team_config.web_block_name,
                            "second": second_candidate.team_config.web_block_name,
                        },
                    )
                )
                continue
            provisional.append((chronicle, chosen_candidate))
            assigned_counts[chosen_candidate.team_config.web_block_name] = (
                assigned_counts.get(chosen_candidate.team_config.web_block_name, 0) + 1
            )

        grouped_by_team: dict[str, list[tuple[ParsedChronicle, CandidateScore]]] = {}
        for chronicle, candidate in provisional:
            grouped_by_team.setdefault(candidate.team_config.web_block_name, []).append((chronicle, candidate))

        mapped: list[MappedChronicle] = []
        for team_name, entries in grouped_by_team.items():
            entries.sort(key=lambda item: item[0].order_index)
            chronicle, candidate = max(entries, key=self._primary_entry_score)
            all_chronicles = [entry_chronicle for entry_chronicle, _entry_candidate in entries]
            warnings = list(candidate.warnings)
            if len(all_chronicles) > 1:
                warnings.append(f"multiple_chronicles:{len(all_chronicles)}")
            mapped.append(
                MappedChronicle(
                    team_config=candidate.team_config,
                    parsed_chronicle=chronicle,
                    confidence=round(max(item[1].confidence for item in entries), 2),
                    warnings=self._unique(warnings),
                    chronicles=all_chronicles,
                )
            )

        mapped.sort(key=lambda item: item.team_config.expected_order)
        return mapped, errors

    def _primary_entry_score(self, entry: tuple[ParsedChronicle, CandidateScore]) -> tuple[int, int, int, int, int, float, int]:
        chronicle, candidate = entry
        body_count = len([paragraph for paragraph in chronicle.body_paragraphs if paragraph and paragraph.strip()])
        partial_count = len(chronicle.partials or [])
        return (
            1 if chronicle.is_complete else 0,
            1 if chronicle.home_score is not None and chronicle.away_score is not None else 0,
            partial_count,
            body_count,
            1 if chronicle.players_text else 0,
            candidate.confidence,
            -int(chronicle.order_index or 0),
        )

    def _score_candidate(self, team: TeamConfig, chronicle: ParsedChronicle, relative_order: int) -> CandidateScore:
        if team.match_rules:
            basket_name = chronicle.home_team if chronicle.basket_is_home else chronicle.away_team
            for rule in team.match_rules:
                heading_ok = normalize_lookup_key(rule.get("heading", "")) == chronicle.normalized_heading
                competition_ok = all(
                    re.search(r"(?:^| )" + re.escape(normalize_lookup_key(part)) + r"(?: |$)", normalize_lookup_key(" ".join(chronicle.competition_lines))) is not None
                    for part in rule.get("competition", "").split("|") if part
                )
                basket_ok = not rule.get("basket_name") or normalize_lookup_key(rule["basket_name"]) == normalize_lookup_key(basket_name or "")
                if heading_ok and competition_ok and basket_ok:
                    return CandidateScore(team, 1.0, ["mapped_by_season_rule"], True, 0.0)
            return CandidateScore(team, 0.0, [], False, 0.0)
        heading_text = chronicle.normalized_heading
        competition_text = normalize_lookup_key(" ".join(chronicle.competition_lines))
        result_text = normalize_lookup_key(
            " ".join(
                part
                for part in (
                    chronicle.result_raw or "",
                    chronicle.home_team or "",
                    chronicle.away_team or "",
                    chronicle.rival_team or "",
                )
                if part
            )
        )
        warnings: list[str] = []
        heading_score = 0.0
        competition_score = 0.0
        result_score = 0.0
        order_bonus = 0.0

        heading_targets = [team.web_block_name, *team.heading_aliases]
        for target in heading_targets:
            target_norm = normalize_lookup_key(target)
            if not target_norm:
                continue
            if target_norm == heading_text:
                heading_score = max(heading_score, 0.75)
                continue
            if target_norm in heading_text or heading_text in target_norm:
                heading_score = max(heading_score, 0.60)
                continue
            overlap = self._token_overlap(target_norm, heading_text)
            if overlap >= 0.66:
                heading_score = max(heading_score, 0.42 + (0.18 * overlap))

        for alias in team.result_aliases:
            alias_norm = normalize_lookup_key(alias)
            if not alias_norm:
                continue
            specificity = self._alias_specificity(alias_norm)
            if alias_norm in result_text:
                result_score = max(result_score, 0.08 + (0.07 * specificity))
                warnings.append("mapped_by_result_alias")
                continue
            overlap = self._token_overlap(alias_norm, result_text)
            if overlap >= 0.60:
                result_score = max(result_score, 0.05 + (0.04 * specificity) + (0.03 * overlap))
                warnings.append("mapped_by_result_alias")

        for alias in team.competition_aliases:
            alias_norm = normalize_lookup_key(alias)
            if not alias_norm:
                continue
            if alias_norm == competition_text:
                competition_score = max(competition_score, 0.22)
                warnings.append("mapped_by_competition_alias")
                continue
            if alias_norm in competition_text or competition_text in alias_norm:
                competition_score = max(competition_score, 0.15)
                warnings.append("mapped_by_competition_alias")
                continue
            overlap = self._token_overlap(alias_norm, competition_text)
            if overlap >= 0.60:
                competition_score = max(competition_score, 0.10 + (0.06 * overlap))
                warnings.append("mapped_by_competition_alias")

        if relative_order and team.expected_order:
            distance = abs(relative_order - team.expected_order)
            order_bonus = max(0.0, 0.24 - (0.06 * distance))

        confidence = heading_score + competition_score + result_score + order_bonus
        direct_evidence = result_score > 0.0 or competition_score > 0.0
        return CandidateScore(
            team_config=team,
            confidence=round(confidence, 4),
            warnings=self._unique(warnings),
            direct_evidence=direct_evidence,
            result_evidence_score=round(result_score, 4),
        )

    def _select_candidate(self, candidates: list[CandidateScore], assigned_counts: dict[str, int]) -> CandidateScore | None:
        if not candidates:
            return None
        top_candidate = candidates[0]
        top_team = top_candidate.team_config.web_block_name
        if assigned_counts.get(top_team, 0) == 0:
            return top_candidate
        if top_candidate.direct_evidence:
            return top_candidate

        for candidate in candidates[1:]:
            team_name = candidate.team_config.web_block_name
            if candidate.confidence < MIN_CONFIDENCE:
                continue
            if assigned_counts.get(team_name, 0) > 0:
                continue
            if top_candidate.confidence - candidate.confidence <= 0.12:
                return candidate
        return top_candidate

    def _alias_specificity(self, alias_norm: str) -> int:
        return len([token for token in alias_norm.split() if token])

    def _token_overlap(self, left: str, right: str) -> float:
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        if union == 0:
            return 0.0
        return intersection / union

    def _unique(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for item in items:
            if item not in seen:
                unique.append(item)
                seen.add(item)
        return unique
