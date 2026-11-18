"""Copy the FFmpeg executable from the pinned wheel beside Atlas.exe."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg


ROOT = Path(__file__).resolve().parent.parent
source = Path(imageio_ffmpeg.get_ffmpeg_exe())
destination = ROOT / "dist" / "Atlas" / "ffmpeg" / "ffmpeg.exe"

if not source.is_file():
    raise FileNotFoundError(f"FFmpeg binary from imageio-ffmpeg was not found: {source}")

destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, destination)
subprocess.run(
    [str(destination), "-version"],
    check=True,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
encoder_list = subprocess.run(
    [str(destination), "-hide_banner", "-encoders"],
    check=True,
    capture_output=True,
    text=True,
).stdout
if "libx264" not in encoder_list:
    raise RuntimeError("The bundled FFmpeg build does not provide the required libx264 encoder.")
print(f"Bundled FFmpeg at {destination}")
