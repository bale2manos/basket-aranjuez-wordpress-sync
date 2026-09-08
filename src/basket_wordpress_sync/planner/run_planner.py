from __future__ import annotations

from ..mapper import TeamMapper
from ..models import DeletionPlan, EntryTarget, ParsedChronicle, RunEvent, RunPlan, TeamConfig
from ..normalizer.text import matches_target_section


class DryRunPlanner:
    def __init__(self, teams: list[TeamConfig], *, delete_mode: str = "clear_texts_only") -> None:
        self.teams = list(teams)
        self.delete_mode = delete_mode
        self.mapper = TeamMapper(self.teams)

    def build_plan(
        self,
        section: str,
        chronicles: list[ParsedChronicle],
        *,
        entry_target: EntryTarget | None = None,
    ) -> RunPlan:
        section_teams = [team for team in self.teams if team.section == section]
        relevant = [
            chronicle
            for chronicle in chronicles
            if matches_target_section(section, chronicle_section=chronicle.section, heading=chronicle.raw_heading)
        ]
        ignored = [
            chronicle
            for chronicle in chronicles
            if not matches_target_section(section, chronicle_section=chronicle.section, heading=chronicle.raw_heading)
        ]
        updates, mapping_errors = self.mapper.map_section(section, relevant)

        matched_names = {item.team_config.web_block_name for item in updates}
        deletions = [
            DeletionPlan(
                team_config=team,
                delete_mode=self.delete_mode,
                warnings=self._deletion_warnings(team.web_block_name),
            )
            for team in sorted(section_teams, key=lambda item: item.expected_order)
            if team.web_block_name not in matched_names
        ]

        errors = list(mapping_errors)
        for chronicle in relevant:
            for warning in chronicle.warnings:
                level = "warning"
                code = warning.split(":", 1)[0] if ":" in warning else warning
                if code in {"parse_error", "mapping_error", "block_not_found", "widget_edit_error", "save_error"}:
                    level = "error"
                errors.append(
                    RunEvent(
                        level=level,
                        code=code,
                        message=warning,
                        details={"heading": chronicle.raw_heading},
                    )
                )

        return RunPlan(
            section=section,
            entry_target=entry_target,
            updates=updates,
            deletions=deletions,
            ignored=ignored,
            errors=self._dedupe_errors(errors),
        )

    def _deletion_warnings(self, team_name: str) -> list[str]:
        warnings = [f"team_absent_in_docx:{team_name}"]
        if self.delete_mode == "skip_and_warn":
            warnings.append("delete_mode_skip_and_warn")
        return warnings

    def _dedupe_errors(self, errors: list[RunEvent]) -> list[RunEvent]:
        unique: list[RunEvent] = []
        seen: set[tuple[str, str, str]] = set()
        for error in errors:
            key = (error.level, error.code, error.message)
            if key not in seen:
                unique.append(error)
                seen.add(key)
        return unique
