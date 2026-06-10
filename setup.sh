#!/bin/bash

# =============================================================================
# Agent Second Brain - VPS Setup Script (v3.0)
# =============================================================================
# Interactive first-time setup: installs dependencies, clones your fork,
# asks for tokens, then delegates the heavy lifting (systemd units, brain
# session, health check) to upgrade.sh — the same script that migrates
# existing installs. One source of truth, no drift.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/smixs/agent-second-brain/main/bootstrap.sh | bash
# Or: bash setup.sh
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

CHECK="[OK]"
CROSS="[X]"
WARN="[!]"
ARROW="-->"
GEAR="[*]"

print_banner() {
    echo ""
    echo -e "${PURPLE}${BOLD}"
    echo "  ╔═══════════════════════════════════════════════════════════╗"
    echo "  ║                                                           ║"
    echo "  ║          AGENT SECOND BRAIN - VPS SETUP (v3.0)            ║"
    echo "  ║                                                           ║"
    echo "  ║   Always-on agent with long-term memory                   ║"
    echo "  ║                                                           ║"
    echo "  ╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo ""
}

info()    { echo -e "${BLUE}${GEAR}${NC} $1"; }
success() { echo -e "${GREEN}${CHECK}${NC} $1"; }
warn()    { echo -e "${YELLOW}${WARN}${NC} $1"; }
error()   { echo -e "${RED}${CROSS}${NC} $1"; }
ask()     { echo -e "${YELLOW}?${NC} $1"; }

step() {
    echo ""
    echo -e "${CYAN}${BOLD}${ARROW} $1${NC}"
    echo -e "${CYAN}$(printf '%.0s─' {1..60})${NC}"
}

# =============================================================================
# Validation
# =============================================================================

