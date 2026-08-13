"""Run the local Streamlit dashboard with a Windows WMI import workaround.

On this machine, Python's platform.system() can hang inside a WMI query while
Streamlit imports. Streamlit only needs the OS family here, so we pin it before
loading Streamlit's CLI.
"""

from __future__ import annotations

import platform
import sys
from collections import namedtuple


def main() -> None:
    uname_result = namedtuple(
        "uname_result", "system node release version machine processor"
    )
    fixed_uname = uname_result("Windows", "localhost", "10", "10", "AMD64", "AMD64")

    # Avoid Python's Windows WMI lookup. On this machine that lookup can hang,
    # and both Streamlit and SQLAlchemy ask platform for OS metadata on import.
    platform.system = lambda: fixed_uname.system  # type: ignore[assignment]
    platform.machine = lambda: fixed_uname.machine  # type: ignore[assignment]
    platform.processor = lambda: fixed_uname.processor  # type: ignore[assignment]
    platform.uname = lambda: fixed_uname  # type: ignore[assignment]
    platform.win32_ver = lambda *args, **kwargs: ("10", "", "", "")  # type: ignore[assignment]

    from streamlit.web.cli import main as streamlit_main

    sys.argv = [
        "streamlit",
        "run",
        "dashboard/app.py",
        "--server.port=8502",
        "--server.address=127.0.0.1",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]
    streamlit_main(prog_name="streamlit")


if __name__ == "__main__":
    main()
