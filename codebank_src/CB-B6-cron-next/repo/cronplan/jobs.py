"""The job list shown on the dashboard."""
from .cron import next_fire


def upcoming(jobs, now, zone="Europe/Berlin"):
    """jobs: {name: expr}. Returns [(next run UTC, name)] sorted."""
    return sorted((next_fire(expr, now, zone), name) for name, expr in jobs.items())
