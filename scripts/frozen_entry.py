#!/usr/bin/env python3
"""Entry point used for frozen builds (PyInstaller / standalone bundle).

The frozen executable behaves exactly like the ``music-rpc`` command line tool:

    music-rpc                      run the daemon
    music-rpc run --background     start quietly (autostart)
    music-rpc native-host <origin> browser native messaging host
    music-rpc install / doctor / dump / status / settings / version
"""
from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    from musicrpc.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    # Required so that a frozen build never re-executes itself uncontrolled.
    multiprocessing.freeze_support()
    sys.exit(main())
