"""Điều khiển trình duyệt thật (Playwright) bằng phiên đã đăng nhập sẵn.

Playwright không bắt buộc: chỉ được import khi thực sự mở trình duyệt.
Cài bằng::

    pip install playwright
    playwright install chrome
"""

from .session import (
    DEFAULT_LOGIN_SELECTORS,
    DEFAULT_LOGIN_TEXTS,
    BrowserSession,
    BrowserUnavailable,
    LoginTimeout,
    default_profile_dir,
    is_logged_in,
    open_shop,
    wait_for_login,
)

__all__ = [
    "DEFAULT_LOGIN_SELECTORS",
    "DEFAULT_LOGIN_TEXTS",
    "BrowserSession",
    "BrowserUnavailable",
    "LoginTimeout",
    "default_profile_dir",
    "is_logged_in",
    "open_shop",
    "wait_for_login",
]
