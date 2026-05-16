# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Paul (nfbr.net)
"""Single source of truth for app metadata.

Everything that shows the version - the window title, About dialog,
PyInstaller version file, README, CHANGELOG - reads from here.
"""

APP_NAME = "WS500 Util"
VERSION = "1.0.0"
AUTHOR = "Paul (nfbr.net)"
COPYRIGHT = "Copyright (c) 2026 Paul (nfbr.net)"
LICENSE = "GPL-3.0-or-later"
URL = ""  # set when a public repo URL is available

# The Wakespeed Communications and Configuration Guide version this app's
# schema and field semantics target. Bump alongside ws_schema.json if/when
# Wakespeed publishes a newer guide.
GUIDE_VERSION = "2.6.1"
GUIDE_TITLE = "Wakespeed Communications and Configuration Guide"
