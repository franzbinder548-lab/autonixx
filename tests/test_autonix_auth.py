import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DERIV_APP_ID", "test-app-id")
os.environ.setdefault("FLASK_ENV", "development")

import app as flask_app
import autonix_auth as auth


class AutonixAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, "users.sqlite3")
        self.environment = patch.dict(
            os.environ,
            {
                "AUTONIX_AUTH_DATABASE_PATH": self.database_path,
                "AUTONIX_PUBLIC_URL": "https://autonixtrading.online",
                "SMTP_HOST": "",
                "SMTP_PORT": "",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "SMTP_ENCRYPTION": "",
                "MAIL_FROM_NAME": "",
                "MAIL_FROM_ADDRESS": "",
            },
        )
        self.environment.start()
        self.client = flask_app.app.test_client()

    def tearDown(self):
        self.environment.stop()
        self.temp_dir.cleanup()

    def _csrf(self):
        with self.client.session_transaction() as user_session:
            return user_session["autonix_csrf_token"]

    def _register(self, email="person@example.com"):
        page = self.client.get("/register")
        self.assertEqual(page.status_code, 200)
        return self.client.post(
            "/register",
            data={
                "csrf_token": self._csrf(),
                "full_name": "Autonix User",
                "email": email,
                "password": "StrongPassword!123",
                "confirm_password": "StrongPassword!123",
            },
        )

    def test_registration_and_login_do_not_require_a_deriv_session(self):
        response = self._register()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        with self.client.session_transaction() as user_session:
            self.assertTrue(user_session.get("autonix_user_id"))
            self.assertNotIn("user", user_session)

        self.client.get("/dashboard")
        self.client.post(
            "/account/logout",
            data={"csrf_token": self._csrf()},
        )
        self.client.get("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self._csrf(),
                "email": "person@example.com",
                "password": "StrongPassword!123",
            },
        )
        self.assertEqual(response.status_code, 302)
        profile = self.client.get("/account/profile")
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json["email"], "person@example.com")
        self.assertFalse(profile.json["deriv_connected"])

    def test_dashboard_requires_autonix_identity_but_not_deriv(self):
        unauthenticated = self.client.get("/dashboard")
        self.assertEqual(unauthenticated.status_code, 302)
        self.assertIn("/login", unauthenticated.headers["Location"])

        self._register()
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Connect Deriv Account", dashboard.data)
        self.assertIn(b"Welcome back, AUTONIX USER", dashboard.data)
        self.assertIn(b'data-theme="dark"', dashboard.data)
        self.assertEqual(dashboard.data.count(b'id="siteNav"'), 1)
        self.assertIn(b"Available AI bots", dashboard.data)
        self.assertIn(b"Free Bot", dashboard.data)
        self.assertIn(b"Basic Bot", dashboard.data)
        self.assertIn(b"Expert Bot", dashboard.data)
        self.assertIn(b"Bulk Trader", dashboard.data)
        self.assertEqual(dashboard.data.count(b'class="ad-bot-card"'), 4)
        self.assertIn(b"Recent activity", dashboard.data)
        self.assertIn(b"Quick actions", dashboard.data)
        self.assertIn(b"Profile &amp; account settings", dashboard.data)
        self.assertEqual(dashboard.data.count(b'id="dash-live-chart"'), 1)
        self.assertIn(b'href="/auth/login"', dashboard.data)
        self.assertIn(b"https://t.deriv.link?t=99THF5S5V9K8", dashboard.data)

    def test_deriv_account_can_be_linked_to_multiple_autonix_profiles(self):
        self._register("first@example.com")
        with self.client.session_transaction() as user_session:
            first_user_id = user_session["autonix_user_id"]
            first_account = {
                "account": "CR100001",
                "currency": "USD",
                "isVirtual": False,
                "accountType": "real",
                "balance": 0,
            }
            additional_account = {
                "account": "CR100002",
                "currency": "USD",
                "isVirtual": True,
                "accountType": "demo",
                "balance": 0,
            }
            user_session["user"] = {
                "isAuthenticated": True,
                "activeAccount": first_account,
                "accounts": [first_account, additional_account],
                "_accessToken": "",
            }
        first_dashboard = self.client.get("/dashboard")
        self.assertEqual(first_dashboard.status_code, 200)
        self.client.post("/account/logout", data={"csrf_token": self._csrf()})

        self._register("second@example.com")
        with self.client.session_transaction() as user_session:
            second_user_id = user_session["autonix_user_id"]
            shared_account = {"account": "CR100001", "accountType": "real"}
            second_account = {"account": "CR100003", "accountType": "demo"}
            user_session["user"] = {
                "isAuthenticated": True,
                "activeAccount": {
                    "account": "CR100001",
                    "currency": "USD",
                    "isVirtual": False,
                    "accountType": "real",
                    "balance": 0,
                },
                "accounts": [shared_account, second_account],
                "_accessToken": "",
            }
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(
            b"One or more Deriv accounts are linked to another Autonix profile.",
            dashboard.data,
        )
        profile = self.client.get("/account/profile").json
        self.assertTrue(profile["deriv_connected"])
        self.assertEqual(profile["deriv_accounts"], ["CR100001", "CR100003"])

        connection = auth._database()
        try:
            links = connection.execute(
                "SELECT user_id, deriv_account_id FROM autonix_deriv_accounts "
                "WHERE user_id IN (?, ?) ORDER BY user_id, deriv_account_id",
                (first_user_id, second_user_id),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [(row["user_id"], row["deriv_account_id"]) for row in links],
            sorted([
                (first_user_id, "CR100001"),
                (first_user_id, "CR100002"),
                (second_user_id, "CR100001"),
                (second_user_id, "CR100003"),
            ]),
        )

    def test_existing_deriv_account_links_migrate_to_multi_profile_schema(self):
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE autonix_users (
                    id TEXT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT,
                    auth_provider TEXT NOT NULL,
                    google_id TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    last_login TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    deriv_connected INTEGER NOT NULL DEFAULT 0,
                    email_verified INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE autonix_deriv_accounts (
                    user_id TEXT NOT NULL REFERENCES autonix_users(id) ON DELETE CASCADE,
                    deriv_account_id TEXT NOT NULL UNIQUE,
                    connected_at TEXT NOT NULL,
                    account_type TEXT,
                    currency TEXT,
                    balance REAL,
                    last_successful_sync_at TEXT,
                    PRIMARY KEY (user_id, deriv_account_id)
                );
                INSERT INTO autonix_users (id, full_name, email, auth_provider, created_at)
                    VALUES ('first-user', 'First User', 'first@example.com', 'password', '2026-01-01');
                INSERT INTO autonix_users (id, full_name, email, auth_provider, created_at)
                    VALUES ('second-user', 'Second User', 'second@example.com', 'password', '2026-01-01');
                INSERT INTO autonix_deriv_accounts
                    (user_id, deriv_account_id, connected_at)
                    VALUES ('first-user', 'CR100001', '2026-01-01');
                """
            )
        finally:
            connection.close()

        self.assertEqual(
            auth.link_deriv_accounts("second-user", [{"account": "CR100001"}]),
            [],
        )
        connection = auth._database()
        try:
            linked_users = connection.execute(
                "SELECT user_id FROM autonix_deriv_accounts "
                "WHERE deriv_account_id = ? ORDER BY user_id",
                ("CR100001",),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [row["user_id"] for row in linked_users],
            ["first-user", "second-user"],
        )

    def test_profile_name_update_requires_csrf_and_saves_name(self):
        self._register()
        self.client.get("/dashboard")
        csrf_token = self._csrf()
        invalid = self.client.post(
            "/account/profile/update",
            data={"full_name": "Changed Name"},
        )
        self.assertEqual(invalid.status_code, 302)
        self.assertEqual(
            self.client.get("/account/profile").json["full_name"],
            "Autonix User",
        )

        updated = self.client.post(
            "/account/profile/update",
            data={
                "csrf_token": csrf_token,
                "full_name": "Changed Name",
            },
        )
        self.assertEqual(updated.status_code, 302)
        self.assertIn(b"Welcome back, CHANGED NAME", self.client.get("/dashboard").data)

    def test_verification_email_can_be_resent_and_link_verifies_account(self):
        smtp_environment = {
            "SMTP_HOST": "smtp-relay.brevo.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "test-login",
            "SMTP_PASSWORD": "test-key",
            "SMTP_ENCRYPTION": "STARTTLS",
            "MAIL_FROM_NAME": "Autonix",
            "MAIL_FROM_ADDRESS": "verified@example.com",
        }
        with patch.dict(os.environ, smtp_environment), patch.object(
            auth, "_send_template_email"
        ) as send_email:
            self._register()
            send_email.reset_mock()
            self.client.get("/dashboard")
            response = self.client.post(
                "/email/verification/resend",
                data={"csrf_token": self._csrf()},
            )

        self.assertEqual(response.status_code, 302)
        send_email.assert_called_once()
        template, _, subject, context = send_email.call_args.args
        self.assertEqual(template, "email_verification")
        self.assertEqual(subject, "Verify your Autonix email address")
        self.assertIn("https://autonixtrading.online/email/verify/", context["verification_url"])
        verification = self.client.get(context["verification_url"].replace("https://autonixtrading.online", ""))
        self.assertEqual(verification.status_code, 302)
        self.assertTrue(self.client.get("/account/profile").json["email_verified"])

    def test_login_security_templates_are_only_sent_for_new_user_agent(self):
        self._register()
        self.client.get("/dashboard")
        self.client.post(
            "/account/logout",
            data={"csrf_token": self._csrf()},
        )
        self.client.get("/login")
        login_data = {
            "csrf_token": self._csrf(),
            "email": "person@example.com",
            "password": "StrongPassword!123",
        }
        with patch.object(auth, "_send_template_email") as send_email:
            response = self.client.post(
                "/login",
                data=login_data,
                headers={"User-Agent": "AutonixTestBrowser/1"},
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(
                [call.args[0] for call in send_email.call_args_list],
                ["new_login", "suspicious_activity"],
            )

            self.client.get("/dashboard")
            self.client.post(
                "/account/logout",
                data={"csrf_token": self._csrf()},
            )
            self.client.get("/login")
            login_data["csrf_token"] = self._csrf()
            response = self.client.post(
                "/login",
                data=login_data,
                headers={"User-Agent": "AutonixTestBrowser/1"},
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(send_email.call_count, 2)

    def test_email_sender_renders_multipart_templates_and_accepts_starttls(self):
        contexts = {
            "welcome": {
                "first_name": "Autonix", "full_name": "Autonix User",
                "email": "person@example.com", "registration_date": "November 15, 2026",
                "dashboard_url": "https://autonixtrading.online/dashboard",
            },
            "email_verification": {
                "first_name": "Autonix", "expiry_time": "24 hours",
                "verification_url": "https://autonixtrading.online/email/verify/test",
            },
            "password_reset": {
                "first_name": "Autonix", "expiry_time": "1 hour",
                "reset_url": "https://autonixtrading.online/password/reset/test",
                "support_url": auth.SECURITY_SUPPORT_URL,
            },
            "new_login": {
                "first_name": "Autonix", "login_datetime": "Now", "device": "Windows",
                "browser": "Test", "location": "Unavailable", "ip_address": "Unavailable",
                "review_url": "https://autonixtrading.online/dashboard",
            },
            "password_changed": {
                "first_name": "Autonix", "change_datetime": "Now", "device": "Windows",
                "browser": "Test", "ip_address": "Unavailable",
                "support_url": auth.SECURITY_SUPPORT_URL,
            },
            "suspicious_activity": {
                "first_name": "Autonix", "activity_type": "New device",
                "activity_datetime": "Now", "device": "Windows", "browser": "Test",
                "location": "Unavailable", "ip_address": "Unavailable",
                "secure_url": "https://autonixtrading.online/dashboard",
            },
        }
        settings = {
            "SMTP_HOST": "smtp-relay.brevo.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "test-login",
            "SMTP_PASSWORD": "test-key",
            "SMTP_ENCRYPTION": "STARTTLS",
            "MAIL_FROM_NAME": "Autonix",
            "MAIL_FROM_ADDRESS": "verified@example.com",
        }
        with patch.dict(os.environ, settings), patch.object(auth.smtplib, "SMTP") as smtp_factory:
            smtp = smtp_factory.return_value.__enter__.return_value
            for name, context in contexts.items():
                with flask_app.app.test_request_context("/"):
                    auth._send_template_email(
                        name, "person@example.com", "Test subject", context
                    )
                message = smtp.send_message.call_args.args[0]
                self.assertIsNotNone(message.get_body(preferencelist=("plain",)))
                self.assertIsNotNone(message.get_body(preferencelist=("html",)))
            smtp.starttls.assert_called()
            self.assertEqual(smtp.send_message.call_count, len(contexts))

    def test_password_reset_sends_custom_reset_and_changed_notifications(self):
        smtp_environment = {
            "SMTP_HOST": "smtp-relay.brevo.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "test-login",
            "SMTP_PASSWORD": "test-key",
            "SMTP_ENCRYPTION": "tls",
            "MAIL_FROM_NAME": "Autonix",
            "MAIL_FROM_ADDRESS": "verified@example.com",
        }
        with patch.dict(os.environ, smtp_environment), patch.object(
            auth, "_send_template_email"
        ) as send_email:
            self._register()
            send_email.reset_mock()
            self.client.get("/password/forgot")
            response = self.client.post(
                "/password/forgot",
                data={
                    "csrf_token": self._csrf(),
                    "email": "person@example.com",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(send_email.call_args.args[0], "password_reset")
            reset_url = send_email.call_args.args[3]["reset_url"]
            reset_path = reset_url.replace("https://autonixtrading.online", "")

            self.client.get(reset_path)
            response = self.client.post(
                reset_path,
                data={
                    "csrf_token": self._csrf(),
                    "password": "AnotherStrongPassword!123",
                    "confirm_password": "AnotherStrongPassword!123",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(send_email.call_args.args[0], "password_changed")

        self.client.get("/login")
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self._csrf(),
                "email": "person@example.com",
                "password": "AnotherStrongPassword!123",
            },
            headers={"User-Agent": "PasswordResetTestBrowser/1"},
        )
        self.assertEqual(response.status_code, 302)

    def test_landing_page_promotes_autonix_login_and_registration(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create Autonix Account", response.data)
        self.assertIn(b'href="/login"', response.data)
        self.assertIn(b'href="/register"', response.data)

    def test_authenticated_navigation_has_compact_dashboard_without_duplicate_ctas(self):
        self._register()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b">Dashboard</a>", response.data)
        self.assertIn(b"Account settings", response.data)
        self.assertIn(b"Log out of Autonix", response.data)
        self.assertNotIn(b'id="demoBtn"', response.data)
        self.assertNotIn(b'id="goToTradeBtn"', response.data)
        self.assertNotIn(b">My account</a>", response.data)
        self.assertNotIn(b"class=\"nav-btn nav-btn-ghost\">Connect Deriv", response.data)

    def test_deriv_connection_is_associated_and_deriv_logout_keeps_autonix_session(self):
        self._register()
        with self.client.session_transaction() as user_session:
            user_id = user_session["autonix_user_id"]
            account = {
                "account": "CR100001",
                "currency": "USD",
                "isVirtual": False,
                "accountType": "real",
                "balance": 0,
            }
            user_session["user"] = {
                "isAuthenticated": True,
                "activeAccount": account,
                "accounts": [account],
                "_accessToken": "",
            }

        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"CR100001", dashboard.data)
        self.assertIn(b"Deriv account connected", dashboard.data)
        self.assertIn(b'id="navbar-account-balance"', dashboard.data)
        self.assertIn(b"data-account-active-balance", dashboard.data)
        self.assertIn(b"data-account-amount=", dashboard.data)
        self.assertIn(b'href="#dashboard-accounts"', dashboard.data)
        self.assertIn(b'href="/auth/logout"', dashboard.data)
        self.assertEqual(dashboard.data.count(b'id="dash-live-chart"'), 1)
        linked_profile = self.client.get("/account/profile").json
        self.assertTrue(linked_profile["deriv_connected"])
        self.assertEqual(linked_profile["deriv_accounts"], ["CR100001"])

        response = self.client.get("/auth/logout")
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as user_session:
            self.assertEqual(user_session["autonix_user_id"], user_id)
            self.assertNotIn("user", user_session)
        self.assertFalse(self.client.get("/account/profile").json["deriv_connected"])

    def test_autonix_logout_revokes_deriv_tokens_and_clears_the_entire_session(self):
        self._register()
        with self.client.session_transaction() as user_session:
            user_session["user"] = {
                "isAuthenticated": True,
                "activeAccount": {
                    "account": "CR100001",
                    "balance": 0,
                    "currency": "USD",
                    "isVirtual": False,
                },
                "accounts": [{
                    "account": "CR100001",
                    "balance": 0,
                    "currency": "USD",
                    "isVirtual": False,
                    "token": "account-token",
                }],
                "_accessToken": "access-token",
                "_refreshToken": "refresh-token",
            }

        self.client.get("/dashboard")
        with patch.object(flask_app, "_try_revoke") as revoke:
            response = self.client.post(
                "/account/logout",
                data={"csrf_token": self._csrf()},
            )

        self.assertEqual(response.status_code, 302)
        self.assertCountEqual(
            [call.args[0] for call in revoke.call_args_list],
            ["access-token", "refresh-token", "account-token"],
        )
        with self.client.session_transaction() as user_session:
            self.assertEqual(dict(user_session), {})

    def test_orphaned_deriv_session_is_removed(self):
        with self.client.session_transaction() as user_session:
            user_session["user"] = {
                "isAuthenticated": True,
                "_accessToken": "orphan-token",
                "accounts": [],
            }

        with patch.object(flask_app, "_try_revoke") as revoke:
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        revoke.assert_called_once_with("orphan-token")
        with self.client.session_transaction() as user_session:
            self.assertNotIn("user", user_session)

    def test_get_account_switch_returns_to_safe_current_page(self):
        self._register()
        with self.client.session_transaction() as user_session:
            user_session["user"] = {
                "isAuthenticated": True,
                "activeAccount": {"account": "CR100001"},
                "accounts": [{"account": "CR100001", "token": ""}],
                "_accessToken": "",
            }

        response = self.client.get(
            "/auth/switch/CR100001?next=%2Fbots%3Ftab%3Doverview"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/bots?tab=overview")

        response = self.client.get(
            "/auth/switch/CR100001?next=https%3A%2F%2Fexample.com%2Fsteal"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/trader")

    def test_password_reset_reports_missing_smtp_configuration_without_fake_success(self):
        self.client.get("/password/forgot")
        response = self.client.post(
            "/password/forgot",
            data={
                "csrf_token": self._csrf(),
                "email": "person@example.com",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"SMTP_HOST", response.data)
        self.assertIn(b"MAIL_FROM_ADDRESS", response.data)
        self.assertFalse(os.path.exists(self.database_path))

    def test_google_sign_in_is_unavailable_for_autonix_accounts(self):
        for path in ("/auth/google", "/auth/google/callback"):
            self.assertEqual(self.client.get(path).status_code, 404)

        for path in ("/login", "/register"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"Continue with Google", response.data)

    def test_password_hash_is_not_stored_as_plaintext(self):
        self._register()
        connection = sqlite3.connect(self.database_path)
        try:
            stored = connection.execute(
                "SELECT password_hash FROM autonix_users WHERE email = ?",
                ("person@example.com",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn("StrongPassword!123", stored)


if __name__ == "__main__":
    unittest.main()
