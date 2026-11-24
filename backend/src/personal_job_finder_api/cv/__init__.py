"""Master CV handling: PDF extraction, AI parsing, and local persistence.

The CV is the most sensitive data the application holds. It stays in SQLite on
this machine, is never written to a repository path, and is only sent to the
provider the user configured for their own key.
"""
