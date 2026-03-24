# maunium-stickerpicker - A fast and simple Matrix sticker picker widget.
# Copyright (C) 2020 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from functools import partial
from io import BytesIO
import gzip
import os.path
import json
from pathlib import Path
from typing import Dict, List
import tempfile
import subprocess

from PIL import Image

try:
    from PIL.ImageMath import unsafe_eval as _imagemath_eval
except ImportError:
    from PIL.ImageMath import eval as _imagemath_eval

from . import matrix

open_utf8 = partial(open, encoding='UTF-8')

def convert_video(data: bytes, max_w=256, max_h=256) -> (bytes, int, int):
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.webm")
        output_path = os.path.join(tmpdir, "output.apng")

        with open(input_path, "wb") as f:
            f.write(data)

        result = subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_path,
            "-vf", (
                f"scale='min({max_w},iw)':'min({max_h},ih)'"
                f":force_original_aspect_ratio=decrease"
            ),
            "-plays", "0",
            "-f", "apng",
            output_path,
        ], capture_output=True)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {result.stderr.decode()}")

        apng_data = Path(output_path).read_bytes()

    image = Image.open(BytesIO(apng_data))
    w, h = image.size
    return apng_data, w, h


def convert_tgs(data: bytes, max_w=256, max_h=256) -> (bytes, int, int):
    from rlottie_python import LottieAnimation

    lottie_json = gzip.decompress(data).decode("utf-8")
    anim = LottieAnimation.from_data(lottie_json)

    frame_count = anim.lottie_animation_get_totalframe()
    fps = anim.lottie_animation_get_framerate()
    orig_w, orig_h = anim.lottie_animation_get_size()

    w, h = orig_w, orig_h
    if w > max_w or h > max_h:
        if w >= h:
            h = int(h * max_w / w)
            w = max_w
        else:
            w = int(w * max_h / h)
            h = max_h

    frames = []
    for i in range(frame_count):
        buf = anim.lottie_animation_render(frame_num=i, width=w, height=h)
        # rlottie outputs premultiplied ARGB32 (BGRA bytes on little-endian)
        frame = Image.frombytes("RGBA", (w, h), buf, "raw", "BGRA")
        r, g, b, a = frame.split()
        frame = Image.merge("RGBA", [
            _imagemath_eval(
                "convert(min((c * 255) / max(a, 1), 255), 'L')",
                c=c, a=a,
            )
            for c in (r, g, b)
        ] + [a])
        frames.append(frame)

    if not frames:
        raise RuntimeError("No frames rendered from TGS animation")

    new_file = BytesIO()
    if len(frames) == 1:
        frames[0].save(new_file, "PNG")
    else:
        duration_ms = int(1000 / fps)
        frames[0].save(
            new_file,
            format="PNG",
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=0,
        )

    return new_file.getvalue(), w, h



def convert_image(data: bytes, max_w=256, max_h=256) -> (bytes, int, int):
    image: Image.Image = Image.open(BytesIO(data)).convert("RGBA")
    new_file = BytesIO()
    image.save(new_file, "png")
    w, h = image.size
    if w > max_w or h > max_h:
        # Set the width and height to lower values so clients wouldn't show them as huge images
        if w > h:
            h = int(h / (w / max_w))
            w = max_w
        else:
            w = int(w / (h / max_h))
            h = max_h
    return new_file.getvalue(), w, h


def add_to_index(name: str, output_dir: str) -> None:
    index_path = os.path.join(output_dir, "index.json")
    try:
        with open_utf8(index_path) as index_file:
            index_data = json.load(index_file)
    except (FileNotFoundError, json.JSONDecodeError):
        index_data = {"packs": []}
    if "homeserver_url" not in index_data and matrix.homeserver_url:
        index_data["homeserver_url"] = matrix.homeserver_url
    if name not in index_data["packs"]:
        index_data["packs"].append(name)
        with open_utf8(index_path, "w") as index_file:
            json.dump(index_data, index_file, indent="  ")
        print(f"Added {name} to {index_path}")


def make_sticker(mxc: str, width: int, height: int, size: int,
                 body: str = "") -> matrix.StickerInfo:
    return {
        "body": body,
        "url": mxc,
        "info": {
            "w": width,
            "h": height,
            "size": size,
            "mimetype": "image/png",

            # Element iOS compatibility hack
            "thumbnail_url": mxc,
            "thumbnail_info": {
                "w": width,
                "h": height,
                "size": size,
                "mimetype": "image/png",
            },
        },
        "msgtype": "m.sticker",
    }


def add_thumbnails(stickers: List[matrix.StickerInfo], stickers_data: Dict[str, bytes], output_dir: str) -> None:
    thumbnails = Path(output_dir, "thumbnails")
    thumbnails.mkdir(parents=True, exist_ok=True)

    for sticker in stickers:
        image_data, _, _ = convert_image(stickers_data[sticker["url"]], 128, 128)

        name = sticker["url"].split("/")[-1]
        thumbnail_path = thumbnails / name
        thumbnail_path.write_bytes(image_data)
