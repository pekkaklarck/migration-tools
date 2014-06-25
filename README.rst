===============
Migration tools
===============

Some helpers written for moving Robot Framework projects from Google Code to
GitHub. May be useful also for others at least as a starting point.

Moving issues
=============

`<issues/issues.py>`_ script supports migrating issues from Google Code
to GitHub.

Preconditions::

    mkvirtualenv migration    # optional
    pip install beautifulsoup4
    pip install github3.py

Usage::

    python issues.py [options] source_project target_project github_username [github_password]
    python issues.py --help

Example::

    python issues.py robotframework pekkaklarck/rf-migration-test pekkaklarck


Converting wiki pages
=====================

`<wiki/transformer.py>`_ script can be used for converting wiki pages in
Google Code wiki syntax to reStructuredText.
