# Conventions (always applies)

- Code, comments, docstrings, and log messages are in **English**.
- **Type hints are mandatory** and **Google-style docstrings are required** on every module, function, and class (Ruff `D` rules + mypy enforce this; `tests/` are exempt from docstring rules). Docstrings describe the *meaning* of arguments, not their types.
- **Logging**: `logger = logging.getLogger(__name__)`, `%`-style lazy args (never f-strings in log calls), never `print`. INFO = milestones, DEBUG = everything our code does.
- Comments explain *why*, not *what* (MIT Comm Lab style).
- `app/core/` is the bottom layer: it provides config, constants, and logging and must **not** import from the rest of `app/`. Everything else may import from `app/core/`.
- Dependencies in `requirements.txt` are pinned (`==`); bumping a pin means running pip-audit and the test suite.
