# Autonix and Deriv Authentication - Testing & Debugging Guide

## Overview

Autonix website identity and Deriv trading authorization are separate systems.
Autonix users are stored in `data/autonix_users.sqlite3`; Deriv OAuth 2.0 + PKCE
continues to own trading authorization and its existing token session.

## Autonix Account Flow

```
1. Visitor registers with email/password or Google at /register
   ↓
2. Autonix stores the website identity and starts an Autonix session
   ↓
3. Autonix redirects to the protected /dashboard
   ↓
4. User can access their profile without a Deriv account
   ↓
5. Connect Deriv Account invokes the existing /auth/login flow
   ↓
6. After a successful Deriv account session, verified account IDs are linked
   to the current Autonix user in the separate Autonix database
```

Deriv token exchange, account selection, token storage, WebSocket authorization,
and trading remain in the existing Deriv implementation. Autonix logout clears
website identity only; Deriv logout revokes and clears Deriv state while retaining
an active Autonix session.

## Key Routes

- `/register`, `/login`: Autonix email/password authentication.
- `/auth/google`, `/auth/google/callback`: Autonix Google OAuth.
- `/password/forgot`, `/password/reset/<token>`: Autonix password recovery.
- `/account/profile`, `/account/logout`: Autonix profile and website logout.
- `/dashboard`: Requires an active Autonix account; Deriv is optional.
- `/auth/login`: Existing Deriv OAuth entry point; PKCE and fallback behavior are retained.
- `/auth/logout`: Revokes and clears Deriv state while preserving Autonix identity.

## Environment Configuration

The app loads `.env.production` before `.env`. Keep real values there and out of
source control. `.env.example` contains placeholders only.

```bash
# Existing Deriv OAuth setup (unchanged)
DERIV_APP_ID=your_deriv_oauth_client_id
REDIRECT_URL=https://your-domain.example
SESSION_SECRET=your_long_random_session_secret
DERIV_LEGACY_APP_ID=your_numeric_legacy_app_id

# Brevo SMTP (TLS on port 587 is recommended)
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=your-brevo-smtp-login
SMTP_PASSWORD=your-brevo-smtp-key
SMTP_ENCRYPTION=tls
MAIL_FROM_NAME=Autonix
MAIL_FROM_ADDRESS=your-verified-sender-address

# Google OAuth web client
AUTONIX_GOOGLE_CLIENT_ID=your-google-oauth-client-id
AUTONIX_GOOGLE_CLIENT_SECRET=your-google-oauth-client-secret
AUTONIX_GOOGLE_REDIRECT_URI=https://your-domain.example/auth/google/callback
AUTONIX_PUBLIC_URL=https://your-domain.example
```

The Google callback URL must exactly match the URI registered in Google Cloud.
Brevo requires valid SMTP credentials and a verified sender address. Missing SMTP
settings are reported by variable name; SMTP errors are logged and surfaced as
delivery failures, not simulated success. Optionally set `AUTONIX_PUBLIC_URL` to
the canonical site origin for reset links; otherwise the existing `REDIRECT_URL`
is used.

## Testing the Flask App

Start Flask with the normal project command:

```bash
python app.py
```

#### Check Session Status

```bash
curl http://localhost:5000/api/auth-debug
```

Expected response (after login):
```json
{
  "timestamp": "2026-09-01T10:30:45.123456+00:00",
  "session_exists": true,
  "is_authenticated": true,
  "session_keys": ["isAuthenticated", "activeAccount", "accounts", "_accessToken", "_refreshToken", "_tokenExpiresAt"],
  "active_account": "VRT123456",
  "num_accounts": 1
}
```

#### Quick Auth Test

```bash
curl http://localhost:5000/api/auth-test
```

Expected response (after login):
```json
{
  "authenticated": true,
  "account": "VRT123456",
  "currency": "USD"
}
```

### Test Autonix and Deriv Separately

1. Visit `/register` and create an Autonix account without signing in to Deriv.
2. Verify `/dashboard` shows the Autonix profile and Deriv connection options.
3. Verify `/account/profile` returns the Autonix profile and an unconnected state.
4. Choose **Connect Deriv Account** and complete the existing Deriv OAuth flow.
5. Verify the dashboard shows the existing Deriv account information and
   `/account/profile` lists its account ID.
6. Submit a password reset for an email-backed account. Verify a real email is
   accepted by Brevo and received; SMTP or sender-verification failures must be
   treated as delivery failures.
7. Autonix logout should end website identity. Deriv logout should revoke/clear
   Deriv session state while keeping an Autonix session active.
8. A visitor without an Autonix session should be redirected from `/dashboard`
   to `/login`.

## Common Issues & Fixes

### Issue 1: "No authorization code received"

**Symptom:** Login form appears, but after Deriv login, get error instead of dashboard.

**Causes:**
- REDIRECT_URL not matching registered URI in Deriv app settings
- Deriv app not properly configured
- Browser blocked the redirect

**Fix:**
1. Check `REDIRECT_URL` in your `.env`
   ```bash
   REDIRECT_URL=http://localhost:5000
   # Must match EXACTLY what you registered in Deriv app settings
   ```
   
2. Verify Deriv app is registered:
   - Go to https://app.deriv.com/account/api-token
   - Or contact Deriv support

3. Check Flask logs:
   ```bash
   # Look for error lines in logs
   grep "ERROR" logs.txt
   ```

### Issue 2: "PKCE verifier mismatch"

**Symptom:** Login fails with "Login session expired or invalid"

