#!/bin/bash
# Script to generate requirements.txt from pyproject.toml
# This ensures requirements.txt stays in sync with pyproject.toml

set -e

echo "Generating requirements.txt from pyproject.toml..."

# Install the package in a temporary virtual environment to get exact versions
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

python3 -m venv "$TEMP_DIR/venv"
source "$TEMP_DIR/venv/bin/activate"

# Install from pyproject.toml
pip install --upgrade pip setuptools wheel > /dev/null 2>&1
pip install . > /dev/null 2>&1

# Generate requirements.txt with exact versions
pip freeze | grep -E "(cachetools|google|slack|requests|python-dotenv|certifi|charset|idna|oauthlib|proto|protobuf|pyasn1|pyparsing|rsa|uritemplate|urllib3|httplib2)" > requirements.txt

# Sort and clean up
sort -u requirements.txt -o requirements.txt

echo "? requirements.txt updated successfully"
echo ""
echo "Note: This includes all transitive dependencies. For a minimal requirements.txt"
echo "with only direct dependencies, manually edit pyproject.toml and sync versions."
