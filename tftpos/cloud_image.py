"""Cloud image import, conversion, and management."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Optional

from tftpos.models import CloudImage

logger = logging.getLogger("tftpos.cloud_image")

SUPPORTED_FORMATS = ("qcow2", "raw", "vmdk", "vhd", "vhdx")


def _images_dir(data_dir: Path) -> Path:
    """Return the images subdirectory under data_dir."""
    d = data_dir / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _image_subdir(
    images_dir: Path,
    vendor: str,
    version: str,
    arch: str,
) -> Path:
    """Return and create the per-image subdirectory."""
    name = f"{vendor}-{version}-{arch}"
    dest = images_dir / name
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _metadata_path(image_dir: Path) -> Path:
    """Return the metadata JSON file path inside an image directory."""
    return image_dir / "image.json"


def _save_metadata(image: CloudImage) -> None:
    """Persist image metadata as JSON next to the image file."""
    meta = _metadata_path(image.path.parent)
    data = {
        "name": image.name,
        "os_family": image.os_family,
        "vendor": image.vendor,
        "version": image.version,
        "arch": image.arch,
        "format": image.format,
        "filename": image.path.name,
        "size_bytes": image.size_bytes,
        "cloud_init": image.cloud_init,
    }
    meta.write_text(json.dumps(data, indent=2))


def _load_metadata(meta_path: Path) -> Optional[CloudImage]:
    """Load a CloudImage from a metadata JSON file."""
    try:
        data = json.loads(meta_path.read_text())
        image_path = meta_path.parent / data["filename"]
        return CloudImage(
            name=data["name"],
            os_family=data["os_family"],
            vendor=data["vendor"],
            version=data["version"],
            arch=data.get("arch", "x86_64"),
            format=data.get("format", "qcow2"),
            path=image_path,
            size_bytes=data.get("size_bytes", 0),
            cloud_init=data.get("cloud_init", True),
        )
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        logger.warning("Failed to load image metadata from %s: %s", meta_path, exc)
        return None


def import_cloud_image(
    source: str,
    os_family: str,
    vendor: str,
    version: str,
    arch: str = "x86_64",
    fmt: str = "qcow2",
    images_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> CloudImage:
    """Import a cloud image from a URL or local file path.

    Parameters
    ----------
    source:
        A URL (http/https) or a local filesystem path.
    os_family:
        OS family identifier (e.g. "fedora", "ubuntu").
    vendor:
        Vendor string (e.g. "fedora", "canonical").
    version:
        OS version (e.g. "40", "24.04").
    arch:
        Architecture, default ``x86_64``.
    fmt:
        Image format, default ``qcow2``.
    images_dir:
        Override directory to store images (used directly).
    data_dir:
        Base data directory -- images stored under ``<data_dir>/images/``.

    Returns
    -------
    CloudImage with the ``path`` pointing at the stored file.
    """
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            f"unsupported image format {fmt!r}; "
            f"supported: {', '.join(SUPPORTED_FORMATS)}"
        )

    if images_dir is None:
        if data_dir is None:
            raise ValueError("images_dir or data_dir is required")
        images_dir = _images_dir(data_dir)

    dest_dir = _image_subdir(images_dir, vendor, version, arch)
    image_name = f"{vendor}-{version}-{arch}"

    if source.startswith("http://") or source.startswith("https://"):
        filename = _filename_from_url(source, fmt)
        dest_file = dest_dir / filename
        logger.info("Downloading cloud image from %s to %s", source, dest_file)
        _download_file(source, dest_file)
    else:
        src_path = Path(source)
        if not src_path.exists():
            raise FileNotFoundError(f"source image not found: {source}")
        dest_file = dest_dir / src_path.name
        logger.info("Copying cloud image from %s to %s", src_path, dest_file)
        shutil.copy2(str(src_path), str(dest_file))

    size_bytes = dest_file.stat().st_size

    image = CloudImage(
        name=image_name,
        os_family=os_family,
        vendor=vendor,
        version=version,
        arch=arch,
        format=fmt,
        path=dest_file,
        size_bytes=size_bytes,
        cloud_init=True,
    )
    _save_metadata(image)

    logger.info(
        "Cloud image imported name=%s format=%s size=%d",
        image_name, fmt, size_bytes,
    )
    return image


def _filename_from_url(url: str, fmt: str) -> str:
    """Extract a filename from a URL, falling back to a default."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    name = Path(parsed.path).name
    if not name or name == "/":
        name = f"image.{fmt}"
    return name


