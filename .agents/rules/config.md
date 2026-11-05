# Configuration, constants, and core

- Two kinds of values, two places: varies per **environment** (endpoints, keys, log levels) → accessor in `app/core/config.py`; fixed **design choice** (limit, batch size, schedule) → named constant in `app/core/constants.py` with a comment explaining the choice.
- Never read `os.environ` outside `config.py`. New settings get a named accessor: `config.require("NAME")` when security- or correctness-critical (fails fast with `MissingSettingError`), `config.get("NAME", default)` otherwise.
- New-setting checklist — both places, every time:
  1. accessor in `app/core/config.py`;
  2. key with a placeholder value in `.env.example`.
- `app/core/` is the bottom layer: it must not import from the rest of `app/`.
