# AppsFlyer Automation Fixes

## Issues Fixed

### 1. Headless Mode Login Failure (Issue #1)

**Problem:**
- `AF_HEADLESS=1` (headless mode) failed with "invalid email/password" error
- `AF_HEADLESS=0` (visible browser) worked with identical credentials
- False diagnosis: credentials were blamed when they were actually correct

**Root Cause:**
- Generic "authentication failed" messages were incorrectly interpreted as credential errors
- Headless browsers may trigger different server responses or timing issues
- Error detection was too aggressive, not distinguishing actual credential errors from other failures
- Insufficient wait times after form submission in headless mode

**Fixes Applied:**

1. **Improved Error Detection** (`src/appsflyer/session.py`):
   - Generic "authentication failed" now requires login form context to be reported
   - Explicit credential error patterns (e.g., "invalid username or password") still caught
   - Error messages now suggest `AF_LOGIN_MODE=manual` as primary workaround
   - Added `AF_EMAIL` reference in error messages for debugging

2. **Enhanced Headless Compatibility** (`src/appsflyer/playwright_session.py`):
   - Added human-like delays between field fills in headless mode (100-200ms)
   - Increased wait time after login submission in headless (1500ms vs 800ms)
   - Added double-check mechanism: if error detected in headless, wait 1s and re-verify
   - All waits guarded with `hasattr()` checks for test compatibility

3. **Better Session State Management**:
   - Improved logging when session state is loaded/missing
   - Skip saving state when on login page (failed login)
   - Skip saving state when blocked by captcha/2FA
   - Prevents overwriting good session state with failed attempts

**Testing:**
```bash
# Verify login with saved session (headless)
AF_HEADLESS=1 python3 scripts/af_login.py

# If first login needed, use manual mode once
AF_HEADLESS=0 AF_LOGIN_MODE=manual python3 scripts/af_login.py
```

### 2. Unity Event Selection Failure (Issue #2)

**Problem:**
- Unity integration for `coj.jemuset.mysticster` failed with error: "Не нашёл пункт af_app_opened"
- Event was manually visible in UI afterward, suggesting timing/rendering issue
- Dropdown selection happened before items were fully loaded

**Root Cause:**
- After clicking dropdown button, code immediately tried to select event
- For dropdowns with many items, rendering and population takes time
- Search functionality wasn't used proactively for reliability
- No explicit waits for dropdown menu to appear and populate

**Fixes Applied:**

1. **Dropdown Rendering Waits** (`src/appsflyer/playwright_session.py`):
   - Added explicit wait for dropdown to become visible (3s timeout)
   - Added 300ms delay after dropdown opens for items to populate
   - Better fallback detection for different dropdown types

2. **Proactive Search Usage**:
   - Search functionality now used FIRST for event selection (for long lists)
   - Improved `_pick_menu_option` to handle both AppsFlyer menu types
   - Extended wait mode (800ms vs 500ms) for stubborn dropdowns
   - Search input fills target event name to filter results

3. **Enhanced Event Mapping** (`_add_mapping_row`):
   - Added 400ms wait after clicking "Add event" button
   - Wait for new row buttons to be visible (3s timeout)
   - Scroll target dropdown into view before clicking
   - Additional 300ms wait after dropdown click before selection

4. **Better Error Messages**:
   - Changed from generic "Не нашёл пункт X" to "Не нашёл пункт X. Проверьте, что событие существует в AppsFlyer."
   - Distinguishes between dropdown not opening vs event not in list

**Event Mapping (Unity):**
- `af_app_opened` → `session`
- `af_complete_registration` → `level_complete`  
- `af_purchase` → `purchase`
- All sources including organic
- Values & revenue enabled
- 6-month attribution window (default)

**Testing:**
```bash
# Test Unity integration with proper event selection
python3 -m unittest tests.test_af_headless_unity.TestUnityEventSelection -v
```

## Verification

All existing tests pass:
```bash
python3 -m unittest tests.test_af_share -v  # 55 tests pass
python3 -m unittest tests.test_af_headless_unity -v  # 7 new tests pass
```

## Usage Recommendations

### For Automatic Background Operation (Headless)

1. **First Time Setup (Manual Login):**
   ```bash
   AF_HEADLESS=0 AF_LOGIN_MODE=manual python3 scripts/af_login.py
   ```
   - Opens visible browser
   - Login manually (Google SSO or email/password)
   - Session saved to `data/af_state.json`

2. **Subsequent Runs (Automatic):**
   ```bash
   AF_HEADLESS=1 python3 scripts/af_login.py
   # Or just use bot normally - session will be reused
   ```
   - Uses saved session from `data/af_state.json`
   - No visible browser window
   - No repeated login

### Session Lifecycle

- **Saved State:** `data/af_state.json` (or `AF_STATE_PATH` env var)
- **Lifetime:** Until AppsFlyer expires the session (typically weeks/months)
- **Validation:** Account matches `AF_EMAIL` from config
- **Regeneration:** Delete `data/af_state.json` and run manual login again

### Configuration

```env
# .env file
AF_EMAIL=your.email@example.com
AF_PASSWORD=your_password_if_using_automatic_login

# Login mode: "password" (default) or "manual"
AF_LOGIN_MODE=password

# Browser visibility: "1" = headless (default), "0" = visible
AF_HEADLESS=1

# Session storage path
AF_STATE_PATH=data/af_state.json

# Timeout for operations (milliseconds)
AF_TIMEOUT_MS=45000
```

## Known Limitations

1. **Live Verification:** These fixes require live AppsFlyer access to fully verify. Testing without credentials validates structure but not end-to-end behavior.

2. **Browser Automation:** Uses supported Playwright automation. Does not bypass or circumvent AppsFlyer security. If AppsFlyer blocks automation, manual login with saved session is the recommended workaround.

3. **Event Creation:** Assumes standard AppsFlyer events (`af_app_opened`, etc.) exist in the account. Does not create new custom events - only maps existing ones.

4. **Unity Game ID Conflicts:** If Unity integration exists with different Game ID, warns rather than silently replacing (safety feature).

## Troubleshooting

### Headless Login Still Fails

1. Try manual login once:
   ```bash
   AF_HEADLESS=0 AF_LOGIN_MODE=manual python3 scripts/af_login.py
   ```

2. Check session file exists and is recent:
   ```bash
   ls -lh data/af_state.json
   ```

3. Verify account in session matches AF_EMAIL

4. Check logs for `af_state_loaded` confirmation

### Unity Event Not Found

1. Verify event exists in AppsFlyer account
2. Check screenshot in `logs/af-unity-*.png` for visual confirmation
3. Ensure proper event name (case-sensitive): `af_app_opened`, not `AF_APP_OPENED`
4. Review logs for `af_dropdown_search` and `af_select_event_dropdown` entries

### Session State Not Preserved

1. Check that login succeeds (not stuck on login page)
2. Verify not blocked by 2FA/captcha (would skip state save)
3. Look for `af_state_saved` in logs vs `af_state_skip_save`
4. Ensure `data/` directory is writable
