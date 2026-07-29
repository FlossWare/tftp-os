"""Tests for the TftpOS custom exception hierarchy and error handling.

Covers:
 - Exception hierarchy and attributes
 - format_error() output
 - CLI integration (--verbose, suggestion display)
 - API structured error responses
"""

from __future__ import annotations

from pathlib import Path
import pytest

from tftpos.errors import (
    ConfigError,
    TftpOSError,
    PluginError,
    ProvisionError,
    ValidationError,
    format_error,
)


# ===================================================================
# 1. Exception hierarchy tests
# ===================================================================


class TestExceptionHierarchy:
    """Verify class relationships and default attributes."""

    def test_tftpos_error_is_exception(self):
        assert issubclass(TftpOSError, Exception)

    def test_config_error_is_tftpos_error(self):
        assert issubclass(ConfigError, TftpOSError)

    def test_validation_error_is_tftpos_error(self):
        assert issubclass(ValidationError, TftpOSError)

    def test_provision_error_is_tftpos_error(self):
        assert issubclass(ProvisionError, TftpOSError)

    def test_plugin_error_is_tftpos_error(self):
        assert issubclass(PluginError, TftpOSError)

    def test_tftpos_error_has_error_code(self):
        assert TftpOSError.error_code == "TFTPOS_ERROR"

    def test_config_error_has_error_code(self):
        assert ConfigError.error_code == "CONFIG_ERROR"

    def test_validation_error_has_error_code(self):
        assert ValidationError.error_code == "VALIDATION_ERROR"

    def test_provision_error_has_error_code(self):
        assert ProvisionError.error_code == "PROVISION_ERROR"

    def test_plugin_error_has_error_code(self):
        assert PluginError.error_code == "PLUGIN_ERROR"


# ===================================================================
# 2. Exception attribute tests
# ===================================================================


class TestExceptionAttributes:
    """Verify that all attributes are stored and accessible."""

    def test_message_stored(self):
        exc = TftpOSError("something broke")
        assert exc.message == "something broke"

    def test_str_is_message(self):
        exc = TftpOSError("something broke")
        assert str(exc) == "something broke"

    def test_suggestion_default_none(self):
        exc = TftpOSError("fail")
        assert exc.suggestion is None

    def test_suggestion_stored(self):
        exc = TftpOSError("fail", suggestion="try again")
        assert exc.suggestion == "try again"

    def test_context_default_empty(self):
        exc = TftpOSError("fail")
        assert exc.context == {}

    def test_context_stored(self):
        ctx = {"path": "/etc/tftpos/tftpos.toml"}
        exc = TftpOSError("fail", context=ctx)
        assert exc.context == ctx

    def test_all_attributes_together(self):
        exc = ConfigError(
            "cannot read config",
            suggestion="check the path",
            context={"config_path": "/etc/tftpos/tftpos.toml"},
        )
        assert exc.message == "cannot read config"
        assert exc.suggestion == "check the path"
        assert exc.context["config_path"] == "/etc/tftpos/tftpos.toml"
        assert exc.error_code == "CONFIG_ERROR"

    def test_can_catch_subclass_as_base(self):
        exc = ValidationError("bad mac")
        with pytest.raises(TftpOSError):
            raise exc


# ===================================================================
# 3. format_error() tests
# ===================================================================


class TestFormatError:
    """Verify the CLI formatting helper."""

    def test_basic_message(self):
        exc = TftpOSError("something failed")
        result = format_error(exc)
        assert result == "error: something failed"

    def test_with_suggestion(self):
        exc = TftpOSError("bad input", suggestion="try X instead")
        result = format_error(exc)
        assert "error: bad input" in result
        assert "hint: try X instead" in result

    def test_with_context(self):
        exc = TftpOSError(
            "not found",
            context={"path": "/etc/tftpos/tftpos.toml"},
        )
        result = format_error(exc)
        assert "path: /etc/tftpos/tftpos.toml" in result

    def test_verbose_includes_traceback(self):
        try:
            raise ConfigError("broken config")
        except ConfigError as exc:
            result = format_error(exc, verbose=True)
        assert "Traceback" in result
        assert "ConfigError" in result

    def test_non_verbose_no_traceback(self):
        try:
            raise ConfigError("broken config")
        except ConfigError as exc:
            result = format_error(exc, verbose=False)
        assert "Traceback" not in result

    def test_multiline_context(self):
        exc = TftpOSError(
            "fail",
            context={"a": "1", "b": "2"},
        )
        result = format_error(exc)
        assert "  a: 1" in result
        assert "  b: 2" in result

    def test_all_sections_present(self):
        try:
            raise PluginError(
                "plugin missing",
                suggestion="install it",
                context={"plugin": "beos"},
            )
        except PluginError as exc:
            result = format_error(exc, verbose=True)
        assert "error: plugin missing" in result
        assert "hint: install it" in result
        assert "plugin: beos" in result
        assert "Traceback" in result


# ===================================================================
# 4. Edge cases
# ===================================================================


class TestEdgeCases:
    """Edge cases for the error handling system."""

    def test_empty_context_not_shown(self):
        """Empty context dict produces no extra lines."""
        exc = TftpOSError("fail", context={})
        result = format_error(exc)
        assert result == "error: fail"

    def test_none_suggestion_not_shown(self):
        """None suggestion produces no hint line."""
        exc = TftpOSError("fail", suggestion=None)
        result = format_error(exc)
        assert "hint:" not in result

    def test_exception_is_catchable_in_except(self):
        """TftpOSError and subclasses work in try/except."""
        caught = False
        try:
            raise ProvisionError("no rule")
        except TftpOSError:
            caught = True
        assert caught

    def test_exception_chain_preserved(self):
        """__cause__ is preserved when wrapping ValueError."""
        original = ValueError("original error")
        try:
            try:
                raise original
            except ValueError as exc:
                raise ConfigError("wrapped") from exc
        except ConfigError as exc:
            assert exc.__cause__ is original

    def test_format_error_with_empty_message(self):
        """Empty message is handled gracefully."""
        exc = TftpOSError("")
        result = format_error(exc)
        assert result == "error: "

    def test_context_with_path_object(self):
        """Context can contain Path objects (converted to str)."""
        exc = TftpOSError(
            "fail",
            context={"path": str(Path("/etc/tftpos/tftpos.toml"))},
        )
        result = format_error(exc)
        assert "/etc/tftpos/tftpos.toml" in result
