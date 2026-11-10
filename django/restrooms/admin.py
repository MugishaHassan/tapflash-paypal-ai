from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.utils import model_ngettext
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone
from django.utils.html import format_html

from .hours import is_open_at
from .models import MAX_PHOTO_BYTES, Restroom


class RestroomForm(forms.ModelForm):
    class Meta:
        model = Restroom
        fields = '__all__'

    def clean_photo(self):
        photo = self.cleaned_data['photo']
        # Only a new upload is checked: a saved photo was checked when it came in.
        if isinstance(photo, UploadedFile) and photo.size > MAX_PHOTO_BYTES:
            raise forms.ValidationError('This photo is too big. Please use one under 8 MB.')
        return photo


@admin.register(Restroom)
class RestroomAdmin(admin.ModelAdmin):
    form = RestroomForm
    list_display = ('name', 'thumbnail', 'area', 'opening_hours', 'open_now', 'price', 'is_active')
    list_editable = ('price', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'area')
    ordering = ('name',)
    prepopulated_fields = {'code': ('name',)}
    readonly_fields = ('map_link', 'photo_preview', 'created_at', 'updated_at')
    fieldsets = (
        (None, {'fields': ('name', 'code', 'area', 'is_active')}),
        ('Location', {'fields': ('latitude', 'longitude', 'map_link')}),
        ('Price and opening hours', {'fields': ('price', 'opening_hours')}),
        ('Photo', {'fields': ('photo', 'photo_preview')}),
        ('What it offers', {'fields': ('cleanliness', 'accessible', 'disabled_toilet', 'baby_changing')}),
        ('Operator and payouts', {'fields': ('operator_name', 'payout_email', 'operator_share_percent')}),
        ('Record', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    actions = ('activate', 'deactivate')

    def changelist_view(self, request, extra_context=None):
        # The list's template warns that these restrooms are not offered while demo mode is on.
        demo = settings.TAPFLASH.demo_restrooms_near_user
        return super().changelist_view(request, {**(extra_context or {}), 'demo_restrooms_near_user': demo})

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'price':
            # An empty price shows the default it falls back to.
            field.widget.attrs['placeholder'] = str(settings.TAPFLASH.default_price)
        return field

    @admin.display(description='photo')
    def thumbnail(self, restroom):
        if not restroom.photo:
            return None
        return format_html(
            '<img src="{}" alt="" width="64" height="48" style="object-fit: cover; border-radius: 4px;">',
            restroom.photo.url,
        )

    @admin.display(description='photo preview')
    def photo_preview(self, restroom):
        if not restroom.photo:
            return 'No photo yet.'
        return format_html('<img src="{}" alt="" style="max-width: 320px; border-radius: 6px;">', restroom.photo.url)

    @admin.display(description='check on a map')
    def map_link(self, restroom):
        if restroom.latitude is None or restroom.longitude is None:
            return None
        url = f'https://www.google.com/maps/search/?api=1&query={restroom.latitude},{restroom.longitude}'
        return format_html('<a href="{}" target="_blank" rel="noopener">Open in Google Maps</a>', url)

    @admin.display(boolean=True, description='open now')
    def open_now(self, restroom):
        return is_open_at(restroom.opening_hours, timezone.now(), settings.TAPFLASH.timezone)

    @admin.action(description='Activate selected restrooms')
    def activate(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f'{count} {model_ngettext(self.opts, count)} activated.')

    @admin.action(description='Deactivate selected restrooms')
    def deactivate(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f'{count} {model_ngettext(self.opts, count)} deactivated.')
