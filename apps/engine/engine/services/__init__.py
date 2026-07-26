"""Render-core services derived from MoneyPrinterTurbo.

The upstream snapshot these are ported from lives in `vendor/moneyprinterturbo`.
Each module names the file it came from and what was changed.

What every module here has in common, and what makes it *ours* rather than a
copy: configuration comes from `engine.settings.Settings`, paths go through
`engine.storage.store`, and nothing reaches for a module-level task dict. Those
three are the whole difference between this package and `vendor/`.
"""
