"""Custom exceptions for the application."""


class LeadAutomationError(Exception):
    """Base exception for all application errors."""
    pass


class ProxyError(LeadAutomationError):
    """Proxy related errors."""
    pass


class AuthenticationError(LeadAutomationError):
    """Authentication errors."""
    pass


class CollectorError(LeadAutomationError):
    """Audience collection errors."""
    pass


class SenderError(LeadAutomationError):
    """Message sending errors."""
    pass


class DatabaseError(LeadAutomationError):
    """Database operation errors."""
    pass


class TaskError(LeadAutomationError):
    """Task execution errors."""
    pass
