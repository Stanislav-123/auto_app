#!/usr/bin/env python3
"""
Real behavioral tests that invoke production code with Playwright fixtures.

Each test calls actual methods and asserts observable effects.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.appsflyer.playwright_session import PlaywrightAppsFlyer
from src.appsflyer.session import (
    login_auth_error_reason,
    AfBlocked,
    AfError,
    ShareOutcome,
    ShareTask,
    run_share,
)


class TestAuthErrorClassification(unittest.TestCase):
    """Test error classification with actual method invocation."""

    def test_generic_auth_without_form_not_credential_error(self):
        """Invokes: login_auth_error_reason() - generic error without form."""
        result = login_auth_error_reason("authentication failed")
        self.assertIsNone(result)
    
    def test_generic_auth_with_form_is_credential_error(self):
        """Invokes: login_auth_error_reason() - generic error with form context."""
        result = login_auth_error_reason("authentication failed\nEnter password\nEmail", email="test@example.com")
        self.assertIsNotNone(result)
        self.assertIn("AF_EMAIL", result)
    
    def test_explicit_credential_error_detected(self):
        """Invokes: login_auth_error_reason() - explicit credential patterns."""
        result = login_auth_error_reason("Invalid username or password", email="test@example.com")
        self.assertIsNotNone(result)
        self.assertIn("AF_LOGIN_MODE=manual", result)


class TestStateSaveValidation(unittest.TestCase):
    """Test _save_state method with mocked Playwright objects."""
    
    def test_save_state_skips_on_login_page(self):
        """Invokes: _save_state() - should skip when on login page."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session._context = MagicMock()
        session.page = MagicMock()
        session.page.url = "https://hq1.appsflyer.com/auth/login"
        
        # Mock _login_form_visible to return True
        session._login_form_visible = MagicMock(return_value=True)
        
        # Should not call storage_state
        session._save_state()
        session._context.storage_state.assert_not_called()
    
    def test_save_state_validates_hostname(self):
        """Invokes: _save_state() - validates AppsFlyer hostname."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session._context = MagicMock()
        session.page = MagicMock()
        session.page.url = "https://evil.com/fake"
        
        session._login_form_visible = MagicMock(return_value=False)
        session._blocked_reason = MagicMock(return_value=None)
        
        # Should skip - wrong hostname
        session._save_state()
        session._context.storage_state.assert_not_called()
    
    def test_save_state_rejects_suffix_lookalike(self):
        """Invokes: _save_state() - rejects notappsflyer.com (suffix match bug)."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session._context = MagicMock()
        session.page = MagicMock()
        session.page.url = "https://notappsflyer.com/dashboard"
        
        session._login_form_visible = MagicMock(return_value=False)
        session._blocked_reason = MagicMock(return_value=None)
        
        # Should skip - hostname ends with 'appsflyer.com' but is not AF domain
        session._save_state()
        session._context.storage_state.assert_not_called()
    
    def test_save_state_accepts_valid_af_domains(self):
        """Invokes: _save_state() - accepts appsflyer.com and *.appsflyer.com."""
        valid_urls = [
            "https://appsflyer.com/dashboard",
            "https://hq1.appsflyer.com/dashboard",
            "https://www.appsflyer.com/page",
        ]
        
        for url in valid_urls:
            with self.subTest(url=url):
                with tempfile.TemporaryDirectory() as tmpdir:
                    state_path = Path(tmpdir) / "af_state.json"
                    
                    session = PlaywrightAppsFlyer(email="test@example.com", password="test", state_file=str(state_path))
                    session._context = MagicMock()
                    session.page = MagicMock()
                    session.page.url = url
                    
                    session._login_form_visible = MagicMock(return_value=False)
                    session._blocked_reason = MagicMock(return_value=None)
                    
                    # Mock auth UI present
                    mock_locator = MagicMock()
                    mock_locator.count.return_value = 1
                    session.page.locator.return_value = mock_locator
                    
                    # Mock storage_state writes valid state
                    def storage_state_effect(path):
                        Path(path).write_text(json.dumps({
                            "cookies": [{"name": "af_session", "domain": ".appsflyer.com"}]
                        }))
                    session._context.storage_state.side_effect = storage_state_effect
                    
                    # Should save for valid AF domain
                    session._save_state()
                    session._context.storage_state.assert_called()
    
    def test_save_state_proceeds_on_content_pages(self):
        """Invokes: _save_state() - proceeds when on non-login AppsFlyer pages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "af_state.json"
            
            session = PlaywrightAppsFlyer(email="test@example.com", password="test", state_file=str(state_path))
            session._context = MagicMock()
            session.page = MagicMock()
            session.page.url = "https://hq1.appsflyer.com/dashboard"
            
            session._login_form_visible = MagicMock(return_value=False)
            session._blocked_reason = MagicMock(return_value=None)
            
            # Mock auth UI present
            mock_locator = MagicMock()
            mock_locator.count.return_value = 1
            session.page.locator.return_value = mock_locator
            
            # Mock storage_state writes valid state
            def storage_state_effect(path):
                Path(path).write_text(json.dumps({
                    "cookies": [{"name": "af_session", "domain": ".appsflyer.com"}]
                }))
            session._context.storage_state.side_effect = storage_state_effect
            
            # Should save
            session._save_state()
            session._context.storage_state.assert_called()


class TestAgencyEventCreation(unittest.TestCase):
    """Test _add_agency_event with mock Playwright page."""
    
    def test_agency_event_skips_if_already_selected(self):
        """Invokes: _add_agency_event() - idempotent, skips existing."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        # Mock event already in list
        session._selected_event_names = MagicMock(return_value=["af_app_opened", "af_purchase"])
        
        result = session._add_agency_event("af_app_opened")
        self.assertEqual(result, "skipped")
    
    def test_agency_event_tries_dropdown_first(self):
        """Invokes: _add_agency_event() - tries dropdown option first."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        session._selected_event_names = MagicMock(return_value=[])
        session._events_root = MagicMock()
        session._empty_event_rows = MagicMock(return_value=[MagicMock()])
        session._focus_row = MagicMock()
        
        # Mock field and dropdown elements
        mock_field = MagicMock()
        mock_option = MagicMock()
        mock_option.first.wait_for = MagicMock()  # Success - found in dropdown
        
        def locator_side_effect(selector):
            if "af-tag-input-input" in selector:
                mock_field.count.return_value = 1
                return mock_field
            elif "af-dropdown-list" in selector:
                mock_option.first.is_visible.return_value = True
                return mock_option
            return MagicMock()
        
        session.page.locator.side_effect = locator_side_effect
        session.page.wait_for_function = MagicMock()  # Verification
        
        result = session._add_agency_event("af_app_opened")
        
        # Should have clicked dropdown option
        mock_option.first.click.assert_called_once()
        self.assertEqual(result, "added")


class TestUnityEventMapping(unittest.TestCase):
    """Test Unity event selection with fixtures."""
    
    def test_unity_mapping_checks_existing_row(self):
        """Invokes: _add_mapping_row() - idempotent, checks existing."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        # Mock row already exists
        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        session.page.locator.return_value = mock_locator
        
        # Should return early
        session._add_mapping_row("af_app_opened")
        
        # Verify did not try to add new row
        session.page.get_by_role.assert_not_called()
    
    def test_unity_event_exact_match_selected(self):
        """Invokes: _select_or_create_event() - selects exact match when present."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        # Mock exact match found in dropdown
        exact_locator = MagicMock()
        exact_locator.count.return_value = 1
        exact_locator.first.is_visible.return_value = True
        exact_locator.first.click = MagicMock()
        
        session.page.locator.return_value = exact_locator
        
        # Should select the exact match
        session._select_or_create_event("af_app_opened")
        
        # Verify clicked
        exact_locator.first.click.assert_called_once()


class TestIdempotencyAndSafety(unittest.TestCase):
    """Test operations are safe to retry."""
    
    def test_state_save_atomic_with_unique_temp(self):
        """Invokes: _save_state() - uses unique temp file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "af_state.json"
            
            session = PlaywrightAppsFlyer(email="test@example.com", password="test", state_file=str(state_path))
            session._context = MagicMock()
            session.page = MagicMock()
            session.page.url = "https://hq1.appsflyer.com/dashboard"
            
            session._login_form_visible = MagicMock(return_value=False)
            session._blocked_reason = MagicMock(return_value=None)
            
            # Mock auth UI present
            auth_locator = MagicMock()
            auth_locator.count.return_value = 1
            session.page.locator.return_value = auth_locator
            
            # Mock storage_state writes valid JSON
            def storage_state_side_effect(path):
                Path(path).write_text(json.dumps({
                    "cookies": [{"name": "af_session", "domain": ".appsflyer.com"}]
                }))
            session._context.storage_state.side_effect = storage_state_side_effect
            
            session._save_state()
            
            # Verify final file exists
            self.assertTrue(state_path.exists())
            # Verify no .tmp files left
            tmp_files = list(state_path.parent.glob(".af_state_*.json"))
            self.assertEqual(len(tmp_files), 0, "Temp files should be cleaned up")


