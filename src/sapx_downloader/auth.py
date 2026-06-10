import os
import re
from pathlib import Path

import requests


BASE_URL = "https://online.coresyssap.com"
CHECK_ROLES_URL = f"{BASE_URL}/user/check_roles/"
CHECK_AUTH_URL = f"{BASE_URL}/user/check_auth_code/"
DO_LOGIN_URL = f"{BASE_URL}/user/do_login"


class LoginError(RuntimeError):
    pass


def load_env_file(env_path: str | Path) -> dict[str, str]:
    path = Path(env_path)
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
        os.environ.setdefault(key.strip(), value.strip())
    return values


def build_authenticated_session(
    username: str,
    password: str,
    pin: str,
    timeout: int = 180,
) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36"
            )
        }
    )
    login(session, username=username, password=password, pin=pin, timeout=timeout)
    return session


def login(
    session: requests.Session,
    username: str,
    password: str,
    pin: str,
    timeout: int = 180,
) -> None:
    if not re.fullmatch(r"\d{6}", pin):
        raise LoginError("PIN must contain exactly 6 digits.")

    role_response = session.post(
        CHECK_ROLES_URL,
        data={"username": username, "password": password},
        timeout=timeout,
    )
    role_response.raise_for_status()
    role_payload = role_response.json()
    if "data" not in role_payload or "username" not in role_payload:
        raise LoginError(f"Unexpected check_roles response: {role_payload}")

    auth_payload = {"username": username, "secrets": ""}
    for index, digit in enumerate(pin, start=1):
        auth_payload[f"pin{index}"] = digit

    auth_response = session.post(CHECK_AUTH_URL, data=auth_payload, timeout=timeout)
    auth_response.raise_for_status()
    auth_payload_result = auth_response.json()
    if auth_payload_result.get("status") != "yes":
        raise LoginError(f"PIN verification failed: {auth_payload_result}")

    final_payload = {
        "username_auth": role_payload["username"],
        "password_auth": role_payload["data"],
    }
    for index, digit in enumerate(pin, start=1):
        final_payload[f"pin{index}"] = digit

    login_response = session.post(DO_LOGIN_URL, data=final_payload, timeout=timeout)
    login_response.raise_for_status()
    if "Log Out" not in login_response.text and "Profile" not in login_response.text:
        raise LoginError("Authenticated page marker not found after login.")
