#!/usr/bin/env bash
# Run a nested Hyprland to test against, isolated from the real session.
#
# Teardown kills the PID this script started and nothing else. An earlier
# version killed "every instance that isn't mine", read $HYPRLAND_INSTANCE_
# SIGNATURE from a leaked env var, decided the real desktop was not mine, and
# killed it. Never identify the sandbox by elimination -- record it at launch.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${SANDBOX_DIR:-/tmp/hyprwrc-sandbox}"
PIDFILE="$RUN/hyprland.pid"
SIGFILE="$RUN/instance.sig"
WLFILE="$RUN/wayland.display"

up() {
  mkdir -p "$RUN/config/conf.d" "$RUN/xdg"
  # Deliberately a .lua config: `repl` -- which is how rules are parsed and
  # config is compile-checked -- exists only under the Lua config manager. A
  # hyprlang sandbox boots fine and then fails every test for the wrong reason.
  # Left empty on purpose. Anything in here is re-evaluated on every reload,
  # so a single wrong API call makes `configerrors` non-empty forever and every
  # verified write rolls itself back -- a failure that looks exactly like the
  # code under test being broken.
  : > "$RUN/config/hyprland.lua"

  # WAYLAND_DISPLAY and the session's XDG_RUNTIME_DIR both have to stay: the
  # nested compositor reaches its parent through them, and without a parent it
  # finds no backend and dies. Isolation is therefore the instance signature,
  # not the runtime dir -- so HYPRLAND_INSTANCE_SIGNATURE is cleared and the
  # nested instance gets its own.
  local before after wl_before wl_after
  before=$(ls "$XDG_RUNTIME_DIR/hypr" 2>/dev/null || true)
  wl_before=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' || true)

  env -u HYPRLAND_INSTANCE_SIGNATURE \
      HYPRLAND_CONFIG="$RUN/config/hyprland.lua" \
      Hyprland -c "$RUN/config/hyprland.lua" \
      > "$RUN/hyprland.log" 2>&1 &
  echo $! > "$PIDFILE"

  # Identify the new instance as the signature that appeared after launch, and
  # cross-check it against the pid's own environment before trusting it.
  for _ in $(seq 1 50); do
    after=$(ls "$XDG_RUNTIME_DIR/hypr" 2>/dev/null || true)
    sig=$(comm -13 <(echo "$before") <(echo "$after") | head -1)
    [[ -n ${sig:-} ]] && { echo "$sig" > "$SIGFILE"; break; }
    sleep 0.2
  done
  [[ -s $SIGFILE ]] || { echo "nested Hyprland did not come up; see $RUN/hyprland.log" >&2; exit 1; }

  # A signature equal to the real session's would mean every command below
  # lands on the actual desktop. Bail rather than find out the hard way.
  if [[ "$(cat "$SIGFILE")" == "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
    echo "refusing: sandbox signature matches the live session" >&2
    down; exit 1
  fi

  # The compositor's own Wayland socket, found the same way as the signature:
  # whichever one appeared after launch. Clients started by `run` connect to
  # this, which is what keeps test windows inside the sandbox.
  # Polled, not sampled once: the socket is advertised a moment after the
  # instance directory, so checking immediately reliably finds nothing.
  for _ in $(seq 1 50); do
    wl_after=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -E '^wayland-[0-9]+$' || true)
    wl=$(comm -13 <(echo "$wl_before") <(echo "$wl_after") | head -1)
    [[ -n ${wl:-} ]] && { echo "$wl" > "$WLFILE"; break; }
    sleep 0.2
  done
  [[ -s $WLFILE ]] || { echo "could not identify the nested Wayland socket" >&2; down; exit 1; }

  echo "sandbox up   pid=$(cat "$PIDFILE")  sig=$(cat "$SIGFILE")  display=$(cat "$WLFILE")"
  echo "real session sig is $HYPRLAND_INSTANCE_SIGNATURE -- must differ"
}

# Run a command inside the sandbox. Config dir points at the sandbox tree, so
# the app reads and writes throwaway rules, never the real ~/.config/hypr.
run() {
  [[ -s $SIGFILE ]] || { echo "sandbox is not up" >&2; exit 1; }
  env HYPRLAND_INSTANCE_SIGNATURE="$(cat "$SIGFILE")" \
      WAYLAND_DISPLAY="$(cat "$WLFILE" 2>/dev/null || echo "$WAYLAND_DISPLAY")" \
      XDG_CONFIG_HOME="$RUN/config-home" \
      PYTHONPATH="$HERE" \
      "$@"
}

down() {
  if [[ -s $PIDFILE ]]; then
    pid=$(cat "$PIDFILE")
    # Only ever this pid, and only if it is still a Hyprland.
    if [[ -r /proc/$pid/comm ]] && grep -qi hyprland "/proc/$pid/comm"; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 25); do [[ -d /proc/$pid ]] || break; sleep 0.2; done
      [[ -d /proc/$pid ]] && kill -9 "$pid" 2>/dev/null || true
      echo "sandbox down pid=$pid"
    else
      echo "pid $pid is not a running Hyprland; leaving it alone" >&2
    fi
    rm -f "$PIDFILE" "$SIGFILE" "$WLFILE"
  else
    echo "no sandbox pidfile; nothing to do"
  fi
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  run) shift; run "$@" ;;
  *) echo "usage: $0 {up|down|run <cmd...>}" >&2; exit 2 ;;
esac