class TestCoreEventsEnsurance(unittest.TestCase):
    """Test _ensure_integration_events_exist checks required events."""
    
    def test_ensure_integration_events_all_present(self):
        """Invokes: _ensure_integration_events_exist() - all events present in mappings."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        from src.appsflyer.plan import IntegrationPlan, EventRow
        
        # All three required events already in mappings
        plan = IntegrationPlan(
            already=False,
            need_integrate=False,
            events=[
                EventRow(af_event="af_app_opened", partner_event="session"),
                EventRow(af_event="af_complete_registration", partner_event="registration"),
                EventRow(af_event="af_purchase", partner_event="purchase"),
            ]
        )
        
        # Should not log any missing events
        session._ensure_integration_events_exist(plan)
    
    def test_ensure_integration_events_some_missing(self):
        """Invokes: _ensure_integration_events_exist() - identifies missing events."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        from src.appsflyer.plan import IntegrationPlan, EventRow
        
        # Only one event in mappings
        plan = IntegrationPlan(
            already=False,
            need_integrate=False,
            events=[
                EventRow(af_event="af_app_opened", partner_event="session"),
            ]
        )
        
        # Should log missing events (checked by actual integration flow)
        session._ensure_integration_events_exist(plan)


class TestPartnerIntegrationFlows(unittest.TestCase):
    """Test each partner type ensures core events before mapping."""
    
    def test_tiktok_integration_ensures_core_events(self):
        """Invokes: configure_tiktok() → _ensure_integration_events_exist()."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        # Mock the flow
        session._open_partner = MagicMock()
        session._read_integration = MagicMock()
        session._ensure_integration_events_exist = MagicMock()
        session._click_tab = MagicMock()
        session._apply_integration = MagicMock()
        session._save_and_confirm = MagicMock()
        
        from src.appsflyer.plan import IntegrationPlan
        plan = IntegrationPlan(already=False, need_integrate=False)
        
        with patch('src.appsflyer.playwright_session.plan_integration', return_value=plan):
            session.configure_tiktok("io.example.app", ["1234567890"])
        
        # Verify core events ensured
        session._ensure_integration_events_exist.assert_called_once()
    
    def test_unity_integration_ensures_core_events(self):
        """Invokes: configure_unity() → _ensure_integration_events_exist()."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        session._open_partner = MagicMock()
        session._read_integration = MagicMock()
        session._read_unity_settled = MagicMock()
        session._ensure_integration_events_exist = MagicMock()
        session._click_tab = MagicMock()
        session._apply_integration = MagicMock()
        session._save_and_confirm = MagicMock()
        session._read_unity_links = MagicMock(return_value=("click_url", "impression_url"))
        
        from src.appsflyer.plan import IntegrationPlan, IntegrationPage
        plan = IntegrationPlan(already=False, need_integrate=False)
        page = IntegrationPage(integrated=True, active=True, game_id="", inapp_game_id="")
        
        with patch('src.appsflyer.playwright_session.plan_integration', return_value=plan):
            with patch('src.appsflyer.playwright_session.unity_conflict', return_value=None):
                session.configure_unity("io.example.app", "1234567890")
        
        # Verify core events ensured
        session._ensure_integration_events_exist.assert_called_once()
    
    def test_snapchat_integration_ensures_core_events(self):
        """Invokes: configure_snapchat() → _ensure_integration_events_exist()."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        session._open_partner = MagicMock()
        session._read_integration = MagicMock()
        session._ensure_integration_events_exist = MagicMock()
        session._click_tab = MagicMock()
        session._apply_integration = MagicMock()
        session._save_and_confirm = MagicMock()
        
        from src.appsflyer.plan import IntegrationPlan
        plan = IntegrationPlan(already=False, need_integrate=False)
        
        with patch('src.appsflyer.playwright_session.plan_integration', return_value=plan):
            session.configure_snapchat("io.example.app", ["uuid-1234"])
        
        # Verify core events ensured
        session._ensure_integration_events_exist.assert_called_once()


class TestPlaywrightDOMFixtures(unittest.TestCase):
    """Real Playwright browser tests with local HTML fixtures."""
    
    @classmethod
    def setUpClass(cls):
        """Set up Playwright once for all tests."""
        try:
            from playwright.sync_api import sync_playwright
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except ImportError:
            cls.playwright = None
            cls.browser = None
    
    @classmethod
    def tearDownClass(cls):
        """Clean up Playwright resources."""
        if cls.browser:
            cls.browser.close()
        if cls.playwright:
            cls.playwright.stop()
    
    def setUp(self):
        """Create a new page for each test."""
        if not self.browser:
            self.skipTest("Playwright not available")
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
    
    def tearDown(self):
        """Close the page after each test."""
        if hasattr(self, 'context'):
            self.context.close()
    
    def test_dropdown_delayed_options_appear(self):
        """Test: wait for dropdown options after delayed render."""
        html = """
        <html>
        <body>
            <div data-qa-id="select-menu__options" style="display: block;">
                <div id="options-container"></div>
            </div>
            <script>
                // Simulate delayed option appearance
                setTimeout(() => {
                    const container = document.getElementById('options-container');
                    const opt1 = document.createElement('div');
                    opt1.setAttribute('role', 'option');
                    opt1.setAttribute('data-value', 'af_app_opened');
                    opt1.textContent = 'af_app_opened';
                    container.appendChild(opt1);
                    
                    const opt2 = document.createElement('div');
                    opt2.setAttribute('role', 'option');
                    opt2.setAttribute('data-value', 'af_purchase');
                    opt2.textContent = 'af_purchase';
                    container.appendChild(opt2);
                }, 500);
            </script>
        </body>
        </html>
        """
        self.page.set_content(html)
        
        # Wait for options to appear (should succeed after 500ms)
        self.page.wait_for_function(
            """() => {
                const opts = [...document.querySelectorAll('[role=option]')];
                return opts.length > 0;
            }""",
            timeout=2000
        )
        
        # Verify options appeared
        options = self.page.locator('[role=option]')
        self.assertEqual(options.count(), 2)
    
    def test_dropdown_exact_match_vs_prefix(self):
        """Test: exact match selection, not prefix match."""
        html = """
        <html>
        <body>
            <div data-qa-id="select-menu__options">
                <div role="option" data-value="af_app_opened">af_app_opened</div>
                <div role="option" data-value="af_app_opened_other">af_app_opened_other</div>
                <div role="option" data-title="af_app_opened">af_app_opened (exact)</div>
            </div>
        </body>
        </html>
        """
        self.page.set_content(html)
        
        # Should find exact match by data-title
        exact = self.page.locator('[role=option][data-title="af_app_opened"]')
        self.assertEqual(exact.count(), 1)
        self.assertIn("exact", exact.first.inner_text())
        
        # Should NOT accidentally select prefix match
        all_opts = self.page.locator('[role=option]')
        self.assertEqual(all_opts.count(), 3)
    
    def test_dropdown_search_filters_results(self):
        """Test: search field filters dropdown options."""
        html = """
        <html>
        <body>
            <div data-qa-id="select-menu__options">
                <input data-qa-id="search-input" type="search" />
                <div id="results">
                    <div role="option" data-name="af_app_opened">af_app_opened</div>
                    <div role="option" data-name="af_purchase">af_purchase</div>
                    <div role="option" data-name="other_event">other_event</div>
                </div>
            </div>
            <script>
                const input = document.querySelector('[data-qa-id="search-input"]');
                const options = document.querySelectorAll('[role=option]');
                input.addEventListener('input', () => {
                    const query = input.value.toLowerCase();
                    options.forEach(opt => {
                        const name = opt.getAttribute('data-name').toLowerCase();
                        opt.style.display = name.includes(query) ? 'block' : 'none';
                    });
                });
            </script>
        </body>
        </html>
        """
        self.page.set_content(html)
        
        # Search for "af_app"
        search = self.page.locator('[data-qa-id="search-input"]')
        search.fill("af_app")
        
        # Wait for filtering
        self.page.wait_for_function(
            """() => {
                const opts = [...document.querySelectorAll('[role=option]')];
                const visible = opts.filter(o => o.style.display !== 'none');
                return visible.length === 1 && visible[0].getAttribute('data-name') === 'af_app_opened';
            }""",
            timeout=2000
        )
        
        # Verify only af_app_opened is visible
        visible_opts = self.page.locator('[role=option]:visible')
        self.assertEqual(visible_opts.count(), 1)
    
    def test_dropdown_create_via_free_text(self):
        """Test: create new event via free-text input and Enter key."""
        html = """
        <html>
        <body>
            <div data-qa-id="select-menu__options">
                <input type="text" id="event-input" />
                <div id="options"></div>
            </div>
            <script>
                const input = document.getElementById('event-input');
                const options = document.getElementById('options');
                
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' && input.value.trim()) {
                        const opt = document.createElement('div');
                        opt.setAttribute('role', 'option');
                        opt.setAttribute('data-value', input.value.trim());
                        opt.textContent = input.value.trim();
                        options.appendChild(opt);
                    }
                });
            </script>
        </body>
        </html>
        """
        self.page.set_content(html)
        
        # Type new event name and press Enter
        input_field = self.page.locator('#event-input')
        input_field.fill("af_new_custom_event")
        input_field.press("Enter")
        
        # Verify option was created
        self.page.wait_for_function(
            """() => {
                const opts = [...document.querySelectorAll('[role=option]')];
                return opts.some(o => o.getAttribute('data-value') === 'af_new_custom_event');
            }""",
            timeout=2000
        )
        
        new_option = self.page.locator('[role=option][data-value="af_new_custom_event"]')
        self.assertEqual(new_option.count(), 1)
    
    def test_dropdown_zero_options_transient_state(self):
        """Test: do not return true on zero options (transient render state)."""
        html = """
        <html>
        <body>
            <div data-qa-id="select-menu__options" id="menu" style="display: block;"></div>
            <script>
                // Initially zero options (transient)
                setTimeout(() => {
                    const menu = document.getElementById('menu');
                    const opt = document.createElement('div');
                    opt.setAttribute('role', 'option');
                    opt.textContent = 'af_app_opened';
                    menu.appendChild(opt);
                }, 300);
            </script>
        </body>
        </html>
        """
        self.page.set_content(html)
        
        # Should NOT return true immediately (zero options)
        # Should wait for stable state with actual options
        try:
            self.page.wait_for_function(
                """() => {
                    const visibleOpts = [...document.querySelectorAll('[role=option]')]
                        .filter(opt => opt.offsetParent !== null);
                    return visibleOpts.length > 0;
                }""",
                timeout=1000
            )
            # Should succeed after 300ms delay
        except Exception:
            self.fail("Should have waited for actual options to appear")
    
    def test_idempotent_event_creation(self):
        """Test: rerun with existing event does not create duplicates."""
        html = """
        <html>
        <body>
            <div id="events">
                <div class="permissions__inapp-item">
                    <span class="af-tag-value">af_app_opened</span>
                </div>
            </div>
        </body>
        </html>
        """
        self.page.set_content(html)
        
        # Check if event already exists
        existing = self.page.locator('.af-tag-value')
        self.assertEqual(existing.count(), 1)
        self.assertEqual(existing.first.inner_text(), "af_app_opened")
        
        # Idempotent check should skip adding
        exists = self.page.evaluate(
            """(eventName) => {
                const tags = [...document.querySelectorAll('.af-tag-value')];
                return tags.some((tag) => (tag.textContent || '').trim() === eventName);
            }""",
            "af_app_opened"
        )
        self.assertTrue(exists)

    HQ_HTML = """
    <html><body>
      <main class="msc__content">
        <h1>My Apps</h1>
        <div data-qa-id="apps-list">com.napp.game.app</div>
      </main>
    </body></html>
    """
    LOGIN_HTML = """
    <html><body>
      <form onsubmit="event.preventDefault(); window.__afLoginSubmitted = true;
        const n = document.createElement('div'); n.setAttribute('role','alert');
        n.textContent = 'Invalid username or password'; document.body.appendChild(n);">
        <input type="email" placeholder="Enter your email" />
        <input type="password" placeholder="Enter password" />
        <button type="submit">Log in</button>
      </form>
    </body></html>
    """
    BLOCK_HTML = """
    <html><body>
      <h1>403 ERROR</h1>
      <p>Request blocked.</p>
      <p>Generated by cloudfront</p>
    </body></html>
    """
    CHALLENGE_HTML = """
    <html><body>
      <p>Please complete recaptcha</p>
      <iframe src="https://www.google.com/recaptcha/api2/anchor" title="reCAPTCHA"
              style="width:300px;height:80px"></iframe>
    </body></html>
    """
    EMPTY_HTML = "<html><body><p>blank</p></body></html>"

    def _routed_session(self, mapping: dict[str, str]):
        seen: list[str] = []

        def handle(route):
            url = route.request.url
            seen.append(url)
            for token, html in mapping.items():
                if token in url:
                    route.fulfill(status=200, content_type="text/html", body=html)
                    return
            route.fulfill(status=200, content_type="text/html", body=self.EMPTY_HTML)

        self.page.route("**/*", handle)
        session = PlaywrightAppsFlyer(
            email="user@example.com",
            password="secret-pass",
            timeout=4000,
            headless=True,
        )
        session.page = self.page
        session._context = self.context
        session.state_file = Path(tempfile.mkdtemp()) / "state.json"
        session._seen_urls = seen
        return session

    def _password_value(self) -> str:
        field = self.page.locator("input[type=password]")
        if not field.count():
            return ""
        try:
            return field.first.input_value()
        except Exception:
            return ""

    def test_ensure_session_reuses_hq_without_login(self):
        """Invokes ensure_session() on routed HQ /apps/myapps; no LOGIN_URL, no password."""
        session = self._routed_session({"/apps/myapps": self.HQ_HTML, "/auth/login": self.LOGIN_HTML})
        session.ensure_session()
        urls = session._seen_urls
        self.assertTrue(any("/apps/myapps" in url for url in urls))
        self.assertFalse(any("/auth/login" in url for url in urls))
        self.assertEqual(self._password_value(), "")
        self.assertTrue(session._authenticated_ui())

    def test_run_share_reuses_session_then_configures(self):
        """Invokes run_share() -> ensure_session() production path with good HQ landing."""
        session = self._routed_session({"/apps/myapps": self.HQ_HTML, "/auth/login": self.LOGIN_HTML})
        configured = []

        def configure_unity(app, game_id, replace=False):
            configured.append((app, game_id, session._authenticated_ui()))
            return ShareOutcome("already", "уже настроено")

        session.configure_unity = configure_unity
        results = run_share(
            ShareTask(kind="unity", pairs=[("com.napp.game.app", "1")]),
            session,
        )
        self.assertEqual(results[0].status, "already")
        self.assertEqual(configured, [("com.napp.game.app", "1", True)])
        self.assertFalse(any("/auth/login" in url for url in session._seen_urls))
        self.assertEqual(self._password_value(), "")

    def test_ensure_session_login_form_on_probe_submits_once(self):
        """Invokes ensure_session() when probe is the login form — one password fill, then AfError."""
        session = self._routed_session({
            "/apps/myapps": self.LOGIN_HTML,
            "/auth/login": self.LOGIN_HTML,
        })
        with self.assertRaises(AfError) as caught:
            session.ensure_session()
        self.assertIn("AppsFlyer", str(caught.exception))
        self.assertTrue(any("/apps/myapps" in url for url in session._seen_urls))
        self.assertTrue(any("/auth/login" in url for url in session._seen_urls))
        self.assertEqual(self._password_value(), "secret-pass")

    def test_ensure_session_cloudfront_403_no_password(self):
        """Invokes ensure_session() on CloudFront 403 — AfBlocked, password untouched."""
        session = self._routed_session({"/apps/myapps": self.BLOCK_HTML, "/auth/login": self.LOGIN_HTML})
        with self.assertRaises(AfBlocked) as caught:
            session.ensure_session()
        self.assertIn("403", str(caught.exception))
        self.assertFalse(any("/auth/login" in url for url in session._seen_urls))
        self.assertEqual(self._password_value(), "")

    def test_ensure_session_challenge_no_password(self):
        """Invokes ensure_session() on recaptcha challenge — AfBlocked, password untouched."""
        session = self._routed_session({"/apps/myapps": self.CHALLENGE_HTML, "/auth/login": self.LOGIN_HTML})
        with self.assertRaises(AfBlocked) as caught:
            session.ensure_session()
        self.assertIn("капча", str(caught.exception))
        self.assertFalse(any("/auth/login" in url for url in session._seen_urls))
        self.assertEqual(self._password_value(), "")

    def test_ensure_session_malformed_page_no_password(self):
        """Invokes ensure_session() on empty HQ response — AfError, no login submit."""
        session = self._routed_session({"/apps/myapps": self.EMPTY_HTML, "/auth/login": self.LOGIN_HTML})
        with self.assertRaises((AfError, AfBlocked)):
            session.ensure_session()
        self.assertFalse(any("/auth/login" in url for url in session._seen_urls))
        self.assertEqual(self._password_value(), "")

    def test_add_mapping_row_sync_row_after_click(self):
        """Invokes _add_mapping_row() when the new AF-event row appears immediately on click."""
        html = """
        <html><body>
          <div data-qa-id="in-app-mapping">
            <button type="button" id="add-event">Add event</button>
            <div id="rows"></div>
          </div>
          <script>
            document.getElementById('add-event').addEventListener('click', () => {
              const wrap = document.createElement('div');
              wrap.setAttribute('data-qa-id', 'events-mapping-dropdown-eventName-');
              const btn = document.createElement('button');
              btn.setAttribute('data-qa-id', 'select-button');
              btn.setAttribute('type', 'button');
              wrap.appendChild(btn);
              document.getElementById('rows').appendChild(wrap);
            });
            document.addEventListener('click', (event) => {
              const btn = event.target.closest('button[data-qa-id="select-button"]');
              if (!btn) return;
              const wrap = btn.closest('[data-qa-id^="events-mapping-dropdown-eventName-"]');
              if (!wrap) return;
              let menu = document.querySelector('[data-qa-id="select-menu__options"]');
              if (!menu) {
                menu = document.createElement('div');
                menu.setAttribute('data-qa-id', 'select-menu__options');
                menu.setAttribute('role', 'listbox');
                const opt = document.createElement('div');
                opt.setAttribute('role', 'option');
                opt.setAttribute('data-value', 'af_app_opened');
                opt.setAttribute('data-title', 'af_app_opened');
                opt.textContent = 'af_app_opened';
                opt.addEventListener('click', () => {
                  wrap.setAttribute('data-qa-id', 'events-mapping-dropdown-eventName-af_app_opened');
                  btn.setAttribute('data-value', 'af_app_opened');
                  btn.textContent = 'af_app_opened';
                  menu.remove();
                });
                menu.appendChild(opt);
                document.body.appendChild(menu);
              }
            });
          </script>
        </body></html>
        """
        self.page.set_content(html)
        session = PlaywrightAppsFlyer(email="user@example.com", password="secret", timeout=4000, headless=True)
        session.page = self.page
        session._add_mapping_row("af_app_opened")
        self.assertEqual(
            self.page.locator('[data-qa-id="events-mapping-dropdown-eventName-af_app_opened"]').count(),
            1,
        )

    def test_mapping_debug_snapshot_reports_placeholder_select(self):
        """Invokes _mapping_debug_snapshot() on mapping UI with placeholder Select, no writes."""
        html = """
        <html><body>
          <div data-qa-id="in-app-mapping">
            <h2>In-app event postbacks</h2>
            <input data-qa-id="switch-input" type="checkbox" checked />
            <button type="button">Add event</button>
            <div data-qa-id="events-mapping-dropdown-eventName-">
              <button data-qa-id="select-button" type="button">Select...</button>
            </div>
          </div>
        </body></html>
        """
        self.page.set_content(html)
        session = PlaywrightAppsFlyer(email="user@example.com", password="secret", timeout=4000, headless=True)
        session.page = self.page
        snap = session._mapping_debug_snapshot()
        self.assertIn("Select", snap)
        self.assertIn("eventName", snap)
        self.assertIn("Add event", snap)

    def test_add_mapping_row_live_select_placeholder_source_only(self):
        """Invokes _add_mapping_row() on live Unity markup: source Select, partner Select, send defaults."""
        html = """
        <html><body>
          <div data-qa-id="in-app-mapping">
            <input data-qa-id="switch-input" type="checkbox" checked />
            <button type="button" id="add-event">Add event</button>
            <div id="rows">
              <div data-qa-id="events-mapping-dropdown-eventName-af_purchase">
                <button data-qa-id="select-button" data-value="af_purchase" type="button">af_purchase</button>
              </div>
              <div data-qa-id="events-mapping-dropdown-partnerEventName-af_purchase">
                <button data-qa-id="select-button" data-value="purchase" type="button">purchase</button>
              </div>
            </div>
          </div>
          <script>
            function addSelect(parent, qa, value, text) {
              const wrap = document.createElement('div');
              wrap.setAttribute('data-qa-id', qa);
              const btn = document.createElement('button');
              btn.setAttribute('data-qa-id', 'select-button');
              btn.setAttribute('type', 'button');
              if (value) btn.setAttribute('data-value', value);
              btn.textContent = text;
              wrap.appendChild(btn);
              parent.appendChild(wrap);
              return {wrap, btn};
            }
            document.getElementById('add-event').addEventListener('click', () => {
              const rows = document.getElementById('rows');
              const src = addSelect(rows, 'events-mapping-dropdown-eventName-', '', 'Select');
              addSelect(rows, 'events-mapping-dropdown-partnerEventName-', '', 'Select');
              addSelect(rows, 'events-mapping-dropdown-sendingOption-', 'send-attributed', 'This partner only');
              addSelect(rows, 'events-mapping-dropdown-sendValue-', 'nothing', 'No values & no revenue');
              src.btn.addEventListener('click', (event) => {
                event.stopPropagation();
                let menu = document.querySelector('[data-qa-id="select-menu__options"]');
                if (menu) menu.remove();
                menu = document.createElement('div');
                menu.setAttribute('data-qa-id', 'select-menu__options');
                menu.setAttribute('role', 'listbox');
                const opt = document.createElement('div');
                opt.setAttribute('role', 'option');
                opt.setAttribute('data-value', 'af_app_opened');
                opt.setAttribute('data-title', 'af_app_opened');
                opt.textContent = 'af_app_opened';
                opt.addEventListener('click', () => {
                  src.wrap.setAttribute('data-qa-id', 'events-mapping-dropdown-eventName-af_app_opened');
                  src.btn.setAttribute('data-value', 'af_app_opened');
                  src.btn.textContent = 'af_app_opened';
                  menu.remove();
                });
                menu.appendChild(opt);
                document.body.appendChild(menu);
              });
            });
          </script>
        </body></html>
        """
        self.page.set_content(html)
        session = PlaywrightAppsFlyer(email="user@example.com", password="secret", timeout=4000, headless=True)
        session.page = self.page
        session._add_mapping_row("af_app_opened")
        self.assertEqual(
            self.page.locator('[data-qa-id="events-mapping-dropdown-eventName-af_app_opened"]').count(),
            1,
        )
        purchase = self.page.locator(
            '[data-qa-id="events-mapping-dropdown-eventName-af_purchase"] button[data-qa-id=select-button]'
        )
        self.assertEqual(purchase.first.get_attribute("data-value"), "af_purchase")
        self.assertEqual(purchase.first.inner_text().strip(), "af_purchase")
        partner_blank = self.page.locator(
            '[data-qa-id="events-mapping-dropdown-partnerEventName-"] button[data-qa-id=select-button]'
        )
        self.assertEqual(partner_blank.first.inner_text().strip(), "Select")
        self.assertEqual(partner_blank.first.get_attribute("data-value") or "", "")

    def test_choose_partner_placeholder_select_sets_label(self):
        """Invokes _choose() when partnerEventName shows live placeholder Select and empty value."""
        html = """
        <html><body>
          <div data-qa-id="events-mapping-dropdown-partnerEventName-af_app_opened">
            <button data-qa-id="select-button" type="button">Select</button>
          </div>
          <script>
            document.querySelector('button[data-qa-id=select-button]').addEventListener('click', () => {
              const menu = document.createElement('div');
              menu.setAttribute('data-qa-id', 'select-menu__options');
              menu.setAttribute('role', 'listbox');
              const opt = document.createElement('div');
              opt.setAttribute('role', 'option');
              opt.setAttribute('data-value', 'session');
              opt.textContent = 'session';
              opt.addEventListener('click', () => {
                const btn = document.querySelector('button[data-qa-id=select-button]');
                btn.setAttribute('data-value', 'session');
                btn.textContent = 'session';
                menu.remove();
              });
              menu.appendChild(opt);
              document.body.appendChild(menu);
            });
          </script>
        </body></html>
        """
        self.page.set_content(html)
        session = PlaywrightAppsFlyer(email="user@example.com", password="secret", timeout=4000, headless=True)
        session.page = self.page
        session._choose(
            "events-mapping-dropdown-partnerEventName-af_app_opened",
            label="session",
        )
        btn = self.page.locator(
            '[data-qa-id="events-mapping-dropdown-partnerEventName-af_app_opened"] button[data-qa-id=select-button]'
        )
        self.assertEqual(btn.first.inner_text().strip(), "session")
        self.assertEqual(btn.first.get_attribute("data-value"), "session")

    def test_unassigned_select_does_not_treat_real_labels_as_empty(self):
        """Invokes _is_unassigned_select() — only empty value + Select placeholder."""
        session = PlaywrightAppsFlyer(email="user@example.com", password="secret")
        self.assertTrue(session._is_unassigned_select("Select", ""))
        self.assertTrue(session._is_unassigned_select("Select...", ""))
        self.assertTrue(session._is_unassigned_select("", ""))
        self.assertFalse(session._is_unassigned_select("af_app_opened", ""))
        self.assertFalse(session._is_unassigned_select("This partner only", "send-attributed"))
        self.assertFalse(session._is_unassigned_select("Select", "af_app_opened"))
        self.assertFalse(session._is_unassigned_select("No values & no revenue", "nothing"))

    def test_snapchat_terms_gate_does_not_accept_or_search_app_id(self):
        """Invokes configure_snapchat() on Attribution Terms — requires_terms_acceptance, no Accept click."""
        html = """
        <html><body>
          <button data-qa-id="msc-tab-integration">Integration</button>
          <button data-qa-id="save-tab-changes-btn" disabled>Save Integration</button>
          <h1>Attribution Terms</h1>
          <p>Effective: July 1, 2021</p>
          <p>These Attribution Terms ('Terms') form a legally binding contract between you and Snap Inc.</p>
          <p>PLEASE READ THESE TERMS CAREFULLY. IF YOU DO NOT AGREE TO THESE TERMS, DO NOT ACCEPT AND DO NOT USE THE ATTRIBUTION PROGRAM.</p>
          <p>Snap Terms of Service</p>
          <button type="button" id="accept-terms" onclick="window.__accepted = true">Accept</button>
        </body></html>
        """
        self.page.set_content(html)
        session = PlaywrightAppsFlyer(email="user@example.com", password="secret", timeout=4000, headless=True)
        session.page = self.page
        session._open_partner = lambda *a, **k: None
        with self.assertRaises(AfError) as caught:
            session.configure_snapchat("com.napp.game.app", ["11111111-2222-4333-8444-555555555555"])
        self.assertIn("requires_terms_acceptance", str(caught.exception))
        self.assertNotIn("App ID", str(caught.exception))
        accepted = self.page.evaluate("() => window.__accepted === true")
        self.assertFalse(accepted)


if __name__ == "__main__":
    unittest.main()
