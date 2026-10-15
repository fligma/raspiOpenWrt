#!/usr/bin/env python3
"""The application's own modules, kept apart from the contract addons import.

``addon_api``, ``addon_client`` and ``addon_common`` stay in the program's
folder, because a compiled addon imports them by name and will go on doing it
long after this folder is rearranged. Everything the window itself is made of
lives here, one file per layer, and each reads only the ones above it:

* :mod:`core.const` - the paths, the version and the shape of the shipped
  look, with no Qt in it, so the Qt-free half of the contract can read it;
* :mod:`core.ui` - the theme, the settings file, the widgets and the boot card;
* :mod:`core.net` - the SSH workers, the remote commands, the terminal screen;
* :mod:`core.views` - the three tabs a device is looked at through;
* :mod:`core.addon` - finding and running addons, and building a new one;
* :mod:`core.window` - the window, which composes all of the above.

Nothing is re-exported here, and no module of this package is imported by a
name built at runtime: every one is reached by a plain ``import``, which is
how a build finds all of them without being told what they are.
"""
