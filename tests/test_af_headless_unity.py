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
from src.appsflyer.session import login_auth_error_reason, AfError


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
        """Invokes: _select_or_create_unity_event() - selects exact match when present."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        # Mock exact match found in dropdown
        exact_locator = MagicMock()
        exact_locator.count.return_value = 1
        exact_locator.first.is_visible.return_value = True
        exact_locator.first.click = MagicMock()
        
        session.page.locator.return_value = exact_locator
        
        # Should select the exact match
        session._select_or_create_unity_event("af_app_opened")
        
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
    """Test _ensure_core_af_events_exist for all partner types."""
    
    def test_ensure_core_events_checks_existing(self):
        """Invokes: _ensure_core_af_events_exist() - checks existing events."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        # Mock navigation
        session._open_partner = MagicMock()
        session._click_tab = MagicMock()
        
        # Mock all three events already present
        session._selected_event_names = MagicMock(return_value=[
            "af_app_opened", "af_complete_registration", "af_purchase", "other_event"
        ])
        
        # Should not try to add any events
        session._add_agency_event = MagicMock()
        session._save_and_confirm = MagicMock()
        
        session._ensure_core_af_events_exist("io.example.app", "partner_id")
        
        # Verify no events added
        session._add_agency_event.assert_not_called()
        session._save_and_confirm.assert_not_called()
    
    def test_ensure_core_events_creates_missing(self):
        """Invokes: _ensure_core_af_events_exist() - creates missing events."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        session._open_partner = MagicMock()
        session._click_tab = MagicMock()
        
        # Mock only one event present
        session._selected_event_names = MagicMock(return_value=["af_app_opened"])
        session._add_agency_event = MagicMock(return_value="added")
        session._save_and_confirm = MagicMock()
        
        session._ensure_core_af_events_exist("io.example.app", "partner_id")
        
        # Should create two missing events
        calls = [call[0][0] for call in session._add_agency_event.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertIn("af_complete_registration", calls)
        self.assertIn("af_purchase", calls)
        
        # Should save after adding
        session._save_and_confirm.assert_called_once()


class TestPartnerIntegrationFlows(unittest.TestCase):
    """Test each partner type ensures core events before mapping."""
    
    def test_tiktok_integration_ensures_core_events(self):
        """Invokes: configure_tiktok() → _ensure_core_af_events_exist()."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        # Mock the flow
        session._open_partner = MagicMock()
        session._read_integration = MagicMock()
        session._ensure_core_af_events_exist = MagicMock()
        session._click_tab = MagicMock()
        session._apply_integration = MagicMock()
        session._save_and_confirm = MagicMock()
        
        from src.appsflyer.plan import IntegrationPlan
        plan = IntegrationPlan(already=False, need_integrate=False)
        
        with patch('src.appsflyer.playwright_session.plan_integration', return_value=plan):
            session.configure_tiktok("io.example.app", ["1234567890"])
        
        # Verify core events ensured
        session._ensure_core_af_events_exist.assert_called_once()
    
    def test_unity_integration_ensures_core_events(self):
        """Invokes: configure_unity() → _ensure_core_af_events_exist()."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        session._open_partner = MagicMock()
        session._read_integration = MagicMock()
        session._read_unity_settled = MagicMock()
        session._ensure_core_af_events_exist = MagicMock()
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
        session._ensure_core_af_events_exist.assert_called_once()
    
    def test_snapchat_integration_ensures_core_events(self):
        """Invokes: configure_snapchat() → _ensure_core_af_events_exist()."""
        session = PlaywrightAppsFlyer(email="test@example.com", password="test")
        session.page = MagicMock()
        
        session._open_partner = MagicMock()
        session._read_integration = MagicMock()
        session._ensure_core_af_events_exist = MagicMock()
        session._click_tab = MagicMock()
        session._apply_integration = MagicMock()
        session._save_and_confirm = MagicMock()
        
        from src.appsflyer.plan import IntegrationPlan
        plan = IntegrationPlan(already=False, need_integrate=False)
        
        with patch('src.appsflyer.playwright_session.plan_integration', return_value=plan):
            session.configure_snapchat("io.example.app", ["uuid-1234"])
        
        # Verify core events ensured
        session._ensure_core_af_events_exist.assert_called_once()


if __name__ == "__main__":
    unittest.main()