def _download_file(url: str, dest: Path) -> None:
    """Download a file from a URL to a local path."""
    with urllib.request.urlopen(url) as response:
        with open(dest, "wb") as out:
            shutil.copyfileobj(response, out)


def convert_image(
    source: Path,
    dest_format: str,
    dest_path: Path,
) -> Path:
    """Convert an image between formats using qemu-img.

    Parameters
    ----------
    source:
        Path to the source image file.
    dest_format:
        Target format (qcow2, raw, vmdk, vhd, vhdx).
    dest_path:
        Path for the converted image.

    Returns
    -------
    Path to the converted image.
    """
    if dest_format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"unsupported target format {dest_format!r}; "
            f"supported: {', '.join(SUPPORTED_FORMATS)}"
        )

    if not source.exists():
        raise FileNotFoundError(f"source image not found: {source}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "qemu-img", "convert",
        "-f", _detect_format(source),
        "-O", dest_format,
        str(source),
        str(dest_path),
    ]

    logger.info("Converting image %s -> %s (%s)", source, dest_path, dest_format)
    subprocess.run(cmd, check=True, capture_output=True)

    return dest_path


def resize_image(image_path: Path, size: str) -> None:
    """Resize an image using qemu-img.

    Parameters
    ----------
    image_path:
        Path to the image to resize.
    size:
        New size specification (e.g. "20G", "+5G").
    """
    if not image_path.exists():
        raise FileNotFoundError(f"image not found: {image_path}")

    cmd = ["qemu-img", "resize", str(image_path), size]

    logger.info("Resizing image %s to %s", image_path, size)
    subprocess.run(cmd, check=True, capture_output=True)


def list_images(
    images_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> List[CloudImage]:
    """List all imported cloud images.

    Parameters
    ----------
    images_dir:
        Direct path to the images directory.
    data_dir:
        Base data directory (images read from ``<data_dir>/images/``).

    Returns
    -------
    List of CloudImage objects found on disk.
    """
    if images_dir is None:
        if data_dir is None:
            raise ValueError("images_dir or data_dir is required")
        images_dir = data_dir / "images"

    if not images_dir.exists():
        return []

    results: List[CloudImage] = []
    for meta_file in sorted(images_dir.rglob("image.json")):
        img = _load_metadata(meta_file)
        if img is not None:
            results.append(img)

    return results


def delete_image(
    name: str,
    images_dir: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> bool:
    """Delete a cloud image by name.

    Parameters
    ----------
    name:
        The image name (e.g. "fedora-40-x86_64").
    images_dir:
        Direct path to the images directory.
    data_dir:
        Base data directory.

    Returns
    -------
    True if the image was found and deleted, False otherwise.
    """
    if images_dir is None:
        if data_dir is None:
            raise ValueError("images_dir or data_dir is required")
        images_dir = data_dir / "images"

    if not images_dir.exists():
        return False

    for meta_file in images_dir.rglob("image.json"):
        img = _load_metadata(meta_file)
        if img is not None and img.name == name:
            image_dir = meta_file.parent
            logger.info("Deleting cloud image %s at %s", name, image_dir)
            shutil.rmtree(str(image_dir))
            return True

    return False


def _detect_format(image_path: Path) -> str:
    """Detect the format of an image from its extension."""
    suffix = image_path.suffix.lstrip(".").lower()
    format_map = {
        "qcow2": "qcow2",
        "raw": "raw",
        "img": "raw",
        "vmdk": "vmdk",
        "vhd": "vpc",
        "vhdx": "vhdx",
    }
    return format_map.get(suffix, "qcow2")
