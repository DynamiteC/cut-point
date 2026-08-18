#!/usr/bin/env bash
# Downloads a small Creative-Commons sample video into data/videos/ and registers
# it as demo_001's source video path. The upstream clip is only 10s long; it is
# looped with ffmpeg (stream concat, no re-encode of source frames beyond the
# final trim) to match demo_001's synthetic duration (90s) so Phase 7's demo can
# extract clips around cliffs anywhere in the trailer.
#
# Source: "Big Buck Bunny" (Blender Foundation, 2008), licensed CC BY 3.0.
# https://peach.blender.org/  -- used here only as a stand-in trailer clip for the
# local demo; not affiliated with the CutPoint project.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIDEOS_DIR="$REPO_ROOT/data/videos"
DEST="$VIDEOS_DIR/demo_001.mp4"
RAW_CLIP="$VIDEOS_DIR/.bbb_10s_raw.mp4"
SOURCE_URL="https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4"
TARGET_DURATION_S=90
USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

mkdir -p "$VIDEOS_DIR"

if [[ -f "$DEST" ]]; then
    echo "already present: $DEST"
    exit 0
fi

echo "downloading sample video from $SOURCE_URL ..."
curl -fsSL -A "$USER_AGENT" --max-time 60 "$SOURCE_URL" -o "$RAW_CLIP.tmp"
mv "$RAW_CLIP.tmp" "$RAW_CLIP"

echo "looping 10s clip to ${TARGET_DURATION_S}s to match demo_001's synthetic duration..."
CONCAT_LIST="$VIDEOS_DIR/.concat_list.txt"
: > "$CONCAT_LIST"
for _ in $(seq 1 10); do
    echo "file '$RAW_CLIP'" >> "$CONCAT_LIST"
done
ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" -t "$TARGET_DURATION_S" -c copy "$DEST"
rm -f "$CONCAT_LIST" "$RAW_CLIP"

cat > "$VIDEOS_DIR/ATTRIBUTION.txt" <<'EOF'
demo_001.mp4: derived from "Big Buck Bunny" (c) 2008, Blender Foundation |
peach.blender.org. Licensed under Creative Commons Attribution 3.0 (CC BY 3.0).
The original 10s clip was looped to 90s with ffmpeg concat to match the
synthetic trailer duration used in this demo. Used here solely as a
Creative-Commons stand-in source video for local demo purposes -- not an
actual movie trailer.
EOF

echo "saved to $DEST"
