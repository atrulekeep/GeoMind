#!/bin/bash

cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

REPO_URL="https://github.com/atrulekeep/GeoMind.git"
SSH_URL="git@github.com:atrulekeep/GeoMind.git"
BRANCH="main"

echo -e "${BLUE}=== GeoMind Publish Script ===${NC}"
echo ""

echo "[1/6] Checking git repository..."
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    echo "Initializing git repository..."
    git init || { echo -e "${RED}ERROR: git init failed${NC}"; exit 1; }
fi
git branch -M "$BRANCH" 2>/dev/null

echo "[2/6] Checking remote origin..."
if git remote get-url origin &>/dev/null; then
    CURRENT_URL="$(git remote get-url origin)"
    if [ "$CURRENT_URL" != "$REPO_URL" ]; then
        echo "Updating origin: $CURRENT_URL -> $REPO_URL"
        git remote set-url origin "$REPO_URL"
    fi
else
    git remote add origin "$REPO_URL"
    echo "Remote origin added: $REPO_URL"
fi

echo "[3/6] Adding all files..."
git add -A

if [ -n "$(git diff --cached --name-only)" ]; then
    echo -e "${YELLOW}Staged files:${NC}"
    git diff --cached --name-status
else
    echo -e "${YELLOW}No staged changes.${NC}"
fi
echo ""

echo "[4/6] Committing changes..."
if ! git config user.name &>/dev/null || ! git config user.email &>/dev/null; then
    echo -e "${RED}ERROR: Git identity is not configured. Run:${NC}"
    echo "  git config --global user.name \"Your Name\""
    echo "  git config --global user.email \"you@example.com\""
    exit 1
fi

if [ -n "$1" ]; then
    COMMIT_MSG="$1"
else
    COMMIT_MSG="Update GeoMind - $(date '+%Y-%m-%d %H:%M:%S')"
fi

if git diff --cached --quiet; then
    echo -e "${YELLOW}✓ No changes to commit${NC}"
else
    if git commit -m "$COMMIT_MSG"; then
        echo -e "${GREEN}✓ Commit successful${NC}"
    else
        echo -e "${RED}✗ Commit failed${NC}"
        exit 1
    fi
fi

echo ""
echo "[5/6] Pushing to GitHub ($BRANCH)..."
if git push -u origin "$BRANCH"; then
    echo -e "${GREEN}✓ Successfully pushed to GitHub${NC}"
else
    echo ""
    echo -e "${RED}✗ Failed to push to GitHub${NC}"
    echo -e "${YELLOW}Possible fixes:${NC}"
    echo "  1. Network/proxy issue - check your proxy or switch network:"
    echo "       git config --global http.proxy http://127.0.0.1:7890"
    echo "       git config --global --unset http.proxy"
    echo "  2. Switch remote to SSH (make sure your SSH key is added to GitHub):"
    echo "       git remote set-url origin $SSH_URL"
    echo "       ssh -T git@github.com"
    echo "  3. Remote has commits you don't have locally - rebase then push:"
    echo "       git pull --rebase origin $BRANCH && git push -u origin $BRANCH"
    echo ""
    AHEAD="$(git rev-list --count origin/$BRANCH..HEAD 2>/dev/null || echo '?')"
    echo -e "${YELLOW}Local commits are kept (ahead of remote: ${AHEAD}). Re-run after fixing the issue.${NC}"
    exit 1
fi

echo ""
echo "[6/6] Done."
echo -e "${GREEN}=== Publish succeeded ===${NC}"
echo "Repository: $REPO_URL"
