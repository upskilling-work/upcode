#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/upskilling-work/upcode.git"
INSTALL_DIR="${UPCODE_INSTALL_DIR:-$HOME/.upcode}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { printf "${GREEN}[upcode]${NC} %s\n" "$*"; }
warn()    { printf "${YELLOW}[upcode]${NC} %s\n" "$*"; }
error()   { printf "${RED}[upcode] error:${NC} %s\n" "$*" >&2; exit 1; }

check_git() {
    if ! command -v git &>/dev/null; then
        error "git is required but not installed. Install it from https://git-scm.com and re-run."
    fi
    info "git found: $(git --version)"
}

PYTHON_BIN=""

check_python() {
    if command -v python3 &>/dev/null; then
        PYTHON_BIN="python3"
    elif command -v python &>/dev/null && python --version 2>&1 | grep -q "^Python 3"; then
        PYTHON_BIN="python"
    else
        error "Python 3 is required but not found. Install it from https://python.org and re-run."
    fi

    local ver
    ver=$("$PYTHON_BIN" -c "import sys; print('%d.%d' % sys.version_info[:2])")
    local major minor
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)

    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 9 ]; }; then
        error "Python 3.9+ is required. Found Python $ver."
    fi

    info "Python found: $("$PYTHON_BIN" --version)"
}

clone_or_update() {
    if [ -d "$INSTALL_DIR/.git" ]; then
        warn "Directory $INSTALL_DIR already exists — pulling latest changes."
        git -C "$INSTALL_DIR" pull --ff-only
    else
        info "Cloning upcode into $INSTALL_DIR ..."
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
}

install_deps() {
    local venv="$INSTALL_DIR/.venv"
    info "Creating virtual environment at $venv ..."
    "$PYTHON_BIN" -m venv "$venv"
    info "Installing Python dependencies ..."
    "$venv/bin/pip" install --quiet --upgrade pip
    "$venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
}

install_launcher() {
    local bin_dir="$HOME/.local/bin"
    local launcher="$bin_dir/upcode"

    mkdir -p "$bin_dir"

    cat > "$launcher" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$INSTALL_DIR"
exec "$INSTALL_DIR/.venv/bin/python" -m cowork.tui "\$@"
EOF
    chmod +x "$launcher"
    info "Launcher created at $launcher"

    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) warn "$bin_dir is not in your PATH. Add this to your shell profile:" ;;
    esac
    case ":$PATH:" in
        *":$bin_dir:"*) ;;
        *) printf '  export PATH="%s:$PATH"\n' "$bin_dir" ;;
    esac
}

setup_env() {
    local env_file="$INSTALL_DIR/.env"
    if [ ! -f "$env_file" ]; then
        if [ -f "$INSTALL_DIR/.env.example" ]; then
            cp "$INSTALL_DIR/.env.example" "$env_file"
            warn ".env created from .env.example — edit $env_file to add your API keys."
        fi
    else
        info ".env already exists, skipping."
    fi
}

print_usage() {
    printf "\n${GREEN}Installation complete!${NC}\n\n"
    printf "Run upcode:\n"
    printf "  upcode\n\n"
    printf "Edit %s/.env to configure your API keys.\n\n" "$INSTALL_DIR"
}

main() {
    info "Installing upcode ..."
    check_git
    check_python
    clone_or_update
    install_deps
    install_launcher
    setup_env
    print_usage
}

main "$@"
