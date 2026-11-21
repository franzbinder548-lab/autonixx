import hashlib
import hmac
import ipaddress
import os
import secrets
import smtplib
import sqlite3
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


autonix_auth = Blueprint("autonix_auth", __name__)
BASE_DIR = Path(__file__).resolve().parent
PASSWORD_RESET_LIFETIME = timedelta(hours=1)
EMAIL_VERIFICATION_LIFETIME = timedelta(hours=24)
SECURITY_SUPPORT_URL = "https://wa.me/254776685670"
DEFAULT_PUBLIC_URL = "https://autonixtrading.online"


def _utc_now():
    return datetime.now(timezone.utc)


def _timestamp(value=None):
    return (value or _utc_now()).isoformat(timespec="seconds")


def _database_path():
    configured = os.getenv("AUTONIX_AUTH_DATABASE_PATH", "").strip()
    if not configured:
        return BASE_DIR / "data" / "autonix_users.sqlite3"
    path = Path(configured)
    return path if path.is_absolute() else BASE_DIR / path


def _database():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS autonix_users (
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
        CREATE TABLE IF NOT EXISTS autonix_deriv_accounts (
            user_id TEXT NOT NULL REFERENCES autonix_users(id) ON DELETE CASCADE,
            deriv_account_id TEXT NOT NULL UNIQUE,
            connected_at TEXT NOT NULL,
            account_type TEXT,
            currency TEXT,
            balance REAL,
            last_successful_sync_at TEXT,
            PRIMARY KEY (user_id, deriv_account_id)
        );
        CREATE TABLE IF NOT EXISTS autonix_password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES autonix_users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS autonix_email_verifications (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES autonix_users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS autonix_login_devices (
            user_id TEXT NOT NULL REFERENCES autonix_users(id) ON DELETE CASCADE,
            device_hash TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (user_id, device_hash)
        );
        """
    )
    user_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(autonix_users)")
    }
    if "email_verified" not in user_columns:
        connection.execute(
            "ALTER TABLE autonix_users ADD COLUMN email_verified "
            "INTEGER NOT NULL DEFAULT 0"
        )
    deriv_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(autonix_deriv_accounts)")
    }
    for column, definition in (
        ("account_type", "TEXT"),
        ("currency", "TEXT"),
        ("balance", "REAL"),
        ("last_successful_sync_at", "TEXT"),
    ):
        if column not in deriv_columns:
            connection.execute(
                f"ALTER TABLE autonix_deriv_accounts ADD COLUMN {column} {definition}"
            )
    return connection


def _csrf_token():
    token = session.get("autonix_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["autonix_csrf_token"] = token
    return token


def get_autonix_csrf_token():
    return _csrf_token()


def _valid_csrf():
    expected = session.get("autonix_csrf_token", "")
    supplied = request.form.get("csrf_token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _render_auth(mode, status=200):
    return (
        render_template(
            "autonix_auth.html",
            mode=mode,
            csrf_token=_csrf_token(),
            next_url=request.form.get(
                "next", request.args.get("next", "/dashboard")
            ),
        ),
        status,
    )


def _safe_next(value):
    if not value:
        return "/dashboard"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return "/dashboard"
    return value


def _valid_email(value):
    if not isinstance(value, str) or len(value) > 254 or value.count("@") != 1:
        return False
    if any(character.isspace() or character in ",<>;" for character in value):
        return False
    local, domain = value.rsplit("@", 1)
    if not local or len(local) > 64 or not domain:
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(
        label
        and not label.startswith("-")
        and not label.endswith("-")
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def _password_hash(password):
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64
    )
    return f"scrypt${salt.hex()}${derived.hex()}"


def _password_matches(password, encoded):
    try:
        algorithm, salt_hex, digest_hex = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2**14,
            r=8,
            p=1,
            dklen=64,
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _establish_session(user_id):
    connection = _database()
    try:
        connection.execute(
            "UPDATE autonix_users SET last_login = ? WHERE id = ? AND status = 'active'",
            (_timestamp(), user_id),
        )
        connection.commit()
    finally:
        connection.close()
    session["autonix_user_id"] = user_id
    session.permanent = True
    session.pop("autonix_csrf_token", None)


def get_current_autonix_user():
    user_id = session.get("autonix_user_id")
    if not user_id:
        return None
    connection = _database()
    try:
        user = connection.execute(
            "SELECT id, full_name, email, auth_provider, created_at, "
            "last_login, status, deriv_connected, email_verified FROM autonix_users "
            "WHERE id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
        if not user:
            session.pop("autonix_user_id", None)
            return None
        result = dict(user)
        result["deriv_accounts"] = [
            row["deriv_account_id"]
            for row in connection.execute(
                "SELECT deriv_account_id FROM autonix_deriv_accounts "
                "WHERE user_id = ? ORDER BY connected_at",
                (user_id,),
            ).fetchall()
        ]
        return result
    finally:
        connection.close()


def link_deriv_accounts(user_id, accounts):
    if not user_id:
        return []
    connection = _database()
    conflicts = []
    linked_any = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        for account in accounts:
            account_id = str(account.get("account") or "").strip()
            if not account_id:
                continue
            owner = connection.execute(
                "SELECT user_id FROM autonix_deriv_accounts WHERE deriv_account_id = ?",
                (account_id,),
            ).fetchone()
            if owner and owner["user_id"] != user_id:
                conflicts.append(account_id)
                continue
            connection.execute(
                "INSERT OR IGNORE INTO autonix_deriv_accounts "
                "(user_id, deriv_account_id, connected_at) VALUES (?, ?, ?)",
                (user_id, account_id, _timestamp()),
            )
            account_type = account.get("accountType") or account.get("account_type")
            if account_type:
                connection.execute(
                    "UPDATE autonix_deriv_accounts SET account_type = ? "
                    "WHERE user_id = ? AND deriv_account_id = ?",
                    (str(account_type).lower(), user_id, account_id),
                )
            linked_any = True
        if linked_any:
            connection.execute(
                "UPDATE autonix_users SET deriv_connected = 1 "
                "WHERE id = ? AND status = 'active'",
                (user_id,),
            )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()
    return conflicts


def update_deriv_account_snapshots(user_id, accounts, synced_at=None):
    """Persist non-secret account details and balances received from Deriv."""
    if not user_id:
        return
    connection = _database()
    try:
        for account in accounts:
            account_id = str(account.get("account") or "").strip()
            balance = account.get("balance")
            if not account_id or balance is None:
                continue
            try:
                balance = float(balance)
            except (TypeError, ValueError):
                continue
            connection.execute(
                "UPDATE autonix_deriv_accounts SET account_type = COALESCE(?, account_type), "
                "currency = COALESCE(?, currency), balance = ?, last_successful_sync_at = ? "
                "WHERE user_id = ? AND deriv_account_id = ?",
                (
                    account.get("accountType") or account.get("account_type"),
                    str(account.get("currency")).upper() if account.get("currency") else None,
                    balance,
                    synced_at or _timestamp(),
                    user_id,
                    account_id,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def get_deriv_account_snapshots():
    connection = _database()
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT a.user_id, u.full_name, u.email, u.deriv_connected, "
                "a.deriv_account_id, a.connected_at, a.account_type, a.currency, "
                "a.balance, a.last_successful_sync_at "
                "FROM autonix_deriv_accounts a "
                "JOIN autonix_users u ON u.id = a.user_id "
                "ORDER BY u.full_name COLLATE NOCASE, a.deriv_account_id"
            ).fetchall()
        ]
    finally:
        connection.close()


def set_deriv_connection(user_id, connected):
    if not user_id:
        return
    connection = _database()
    try:
        connection.execute(
            "UPDATE autonix_users SET deriv_connected = ? WHERE id = ?",
            (1 if connected else 0, user_id),
        )
        connection.commit()
    finally:
        connection.close()


def _smtp_settings():
    names = (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_ENCRYPTION",
        "MAIL_FROM_NAME",
        "MAIL_FROM_ADDRESS",
    )
    settings = {name: os.getenv(name, "").strip() for name in names}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise ValueError("Missing SMTP settings: " + ", ".join(missing))
    try:
        settings["SMTP_PORT"] = int(settings["SMTP_PORT"])
    except ValueError as exc:
        raise ValueError("SMTP_PORT must be an integer") from exc
    if not 1 <= settings["SMTP_PORT"] <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535")
    settings["SMTP_ENCRYPTION"] = settings["SMTP_ENCRYPTION"].lower()
    if settings["SMTP_ENCRYPTION"] == "starttls":
        settings["SMTP_ENCRYPTION"] = "tls"
    if settings["SMTP_ENCRYPTION"] not in {"tls", "ssl"}:
        raise ValueError("SMTP_ENCRYPTION must be 'starttls', 'tls', or 'ssl'")
    if not _valid_email(settings["MAIL_FROM_ADDRESS"]):
        raise ValueError("MAIL_FROM_ADDRESS must be a single valid email address")
    return settings


def _public_url(path):
    base_url = os.getenv("AUTONIX_PUBLIC_URL", "").strip()
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        redirect_url = os.getenv("REDIRECT_URL", "").strip()
        parsed = urlparse(redirect_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            base_url = f"{parsed.scheme}://{parsed.netloc}"
        else:
            base_url = DEFAULT_PUBLIC_URL
    return base_url.rstrip("/") + (path if path.startswith("/") else "/" + path)


def _email_first_name(full_name):
    return (full_name or "").strip().split(" ", 1)[0] or "there"


def _email_datetime(value=None):
    value = value or _utc_now()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")


def _send_template_email(template_name, recipient, subject, context):
    settings = _smtp_settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(
        (settings["MAIL_FROM_NAME"], settings["MAIL_FROM_ADDRESS"])
    )
    message["To"] = recipient
    message.set_content(render_template(f"emails/{template_name}.txt", **context))
    message.add_alternative(
        render_template(f"emails/{template_name}.html", **context),
        subtype="html",
    )

    ssl_context = ssl.create_default_context()
    if settings["SMTP_ENCRYPTION"] == "ssl":
        with smtplib.SMTP_SSL(
            settings["SMTP_HOST"],
            settings["SMTP_PORT"],
            timeout=20,
            context=ssl_context,
        ) as smtp:
            smtp.login(settings["SMTP_USERNAME"], settings["SMTP_PASSWORD"])
            smtp.send_message(message)
    else:
        with smtplib.SMTP(
            settings["SMTP_HOST"], settings["SMTP_PORT"], timeout=20
        ) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl_context)
            smtp.ehlo()
            smtp.login(settings["SMTP_USERNAME"], settings["SMTP_PASSWORD"])
            smtp.send_message(message)


def _send_password_reset_email(recipient, reset_url, full_name="there"):
    _send_template_email(
        "password_reset",
        recipient,
        "Reset your Autonix password",
        {
            "first_name": _email_first_name(full_name),
            "expiry_time": "1 hour",
            "reset_url": reset_url,
            "support_url": SECURITY_SUPPORT_URL,
        },
    )


def _email_delivery_failed(label, exc):
    current_app.logger.error(
        "%s email delivery failed (%s): %s", label, type(exc).__name__, exc
    )
    return False


def _try_send_template_email(template_name, recipient, subject, context, label):
    try:
        _send_template_email(template_name, recipient, subject, context)
        return True
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        return _email_delivery_failed(label, exc)


def _issue_email_verification(user):
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = _timestamp(_utc_now() + EMAIL_VERIFICATION_LIFETIME)
    verification_url = _public_url(
        url_for("autonix_auth.verify_email", token=raw_token)
    )
    connection = _database()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM autonix_email_verifications WHERE user_id = ?",
            (user["id"],),
        )
        connection.execute(
            "INSERT INTO autonix_email_verifications "
            "(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user["id"], expires_at),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()

    return _try_send_template_email(
        "email_verification",
        user["email"],
        "Verify your Autonix email address",
        {
            "first_name": _email_first_name(user["full_name"]),
            "expiry_time": "24 hours",
            "verification_url": verification_url,
        },
        "Email verification",
    )


def _welcome_email_context(user):
    return {
        "first_name": _email_first_name(user["full_name"]),
        "full_name": user["full_name"],
        "email": user["email"],
        "registration_date": _email_datetime(user["created_at"]),
        "dashboard_url": _public_url("/dashboard"),
    }


def _send_welcome_email(user):
    return _try_send_template_email(
        "welcome",
        user["email"],
        "Welcome to Autonix",
        _welcome_email_context(user),
        "Welcome",
    )


def _request_security_details():
    user_agent = request.user_agent
    browser = " ".join(
        value for value in (user_agent.browser, user_agent.version) if value
    )
    device = user_agent.platform or "Unavailable"
    remote_address = request.remote_addr or ""
    try:
        address = ipaddress.ip_address(remote_address)
        ip_address = str(address) if address.is_global else "Unavailable"
    except ValueError:
        ip_address = "Unavailable"
    return {
        "login_datetime": _email_datetime(),
        "change_datetime": _email_datetime(),
        "activity_datetime": _email_datetime(),
        "device": device[:160],
        "browser": browser[:160] or "Unavailable",
        "location": "Unavailable",
        "ip_address": ip_address,
    }


def _is_new_login_device(user_id):
    user_agent = request.headers.get("User-Agent", "").strip()
    device_hash = hashlib.sha256(
        (user_agent or "unknown-device").encode("utf-8")
    ).hexdigest()
    now = _timestamp()
    connection = _database()
    try:
        connection.execute("BEGIN IMMEDIATE")
        inserted = connection.execute(
            "INSERT OR IGNORE INTO autonix_login_devices "
            "(user_id, device_hash, first_seen_at, last_seen_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, device_hash, now, now),
        ).rowcount == 1
        if not inserted:
            connection.execute(
                "UPDATE autonix_login_devices SET last_seen_at = ? "
                "WHERE user_id = ? AND device_hash = ?",
                (now, user_id, device_hash),
            )
        connection.commit()
        return inserted
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()


def _notify_new_login_device(user_id):
    if not _is_new_login_device(user_id):
        return True
    connection = _database()
    try:
        user = connection.execute(
            "SELECT full_name, email FROM autonix_users "
            "WHERE id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
    finally:
        connection.close()
    if not user:
        return False

    context = {
        **_request_security_details(),
        "first_name": _email_first_name(user["full_name"]),
        "review_url": _public_url("/dashboard#account-profile"),
        "secure_url": _public_url("/dashboard#account-profile"),
        "activity_type": "New browser or device login",
    }
    login_sent = _try_send_template_email(
        "new_login",
        user["email"],
        "New login to your Autonix account",
        context,
        "New login notification",
    )
    suspicious_sent = _try_send_template_email(
        "suspicious_activity",
        user["email"],
        "Security alert for your Autonix account",
        context,
        "Suspicious activity alert",
    )
    return login_sent and suspicious_sent


def _send_password_changed_email(user):
    context = {
        **_request_security_details(),
        "first_name": _email_first_name(user["full_name"]),
        "support_url": SECURITY_SUPPORT_URL,
    }
    return _try_send_template_email(
        "password_changed",
        user["email"],
        "Your Autonix password was changed",
        context,
        "Password changed notification",
    )


def _delete_password_reset(token_hash):
    connection = _database()
    try:
        connection.execute(
            "DELETE FROM autonix_password_resets WHERE token_hash = ?",
            (token_hash,),
        )
        connection.commit()
    finally:
        connection.close()


@autonix_auth.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return _render_auth("register")
    if not _valid_csrf():
        return _render_auth("register", 400)

    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirmation = request.form.get("confirm_password", "")
    if not full_name or len(full_name) > 200:
        flash("Enter your full name (up to 200 characters).", "error")
        return _render_auth("register", 400)
    if not _valid_email(email):
        flash("Enter a valid email address.", "error")
        return _render_auth("register", 400)
    if len(password) < 12:
        flash("Use a password with at least 12 characters.", "error")
        return _render_auth("register", 400)
    if password != confirmation:
        flash("The passwords do not match.", "error")
        return _render_auth("register", 400)

    user_id = secrets.token_urlsafe(24)
    created_at = _timestamp()
    connection = _database()
    try:
        connection.execute(
            "INSERT INTO autonix_users "
            "(id, full_name, email, password_hash, auth_provider, created_at) "
            "VALUES (?, ?, ?, ?, 'email', ?)",
            (user_id, full_name, email, _password_hash(password), created_at),
        )
        connection.commit()
    except sqlite3.IntegrityError:
        flash("An account already exists for that email address.", "error")
        return _render_auth("register", 409)
    finally:
        connection.close()

    _establish_session(user_id)
    user = {
        "id": user_id,
        "full_name": full_name,
        "email": email,
        "created_at": created_at,
    }
    try:
        welcome_sent = _send_welcome_email(user)
    except ValueError as exc:
        welcome_sent = _email_delivery_failed("Welcome", exc)
    try:
        verification_sent = _issue_email_verification(user)
    except ValueError as exc:
        verification_sent = _email_delivery_failed("Email verification", exc)
    if not welcome_sent or not verification_sent:
        flash(
            "Your account is ready, but one or more emails could not be sent. "
            "Check your email settings or resend the verification email from your profile.",
            "warning",
        )
    return redirect(_safe_next(request.form.get("next", "")))


@autonix_auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return _render_auth("login")
    if not _valid_csrf():
        return _render_auth("login", 400)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    connection = _database()
    try:
        user = connection.execute(
            "SELECT id, password_hash FROM autonix_users "
            "WHERE email = ? AND status = 'active'",
            (email,),
        ).fetchone()
    finally:
        connection.close()
    if not user or not user["password_hash"] or not _password_matches(
        password, user["password_hash"]
    ):
        flash("Email or password is incorrect.", "error")
        return _render_auth("login", 401)

    _establish_session(user["id"])
    try:
        if not _notify_new_login_device(user["id"]):
            flash(
                "You are signed in, but one or more security emails could not be sent.",
                "warning",
            )
    except (sqlite3.Error, ValueError):
        current_app.logger.exception("New-login security notification failed")
        flash(
            "You are signed in, but your new-device security email could not be sent.",
            "warning",
        )
    return redirect(_safe_next(request.form.get("next", "")))


@autonix_auth.route("/password/forgot", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return _render_auth("forgot")
    if not _valid_csrf():
        return _render_auth("forgot", 400)
    try:
        _smtp_settings()
    except ValueError as exc:
        current_app.logger.error("Password reset email is not configured: %s", exc)
        flash(str(exc), "error")
        return _render_auth("forgot", 503)
    try:
        _public_url("/")
    except ValueError as exc:
        current_app.logger.error("Password reset email is not configured: %s", exc)
        flash(str(exc), "error")
        return _render_auth("forgot", 503)

    email = request.form.get("email", "").strip().lower()
    connection = _database()
    try:
        user = connection.execute(
            "SELECT id, full_name FROM autonix_users "
            "WHERE email = ? AND status = 'active' "
            "AND password_hash IS NOT NULL",
            (email,),
        ).fetchone()
        if user:
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            connection.execute(
                "DELETE FROM autonix_password_resets WHERE user_id = ?",
                (user["id"],),
            )
            connection.execute(
                "INSERT INTO autonix_password_resets (token_hash, user_id, expires_at) "
                "VALUES (?, ?, ?)",
                (
                    token_hash,
                    user["id"],
                    _timestamp(_utc_now() + PASSWORD_RESET_LIFETIME),
                ),
            )
            connection.commit()
        else:
            raw_token = None
            token_hash = None
    finally:
        connection.close()

    if raw_token:
        reset_url = _public_url(
            url_for("autonix_auth.reset_password", token=raw_token)
        )
        try:
            _send_password_reset_email(email, reset_url, user["full_name"])
        except smtplib.SMTPAuthenticationError as exc:
            _delete_password_reset(token_hash)
            current_app.logger.error(
                "Brevo SMTP authentication failed (code %s)", exc.smtp_code
            )
            flash(
                "Brevo rejected SMTP authentication. Verify SMTP_USERNAME and "
                "SMTP_PASSWORD in .env.production; no email was sent.",
                "error",
            )
            return _render_auth("forgot", 502)
        except smtplib.SMTPRecipientsRefused:
            _delete_password_reset(token_hash)
            current_app.logger.error("Brevo rejected the password-reset recipient")
            flash(
                "Brevo rejected the recipient. Check the account email and SMTP setup.",
                "error",
            )
            return _render_auth("forgot", 502)
        except smtplib.SMTPDataError as exc:
            _delete_password_reset(token_hash)
            current_app.logger.error(
                "Brevo rejected the reset email (code %s)", exc.smtp_code
            )
            flash(
                "Brevo rejected the email. Verify MAIL_FROM_ADDRESS and confirm "
                "that sender is verified in Brevo; no email was sent.",
                "error",
            )
            return _render_auth("forgot", 502)
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            _delete_password_reset(token_hash)
            current_app.logger.error(
                "Brevo SMTP password-reset delivery failed (%s)",
                type(exc).__name__,
            )
            flash(
                "The reset email could not be delivered. Check SMTP connectivity, "
                "credentials, and Brevo sender verification.",
                "error",
            )
            return _render_auth("forgot", 502)

    flash("If an account matches that email, a reset link has been sent.", "success")
    return _render_auth("forgot")


@autonix_auth.route("/password/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = _database()
    try:
        reset = connection.execute(
            "SELECT user_id FROM autonix_password_resets "
            "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            (token_hash, _timestamp()),
        ).fetchone()
    finally:
        connection.close()
    if not reset:
        flash("This password reset link is invalid or expired.", "error")
        return redirect(url_for("autonix_auth.forgot_password"))
    if request.method == "GET":
        return _render_auth("reset")
    if not _valid_csrf():
        return _render_auth("reset", 400)

    password = request.form.get("password", "")
    confirmation = request.form.get("confirm_password", "")
    if len(password) < 12 or password != confirmation:
        flash(
            "Use matching passwords with at least 12 characters.",
            "error",
        )
        return _render_auth("reset", 400)

    connection = _database()
    user = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        reset = connection.execute(
            "SELECT user_id FROM autonix_password_resets "
            "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            (token_hash, _timestamp()),
        ).fetchone()
        if not reset:
            connection.rollback()
            flash("This password reset link is invalid or expired.", "error")
            return redirect(url_for("autonix_auth.forgot_password"))
        user = connection.execute(
            "SELECT full_name, email FROM autonix_users WHERE id = ?",
            (reset["user_id"],),
        ).fetchone()
        if not user:
            connection.rollback()
            flash("This password reset link is invalid or expired.", "error")
            return redirect(url_for("autonix_auth.forgot_password"))
        connection.execute(
            "UPDATE autonix_users SET password_hash = ?, auth_provider = "
            "CASE WHEN google_id IS NULL THEN 'email' ELSE 'email+google' END "
            "WHERE id = ?",
            (_password_hash(password), reset["user_id"]),
        )
        connection.execute(
            "UPDATE autonix_password_resets SET used_at = ? WHERE token_hash = ?",
            (_timestamp(), token_hash),
        )
        connection.execute(
            "DELETE FROM autonix_password_resets WHERE user_id = ? "
            "AND token_hash != ?",
            (reset["user_id"], token_hash),
        )
        connection.commit()
    finally:
        connection.close()

    if not _send_password_changed_email(user):
        flash(
            "Your password was changed, but the security notification email could not be sent.",
            "warning",
        )
    flash("Your password has been updated. Please sign in.", "success")
    return redirect(url_for("autonix_auth.login"))


@autonix_auth.route("/email/verify/<token>")
def verify_email(token):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = _database()
    try:
        connection.execute("BEGIN IMMEDIATE")
        verification = connection.execute(
            "SELECT user_id FROM autonix_email_verifications "
            "WHERE token_hash = ? AND expires_at > ?",
            (token_hash, _timestamp()),
        ).fetchone()
        if not verification:
            connection.rollback()
            flash("This verification link is invalid or expired.", "error")
            return redirect(url_for("autonix_auth.login"))
        connection.execute(
            "UPDATE autonix_users SET email_verified = 1 "
            "WHERE id = ? AND status = 'active'",
            (verification["user_id"],),
        )
        connection.execute(
            "DELETE FROM autonix_email_verifications WHERE user_id = ?",
            (verification["user_id"],),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.close()

    flash("Your email address has been verified.", "success")
    return redirect(
        "/dashboard" if get_current_autonix_user() else url_for("autonix_auth.login")
    )


@autonix_auth.route("/email/verification/resend", methods=["POST"])
def resend_email_verification():
    user = get_current_autonix_user()
    if not user:
        return redirect(url_for("autonix_auth.login", next="/dashboard"))
    if not _valid_csrf():
        return jsonify({"error": "csrf_validation_failed"}), 400
    if user["email_verified"]:
        flash("Your email address is already verified.", "success")
        return redirect("/dashboard#account-profile")

    try:
        _smtp_settings()
    except ValueError as exc:
        current_app.logger.error("Email verification is not configured: %s", exc)
        flash("Email verification is not configured: " + str(exc), "error")
        return redirect("/dashboard#account-profile")

    try:
        sent = _issue_email_verification(user)
    except ValueError as exc:
        sent = _email_delivery_failed("Email verification", exc)
    if sent:
        flash("A new verification link has been sent to your email.", "success")
    else:
        flash("The verification email could not be sent. Please try again later.", "error")
    return redirect("/dashboard#account-profile")


@autonix_auth.route("/account/logout", methods=["POST"])
def logout():
    if not _valid_csrf():
        return jsonify({"error": "csrf_validation_failed"}), 400
    cleanup_deriv_session = current_app.config.get("AUTONIX_DERIV_SESSION_CLEANUP")
    if callable(cleanup_deriv_session):
        cleanup_deriv_session()
    session.clear()
    return redirect("/")


@autonix_auth.route("/account/profile")
def profile():
    user = get_current_autonix_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(
        {
            "id": user["id"],
            "full_name": user["full_name"],
            "email": user["email"],
            "auth_provider": user["auth_provider"],
            "created_at": user["created_at"],
            "last_login": user["last_login"],
            "status": user["status"],
            "email_verified": bool(user["email_verified"]),
            "deriv_connected": bool(user["deriv_connected"]),
            "deriv_accounts": user["deriv_accounts"],
        }
    )


@autonix_auth.route("/account/profile/update", methods=["POST"])
def update_profile():
    user = get_current_autonix_user()
    if not user:
        return redirect(url_for("autonix_auth.login", next="/dashboard#account-profile"))
    if not _valid_csrf():
        flash("Your request could not be verified. Please try again.", "error")
        return redirect("/dashboard#account-profile")

    full_name = request.form.get("full_name", "").strip()
    if not full_name or len(full_name) > 200:
        flash("Enter a name between 1 and 200 characters.", "error")
        return redirect("/dashboard#account-profile")

    connection = _database()
    try:
        cursor = connection.execute(
            "UPDATE autonix_users SET full_name = ? "
            "WHERE id = ? AND status = 'active'",
            (full_name, user["id"]),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            flash("Your profile could not be updated. Please sign in again.", "error")
            return redirect("/dashboard#account-profile")
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        current_app.logger.exception("Autonix profile name update failed")
        flash("Your profile could not be saved. Please try again.", "error")
        return redirect("/dashboard#account-profile")
    finally:
        connection.close()

    flash("Your profile name was updated.", "success")
    return redirect("/dashboard#account-profile")
