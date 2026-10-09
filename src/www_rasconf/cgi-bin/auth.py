"""Session/PIN authentication helpers for the Rasconf dashboard."""

import hashlib
import os


def load_secret_hash(hashpath, default_hash):
    """Read the stored PIN hash, seeding the file with the default on first run."""
    if not os.path.exists(hashpath):
        with open(hashpath, "w") as file:
            file.write(default_hash)
    with open(hashpath) as file:
        return file.readline().strip()


def check_auth(params, cookie, secret_hash):
    """Verify session cookie or a PIN login attempt.

    Returns (is_authenticated, should_issue_new_cookie).
    """
    submitted_pin = params.get("pin", [""])[0]

    if submitted_pin:
        pin_hash = hashlib.sha256(submitted_pin.encode("utf-8")).hexdigest()
        if pin_hash == secret_hash:
            return True, True

    if "session" in cookie and cookie["session"].value.strip() == secret_hash:
        return True, False

    return False, False
