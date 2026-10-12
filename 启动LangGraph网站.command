#!/bin/zsh
set -e
cd -- "${0:A:h}"
exec .venv/bin/python main.py --port 8894
