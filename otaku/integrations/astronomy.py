"""The sky's own facts, worked out from the instant alone: the moon's
phase and the year's nearest turning point — a solstice or an equinox.
Asked of no service and needing no place, so nothing about where the
asker is goes into them or comes out.
"""

from datetime import UTC, date, datetime

from otaku.integrations.base import Integration

# The moon's mean month, new moon to new moon, from one known new moon.
_SYNODIC_MONTH = 29.530588853  # days
_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=UTC)
_MOON_PHASES = (
    "new moon",
    "waxing crescent",
    "first quarter",
    "waxing gibbous",
    "full moon",
    "waning gibbous",
    "last quarter",
    "waning crescent",
)
# The year's four turning points as Meeus gives their mean instants
# (Astronomical Algorithms, table 27.B, years 1000 to 3000): a Julian
# Ephemeris Day, a polynomial in millennia from 2000 — within the hour.
_TURNING_POINTS = (
    ("March equinox", (2451623.80984, 365242.37404, 0.05169, -0.00411, -0.00057)),
    ("June solstice", (2451716.56767, 365241.62603, 0.00325, 0.00888, -0.00030)),
    ("September equinox", (2451810.21715, 365242.01767, -0.11575, 0.00337, 0.00078)),
    ("December solstice", (2451900.05952, 365242.74049, -0.06223, -0.00823, 0.00032)),
)
_JD_UNIX_EPOCH = 2440587.5  # the Julian Day of 1970-01-01 00:00 UTC


class Astronomy(Integration):
    """The sky as an integration: no service, no key, no section — the
    facts worked out here, the system info tool's to report."""

    id = "astronomy"
    label = "Astronomy"
    configurable = False

    def moon_phase(self, at: datetime) -> str:
        """The moon's phase at the aware instant `at`, one of the eight
        named, read off the mean month — up to half a day out at a
        phase's edge, which no story notices."""
        age = (at - _NEW_MOON).total_seconds() / 86400 % _SYNODIC_MONTH
        return _MOON_PHASES[round(age / _SYNODIC_MONTH * 8) % 8]

    def turning_point(self, at: datetime) -> str:
        """The solstice or equinox nearest the aware instant `at`,
        counted in days of `at`'s own calendar: "September equinox 8
        days ago", "June solstice today", "December solstice in 81
        days"."""
        today = at.date()
        nearest = min(
            (
                (_turning_day(coefficients, year, at), name)
                for year in (today.year - 1, today.year, today.year + 1)
                for name, coefficients in _TURNING_POINTS
            ),
            key=lambda point: abs((point[0] - today).days),
        )
        day, name = nearest
        days = (day - today).days
        if days == 0:
            return f"{name} today"
        count = f"{abs(days)} day{'' if abs(days) == 1 else 's'}"
        return f"{name} in {count}" if days > 0 else f"{name} {count} ago"


def _turning_day(coefficients: tuple[float, ...], year: int, at: datetime) -> date:
    """The day a turning point of `year` falls on in `at`'s calendar."""
    millennia = (year - 2000) / 1000
    jde = sum(c * millennia**power for power, c in enumerate(coefficients))
    instant = datetime.fromtimestamp((jde - _JD_UNIX_EPOCH) * 86400, tz=UTC)
    return instant.astimezone(at.tzinfo).date()
