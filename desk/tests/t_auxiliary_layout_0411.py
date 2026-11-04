"""Auxiliary pages, opened alone with inert bridges: no profile, updater or agent.

Optional Playwright is the real HTML consumer; native Qt remains a separate test.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

DESK = Path(__file__).resolve().parents[1]
HAS_BROWSER = importlib.util.find_spec('playwright') is not None


@unittest.skipUnless(HAS_BROWSER, 'Playwright required for auxiliary browser geometry')
class AuxiliaryLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(headless=True)
        except Exception:
            cls.pw.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, name, theme, width, height):
        self.context = self.browser.new_context(viewport={'width': width, 'height': height},
                                               color_scheme=theme, reduced_motion='reduce')
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.requests = []
        self.page.on('request', lambda request: self.requests.append(request.url))
        if name == 'update':
            from localharness.update_window import page
            with tempfile.TemporaryDirectory() as root:
                html = page(root)
            stub = 'window.pywebview={api:{}};'
        else:
            html = (DESK / 'resources' / (name + '.html')).read_text(encoding='utf-8')
            defaults = dict(version='2.3.0-rc50', mode='connect', python_update=True,
                            old='C:/Users/Owner/Previous programme',
                            target='C:/Users/Owner/New programme',
                            data='C:/Users/Owner/' + 'Long data folder/' * 30)
            stub = 'window.__HELENE__={platform:"linux",invoke:async c=>'
            stub += '(c==="first_start"?{stage:"create"}:c==="migration_defaults"?'
            stub += json.dumps(defaults) + ':c==="migration_inspect"?{python_update:true}:{})};'
        temporary = tempfile.TemporaryDirectory(prefix='auxiliary-layout-')
        self.addCleanup(temporary.cleanup)
        file = Path(temporary.name) / 'page.html'
        file.write_text(html.replace('<script>', '<script>' + stub, 1), encoding='utf-8')
        self.page.goto(file.as_uri(), wait_until='load')
        self.page.evaluate('document.fonts.ready')
        self.page.wait_for_timeout(60)

    def check(self):
        self.assertEqual(self.errors, [])
        self.assertFalse(any(url.startswith(('http:', 'https:')) for url in self.requests))
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        bad = self.page.evaluate('''() => [...document.querySelectorAll('button,input,select,textarea')]
            .filter(e => e.getClientRects().length).filter(e => {
                const target=e.type==='checkbox' ? (e.closest('label')||e) : e;
                const r=target.getBoundingClientRect();
                return r.left < -1 || r.right > innerWidth+1 || r.width < 24 || r.height < 24;
            }).map(e=>e.id||e.textContent)''')
        self.assertEqual(bad, [])

    def test_first_start_custom_subscription_error_and_equal_fields(self):
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                self.open('first-start', theme, 360, 420)
                self.page.click('#soul-custom')
                self.page.locator('#soul-text').fill('Owner constitution\n' * 30)
                self.page.select_option('#provider', 'chatgpt')
                self.page.evaluate("error('Diagnostic /' + 'long/folder/'.repeat(30))")
                for field_id in ('name', 'owner', 'provider', 'model', 'soul-text', 'create'):
                    self.page.locator('#' + field_id).focus()
                    rect = self.page.locator('#' + field_id).bounding_box()
                    self.assertGreaterEqual(rect['y'], 40, field_id)
                    # Large textarea is intentionally internally scrollable, not clipped by chrome.
                    self.assertLessEqual(rect['y'] + rect['height'], 420, field_id)
                self.check()
                sizes = self.page.locator('#name,#owner,#provider,#model').evaluate_all(
                    'els=>els.map(e=>e.getBoundingClientRect().height)')
                self.assertEqual(sizes, [40, 40, 40, 40])
                self.assertTrue(self.page.locator('#key').is_disabled())
                self.assertFalse(self.page.locator('#soul-text').is_disabled())
                self.assertEqual(self.page.locator('#soul-file').bounding_box()['height'], 40)

    def test_migration_pinned_footer_long_paths_expanded_fields_and_failure(self):
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                self.open('python-migration', theme, 420, 360)
                main = self.page.locator('main').bounding_box()
                self.assertGreaterEqual(main['height'], 220)
                first_field = self.page.locator('#mode').locator('..').bounding_box()
                self.assertGreaterEqual(first_field['y'], main['y'])
                self.assertLessEqual(first_field['y'] + first_field['height'], main['y'] + main['height'])
                initial = self.page.locator('#install').bounding_box()
                self.assertGreaterEqual(initial['y'], 40)
                self.assertLessEqual(initial['y'] + initial['height'], 360)
                self.page.locator('#move-data').check()
                self.page.locator('details').first.evaluate('(e)=>e.open=true')
                for field_id in ('mode', 'old', 'data', 'data-destination', 'destination'):
                    self.page.locator('#' + field_id).focus()
                    self.page.wait_for_timeout(60)
                    rect = self.page.locator('#' + field_id).bounding_box()
                    footer = self.page.locator('.actions').bounding_box()
                    self.assertGreaterEqual(rect['y'], 40, field_id)
                    self.assertLessEqual(rect['y'] + rect['height'], footer['y'] + 1, field_id)
                self.page.locator('#previous').scroll_into_view_if_needed()
                whole_field = self.page.locator('#previous').bounding_box()
                viewport = self.page.locator('main').bounding_box()
                self.assertGreaterEqual(whole_field['y'], viewport['y'])
                self.assertLessEqual(whole_field['y'] + whole_field['height'], viewport['y'] + viewport['height'])
                self.page.locator('#destination').scroll_into_view_if_needed()
                self.page.locator('#destination').focus()
                self.page.wait_for_timeout(60)
                action = self.page.locator('.actions').bounding_box()
                field = self.page.locator('#destination').bounding_box()
                self.assertLessEqual(field['y'] + field['height'], action['y'] + 1)
                button = self.page.locator('#install').bounding_box()
                self.assertGreaterEqual(button['y'], 40)
                self.assertLessEqual(button['y'] + button['height'], 360)
                self.check()
                self.page.evaluate("$('prepare').hidden=true;progress({stage:'failed',label:'Установка требует внимания'});showError('long/path/'.repeat(80));document.querySelector('#error-box details').open=true;$('finish').hidden=false")
                self.page.locator('#close').scroll_into_view_if_needed()
                self.check()

    def test_updater_minimum_source_choice_and_terminal_states(self):
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                self.open('update', theme, 360, 300)
                self.page.evaluate("paint({stage:'choosing_source',source_choice:{version:'2.3.0-rc50'},stages:[]})")
                self.page.locator('#cancel-choice').scroll_into_view_if_needed()
                self.check()
                for stage in ('failed', 'completed', 'cancelled'):
                    self.page.evaluate('(stage)=>paint({stage,stages:[],can_close:true,error:"Diagnostic /"+"path/".repeat(60)})', stage)
                    self.check()
                    self.assertFalse(self.page.locator('#close').is_disabled())


if __name__ == '__main__':
    unittest.main(verbosity=2)
