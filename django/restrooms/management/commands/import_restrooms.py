"""Add or update restrooms from a JSON file in the Node version's format:

    [{"id": "nbi-city-market", "name": "City Market Restroom", "area": "Nairobi CBD",
      "lat": -1.2833, "lng": 36.8219, "price": 100, "hours": "6:00 AM – 9:00 PM",
      "image": "city-market.jpg"}]

"id" becomes the restroom's code. A restroom with the same code is updated
(it stays active or inactive as it was), the others are added. Nothing is
saved unless every entry is valid. A photo that is missing or not a picture
is skipped with a warning.
"""
import json
from decimal import Decimal
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from restrooms.geo import is_number, is_valid_location
from restrooms.models import MAX_PHOTO_BYTES, Restroom

UPDATED_FIELDS = ('name', 'area', 'latitude', 'longitude', 'price', 'opening_hours')
# The file's names for the model's fields, for error messages.
FILE_NAMES = {'code': 'id', 'latitude': 'lat', 'longitude': 'lng', 'opening_hours': 'hours'}


class Command(BaseCommand):
    help = "Add or update restrooms from a JSON file in the Node version's format (data/restrooms.json)."

    def add_arguments(self, parser):
        parser.add_argument('file', type=Path, help='The JSON file, for example restrooms/sample_restrooms.json.')
        parser.add_argument(
            '--images-dir',
            type=Path,
            help='The folder with the photos named in "image" (default: an "images" folder next to the file).',
        )

    def handle(self, *args, file, images_dir, **options):
        entries = read_entries(file)
        seen = set()
        checked = []
        for number, entry in enumerate(entries, start=1):
            where = f'{file.name} entry #{number}'
            checked.append((where, checked_restroom(entry, where, seen), entry.get('image')))

        added = 0
        photos = []
        with transaction.atomic():
            for where, restroom, image in checked:
                fields = {name: getattr(restroom, name) for name in UPDATED_FIELDS}
                row, created = Restroom.objects.update_or_create(code=restroom.code, defaults=fields)
                added += created
                if isinstance(image, str) and image:
                    photos.append((row, image, where))
        # Photos are copied once the rows are saved, so a failed import leaves no stray files.
        for row, image, where in photos:
            self.attach_photo(row, (images_dir or file.parent / 'images') / Path(image).name, where)

        noun = 'restroom' if len(checked) == 1 else 'restrooms'
        self.stdout.write(self.style.SUCCESS(
            f'Imported {len(checked)} {noun} from {file.name}: {added} new, {len(checked) - added} updated.'
        ))

    def attach_photo(self, restroom, path, where):
        if restroom.photo:
            return  # keep the photo it has, which may have been changed in the admin
        problem = photo_problem(path)
        if problem:
            self.stderr.write(f'{where}: {problem}, imported without a photo')
            return
        with path.open('rb') as photo:
            restroom.photo.save(path.name, File(photo))


def photo_problem(path):
    """What stops the file from being a restroom photo under the admin's rules, or None."""
    if not path.is_file():
        return f'image not found at {path}'
    if path.stat().st_size > MAX_PHOTO_BYTES:
        return f'{path} is over 8 MB'
    with path.open('rb') as photo:
        try:
            # The admin's own check: a picture file name, and Pillow can read it.
            forms.ImageField().clean(File(photo))
        except ValidationError:
            return f'{path} is not a picture'
    return None


def read_entries(file):
    if not file.is_file():
        raise CommandError(f'{file} not found')
    try:
        # utf-8-sig skips the BOM that some Windows editors add.
        entries = json.loads(file.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as err:
        raise CommandError(f'Could not read {file}: {err}') from None
    if not isinstance(entries, list):
        raise CommandError(f'{file} must contain a JSON array of restrooms')
    return entries


def checked_restroom(entry, where, seen):
    """An unsaved Restroom for one entry, or CommandError saying what is wrong with it."""
    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']:
        raise CommandError(f'{where}: "id" is required')
    code = entry['id']
    if code in seen:
        raise CommandError(f'{where}: duplicate id "{code}"')
    seen.add(code)
    if not isinstance(entry.get('name'), str) or not entry['name']:
        raise CommandError(f'{where}: "name" is required')
    if not is_valid_location(entry.get('lat'), entry.get('lng')):
        raise CommandError(f'{where}: "lat" and "lng" must be valid numbers')
    price = entry.get('price')
    if price is not None and not (is_number(price) and price >= 0):
        raise CommandError(f'{where}: "price" must be a number')

    restroom = Restroom(
        code=code,
        name=entry['name'],
        area=entry.get('area') or '',
        latitude=entry['lat'],
        longitude=entry['lng'],
        # No price means the default price, as in the admin.
        price=None if price is None else Decimal(str(price)),
        opening_hours=entry.get('hours') or '',
    )
    try:
        restroom.full_clean(exclude=['photo'], validate_unique=False)
    except ValidationError as err:
        problems = '; '.join(
            f'"{FILE_NAMES.get(field, field)}": {" ".join(messages)}' for field, messages in err.message_dict.items()
        )
        raise CommandError(f'{where}: {problems}') from None
    return restroom
