"""Project-local hook for CureFlow's ``workflow`` package.

The build environment can also contain an unrelated PyPI distribution named
``workflow``. Its contributed PyInstaller hook calls ``copy_metadata`` for
that distribution and fails when CureFlow's source package wins import
resolution. This higher-priority hook intentionally performs no metadata copy;
CureFlow's workflow package contains only Python modules.
"""

hiddenimports = []
