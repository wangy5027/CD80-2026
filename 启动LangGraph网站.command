#!/bin/zsh
set -e
cd -- "${0:A:h}"
exec cd80-data-api/.venv/bin/python cd80-data-api/scripts/start_all.py
