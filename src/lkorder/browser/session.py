"""Phiên trình duyệt dùng lại cookie/đăng nhập của người dùng.

Cách hoạt động:

* Mở Chrome với *persistent profile* đặt ở `data/browser_profile`. Mọi cookie,
  phiên đăng nhập được lưu lại trong thư mục này giữa các lần chạy.
* Luôn hiện cửa sổ (không headless) để người dùng tự đăng nhập lần đầu —
  công cụ không bao giờ hỏi hay lưu mật khẩu.
* `open_shop(url)` mở trang shop, dò xem đã đăng nhập chưa (có nút "Đăng
  xuất", tên tài khoản...). Nếu chưa thì chờ người dùng đăng nhập xong trong
  cửa sổ vừa mở rồi mới trả trang về.

Playwright chỉ được import khi cần (import lười), nên phần còn lại của
`lkorder` vẫn chạy được khi chưa cài.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import project_root

# Phần tử CSS thường chỉ xuất hiện khi đã đăng nhập.
DEFAULT_LOGIN_SELECTORS: tuple[str, ...] = (
    "a[href*='logout']",
    "a[href*='dang-xuat']",
    "a[href*='signout']",
    "a[href*='sign-out']",
    "form[action*='logout']",
    "[data-testid='account-name']",
    ".customer-name",
    ".account-name",
)

# Chữ (so khớp không phân biệt hoa thường) chỉ hiện khi đã đăng nhập.
DEFAULT_LOGIN_TEXTS: tuple[str, ...] = (
    "đăng xuất",
    "thoát tài khoản",
    "log out",
    "logout",
    "sign out",
    "xin chào",
    "tài khoản của tôi",
)

DEFAULT_LOGIN_TIMEOUT = 300.0   # giây chờ người dùng đăng nhập
DEFAULT_POLL_INTERVAL = 2.0     # giây giữa hai lần dò

INSTALL_HINT = (
    "Chưa cài Playwright nên không mở được trình duyệt.\n"
    "Cài bằng:\n"
    "    pip install playwright\n"
    "    playwright install chrome"
)


class BrowserUnavailable(RuntimeError):
    """Không mở được trình duyệt (thiếu Playwright hoặc thiếu Chrome)."""


class LoginTimeout(TimeoutError):
    """Hết thời gian chờ mà người dùng vẫn chưa đăng nhập."""


def default_profile_dir() -> Path:
    return project_root() / "data" / "browser_profile"


def _load_playwright() -> Callable[[], Any]:
    """Trả về `sync_playwright`; báo lỗi rõ ràng nếu chưa cài."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise BrowserUnavailable(INSTALL_HINT) from e
    return sync_playwright


# ------------------------------------------------------------ dò đăng nhập


def is_logged_in(page: Any,
                 selectors: Iterable[str] = DEFAULT_LOGIN_SELECTORS,
                 texts: Iterable[str] = DEFAULT_LOGIN_TEXTS) -> bool:
    """Trang hiện tại có dấu hiệu đã đăng nhập không.

    Lỗi khi dò (trang đang chuyển hướng, bị đóng...) được coi là "chưa".
    """
    for sel in selectors:
        try:
            if page.query_selector(sel) is not None:
                return True
        except Exception:
            continue
    try:
        body = (page.inner_text("body") or "").casefold()
    except Exception:
        return False
    return any(t.casefold() in body for t in texts)


def _safe_check(check: Callable[[Any], bool], page: Any) -> bool:
    try:
        return bool(check(page))
    except Exception:
        return False


def describe_login_signs(selectors: Iterable[str] = DEFAULT_LOGIN_SELECTORS,
                         texts: Iterable[str] = DEFAULT_LOGIN_TEXTS) -> str:
    """Mô tả các dấu hiệu mặc định dùng để dò đăng nhập (cho thông báo lỗi)."""
    parts = []
    if selectors:
        parts.append("phần tử " + ", ".join(selectors))
    if texts:
        parts.append("chữ " + ", ".join(f"'{t}'" for t in texts))
    return " hoặc ".join(parts) or "(không có)"


