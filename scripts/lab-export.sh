#!/usr/bin/env bash
# Copy the app into an agent-lab checkout as public/modules/events/, where the lab finds its module
# (default.nix) and VM test (test.nix). The copy is a committed tree, so untracked and ignored files
# (data/, node_modules/, dist/) never reach the lab; .gitattributes leaves out what only this
# repository needs. Review the result on a lab branch and open a pull request there.
#
#   scripts/lab-export.sh ~/agent-lab [commit]
set -euo pipefail
lab=$(realpath "${1:?usage: scripts/lab-export.sh <agent-lab checkout> [commit]}")
[ -f "$lab/public/modules/lib/modules-in.nix" ] || { echo "not an agent-lab checkout: $lab" >&2; exit 1; }
cd "$(dirname "$0")/.."
commit=$(git rev-parse --verify "${2:-HEAD}^{commit}")
[ -z "$(git status --porcelain)" ] || echo "note: uncommitted changes here are not copied" >&2
dest="$lab/public/modules/events"
rm -rf "$dest"
mkdir -p "$dest"
git archive "$commit" | tar -x -C "$dest"
echo "copied events $(git rev-parse --short "$commit") into $dest; git add it there before nix flake check"