**Causes:**
- Session cookie lost during redirect
- Tampered state parameter

**Fix:**
1. Verify `SESSION_SECRET` is set
   ```bash
   SESSION_SECRET=your_secret_key_here
   ```

2. Check Flask session cookie settings (should be automatic)

3. Restart Flask app:
   ```bash
   pkill -f "python app.py"
   python app.py
   ```

### Issue 3: "No access_token in response"

**Symptom:** OAuth callback succeeds but session not created

**Causes:**
- Token exchange failed silently
- Deriv OAuth service error
- DERIV_APP_ID incorrect

**Fix:**
1. Check DERIV_APP_ID is correct (should be alphanumeric string, not numeric)
   ```bash
   echo $DERIV_APP_ID
   # Should look like: "34567890abcdef"
   ```

2. Check Flask logs for detailed error:
   ```bash
   grep "token_data" logs.txt
   ```

3. Try switching to legacy OAuth:
   - Set `DERIV_LEGACY_APP_ID` to numeric app ID
   - This may work better if new OAuth has issues

### Issue 4: Dashboard shows "Not Authenticated"

**Symptom:** Redirected to dashboard, but see login prompt instead

**Causes:**
- Session not persisted properly
- SESSION_SECRET not set or changed
- Cookie not being sent

**Fix:**
1. Check session with debug endpoint:
   ```bash
   curl http://localhost:5000/api/auth-debug
   ```
   
   If `"session_exists": false`, session was lost.

2. Make sure SESSION_SECRET is persistent:
   ```bash
   # .env file
   SESSION_SECRET=your_permanent_secret_key_here
   ```
   
   (Without this, sessions are lost on app restart)

3. Check browser cookies:
   - Open DevTools → Application → Cookies
   - Look for `session` cookie
   - Should have value, not empty

### Issue 5: Can't access dashboard after login

**Symptom:** Redirects to dashboard but page doesn't load

**Causes:**
- templates/dashboard.html missing
- Template rendering error
- Session data not passed to template

**Fix:**
1. Check if dashboard template exists:
   ```bash
   ls -la templates/dashboard.html
   ```

2. Check Flask logs for template errors:
   ```bash
   grep "render_template" logs.txt
   ```

3. Verify session data is being sent:
   ```bash
   curl -H "Cookie: session=your_session_id" http://localhost:5000/api/auth-debug
   ```

## Session Data Structure

When user is logged in, Flask session contains:

```python
{
    "user": {
        "isAuthenticated": True,
        "activeAccount": {
            "account": "VRT123456",
            "token": "",  # Empty in _safe_for_template (stripped for security)
            "currency": "USD",
            "isVirtual": True,
            "accountType": "virtual",
            "balance": 10000.00
        },
        "accounts": [
            # ... list of all user's accounts
        ],
        # Private keys (not sent to browser):
        "_accessToken": "access_token_string",
        "_refreshToken": "refresh_token_string", 
        "_tokenExpiresAt": "2026-09-01T18:30:45.123456+00:00"
    }
}
```

## Testing Checklist

- [ ] Flask app starts without errors
- [ ] Marketing page loads at `/`
- [ ] Autonix registration/login works without a Deriv account
- [ ] `/dashboard` redirects unauthenticated visitors to `/login`
- [ ] An Autonix-only user sees the profile and Deriv connection options
- [ ] Password reset sends a real Brevo email from a verified sender
- [ ] Google OAuth creates or signs into an Autonix account
- [ ] Connect Deriv invokes the unchanged Deriv authorization flow
- [ ] A successful Deriv account session is associated with its Autonix user
- [ ] The existing connected Deriv dashboard and account switching still work
- [ ] `/api/auth-debug` shows `is_authenticated: true`
- [ ] Deriv logout leaves the Autonix website session active
- [ ] SMTP delivery failures are reported instead of simulated as success

## Next Steps: React Integration

The React trading-bot app continues to use Flask's Deriv endpoints for trading
authorization:

1. React needs to check Flask's `/auth/session` endpoint
2. React calls Flask's `/auth/login` when a Deriv connection is needed
3. React calls Flask's `/auth/logout` to disconnect Deriv

See `UNIFIED_AUTHENTICATION.md` for React integration guide.

## Debug Helpers

### View Flask logs

If running in terminal:
```bash
# Logs appear in terminal
tail -f flask_logs.txt
```

### Enable verbose Flask logging

Add to app.py:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)
```

### Test session persistence

```bash
# Start session
curl -c cookies.txt http://localhost:5000/auth/login

# Check if session exists
curl -b cookies.txt http://localhost:5000/api/auth-debug

# Logout
curl -b cookies.txt http://localhost:5000/auth/logout

# Verify session cleared
curl -b cookies.txt http://localhost:5000/api/auth-debug
```

### Check environment in running app

Add this endpoint to see what config Flask has:

```python
@app.route("/api/config-debug")
def config_debug():
    return jsonify({
        "DERIV_APP_ID": DERIV_APP_ID[:10] + "..." if DERIV_APP_ID else None,
        "REDIRECT_URL": REDIRECT_URL,
        "SESSION_COOKIE_SAMESITE": app.config.get("SESSION_COOKIE_SAMESITE"),
        "SESSION_COOKIE_SECURE": app.config.get("SESSION_COOKIE_SECURE"),
        "SESSION_COOKIE_HTTPONLY": app.config.get("SESSION_COOKIE_HTTPONLY"),
    })
```

Then:
```bash
curl http://localhost:5000/api/config-debug
```
