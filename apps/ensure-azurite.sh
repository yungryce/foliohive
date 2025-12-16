#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="$SCRIPT_DIR"
REPO_ROOT="$(dirname "$APPS_DIR")"

readonly AZURITE_HOST="127.0.0.1"
readonly AZURITE_PORTS=(10000 10001 10002)
readonly AZURITE_LOCATION="$REPO_ROOT/.azurite"
readonly AZURITE_LOG_DIR="$APPS_DIR/logs"
readonly AZURITE_LOG_FILE="$AZURITE_LOG_DIR/azurite.log"
readonly AZURITE_STARTUP_TIMEOUT=15
readonly AZURITE_PORT_LIST="10000,10001,10002"

log_info()  { echo "[INFO]  $1"; }
log_warn()  { echo "[WARN]  $1"; }
log_error() { echo "[ERROR] $1" >&2; }
log_step()  { echo "[STEP]  $1"; }

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        log_error "Required command '$1' not found in PATH"
        exit 1
    fi
}

is_azurite_port_ready() {
    local port="$1"
    local url
    url="http://${AZURITE_HOST}:${port}/"
    curl -fsS "$url" >/dev/null 2>&1
}

are_azurite_ports_ready() {
    for port in "${AZURITE_PORTS[@]}"; do
        if ! is_azurite_port_ready "$port"; then
            return 1
        fi
    done
    return 0
}

_listening_endpoint_matches_port() {
    local port="$1"
    # Match patterns like 127.0.0.1:10000, 0.0.0.0:10000, [::]:10000, :::10000
    local matcher="(:|\.)${port}(\\s|$)"

    if command -v ss >/dev/null 2>&1; then
        ss -ltn | awk 'NR>1 {print $4}' | grep -E "${matcher}" >/dev/null 2>&1 && return 0
    fi

    if command -v netstat >/dev/null 2>&1; then
        netstat -tln | awk 'NR>2 {print $4}' | grep -E "${matcher}" >/dev/null 2>&1 && return 0
    fi

    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:${port} -sTCP:LISTEN >/dev/null 2>&1 && return 0
    fi

    return 1
}

are_azurite_ports_bound() {
    for port in "${AZURITE_PORTS[@]}"; do
        if _listening_endpoint_matches_port "$port"; then
            return 0
        fi
    done
    return 1
}

start_azurite() {
    require_command azurite
    mkdir -p "$AZURITE_LOG_DIR" "$AZURITE_LOCATION"
    log_step "Launching Azurite (ports $AZURITE_PORT_LIST)"
    nohup azurite \
        --silent \
        --location "$AZURITE_LOCATION" \
        --blobHost "$AZURITE_HOST" --blobPort 10000 \
        --queueHost "$AZURITE_HOST" --queuePort 10001 \
        --tableHost "$AZURITE_HOST" --tablePort 10002 \
        >"$AZURITE_LOG_FILE" 2>&1 &
    local pid="$!"
    disown 2>/dev/null || true
    log_info "Azurite started (pid $pid, logs: $AZURITE_LOG_FILE)"
}

wait_for_azurite_ready() {
    local elapsed=0
    while (( elapsed < AZURITE_STARTUP_TIMEOUT )); do
        if are_azurite_ports_ready; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

ensure_azurite() {
    require_command curl
    if are_azurite_ports_ready; then
        log_info "Azurite is already running on ports $AZURITE_PORT_LIST"
        return 0
    fi
    if are_azurite_ports_bound; then
        log_warn "Ports $AZURITE_PORT_LIST are bound but not responding; assuming an existing Azurite instance and skipping startup"
        return 0
    fi
    start_azurite
    if wait_for_azurite_ready; then
        log_info "Azurite reachable on all ports"
        return 0
    fi
    if grep -q "EADDRINUSE" "$AZURITE_LOG_FILE" 2>/dev/null; then
        log_warn "Azurite reported EADDRINUSE; assuming an existing instance is active and continuing"
        return 0
    fi
    log_error "Azurite did not become available within ${AZURITE_STARTUP_TIMEOUT}s."
    log_error "Check $AZURITE_LOG_FILE for details."
    exit 1
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    ensure_azurite
fi