def wait_for_login(page: Any,
                   timeout: float = DEFAULT_LOGIN_TIMEOUT,
                   poll: float = DEFAULT_POLL_INTERVAL,
                   selectors: Iterable[str] = DEFAULT_LOGIN_SELECTORS,
                   texts: Iterable[str] = DEFAULT_LOGIN_TEXTS,
                   sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic,
                   notify: Callable[[str], None] | None = print,
                   login_check: Callable[[Any], bool] | None = None,
                   login_signs: str | None = None) -> None:
    """Chờ tới khi trang có dấu hiệu đã đăng nhập, quá `timeout` thì báo lỗi.

    `login_check(page) -> bool` (của adapter shop) thay cho cách dò chung
    bằng `selectors`/`texts`; `login_signs` mô tả nó trong thông báo lỗi.
    """
    selectors, texts = tuple(selectors), tuple(texts)
    if login_check is not None:
        def check() -> bool:
            return _safe_check(login_check, page)
        signs = login_signs or getattr(login_check, "__name__", "login_check")
    else:
        def check() -> bool:
            return is_logged_in(page, selectors, texts)
        signs = login_signs or describe_login_signs(selectors, texts)
    if check():
        return
    if notify:
        notify("Chưa đăng nhập. Hãy đăng nhập trong cửa sổ trình duyệt vừa mở; "
               "công cụ sẽ tự tiếp tục khi xong.")
    deadline = clock() + timeout
    while clock() < deadline:
        sleep(poll)
        if check():
            if notify:
                notify("Đã đăng nhập, tiếp tục.")
            return
    raise LoginTimeout(f"Quá {timeout:.0f} giây mà vẫn chưa đăng nhập "
                       f"(đã dò: {signs}).")


def login_options_for(shop_url: str) -> dict[str, Any]:
    """`login_check`/`login_signs` của adapter shop (nếu có) cho `open_shop`.

    Shop không có adapter hoặc adapter không có `login_check` -> {} (dùng cách
    dò chung).
    """
    from .shops import filler_class_for

    cls = filler_class_for(shop_url or "")
    check = getattr(cls, "login_check", None) if cls else None
    if not callable(check):
        return {}
    return {"login_check": check,
            "login_signs": getattr(cls, "login_signs", None)}


# ------------------------------------------------------------ phiên


class BrowserSession:
    """Một cửa sổ Chrome dùng profile lưu sẵn. Dùng được với `with`."""

    def __init__(self, profile_dir: str | Path | None = None,
                 channel: str | None = "chrome",
                 headless: bool = False,
                 playwright_factory: Callable[[], Any] | None = None) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else default_profile_dir()
        self.channel = channel
        self.headless = headless
        self._factory = playwright_factory
        self._pw_cm: Any = None
        self._pw: Any = None
        self.context: Any = None

    # -- vòng đời

    def start(self) -> "BrowserSession":
        if self.context is not None:
            return self
        factory = self._factory or _load_playwright()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw_cm = factory()
        self._pw = self._pw_cm.__enter__()
        kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "no_viewport": True,
        }
        if self.channel:
            kwargs["channel"] = self.channel
        try:
            self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            self._stop_playwright()
            raise BrowserUnavailable(
                f"Không mở được trình duyệt ({self.channel or 'chromium'}): {e}\n"
                "Nếu chưa có Chrome, chạy: playwright install chrome"
            ) from e
        return self

    def _stop_playwright(self) -> None:
        if self._pw_cm is not None:
            try:
                self._pw_cm.__exit__(None, None, None)
            finally:
                self._pw_cm = None
                self._pw = None

    def close(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
        finally:
            self.context = None
            self._stop_playwright()

    def __enter__(self) -> "BrowserSession":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- thao tác

    def new_page(self) -> Any:
        self.start()
        pages = list(getattr(self.context, "pages", None) or [])
        # Profile vừa mở thường có sẵn một tab trắng: dùng luôn cho gọn.
        if pages and pages[0].url in ("", "about:blank"):
            return pages[0]
        return self.context.new_page()

    def open_shop(self, url: str, *, wait_login: bool = True,
                  timeout: float = DEFAULT_LOGIN_TIMEOUT,
                  poll: float = DEFAULT_POLL_INTERVAL,
                  selectors: Iterable[str] = DEFAULT_LOGIN_SELECTORS,
                  texts: Iterable[str] = DEFAULT_LOGIN_TEXTS,
                  login_check: Callable[[Any], bool] | None = None,
                  login_signs: str | None = None,
                  **wait_kwargs: Any) -> Any:
        """Mở `url`; nếu chưa đăng nhập thì chờ người dùng đăng nhập xong.

        `login_check(page) -> bool`: cách dò đăng nhập riêng của shop (nếu có).
        """
        page = self.new_page()
        page.goto(url, wait_until="domcontentloaded")
        if wait_login:
            wait_for_login(page, timeout=timeout, poll=poll,
                           selectors=selectors, texts=texts,
                           login_check=login_check, login_signs=login_signs,
                           **wait_kwargs)
        return page


_default_session: BrowserSession | None = None


def open_shop(url: str, session: BrowserSession | None = None, **kwargs: Any) -> Any:
    """Mở shop bằng phiên dùng chung (tạo khi cần lần đầu) và trả về trang."""
    global _default_session
    if session is None:
        if _default_session is None:
            _default_session = BrowserSession()
        session = _default_session
    return session.open_shop(url, **kwargs)
