"""Bounded private JSON I/O with constant diagnostics and atomic replacement.

Only the immediate containing directory must be owned by this user and 0700;
all path components must be real directories, with no symlink traversal.
Missing directories are created 0700. Files are regular, owned, single-link,
0600. Parent directory FDs anchor operations against path replacement races.
No values, paths, serializer exceptions or validator exceptions are printed.
"""

import json
import os
import secrets
import stat

MAX_BYTES = 1024 * 1024


def _fail(code):
    raise ValueError(code) from None


def _limit(value):
    if type(value) is not int or not 1 <= value <= MAX_BYTES:
        _fail("invalid_limit")
    return value


def _private(info, directory=False):
    expected = 0o700 if directory else 0o600
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != expected:
        _fail("unsafe_permissions")
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        _fail("unsafe_file")


def _parent(path, create=False):
    try:
        path = os.fspath(path)
    except Exception:
        _fail("invalid_path")
    try:
        if type(path) is not str or not path or "\0" in path:
            _fail("invalid_path")
        parts = path.split("/")
        if ".." in parts or not parts[-1] or parts[-1] == ".":
            _fail("invalid_path")
        fd = os.open(
            "/" if path.startswith("/") else ".",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            for part in parts[:-1]:
                if part in ("", "."):
                    continue
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
            _private(os.fstat(fd), directory=True)
            return fd, parts[-1]
        except Exception:
            os.close(fd)
            raise
    except (OSError, TypeError, ValueError):
        # Preserve only our own constant permissions error, never OS path text.
        raise


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key")
        result[key] = value
    return result


def _constant(_value):
    _fail("invalid_json")


def _validate(data, validator):
    if validator is None:
        return data
    try:
        return validator(data)
    except Exception:
        _fail("validation_failed")


def load_private_json(path, validator=None, max_bytes=MAX_BYTES):
    """Load bounded UTF-8 JSON; optional validator returns normalized data."""
    limit = _limit(max_bytes)
    parent = None
    try:
        parent, name = _parent(path)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            _private(info)
            if info.st_size > limit:
                _fail("file_too_large")
            content = source.read(limit + 1)
        if len(content) > limit:
            _fail("file_too_large")
        try:
            value = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_pairs,
                parse_constant=_constant,
            )
        except (UnicodeError, ValueError, RecursionError):
            _fail("invalid_json")
        return _validate(value, validator)
    except OSError:
        _fail("private_io_failed")
    finally:
        if parent is not None:
            os.close(parent)


def write_private_json(path, data, validator=None, max_bytes=MAX_BYTES):
    """Validate and serialize before touching destination; atomically replace.

    Does not emit stdout. A caller may print its own fixed success code. fsync
    covers file and directory; trusted local operators remain the boundary.
    """
    limit = _limit(max_bytes)
    value = _validate(data, validator)
    content = bytearray()
    too_large = False
    try:
        for chunk in json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).iterencode(value):
            content.extend(chunk.encode("utf-8"))
            if len(content) + 1 > limit:
                too_large = True
                _fail("file_too_large")
        content.extend(b"\n")
    except Exception:
        _fail("file_too_large" if too_large else "serialization_failed")
    parent = temporary = None
    try:
        parent, name = _parent(path, create=True)
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _private(info)
        temporary = ".private-" + secrets.token_hex(16)
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        temporary = None
        os.fsync(parent)
    except OSError:
        _fail("private_io_failed")
    finally:
        if parent is not None:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except OSError:
                    pass
            os.close(parent)
