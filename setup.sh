#!/usr/bin/env bash
# mestre-harness setup.
#
# Installs the mestre-* skills and the eight mestre-* agents into one
# repository, creates the .agent-work/ workspace, and keeps all of it out of
# Git through the global excludes file. Nothing tracked is created or changed.
#
# Usage:
#   setup.sh [install] [--quiet] [repo-dir]  install or update (default command)
#   setup.sh status [repo-dir]               show what is installed and ignored
#   setup.sh uninstall [--purge] [repo-dir]  remove agents and skills; --purge
#                                            also deletes .agent-work/
#
# repo-dir defaults to the current directory. Run it once per worktree.

set -euo pipefail

PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="mestre-"
MAIN_SKILL="mestre-harness"
WT_SKILL="mestre-wt"
# Replaced in the worktree skill with the absolute path of this script.
SETUP_PLACEHOLDER="__MESTRE_SETUP__"
WORK_DIR=".agent-work"
IGNORE_HEADER="# mestre-harness: local-only agent workflow"
IGNORE_PATTERNS=(
  "${WORK_DIR}/"
  "**/.claude/agents/${PREFIX}*.md"
  "**/.claude/skills/${PREFIX}*/"
)
QUIET=0

die() { echo "error: $*" >&2; exit 1; }
warn() { echo "warning: $*" >&2; }
say() { [ "$QUIET" -eq 1 ] || echo "$*"; }
note() { say "  $*"; }

usage() {
  cat <<'USAGE'
Usage:
  setup.sh [install] [--quiet] [repo-dir]  install or update (default command)
  setup.sh status [repo-dir]               show what is installed and ignored
  setup.sh uninstall [--purge] [repo-dir]  remove agents and skills; --purge
                                           also deletes .agent-work/

repo-dir defaults to the current directory. Run it once per worktree.
--quiet prints nothing on success; warnings and errors still go to stderr.
USAGE
}

# Sets ROOT and IS_GIT for the target directory.
resolve_root() {
  local target="$1"
  [ -d "$target" ] || die "not a directory: $target"
  if ROOT="$(git -C "$target" rev-parse --show-toplevel 2>/dev/null)"; then
    IS_GIT=1
  else
    ROOT="$(cd "$target" && pwd)"
    IS_GIT=0
  fi
  AGENTS_DEST="$ROOT/.claude/agents"
  SKILLS_DEST="$ROOT/.claude/skills"
}

count_matches() {
  local count=0 path
  for path in "$@"; do
    [ -e "$path" ] && count=$((count + 1))
  done
  echo "$count"
}

# Same lookup Git uses: core.excludesFile, else $XDG_CONFIG_HOME/git/ignore.
excludes_file() {
  local configured
  configured="$(git config --global --type=path --get core.excludesFile 2>/dev/null || true)"
  if [ -n "$configured" ]; then
    echo "$configured"
  else
    echo "${XDG_CONFIG_HOME:-$HOME/.config}/git/ignore"
  fi
}

ensure_global_ignores() {
  local file pattern added=0
  file="$(excludes_file)"
  mkdir -p "$(dirname "$file")"
  [ -e "$file" ] || : > "$file"
  for pattern in "${IGNORE_PATTERNS[@]}"; do
    if ! grep -Fxq -- "$pattern" "$file"; then
      if [ "$added" -eq 0 ]; then
        # Keep the existing last line intact before appending.
        if [ -s "$file" ] && [ -n "$(tail -c1 "$file")" ]; then echo >> "$file"; fi
        grep -Fxq -- "$IGNORE_HEADER" "$file" || echo "$IGNORE_HEADER" >> "$file"
      fi
      echo "$pattern" >> "$file"
      added=$((added + 1))
    fi
  done
  if [ "$added" -gt 0 ]; then
    note "added $added pattern(s) to $file"
  else
    note "global excludes already cover the harness ($file)"
  fi
}

# The harness must never modify files a repository tracks.
refuse_if_tracked() {
  [ "$IS_GIT" -eq 1 ] || return 0
  local tracked
  tracked="$(git -C "$ROOT" ls-files -- \
    ".claude/agents/${PREFIX}*.md" ".claude/skills/${PREFIX}*" "$WORK_DIR")"
  if [ -n "$tracked" ]; then
    echo "$tracked" | sed 's/^/  tracked: /' >&2
    die "this repository tracks harness paths; refusing to touch them"
  fi
}

# Prints the installed paths Git does not ignore. Empty output means all good.
unignored_paths() {
  [ "$IS_GIT" -eq 1 ] || return 0
  local path
  for path in "$ROOT/$WORK_DIR" "$SKILLS_DEST/${PREFIX}"*/SKILL.md "$AGENTS_DEST/${PREFIX}"*.md; do
    [ -e "$path" ] || continue
    git -C "$ROOT" check-ignore -q -- "$path" || echo "$path"
  done
}

