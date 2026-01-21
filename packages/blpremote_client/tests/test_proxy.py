"""Tests for the proxy API (Session, Service, Request)."""

from unittest.mock import Mock, patch

import pytest
from blpremote_client.proxy.request import Element, Request
from blpremote_client.proxy.service import Service
from blpremote_client.proxy.session import Session, SessionOptions


class TestSessionOptions:
    """Tests for SessionOptions."""

    def test_default_options(self):
        """Test default session options."""
        opts = SessionOptions()
        assert opts._server_host == "localhost"
        assert opts._server_port == 8194

    def test_set_server_host(self):
        """Test setting server host."""
        opts = SessionOptions()
        opts.setServerHost("192.168.1.100")
        assert opts._server_host == "192.168.1.100"


class TestRequest:
    """Tests for Request proxy."""

    def test_get_element(self):
        """Test getting an element."""
        mock_service = Mock()
        mock_service._name = "//blp/refdata"
        req = Request(mock_service, "ReferenceDataRequest", "req1")

        elem = req.getElement("securities")
        assert isinstance(elem, Element)

    def test_append_value(self):
        """Test appending values to elements."""
        mock_service = Mock()
        mock_service._name = "//blp/refdata"
        req = Request(mock_service, "ReferenceDataRequest", "req1")

        req.getElement("securities").appendValue("IBM US Equity")
        req.getElement("securities").appendValue("AAPL US Equity")
        req.getElement("fields").appendValue("PX_LAST")

        ops = req._get_operations()
        assert len(ops) == 3
        assert ops[0] == {"type": "append", "path": "securities", "value": "IBM US Equity"}
        assert ops[1] == {"type": "append", "path": "securities", "value": "AAPL US Equity"}
        assert ops[2] == {"type": "append", "path": "fields", "value": "PX_LAST"}

    def test_set_value(self):
        """Test setting values on elements."""
        mock_service = Mock()
        mock_service._name = "//blp/refdata"
        req = Request(mock_service, "ReferenceDataRequest", "req1")

        req.set("overrideOption", "OVERRIDE_VALUE")

        ops = req._get_operations()
        assert len(ops) == 1
        assert ops[0] == {"type": "set", "path": "overrideOption", "value": "OVERRIDE_VALUE"}


class TestSession:
    """Tests for Session proxy."""

    @patch("blpremote_client.proxy.session.RemoteHost")
    def test_session_start(self, mock_remote_class):
        """Test starting a session."""
        opts = SessionOptions()
        session = Session(opts, remote_host="http://localhost:8000")

        result = session.start()
        assert result is True
        assert session._started is True
        assert len(session._ops) == 1
        assert session._ops[0]["op"] == "start_session"

    @patch("blpremote_client.proxy.session.RemoteHost")
    def test_session_open_service(self, mock_remote_class):
        """Test opening a service."""
        opts = SessionOptions()
        session = Session(opts, remote_host="http://localhost:8000")

        session.start()
        result = session.openService("//blp/refdata")

        assert result is True
        assert "//blp/refdata" in session._services
        assert len(session._ops) == 2

    @patch("blpremote_client.proxy.session.RemoteHost")
    def test_session_get_service(self, mock_remote_class):
        """Test getting an opened service."""
        opts = SessionOptions()
        session = Session(opts, remote_host="http://localhost:8000")

        session.start()
        session.openService("//blp/refdata")
        svc = session.getService("//blp/refdata")

        assert isinstance(svc, Service)
        assert svc.name == "//blp/refdata"

    @patch("blpremote_client.proxy.session.RemoteHost")
    def test_session_get_unopened_service_fails(self, mock_remote_class):
        """Test that getting an unopened service raises error."""
        opts = SessionOptions()
        session = Session(opts, remote_host="http://localhost:8000")

        session.start()
        with pytest.raises(ValueError, match="not opened"):
            session.getService("//blp/refdata")

    @patch("blpremote_client.proxy.session.RemoteHost")
    def test_session_not_started_fails(self, mock_remote_class):
        """Test that opening service without start raises error."""
        opts = SessionOptions()
        session = Session(opts, remote_host="http://localhost:8000")

        with pytest.raises(RuntimeError, match="not started"):
            session.openService("//blp/refdata")
