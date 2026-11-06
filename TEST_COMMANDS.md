# Testing Commands for AppsFlyer Fixes

## Quick Test Summary

All automated tests pass:
```bash
# Run existing AF share tests (55 tests)
python3 -m unittest tests.test_af_share -v

# Run new headless/Unity tests (7 tests)
python3 -m unittest tests.test_af_headless_unity -v

# Run all tests
python3 -m unittest discover tests -v
```

## Manual Testing (Requires Live AppsFlyer Access)

### Test 1: Headless Login with Session Reuse

**Setup - First Time (Manual Login):**
```bash
# Set environment variables
export AF_EMAIL="your.email@appsflyer.com"
export AF_PASSWORD="your_password"  # Only if using password login
export AF_HEADLESS=0
export AF_LOGIN_MODE=manual

# Run manual login (opens visible browser)
python3 scripts/af_login.py
```

**Expected Result:**
- Browser opens visibly
- You log in manually (Google SSO or email/password)
- After successful login, browser closes
- Session saved to `data/af_state.json`
- Message: "Готово. Сессия: data/af_state.json"

**Test - Headless Mode with Saved Session:**
```bash
# Now test headless mode
export AF_HEADLESS=1
export AF_LOGIN_MODE=password

# Run headless (should use saved session)
python3 scripts/af_login.py
```

**Expected Result:**
- No visible browser window
- Quick execution (no login form interaction)
- Log shows: `af_state_loaded path=data/af_state.json`
- Success message with AppsFlyer URL
- No "invalid email/password" errors

**Test - Headless Mode WITHOUT Saved Session:**
```bash
# Remove saved session to test fresh login
rm data/af_state.json

# Try headless login (now with improvements)
export AF_HEADLESS=1
export AF_LOGIN_MODE=password
python3 scripts/af_login.py
```

**Expected Result:**
- Headless browser attempts login
- If credentials are correct and no 2FA/captcha:
  - Should succeed and save new session
  - Log shows `af_state_saved path=data/af_state.json`
- If it fails with authentication error:
  - Error message suggests `AF_LOGIN_MODE=manual` workaround
  - Existing good state not overwritten

### Test 2: Unity Event Selection

**Prerequisites:**
- AF_EMAIL and AF_PASSWORD (or saved session in data/af_state.json)
- AppsFlyer app ID (Android bundle or iOS id)
- Unity Ads Game ID

**Test Command:**
```bash
export AF_EMAIL="your.email@appsflyer.com"
export AF_HEADLESS=1  # Can test headless now

# Test Unity integration via Python
python3 -c "
from src.appsflyer.session import ShareTask, execute_share

task = ShareTask(
    kind='unity',
    pairs=[('io.example.app', '1234567890')]  # Replace with real app and game ID
)

try:
    results = execute_share(task)
    for result in results:
        print(f'{result.app}: {result.status} - {result.reason}')
        if result.links:
            print(f'  Click: {result.links[0]}')
            print(f'  Impression: {result.links[1]}')
except Exception as e:
    print(f'Error: {e}')
"
```

**Expected Result:**
- No "Не нашёл пункт af_app_opened" errors
- Events `af_app_opened`, `af_complete_registration`, `af_purchase` mapped successfully
- Logs show:
  - `af_add_event_row event=af_app_opened`
  - `af_select_event_dropdown event=af_app_opened`
  - Possibly `af_dropdown_search label=af_app_opened`
- Success or "already" status (if already configured)
- Click and impression URLs returned

**Alternative Test via Bot Command:**
```bash
# If using Telegram bot
/share_unity 1234567890 io.example.app
```

**Expected Result:**
- Bot processes Unity integration
- Returns attribution links or "уже настроено" (already configured)
- No event selection errors in logs

### Test 3: Session State Preservation

**Test - Failed Login Doesn't Overwrite Good State:**
```bash
# Create a good session first
export AF_HEADLESS=0
export AF_LOGIN_MODE=manual
python3 scripts/af_login.py
# (Login successfully, session saved)

# Now simulate a failure with wrong password
cp data/af_state.json data/af_state_backup.json
export AF_HEADLESS=1
export AF_LOGIN_MODE=password
export AF_PASSWORD="wrong_password"

python3 scripts/af_login.py
# This will fail

# Verify state not overwritten
diff data/af_state.json data/af_state_backup.json
```

