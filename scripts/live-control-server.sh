#!/usr/bin/env bash
# Live Control Server — Manus-style live streaming + voice control
#
# Architecture:
#   macOS VNC (5900) → websockify (6080 WS) → noVNC (browser)
#   Cloudflare Tunnel → public URL for remote access
#   OpenAI Realtime API → voice commands → remote control daemon
#
# Usage: ./scripts/live-control-server.sh [start|stop|status]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for envfile in "$PROJECT_ROOT/.env" "$PROJECT_ROOT/backend/.env"; do
    [ -f "$envfile" ] && { set -a; source "$envfile"; set +a; }
done

# Config
VNC_PORT="${VNC_PORT:-5900}"
WEBSOCKIFY_PORT="${WEBSOCKIFY_PORT:-6080}"
CONTROL_PANEL_PORT="${CONTROL_PANEL_PORT:-6090}"
NOVNC_DIR="${NOVNC_DIR:-/tmp/noVNC}"
WEBSOCKIFY_BIN="${WEBSOCKIFY_BIN:-$HOME/Library/Python/3.9/bin/websockify}"
TUNNEL_HOSTNAME="${LIVE_CONTROL_HOSTNAME:-}"  # Optional: custom domain
PIDFILE_DIR="/tmp/openclaw_live"
LOG_DIR="/tmp/openclaw_live/logs"

mkdir -p "$PIDFILE_DIR" "$LOG_DIR"

log() { echo "$(date '+%H:%M:%S') [live-ctrl] $*"; }

# ------------------------------------------------------------------
# Start all components
# ------------------------------------------------------------------
do_start() {
    log "Starting Live Control Server..."

    # 0. Security: require password
    if [ -z "${LIVE_CONTROL_PASSWORD:-}" ]; then
        log "ERROR: LIVE_CONTROL_PASSWORD not set in .env"
        log "Set it: echo 'LIVE_CONTROL_PASSWORD=your-strong-password' >> backend/.env"
        log "This password protects your desktop from unauthorized access."
        exit 1
    fi
    log "Auth: password-protected login enabled"

    # 1. Check VNC is running
    if ! lsof -i ":$VNC_PORT" > /dev/null 2>&1; then
        log "WARNING: macOS Screen Sharing not enabled on port $VNC_PORT"
        log "Enable it: System Settings → General → Sharing → Screen Sharing"
        log "Continuing anyway — websockify will retry connections..."
    else
        log "VNC server detected on port $VNC_PORT"
    fi

    # 2. Start websockify (VNC → WebSocket bridge)
    if [ -f "$PIDFILE_DIR/websockify.pid" ] && kill -0 "$(cat "$PIDFILE_DIR/websockify.pid")" 2>/dev/null; then
        log "websockify already running (PID $(cat "$PIDFILE_DIR/websockify.pid"))"
    else
        log "Starting websockify on port $WEBSOCKIFY_PORT → VNC $VNC_PORT"
        "$WEBSOCKIFY_BIN" --web="$NOVNC_DIR" \
            "$WEBSOCKIFY_PORT" "localhost:$VNC_PORT" \
            > "$LOG_DIR/websockify.log" 2>&1 &
        echo $! > "$PIDFILE_DIR/websockify.pid"
        log "websockify started (PID $!)"
    fi

    # 3. Start the control panel server (custom page with voice + noVNC embed)
    if [ -f "$PIDFILE_DIR/control-panel.pid" ] && kill -0 "$(cat "$PIDFILE_DIR/control-panel.pid")" 2>/dev/null; then
        log "Control panel already running (PID $(cat "$PIDFILE_DIR/control-panel.pid"))"
    else
        log "Starting control panel on port $CONTROL_PANEL_PORT"
        node "$SCRIPT_DIR/live-control-panel.js" \
            > "$LOG_DIR/control-panel.log" 2>&1 &
        echo $! > "$PIDFILE_DIR/control-panel.pid"
        log "Control panel started (PID $!)"
    fi

    sleep 1

    # 4. Start Cloudflare Tunnel
    if [ -f "$PIDFILE_DIR/tunnel.pid" ] && kill -0 "$(cat "$PIDFILE_DIR/tunnel.pid")" 2>/dev/null; then
        log "Tunnel already running (PID $(cat "$PIDFILE_DIR/tunnel.pid"))"
    else
        log "Starting Cloudflare Tunnel..."
        if [ -n "$TUNNEL_HOSTNAME" ]; then
            cloudflared tunnel --url "http://localhost:$CONTROL_PANEL_PORT" \
                --hostname "$TUNNEL_HOSTNAME" \
                > "$LOG_DIR/tunnel.log" 2>&1 &
        else
            cloudflared tunnel --url "http://localhost:$CONTROL_PANEL_PORT" \
                > "$LOG_DIR/tunnel.log" 2>&1 &
        fi
        echo $! > "$PIDFILE_DIR/tunnel.pid"
        log "Tunnel started (PID $!)"

        # Wait for tunnel URL
        sleep 3
        local tunnel_url
        tunnel_url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/tunnel.log" 2>/dev/null | head -1)
        if [ -n "$tunnel_url" ]; then
            log "========================================="
            log "LIVE CONTROL URL: $tunnel_url"
            log "========================================="

            # Save URL to file (do NOT post to public channels)
            echo "$tunnel_url" > "$PIDFILE_DIR/tunnel_url"
            log "URL saved to $PIDFILE_DIR/tunnel_url"
            log "SECURITY: URL NOT posted to Slack (public channel)"
            log "Share it manually via DM only"
        else
            log "Tunnel URL not detected yet — check $LOG_DIR/tunnel.log"
        fi
    fi

    log "All components started. Logs in $LOG_DIR/"
}

# ------------------------------------------------------------------
# Stop all components
# ------------------------------------------------------------------
do_stop() {
    log "Stopping Live Control Server..."
    for component in tunnel control-panel websockify; do
        local pidfile="$PIDFILE_DIR/$component.pid"
        if [ -f "$pidfile" ]; then
            local pid
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null
                log "Stopped $component (PID $pid)"
            fi
            rm -f "$pidfile"
        fi
    done
    log "All components stopped."
}

# ------------------------------------------------------------------
# Status check
# ------------------------------------------------------------------
do_status() {
    echo "=== Live Control Server Status ==="
    for component in websockify control-panel tunnel; do
        local pidfile="$PIDFILE_DIR/$component.pid"
        if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
            echo "  $component: RUNNING (PID $(cat "$pidfile"))"
        else
            echo "  $component: STOPPED"
        fi
    done

    # Show tunnel URL if available
    local tunnel_url
    tunnel_url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_DIR/tunnel.log" 2>/dev/null | head -1)
    [ -n "$tunnel_url" ] && echo "  URL: $tunnel_url"

    # VNC status
    if lsof -i ":$VNC_PORT" > /dev/null 2>&1; then
        echo "  VNC: LISTENING on $VNC_PORT"
    else
        echo "  VNC: NOT RUNNING"
    fi
}

case "${1:-start}" in
    start)  do_start ;;
    stop)   do_stop ;;
    status) do_status ;;
    restart) do_stop; sleep 1; do_start ;;
    *) echo "Usage: $0 [start|stop|status|restart]" ;;
esac
