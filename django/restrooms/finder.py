import math
from dataclasses import replace
from datetime import datetime, timedelta

from django.apps import apps
from django.db.models import OuterRef, Subquery

from .demo import demo_restrooms_around
from .geo import EARTH_RADIUS_KM, distance_km
from .hours import is_open_on_arrival
from .models import Restroom


def find_nearest(facilities, location, *, max_results, max_distance_km, at=None, timezone=None,
                 leave_out=frozenset()):
    """The closest facilities, each with its distance_km, nearest first.

    With `at` (now), only the ones still open when the user gets there on
    foot. Codes in `leave_out` are skipped and the next closest take their place.
    """
    nearby = []
    for facility in facilities:
        if facility.code in leave_out:
            continue
        placed = replace(facility, distance_km=distance_km(location, facility))
        if placed.distance_km > max_distance_km:
            continue
        if at and not is_open_on_arrival(placed, at, timezone):
            continue
        nearby.append(placed)
    nearby.sort(key=lambda facility: facility.distance_km)
    return nearby[:max_results]


# Needs that only some restrooms meet. "clean" and "convenient" list every
# restroom, sorted by rating or by distance.
NEED_FILTERS = {
    'accessible': lambda facility: facility.accessible,
    'disabled': lambda facility: facility.disabled_toilet,
    'baby': lambda facility: facility.baby_changing,
}


class RestroomFinder:
    """Where the chat looks for restrooms: active ones in the database, or
    demo restrooms around the user when DEMO_RESTROOMS_NEAR_USER is on."""

    def __init__(self, config):
        self.config = config

    def nearest(self, location, at=None, leave_out=frozenset(), need=''):
        """The closest restrooms, or for a need (see NEEDS) only those that
        offer it; "clean" lists the best rated nearby first."""
        if self.config.demo_restrooms_near_user:
            candidates = demo_restrooms_around(location, self.config.default_price)
        else:
            candidates = self._active_near(location)
            # DEMO_RESTROOMS_FALLBACK: real restrooms where there are some,
            # sample ones around anyone far from them (a judge abroad).
            if self.config.demo_restrooms_fallback and not any(
                distance_km(location, facility) <= self.config.max_distance_km for facility in candidates
            ):
                candidates = demo_restrooms_around(location, self.config.default_price)
        wanted = NEED_FILTERS.get(need)
        if wanted:
            candidates = [facility for facility in candidates if wanted(facility)]
        found = find_nearest(
            candidates,
            location,
            max_results=len(candidates) if need == 'clean' else self.config.max_results,
            max_distance_km=self.config.max_distance_km,
            at=at,
            timezone=self.config.timezone,
            leave_out=leave_out,
        )
        if need == 'clean':
            # Cleaned in the last 6 hours (a checked photo proves it) first, then the star rating.
            def fresh(facility):
                if not (at and facility.cleaned_at):
                    return False
                return at - datetime.fromisoformat(facility.cleaned_at) <= timedelta(hours=6)
            found = sorted(found, key=lambda facility: (not fresh(facility), -facility.cleanliness))
            found = found[: self.config.max_results]
        return found

    def _active_near(self, location):
        # Nothing further north or south than MAX_DISTANCE_KM can be close
        # enough, so only that band of latitudes is read (plus a little extra;
        # find_nearest checks the exact distance).
        band = math.degrees(self.config.max_distance_km / EARTH_RADIUS_KM) + 0.01
        # The last approved cleaning photo of each restroom (ops.CleaningCheck), shown to visitors as
        # "cleaned 20 min ago": a cleaner's checked work is what the visitor sees before paying.
        latest = OuterRef('pk')
        approved = (
            apps.get_model('ops', 'CleaningCheck').objects
            .filter(restroom=latest, status='approved').order_by('-created_at')
        )
        rows = Restroom.objects.filter(
            is_active=True,
            latitude__range=(location.lat - band, location.lat + band),
        ).annotate(
            cleaned_at=Subquery(approved.values('created_at')[:1]),
            cleaned_score=Subquery(approved.values('score')[:1]),
        ).order_by('pk')
        return [row.to_facility(self.config.default_price, row.cleaned_at, row.cleaned_score) for row in rows]
