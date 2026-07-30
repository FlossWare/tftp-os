"""Stage firmware files under tftp_root for external TFTP daemons."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger("tftpos.staging")


def _safe_name(name: str) -> str:
    """Validate a filename to prevent path traversal.

    Rejects names that contain directory separators, ``..``
    components, or are empty / dot-only.  Raises ``ValueError``
    for unsafe input.
    """
    # Reject names with directory separators or traversal
    if "/" in name or os.sep in name:
        raise ValueError(
            f"unsafe staged filename: {name!r}"
        )
    if ".." in name:
        raise ValueError(
            f"unsafe staged filename: {name!r}"
        )
    if not name or name in (".", ".."):
        raise ValueError(
            f"unsafe staged filename: {name!r}"
        )

    return name


def _validate_under_root(
    path: Path, root: Path
) -> None:
    """Ensure *path* lives directly under *root*.

    Checks the parent directory (resolved without following
    symlinks on the leaf) to prevent path traversal.
    Raises ``ValueError`` if the path escapes *root*.
    """
    # Resolve the parent to handle any ".." in the root
    # path itself, but do not follow the leaf (which may
    # be an existing symlink pointing elsewhere).
    parent_resolved = path.parent.resolve()
    root_resolved = root.resolve()
    try:
        common = os.path.commonpath(
            [str(parent_resolved), str(root_resolved)]
        )
    except ValueError:
        raise ValueError(
            f"path {path} is not under {root}"
        )
    if common != str(root_resolved):
        raise ValueError(
            f"path traversal detected: {path} escapes "
            f"{root}"
        )


def stage(
    firmware_path: Union[str, Path],
    tftp_root: Union[str, Path],
    name: Optional[str] = None,
    *,
    symlink: bool = True,
) -> Path:
    """Place or link a firmware file under *tftp_root*.

    Parameters
    ----------
    firmware_path:
        Absolute or relative path to the firmware file.  Must exist.
    tftp_root:
        Directory that the external TFTP daemon serves from
        (e.g. ``/srv/tftp``).  Created if it does not exist.
    name:
        Filename to use inside *tftp_root*.  Defaults to the
        original basename of *firmware_path*.
    symlink:
        If ``True`` (default), create a symbolic link.  Falls back
        to a file copy when the source and target are on different
        filesystems.  If ``False``, always copy.

    Returns
    -------
    Path
        The path of the staged file/symlink inside *tftp_root*.

    Raises
    ------
    FileNotFoundError
        If *firmware_path* does not exist.
    ValueError
        If the resolved target would escape *tftp_root*
        (path traversal).
    """
    firmware_path = Path(firmware_path)
    tftp_root = Path(tftp_root)

    if not firmware_path.exists():
        raise FileNotFoundError(
            f"firmware file not found: {firmware_path}"
        )

    # Determine the filename inside tftp_root
    if name is not None:
        safe = _safe_name(name)
    else:
        safe = _safe_name(firmware_path.name)

    # Create tftp_root if needed
    tftp_root.mkdir(parents=True, exist_ok=True)

    target = tftp_root / safe

    # Validate that the target stays under tftp_root
    _validate_under_root(target, tftp_root)

    # Remove any existing file/symlink at the target
    if target.exists() or target.is_symlink():
        target.unlink()

    if symlink:
        try:
            os.symlink(
                os.path.abspath(firmware_path), target
            )
            logger.info(
                "staged symlink %s -> %s",
                target,
                firmware_path,
            )
        except OSError:
            # Cross-filesystem or permission issue -- fall back
            # to a copy
            shutil.copy2(firmware_path, target)
            logger.info(
                "staged copy %s (symlink failed)",
                target,
            )
    else:
        shutil.copy2(firmware_path, target)
        logger.info("staged copy %s", target)

    return target


def unstage(staged_path: Union[str, Path]) -> bool:
    """Remove a previously staged file or symlink.

    Returns ``True`` if the file was removed, ``False`` if it
    was not found.
    """
    staged_path = Path(staged_path)
    if staged_path.exists() or staged_path.is_symlink():
        staged_path.unlink()
        logger.info("unstaged %s", staged_path)
        return True
    return False


def list_staged(
    tftp_root: Union[str, Path],
) -> list[Path]:
    """List all files and symlinks currently in *tftp_root*.

    Returns an empty list if the directory does not exist.
    """
    tftp_root = Path(tftp_root)
    if not tftp_root.is_dir():
        return []
    return sorted(tftp_root.iterdir())
