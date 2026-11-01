"""Cron expressions: parsing and next run (to do, see docs/cron.md)."""


def parse(expr: str):
    raise NotImplementedError


def next_fire(expr: str, after, zone: str):
    raise NotImplementedError
