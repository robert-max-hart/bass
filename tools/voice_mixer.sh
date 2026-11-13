#!/usr/bin/env bash
# Launch the voicegen mixer on the bass voices, with chapter 1's lines as test text.
# Extra arguments are passed on and win over the defaults, e.g.:
#   tools/voice_mixer.sh --script stories/optometrist_p2/voice_script.txt --port 8800
set -euo pipefail
cd "$(dirname "$0")/.."
exec ~/venv/kokoro/bin/python -I -m voicegen mixer \
    --voices-dir stories/voices \
    --script stories/optometrist_p1/voice_script.txt \
    "$@"
