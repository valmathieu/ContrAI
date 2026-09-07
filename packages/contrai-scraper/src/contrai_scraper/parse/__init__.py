"""Turning observed wire events into a ``contrai-data`` record.

The stages run one way, each knowing only the one below it:

* :mod:`~contrai_scraper.parse.translate` resolves the site's tokens into
  ``contrai_core`` values through the profile;
* :mod:`~contrai_scraper.parse.deal` rebuilds the four hands and the trick
  the wire never sends;
* :mod:`~contrai_scraper.parse.snapshot` reads the table description the
  socket opens with;
* :mod:`~contrai_scraper.parse.live` turns keyed events into bids and plays;
* :mod:`~contrai_scraper.parse.session` assembles a whole game.
"""
