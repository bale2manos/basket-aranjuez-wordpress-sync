from pathlib import Path
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from basket_wordpress_sync.models import PartialScore
from basket_wordpress_sync.wordpress.contexts import TeamSectionAnchor, PERIOD_LABEL_RE
from basket_wordpress_sync.wordpress.service import ElementorUpdateService
from basket_wordpress_sync.wordpress.match_layout import reconcile_match_layout


def widget(id, text, x, y, type="heading"):
    return {"dataId": id, "text": text, "x": x, "y": y, "width": 100, "height": 20, "type": type}


def make_anchor(periods=(6, 6)):
    catalog = [widget("team", "Alevin Femenino Rubiocar", 0, 0), widget("comp", "LIGA", 0, 80), widget("group", "GRUPO 8", 500, 80)]
    for match, count in enumerate(periods):
        top = 200 + match * 800
        p = f"m{match}"
        catalog += [widget(p + "home", "Local", 0, top), widget(p + "score", "65 - 15", 400, top), widget(p + "away", "Visitante", 800, top)]
        for i in range(count):
            label = f"{i+1}P" if count == 6 else f"{i+1}C" if i < 4 else "TE"
            catalog.append(widget(p + f"label{i+1}", label, 200 + i * 100, top + 100))
        for side, y in (("home", top + 200), ("away", top + 300)):
            catalog.append(widget(p + side + "name", side, 0, y))
            for i in range(count):
                catalog.append(widget(p + side + str(i+1), "9", 200 + i * 100, y))
        catalog += [widget(p + "roster", "JUGADORAS: original", 0, top + 400, "text_editor"), widget(p + "body", "Cronica", 0, top + 500, "text_editor")]
    catalog.append(widget("next", "Alevin Masculino Negro", 0, 3000))
    catalog[0]["width"] = catalog[-1]["width"] = 1000
    locator = Mock()
    locator.count.return_value = 1
    locator.first = locator
    preview = SimpleNamespace(page=Mock(), locator=lambda selector: locator)
    return TeamSectionAnchor(preview, "Alevin Femenino Rubiocar", None, None, {}, widget_catalog=catalog, current_widget=catalog[0])


class MatchIsolationTests(unittest.TestCase):
    def test_two_matches_have_disjoint_fields_and_preserve_team_boundary(self):
        views = make_anchor().match_anchors()
        self.assertEqual(len(views), 2)
        for i, view in enumerate(views):
            fields = view._structured_field_widgets()
            self.assertEqual(fields["score"]["dataId"], f"m{i}score")
            self.assertEqual(fields["chronicle_text"]["dataId"], f"m{i}body")
            self.assertEqual(fields["players_text"]["dataId"], f"m{i}roster")
            self.assertEqual(fields["away_period_6"]["dataId"], f"m{i}away6")
            self.assertNotIn("next", [w["dataId"] for w in view._block_widgets()])

    def test_single_match_without_layout_change_makes_no_editor_calls(self):
        anchor = make_anchor((6,))
        self.assertEqual(len(anchor.prepare_match_layout([6])), 1)
        anchor.preview.page.evaluate.assert_not_called()

    def test_missing_body_is_rejected_before_any_mutation(self):
        anchor = make_anchor()
        anchor.widget_catalog = [w for w in anchor.widget_catalog if w["dataId"] != "m1body"]
        with self.assertRaisesRegex(LookupError, "roster y cronica"):
            anchor.prepare_match_layout([6, 6])
        anchor.preview.page.evaluate.assert_not_called()

    def test_overtime_te_is_a_real_fifth_column(self):
        view = make_anchor((5,)).match_anchors()[0]
        self.assertTrue(PERIOD_LABEL_RE.fullmatch("TE"))
        self.assertEqual(view._structured_field_widgets()["away_period_5"]["dataId"], "m0away5")

    def test_layout_payload_excludes_team_metadata_and_next_team(self):
        anchor = make_anchor()
        reconcile_match_layout(anchor, anchor.match_anchors(), [6])
        payload = anchor.preview.page.evaluate.call_args.args[1]
        self.assertIn("team", payload["protectedIds"])
        removed = payload["matches"][1]["remove"]
        self.assertIn("m1roster", removed)
        self.assertIn("m1body", removed)
        self.assertNotIn("team", removed)
        self.assertNotIn("next", removed)


