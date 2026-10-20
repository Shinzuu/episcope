#!/usr/bin/env bash
# Build the group pack PDFs into share/EpiScope-group-pack/ from *.tex here and report/. The video slides and
# script are built elsewhere (~/Projects/episcope-video), not in this pack.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SRC/../.." && pwd)"
PACK="$ROOT/share/EpiScope-group-pack"
B="$SRC/build"
mkdir -p "$B" "$PACK"
doc() {  # doc NAME BODYFILE: wrap a body in the pack style and build NAME.pdf
  cat >"$B/$1.tex" <<TEX
\\documentclass[11pt]{article}
\\usepackage{pack}
\\newcommand{\\members}{Shezan (Data Engineer), Tahsin Ahmed (Rafi, Analytics), Rimjhim (Visualisation), Samira (Platform \\& Evaluation)}
\\begin{document}
\\input{$2}
\\end{document}
TEX
  (cd "$B" && TEXINPUTS="$SRC:" latexmk -pdf -interaction=nonstopmode -halt-on-error "$1.tex" >/dev/null) \
    || { grep -E '^!' -A3 "$B/$1.log"; exit 1; }
  grep -E 'Warning|Overfull|Underfull' "$B/$1.log" | grep -v 'Package rerunfilecheck' || true
  cp "$B/$1.pdf" "$PACK/"
}
doc 01-Status-and-tasks "$SRC/status.tex"
(cd "$ROOT/report" && latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex >/dev/null) \
  || { grep -E '^!' -A3 "$ROOT/report/main.log"; exit 1; }
grep -E 'Warning|Overfull|Underfull' "$ROOT/report/main.log" || true
cp "$ROOT/report/main.pdf" "$PACK/02-Report-draft.pdf"
echo "pack built in $PACK"
