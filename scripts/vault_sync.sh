#!/bin/bash
# Sync vault changes to GitHub (pull → commit if changed → push)
set -e

REPO="/home/brain/projects/obsidian-vault"
cd "$REPO"

# Pull remote changes first (autostash handles any local uncommitted changes)
git pull --rebase --autostash --quiet origin main 2>&1 || {
  echo "Pull failed — skipping push to avoid conflict"
  exit 1
}

# Check if vault has any changes
if git diff --quiet HEAD && git diff --cached --quiet && \
   [ -z "$(git ls-files --others --exclude-standard)" ]; then
  echo "No vault changes to commit"
  exit 0
fi

# Stage all changes (whole vault repo)
git add .

# Commit
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
git commit -m "vault: auto-sync $TIMESTAMP"

# Push
git push origin main --quiet
echo "Pushed vault changes at $TIMESTAMP"