class MatchUpdateTests(unittest.TestCase):
    def service(self):
        service = object.__new__(ElementorUpdateService)
        service.settings = SimpleNamespace(runtime=SimpleNamespace(
            section_period_counts={"junior": 4, "alevin": 6},
            period_label_templates={4: "{index}C", 6: "{index}P"}, overtime_label="PR",
            empty_chronicle_placeholder="-"))
        return service

    def test_overtime_does_not_get_truncated_to_four(self):
        service = self.service()
        anchor = make_anchor((5,)).match_anchors()[0]
        chronicle = SimpleNamespace(partials=[PartialScore(label=str(i), home_score=5, away_score=7) for i in range(5)])
        self.assertEqual(service._period_slot_count("junior", anchor, chronicle), 5)
        updates = service._build_period_updates(5, chronicle, regulation_count=4)
        self.assertEqual(updates[-1]["label"], "PR")
        self.assertEqual(updates[-1]["away_weight"], "bold")

    def test_six_alevin_periods_are_not_overtimes(self):
        updates = self.service()._build_period_updates(6, SimpleNamespace(partials=[]), regulation_count=6)
        self.assertEqual([u["label"] for u in updates], [f"{i}P" for i in range(1, 7)])
        self.assertTrue(all(u["home_value"] == u["away_value"] == "-" for u in updates))

    def test_each_match_updates_its_own_body_and_never_roster(self):
        service = self.service()
        service._safe_header_update = Mock(return_value=1)
        service._safe_text_update = Mock(return_value=1)
        views = make_anchor().match_anchors()
        for i, view in enumerate(views):
            c = SimpleNamespace(home_team=f"Local {i}", away_team=f"Rival {i}", home_score=65+i,
                                away_score=15, partials=[], body_paragraphs=[f"Relato {i}"])
            service._update_match(view, None, None, "alevin", c)
        texts = service._safe_text_update.call_args_list
        self.assertEqual([c.args[2] for c in texts], ["chronicle_text", "chronicle_text"])
        self.assertEqual([c.kwargs["paragraphs"] for c in texts], [["Relato 0"], ["Relato 1"]])
        self.assertIs(texts[1].args[0], views[1])


class LocalBrowserLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1200, "height": 900})
        self.page.route("**/*", lambda route: route.abort())
        self.page.set_content('<style>body{margin:0} main{width:1000px;padding-top:600px} h2,p{margin:0;font:16px sans-serif}</style><main></main>')
        self.page.add_script_tag(content=(ROOT / "tests/fixtures/elementor_editor_mock.js").read_text(encoding="utf-8"))

    def tearDown(self):
        self.page.close()

    def install(self, period_counts, *, grid=False):
        def h(id, title):
            return {"id": id, "elType": "widget", "widgetType": "heading", "settings": {"title": title}}
        def row(id, children):
            return {"id": id, "elType": "container", "settings": {"flex_direction": "row"}, "elements": children}
        models = [h("team", "Alevin Femenino Rubiocar"), row("meta", [h("comp", "LIGA"), h("group", "GRUPO 8")])]
        for i, count in enumerate(period_counts):
            p = f"m{i}"
            models.append(row(p + "scoreRow", [h(p+"home", "Local"), h(p+"score", "65 - 15"), h(p+"away", "Visitante")]))
            models.append(row(p+"labels", [h(p+f"label{j}", f"{j}P" if count == 6 else f"{j}C" if j < 5 else "TE") for j in range(1,count+1)]))
            for side in ("home", "away"):
                models.append(row(p+side+"Row", [h(p+side+"name", side)] + [h(p+side+str(j), "9") for j in range(1,count+1)]))
            models.extend({"id": p+k, "elType": "widget", "widgetType": "text-editor", "settings": {"editor": text}} for k,text in (("roster","JUGADORAS: original"),("body","Cronica original")))
        models.append(h("next", "Alevin Masculino Negro"))
        if grid:
            for model in models:
                if model["id"].endswith(("labels", "homeRow", "awayRow")):
                    model["settings"].update(container_type="grid", grid_columns_grid={"unit": "fr", "size": len(model["elements"])})
        self.page.evaluate("models => window.installEditor(models)", models)
        preview = SimpleNamespace(root=lambda: self.page, locator=self.page.locator, page=self.page)
        anchor = TeamSectionAnchor(preview, "Alevin Femenino Rubiocar", self.page.locator('[data-id="team"]'), self.page.locator('main'), {})
        anchor._prime_widget_catalog()
        anchor._set_current_widget_by_data_id("team")
        return anchor

    def test_create_second_match_and_repeat_is_idempotent(self):
        anchor = self.install([6])
        matches = anchor.prepare_match_layout([6, 6])
        self.assertEqual(len(matches), 2)
        first, second = [m._structured_field_widgets() for m in matches]
        self.assertNotEqual(first["chronicle_text"]["dataId"], second["chronicle_text"]["dataId"])
        self.assertEqual(second["players_text"]["text"], "JUGADORAS: original")
        self.assertEqual(len(anchor.prepare_match_layout([6, 6])), 2)
        self.assertEqual(self.page.locator('.basket-sync-match').count(), 1)
        self.assertEqual(self.page.locator('[data-id="next"]').inner_text(), "Alevin Masculino Negro")

    def test_expand_four_to_five_real_columns(self):
        anchor = self.install([4])
        fields = anchor.prepare_match_layout([5])[0]._structured_field_widgets()
        for prefix in ("period_label", "home_period", "away_period"):
            self.assertIn(prefix + "_5", fields)
        self.assertEqual(self.page.locator('[data-id="m0roster"]').inner_text(), "JUGADORAS: original")

    def test_expand_grid_table_keeps_all_cells_in_one_row(self):
        anchor = self.install([4], grid=True)
        fields = anchor.prepare_match_layout([5])[0]._structured_field_widgets()
        for prefix in ("period_label", "home_period", "away_period"):
            a, b = fields[prefix + "_1"], fields[prefix + "_5"]
            self.assertAlmostEqual(a["y"], b["y"], delta=1)
            self.assertLess(a["x"], b["x"])

    def test_catalog_excludes_footer_widgets_from_other_documents(self):
        anchor = self.install([6])
        self.page.evaluate("""() => {
          const main = document.querySelector('main');
          main.classList.add('elementor'); main.dataset.elementorId = '123';
          const footer = document.createElement('div');
          footer.innerHTML = '<div class="elementor-widget elementor-widget-text-editor" data-id="footer">Footer</div>';
          document.body.append(footer);
        }""")
        anchor.preview.page = SimpleNamespace(url="https://example.test/wp-admin/post.php?post=123&action=elementor")
        anchor.widget_catalog.clear()
        anchor._prime_widget_catalog()
        self.assertNotIn("footer", [w["dataId"] for w in anchor.widget_catalog])

    def test_remove_whole_surplus_match_without_touching_roster_of_first(self):
        anchor = self.install([6, 6])
        self.assertEqual(len(anchor.prepare_match_layout([6])), 1)
        self.assertEqual(self.page.locator('[data-id="m1body"]').count(), 0)
        self.assertEqual(self.page.locator('[data-id="m1scoreRow"]').count(), 0)
        self.assertEqual(self.page.locator('[data-id="m0roster"]').count(), 1)
        self.assertEqual(self.page.locator('[data-id="team"]').count(), 1)
        self.assertEqual(self.page.locator('[data-id="next"]').count(), 1)

    @unittest.skipUnless(os.environ.get("BASKET_SYNC_TEST_DOCX"), "Optional real DOCX simulation")
    def test_real_docx_match_values_in_local_editor(self):
        from basket_wordpress_sync.parser import ChronicleParser
        from basket_wordpress_sync.planner import DryRunPlanner
        from basket_wordpress_sync.settings import load_settings
        from basket_wordpress_sync.normalizer.text import format_team_display

        settings = load_settings(ROOT)
        chronicles = ChronicleParser().parse_docx(Path(os.environ["BASKET_SYNC_TEST_DOCX"]))
        service = object.__new__(ElementorUpdateService)
        service.settings = settings
        service._safe_team_photo_update = Mock(return_value=0)

        def write_header(anchor, adapter, field, value, **kwargs):
            if value is None:
                return 0
            target = anchor._structured_field_widgets()[field]["dataId"]
            self.page.evaluate("""async ({id, title}) => {
              const root = window.elementor.documents.getCurrent().container;
              const find = n => n.id === id ? n : n.children.map(find).find(Boolean);
              await window.$e.run('document/elements/settings', {container: find(root), settings: {title}});
            }""", {"id": target, "title": value})
            return 1

        def write_body(anchor, adapter, field, *, paragraphs, players_text):
            self.assertEqual(field, "chronicle_text")
            target = anchor._structured_field_widgets()[field]["dataId"]
            self.page.evaluate("""async ({id, editor}) => {
              const root = window.elementor.documents.getCurrent().container;
              const find = n => n.id === id ? n : n.children.map(find).find(Boolean);
              await window.$e.run('document/elements/settings', {container: find(root), settings: {editor}});
            }""", {"id": target, "editor": "\n".join(paragraphs)})
            return 1

        service._safe_header_update = write_header
        service._safe_text_update = write_body
        teams = games = overtimes = doubles = 0
        for section in dict.fromkeys(t.section for t in settings.teams):
            plan = DryRunPlanner(settings.teams).build_plan(section, chronicles)
            self.assertFalse([e for e in plan.errors if e.level == "error"])
            for mapped in plan.updates:
                base = settings.runtime.section_period_counts.get(section, 4)
                anchor = self.install([base])
                service._update_block(anchor=anchor, side_panel=None, mapped_chronicle=mapped)
                views = anchor.match_anchors()
                self.assertEqual(len(views), len(mapped.all_chronicles))
                for view, match in zip(views, mapped.all_chronicles):
                    fields = view._structured_field_widgets()
                    def text(name):
                        return self.page.locator('[data-id="' + fields[name]["dataId"] + '"]').inner_text()
                    self.assertEqual(text("home_team"), format_team_display(match.home_team))
                    self.assertEqual(text("away_team"), format_team_display(match.away_team))
                    self.assertEqual(text("score"), f"{match.home_score} - {match.away_score}")
                    self.assertEqual(text("players_text"), "JUGADORAS: original")
                    wanted_body = "\n".join(p for p in match.body_paragraphs if p and p.strip()) or "-"
                    body_id = fields["chronicle_text"]["dataId"]
                    self.assertEqual(self.page.locator(f'[data-id="{body_id}"]').text_content(), wanted_body)
                    for i, partial in enumerate(match.partials, 1):
                        home, away, _ = service._period_display_values(partial)
                        self.assertEqual(text(f"home_period_{i}"), home)
                        self.assertEqual(text(f"away_period_{i}"), away)
                    if len(match.partials) > base:
                        self.assertEqual(text(f"period_label_{base + 1}"), settings.runtime.overtime_label)
                        overtimes += 1
                    games += 1
                teams += 1
                doubles += len(views) > 1
        print(f"\nLocal DOCX simulation: {teams} teams, {games} games, {doubles} double fixtures, {overtimes} overtimes; no network.")


if __name__ == "__main__":
    unittest.main()
