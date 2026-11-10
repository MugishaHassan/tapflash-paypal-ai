from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Facility:
    """A restroom as the chat sees it.

    The chat keeps this copy, so a change in the admin does not alter a
    journey that is already under way. Demo restrooms exist only in this form.
    """
    code: str
    name: str
    area: str
    lat: float
    lng: float
    price: Decimal
    hours: str = ''
    photo: str = ''  # file name under MEDIA_ROOT, '' when there is none
    cleanliness: int = 0  # 1 to 5 stars, 0 when not rated
    accessible: bool = False  # step-free entrance
    disabled_toilet: bool = False
    baby_changing: bool = False
    distance_km: float | None = None  # from the user, set when restrooms are searched
    # The last cleaning a checked photo proved (ISO time, '' when none), and whether Gemini scored that photo.
    cleaned_at: str = ''
    cleaned_checked_by_ai: bool = False