validate_telegram_token() {
    [[ $1 =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]
}

validate_telegram_id() {
    [[ $1 =~ ^[0-9]+$ ]] && [ "$1" -gt 0 ]
}

validate_deepgram_key() {
    [[ $1 =~ ^[A-Za-z0-9]+$ ]] && [ ${#1} -ge 20 ]
}

# =============================================================================
# Checks
# =============================================================================

check_root() {
    if [ "$EUID" -eq 0 ]; then
        error "Do not run this script as root!"
        echo "  Create a regular user first: adduser myuser && usermod -aG sudo myuser"
        echo "  Then run: su - myuser && bash setup.sh"
        exit 1
    fi
}

check_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
            warn "This script is tested on Ubuntu/Debian. You're running: $ID"
            read -p "Continue anyway? (y/N): " -r REPLY
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
        fi
    fi
}

check_command() {
    command -v "$1" &> /dev/null
}

# =============================================================================
# Installation
# =============================================================================

install_system_deps() {
    step "Installing system dependencies"
    info "Updating package list..."
    sudo apt-get update -qq
    info "Installing git, curl, wget, tmux..."
    sudo apt-get install -y -qq git curl wget tmux
    success "System dependencies installed"
}

install_uv() {
    step "Installing uv (Python package manager)"
    if check_command uv || [ -x "$HOME/.local/bin/uv" ]; then
        success "uv already installed"
        return
    fi
    info "Downloading and installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    fi
    # uv manages its own CPython (requires-python from pyproject) — no
    # system Python or PPA needed.
    success "uv installed"
}

install_nodejs() {
    step "Installing Node.js 20 (required by Claude Code)"
    if check_command node; then
        NODE_VERSION=$(node --version | cut -d'v' -f2 | cut -d'.' -f1)
        if [ "$NODE_VERSION" -ge 18 ]; then
            success "Node.js $(node --version) already installed"
            return
        fi
    fi
    info "Adding NodeSource repository..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    info "Installing Node.js..."
    sudo apt-get install -y -qq nodejs
    success "Node.js $(node --version) installed"
}

install_claude_cli() {
    step "Installing Claude Code"
    if check_command claude; then
        success "Claude Code already installed: $(claude --version 2>/dev/null || echo 'version unknown')"
        return
    fi
    info "Installing @anthropic-ai/claude-code globally..."
    sudo npm install -g @anthropic-ai/claude-code
    success "Claude Code installed"
}

# =============================================================================
# Configuration
# =============================================================================

clone_repository() {
    step "Setting up project"

    PROJECTS_DIR="$HOME/projects"
    PROJECT_DIR="$PROJECTS_DIR/agent-second-brain"
    mkdir -p "$PROJECTS_DIR"

    if [ -d "$PROJECT_DIR" ]; then
        warn "Project directory already exists: $PROJECT_DIR"
        read -p "Remove and re-clone? (y/N): " -r REPLY
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$PROJECT_DIR"
        else
            cd "$PROJECT_DIR"
            success "Using existing directory"
            return
        fi
    fi

    ask "Enter your GitHub username (the one where you forked the repo):"
    read -r GITHUB_USER
    if [ -z "$GITHUB_USER" ]; then
        error "GitHub username cannot be empty"
        exit 1
    fi

    REPO_URL="https://github.com/$GITHUB_USER/agent-second-brain.git"
    info "Cloning from $REPO_URL..."
    if ! git clone "$REPO_URL" "$PROJECT_DIR" 2>/dev/null; then
        error "Failed to clone repository!"
        echo "  Make sure you've forked the repo and the username is correct"
        exit 1
    fi

    cd "$PROJECT_DIR"
    success "Repository cloned to $PROJECT_DIR"
    echo "$GITHUB_USER" > .github_user
}

collect_tokens() {
    step "Collecting API tokens"
    echo ""
    echo "You'll need these tokens (get them from the services):"
    echo "  - Telegram Bot Token (from @BotFather)"
    echo "  - Your Telegram ID (from @userinfobot)"
    echo "  - Deepgram API Key (from console.deepgram.com)"
    echo ""

    while true; do
        ask "Telegram Bot Token (from @BotFather):"
        read -r TELEGRAM_BOT_TOKEN
        if validate_telegram_token "$TELEGRAM_BOT_TOKEN"; then
            success "Token format valid"
            break
        fi
        error "Invalid token format. Should be like: 123456789:ABC-DEF1234ghIkl-zyx57W2v"
    done

    while true; do
        ask "Your Telegram User ID (from @userinfobot):"
        read -r TELEGRAM_USER_ID
        if validate_telegram_id "$TELEGRAM_USER_ID"; then
            success "User ID valid"
            break
        fi
        error "Invalid User ID. Should be a number like: 123456789"
    done

    while true; do
        ask "Deepgram API Key (from console.deepgram.com):"
        read -r DEEPGRAM_API_KEY
        if validate_deepgram_key "$DEEPGRAM_API_KEY"; then
            success "API Key format valid"
            break
        fi
        error "Invalid API key format. Should be alphanumeric, 20+ characters"
    done

    ask "Your timezone for reports and schedules (Enter for UTC, e.g. Asia/Tashkent):"
    read -r USER_TZ
    USER_TZ="${USER_TZ:-UTC}"
    success "Timezone: $USER_TZ"
}

create_env_file() {
    step "Creating .env file"

    ENV_FILE="$PROJECT_DIR/.env"
    if [ -f "$ENV_FILE" ]; then
        warn ".env file already exists"
        read -p "Overwrite? (y/N): " -r REPLY
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            success "Keeping existing .env"
            return
        fi
    fi

    cat > "$ENV_FILE" << EOF
# Telegram Bot API token from @BotFather
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN

# Deepgram API key for voice transcription
DEEPGRAM_API_KEY=$DEEPGRAM_API_KEY

# Path to Obsidian vault directory
VAULT_PATH=./vault

# JSON array of Telegram user IDs allowed to use the bot.
# The FIRST id also receives health alerts / daily reports.
ALLOWED_USER_IDS=[$TELEGRAM_USER_ID]

# Timezone for timers, reports and schedules
TZ=$USER_TZ
EOF

    chmod 600 "$ENV_FILE"
    success ".env file created (permissions: 600)"
}

configure_git_remote() {
    step "Configuring Git for push access"

    cd "$PROJECT_DIR"
    git config user.name "Agent Second Brain Bot"
    git config user.email "bot@localhost"

    ask "Do you want to configure GitHub push access? (for auto-sync of your vault)"
    read -p "(y/N): " -r REPLY
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        warn "Skipping GitHub push configuration"
        return
    fi

    echo ""
    echo "Create a Personal Access Token on GitHub:"
    echo "  1. Go to: github.com > Settings > Developer settings > Personal access tokens > Tokens (classic)"
    echo "  2. Generate new token with 'repo' scope"
    echo "  3. Copy the token"
    echo ""

    ask "Enter your GitHub Personal Access Token:"
    read -rs GITHUB_TOKEN
    echo ""
    if [ -z "$GITHUB_TOKEN" ]; then
        warn "No token provided, skipping"
        return
    fi

    GITHUB_USER=$(cat .github_user 2>/dev/null || echo "")
    if [ -z "$GITHUB_USER" ]; then
        ask "Enter your GitHub username:"
        read -r GITHUB_USER
    fi

    git remote set-url origin "https://$GITHUB_TOKEN@github.com/$GITHUB_USER/agent-second-brain.git"
    success "Git remote configured for push"
}

authorize_claude() {
    step "Claude Code authorization"

    # The persistent brain session runs on your Claude subscription —
    # it cannot start without a logged-in Claude Code.
    if claude auth status --json 2>/dev/null | grep -q '"loggedIn": *true'; then
        success "Claude Code already authorized"
        return
    fi

    warn "Claude Code is not logged in — the brain session needs it."
    echo ""
    echo "Open a SECOND terminal on this server and run:"
    echo -e "  ${CYAN}claude${NC}"
    echo "Complete the login it offers (subscription account), then exit claude."
    echo ""
    while true; do
        read -p "Press Enter when done (or type 'skip' to continue without login): " -r REPLY
        if [[ $REPLY == "skip" ]]; then
            warn "Continuing without login — the bot will alert until you log in."
            return
        fi
        if claude auth status --json 2>/dev/null | grep -q '"loggedIn": *true'; then
            success "Claude Code authorized"
            return
        fi
        error "Still not logged in. Try again (or type 'skip')."
    done
}

run_upgrade() {
    step "Installing services and starting the agent (upgrade.sh)"
    # All the heavy lifting lives in upgrade.sh — the SAME script that
    # migrates older installs: uv sync, dbrain-* systemd --user units,
    # linger, dbrain CLI, claude-p guard, first health check.
    bash "$PROJECT_DIR/upgrade.sh"
}

print_outro() {
    echo ""
    echo -e "${GREEN}${BOLD}"
    echo "  ╔═══════════════════════════════════════════════════════════╗"
    echo "  ║                                                           ║"
    echo "  ║                    SETUP COMPLETE!                        ║"
    echo "  ║                                                           ║"
    echo "  ╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo ""
    echo "  Next steps:"
    echo "    1. Open Telegram and find your bot"
    echo "    2. Send /start to test"
    echo "    3. Send a voice message!"
    echo "    4. Try: \"remind me in 10 minutes to stretch\""
    echo ""
    echo "  Useful commands:"
    echo "    - Status:       dbrain status"
    echo "    - View logs:    journalctl --user -u dbrain-bot -f"
    echo "    - Restart bot:  systemctl --user restart dbrain-bot"
    echo ""
}

# =============================================================================
# Main
# =============================================================================

main() {
    print_banner

    check_root
    check_os

    echo "This script will:"
    echo "  1. Install required software (uv, Node.js, tmux, Claude Code)"
    echo "  2. Clone your fork of the repository"
    echo "  3. Ask for your API tokens"
    echo "  4. Set up the always-on agent (systemd --user + tmux brain)"
    echo ""

    read -p "Ready to start? (Y/n): " -r REPLY
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo "Setup cancelled."
        exit 0
    fi

    install_system_deps
    install_uv
    install_nodejs
    install_claude_cli

    clone_repository
    collect_tokens
    create_env_file
    configure_git_remote
    authorize_claude

    run_upgrade
    print_outro
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