**Expected Result:**
- Login fails with authentication error
- Log shows: `af_state_skip_save reason=login_page_visible`
- `data/af_state.json` unchanged (good state preserved)
- Files are identical in diff

## Verification Checklist

### Headless Login
- [ ] Manual login works and saves session (`AF_LOGIN_MODE=manual`)
- [ ] Headless mode uses saved session without re-login
- [ ] Headless mode with fresh login works (if no 2FA/captcha)
- [ ] Error messages suggest manual mode workaround
- [ ] Failed login doesn't overwrite good session state
- [ ] Logs show `af_state_loaded` when session used
- [ ] Logs show `af_state_saved` when session updated

### Unity Event Selection
- [ ] Unity integration completes without "Не нашёл пункт" errors
- [ ] All three events mapped: af_app_opened, af_complete_registration, af_purchase
- [ ] Dropdown waits and search functionality work
- [ ] Click and impression URLs returned
- [ ] Logs show event selection steps
- [ ] Works with both fresh integration and updates

### Error Handling
- [ ] Credential errors explicitly mention AF_EMAIL and AF_PASSWORD
- [ ] Generic errors suggest AF_LOGIN_MODE=manual
- [ ] 2FA/captcha detected and reported (not blamed on credentials)
- [ ] Missing events get clear error message with verification suggestion

## Troubleshooting

### Headless Still Fails
1. Check if `data/af_state.json` exists and is recent
2. Try deleting it and using manual mode once
3. Verify AF_EMAIL matches the account in saved session
4. Check for 2FA or captcha requirements on account

### Unity Events Not Found
1. Check screenshot in `logs/af-unity-*.png`
2. Verify events exist in AppsFlyer account
3. Look for `af_dropdown_search` in logs
4. Ensure proper event names (case-sensitive)

### Session Not Saved
1. Verify login actually succeeds (not stuck on login page)
2. Check for 2FA/captcha blocking (state save skipped)
3. Look for `af_state_skip_save` vs `af_state_saved` in logs
4. Ensure `data/` directory is writable

## Log Analysis

**Key log entries to look for:**

**Session State:**
- `af_state_loaded path=data/af_state.json` - Good session reused
- `af_state_not_found` - No saved session, will login
- `af_state_saved` - Session saved successfully
- `af_state_skip_save reason=login_page_visible` - Failed login, state preserved
- `af_state_skip_save reason=blocked` - 2FA/captcha, state preserved

**Headless Login:**
- `af_login mode=password` - Automatic login
- `af_login mode=manual` - Manual login mode
- `af_login_checking_headless_auth_delay` - Extra verification in headless
- `af_browser executable=/path/to/chrome` - Using system browser
- `af_browser fallback=chromium` - Using bundled Chromium

**Unity Events:**
- `af_add_event_row event=af_app_opened` - Adding event row
- `af_select_event_dropdown event=af_app_opened` - Clicked dropdown
- `af_dropdown_search label=af_app_opened` - Using search
- `af_dropdown_wait_timeout` - Dropdown rendering took time (non-fatal)

## Environment Variables Reference

```bash
# AppsFlyer Authentication
AF_EMAIL=your.email@example.com
AF_PASSWORD=your_password  # Optional if using manual mode

# Login Configuration
AF_LOGIN_MODE=password     # "password" or "manual"
AF_HEADLESS=1              # "1" = headless, "0" = visible

# Session Management
AF_STATE_PATH=data/af_state.json  # Where to save/load session

# Timeouts (milliseconds)
AF_TIMEOUT_MS=45000

# Browser (optional)
AF_BROWSER_CHANNEL=chrome  # chrome, msedge, chromium
AF_BROWSER_PATH=/path/to/browser  # Explicit path if needed
```

## Success Criteria

**Headless Login:**
✅ No false "invalid email/password" errors with correct credentials  
✅ Saved session reused automatically in headless mode  
✅ Manual mode works as fallback when needed  
✅ Good session state preserved on failures  

**Unity Integration:**
✅ All three standard events mapped without errors  
✅ Dropdowns render and populate properly  
✅ Attribution links returned  
✅ Idempotent (safe to run multiple times)  

**Overall:**
✅ All automated tests pass  
✅ Clear error messages guide users to solutions  
✅ No breaking changes to existing functionality  
