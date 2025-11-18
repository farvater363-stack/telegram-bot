#!/bin/bash
set -euo pipefail

# always run from the script directory
cd "$(dirname "$0")"

# ensure pyenv + Python 3.11.9 are available
export PYENV_ROOT="$HOME/.pyenv"
if [ ! -d "$PYENV_ROOT" ]; then
  curl https://pyenv.run | bash
fi
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"

PY_VERSION="3.11.9"
if ! pyenv versions --bare | grep -qx "$PY_VERSION"; then
  pyenv install "$PY_VERSION"
fi
pyenv shell "$PY_VERSION"

# fresh virtualenv + deps
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

exec python bot.py
