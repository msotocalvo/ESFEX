"""Module entry point so ``python -m esfex`` works everywhere.

The ``esfex`` console script (see ``[project.scripts]`` in ``pyproject.toml``)
is a launcher pip drops into the environment's ``Scripts``/``bin`` directory.
On Windows that directory is frequently not on ``PATH`` (python.org installer
without "Add Python to PATH", ``--user`` fallback installs, Microsoft Store
Python, …), so ``esfex studio`` fails with "command not found". Running the
package as a module only needs ``python`` itself on ``PATH``, which makes
``python -m esfex studio`` a reliable, PATH-independent fallback.
"""

from esfex.cli import _entrypoint

if __name__ == "__main__":
    _entrypoint()
