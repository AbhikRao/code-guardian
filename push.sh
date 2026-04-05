#!/bin/zsh
# push.sh — run this once you have a classic token
# Usage: ./push.sh ghp_yourclassictokenhere

TOKEN=$1
if [ -z "$TOKEN" ]; then
  echo "Usage: ./push.sh <github_classic_token>"
  exit 1
fi

cd /Users/abhik/Downloads/code-guardian
git remote remove origin 2>/dev/null
git remote add origin https://AbhikRao:${TOKEN}@github.com/AbhikRao/code-guardian.git
git push origin main --force
echo "Done! Check https://github.com/AbhikRao/code-guardian"