report_ignore_state() {
  local leaked
  leaked="$(unignored_paths)"
  if [ "$IS_GIT" -eq 0 ]; then
    note "not a Git repository: nothing to ignore yet, and the harness needs Git for commits and review"
  elif [ -z "$leaked" ]; then
    note "all harness paths are ignored by Git"
  else
    echo "$leaked" | sed 's/^/  NOT ignored: /' >&2
    warn "check this repository's .gitignore for a rule that re-includes them"
  fi
}

# Copies the pack's skills into a skills directory. The worktree skill is the
# only templated file: it carries the absolute path of this script.
render_skills() {
  local dest="$1" skill escaped
  mkdir -p "$dest"
  for skill in "$PACK_DIR/skills/${PREFIX}"*/; do
    cp -R "${skill%/}" "$dest/"
  done
  find "$dest/${PREFIX}"* -name .DS_Store -delete
  escaped="$(printf '%s' "$PACK_DIR/setup.sh" | sed 's/[\\&|]/\\&/g')"
  sed "s|$SETUP_PLACEHOLDER|$escaped|g" "$PACK_DIR/skills/$WT_SKILL/SKILL.md" \
    > "$dest/$WT_SKILL/SKILL.md"
}

# True when the installed skills and agents are identical to this pack.
matches_pack() {
  local file skill staging result=0
  for file in "$PACK_DIR/agents/${PREFIX}"*.md; do
    cmp -s "$file" "$AGENTS_DEST/$(basename "$file")" || return 1
  done
  for file in "$AGENTS_DEST/${PREFIX}"*.md; do
    [ -e "$PACK_DIR/agents/$(basename "$file")" ] || return 1
  done
  for skill in "$SKILLS_DEST/${PREFIX}"*/; do
    [ -d "$PACK_DIR/skills/$(basename "$skill")" ] || return 1
  done
  staging="$(mktemp -d)"
  render_skills "$staging"
  for skill in "$staging/${PREFIX}"*/; do
    diff -rq "$skill" "$SKILLS_DEST/$(basename "$skill")" >/dev/null 2>&1 || result=1
  done
  rm -rf "$staging"
  return "$result"
}

remove_installed() {
  rm -f "$AGENTS_DEST/${PREFIX}"*.md
  rm -rf "${SKILLS_DEST:?}/${PREFIX}"*
}

cmd_install() {
  refuse_if_tracked
  say "Installing mestre-harness into $ROOT"
  ensure_global_ignores
  remove_installed
  mkdir -p "$AGENTS_DEST" "$ROOT/$WORK_DIR"
  cp "$PACK_DIR/agents/${PREFIX}"*.md "$AGENTS_DEST/"
  render_skills "$SKILLS_DEST"
  note "agents: $(count_matches "$AGENTS_DEST/${PREFIX}"*.md) in .claude/agents/"
  note "skills: $(count_matches "$SKILLS_DEST/${PREFIX}"*/SKILL.md) in .claude/skills/"
  note "workspace: $WORK_DIR/"
  report_ignore_state
  say "Done. Start a new Claude Code session in this repository and run /$MAIN_SKILL"
}

cmd_status() {
  local agents skills
  agents="$(count_matches "$AGENTS_DEST/${PREFIX}"*.md)"
  skills="$(count_matches "$SKILLS_DEST/${PREFIX}"*/SKILL.md)"
  say "mestre-harness status for $ROOT"
  note "agents installed: $agents"
  note "skills installed: $skills"
  if [ -d "$ROOT/$WORK_DIR" ]; then note "workspace: present"; else note "workspace: missing"; fi
  if [ "$agents" -gt 0 ] && [ "$skills" -gt 0 ]; then
    if matches_pack; then
      note "matches the pack at $PACK_DIR"
    else
      note "differs from the pack at $PACK_DIR; run install to update"
    fi
  fi
  report_ignore_state
}

cmd_uninstall() {
  local purge="$1"
  refuse_if_tracked
  say "Removing mestre-harness from $ROOT"
  remove_installed
  rmdir "$AGENTS_DEST" "$SKILLS_DEST" "$ROOT/.claude" 2>/dev/null || true
  note "removed agents and skills"
  if [ "$purge" -eq 1 ]; then
    rm -rf "${ROOT:?}/$WORK_DIR"
    note "deleted $WORK_DIR/"
  elif [ -d "$ROOT/$WORK_DIR" ]; then
    note "kept $WORK_DIR/ (use --purge to delete it)"
  fi
  note "global excludes patterns were left in place; they are harmless without the harness"
}

main() {
  local command="install" purge=0 target="."
  local arg
  for arg in "$@"; do
    case "$arg" in
      install|status|uninstall) command="$arg" ;;
      --purge) purge=1 ;;
      --quiet) QUIET=1 ;;
      -h|--help) usage; exit 0 ;;
      -*) die "unknown option: $arg" ;;
      *) target="$arg" ;;
    esac
  done
  [ -f "$PACK_DIR/skills/$MAIN_SKILL/SKILL.md" ] \
    || die "pack is incomplete: $PACK_DIR/skills/$MAIN_SKILL/SKILL.md not found"
  resolve_root "$target"
  case "$command" in
    install) cmd_install ;;
    status) cmd_status ;;
    uninstall) cmd_uninstall "$purge" ;;
  esac
}

main "$@"
