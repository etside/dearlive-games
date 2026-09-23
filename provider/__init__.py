"""B2B game provider surface for Teen Patti Pro.

The provider package adds operator authentication, sessions, tables, wallet and
documentation on top of the existing game engine. It is transport agnostic;
the engine, its rules and its UI are untouched.
"""
from provider.router import ProviderContext, ProviderError, dispatch, is_provider_path

__all__ = ["ProviderContext", "ProviderError", "dispatch", "is_provider_path"]
