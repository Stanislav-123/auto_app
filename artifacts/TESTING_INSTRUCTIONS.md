# Testing Instructions - PR #29 Integration Event Creation

## Overview

This package contains modified source files for testing AppsFlyer integration event creation fixes on Windows WITHOUT overwriting your production checkout.

## What's Included

- Modified source files: `src/appsflyer/playwright_session.py`, `src/appsflyer/session.py`
- Tests: `tests/test_af_headless_unity.py`, `tests/test_af_share.py`
- Discovery runner: `artifacts/discover_test_app.py`
- This instructions file

## Setup - Windows Overlay Testing

### 1. Extract to Temporary Test Directory

```powershell
# Extract ZIP to a NEW directory (NOT your production repo)
cd C:\Temp
mkdir af_pr29_test
cd af_pr29_test
# Extract af_pr29_test.zip here
```

### 2. Install Dependencies (if not already in production .venv)

```powershell
# Activate your production .venv OR create new one
C:\path\to\production\auto_app\.venv\Scripts\activate

# OR create fresh venv for testing
python -m venv .venv
.venv\Scripts\activate
pip install playwright python-telegram-bot python-dotenv
playwright install chromium
playwright install-deps chromium
```

### 3. Prevent PYTHONPATH Conflicts

**IMPORTANT:** Do NOT set `PYTHONPATH` environment variable. The scripts use explicit base-path arguments to load production .env while using test source files.

```powershell
# Make sure PYTHONPATH is NOT set
Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
```

## Step 1: Discover Test App (Read-Only)

This step inspects your AppsFlyer account WITHOUT making changes.

```powershell
python artifacts/discover_test_app.py --base-path C:\path\to\production\auto_app
```

**What it does:**
- Loads AF_EMAIL/AF_PASSWORD from production .env
- Opens system Chrome (headed, visible browser)
- Uses existing session from data/af_state.json
- Lists apps in your account
- Inspects Unity/TikTok/Snapchat status (read-only)
- Reports first app with all three unconfigured
- Writes sanitized result to `artifacts/discovery_result.json`

**To inspect specific app:**
```powershell
python artifacts/discover_test_app.py --base-path C:\path\to\production\auto_app --app io.example.testapp
```

**Output:** `artifacts/discovery_result.json` with:
```json
{
  "candidate_app": "io.example.testapp",
  "unity_status": {"integrated": false, "active": false},
  "tiktok_status": {"integrated": false, "active": false},
  "snapchat_status": {"integrated": false, "active": false},
  "all_unconfigured": true
}
```

**ANNOUNCE the candidate app to parent BEFORE proceeding to live test.**

## Step 2: Run Tests (Verify Code)

```powershell
# Run all tests (67 total, including 6 Playwright DOM fixtures)
python -m unittest tests.test_af_headless_unity tests.test_af_share -v

# Should show:
# Ran 67 tests in ~2s
# OK
```

## Step 3: Live Integration Test (After Parent Approval)

**WAIT for parent to:**
1. Review discovery_result.json
2. Confirm test app
3. Provide test IDs: Unity Game ID, TikTok App ID, Snapchat App ID

**Then run integration test:**

```powershell
# Set test IDs provided by parent
$env:TEST_UNITY_GAME_ID = "1234567"
$env:TEST_TIKTOK_APP_ID = "7234567890123456789"
$env:TEST_SNAPCHAT_APP_ID = "uuid-1234-5678"
$env:TEST_APP = "io.example.testapp"  # From discovery

# Run live integration test using production .env
python artifacts/run_live_test.py --base-path C:\path\to\production\auto_app
```

**What it does:**
- Uses test source files (this directory)
- Loads credentials from production .env (--base-path)
- Uses production state file (no mixing)
- Configures Unity/TikTok/Snapchat with provided IDs
- Reports success/failure with screenshots on error
- Logs in `logs/` directory

**After test:** Verify in AppsFlyer UI:
- Unity: Game ID set, events mapped (session/level_complete/purchase)
- TikTok: App ID set, events mapped (LaunchAPP/Registration/Purchase)
- Snapchat: App ID set, events mapped (APP_OPEN/SIGN_UP/PURCHASE)
- All events: af_app_opened, af_complete_registration, af_purchase exist
- All mappings: All media sources, Values & revenue

## Safety Features

1. **No production file overwrite**: Test files stay in temp directory
2. **Base-path argument**: Loads .env without PYTHONPATH mixing
3. **Read-only discovery**: Inspects without mutations
4. **Explicit test app**: Parent confirms before writes
5. **Screenshots on error**: Logs go to `logs/` in test directory

## Rollback

Since production checkout is untouched:
```powershell
# Just delete test directory when done
cd ..
Remove-Item -Recurse -Force af_pr29_test
```

## Common Issues

**"No module named src"**
- Don't use `PYTHONPATH`
- Use `--base-path` argument instead
- Scripts handle imports correctly with base-path

**"Could not load .env"**
- Check --base-path points to production repo root
- Verify .env exists there with AF_EMAIL, AF_PASSWORD

**"CloudFront 403"**
- Observed on test setup
- Use AF_HEADLESS=0 (scripts do this automatically)
- Check data/af_state.json has valid session

**"Browser not found"**
- Run `playwright install chromium`
- Run `playwright install-deps chromium` (Linux/CI only)
- On Windows, system Chrome is preferred

## Test Counts

- **test_af_headless_unity.py**: 25 tests (includes hostname validation)
- **test_af_share.py**: 45 tests
- **Total**: 70 tests, all should PASS

## What Was Fixed

1. **Event creation in mapping dropdown** (not Permissions tab)
2. **Fresh row targeting** (eventName parent check, not buttons.last)
3. **Exact event matching** (no prefix accidents)
4. **State save preservation** (skips on login/blocked/wrong domain)
5. **Hostname validation** (rejects notappsflyer.com)
6. **CloudFront 403 detection** (clear messaging)

## What Remains Unsolved

- **Headless mode**: Observed CloudFront 403 blocks on test setup
- **Workaround**: Use AF_HEADLESS=0 (headed/visible browser)
